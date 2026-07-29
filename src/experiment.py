"""
Engineering experiments, not guesses.

A list of "things you could try" is what a chatbot produces. An engineer designs
an experiment: one change, a prediction of what should happen if the hypothesis
is right, a way to measure it, and a decision rule for what to do next in either
outcome. That structure is what makes an autonomous loop converge instead of
wandering.

Each Experiment records:
    objective          what we are trying to establish
    hypothesis         which explanation it tests
    change             the single edit or command being made
    expected_outcome   what we should see if the hypothesis is correct
    success_criteria   how we will judge it, concretely
    if_successful      what to do next when it passes
    if_unsuccessful    what to do next when it fails

Deterministic: this module plans and evaluates, it does not call the AI.
"""
from dataclasses import dataclass, field
from datetime import datetime

from .hypothesis import Hypothesis

# Outcomes.
PENDING = "pending"
SUCCESS = "success"
FAILURE = "failure"
INCONCLUSIVE = "inconclusive"

# What a given hypothesis suggests changing first. Kept small and conservative:
# the loop should make the smallest reversible change that tests the idea.
FIRST_CHANGE = {
    "no-mesh": ("Generate the mesh", ["blockMesh"],
                "checkMesh finds a valid mesh instead of a missing 'points' file"),
    "time-step": ("Limit the Courant number", None,
                  "the run proceeds further and the Courant number stays bounded"),
    "numerical-stability": ("Switch convection to a bounded upwind scheme", None,
                            "residuals fall instead of rising"),
    "pressure-convergence": ("Add non-orthogonal correctors", None,
                             "the continuity error stops growing"),
    "turbulence-setup": ("Provide the missing turbulence field", None,
                         "the solver constructs the turbulence model and starts"),
    "boundary-conditions": ("Relax the over-constrained patch", None,
                            "the first iteration produces finite residuals"),
    "parallel-setup": ("Re-decompose the case", ["decomposePar"],
                       "the processor folder count matches numberOfSubdomains"),
    "mesh-quality": ("Measure the mesh", ["checkMesh"],
                     "checkMesh quantifies the non-orthogonality and skewness"),
    "solver-mismatch": ("Align the algorithm block with the solver", None,
                        "the solver initialises without a dictionary error"),
    "dictionary-syntax": ("Repair the malformed dictionary", None,
                          "foamDictionary can read the file"),
    "field-clipping": ("Correct the field's inlet and wall treatment", None,
                       "the bounding warnings stop"),
    "thermophysical": ("Supply the expected properties file", None,
                       "the thermophysical model is constructed"),
    "run-failure": ("Address the first error in the log", None,
                    "the run gets past the point where it previously failed"),
}


@dataclass
class Experiment:
    """One test of one hypothesis."""
    number: int = 0
    hypothesis_key: str = ""
    objective: str = ""
    change_description: str = ""
    commands: list = field(default_factory=list)      # read-only diagnostics to run
    edits: list = field(default_factory=list)         # EditProposal-like records
    evidence_tested: list = field(default_factory=list)
    expected_outcome: str = ""
    success_criteria: str = ""
    if_successful: str = ""
    if_unsuccessful: str = ""
    outcome: str = PENDING
    observations: list = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def start(self) -> None:
        self.started_at = datetime.now().isoformat(timespec="seconds")

    def finish(self, outcome: str, observation: str = "") -> None:
        self.outcome = outcome
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        if observation:
            self.observations.append(observation)

    def summary(self) -> str:
        lines = [
            f"**Experiment {self.number}: {self.objective}**",
            f"- Testing: {self.hypothesis_key}",
            f"- Change: {self.change_description}",
            f"- Expected if correct: {self.expected_outcome}",
            f"- Success criteria: {self.success_criteria}",
        ]
        if self.commands:
            lines.append(f"- Diagnostics: {', '.join(' '.join(c) for c in self.commands)}")
        if self.outcome != PENDING:
            lines.append(f"- Outcome: **{self.outcome}**")
            for o in self.observations:
                lines.append(f"  - {o}")
        else:
            lines.append(f"- If it works: {self.if_successful}")
            lines.append(f"- If it does not: {self.if_unsuccessful}")
        return "\n".join(lines)


