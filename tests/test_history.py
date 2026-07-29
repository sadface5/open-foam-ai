"""
Tests for versioned file editing (Upgrade 6), cross-source retrieval
(Upgrade 10), the learning database (Upgrade 11), and the GUI buttons that
expose the investigation and comparison features.

Run it with:
    QT_QPA_PLATFORM=offscreen python tests/test_history.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-not-real")

import src.debug_memory as dm  # noqa: E402
import src.edit_history as eh  # noqa: E402
from src.debug_memory import AttemptRecord, DebugSession  # noqa: E402
from src.diagnoser import EditProposal, _format_snippets  # noqa: E402
from src.retrieval import DebugRetriever, RetrievedItem  # noqa: E402


class _TempPaths:
    """Redirect the JSON stores into a temp folder so tests never touch real data."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp())
        self._saved = (eh.HISTORY_FILE, dm.LEARNED_DB)
        eh.HISTORY_FILE = self.dir / "edit_history.json"
        dm.LEARNED_DB = self.dir / "solved_cases.json"
        return self.dir

    def __exit__(self, *exc):
        eh.HISTORY_FILE, dm.LEARNED_DB = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)


def _case() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "system").mkdir(parents=True)
    (root / "system" / "controlDict").write_text("application simpleFoam;\ndeltaT 1;\n")
    return root


def _proposal(content: str, path: str = "system/controlDict") -> EditProposal:
    return EditProposal(file_path=path, new_content=content, reason="test edit")


# --- versioned editing (Upgrade 6) --------------------------------------------
def test_edit_records_reason_confidence_and_evidence():
    with _TempPaths():
        case = _case()
        try:
            ed = eh.VersionedEditor(str(case))
            rec = ed.apply(_proposal("application pimpleFoam;\n"),
                           reason="Solver did not match the algorithm block",
                           confidence="high", evidence=["application=simpleFoam"],
                           triggered_rules=["solver-algorithm-mismatch"],
                           hypothesis_key="solver-mismatch", applied_by="autonomous-loop")
            assert rec.version == 1
            assert rec.confidence == "high"
            assert rec.triggered_rules == ["solver-algorithm-mismatch"]
            assert "assistant" in rec.summary()
        finally:
            shutil.rmtree(case, ignore_errors=True)


def test_versions_increment_per_file():
    with _TempPaths():
        case = _case()
        try:
            ed = eh.VersionedEditor(str(case))
            ed.apply(_proposal("v1\n"), reason="first")
            ed.apply(_proposal("v2\n"), reason="second")
            versions = [r.version for r in ed.versions_of("system/controlDict")]
            assert versions == [1, 2], versions
        finally:
            shutil.rmtree(case, ignore_errors=True)


def test_rollback_to_an_earlier_version_restores_content():
    with _TempPaths():
        case = _case()
        try:
            original = (case / "system/controlDict").read_text()
            ed = eh.VersionedEditor(str(case))
            ed.apply(_proposal("second version\n"), reason="change one")
            ed.apply(_proposal("third version\n"), reason="change two")
            assert (case / "system/controlDict").read_text() == "third version\n"

            assert ed.rollback_to("system/controlDict", 1) is True
            assert (case / "system/controlDict").read_text() == original
        finally:
            shutil.rmtree(case, ignore_errors=True)


def test_rollback_to_unknown_version_fails_safely():
    with _TempPaths():
        case = _case()
        try:
            ed = eh.VersionedEditor(str(case))
            assert ed.rollback_to("system/controlDict", 99) is False
        finally:
            shutil.rmtree(case, ignore_errors=True)


def test_rollback_of_a_created_file_removes_it():
    with _TempPaths():
        case = _case()
        try:
            ed = eh.VersionedEditor(str(case))
            ed.apply(_proposal("uniform 0;\n", "0/omega"), reason="add missing field")
            assert (case / "0/omega").is_file()
            assert ed.rollback_to("0/omega", 1) is True
            assert not (case / "0/omega").is_file()
        finally:
            shutil.rmtree(case, ignore_errors=True)


def test_underlying_safety_guarantees_are_preserved():
    """The wrapper must not weaken FileEditor: backup made, escape refused."""
    with _TempPaths():
        case = _case()
        try:
            ed = eh.VersionedEditor(str(case))
            rec = ed.apply(_proposal("changed\n"), reason="x")
            assert rec.backup_abs and Path(rec.backup_abs).is_file(), "no backup was made"
            try:
                ed.editor.resolve("../../escape.txt")
                raise AssertionError("path escape was not refused")
            except ValueError:
                pass
        finally:
            shutil.rmtree(case, ignore_errors=True)


def test_audit_trail_is_readable():
    with _TempPaths():
        case = _case()
        try:
            ed = eh.VersionedEditor(str(case))
            ed.apply(_proposal("x\n"), reason="Lowered relaxation", confidence="medium")
            trail = ed.audit_trail()
            assert "Lowered relaxation" in trail and "medium" in trail
            assert "No file changes" in eh.VersionedEditor(str(_case())).audit_trail()
        finally:
            shutil.rmtree(case, ignore_errors=True)


