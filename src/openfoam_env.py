"""
Finds the OpenFOAM installation on this computer, however it was installed.

WHY THIS FILE EXISTS
--------------------
You cannot simply run `checkMesh` like a normal program. OpenFOAM only works
inside a shell that has first "sourced" its `etc/bashrc` file, which sets around
fifty environment variables (WM_PROJECT_DIR, FOAM_SOLVERS, LD_LIBRARY_PATH, ...).
On top of that, people install OpenFOAM in very different ways:

  * native Linux package        (openfoam.org  -> /opt/openfoam11/etc/bashrc)
  * native Linux package        (openfoam.com  -> /usr/lib/openfoam/openfoam2312/etc/bashrc)
  * compiled from source        (~/OpenFOAM/OpenFOAM-11/etc/bashrc)
  * inside a conda environment  ($CONDA_PREFIX/opt/openfoam*/etc/bashrc)
  * WSL on Windows              (a Linux distro running inside Windows)
  * Docker container            (no local install at all)
  * blueCFD-Core on Windows     (a native Windows port, uses setvars.bat)
  * already active              (the user launched us from a sourced shell)

This module detects whichever of those exist and knows how to wrap a command so
it runs correctly in that environment. Nothing here executes a solver; it only
works out HOW a command would need to be launched. Actually running commands is
the job of the command runner module.

This file is additive: importing it changes nothing about existing behaviour,
and it never raises if OpenFOAM is missing -- it simply reports that none was
found.
"""
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

# --- Manual override ----------------------------------------------------------
# Auto-detection can never cover every setup, so the user can always force one.
# Set either of these in the .env file next to the app:
#
#   OPENFOAM_BASHRC=/opt/openfoam11/etc/bashrc     (path to the bashrc to source)
#   OPENFOAM_BACKEND=wsl                           (native | wsl | docker | bluecfd)
#   OPENFOAM_WSL_DISTRO=Ubuntu-22.04               (which WSL distro to use)
#   OPENFOAM_DOCKER_IMAGE=openfoam/openfoam11-paraview56
#
ENV_BASHRC = "OPENFOAM_BASHRC"
ENV_BACKEND = "OPENFOAM_BACKEND"
ENV_WSL_DISTRO = "OPENFOAM_WSL_DISTRO"
ENV_DOCKER_IMAGE = "OPENFOAM_DOCKER_IMAGE"

# --- Where to look for an etc/bashrc ------------------------------------------
# Glob patterns covering the installation routes documented by openfoam.com and
# openfoam.org, plus FOAM-extend. Every match is offered, so a machine carrying
# several versions lets the user pick.
BASHRC_GLOBS = [
    # --- openfoam.com (ESI / OpenCFD) ---
    "/usr/lib/openfoam/openfoam*/etc/bashrc",   # deb & rpm packages
    "/opt/OpenFOAM/OpenFOAM-v*/etc/bashrc",     # precompiled tgz bundles
    "~/OpenFOAM/OpenFOAM-v*/etc/bashrc",        # built from source (ESI naming)
    # --- openfoam.org (The OpenFOAM Foundation) ---
    "/opt/openfoam*/etc/bashrc",                # deb packages
    "/opt/OpenFOAM-*/etc/bashrc",               # older layout
    "~/OpenFOAM/OpenFOAM-*/etc/bashrc",         # built from source
    # --- distro-packaged ---
    "/usr/share/openfoam/etc/bashrc",
    "/usr/lib/openfoam/etc/bashrc",
    # --- FOAM-extend ---
    "~/foam/foam-extend-*/etc/bashrc",
    "/opt/foam/foam-extend-*/etc/bashrc",
    # --- generic / user-chosen locations ---
    "~/openfoam/*/etc/bashrc",
    "/usr/local/OpenFOAM/OpenFOAM-*/etc/bashrc",
]

# Typical blueCFD-Core (native Windows) install locations.
BLUECFD_GLOBS = [
    r"C:\Program Files\blueCFD-Core-*\setvars.bat",
    r"C:\blueCFD-Core-*\setvars.bat",
]

# Docker images that ship OpenFOAM, used only as a last resort.
DEFAULT_DOCKER_IMAGES = [
    "openfoam/openfoam11-paraview56",
    "opencfd/openfoam-default",
]


