"""
Offline tests for the conversational pipeline (no API calls).

Run it with:
    QT_QPA_PLATFORM=offscreen python tests/test_pipeline.py
(or, on Windows PowerShell:  $env:QT_QPA_PLATFORM="offscreen"; python tests/test_pipeline.py)

It checks intent classification / skill selection / routing for the scenarios the
spec asked for, that the internal step findings stay hidden, and that a full GUI
turn works end-to-end with the network call faked out.
"""
import os
import sys

# Make sure we can import the project when run from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.intent import BC, DIVERGENCE, NUM, classify_intent  # noqa: E402
from src.diagnoser import InternalAnalysis  # noqa: E402


# ---- intent classification / routing ----
def test_whole_case_audit():
    it = classify_intent("Check the whole case.", has_case=True)
    assert it.name == "whole_case_audit" and it.needs_diagnosis and len(it.skills) == 5


def test_targeted_one_boundary():
    it = classify_intent("Is my outlet boundary condition wrong?", has_case=True)
    assert it.name == "targeted_inspect"
    assert it.skills == [BC]
    assert "outlet" in it.focus


def test_followup_reuses_prior():
    it = classify_intent("What about the inlet?", has_case=True, has_prior=True)
    assert it.name == "follow_up" and it.needs_diagnosis is False


def test_simpler_explanation():
    it = classify_intent("Explain that more simply.", has_case=True, has_prior=True)
    assert it.name == "simpler_explanation" and it.needs_diagnosis is False
    assert it.detail_level == "simpler"


def test_what_to_change_first():
    it = classify_intent("What should I change first?", has_case=True, has_prior=True)
    assert it.name == "what_to_change_first" and it.needs_diagnosis is False
    assert it.detail_level == "brief"


def test_propose_edit():
    it = classify_intent("Please edit the file to set deltaT to 0.001.", has_case=True)
    assert it.name == "propose_edit"


def test_general_question_no_case():
    it = classify_intent("What does nNonOrthogonalCorrectors do?", has_case=False)
    assert it.name == "general_question" and it.needs_diagnosis is False


def test_multiple_skills_for_divergence():
    it = classify_intent("Why is my pressure diverging?", has_case=True)
    assert it.name == "why_failing"
    assert DIVERGENCE in it.skills and BC in it.skills and NUM in it.skills  # multi-skill


def test_no_log_still_runs_diagnosis():
    # Even without a solver log, a loaded case should still be analyzed.
    it = classify_intent("The run keeps crashing.", has_case=True)
    assert it.name == "why_failing" and it.needs_diagnosis is True


def test_repeat_question_reuses_prior():
    it = classify_intent(
        "why is the pressure diverging",
        has_case=True, has_prior=True,
        prior_questions=["Why is my pressure diverging?"],
    )
    assert it.name == "follow_up" and it.needs_diagnosis is False


# ---- the internal step findings must never be part of the public object ----
def test_internal_hides_step_findings():
    a = InternalAnalysis.from_dict({
        "selected_skills": ["Solver Divergence Debugger"],
        "step_findings": [{"skill": "x", "step": 1, "finding": "y", "status": "unknown"}],
        "ranked_causes": [{"cause": "z"}],
        "confidence": "low",
    })
    pub = a.public_dict()
    assert "step_findings" not in pub
    assert "ranked_causes" in pub and a.step_findings  # kept internally, hidden publicly


# ---- a full GUI turn, with the network call faked ----
def test_gui_turn_offline():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import src.gui.main_window as mw

    def fake_run_full_turn(*args, on_delta=None, **kwargs):
        if on_delta:
            on_delta("Most likely cause\n\n")
            on_delta("Missing 0/omega field.")
        return ("Most likely cause\n\nMissing 0/omega field.", {"confidence": "high", "ranked_causes": []})

    mw.run_full_turn = fake_run_full_turn  # patch the name used inside _on_send

    win = mw.MainWindow()
    assert win.skill_list.count() == 6 and win.active_skill == mw.AUTO_LABEL
    win.case_files = {"system/controlDict": "application simpleFoam;"}  # pretend a case is loaded
    win.input.setPlainText("why is it diverging?")
    win._on_send()

    worker = win.active_worker
    assert worker is not None
    worker.wait(5000)
    for _ in range(100):
        app.processEvents()
        if win.active_worker is None:
            break

    convo = win.conversations[win.convo_index]
    assert convo["last_response"] == "Most likely cause\n\nMissing 0/omega field."
    assert convo["last_internal"] == {"confidence": "high", "ranked_causes": []}
    # The user message + the assistant reply are both stored (plus the greeting).
    assert convo["messages"][-1]["role"] == "assistant"
    assert "omega" in convo["messages"][-1]["text"]


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed.")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
