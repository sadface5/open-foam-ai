"""
Offline tests for the Phase 2 upgrades: complete case intelligence, the
cross-file rule engine, and OpenFOAM environment discovery.

Run it with:
    QT_QPA_PLATFORM=offscreen python tests/test_rules.py
(or, on Windows PowerShell:  $env:QT_QPA_PLATFORM="offscreen"; python tests/test_rules.py)

None of these tests need an API key, a network connection, or an OpenFOAM
installation. Fixtures are built in a temporary folder and removed afterwards.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src import case_survey  # noqa: E402
from src.openfoam_env import (FoamInstall, build_command, to_backend_path)  # noqa: E402
from src.rules import CRITICAL, run_all_checks, run_rules, rule_count  # noqa: E402
from src.rules.context import CaseContext, _parse_div_schemes  # noqa: E402


# --- fixture helpers ----------------------------------------------------------
def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _make_case(**overrides) -> Path:
    """A small but realistic steady incompressible case, tweakable per test."""
    root = Path(tempfile.mkdtemp(prefix="foamtest_"))
    files = {
        "system/controlDict": "application simpleFoam;\ndeltaT 1;\nendTime 100;\n",
        "system/fvSchemes": (
            "ddtSchemes { default steadyState; }\n"
            "divSchemes { default none; div(phi,U) bounded Gauss upwind;"
            " div((nuEff*dev2(T(grad(U))))) Gauss linear; }\n"
        ),
        "system/fvSolution": (
            "solvers { p { solver GAMG; } U { solver smoothSolver; } "
            "k { solver smoothSolver; } omega { solver smoothSolver; } }\n"
            "SIMPLE { nNonOrthogonalCorrectors 1; }\n"
            "relaxationFactors { fields { p 0.3; } equations { U 0.7; } }\n"
        ),
        "constant/turbulenceProperties": "simulationType RAS;\nRAS { RASModel kOmegaSST; }\n",
        "constant/transportProperties": "nu [0 2 -1 0 0 0 0] 1e-05;\n",
        "constant/polyMesh/boundary": (
            "2\n( inlet { type patch; nFaces 10; } "
            "walls { type wall; nFaces 40; } )\n"
        ),
        "constant/polyMesh/faces": "faces\n",
        "0/U": "boundaryField { inlet { type fixedValue; } walls { type noSlip; } }\n",
        "0/p": "boundaryField { inlet { type zeroGradient; } walls { type zeroGradient; } }\n",
        "0/k": "boundaryField { inlet { type fixedValue; } walls { type kqRWallFunction; } }\n",
        "0/omega": "boundaryField { inlet { type fixedValue; } walls { type omegaWallFunction; } }\n",
        "0/nut": "boundaryField { inlet { type calculated; } walls { type nutkWallFunction; } }\n",
    }
    files.update(overrides)
    for rel, text in files.items():
        if text is not None:
            _write(root, rel, text)
    return root


def _findings(root: Path):
    return run_all_checks(case_survey.read_case_full(str(root)),
                          survey=case_survey.survey_case(str(root)))


def _ids(findings):
    return {f.rule_id for f in findings}


# --- complete case intelligence (Upgrade 1) -----------------------------------
def test_survey_finds_time_and_processor_dirs():
    root = _make_case()
    try:
        for d in ("0.5", "100", "processor0", "processor1"):
            (root / d).mkdir(exist_ok=True)
        s = case_survey.survey_case(str(root))
        assert s["time_dirs"] == ["0", "0.5", "100"], s["time_dirs"]
        assert s["result_times"] == ["0.5", "100"]
        assert s["latest_time"] == "100" and s["ran_at_all"] is True
        assert s["is_decomposed"] and s["n_processor_dirs"] == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_survey_reads_log_tail_not_head():
    """Divergence appears at the END of a log, so we must keep the tail."""
    root = _make_case()
    try:
        body = "\n".join(f"Time = {i}" for i in range(20000))
        _write(root, "log.simpleFoam", body + "\nFATAL: Floating point exception\n")
        files = case_survey.read_case_full(str(root))
        log = files["log.simpleFoam"]
        assert "Floating point exception" in log, "tail was lost"
        assert len(log) <= case_survey.MAX_LOG_CHARS + 200
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_survey_discovers_uncurated_dicts_but_skips_binaries():
    root = _make_case()
    try:
        _write(root, "system/decomposeParDict", "numberOfSubdomains 4;\nmethod scotch;\n")
        _write(root, "constant/polyMesh/points", "x" * 5000)
        files = case_survey.read_case_full(str(root))
        assert "system/decomposeParDict" in files, "extra dictionary not discovered"
        assert "constant/polyMesh/points" not in files, "mesh binary should not be read"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- divergence-scheme parsing (regression for a real bug) --------------------
def test_div_schemes_keep_nested_parentheses():
    parsed = _parse_div_schemes(
        "default none; div(phi,U) bounded Gauss upwind;"
        " div((nuEff*dev2(T(grad(U))))) Gauss linear;"
    )
    assert "div(phi,U)" in parsed
    assert "div((nuEff*dev2(T(grad(U)))))" in parsed, "nested parens were truncated"
    assert parsed["div(phi,U)"] == "bounded Gauss upwind"


def test_stress_term_not_flagged_as_unbounded():
    """The stress term is always Gauss linear; flagging it is a false alarm."""
    ctx = CaseContext({"system/fvSchemes":
                       "divSchemes { div(phi,U) bounded Gauss upwind;"
                       " div((nuEff*dev2(T(grad(U))))) Gauss linear; }"})
    assert list(ctx.convection_div_schemes) == ["div(phi,U)"]
    assert "unbounded-div-scheme" not in _ids(run_rules(ctx))


# --- cross-file consistency (Upgrade 2) ---------------------------------------
def test_detects_missing_turbulence_field():
    root = _make_case(**{"0/omega": None})  # kOmegaSST without omega
    try:
        hits = [f for f in _findings(root) if f.rule_id == "turbulence-fields-mismatch"]
        assert hits and hits[0].severity == CRITICAL
        assert "omega" in hits[0].detail
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_detects_decomposition_mismatch():
    """The contradiction is invisible unless dict and disk are compared."""
    root = _make_case()
    try:
        _write(root, "system/decomposeParDict", "numberOfSubdomains 4;\n")
        for d in ("processor0", "processor1", "processor2"):
            (root / d).mkdir(exist_ok=True)
        hits = [f for f in _findings(root) if f.rule_id == "decomposition-mismatch"]
        assert hits, "did not notice 4 subdomains vs 3 processor folders"
        assert "4" in hits[0].detail and "3" in hits[0].detail
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_detects_wall_function_on_non_wall_patch():
    root = _make_case(**{
        "0/nut": "boundaryField { inlet { type nutkWallFunction; } "
                 "walls { type nutkWallFunction; } }\n",
    })
    try:
        hits = [f for f in _findings(root) if f.rule_id == "wall-function-on-non-wall"]
        assert hits, "wall function on a 'patch'-type patch was not caught"
        assert "inlet" in hits[0].detail
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_detects_over_constrained_patch():
    root = _make_case(**{
        "0/p": "boundaryField { inlet { type fixedValue; } walls { type zeroGradient; } }\n",
    })
    try:
        assert "patch-type-disagreement" in _ids(_findings(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_detects_solver_algorithm_mismatch():
    root = _make_case(**{
        "system/fvSolution": "solvers { p { solver GAMG; } }\nPIMPLE { }\n",
    })
    try:
        assert "solver-algorithm-mismatch" in _ids(_findings(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_clean_case_produces_no_critical_findings():
    """A well-formed case must not be spammed with false alarms."""
    root = _make_case()
    try:
        criticals = [f for f in _findings(root) if f.severity == CRITICAL]
        assert not criticals, f"false positives on a clean case: {[f.rule_id for f in criticals]}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_legacy_duplicate_is_suppressed():
    """The old and new turbulence checks must not both report the same problem."""
    root = _make_case(**{"0/omega": None})
    try:
        ids = [f.rule_id for f in _findings(root)]
        assert "legacy:turbulence-fields" not in ids, "duplicate legacy finding leaked through"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_engine_survives_garbage_input():
    """A broken rule or unparseable file must never crash the app."""
    for files in ({}, {"system/controlDict": "}}}{{{ nonsense ;;"}, {"0/U": ""}):
        run_rules(CaseContext(files))  # must not raise
    assert rule_count() > 15


# --- OpenFOAM environment discovery (Upgrade 5 foundation) --------------------
def test_wsl_path_translation():
    inst = FoamInstall(kind="wsl", label="t", bashrc="/opt/openfoam11/etc/bashrc", distro="Ubuntu")
    got = to_backend_path(r"C:\Users\me\case", inst)
    assert got.startswith("/mnt/c/"), got
    assert got.endswith("/case")


def test_docker_mounts_case_at_fixed_point():
    inst = FoamInstall(kind="docker", label="t", image="openfoam/openfoam11-paraview56")
    assert to_backend_path(r"C:\Users\me\case", inst) == "/case"
    argv = build_command(inst, ["checkMesh"], r"C:\Users\me\case")
    assert argv[0] == "docker" and "-v" in argv and "/case" in " ".join(argv)


def test_command_quoting_resists_injection():
    """A case path must never be able to inject a second shell command."""
    inst = FoamInstall(kind="native", label="t", bashrc="/opt/openfoam11/etc/bashrc")
    argv = build_command(inst, ["checkMesh"], "/tmp/it's a case; rm -rf /")
    script = argv[-1]
    assert "'\\''" in script, "apostrophe was not escaped"
    assert "; rm -rf /" not in script.replace("'\\''", ""), "injection escaped quoting"


def test_active_install_runs_command_directly():
    inst = FoamInstall(kind="active", label="t")
    assert build_command(inst, ["checkMesh", "-latestTime"], "/case") == ["checkMesh", "-latestTime"]


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