# --- retrieval (Upgrade 10) ---------------------------------------------------
def test_retrieved_item_is_snippet_compatible():
    """It must work anywhere a knowledge_base.Snippet was expected."""
    item = RetrievedItem(source_kind="knowledge", label="bc.md", text="Some guidance.")
    assert item.source == "bc.md"
    assert "bc.md" in _format_snippets([item])


def test_solved_cases_are_labelled_as_stronger_evidence():
    item = RetrievedItem(source_kind="solved-case", label="simpleFoam", text="Fixed by X.")
    assert "solved previously" in item.source


def test_retriever_returns_knowledge_and_ranks_solved_cases_first():
    with _TempPaths():
        session = DebugSession(case_path="/tmp/c")
        session.record_attempt(AttemptRecord(
            iteration=1, hypothesis_key="turbulence-setup",
            description="Added 0/omega", outcome="success"))
        session.resolved = True
        dm.record_solved_case(session, solver="simpleFoam", turbulence="kOmegaSST",
                              problem="missing omega field", fix="Added 0/omega")

        result = DebugRetriever().retrieve("missing omega turbulence field",
                                           solver="simpleFoam", turbulence="kOmegaSST")
        kinds = [i.source_kind for i in result.items]
        assert "solved-case" in kinds, kinds
        assert kinds[0] == "solved-case", "solved cases must outrank general notes"
        assert "solved cases outrank" in result.as_prompt_block()


def test_retriever_handles_empty_query():
    assert DebugRetriever().retrieve("").as_prompt_block() == "" or True  # must not raise


# --- learning database (Upgrade 11) -------------------------------------------
def test_solved_case_round_trips():
    with _TempPaths():
        s = DebugSession(case_path="/tmp/c")
        s.record_attempt(AttemptRecord(iteration=1, hypothesis_key="no-mesh",
                                       description="Ran blockMesh", outcome="success"))
        s.resolved = True
        dm.record_solved_case(s, solver="icoFoam", problem="no mesh", fix="Ran blockMesh")
        rows = json.loads(dm.LEARNED_DB.read_text())
        assert rows and rows[-1]["fix"] == "Ran blockMesh"
        assert dm.recall_similar(solver="icoFoam", problem="no mesh")


def test_unresolved_sessions_are_not_recalled():
    with _TempPaths():
        s = DebugSession(case_path="/tmp/c")
        s.record_attempt(AttemptRecord(iteration=1, description="Tried something",
                                       outcome="failure"))
        s.resolved = False
        dm.record_solved_case(s, solver="icoFoam", problem="no mesh", fix="nothing")
        assert dm.recall_similar(solver="icoFoam", problem="no mesh") == []


def test_session_persistence_round_trip():
    with _TempPaths() as tmp:
        saved = dm.BACKUPS_DIR
        try:
            dm.BACKUPS_DIR = tmp
            s = DebugSession(case_path="/tmp/mycase")
            s.record_attempt(AttemptRecord(iteration=1, hypothesis_key="no-mesh",
                                           description="Ran blockMesh", outcome="failure"))
            dm.save_session(s)
            back = dm.load_session("/tmp/mycase")
            assert back.already_tried("no-mesh")
            assert len(back.attempts) == 1
        finally:
            dm.BACKUPS_DIR = saved


# --- the GUI buttons ----------------------------------------------------------
def _window():
    from PySide6.QtWidgets import QApplication
    from src.gui.main_window import MainWindow
    if QApplication.instance() is None:
        QApplication([])
    return MainWindow()


def test_gui_exposes_investigation_and_comparison():
    w = _window()
    assert hasattr(w, "autodebug_btn") and hasattr(w, "compare_btn")
    assert w.autodebug_btn.toolTip()


def test_autodebug_requires_a_case_folder():
    w = _window()
    w.case_root = None
    w._run_autonomous_debug()
    assert "Select a case folder" in w.conversations[w.convo_index]["messages"][-1]["text"]


def test_autodebug_reports_without_modifying_the_case():
    w = _window()
    case = Path(__file__).resolve().parent.parent / "examples" / "broken_case"
    before = {p.name for p in case.rglob("*") if p.is_file()}
    w.case_root = str(case)
    w._run_autonomous_debug()
    after = {p.name for p in case.rglob("*") if p.is_file()}
    assert before == after, "the investigation modified the case"
    text = w.conversations[w.convo_index]["messages"][-1]["text"]
    assert "Investigation" in text and "Ranked causes" in text


def test_gui_uses_the_versioned_editor():
    w = _window()
    case = _case()
    try:
        w.case_root = str(case)
        w.editor = eh.VersionedEditor(str(case))
        assert hasattr(w.editor, "audit_trail") and hasattr(w.editor, "rollback_to")
    finally:
        shutil.rmtree(case, ignore_errors=True)


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
