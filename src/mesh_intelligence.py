"""
Mesh quality analysis, and what it means for the solver.

checkMesh prints a wall of numbers and a cheerful "Mesh OK" even when the mesh
is bad enough to ruin a run. This module reads that output, judges each metric
against the thresholds practitioners actually use, and -- most importantly --
connects mesh quality to the behaviour seen in the solver log:

    "non-orthogonality is 78 and the pressure residuals stalled"
        -> raise nNonOrthogonalCorrectors before blaming the boundary conditions

    "max skewness 12 and the run went nan near a wall"
        -> the mesh is the prime suspect, not the numerics

It parses text only. It cannot run checkMesh; when no checkMesh output is
available it says so rather than guessing, because mesh quality genuinely cannot
be determined from the dictionaries alone.
"""
import re
from dataclasses import dataclass, field

# --- Thresholds used by most OpenFOAM practitioners ---------------------------
NON_ORTHO_FINE = 60.0        # below this, the default settings cope
NON_ORTHO_SEVERE = 70.0      # OpenFOAM's own "severely non-orthogonal" mark
NON_ORTHO_EXTREME = 80.0     # expect trouble regardless of settings

SKEWNESS_WARN = 4.0          # OpenFOAM warns above this
SKEWNESS_BAD = 10.0

ASPECT_WARN = 100.0
ASPECT_BAD = 1000.0

# --- checkMesh line patterns --------------------------------------------------
RE_CELLS = re.compile(r"^\s*cells:\s+(\d+)", re.MULTILINE)
RE_POINTS = re.compile(r"^\s*points:\s+(\d+)", re.MULTILINE)
RE_FACES = re.compile(r"^\s*faces:\s+(\d+)", re.MULTILINE)
RE_NONORTHO = re.compile(r"non-orthogonality Max:\s*([\d.eE+-]+)\s*average:\s*([\d.eE+-]+)", re.IGNORECASE)
RE_SEVERE = re.compile(r"Number of severely non-orthogonal[^:]*:\s*(\d+)", re.IGNORECASE)
RE_SKEW = re.compile(r"Max skewness\s*=\s*([\d.eE+-]+)", re.IGNORECASE)
RE_SKEW_COUNT = re.compile(r"([\d]+)\s+highly skew faces", re.IGNORECASE)
RE_ASPECT = re.compile(r"Max aspect ratio\s*=\s*([\d.eE+-]+)", re.IGNORECASE)
RE_MINVOL = re.compile(r"Min volume\s*=\s*([\d.eE+-]+)", re.IGNORECASE)
RE_FAILED = re.compile(r"Failed\s+(\d+)\s+mesh check", re.IGNORECASE)
RE_NEGVOL = re.compile(r"(zero or negative cell volume|negative volume)", re.IGNORECASE)
RE_MESH_OK = re.compile(r"^\s*Mesh OK\.?\s*$", re.MULTILINE)

# Lines checkMesh marks with *** are failures; ** / * are warnings.
RE_STARRED = re.compile(r"^\s*\*{1,3}\s*(.+)$", re.MULTILINE)


