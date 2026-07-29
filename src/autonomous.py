"""
The autonomous debugging loop.

    analyse -> rules -> hypotheses -> experiment -> act -> re-measure ->
    evaluate -> record -> repeat

It keeps going until the issue is resolved, confidence stops improving, no
untested hypothesis remains, or the iteration cap is reached -- then stops and
explains why.

SAFETY MODEL
------------
This is the only part of the project that acts on its own, so the limits are
structural rather than instructions in a prompt:

  * A hard iteration cap (default 6). The loop cannot run forever.
  * Read-only by default. Writing files or running mesh utilities requires
    allow_writes=True, which the caller ties to explicit user approval.
  * Every edit still goes through FileEditor, so it is backed up and undoable.
  * Nothing outside the selected case folder can be touched, ever.
  * Every action is recorded in the session, and the loop refuses to repeat a
    fix that already failed.
  * An `on_event` callback reports each step, so the user can watch and stop it.

The loop does not call the AI. It orchestrates the deterministic machinery --
rules, hypotheses, experiments, commands. That keeps its behaviour inspectable
and testable; the AI's job is still to explain the result in conversation.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import command_runner as cr
from .case_survey import read_case_full, survey_case
from .debug_memory import (AttemptRecord, DebugSession, record_solved_case,
                           save_session)
from .rules.context import CaseContext
from .experiment import (FAILURE, INCONCLUSIVE, SUCCESS, evaluate,
                         evaluate_findings, plan_experiment)
from .hypothesis import REFUTED, build_hypotheses, format_hypotheses
from .log_intelligence import analyze_log
from .rules import run_all_checks

DEFAULT_MAX_ITERATIONS = 6

# Why the loop stopped.
STOP_RESOLVED = "the issue appears to be resolved"
STOP_NO_HYPOTHESES = "no untested explanations remain"
STOP_NO_PROGRESS = "confidence stopped improving"
STOP_MAX_ITERATIONS = "the iteration limit was reached"
STOP_NEEDS_APPROVAL = "the next step needs your approval before it can run"
STOP_NO_OPENFOAM = "no OpenFOAM installation is available to test against"


@dataclass
class LoopResult:
    """The outcome of a full autonomous run."""
    session: Optional[DebugSession] = None
    hypotheses: list = field(default_factory=list)
    experiments: list = field(default_factory=list)
    iterations: int = 0
    resolved: bool = False
    stop_reason: str = ""
    findings: list = field(default_factory=list)
    blocked_actions: list = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Ran {self.iterations} iteration(s); stopped because {self.stop_reason}.", ""]
        if self.hypotheses:
            lines.append(format_hypotheses(self.hypotheses, limit=4))
            lines.append("")
        if self.experiments:
            lines.append("**What was tried:**")
            for e in self.experiments:
                lines.append(f"- Experiment {e.number}: {e.change_description} -> "
                             f"**{e.outcome}**"
                             + (f" ({e.observations[0]})" if e.observations else ""))
            lines.append("")
        if self.blocked_actions:
            lines.append("**Needs your approval to go further:**")
            for a in self.blocked_actions:
                lines.append(f"- {a}")
        return "\n".join(lines).strip()


def _measure(case_path: str) -> tuple:
    """
    Take a reading of the case: findings from the rules, plus the solver log.

    Returns (findings, log_analysis, files).
    """
    files = read_case_full(case_path)
    try:
        survey = survey_case(case_path)
    except OSError:
        survey = None
    findings = run_all_checks(files, survey=survey, case_path=case_path)

    best_log, best_steps = None, -1
    for rel, text in files.items():
        if not (rel.startswith("log") or rel.endswith(".log")):
            continue
        if "checkmesh" in rel.lower():
            continue
        a = analyze_log(text)
        if a.parsed and a.n_steps > best_steps:
            best_log, best_steps = a, a.n_steps
    return findings, best_log, files


def _confidence_of(hypotheses) -> float:
    return hypotheses[0].confidence if hypotheses else 0.0


def run_loop(case_path: str, *, max_iterations: int = DEFAULT_MAX_ITERATIONS,
             allow_writes: bool = False, allow_solver: bool = False,
             session: Optional[DebugSession] = None,
             on_event: Optional[Callable[[str, dict], None]] = None) -> LoopResult:
    """
    Run the autonomous debugging loop over one case.

    allow_writes -- may run mesh utilities that modify the case (blockMesh,
                    decomposePar, ...). Off by default.
    allow_solver -- may run a solver to test a fix. Off by default; solver runs
                    are slow and always write results.
    on_event     -- called as on_event(kind, payload) for 'iteration', 'measure',
                    'hypotheses', 'experiment', 'command', 'result', 'stop'.
    """
    def emit(kind: str, **payload):
        if on_event:
            try:
                on_event(kind, payload)
            except Exception:      # a UI callback must never break the loop
                pass

    session = session or DebugSession(case_path=str(case_path))
    result = LoopResult(session=session)

    findings, log_before, _ = _measure(case_path)
    result.findings = findings
    emit("measure", findings=len(findings),
         crashed=bool(getattr(log_before, "crashed", False)))

    hypotheses = build_hypotheses(findings)
    result.hypotheses = hypotheses
    emit("hypotheses", count=len(hypotheses),
         top=hypotheses[0].title if hypotheses else None)

    if not findings:
        result.stop_reason = STOP_RESOLVED
        result.resolved = True
        session.resolved = True
        emit("stop", reason=result.stop_reason)
        return result

    previous_confidence = _confidence_of(hypotheses)

    for iteration in range(1, max_iterations + 1):
        result.iterations = iteration
        session.iterations = iteration
        emit("iteration", number=iteration, of=max_iterations)

        # Pick the best explanation we have not already ruled out.
        candidates = session.remaining(hypotheses)
        candidates = [h for h in candidates if h.status != REFUTED]
        if not candidates:
            result.stop_reason = STOP_NO_HYPOTHESES
            break

        target = candidates[0]
        experiment = plan_experiment(target, number=iteration, remaining=candidates[1:])
        experiment.start()
        result.experiments.append(experiment)
        emit("experiment", number=iteration, objective=experiment.objective,
             change=experiment.change_description)

        # --- act: only the read-only diagnostics the experiment asked for -----
        ran_anything = False
        commands_run = []
        for command in experiment.commands:
            name = command[0] if command else ""
            try:
                cr.check_allowed(command, allow_write=allow_writes)
            except cr.CommandNotAllowed as e:
                result.blocked_actions.append(f"{' '.join(command)} — {e}")
                emit("blocked", command=command, reason=str(e))
                continue

            if cr.best_install() is None:
                result.stop_reason = STOP_NO_OPENFOAM
                emit("stop", reason=result.stop_reason)
                experiment.finish(INCONCLUSIVE, "No OpenFOAM available to run diagnostics.")
                session.record_attempt(AttemptRecord(
                    iteration=iteration, hypothesis_key=target.key,
                    description=experiment.change_description,
                    outcome=INCONCLUSIVE,
                    observation="no OpenFOAM installation available"))
                return result

            emit("command", command=command)
            outcome = cr.run_command(command, case_path, allow_write=allow_writes)
            commands_run.append(command)
            ran_anything = True
            emit("result", command=name, ok=outcome.ok, summary=outcome.summary())

        # --- re-measure --------------------------------------------------------
        findings_after, log_after, _ = _measure(case_path)
        if ran_anything:
            # Prefer the direct test: did the problem we targeted go away? Many
            # fixes (generating a mesh, adding a field) produce no solver log,
            # so judging only by log behaviour would miss a working fix.
            verdict = evaluate_findings(experiment, target, findings, findings_after)
            if verdict is None:
                verdict = evaluate(experiment, log_before, log_after)
        else:
            verdict = INCONCLUSIVE
        if not ran_anything:
            experiment.finish(
                INCONCLUSIVE,
                "This change needs to be applied by you before it can be tested."
                if not allow_writes else "No runnable diagnostic was available for this step.",
            )
            result.blocked_actions.append(
                f"{experiment.change_description} (tests: {target.title})"
            )

        session.record_attempt(AttemptRecord(
            iteration=iteration,
            hypothesis_key=target.key,
            description=experiment.change_description,
            commands_run=commands_run,
            outcome=experiment.outcome,
            observation=experiment.observations[0] if experiment.observations else "",
        ))

        if verdict == SUCCESS:
            target.status = "confirmed"
            result.resolved = not findings_after or not any(
                f.severity == "critical" for f in findings_after
            )
            if result.resolved:
                session.resolved = True
                result.stop_reason = STOP_RESOLVED
                break
        elif verdict == FAILURE:
            target.status = REFUTED
        else:
            # Inconclusive: do not test it again this session, but do not claim
            # it was disproved either.
            target.status = REFUTED if ran_anything else target.status
            if not ran_anything:
                # Nothing was executed, so looping again would change nothing.
                result.stop_reason = STOP_NEEDS_APPROVAL
                break

        # --- has the picture improved? ----------------------------------------
        findings = findings_after
        result.findings = findings
        hypotheses = build_hypotheses(findings)
        result.hypotheses = hypotheses
        log_before = log_after

        current = _confidence_of(hypotheses)
        if abs(current - previous_confidence) < 0.01 and verdict != SUCCESS:
            result.stop_reason = STOP_NO_PROGRESS
            break
        previous_confidence = current
    else:
        result.stop_reason = STOP_MAX_ITERATIONS

    if not result.stop_reason:
        result.stop_reason = STOP_MAX_ITERATIONS
    session.stop_reason = result.stop_reason

    # --- learn from the session -------------------------------------------------
    # Save what was tried so a restart does not lose it, and -- when something
    # actually worked -- record it so future diagnoses can recall this case.
    try:
        save_session(session)
        if session.successful_fixes:
            ctx = CaseContext(read_case_full(case_path))
            record_solved_case(
                session,
                solver=ctx.application,
                turbulence=ctx.turbulence_model,
                problem="; ".join(h.title for h in result.hypotheses[:2]),
                confidence=(result.hypotheses[0].confidence_label
                            if result.hypotheses else ""),
            )
            emit("learned", fixes=len(session.successful_fixes))
    except Exception:      # learning is a bonus; it must never break the run
        pass

    emit("stop", reason=result.stop_reason, resolved=result.resolved)
    return result


def dry_run(case_path: str, max_iterations: int = DEFAULT_MAX_ITERATIONS) -> LoopResult:
    """
    Plan without touching anything.

    Produces the ranked hypotheses and the experiments that WOULD be run, so the
    user can see the whole plan before granting permission for any of it.
    """
    return run_loop(case_path, max_iterations=max_iterations,
                    allow_writes=False, allow_solver=False)