@dataclass
class FoamInstall:
    """
    One usable OpenFOAM environment.

    `kind` tells you how it has to be launched:
        "active"  -> already sourced in our own environment; run directly
        "native"  -> source a bashrc in a local bash shell
        "wsl"     -> source a bashrc inside a WSL distro
        "docker"  -> run inside a container, mounting the case folder
        "bluecfd" -> native Windows port, call setvars.bat first
    """
    kind: str
    label: str                              # human-readable, shown in the UI
    version: Optional[str] = None           # e.g. "11" or "2312", if we can tell
    bashrc: Optional[str] = None            # path to etc/bashrc (native/wsl/conda)
    distro: Optional[str] = None            # WSL distro name
    image: Optional[str] = None             # Docker image name
    setvars: Optional[str] = None           # blueCFD setvars.bat
    module: Optional[str] = None            # environment-module name (HPC)
    priority: int = 50                      # lower = preferred
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """A stable identifier, so a chosen install can be remembered."""
        return "|".join(filter(None, [self.kind, self.image, self.distro,
                                      self.bashrc, self.setvars, self.module]))

    def describe(self) -> str:
        v = f" {self.version}" if self.version else ""
        return f"{self.label}{v}"


# --- Small helpers ------------------------------------------------------------
def _expand_glob(pattern: str) -> list[Path]:
    """Expand one glob pattern (which may start with ~) into existing paths."""
    pattern = os.path.expanduser(pattern)
    # Split the pattern into a fixed root and the globbed remainder.
    p = Path(pattern)
    parts = p.parts
    fixed: list[str] = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        fixed.append(part)
    root = Path(*fixed) if fixed else Path("/")
    rest = str(Path(*parts[len(fixed):])) if len(parts) > len(fixed) else ""
    if not rest:
        return [p] if p.exists() else []
    try:
        return sorted(root.glob(rest))
    except (OSError, ValueError):
        return []


