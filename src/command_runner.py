"""
Running OpenFOAM utilities safely, including in parallel.

This is the only module that executes anything. Everything else in the project
reads files. The safety rules are enforced here rather than left to the AI:

  * ALLOWLIST -- only known OpenFOAM utilities may run. Arbitrary shell commands
    are refused, so a prompt injection in a log file or dictionary cannot turn
    into code execution.
  * NO SHELL -- commands are passed as argument lists (shell=False). A case path
    containing ';' or '&&' is data, never syntax.
  * CONFINEMENT -- the working directory must be inside the selected case folder.
  * TIMEOUTS -- every command has one, so a runaway solver cannot hang the app.
  * READ-ONLY BY DEFAULT -- utilities that modify a case are marked, and the
    caller has to opt in explicitly via allow_write=True.

Parallel runs are supported: decomposePar, then `mpirun -np N solver -parallel`,
then reconstructPar. The number of subdomains is read from decomposeParDict so
it always matches the case.
"""
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import openfoam_parser as ofp
from .openfoam_env import FoamInstall, best_install, build_command

# --- What may be run ----------------------------------------------------------
# Read-only utilities: safe to run at any time; they only inspect the case.
READ_ONLY_COMMANDS = {
    "checkMesh", "foamDictionary", "surfaceCheck", "surfaceInspect",
    "postProcess", "foamToVTK", "foamListTimes", "foamInfo",
    "patchSummary", "foamVersion", "blockMesh -help", "transformPoints -help",
}

# Utilities that CHANGE the case. Allowed only when the caller passes
# allow_write=True, which the GUI ties to explicit user approval.
WRITE_COMMANDS = {
    "blockMesh", "snappyHexMesh", "surfaceFeatureExtract", "extrudeMesh",
    "renumberMesh", "decomposePar", "reconstructPar", "reconstructParMesh",
    "setFields", "topoSet", "createPatch", "transformPoints", "checkMesh -writeAllFields",
    "potentialFoam", "mapFields", "splitMeshRegions",
}

# Solvers are the heaviest case: they run for a long time and write results.
# They are permitted only through run_solver(), never run_command().
KNOWN_SOLVER_PREFIXES = ("simpleFoam", "pimpleFoam", "pisoFoam", "icoFoam",
                         "potentialFoam", "interFoam", "rhoSimpleFoam",
                         "rhoPimpleFoam", "buoyantSimpleFoam", "buoyantPimpleFoam",
                         "chtMultiRegionFoam", "sonicFoam", "laplacianFoam",
                         "scalarTransportFoam", "multiphaseInterFoam", "interIsoFoam")

DEFAULT_TIMEOUT = 300          # 5 minutes for utilities
DEFAULT_SOLVER_TIMEOUT = 1800  # 30 minutes for a solver
MAX_OUTPUT_CHARS = 200_000


class CommandNotAllowed(Exception):
    """Raised when a command is not on the allowlist."""


@dataclass
class CommandResult:
    """The outcome of one command."""
    command: list = field(default_factory=list)
    argv: list = field(default_factory=list)
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    timed_out: bool = False
    install: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return (self.stdout or "") + (("\n" + self.stderr) if self.stderr else "")

    def tail(self, chars: int = 4000) -> str:
        text = self.output
        return text if len(text) <= chars else "... [earlier output omitted] ...\n" + text[-chars:]

    def clean_output(self, chars: int = 2500) -> str:
        """
        The output with OpenFOAM's banner removed.

        Every utility prints ~20 lines of version/host/PID boilerplate before
        anything useful. Showing that to the user buries the actual result, so
        we drop everything up to the '// * * *' separator that ends the header.
        """
        text = self.output
        marker = text.find("// * * *")
        if marker != -1:
            end = text.find("\n", marker)
            if end != -1:
                text = text[end + 1:]
        text = text.strip()
        if len(text) > chars:
            text = "... [earlier output omitted] ...\n" + text[-chars:]
        return text

    def summary(self) -> str:
        name = self.command[0] if self.command else "command"
        if self.timed_out:
            return f"{name} timed out after {self.duration_s:.0f}s."
        state = "succeeded" if self.ok else f"failed (exit {self.exit_code})"
        return f"{name} {state} in {self.duration_s:.1f}s."


def _base_name(command: list[str]) -> str:
    return command[0] if command else ""


def check_allowed(command: list[str], allow_write: bool = False) -> None:
    """Raise CommandNotAllowed unless this command may run."""
    if not command:
        raise CommandNotAllowed("No command was given.")
    name = _base_name(command)
    if name in READ_ONLY_COMMANDS:
        return
    if name in WRITE_COMMANDS:
        if allow_write:
            return
        raise CommandNotAllowed(
            f"'{name}' modifies the case, so it needs explicit approval before it can run."
        )
    if name.startswith(KNOWN_SOLVER_PREFIXES):
        raise CommandNotAllowed(
            f"'{name}' is a solver; use run_solver() so the run is supervised and time-limited."
        )
    raise CommandNotAllowed(
        f"'{name}' is not a recognised OpenFOAM utility, so it will not be run."
    )


def _confine(case_dir: str) -> Path:
    root = Path(case_dir).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Case folder not found: {case_dir}")
    return root


def _truncate(text: str) -> str:
    if text and len(text) > MAX_OUTPUT_CHARS:
        return text[:2000] + "\n... [output truncated] ...\n" + text[-(MAX_OUTPUT_CHARS - 2000):]
    return text or ""


