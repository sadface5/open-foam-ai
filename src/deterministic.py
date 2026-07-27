"""
Deterministic checks: things the app can verify by itself, WITHOUT the AI.

Running these first means Claude receives machine-verified facts (missing files,
patch mismatches, out-of-range numbers, ...) instead of having to infer them.
Each check returns a Finding with a clear status. These are shown in the progress
panel AND handed to Claude as evidence, clearly labeled as app-computed.

This module never runs OpenFOAM; it only reads text files.
"""
from dataclasses import dataclass, field

from . import openfoam_parser as ofp

# Status values, roughly in order of importance.
CRITICAL = "critical"
WARNING = "warning"
INFO = "info"
OK = "ok"

# Fields we consider "always solved" and therefore expect solver entries for.
CORE_SOLVED_FIELDS = ["p", "U"]


@dataclass
class Finding:
    check: str
    status: str          # critical | warning | info | ok
    detail: str
    files: list = field(default_factory=list)


def _has(case_files: dict, *names: str) -> str | None:
    """Return the first of the given relative paths that is present, else None."""
    for name in names:
        if name in case_files:
            return name
    return None


def _zero_fields(case_files: dict) -> dict[str, str]:
    """Map bare field name -> its relative path, for whichever 0/ folder exists."""
    out = {}
    for rel in case_files:
        for prefix in ("0/", "0.orig/"):
            if rel.startswith(prefix):
                out[rel[len(prefix):]] = rel
    return out


def run_deterministic_checks(case_files: dict[str, str], skill_name: str = "") -> list[Finding]:
    """Run every feasible check against the provided case files."""
    findings: list[Finding] = []
    if not case_files:
        findings.append(Finding("case-files", INFO, "No case folder was provided; only pasted text is available."))
        return findings

    _check_key_files(case_files, findings)
    _check_brace_balance(case_files, findings)
    _check_patch_coverage(case_files, findings)
    _check_turbulence_fields(case_files, findings)
    _check_solver_entries(case_files, findings)
    _check_scheme_sections(case_files, findings)
    _check_steady_transient(case_files, findings)
    _check_numeric_ranges(case_files, findings)

    if not findings:
        findings.append(Finding("overall", OK, "No deterministic problems detected in the readable files."))
    return findings


# --- individual checks --------------------------------------------------------
def _check_key_files(cf, findings):
    important = {
        "system/controlDict": WARNING,
        "system/fvSchemes": WARNING,
        "system/fvSolution": WARNING,
        "constant/polyMesh/boundary": INFO,
    }
    for path, sev in important.items():
        if path not in cf:
            findings.append(Finding("missing-file", sev, f"{path} was not found (or not readable).", [path]))
    turb = _has(cf, "constant/turbulenceProperties", "constant/momentumTransport")
    if not turb:
        findings.append(Finding("missing-file", INFO, "No turbulenceProperties/momentumTransport found.",
                                ["constant/turbulenceProperties"]))
    if not _zero_fields(cf):
        findings.append(Finding("missing-file", WARNING, "No initial field files found (0/ or 0.orig/).", ["0/"]))


def _check_brace_balance(cf, findings):
    for rel, text in cf.items():
        if rel.startswith(("system/", "constant/", "0/", "0.orig/")):
            bal = ofp.brace_balance(text)
            if bal != 0:
                findings.append(Finding("malformed-dict", CRITICAL,
                                        f"{rel} has unbalanced braces ({bal:+d}); the file may be malformed.", [rel]))


def _check_patch_coverage(cf, findings):
    boundary = cf.get("constant/polyMesh/boundary")
    if not boundary:
        return  # cannot verify coverage without the authoritative patch list
    patches = set(ofp.parse_boundary(boundary).keys())
    if not patches:
        return
    for field_name, rel in _zero_fields(cf).items():
        field_patches = set(ofp.parse_field(cf[rel]).get("patches", []))
        if not field_patches:
            continue
        missing = patches - field_patches
        extra = field_patches - patches
        if missing:
            findings.append(Finding("patch-coverage", CRITICAL,
                                    f"{rel} is missing boundary entries for: {', '.join(sorted(missing))}.", [rel]))
        # Flag case-only mismatches (e.g. Inlet vs inlet) as they are easy to miss.
        lower_patches = {p.lower(): p for p in patches}
        for e in extra:
            if e.lower() in lower_patches:
                findings.append(Finding("patch-case", WARNING,
                                        f"{rel} has patch '{e}' but the mesh calls it '{lower_patches[e.lower()]}' (case mismatch).", [rel]))
            else:
                findings.append(Finding("patch-extra", WARNING,
                                        f"{rel} defines patch '{e}' that is not in the mesh boundary.", [rel]))


