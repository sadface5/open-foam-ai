"""
Compare a working case against a broken one.

"It worked yesterday" is the most useful debugging clue there is, and the fastest
route to a fix is usually a diff -- but a raw text diff is useless here, because
it buries the one meaningful change under dozens of harmless ones (a changed
endTime, a reordered block, a different comment).

So this module diffs cases SEMANTICALLY: it compares parsed settings rather than
lines, then ranks each difference by how likely it is to have caused a failure.
Changing a divergence scheme from 'upwind' to 'linear' is reported far above a
changed writeInterval, because one of those breaks runs and the other does not.

Read-only, text only, no OpenFOAM required.
"""
from dataclasses import dataclass, field

from .case_survey import read_case_full, survey_case
from .rules.context import CaseContext, _as_float

# --- How suspicious is a change to each setting? ------------------------------
# Higher = more likely to be the reason a working case stopped working.
RISK_WEIGHTS = {
    "missing-field": 95,
    "turbulence-model": 90,
    "application": 90,
    "bc-type": 85,
    "ddt-scheme": 80,
    "div-scheme": 80,
    "relaxation": 75,
    "delta-t": 70,
    "algorithm": 70,
    "mesh": 65,
    "decomposition": 55,
    "solver-entry": 50,
    "tolerance": 40,
    "transport": 60,
    "added-field": 35,
    "control": 15,          # endTime, writeInterval, purgeWrite: rarely fatal
}

# controlDict keys that are essentially cosmetic for debugging purposes.
COSMETIC_CONTROL_KEYS = {
    "writeInterval", "purgeWrite", "writeFormat", "writePrecision",
    "writeCompression", "timeFormat", "timePrecision", "runTimeModifiable",
    "startFrom", "startTime", "stopAt", "endTime", "graphFormat",
}


@dataclass
class Difference:
    """One semantic difference between the two cases."""
    kind: str                 # a key of RISK_WEIGHTS
    where: str                # human-readable location
    working: str | None
    broken: str | None
    risk: int = 0
    rationale: str = ""
    files: list = field(default_factory=list)

    def as_line(self) -> str:
        w = self.working if self.working is not None else "(absent)"
        b = self.broken if self.broken is not None else "(absent)"
        return f"[risk {self.risk:3d}] {self.where}: working='{w}' -> broken='{b}'" + \
               (f"  -- {self.rationale}" if self.rationale else "")


@dataclass
class CaseComparison:
    differences: list = field(default_factory=list)
    working_only_files: list = field(default_factory=list)
    broken_only_files: list = field(default_factory=list)

    def top(self, n: int = 10) -> list:
        return self.differences[:n]

    def summary(self) -> str:
        if not self.differences:
            return ("No meaningful differences were found between the two cases. "
                    "If one runs and the other does not, the cause is likely outside "
                    "these files -- the mesh, the environment, or the command used.")
        lines = [f"Found {len(self.differences)} meaningful difference(s), most suspicious first:"]
        for d in self.top():
            lines.append("  " + d.as_line())
        return "\n".join(lines)


def _add(diffs, kind, where, working, broken, rationale="", files=None):
    diffs.append(Difference(
        kind=kind, where=where,
        working=None if working is None else str(working),
        broken=None if broken is None else str(broken),
        risk=RISK_WEIGHTS.get(kind, 30),
        rationale=rationale,
        files=list(files or []),
    ))


def _compare_scalar_dict(diffs, name, a: dict, b: dict, kind: str,
                         skip: set | None = None, files=None):
    """Compare two {key: value} mappings taken from the same dictionary file."""
    skip = skip or set()
    for key in sorted(set(a) | set(b)):
        if key in skip:
            continue
        va, vb = a.get(key), b.get(key)
        if va == vb:
            continue
        _add(diffs, kind, f"{name}.{key}", va, vb, files=files)


