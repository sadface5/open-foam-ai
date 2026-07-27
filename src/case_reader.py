"""
Reads the relevant text files from an OpenFOAM/SPUMA case folder.

IMPORTANT: this module only READS files. It never writes, edits, or deletes
anything in your case. Large mesh files (points/faces/owner/neighbour) are NOT
read -- they are only listed in the inventory -- because they can be enormous.
"""
from pathlib import Path

# Small text dictionaries we try to read, relative to the case root.
# Missing files are simply skipped (not every case has every file).
STANDARD_FILES = [
    "system/controlDict",
    "system/fvSchemes",
    "system/fvSolution",
    "system/blockMeshDict",
    "system/snappyHexMeshDict",
    "system/surfaceFeatureExtractDict",
    "system/meshQualityDict",
    "constant/transportProperties",
    "constant/turbulenceProperties",
    "constant/momentumTransport",
    "constant/physicalProperties",
    "constant/thermophysicalProperties",
    "constant/g",
    "constant/polyMesh/boundary",  # small text file: the authoritative patch list
]

# Large mesh files we never read, but do report as "present" in the inventory.
LARGE_MESH_FILES = [
    "constant/polyMesh/points",
    "constant/polyMesh/faces",
    "constant/polyMesh/owner",
    "constant/polyMesh/neighbour",
]

MAX_CHARS_PER_FILE = 15000


def _read_one(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_CHARS_PER_FILE:
        text = (
            text[:MAX_CHARS_PER_FILE]
            + f"\n\n... [truncated: file longer than {MAX_CHARS_PER_FILE} characters] ..."
        )
    return text


def _time_zero_dir(root: Path) -> Path | None:
    """Return the initial-conditions folder (0/ preferred, else 0.orig/)."""
    for name in ("0", "0.orig"):
        d = root / name
        if d.is_dir():
            return d
    return None


def read_case(case_path: str) -> dict[str, str]:
    """
    Return {relative_path: file_contents} for every relevant SMALL file found.
    Files that don't exist are skipped silently.
    """
    root = Path(case_path).expanduser()
    if not root.exists() or not root.is_dir():
        raise NotADirectoryError(f"Case folder not found or not a folder: {case_path}")

    files: dict[str, str] = {}

    for rel in STANDARD_FILES:
        p = root / rel
        if p.is_file():
            files[rel] = _read_one(p)

    zero_dir = _time_zero_dir(root)
    if zero_dir is not None:
        for p in sorted(zero_dir.iterdir()):
            if p.is_file():
                files[f"{zero_dir.name}/{p.name}"] = _read_one(p)

    return files


def case_inventory(case_path: str) -> dict:
    """
    A quick listing of what EXISTS (including big files we don't read), so the
    progress panel and Claude know what is available.
    Returns {"present_large": [...], "geometry": [...], "time_dir": "0"|None}.
    """
    root = Path(case_path).expanduser()
    present_large = [rel for rel in LARGE_MESH_FILES if (root / rel).is_file()]
    geometry = []
    tri = root / "constant" / "triSurface"
    if tri.is_dir():
        geometry = [f"constant/triSurface/{p.name}" for p in sorted(tri.iterdir()) if p.is_file()]
    zero_dir = _time_zero_dir(root)
    return {
        "present_large": present_large,
        "geometry": geometry,
        "time_dir": zero_dir.name if zero_dir else None,
    }