def _version_from_path(text: str) -> Optional[str]:
    """
    Pull a version out of a path like /opt/openfoam11/etc/bashrc  -> "11"
    or /usr/lib/openfoam/openfoam2312/etc/bashrc                  -> "2312".
    """
    m = re.search(r"openfoam[-_]?v?(\d{1,4})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"OpenFOAM-(\S+?)[/\\]", text)
    if m:
        return m.group(1)
    return None


def _run_quiet(argv: list[str], timeout: int = 10) -> tuple[int, str]:
    """
    Run a short probe command and return (exit_code, combined_output).
    Never raises -- a missing program just returns a non-zero code.
    """
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,  # never shell=True: avoids quoting/injection problems
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return 127, ""


# --- Individual detectors -----------------------------------------------------
def _detect_active() -> Optional[FoamInstall]:
    """Are we already running inside a shell where OpenFOAM was sourced?"""
    project_dir = os.environ.get("WM_PROJECT_DIR")
    if project_dir and shutil.which("checkMesh"):
        return FoamInstall(
            kind="active",
            label="OpenFOAM (already active in this shell)",
            version=os.environ.get("WM_PROJECT_VERSION") or _version_from_path(project_dir),
            priority=0,
            notes=["Detected via WM_PROJECT_DIR; commands run directly."],
        )
    # PATH alone is enough for some packaged builds.
    if shutil.which("checkMesh") and shutil.which("blockMesh"):
        return FoamInstall(
            kind="active",
            label="OpenFOAM (found on PATH)",
            priority=5,
            notes=["checkMesh/blockMesh are on PATH; no bashrc sourcing needed."],
        )
    return None


def _detect_native_bashrc() -> list[FoamInstall]:
    """Look for an etc/bashrc on this machine (Linux, macOS, or conda env)."""
    found: list[FoamInstall] = []
    globs = list(BASHRC_GLOBS)

    # A conda/venv-style prefix can carry its own OpenFOAM build.
    prefix = os.environ.get("CONDA_PREFIX") or os.environ.get("VIRTUAL_ENV")
    if prefix:
        globs.insert(0, str(Path(prefix) / "opt" / "openfoam*" / "etc" / "bashrc"))

    seen: set[str] = set()
    for pattern in globs:
        for path in _expand_glob(pattern):
            key = str(path.resolve())
            if key in seen or not path.is_file():
                continue
            seen.add(key)
            in_env = bool(prefix and key.startswith(str(Path(prefix).resolve())))
            found.append(
                FoamInstall(
                    kind="native",
                    label="OpenFOAM (conda/venv)" if in_env else "OpenFOAM (local install)",
                    version=_version_from_path(key),
                    bashrc=key,
                    priority=10 if in_env else 20,
                    notes=[f"Will source: {key}"],
                )
            )
    return found


def _detect_wsl() -> list[FoamInstall]:
    """On Windows, look for OpenFOAM inside any installed WSL distro."""
    if platform.system() != "Windows" or not shutil.which("wsl"):
        return []

    code, out = _run_quiet(["wsl", "--list", "--quiet"])
    if code != 0 or not out.strip():
        return []  # WSL present but no distros installed

    # `wsl --list` output is UTF-16-ish; strip stray NULs and blank lines.
    distros = [d.strip() for d in out.replace("\x00", "").splitlines() if d.strip()]

    found: list[FoamInstall] = []
    for distro in distros:
        # Ask the distro to find a bashrc itself -- far more reliable than guessing.
        probe = (
            "for f in /opt/openfoam*/etc/bashrc /usr/lib/openfoam/openfoam*/etc/bashrc "
            "$HOME/OpenFOAM/OpenFOAM-*/etc/bashrc; do "
            '[ -f "$f" ] && echo "$f" && break; done'
        )
        code, out = _run_quiet(["wsl", "-d", distro, "bash", "-lc", probe], timeout=20)
        bashrc = out.replace("\x00", "").strip().splitlines()
        bashrc = bashrc[0].strip() if bashrc else ""
        if code == 0 and bashrc.startswith("/"):
            found.append(
                FoamInstall(
                    kind="wsl",
                    label=f"OpenFOAM in WSL ({distro})",
                    version=_version_from_path(bashrc),
                    bashrc=bashrc,
                    distro=distro,
                    priority=30,
                    notes=[f"Runs inside WSL distro '{distro}', sourcing {bashrc}."],
                )
            )
    return found


def _detect_spack() -> list[FoamInstall]:
    """
    Spack installs OpenFOAM under an opaque hash path, so ask Spack itself.

    Common on HPC systems and one of the routes OpenFOAM documents.
    """
    if not shutil.which("spack"):
        return []
    found = []
    for spec in ("openfoam", "openfoam-org"):
        code, out = _run_quiet(["spack", "location", "-i", spec], timeout=60)
        prefix = out.strip().splitlines()[0].strip() if code == 0 and out.strip() else ""
        if not prefix or not prefix.startswith("/"):
            continue
        bashrc = Path(prefix) / "etc" / "bashrc"
        if bashrc.is_file():
            found.append(FoamInstall(
                kind="native",
                label=f"OpenFOAM via Spack ({spec})",
                version=_version_from_path(prefix),
                bashrc=str(bashrc),
                priority=25,
                notes=[f"Spack prefix: {prefix}"],
            ))
    return found


def _detect_modules() -> list[FoamInstall]:
    """
    Environment Modules / Lmod, as used on most HPC clusters.

    `module` is a shell function rather than a program, so the command has to be
    run through a login shell. We only offer this when a module whose name looks
    like OpenFOAM is actually available.
    """
    if platform.system() == "Windows":
        return []
    if not (shutil.which("modulecmd") or shutil.which("lmod") or
            Path("/usr/share/Modules/init/bash").is_file()):
        return []

    code, out = _run_quiet(
        ["bash", "-lc", "module -t avail 2>&1 | grep -i openfoam | head -5"], timeout=45
    )
    if code != 0 or not out.strip():
        return []

    found = []
    for line in out.splitlines():
        name = line.strip().rstrip(":")
        if not name or "/" not in name and "openfoam" not in name.lower():
            continue
        found.append(FoamInstall(
            kind="module",
            label=f"OpenFOAM via environment module ({name})",
            version=_version_from_path(name),
            module=name,
            priority=35,
            notes=[f"Runs 'module load {name}' before each command."],
        ))
    return found[:3]


def _detect_bluecfd() -> list[FoamInstall]:
    """blueCFD-Core: a native Windows port that uses setvars.bat."""
    if platform.system() != "Windows":
        return []
    found = []
    for pattern in BLUECFD_GLOBS:
        for path in _expand_glob(pattern):
            if path.is_file():
                found.append(
                    FoamInstall(
                        kind="bluecfd",
                        label="blueCFD-Core (native Windows)",
                        version=_version_from_path(str(path)),
                        setvars=str(path),
                        priority=40,
                        notes=[f"Will call: {path}"],
                    )
                )
    return found


DOCKER_MACHINE_PATHS = [
    r"C:\Program Files\Docker Toolbox\docker-machine.exe",
    r"C:\Program Files (x86)\Docker Toolbox\docker-machine.exe",
    "docker-machine",
]


def _daemon_reachable() -> bool:
    code, _ = _run_quiet(["docker", "version", "--format", "{{.Server.Version}}"], timeout=20)
    return code == 0


def _ensure_docker_env() -> bool:
    """
    Make the Docker daemon reachable, resolving Docker Toolbox automatically.

    Docker Toolbox runs the daemon inside a VirtualBox VM, and the client only
    finds it once DOCKER_HOST / DOCKER_CERT_PATH / DOCKER_TLS_VERIFY are set --
    normally by running `docker-machine env` in your shell. A GUI launched from
    the Start menu never does that, so without this the app would report "no
    Docker" even though the VM is running happily.

    We ask docker-machine for those variables and apply them to this process
    only. Nothing outside the app is modified, and a running machine is never
    started automatically (that can take minutes and is the user's decision).

    Returns True if the daemon is reachable afterwards.
    """
    if _daemon_reachable():
        return True

    exe = next((p for p in DOCKER_MACHINE_PATHS
                if p == "docker-machine" and shutil.which(p) or Path(p).is_file()), None)
    if not exe:
        return False

    # Find a machine that is already Running -- do not start one ourselves.
    code, out = _run_quiet([exe, "ls", "--format", "{{.Name}}\t{{.State}}"], timeout=45)
    if code != 0:
        return False
    running = [ln.split("\t")[0].strip() for ln in out.splitlines()
               if "\t" in ln and ln.split("\t")[1].strip().lower() == "running"]
    if not running:
        return False

    for machine in running:
        code, out = _run_quiet([exe, "env", "--shell", "bash", machine], timeout=45)
        if code != 0:
            continue
        applied = {}
        for m in re.finditer(r'export\s+(DOCKER_[A-Z_]+)="([^"]*)"', out):
            applied[m.group(1)] = m.group(2)
        if not applied:
            continue
        saved = {k: os.environ.get(k) for k in applied}
        os.environ.update(applied)
        if _daemon_reachable():
            return True
        for k, v in saved.items():      # roll back if it did not help
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return False


def _docker_uses_vm_paths() -> bool:
    """
    True when the Docker daemon is reached over TCP rather than a local socket.

    That means Docker Toolbox (or a remote/VM daemon), where the daemon lives
    inside a VirtualBox VM and cannot see Windows paths. Mounts then have to use
    the VM's shared-folder form (/c/Users/...) instead of C:\\Users\\...

    Docker Desktop uses a named pipe / unix socket and accepts native paths.
    """
    host = os.environ.get("DOCKER_HOST", "")
    return host.startswith("tcp://")


def _probe_image_bashrc(image: str) -> Optional[str]:
    """
    Ask an image where its OpenFOAM etc/bashrc lives.

    Many OpenFOAM images do NOT put the solvers on PATH by default -- the
    environment only exists after sourcing that file -- so we cannot just run
    `checkMesh` and hope.
    """
    # An image can ship several versions side by side (e.g. openfoam2006 AND
    # openfoam2012). Sort by version and take the NEWEST rather than whichever
    # the shell happens to glob first.
    probe = (
        "ls -d /usr/lib/openfoam/openfoam*/etc/bashrc /opt/openfoam*/etc/bashrc "
        "/opt/OpenFOAM/OpenFOAM-*/etc/bashrc /openfoam/etc/bashrc 2>/dev/null "
        "| sort -V | tail -1"
    )
    code, out = _run_quiet(["docker", "run", "--rm", image, "bash", "-lc", probe], timeout=90)
    if code != 0:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("/") and line.endswith("bashrc"):
            return line
    return None


def _looks_like_openfoam_image(name: str) -> bool:
    """Match the many ways an OpenFOAM image gets named locally."""
    n = name.lower()
    return "openfoam" in n or n.startswith("of_") or "_of" in n or "foam" in n


def _detect_docker() -> list[FoamInstall]:
    """
    Find OpenFOAM images in the local Docker daemon.

    We enumerate images that are ALREADY PULLED rather than relying on a fixed
    list of names, because most people have a locally-built or renamed image.
    Nothing is ever downloaded automatically.
    """
    if not shutil.which("docker"):
        return []
    # The CLI alone is not enough -- the daemon must answer. This also resolves
    # Docker Toolbox's DOCKER_HOST automatically when a machine is running.
    if not _ensure_docker_env():
        return []

    override = os.environ.get(ENV_DOCKER_IMAGE)
    if override:
        candidates = [override]
    else:
        code, out = _run_quiet(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=30
        )
        local = [ln.strip() for ln in out.splitlines() if ln.strip() and "<none>" not in ln]
        candidates = [n for n in local if _looks_like_openfoam_image(n)]
        # Keep the well-known official images as a fallback if they happen to exist.
        candidates += [n for n in DEFAULT_DOCKER_IMAGES if n in local]

    vm_paths = _docker_uses_vm_paths()
    found = []
    for image in dict.fromkeys(candidates):  # de-duplicate, preserve order
        bashrc = _probe_image_bashrc(image)
        notes = ["Runs in a container; the case folder is mounted at /case."]
        if bashrc:
            notes.append(f"Sources {bashrc} inside the container.")
        if vm_paths:
            notes.append("Daemon is reached over TCP (Docker Toolbox or a remote VM), so "
                         "the case path is translated to the VM's shared-folder form. "
                         "Only folders the VM shares (usually C:\\Users) can be mounted.")
        found.append(
            FoamInstall(
                kind="docker",
                label=f"OpenFOAM in Docker ({image})",
                version=_version_from_path(image),
                bashrc=bashrc,
                image=image,
                priority=60,
                notes=notes,
            )
        )
    return found


def _detect_override() -> Optional[FoamInstall]:
    """An explicit setting in .env always wins over auto-detection."""
    bashrc = os.environ.get(ENV_BASHRC, "").strip()
    backend = os.environ.get(ENV_BACKEND, "").strip().lower()
    distro = os.environ.get(ENV_WSL_DISTRO, "").strip() or None
    image = os.environ.get(ENV_DOCKER_IMAGE, "").strip() or None

    if not bashrc and not backend:
        return None

    kind = backend or ("wsl" if distro else "native")
    return FoamInstall(
        kind=kind,
        label=f"OpenFOAM (manual setting: {kind})",
        version=_version_from_path(bashrc) if bashrc else None,
        bashrc=bashrc or None,
        distro=distro,
        image=image,
        priority=-1,  # always first
        notes=[f"Configured by hand via {ENV_BACKEND}/{ENV_BASHRC} in .env."],
    )


# --- Public API ---------------------------------------------------------------
def detect_installs() -> list[FoamInstall]:
    """
    Return every OpenFOAM environment we can find, best first.
    Returns an empty list if none is available -- this is not an error.
    """
    installs: list[FoamInstall] = []

    override = _detect_override()
    if override:
        installs.append(override)

    active = _detect_active()
    if active:
        installs.append(active)

    installs.extend(_detect_native_bashrc())
    installs.extend(_detect_spack())
    installs.extend(_detect_modules())
    installs.extend(_detect_wsl())
    installs.extend(_detect_bluecfd())
    installs.extend(_detect_docker())

    # A machine can legitimately carry several versions (e.g. a system package
    # plus a source build). Keep them all, de-duplicated, best first, so the
    # user can choose which one a case should run against.
    seen, unique = set(), []
    for inst in sorted(installs, key=lambda i: i.priority):
        if inst.key in seen:
            continue
        seen.add(inst.key)
        unique.append(inst)
    return unique


def find_install(key: str) -> Optional[FoamInstall]:
    """Look up a previously-chosen installation by its stable key."""
    return next((i for i in detect_installs() if i.key == key), None)


def best_install() -> Optional[FoamInstall]:
    """The single environment we would use, or None if OpenFOAM is unavailable."""
    installs = detect_installs()
    return installs[0] if installs else None


def to_backend_path(path: str, install: FoamInstall) -> str:
    """
    Translate a path on THIS machine into the path the backend will see.

    Windows C:\\Users\\me\\case  ->  WSL    /mnt/c/Users/me/case
                                ->  Docker /case (it is mounted there)
    Everything else is passed through unchanged.
    """
    if install.kind == "docker":
        return "/case"

    if install.kind == "wsl" and platform.system() == "Windows":
        p = Path(path).resolve()
        drive = p.drive.rstrip(":").lower()      # "C:" -> "c"
        if not drive:
            return str(p).replace("\\", "/")
        rest = str(p)[len(p.drive):].replace("\\", "/").lstrip("/")
        return str(PurePosixPath(f"/mnt/{drive}") / rest)

    return str(Path(path).resolve())


def to_mount_path(path: str) -> str:
    """
    Translate a host path into the form `docker -v` will accept.

    Docker Desktop understands native Windows paths. Docker Toolbox (and any
    daemon reached over TCP) runs inside a VirtualBox VM that cannot see them --
    and worse, the drive-letter colon collides with the `src:dst` separator, so
    passing C:\\Users\\me\\case produces the confusing error "invalid mode".
    The VM shares C:\\Users as /c/Users, so that is the form we emit.
    """
    p = Path(path).resolve()
    if platform.system() != "Windows" or not _docker_uses_vm_paths():
        return str(p)
    drive = p.drive.rstrip(":").lower()
    if not drive:
        return str(p).replace("\\", "/")
    rest = str(p)[len(p.drive):].replace("\\", "/").lstrip("/")
    return str(PurePosixPath(f"/{drive}") / rest)


def build_command(install: FoamInstall, foam_command: list[str], case_dir: str) -> list[str]:
    """
    Build the full argv needed to run `foam_command` inside `install`, with the
    working directory set to `case_dir`.

    `foam_command` is a list, e.g. ["checkMesh", "-latestTime"]. It is never
    passed through a shell as one string, so case paths containing spaces and
    unusual characters stay safe.

    Returns the argv list to hand to subprocess.run(..., shell=False).
    """
    inner_dir = to_backend_path(case_dir, install)
    # Quote each argument for the inner shell (only for backends that need one).
    joined = " ".join(_sh_quote(a) for a in foam_command)

    if install.kind == "active":
        # Environment is already correct; run the program directly.
        return list(foam_command)

    if install.kind == "native":
        script = f"source {_sh_quote(install.bashrc or '')} && cd {_sh_quote(inner_dir)} && {joined}"
        return ["bash", "-lc", script]

    if install.kind == "wsl":
        script = f"source {_sh_quote(install.bashrc or '')} && cd {_sh_quote(inner_dir)} && {joined}"
        argv = ["wsl"]
        if install.distro:
            argv += ["-d", install.distro]
        return argv + ["bash", "-lc", script]

    if install.kind == "docker":
        # Most OpenFOAM images leave the solvers off PATH until etc/bashrc is
        # sourced, so source it whenever we managed to locate one.
        inner = f"source {_sh_quote(install.bashrc)} && {joined}" if install.bashrc else joined
        return [
            "docker", "run", "--rm",
            "-v", f"{to_mount_path(case_dir)}:/case",
            "-w", "/case",
            install.image or DEFAULT_DOCKER_IMAGES[0],
            "bash", "-lc", inner,
        ]

    if install.kind == "module":
        # `module` is a shell function, so it needs a login shell.
        script = (f"module load {_sh_quote(install.module or '')} && "
                  f"cd {_sh_quote(inner_dir)} && {joined}")
        return ["bash", "-lc", script]

    if install.kind == "bluecfd":
        # blueCFD ships a batch file that prepares the environment, then a shell.
        script = f'call "{install.setvars}" && cd /d "{Path(case_dir).resolve()}" && {joined}'
        return ["cmd", "/c", script]

    raise ValueError(f"Unknown OpenFOAM backend kind: {install.kind}")


def _sh_quote(text: str) -> str:
    """Minimal POSIX single-quoting, so paths with spaces survive the inner shell."""
    if text == "":
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", text):
        return text
    return "'" + text.replace("'", "'\\''") + "'"


def environment_report() -> str:
    """
    A short, plain-English summary of what was found. Useful for the Settings
    dialog and for telling the user why command execution is unavailable.
    """
    installs = detect_installs()
    if not installs:
        return (
            "No OpenFOAM installation was found on this computer.\n\n"
            "The assistant can still read and analyse your case files, but it "
            "cannot run commands such as checkMesh.\n\n"
            "To enable command execution, install OpenFOAM (via WSL, Docker, a "
            "native Linux package, or blueCFD-Core on Windows), or point the app "
            f"at an existing install by adding {ENV_BASHRC}=/path/to/etc/bashrc "
            "to your .env file."
        )
    lines = ["Found the following OpenFOAM environment(s), best first:", ""]
    for i, inst in enumerate(installs, 1):
        lines.append(f"{i}. {inst.describe()}  [{inst.kind}]")
        for note in inst.notes:
            lines.append(f"     {note}")
    return "\n".join(lines)