def run_command(command: list[str], case_dir: str, *, install: Optional[FoamInstall] = None,
                timeout: int = DEFAULT_TIMEOUT, allow_write: bool = False) -> CommandResult:
    """
    Run one OpenFOAM utility against a case.

    Raises CommandNotAllowed if the command is not permitted, and
    RuntimeError if no OpenFOAM installation is available.
    """
    check_allowed(command, allow_write=allow_write)
    root = _confine(case_dir)

    inst = install or best_install()
    if inst is None:
        raise RuntimeError(
            "No OpenFOAM installation was found, so commands cannot be run. "
            "Install OpenFOAM (WSL, Docker, a native package, or blueCFD-Core), or set "
            "OPENFOAM_BASHRC in your .env file."
        )

    argv = build_command(inst, command, str(root))
    started = time.time()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, shell=False)
        return CommandResult(
            command=list(command), argv=argv, exit_code=proc.returncode,
            stdout=_truncate(proc.stdout), stderr=_truncate(proc.stderr),
            duration_s=time.time() - started, install=inst.describe(),
        )
    except subprocess.TimeoutExpired as e:
        return CommandResult(
            command=list(command), argv=argv, exit_code=-1,
            stdout=_truncate(e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")),
            stderr=f"Timed out after {timeout}s.",
            duration_s=time.time() - started, timed_out=True, install=inst.describe(),
        )
    except OSError as e:
        return CommandResult(
            command=list(command), argv=argv, exit_code=127,
            stderr=f"Could not launch the command: {e}",
            duration_s=time.time() - started, install=inst.describe(),
        )


# --- Parallel support ---------------------------------------------------------
def subdomains_for(case_dir: str) -> Optional[int]:
    """Read numberOfSubdomains from system/decomposeParDict, if present."""
    path = Path(case_dir) / "system" / "decomposeParDict"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    value = ofp.scalar_entries(ofp.strip_comments(text)).get("numberOfSubdomains")
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def is_decomposed(case_dir: str) -> bool:
    root = Path(case_dir)
    return any((root / f"processor{i}").is_dir() for i in range(4))


def build_parallel_command(solver: str, n_procs: int, extra: Optional[list] = None) -> list[str]:
    """
    Build `mpirun -np N solver -parallel [extra]`.

    OpenFOAM's own convention: the solver needs the -parallel flag, and mpirun
    needs -np matching the number of subdomains in decomposeParDict.
    """
    if n_procs < 2:
        return [solver] + list(extra or [])
    return ["mpirun", "-np", str(int(n_procs)), solver, "-parallel"] + list(extra or [])


def run_solver(solver: str, case_dir: str, *, install: Optional[FoamInstall] = None,
               parallel: Optional[bool] = None, n_procs: Optional[int] = None,
               timeout: int = DEFAULT_SOLVER_TIMEOUT,
               extra_args: Optional[list] = None) -> CommandResult:
    """
    Run a solver, in serial or in parallel.

    `parallel=None` decides automatically: parallel when decomposeParDict asks
    for more than one subdomain. Running a solver always writes results, so the
    caller is expected to have obtained the user's approval already.
    """
    if not solver.startswith(KNOWN_SOLVER_PREFIXES):
        raise CommandNotAllowed(f"'{solver}' is not a recognised OpenFOAM solver.")
    root = _confine(case_dir)

    procs = n_procs if n_procs is not None else (subdomains_for(str(root)) or 1)
    use_parallel = parallel if parallel is not None else procs > 1
    command = build_parallel_command(solver, procs if use_parallel else 1, extra_args)

    inst = install or best_install()
    if inst is None:
        raise RuntimeError("No OpenFOAM installation was found, so the solver cannot be run.")

    argv = build_command(inst, command, str(root))
    started = time.time()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False)
        return CommandResult(command=command, argv=argv, exit_code=proc.returncode,
                             stdout=_truncate(proc.stdout), stderr=_truncate(proc.stderr),
                             duration_s=time.time() - started, install=inst.describe())
    except subprocess.TimeoutExpired as e:
        return CommandResult(
            command=command, argv=argv, exit_code=-1,
            stdout=_truncate(e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")),
            stderr=f"The solver was stopped after the {timeout}s limit.",
            duration_s=time.time() - started, timed_out=True, install=inst.describe())
    except OSError as e:
        return CommandResult(command=command, argv=argv, exit_code=127,
                             stderr=f"Could not launch the solver: {e}",
                             duration_s=time.time() - started, install=inst.describe())


# --- Convenience wrappers used by the diagnostic flow -------------------------
def run_check_mesh(case_dir: str, *, install: Optional[FoamInstall] = None,
                   parallel: bool = False) -> CommandResult:
    """checkMesh, the single most useful read-only diagnostic."""
    if parallel and is_decomposed(case_dir):
        n = subdomains_for(case_dir) or 2
        command = build_parallel_command("checkMesh", n)
    else:
        command = ["checkMesh"]
    return run_command(command, case_dir, install=install)


def read_dictionary_entry(case_dir: str, dict_path: str, entry: str, *,
                          install: Optional[FoamInstall] = None) -> CommandResult:
    """
    foamDictionary -entry X -value <dict>: authoritative because OpenFOAM itself
    resolves #include, macros and regex entries that our own parser cannot.
    """
    return run_command(
        ["foamDictionary", "-entry", entry, "-value", dict_path],
        case_dir, install=install,
    )


def available() -> bool:
    """True when commands can actually be run on this machine."""
    return best_install() is not None