def compare_contexts(working: CaseContext, broken: CaseContext) -> CaseComparison:
    """Semantic comparison of two already-loaded cases."""
    diffs: list[Difference] = []

    # --- solver / application -------------------------------------------------
    if working.application != broken.application:
        _add(diffs, "application", "controlDict.application",
             working.application, broken.application,
             "A different solver needs different fields, schemes and algorithm block.",
             ["system/controlDict"])

    # --- time stepping --------------------------------------------------------
    if working.delta_t != broken.delta_t:
        rationale = ""
        wa, wb = working.delta_t, broken.delta_t
        if wa and wb and wb > wa:
            rationale = (f"deltaT increased {wb / wa:.1f}x, which raises the Courant number "
                         f"and is a frequent cause of divergence.")
        _add(diffs, "delta-t", "controlDict.deltaT", wa, wb, rationale, ["system/controlDict"])

    _compare_scalar_dict(diffs, "controlDict", working.control, broken.control,
                         "control", skip=COSMETIC_CONTROL_KEYS | {"application", "deltaT"},
                         files=["system/controlDict"])

    # --- schemes --------------------------------------------------------------
    if working.ddt_scheme != broken.ddt_scheme:
        _add(diffs, "ddt-scheme", "fvSchemes.ddtSchemes.default",
             working.ddt_scheme, broken.ddt_scheme,
             "Switching between steady and transient changes the whole solution strategy.",
             ["system/fvSchemes"])

    for key in sorted(set(working.div_schemes) | set(broken.div_schemes)):
        va, vb = working.div_schemes.get(key), broken.div_schemes.get(key)
        if va == vb:
            continue
        rationale = ""
        if vb and "upwind" not in vb.lower() and va and "upwind" in va.lower():
            rationale = ("Moved from a bounded/upwind scheme to a less stable one; this "
                         "commonly turns a working case into a diverging one.")
        elif vb and "bounded" not in vb.lower() and va and "bounded" in va.lower():
            rationale = "Lost the 'bounded' prefix, which matters on steady runs."
        _add(diffs, "div-scheme", f"fvSchemes.divSchemes.{key}", va, vb, rationale,
             ["system/fvSchemes"])

    # --- relaxation -----------------------------------------------------------
    for key in sorted(set(working.relaxation) | set(broken.relaxation)):
        va, vb = working.relaxation.get(key), broken.relaxation.get(key)
        if va == vb:
            continue
        fa, fb = _as_float(va), _as_float(vb)
        rationale = ""
        if fa is not None and fb is not None and fb > fa:
            rationale = (f"Relaxation for '{key}' was raised from {fa} to {fb}; "
                         f"more aggressive relaxation destabilises steady runs.")
        _add(diffs, "relaxation", f"fvSolution.relaxationFactors.{key}", va, vb, rationale,
             ["system/fvSolution"])

    if working.algorithm_block != broken.algorithm_block:
        _add(diffs, "algorithm", "fvSolution algorithm block",
             working.algorithm_block, broken.algorithm_block,
             "The algorithm block must match the solver in controlDict.",
             ["system/fvSolution"])

    # --- turbulence -----------------------------------------------------------
    if working.turbulence_model != broken.turbulence_model:
        _add(diffs, "turbulence-model", "turbulenceProperties model",
             working.turbulence_model, broken.turbulence_model,
             "A different model requires different 0/ fields and wall treatment.",
             ["constant/turbulenceProperties"])
    if working.simulation_type != broken.simulation_type:
        _add(diffs, "turbulence-model", "turbulenceProperties.simulationType",
             working.simulation_type, broken.simulation_type, "",
             ["constant/turbulenceProperties"])

    # --- transport ------------------------------------------------------------
    if working.nu != broken.nu:
        rationale = ""
        if working.nu and broken.nu and broken.nu < working.nu:
            rationale = (f"Viscosity fell {working.nu / broken.nu:.0f}x, raising the Reynolds "
                         f"number; the old mesh and schemes may no longer be adequate.")
        _add(diffs, "transport", "transportProperties.nu", working.nu, broken.nu, rationale,
             ["constant/transportProperties"])

    # --- 0/ fields ------------------------------------------------------------
    wf, bf = set(working.zero_fields), set(broken.zero_fields)
    for name in sorted(wf - bf):
        _add(diffs, "missing-field", f"0/{name}", "present", "absent",
             "The working case has this field and the broken one does not; a missing "
             "required field stops the run immediately.", [f"0/{name}"])
    for name in sorted(bf - wf):
        _add(diffs, "added-field", f"0/{name}", "absent", "present",
             "Only in the broken case; harmless unless it conflicts with the model.",
             [f"0/{name}"])

    # --- boundary conditions --------------------------------------------------
    for name in sorted(wf & bf):
        wp = working.field_patch_types.get(name, {})
        bp = broken.field_patch_types.get(name, {})
        for patch in sorted(set(wp) | set(bp)):
            va, vb = wp.get(patch), bp.get(patch)
            if va == vb:
                continue
            _add(diffs, "bc-type", f"0/{name} patch '{patch}' type", va, vb,
                 "A changed boundary condition type alters the physics at that patch.",
                 [f"0/{name}"])

    # --- mesh / decomposition -------------------------------------------------
    wm, bm = working.mesh_patches, broken.mesh_patches
    if wm and bm:
        for patch in sorted(set(wm) | set(bm)):
            ta = (wm.get(patch) or {}).get("type")
            tb = (bm.get(patch) or {}).get("type")
            if ta != tb:
                _add(diffs, "mesh", f"polyMesh/boundary '{patch}' type", ta, tb,
                     "A patch changing between wall and patch invalidates wall functions.",
                     ["constant/polyMesh/boundary"])
        wn = {p: (d or {}).get("nFaces") for p, d in wm.items()}
        bn = {p: (d or {}).get("nFaces") for p, d in bm.items()}
        if wn != bn:
            _add(diffs, "mesh", "polyMesh/boundary face counts",
                 "see boundary file", "differs",
                 "The mesh itself changed, so mesh-quality conclusions must be re-checked.",
                 ["constant/polyMesh/boundary"])

    if working.n_subdomains != broken.n_subdomains:
        _add(diffs, "decomposition", "decomposeParDict.numberOfSubdomains",
             working.n_subdomains, broken.n_subdomains, "",
             ["system/decomposeParDict"])

    diffs.sort(key=lambda d: (-d.risk, d.where))
    return CaseComparison(differences=diffs)


def compare_cases(working_path: str, broken_path: str) -> CaseComparison:
    """
    Compare two case folders on disk.

    `working_path` is the case that runs; `broken_path` is the one that does not.
    """
    w_files, b_files = read_case_full(working_path), read_case_full(broken_path)
    try:
        w_survey, b_survey = survey_case(working_path), survey_case(broken_path)
    except OSError:
        w_survey = b_survey = None

    result = compare_contexts(
        CaseContext(w_files, survey=w_survey, case_path=working_path),
        CaseContext(b_files, survey=b_survey, case_path=broken_path),
    )
    result.working_only_files = sorted(set(w_files) - set(b_files))
    result.broken_only_files = sorted(set(b_files) - set(w_files))
    return result