def _f(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


@dataclass
class MeshQuality:
    """Everything we could learn from a checkMesh report."""
    parsed: bool = False
    n_cells: int | None = None
    n_points: int | None = None
    n_faces: int | None = None
    max_non_orthogonality: float | None = None
    avg_non_orthogonality: float | None = None
    n_severely_non_orthogonal: int | None = None
    max_skewness: float | None = None
    n_highly_skewed: int | None = None
    max_aspect_ratio: float | None = None
    min_volume: float | None = None
    has_negative_volumes: bool = False
    failed_checks: int = 0
    mesh_ok: bool = False
    warnings: list = field(default_factory=list)   # the starred lines, verbatim
    problems: list = field(default_factory=list)   # our judgements

    # --- derived advice -------------------------------------------------------
    def recommended_non_orthogonal_correctors(self) -> int | None:
        """How many nNonOrthogonalCorrectors this mesh really needs."""
        m = self.max_non_orthogonality
        if m is None:
            return None
        if m < 35:
            return 0
        if m < NON_ORTHO_FINE:
            return 1
        if m < NON_ORTHO_SEVERE:
            return 2
        return 3

    def is_problematic(self) -> bool:
        return bool(self.problems) or self.has_negative_volumes or self.failed_checks > 0

    def summary(self) -> str:
        if not self.parsed:
            return ("No checkMesh output was available, so mesh quality could not be "
                    "assessed. Run checkMesh and supply the output to settle any "
                    "mesh-related question.")
        bits = []
        if self.n_cells:
            bits.append(f"{self.n_cells:,} cells")
        if self.max_non_orthogonality is not None:
            bits.append(f"max non-orthogonality {self.max_non_orthogonality:g}"
                        f" (avg {self.avg_non_orthogonality:g})" if self.avg_non_orthogonality
                        else f"max non-orthogonality {self.max_non_orthogonality:g}")
        if self.max_skewness is not None:
            bits.append(f"max skewness {self.max_skewness:g}")
        if self.max_aspect_ratio is not None:
            bits.append(f"max aspect ratio {self.max_aspect_ratio:g}")
        if self.has_negative_volumes:
            bits.append("NEGATIVE CELL VOLUMES present")
        if self.failed_checks:
            bits.append(f"{self.failed_checks} checkMesh check(s) failed")
        elif self.mesh_ok:
            bits.append("checkMesh reported 'Mesh OK'")
        return "; ".join(bits) + "."


def analyze_checkmesh(text: str) -> MeshQuality:
    """Parse checkMesh output. Never raises; unparseable input yields parsed=False."""
    q = MeshQuality()
    if not text or "checkMesh" not in text and "non-orthogonality" not in text.lower() \
            and "Mesh stats" not in text:
        return q

    m = RE_CELLS.search(text)
    q.n_cells = int(m.group(1)) if m else None
    m = RE_POINTS.search(text)
    q.n_points = int(m.group(1)) if m else None
    m = RE_FACES.search(text)
    q.n_faces = int(m.group(1)) if m else None

    m = RE_NONORTHO.search(text)
    if m:
        q.max_non_orthogonality = _f(m.group(1))
        q.avg_non_orthogonality = _f(m.group(2))
    m = RE_SEVERE.search(text)
    q.n_severely_non_orthogonal = int(m.group(1)) if m else None

    # Take the WORST skewness mentioned (the report can print it more than once).
    skews = [_f(s) for s in RE_SKEW.findall(text)]
    skews = [s for s in skews if s is not None]
    q.max_skewness = max(skews) if skews else None
    m = RE_SKEW_COUNT.search(text)
    q.n_highly_skewed = int(m.group(1)) if m else None

    m = RE_ASPECT.search(text)
    q.max_aspect_ratio = _f(m.group(1)) if m else None
    m = RE_MINVOL.search(text)
    q.min_volume = _f(m.group(1)) if m else None

    q.has_negative_volumes = bool(RE_NEGVOL.search(text)) or (
        q.min_volume is not None and q.min_volume <= 0
    )
    m = RE_FAILED.search(text)
    q.failed_checks = int(m.group(1)) if m else 0
    q.mesh_ok = bool(RE_MESH_OK.search(text))
    q.warnings = [w.strip() for w in RE_STARRED.findall(text)][:20]

    q.parsed = any(v is not None for v in
                   (q.n_cells, q.max_non_orthogonality, q.max_skewness, q.max_aspect_ratio))
    if q.parsed:
        _judge(q)
    return q


def _judge(q: MeshQuality) -> None:
    """Turn raw metrics into plain-English problems, worst first."""
    if q.has_negative_volumes:
        q.problems.append(
            "The mesh contains zero or negative cell volumes. No solver can run on this; "
            "the mesh must be regenerated before anything else is worth trying."
        )
    m = q.max_non_orthogonality
    if m is not None:
        if m >= NON_ORTHO_EXTREME:
            q.problems.append(
                f"Non-orthogonality reaches {m:g}, which is extreme. Expect pressure "
                f"convergence problems no matter how the numerics are tuned; improve the "
                f"mesh, and meanwhile use at least 3 nNonOrthogonalCorrectors."
            )
        elif m >= NON_ORTHO_SEVERE:
            q.problems.append(
                f"Non-orthogonality reaches {m:g} (above OpenFOAM's severe threshold of "
                f"{NON_ORTHO_SEVERE:g}). Use nNonOrthogonalCorrectors 2-3 and a limited "
                f"snGrad/laplacian scheme such as 'limited 0.33'."
            )
        elif m >= NON_ORTHO_FINE:
            q.problems.append(
                f"Non-orthogonality reaches {m:g}. This is workable but needs "
                f"nNonOrthogonalCorrectors of at least 1-2."
            )
    s = q.max_skewness
    if s is not None:
        if s >= SKEWNESS_BAD:
            q.problems.append(
                f"Max skewness is {s:g}, which is very high. Highly skewed cells are a "
                f"common source of local divergence; find and fix them before tuning schemes."
            )
        elif s >= SKEWNESS_WARN:
            q.problems.append(
                f"Max skewness is {s:g}, above the usual warning level of {SKEWNESS_WARN:g}. "
                f"Consider a limited correction scheme, or improve the mesh locally."
            )
    a = q.max_aspect_ratio
    if a is not None:
        if a >= ASPECT_BAD:
            q.problems.append(
                f"Max aspect ratio is {a:g}. Extremely stretched cells slow convergence badly "
                f"and can destabilise the pressure solve."
            )
        elif a >= ASPECT_WARN:
            q.problems.append(
                f"Max aspect ratio is {a:g}. That is acceptable in boundary layers but should "
                f"be checked if it occurs in the bulk of the domain."
            )
    if q.failed_checks and not q.problems:
        q.problems.append(
            f"checkMesh reported {q.failed_checks} failed check(s); see the starred lines."
        )


def relate_to_solver(mesh: MeshQuality, log_analysis) -> list[str]:
    """
    Connect mesh quality to what the solver actually did.

    `log_analysis` is a LogAnalysis from log_intelligence (or None). This is the
    step that turns two separate reports into one diagnosis.
    """
    notes: list[str] = []
    if not mesh.parsed:
        return notes

    stalled = []
    diverging = []
    if log_analysis is not None and getattr(log_analysis, "parsed", False):
        stalled = [k for k, v in log_analysis.residual_trend.items() if v == "flat"]
        diverging = [k for k, v in log_analysis.residual_trend.items() if v == "diverging"]

    m = mesh.max_non_orthogonality
    if m is not None and m >= NON_ORTHO_FINE and ("p" in stalled or "p" in diverging):
        notes.append(
            f"The pressure residual {'stalled' if 'p' in stalled else 'diverged'} and the mesh "
            f"is {m:g} degrees non-orthogonal. These are almost certainly the same problem: "
            f"raise nNonOrthogonalCorrectors to "
            f"{mesh.recommended_non_orthogonal_correctors()} before changing anything else."
        )
    if mesh.max_skewness is not None and mesh.max_skewness >= SKEWNESS_WARN and diverging:
        notes.append(
            f"The run diverged and the mesh has skewness up to {mesh.max_skewness:g}. Localised "
            f"divergence on a skewed mesh usually starts in those cells, so check where the "
            f"first nan appeared before re-tuning the schemes."
        )
    if mesh.has_negative_volumes:
        notes.append(
            "Negative cell volumes make every other finding secondary -- fix the mesh first."
        )
    if not notes and mesh.is_problematic():
        notes.append(
            "The mesh has quality issues, but the log does not clearly implicate them. "
            "Treat the mesh as a contributing factor rather than the primary cause."
        )
    return notes


def find_checkmesh_output(files: dict[str, str]) -> str | None:
    """Locate checkMesh output among the case files (often log.checkMesh)."""
    for rel, text in (files or {}).items():
        if not text:
            continue
        if "checkmesh" in rel.lower() or "Mesh stats" in text or "non-orthogonality Max" in text:
            return text
    return None
