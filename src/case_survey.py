"""
Complete case intelligence: look at EVERYTHING in a case folder, not just a
fixed list of well-known files.

The original case_reader reads a curated set of dictionaries, which is fast and
predictable. This module goes further and surveys the whole case the way an
engineer would when handed an unfamiliar run:

    system/          every dictionary, including functionObject definitions
    constant/        every dictionary, plus mesh presence
    0/               every initial field
    time dirs        0.1, 0.5, 250, ... (written results tell us how far it got)
    processor*/      parallel decomposition
    postProcessing/  function object output (forces, residuals, probes)
    logs             log.simpleFoam, *.log, nohup.out, ...

It stays READ-ONLY and never runs OpenFOAM. Big binaries (points/faces/owner)
are detected and reported but never read into memory.

This module is additive. It imports nothing from the GUI and changes no existing
behaviour; case_reader.read_case() continues to work exactly as before.
"""
import re
from pathlib import Path

from .case_reader import LARGE_MESH_FILES, MAX_CHARS_PER_FILE

# --- Limits, so a huge case can never exhaust memory --------------------------
MAX_EXTRA_DICTS = 80        # how many "extra" dictionaries we will read
MAX_LOG_CHARS = 40000       # per log file (we keep the END -- see _read_tail)
MAX_LOGS = 6                # newest N logs only

# Files inside constant/polyMesh that are huge binaries or long lists.
MESH_BINARIES = {"points", "faces", "owner", "neighbour", "cells", "pointZones",
                 "faceZones", "cellZones", "boundaryProcAddressing",
                 "cellProcAddressing", "faceProcAddressing", "pointProcAddressing"}

# Extensions that are never useful as text.
BINARY_SUFFIXES = {".stl", ".obj", ".vtk", ".vtu", ".vtp", ".png", ".jpg", ".gz",
                   ".eps", ".pdf", ".dat.gz", ".so", ".o", ".bin"}

LOG_PATTERNS = ["log.*", "*.log", "log", "nohup.out"]


def _is_number(name: str) -> bool:
    """True for '0', '0.1', '1e-05', '250' -- i.e. an OpenFOAM time directory."""
    try:
        float(name)
        return True
    except ValueError:
        return False


def _read_head(path: Path, limit: int = MAX_CHARS_PER_FILE) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > limit:
        text = text[:limit] + f"\n\n... [truncated: longer than {limit} characters] ..."
    return text


