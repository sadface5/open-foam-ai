"""
Tests for the OpenFOAM command runner and multi-installation discovery.

Most of these are pure unit tests and run anywhere. The few that need a real
OpenFOAM installation SKIP cleanly when none is present, so the suite still
passes on a machine with no OpenFOAM, in CI, or for a new contributor.

Run it with:
    QT_QPA_PLATFORM=offscreen python tests/test_runner.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src import command_runner as cr  # noqa: E402
from src.openfoam_env import (FoamInstall, best_install, build_command,  # noqa: E402
                              detect_installs, environment_report)


class Skip(Exception):
    """Raised to mark a test as skipped rather than failed."""


def _case_dir() -> str:
    return str(Path(__file__).resolve().parent.parent / "examples" / "broken_case")


# --- allowlist: the security boundary -----------------------------------------
def test_arbitrary_shell_commands_are_refused():
    """A prompt injection in a log or dictionary must never become execution."""
    for bad in (["rm", "-rf", "/"], ["bash", "-c", "echo pwned"], ["curl", "evil.example"],
                ["python", "-c", "import os"], ["sh"], []):
        try:
            cr.check_allowed(bad)
        except cr.CommandNotAllowed:
            continue
        raise AssertionError(f"{bad} should have been refused")


def test_read_only_commands_allowed_without_approval():
    for good in (["checkMesh"], ["foamDictionary", "-entry", "application"],
                 ["postProcess", "-func", "mag(U)"], ["foamListTimes"]):
        cr.check_allowed(good)  # must not raise


def test_write_commands_require_explicit_approval():
    for cmd in (["blockMesh"], ["decomposePar"], ["setFields"], ["renumberMesh"]):
        try:
            cr.check_allowed(cmd, allow_write=False)
            raise AssertionError(f"{cmd} should need approval")
        except cr.CommandNotAllowed:
            pass
        cr.check_allowed(cmd, allow_write=True)  # allowed once approved


def test_solvers_cannot_run_through_run_command():
    for solver in (["simpleFoam"], ["pimpleFoam"], ["interFoam"]):
        try:
            cr.check_allowed(solver, allow_write=True)
            raise AssertionError(f"{solver} should be routed through run_solver()")
        except cr.CommandNotAllowed as e:
            assert "run_solver" in str(e)


# --- parallel support ---------------------------------------------------------
def test_parallel_command_uses_mpirun_and_parallel_flag():
    assert cr.build_parallel_command("simpleFoam", 4) == \
        ["mpirun", "-np", "4", "simpleFoam", "-parallel"]


def test_serial_command_has_no_mpirun():
    assert cr.build_parallel_command("simpleFoam", 1) == ["simpleFoam"]
    assert cr.build_parallel_command("simpleFoam", 0) == ["simpleFoam"]


def test_parallel_extra_args_are_preserved():
    got = cr.build_parallel_command("pimpleFoam", 8, ["-noFunctionObjects"])
    assert got[-1] == "-noFunctionObjects" and got[:3] == ["mpirun", "-np", "8"]


def test_subdomains_read_from_decompose_par_dict():
    root = Path(tempfile.mkdtemp())
    try:
        d = root / "system"
        d.mkdir(parents=True)
        (d / "decomposeParDict").write_text(
            "FoamFile { version 2.0; }\nnumberOfSubdomains 12;\nmethod scotch;\n")
        assert cr.subdomains_for(str(root)) == 12
    finally:
        __import__("shutil").rmtree(root, ignore_errors=True)


def test_subdomains_absent_when_no_dict():
    root = Path(tempfile.mkdtemp())
    try:
        assert cr.subdomains_for(str(root)) is None
    finally:
        __import__("shutil").rmtree(root, ignore_errors=True)


# --- confinement --------------------------------------------------------------
def test_missing_case_folder_is_rejected():
    try:
        cr.run_command(["checkMesh"], str(Path(tempfile.gettempdir()) / "definitely_not_here_xyz"))
        raise AssertionError("a non-existent case folder should be rejected")
    except (NotADirectoryError, RuntimeError):
        pass


# --- multi-installation discovery ---------------------------------------------
def test_detection_never_raises_and_is_deduplicated():
    installs = detect_installs()
    keys = [i.key for i in installs]
    assert len(keys) == len(set(keys)), "detection returned duplicates"
    assert isinstance(environment_report(), str)


def test_installs_are_ordered_best_first():
    installs = detect_installs()
    priorities = [i.priority for i in installs]
    assert priorities == sorted(priorities), priorities


def test_module_backend_builds_module_load():
    inst = FoamInstall(kind="module", label="t", module="openfoam/2312")
    script = build_command(inst, ["checkMesh"], "/tmp/case")[-1]
    assert script.startswith("module load openfoam/2312 &&")


def test_each_backend_produces_a_launchable_argv():
    """Every supported install kind must build a runnable argument list."""
    cases = [
        FoamInstall(kind="active", label="t"),
        FoamInstall(kind="native", label="t", bashrc="/opt/openfoam11/etc/bashrc"),
        FoamInstall(kind="wsl", label="t", bashrc="/opt/openfoam11/etc/bashrc", distro="Ubuntu"),
        FoamInstall(kind="docker", label="t", image="of:latest"),
        FoamInstall(kind="module", label="t", module="openfoam/11"),
    ]
    for inst in cases:
        argv = build_command(inst, ["checkMesh"], "/tmp/case")
        assert isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv), inst.kind


# --- live tests (skipped when OpenFOAM is unavailable) ------------------------
def test_live_checkmesh_runs():
    if best_install() is None:
        raise Skip("no OpenFOAM installation on this machine")
    r = cr.run_check_mesh(_case_dir())
    # broken_case has no mesh, so a NON-zero exit with a FOAM error is correct.
    assert r.exit_code != 0, "expected checkMesh to fail on a case with no mesh"
    assert "FOAM" in r.output or "polyMesh" in r.output, r.tail(300)


def test_live_foam_dictionary_reads_application():
    if best_install() is None:
        raise Skip("no OpenFOAM installation on this machine")
    r = cr.read_dictionary_entry(_case_dir(), "system/controlDict", "application")
    assert r.ok, r.tail(300)
    assert "Foam" in r.stdout, r.stdout[:200]


def test_live_disallowed_command_never_reaches_openfoam():
    if best_install() is None:
        raise Skip("no OpenFOAM installation on this machine")
    try:
        cr.run_command(["bash", "-c", "echo pwned"], _case_dir())
        raise AssertionError("shell command was not blocked")
    except cr.CommandNotAllowed:
        pass


# --- "run checkMesh" must actually run it (reported bug) ----------------------
def test_run_verb_plus_utility_is_detected_as_a_command():
    from src.intent import classify_intent, detect_command

    for phrase, expected in [("run checkMesh", "checkMesh"),
                             ("can you run checkMesh on my case", "checkMesh"),
                             ("please execute blockMesh", "blockMesh"),
                             ("run foamDictionary", "foamDictionary")]:
        assert detect_command(phrase) == [expected], phrase
        intent = classify_intent(phrase, has_case=True)
        assert intent.name == "run_command", f"{phrase} -> {intent.name}"
        assert intent.needs_diagnosis is False
        assert intent.command == [expected]


def test_questions_about_a_utility_are_not_executed():
    """"why did checkMesh complain" is a question, not an instruction."""
    from src.intent import classify_intent, detect_command

    for phrase in ("why did checkMesh complain", "what does checkMesh check",
                   "checkMesh said my mesh is skewed", "is blockMesh needed here"):
        assert detect_command(phrase) is None, phrase
        assert classify_intent(phrase, has_case=True).name != "run_command", phrase


def test_trivial_messages_do_not_trigger_a_full_diagnosis():
    """Regression: every message used to run a 3-skill diagnosis (2 API calls)."""
    from src.intent import classify_intent

    for phrase in ("thanks", "ok", "hello", "great", "what is a Courant number",
                   "what does fvSchemes do", "how does SIMPLE work"):
        intent = classify_intent(phrase, has_case=True)
        assert intent.needs_diagnosis is False, f"{phrase} -> {intent.name} (would diagnose)"


def test_real_diagnostic_questions_still_diagnose():
    """The speed fix must not stop it working when a diagnosis IS wanted."""
    from src.intent import classify_intent

    for phrase in ("why is this diverging", "my case blew up with nan",
                   "check the whole case", "is my outlet boundary condition correct"):
        assert classify_intent(phrase, has_case=True).needs_diagnosis is True, phrase


def test_detection_is_cached():
    """Detection shells out to docker/wsl; repeating it every turn is wasted time."""
    import time

    from src.openfoam_env import detect_installs, refresh_installs

    refresh_installs()
    start = time.time()
    for _ in range(10):
        detect_installs()
    assert time.time() - start < 0.5, "detection does not appear to be cached"


def test_clean_output_strips_the_openfoam_banner():
    r = cr.CommandResult(stdout=(
        "/*------ F ield | OpenFOAM: The Open Source CFD Toolbox ------*/\n"
        "Build  : _79e353b8\nExec   : checkMesh\nPID    : 378\n"
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * //\n"
        "Mesh stats\n    cells: 400\nMesh OK.\n"))
    cleaned = r.clean_output()
    assert "Mesh stats" in cleaned and "Mesh OK." in cleaned
    assert "OpenFOAM: The Open Source" not in cleaned
    assert "PID" not in cleaned


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
