"""
Offline tests for the Phase 3 upgrades: log intelligence, mesh intelligence,
and working-vs-broken case comparison.

Run it with:
    QT_QPA_PLATFORM=offscreen python tests/test_analysis.py

No API key, network, or OpenFOAM installation required.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.case_compare import compare_cases  # noqa: E402
from src.log_intelligence import analyze_log, analyze_logs  # noqa: E402
from src.mesh_intelligence import (analyze_checkmesh, find_checkmesh_output,  # noqa: E402
                                   relate_to_solver)


# --- helpers ------------------------------------------------------------------
def _log(n=25, diverge_from=10, courant_growth=1.18, clip_k=True):
    """Build a synthetic solver log with controllable behaviour."""
    out = []
    for i in range(1, n + 1):
        co = 0.4 * (courant_growth ** i)
        ur = 1e-4 * (1.6 ** i) if i > diverge_from else 1e-4 / (1.3 ** i)
        out.append(
            f"Courant Number mean: {co * 0.3:.4g} max: {co:.4g}\n"
            f"Time = {i * 0.01:g}\n\n"
            f"smoothSolver:  Solving for Ux, Initial residual = {ur:.4g}, "
            f"Final residual = 1e-07, No Iterations 3\n"
            f"GAMG:  Solving for p, Initial residual = {1e-3 / (1.1 ** i):.4g}, "
            f"Final residual = 1e-06, No Iterations 8\n"
            f"time step continuity errors : sum local = 1e-09, global = -1e-12, "
            f"cumulative = {1e-9 * (2.5 ** i):.4g}\n"
            + ("bounding k, min: -0.0012 max: 0.45 average: 0.02\n" if clip_k else "")
        )
    return "\n".join(out)


def _write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _base_case(root: Path):
    _write(root, "system/controlDict", "application simpleFoam;\ndeltaT 1;\nendTime 500;\n")
    _write(root, "system/fvSchemes",
           "ddtSchemes { default steadyState; }\n"
           "divSchemes { div(phi,U) bounded Gauss upwind; }\n")
    _write(root, "system/fvSolution",
           "solvers { p { solver GAMG; } }\nSIMPLE { }\n"
           "relaxationFactors { fields { p 0.3; } }\n")
    _write(root, "constant/turbulenceProperties", "simulationType RAS;\nRAS { RASModel kEpsilon; }\n")
    _write(root, "constant/transportProperties", "nu [0 2 -1 0 0 0 0] 1e-05;\n")
    _write(root, "0/U", "boundaryField { inlet { type fixedValue; } }\n")
    _write(root, "0/p", "boundaryField { inlet { type zeroGradient; } }\n")
    _write(root, "0/k", "boundaryField { inlet { type fixedValue; } }\n")
    _write(root, "0/epsilon", "boundaryField { inlet { type fixedValue; } }\n")


CHECKMESH_BAD = """
Mesh stats
    points:           182000
    faces:            521000
    cells:            170000

Checking geometry...
    Max aspect ratio = 1240.5 OK.
    Min volume = 2.1e-13. Max volume = 3.4e-06.  Total volume = 1.  Cell volumes OK.
    Mesh non-orthogonality Max: 78.42 average: 14.9
 *Number of severely non-orthogonal (> 70 degrees) faces: 412.
 ***Max skewness = 11.8, 27 highly skew faces detected
Failed 2 mesh checks.
"""

CHECKMESH_GOOD = """
Mesh stats
    points:           9000
    cells:            8000

Checking geometry...
    Max aspect ratio = 3.2 OK.
    Min volume = 1e-08. Max volume = 2e-06.  Cell volumes OK.
    Mesh non-orthogonality Max: 22.5 average: 4.1
    Non-orthogonality check OK.
    Max skewness = 0.9 OK.