def _read_tail(path: Path, limit: int = MAX_LOG_CHARS) -> str:
    """
    Read the END of a file. For solver logs this is what matters -- divergence,
    the final residuals, and the error message all appear at the bottom.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > limit:
        text = f"... [earlier {len(text) - limit} characters omitted] ...\n\n" + text[-limit:]
    return text


# --- Individual discovery helpers ---------------------------------------------
def list_time_dirs(root: Path) -> list[str]:
    """Every numeric time directory, sorted numerically ('0', '0.1', '10')."""
    if not root.is_dir():
        return []
    times = [p.name for p in root.iterdir() if p.is_dir() and _is_number(p.name)]
    return sorted(times, key=float)


def find_processor_dirs(root: Path) -> list[str]:
    """processor0, processor1, ... -- tells us the case was decomposed."""
    if not root.is_dir():
        return []
    return sorted(
        (p.name for p in root.iterdir() if p.is_dir() and re.fullmatch(r"processor\d+", p.name)),
        key=lambda n: int(n.replace("processor", "")),
    )


def find_logs(root: Path) -> list[Path]:
    """Solver/utility logs, newest first, capped at MAX_LOGS."""
    if not root.is_dir():
        return []
    hits: set[Path] = set()
    for pattern in LOG_PATTERNS:
        for p in root.glob(pattern):
            if p.is_file() and p.suffix.lower() not in BINARY_SUFFIXES:
                hits.add(p)
    return sorted(hits, key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_LOGS]


def find_post_processing(root: Path) -> list[str]:
    """
    Relative paths of function-object output (forces, residuals, probes).
    We list them rather than reading everything, since these can be numerous.
    """
    pp = root / "postProcessing"
    if not pp.is_dir():
        return []
    out = []
    for p in sorted(pp.rglob("*")):
        if p.is_file() and p.suffix.lower() not in BINARY_SUFFIXES:
            out.append(str(p.relative_to(root)).replace("\\", "/"))
        if len(out) >= 200:
            break
    return out


def find_extra_dicts(root: Path) -> list[str]:
    """
    Every readable dictionary in system/ and constant/ that the curated list in
    case_reader does NOT already cover -- e.g. decomposeParDict, setFieldsDict,
    topoSetDict, fvOptions, functions, and custom includes.
    """
    out: list[str] = []
    for folder in ("system", "constant"):
        d = root / folder
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
            if p.suffix.lower() in BINARY_SUFFIXES:
                continue
            # Skip the mesh binaries but keep polyMesh/boundary.
            if "polyMesh" in rel and p.name in MESH_BINARIES:
                continue
            if "triSurface" in rel:
                continue
            try:
                if p.stat().st_size > 400_000:  # unusually large: skip the body
                    continue
            except OSError:
                continue
            out.append(rel)
    return out[:MAX_EXTRA_DICTS]


# --- The public survey --------------------------------------------------------
def survey_case(case_path: str) -> dict:
    """
    A complete structural picture of the case, WITHOUT reading big files.

    Returns a dict describing what exists. Everything is plain data so it can be
    handed straight to the rule engine, shown in the UI, or serialised to JSON.
    """
    root = Path(case_path).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"Case folder not found or not a folder: {case_path}")

    time_dirs = list_time_dirs(root)
    processors = find_processor_dirs(root)
    logs = find_logs(root)

    # Written results beyond 0/ mean the solver actually produced output.
    result_times = [t for t in time_dirs if float(t) > 0]

    return {
        "root": str(root),
        "time_dirs": time_dirs,
        "result_times": result_times,
        "latest_time": result_times[-1] if result_times else None,
        "ran_at_all": bool(result_times),
        "processor_dirs": processors,
        "is_decomposed": bool(processors),
        "n_processor_dirs": len(processors),
        "logs": [str(p.relative_to(root)).replace("\\", "/") for p in logs],
        "post_processing": find_post_processing(root),
        "extra_dicts": find_extra_dicts(root),
        "mesh_present": [rel for rel in LARGE_MESH_FILES if (root / rel).is_file()],
        "has_mesh": (root / "constant" / "polyMesh" / "faces").is_file(),
    }


def read_case_full(case_path: str) -> dict[str, str]:
    """
    Like case_reader.read_case(), but also returns every extra dictionary found
    in system/ and constant/, plus the tail of each log file.

    Log contents are keyed by their real relative path (e.g. "log.simpleFoam")
    so the rule engine and the AI can tell them apart from dictionaries.
    """
    from .case_reader import read_case  # local import keeps the dependency one-way

    root = Path(case_path).expanduser()
    files = dict(read_case(case_path))  # start from the curated set

    for rel in find_extra_dicts(root):
        if rel in files:
            continue
        p = root / rel
        try:
            files[rel] = _read_head(p)
        except OSError:
            continue

    for p in find_logs(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        try:
            files[rel] = _read_tail(p)
        except OSError:
            continue

    return files


def survey_summary(survey: dict) -> str:
    """A short plain-English description of the survey, for prompts and the UI."""
    bits = []
    if survey["has_mesh"]:
        bits.append("mesh present")
    else:
        bits.append("NO mesh (constant/polyMesh is incomplete)")
    if survey["is_decomposed"]:
        bits.append(f"decomposed into {survey['n_processor_dirs']} processor folders")
    if survey["ran_at_all"]:
        bits.append(f"wrote results up to t={survey['latest_time']}")
    else:
        bits.append("no time directories beyond 0 (the solver produced no output)")
    if survey["logs"]:
        bits.append(f"{len(survey['logs'])} log file(s): {', '.join(survey['logs'])}")
    else:
        bits.append("no log files found")
    if survey["post_processing"]:
        bits.append(f"{len(survey['post_processing'])} postProcessing file(s)")
    return "; ".join(bits) + "."
