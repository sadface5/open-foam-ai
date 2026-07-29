"""
Tests for the autonomous debugging loop and its supporting machinery:
ranked hypotheses, experiment planning, session memory, and the learned-case
database.

Tests needing a real OpenFOAM installation SKIP cleanly when none is present.

Run it with:
    QT_QPA_PLATFORM=offscreen python tests/test_autonomous.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.autonomous import (STOP_MAX_ITERATIONS, STOP_NEEDS_APPROVAL,  # noqa: E402
                            STOP_NO_HYPOTHESES, run_loop)
from src.case_survey import read_case_full, survey_case  # noqa: E402
from src.command_runner import best_install  # noqa: E402
from src.debug_memory import (AttemptRecord, DebugSession, format_recalled)  # noqa: E402
from src.experiment import (FAILURE, INCONCLUSIVE, SUCCESS, evaluate,  # noqa: E402
                            evaluate_findings, plan_experiment)
from src.hypothesis import build_hypotheses, format_hypotheses  # noqa: E402
from src.rules import run_all_checks  # noqa: E402


class Skip(Exception):
    """Marks a test as skipped rather than failed."""


class _F:
    """Minimal stand-in for a RuleFinding."""
    def __init__(self, rule_id, severity="critical", confidence="confirmed",
                 detail="", files=None, evidence=None, category="", title=""):
        self.rule_id = rule_id
        self.severity = severity
        self.confidence = confidence
        self.detail = detail
        self.files = files or []
        self.evidence = evidence or []
        self.category = category
        self.title = title


def _broken_case() -> str:
    return str(Path(__file__).resolve().parent.parent / "examples" / "broken_case")


# --- ranked hypotheses (Upgrade 8) --------------------------------------------
def test_hypotheses_are_ranked_by_confidence():
    hs = build_hypotheses([
        _F("no-mesh", "critical", "confirmed"),
        _F("unbounded-div-scheme", "warning", "possible"),
        _F("nonorthogonal-correctors-missing", "info", "possible"),
    ])
    scores = [h.confidence for h in hs]
    assert scores == sorted(scores, reverse=True), scores
    assert hs[0].key == "no-mesh"


def test_related_findings_merge_into_one_hypothesis():
    hs = build_hypotheses([
        _F("turbulence-fields-mismatch", "critical"),
        _F("turbulence-leftover-fields", "warning"),
    ])
    keys = [h.key for h in hs]
    assert keys.count("turbulence-setup") == 1, keys
    assert len(hs[0].triggered_rules) == 2


def test_corroboration_raises_confidence_but_never_to_certainty():
    one = build_hypotheses([_F("turbulence-fields-mismatch", "critical")])[0]
    two = build_hypotheses([
        _F("turbulence-fields-mismatch", "critical"),
        _F("turbulence-leftover-fields", "critical"),
    ])[0]
    assert two.confidence > one.confidence
    assert two.confidence <= 0.95


def test_generic_missing_file_is_not_a_mesh_hypothesis():
    """Regression: legacy:missing-file fires for ANY absent file."""
    hs = build_hypotheses([
        _F("legacy:missing-file", "info", detail="No turbulenceProperties found.")
    ])
    assert hs[0].key == "missing-files", hs[0].key
    assert "mesh" not in hs[0].title.lower()


def test_every_hypothesis_has_validation_steps():
    findings = run_all_checks(read_case_full(_broken_case()),
                             survey=survey_case(_broken_case()))
    for h in build_hypotheses(findings):
        assert h.validation_steps, h.key


def test_format_hypotheses_handles_empty():
    assert "No ranked hypotheses" in format_hypotheses([])


# --- experiments (Upgrade 7) --------------------------------------------------
def test_experiment_has_full_decision_structure():
    h = build_hypotheses([_F("no-mesh", "critical")])[0]
    e = plan_experiment(h, number=1)
    for attr in ("objective", "change_description", "expected_outcome",
                 "success_criteria", "if_successful", "if_unsuccessful"):
        assert getattr(e, attr), f"experiment missing {attr}"


def test_experiment_names_the_next_hypothesis_on_failure():
    hs = build_hypotheses([_F("no-mesh", "critical"),
                           _F("turbulence-fields-mismatch", "critical")])
    e = plan_experiment(hs[0], number=1, remaining=hs[1:])
    assert hs[1].title in e.if_unsuccessful


def test_findings_evaluation_detects_a_cleared_problem():
    h = build_hypotheses([_F("no-mesh", "critical")])[0]
    e = plan_experiment(h)
    verdict = evaluate_findings(e, h, [_F("no-mesh")], [])
    assert verdict == SUCCESS and e.outcome == SUCCESS


def test_findings_evaluation_reports_partial_progress():
    findings_before = [_F("turbulence-fields-mismatch"), _F("turbulence-leftover-fields")]
    h = build_hypotheses(findings_before)[0]
    e = plan_experiment(h)
    verdict = evaluate_findings(e, h, findings_before, [_F("turbulence-leftover-fields")])
    assert verdict == INCONCLUSIVE
    assert "Partly improved" in e.observations[0]


def test_findings_evaluation_defers_when_nothing_cleared():
    """Returning None lets the caller fall back to log-based judgement."""
    h = build_hypotheses([_F("no-mesh")])[0]
    e = plan_experiment(h)
    assert evaluate_findings(e, h, [_F("no-mesh")], [_F("no-mesh")]) is None


def test_log_evaluation_detects_cleared_crash():
    class L:
        def __init__(self, crashed, steps=10, converged=False, trend=None):
            self.crashed, self.n_steps = crashed, steps
            self.converged, self.residual_trend = converged, trend or {}
    h = build_hypotheses([_F("numerical-stability")])[0]
    e = plan_experiment(h)
    assert evaluate(e, L(True), L(False)) == SUCCESS


def test_log_evaluation_never_claims_success_without_evidence():
    h = build_hypotheses([_F("no-mesh")])[0]
    e = plan_experiment(h)
    assert evaluate(e, None, None) == INCONCLUSIVE


# --- session memory (Upgrade 9) -----------------------------------------------
def test_session_records_and_rules_out_failed_fixes():
    s = DebugSession(case_path="/tmp/case")
    s.record_attempt(AttemptRecord(iteration=1, hypothesis_key="time-step",
                                   description="Reduce deltaT", outcome="failure"))
    assert s.already_tried("time-step")
    assert "Reduce deltaT" in s.failed_fixes
    hs = build_hypotheses([_F("transient-fixed-large-dt", "warning"),
                           _F("no-mesh", "critical")])
    assert all(h.key != "time-step" for h in s.remaining(hs))


def test_session_records_successful_fixes():
    s = DebugSession()
    s.record_attempt(AttemptRecord(iteration=1, hypothesis_key="no-mesh",
                                   description="Run blockMesh", outcome="success",
                                   files_changed=["constant/polyMesh"]))
    assert "no-mesh" in s.confirmed_hypotheses
    assert "constant/polyMesh" in s.files_modified


def test_session_context_warns_against_repeating():
    s = DebugSession()
    s.record_attempt(AttemptRecord(iteration=1, description="Lower relaxation",
                                   outcome="failure"))
    ctx = s.as_context()
    assert "do not repeat" in ctx.lower() and "Lower relaxation" in ctx


def test_empty_session_has_no_context():
    assert DebugSession().as_context() == ""


def test_format_recalled_handles_empty():
    assert format_recalled([]) == ""


# --- the loop (Upgrade 4) -----------------------------------------------------
def test_loop_is_read_only_by_default():
    """Without approval the loop must plan but never modify the case."""
    case = _broken_case()
    before = {p.name for p in Path(case).rglob("*") if p.is_file()}
    result = run_loop(case, allow_writes=False, max_iterations=3)
    after = {p.name for p in Path(case).rglob("*") if p.is_file()}
    assert before == after, "the loop modified files without approval"
    assert result.stop_reason in (STOP_NEEDS_APPROVAL, STOP_NO_HYPOTHESES,
                                  STOP_MAX_ITERATIONS)


def test_loop_reports_what_it_needs_approval_for():
    result = run_loop(_broken_case(), allow_writes=False, max_iterations=3)
    assert result.blocked_actions, "loop should say what it is blocked on"
    assert result.experiments


def test_loop_emits_events_for_observability():
    events = []
    run_loop(_broken_case(), allow_writes=False, max_iterations=2,
             on_event=lambda k, p: events.append(k))
    assert "measure" in events and "hypotheses" in events and "stop" in events


def test_loop_respects_iteration_cap():
    result = run_loop(_broken_case(), allow_writes=False, max_iterations=2)
    assert result.iterations <= 2


def test_loop_survives_a_case_with_nothing_in_it():
    root = Path(tempfile.mkdtemp())
    try:
        result = run_loop(str(root), allow_writes=False, max_iterations=2)
        assert result.stop_reason  # must terminate with a reason, not raise
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_loop_never_repeats_a_refuted_hypothesis():
    session = DebugSession(case_path=_broken_case())
    session.record_attempt(AttemptRecord(iteration=1, hypothesis_key="no-mesh",
                                         description="Generate the mesh",
                                         outcome="failure"))
    result = run_loop(_broken_case(), allow_writes=False, max_iterations=3,
                      session=session)
    tested = {e.hypothesis_key for e in result.experiments}
    assert "no-mesh" not in tested, "loop retried an already-refuted hypothesis"


# --- live (needs real OpenFOAM) -----------------------------------------------
def test_live_loop_generates_a_mesh_and_stops():
    if best_install() is None:
        raise Skip("no OpenFOAM installation on this machine")
    from tests._loop_fixture import build_cavity_case  # noqa: WPS433

    case = build_cavity_case()
    try:
        assert not (case / "constant/polyMesh/faces").is_file()
        result = run_loop(str(case), allow_writes=True, max_iterations=4)
        assert (case / "constant/polyMesh/faces").is_file(), "loop did not generate the mesh"
        assert result.resolved, result.stop_reason
        assert any(e.outcome == SUCCESS for e in result.experiments)
    finally:
        shutil.rmtree(case, ignore_errors=True)


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = skipped = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Skip as s:
            skipped += 1
            print(f"  SKIP  {t.__name__}: {s}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
    passed = len(tests) - failed - skipped
    print(f"\n{passed}/{len(tests) - skipped} tests passed"
          + (f" ({skipped} skipped)" if skipped else "") + ".")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