Mesh OK.
"""


# --- log intelligence (Upgrade 13) --------------------------------------------
def test_log_detects_crash_and_first_diverging_field():
    a = analyze_log(_log() + "\n#0  Foam::sigFpe::sigHandler(int)\nFloating point exception\n")
    assert a.parsed and a.crashed
    assert "floating-point" in a.crash_reason
    assert a.first_diverging_field == "Ux", a.first_diverging_field


def test_log_classifies_residual_trends_per_field():
    a = analyze_log(_log())
    assert a.residual_trend["Ux"] == "diverging"
    assert a.residual_trend["p"] == "improving"


def test_log_tracks_courant_growth():
    a = analyze_log(_log())
    assert a.courant_start < a.courant_end
    assert a.courant_max_seen >= a.courant_end
    assert any("Courant" in n for n in a.notes)


def test_log_counts_bounding_and_warns():
    a = analyze_log(_log(clip_k=True))
    assert a.bounding_counts.get("k") == 25
    assert any("clipped" in n for n in a.notes)
    assert not analyze_log(_log(clip_k=False)).bounding_counts


def test_log_detects_continuity_growth():
    assert analyze_log(_log()).continuity_growing is True


def test_log_handles_failure_before_first_timestep():
    a = analyze_log("--> FOAM FATAL IO ERROR\nCannot read system/fvSchemes\n")
    assert a.crashed and a.n_steps == 0
    assert "dictionary" in a.crash_reason


def test_log_ignores_non_log_text():
    assert analyze_log("").parsed is False
    assert analyze_log("just some prose about CFD").parsed is False


def test_analyze_logs_picks_out_log_files():
    files = {"log.simpleFoam": _log(), "system/controlDict": "application simpleFoam;"}
    got = analyze_logs(files)
    assert list(got) == ["log.simpleFoam"]


# --- mesh intelligence (Upgrade 14) -------------------------------------------
def test_checkmesh_parses_all_metrics():
    q = analyze_checkmesh(CHECKMESH_BAD)
    assert q.parsed and q.n_cells == 170000
    assert q.max_non_orthogonality == 78.42
    assert q.n_severely_non_orthogonal == 412
    assert q.max_skewness == 11.8
    assert q.max_aspect_ratio == 1240.5
    assert q.failed_checks == 2


def test_checkmesh_recommends_correctors_by_severity():
    assert analyze_checkmesh(CHECKMESH_BAD).recommended_non_orthogonal_correctors() == 3
    assert analyze_checkmesh(CHECKMESH_GOOD).recommended_non_orthogonal_correctors() == 0


def test_good_mesh_reports_no_problems():
    q = analyze_checkmesh(CHECKMESH_GOOD)
    assert q.parsed and q.mesh_ok
    assert not q.is_problematic(), q.problems


def test_mesh_relates_stalled_pressure_to_non_orthogonality():
    """The whole point of Upgrade 14: join the mesh report to solver behaviour."""
    stalled = "\n".join(
        f"Time = {i}\n\nGAMG:  Solving for p, Initial residual = 0.001, "
        f"Final residual = 1e-06, No Iterations 40\n" for i in range(1, 20)
    )
    notes = relate_to_solver(analyze_checkmesh(CHECKMESH_BAD), analyze_log(stalled))
    assert notes and "non-orthogonal" in notes[0]
    assert "nNonOrthogonalCorrectors" in notes[0]


def test_mesh_without_checkmesh_says_so():
    q = analyze_checkmesh("")
    assert q.parsed is False
    assert "could not be assessed" in q.summary()


def test_find_checkmesh_output_locates_report():
    assert find_checkmesh_output({"log.checkMesh": CHECKMESH_GOOD}) is not None
    assert find_checkmesh_output({"system/controlDict": "application simpleFoam;"}) is None


# --- case comparison (Upgrade 12) ---------------------------------------------
def test_comparison_ranks_breaking_change_first():
    w, b = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    try:
        _base_case(w)
        _base_case(b)
        # Fatal: remove a required field. Cosmetic: change endTime.
        (b / "0/epsilon").unlink()
        _write(b, "system/controlDict", "application simpleFoam;\ndeltaT 1;\nendTime 900;\n")
        result = compare_cases(str(w), str(b))
        assert result.differences
        assert result.differences[0].kind == "missing-field"
        assert "epsilon" in result.differences[0].where
    finally:
        shutil.rmtree(w, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_comparison_ignores_cosmetic_changes():
    """endTime/writeInterval churn must not drown the real signal."""
    w, b = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    try:
        _base_case(w)
        _base_case(b)
        _write(b, "system/controlDict",
               "application simpleFoam;\ndeltaT 1;\nendTime 9000;\nwriteInterval 3;\n")
        result = compare_cases(str(w), str(b))
        assert not result.differences, [d.where for d in result.differences]
    finally:
        shutil.rmtree(w, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_comparison_flags_scheme_and_relaxation_regressions():
    w, b = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    try:
        _base_case(w)
        _base_case(b)
        _write(b, "system/fvSchemes",
               "ddtSchemes { default steadyState; }\ndivSchemes { div(phi,U) Gauss linear; }\n")
        _write(b, "system/fvSolution",
               "solvers { p { solver GAMG; } }\nSIMPLE { }\n"
               "relaxationFactors { fields { p 0.9; } }\n")
        kinds = {d.kind for d in compare_cases(str(w), str(b)).differences}
        assert "div-scheme" in kinds and "relaxation" in kinds
    finally:
        shutil.rmtree(w, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_comparison_detects_bc_type_change():
    w, b = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    try:
        _base_case(w)
        _base_case(b)
        _write(b, "0/U", "boundaryField { inlet { type zeroGradient; } }\n")
        diffs = compare_cases(str(w), str(b)).differences
        assert any(d.kind == "bc-type" and "inlet" in d.where for d in diffs)
    finally:
        shutil.rmtree(w, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_identical_cases_have_no_differences():
    w, b = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    try:
        _base_case(w)
        _base_case(b)
        result = compare_cases(str(w), str(b))
        assert not result.differences
        assert "No meaningful differences" in result.summary()
    finally:
        shutil.rmtree(w, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


# --- the compare button in the GUI --------------------------------------------
def _gui_with_cases():
    """Build a MainWindow plus a working/broken pair. Returns (win, working, broken)."""
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    from PySide6.QtWidgets import QApplication
    from src.gui import main_window as mw

    if QApplication.instance() is None:
        QApplication([])
    w, b = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    _base_case(w)
    _base_case(b)
    (b / "0/epsilon").unlink()          # the fatal difference
    win = mw.MainWindow()
    win.case_root = str(b)
    return win, w, b


def _last_message(win):
    return win.conversations[win.convo_index]["messages"][-1]["text"]


def test_compare_button_requires_a_case_first():
    win, w, b = _gui_with_cases()
    try:
        win.case_root = None
        win._compare_with_working_case()
        assert "Select the case" in _last_message(win)
    finally:
        shutil.rmtree(w, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_compare_button_posts_ranked_table():
    from PySide6.QtWidgets import QFileDialog
    win, w, b = _gui_with_cases()
    original = QFileDialog.getExistingDirectory
    try:
        QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(w))
        win._compare_with_working_case()
        text = _last_message(win)
        assert "Case comparison" in text
        assert "0/epsilon" in text and "95" in text
        assert win.conversations[win.convo_index]["last_comparison"]
    finally:
        QFileDialog.getExistingDirectory = original
        shutil.rmtree(w, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_compare_button_handles_cancelled_dialog():
    from PySide6.QtWidgets import QFileDialog
    win, w, b = _gui_with_cases()
    original = QFileDialog.getExistingDirectory
    try:
        before = len(win.conversations[win.convo_index]["messages"])
        QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
        win._compare_with_working_case()
        assert len(win.conversations[win.convo_index]["messages"]) == before
    finally:
        QFileDialog.getExistingDirectory = original
        shutil.rmtree(w, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


# --- Docker mount-path translation (regression for a real bug) ----------------
def test_docker_toolbox_mount_path_translation():
    """
    Docker Toolbox reaches the daemon over TCP and cannot see Windows paths --
    passing C:\\... produces 'invalid mode' because of the drive-letter colon.
    """
    import src.openfoam_env as env

    original = os.environ.get("DOCKER_HOST")
    try:
        os.environ["DOCKER_HOST"] = "tcp://192.168.99.100:2376"
        got = env.to_mount_path(r"C:\Users\me\case")
        if sys.platform == "win32":
            assert got.startswith("/c/"), got
            assert ":" not in got, "a colon would break the -v src:dst syntax"
    finally:
        if original is None:
            os.environ.pop("DOCKER_HOST", None)
        else:
            os.environ["DOCKER_HOST"] = original


def test_docker_command_sources_bashrc_when_known():
    """Most OpenFOAM images leave solvers off PATH until etc/bashrc is sourced."""
    from src.openfoam_env import FoamInstall, build_command

    inst = FoamInstall(kind="docker", label="t", image="of:latest",
                       bashrc="/usr/lib/openfoam/openfoam2012/etc/bashrc")
    script = build_command(inst, ["checkMesh"], r"C:\Users\me\case")[-1]
    assert script.startswith("source /usr/lib/openfoam/openfoam2012/etc/bashrc &&")
    assert script.endswith("checkMesh")


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