def plan_experiment(hypothesis: Hypothesis, number: int = 1,
                    remaining: list | None = None) -> Experiment:
    """
    Design the next experiment for a hypothesis.

    The plan deliberately tests ONE thing. If several settings are suspect, the
    loop runs several experiments rather than changing everything at once --
    otherwise a success tells you nothing about which change mattered.
    """
    change, commands, expected = FIRST_CHANGE.get(
        hypothesis.key,
        ("Investigate the files involved", None,
         "the observed behaviour changes in a way that implicates this cause"),
    )

    others = [h.title for h in (remaining or []) if h.key != hypothesis.key][:2]
    next_if_fail = (f"Rule this cause out and move to: {others[0]}"
                    if others else
                    "Rule this cause out and gather more evidence (a solver log or "
                    "checkMesh report) before changing anything else.")

    return Experiment(
        number=number,
        hypothesis_key=hypothesis.key,
        objective=f"Establish whether {hypothesis.title.lower()}",
        change_description=change,
        commands=[list(c) if isinstance(c, (list, tuple)) else [c] for c in (commands or [])],
        evidence_tested=list(hypothesis.evidence[:4]),
        expected_outcome=expected,
        success_criteria=(hypothesis.validation_steps[-1] if hypothesis.validation_steps
                          else "the behaviour changes as predicted"),
        if_successful="Keep the change, then re-check the case for any remaining issues.",
        if_unsuccessful=next_if_fail,
    )


def evaluate_findings(experiment: Experiment, hypothesis,
                      findings_before, findings_after) -> str | None:
    """
    Judge a change by whether the problem it targeted actually went away.

    Many fixes are static -- generating a mesh, adding a missing field, editing
    a dictionary -- and produce no solver log at all. Judging those by log
    analysis alone would call a working fix "inconclusive", so the primary test
    is simply: do the rules that triggered this hypothesis still fire?

    Returns None when it cannot tell, so the caller can fall back to the log.
    """
    if hypothesis is None or findings_after is None:
        return None

    targeted = set(hypothesis.triggered_rules or [])
    if not targeted:
        return None

    def firing(findings):
        return {getattr(f, "rule_id", "") or getattr(f, "check", "") for f in (findings or [])}

    before_ids = firing(findings_before)
    after_ids = firing(findings_after)

    was_firing = targeted & before_ids if findings_before is not None else targeted
    if not was_firing:
        return None

    still_firing = targeted & after_ids
    if not still_firing:
        experiment.finish(SUCCESS,
                          f"The problem no longer appears: {', '.join(sorted(was_firing))} "
                          f"stopped triggering.")
        return SUCCESS

    if len(still_firing) < len(was_firing):
        cleared = was_firing - still_firing
        experiment.finish(INCONCLUSIVE,
                          f"Partly improved: {', '.join(sorted(cleared))} cleared, but "
                          f"{', '.join(sorted(still_firing))} remains.")
        return INCONCLUSIVE

    # Nothing cleared. Only call it a failure if the change was actually applied.
    return None


def evaluate(experiment: Experiment, before, after) -> str:
    """
    Judge an experiment from the evidence, not from optimism.

    `before` and `after` are LogAnalysis-like objects (or None). The rule is
    deliberately strict: we only claim success when something measurably
    improved, and we never call an untested change a success.
    """
    if after is None:
        experiment.finish(INCONCLUSIVE,
                          "No solver log was produced, so the change could not be judged "
                          "from run behaviour.")
        return INCONCLUSIVE

    # Crash cleared?
    if before is not None and getattr(before, "crashed", False) and not getattr(after, "crashed", False):
        experiment.finish(SUCCESS, "The run no longer crashes.")
        return SUCCESS

    # Still crashing, but got further?
    b_steps = getattr(before, "n_steps", 0) or 0
    a_steps = getattr(after, "n_steps", 0) or 0
    if getattr(after, "crashed", False):
        if a_steps > b_steps * 1.5 and a_steps > b_steps + 2:
            experiment.finish(INCONCLUSIVE,
                              f"Still failing, but it reached {a_steps} steps instead of "
                              f"{b_steps} — partial improvement.")
            return INCONCLUSIVE
        experiment.finish(FAILURE, "The run still fails in the same way.")
        return FAILURE

    if getattr(after, "converged", False):
        experiment.finish(SUCCESS, "The solver reported convergence.")
        return SUCCESS

    b_div = {k for k, v in getattr(before, "residual_trend", {}).items() if v == "diverging"} \
        if before is not None else set()
    a_div = {k for k, v in getattr(after, "residual_trend", {}).items() if v == "diverging"}
    if b_div and not a_div:
        experiment.finish(SUCCESS, "Residuals are no longer diverging.")
        return SUCCESS
    if a_div - b_div:
        experiment.finish(FAILURE,
                          f"New fields started diverging: {', '.join(sorted(a_div - b_div))}.")
        return FAILURE

    if a_steps > b_steps:
        experiment.finish(INCONCLUSIVE,
                          f"The run progressed further ({b_steps} to {a_steps} steps) but has "
                          f"not converged.")
        return INCONCLUSIVE

    experiment.finish(INCONCLUSIVE, "No measurable change in behaviour.")
    return INCONCLUSIVE