def _check_turbulence_fields(cf, findings):
    turb_path = _has(cf, "constant/turbulenceProperties", "constant/momentumTransport")
    if not turb_path:
        return
    info = ofp.parse_turbulence(cf[turb_path])
    required = ofp.required_turbulence_fields(info.get("model") or "")
    if not required:
        return
    present = set(_zero_fields(cf).keys())
    missing = [f for f in required if f not in present]
    if missing:
        findings.append(Finding("turbulence-fields", CRITICAL,
                                f"Turbulence model '{info['model']}' needs field(s) {', '.join(missing)} in 0/, but they are missing.",
                                [turb_path]))


def _check_solver_entries(cf, findings):
    fvsol = cf.get("system/fvSolution")
    if not fvsol:
        return
    solvers_block = ofp.content_of(fvsol, "solvers")
    if solvers_block is None:
        findings.append(Finding("solvers-block", CRITICAL, "system/fvSolution has no 'solvers' block.", ["system/fvSolution"]))
        return
    solver_fields = set(ofp.subdict_names(solvers_block))
    present_fields = set(_zero_fields(cf).keys())
    for f in CORE_SOLVED_FIELDS:
        # p may be written pFinal etc.; treat a prefix match as present
        if f in present_fields and not any(s == f or s.startswith(f) for s in solver_fields):
            findings.append(Finding("solver-entry", WARNING,
                                    f"No solver entry for field '{f}' in fvSolution 'solvers'.", ["system/fvSolution"]))


def _check_scheme_sections(cf, findings):
    fvsch = cf.get("system/fvSchemes")
    if not fvsch:
        return
    for section in ["ddtSchemes", "gradSchemes", "divSchemes", "laplacianSchemes", "snGradSchemes"]:
        if ofp.content_of(fvsch, section) is None:
            findings.append(Finding("scheme-section", WARNING,
                                    f"system/fvSchemes is missing the '{section}' section.", ["system/fvSchemes"]))


def _check_steady_transient(cf, findings):
    ctrl = cf.get("system/controlDict")
    fvsch = cf.get("system/fvSchemes")
    if not ctrl:
        return
    app = ofp.scalar_entries(ctrl).get("application", "").lower()
    ddt = ""
    if fvsch:
        ddt_block = ofp.content_of(fvsch, "ddtSchemes") or ""
        ddt = ofp.scalar_entries(ddt_block).get("default", "").lower()
    steady_app = "simple" in app
    if app and ddt:
        if steady_app and "steadystate" not in ddt:
            findings.append(Finding("steady-transient", WARNING,
                                    f"controlDict application '{app}' looks steady, but ddtScheme is '{ddt}' (not steadyState).",
                                    ["system/controlDict", "system/fvSchemes"]))
        if (not steady_app) and app and "steadystate" in ddt:
            findings.append(Finding("steady-transient", WARNING,
                                    f"ddtScheme is steadyState but application '{app}' looks transient.",
                                    ["system/controlDict", "system/fvSchemes"]))


def _check_numeric_ranges(cf, findings):
    # Relaxation factors must be within (0, 1].
    fvsol = cf.get("system/fvSolution")
    if fvsol:
        block = ofp.content_of(fvsol, "relaxationFactors")
        if block:
            entries = dict(ofp.scalar_entries(block))
            for sub in ("fields", "equations"):
                inner = ofp.content_of(block, sub)
                if inner:
                    entries.update(ofp.scalar_entries(inner))
            for name, val in entries.items():
                f = _as_float(val)
                if f is not None and not (0.0 < f <= 1.0):
                    findings.append(Finding("relaxation-range", CRITICAL,
                                            f"relaxationFactor '{name}' = {val} is outside the valid range (0, 1].",
                                            ["system/fvSolution"]))
    # Kinematic viscosity must be positive.
    tp = cf.get("constant/transportProperties")
    if tp:
        nu = ofp.scalar_entries(tp).get("nu", "")
        f = _last_float(nu)
        if f is not None and f <= 0:
            findings.append(Finding("viscosity", CRITICAL, f"nu appears to be non-positive ({nu}).", ["constant/transportProperties"]))
    # deltaT must be positive.
    ctrl = cf.get("system/controlDict")
    if ctrl:
        dt = ofp.scalar_entries(ctrl).get("deltaT", "")
        f = _as_float(dt)
        if f is not None and f <= 0:
            findings.append(Finding("deltaT", CRITICAL, f"deltaT appears to be non-positive ({dt}).", ["system/controlDict"]))


def _as_float(s: str):
    try:
        return float(str(s).strip().split()[0])
    except (ValueError, IndexError):
        return None


def _last_float(s: str):
    """Grab the last number in a string like 'nu [0 2 -1 0 0 0 0] 1e-05'."""
    import re
    nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(s))
    try:
        return float(nums[-1]) if nums else None
    except ValueError:
        return None
