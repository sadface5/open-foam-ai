"""
Numerical settings and stability rules.

These look at fvSchemes, fvSolution and controlDict for settings that are legal
OpenFOAM but are known to cause divergence, stalled convergence, or needlessly
slow runs. Unlike the consistency rules, most of these are judgement calls, so
they are reported with 'likely'/'possible' confidence and the AI is expected to
weigh them against the rest of the case.
"""
from .context import _as_float
from . import (CATEGORY_NUMERICS, CATEGORY_SOLVER, CRITICAL, INFO, LIKELY,
               POSSIBLE, WARNING, finding, rule)

# Divergence schemes that are second-order but unbounded -- accurate yet fragile
# on poor meshes, and a very common cause of early blow-up.
UNBOUNDED_DIV_SCHEMES = ("linear", "cubic", "vanleer", "limitedlinear01")

# Recommended upper bounds for under-relaxation on a steady SIMPLE run.
TYPICAL_RELAXATION = {"p": 0.3, "U": 0.7, "k": 0.7, "epsilon": 0.7, "omega": 0.7}


@rule("relaxation-out-of-range", CATEGORY_NUMERICS, CRITICAL,
      "Relaxation factor outside the valid range")
def relaxation_range(ctx):
    """Relaxation must be in (0, 1]. Anything else is invalid, not merely risky."""
    out = []
    for name, value in ctx.relaxation.items():
        f = _as_float(value)
        if f is None:
            continue
        if not (0.0 < f <= 1.0):
            out.append(finding(
                f"relaxationFactor '{name}' = {value} is outside the valid range (0, 1].",
                files=["system/fvSolution"],
                evidence=[f"{name}={value}"],
                suggestion=f"Set '{name}' to a value between 0 and 1 "
                           f"(commonly {TYPICAL_RELAXATION.get(name, 0.7)}).",
            ))
    return out


@rule("relaxation-aggressive", CATEGORY_NUMERICS, WARNING,
      "Relaxation factor high enough to risk divergence")
def relaxation_aggressive(ctx):
    """
    Valid but aggressive relaxation on a steady run. p above ~0.5 in particular
    is a frequent cause of a SIMPLE run diverging in the first few iterations.
    """
    if not ctx.is_steady:
        return None  # transient runs legitimately use 1.0
    out = []
    for name, limit in TYPICAL_RELAXATION.items():
        value = ctx.relaxation.get(name)
        f = _as_float(value)
        if f is None or not (0.0 < f <= 1.0):
            continue
        if f > limit + 0.15:
            out.append(finding(
                f"Relaxation for '{name}' is {value}, noticeably higher than the usual "
                f"steady-state value of about {limit}.",
                files=["system/fvSolution"],
                evidence=[f"{name}={value}", f"typical={limit}"],
                suggestion=f"If the run diverges early, lower '{name}' towards {limit}.",
                confidence=LIKELY,
            ))
    return out


@rule("unbounded-div-scheme", CATEGORY_NUMERICS, WARNING,
      "Unbounded divergence scheme on a case that may not tolerate it")
def unbounded_divergence(ctx):
    """
    Pure 'linear' divergence schemes are second-order but unbounded. On a skewed
    or non-orthogonal mesh they overshoot and blow up.
    """
    out = []
    # Only convection terms: the stress term div((nuEff*dev2(T(grad(U))))) is
    # correctly 'Gauss linear' almost always, and flagging it is a false alarm.
    for key, value in ctx.convection_div_schemes.items():
        v = (value or "").lower()
        if not v:
            continue
        if any(s in v for s in UNBOUNDED_DIV_SCHEMES) and "limited" not in v and "upwind" not in v:
            out.append(finding(
                f"divScheme '{key}' uses '{value}', which is unbounded and can overshoot "
                f"on a non-orthogonal or skewed mesh.",
                files=["system/fvSchemes"],
                evidence=[f"{key} = {value}"],
                suggestion="For stability try 'bounded Gauss upwind' first, then move to "
                           "'bounded Gauss linearUpwind grad(U)' once it runs.",
                confidence=POSSIBLE,
            ))
    return out


@rule("steady-missing-bounded", CATEGORY_NUMERICS, INFO,
      "Steady run without 'bounded' divergence schemes")
def steady_bounded(ctx):
    """
    On a steady SIMPLE run the convection term should normally be 'bounded',
    which suppresses the error from a not-yet-converged continuity field.
    """
    conv = ctx.convection_div_schemes
    if not ctx.is_steady or not conv:
        return None
    unbounded = [f"{k} = {v}" for k, v in conv.items() if "bounded" not in (v or "").lower()]
    if not unbounded:
        return None
    return finding(
        "This is a steady-state run, but its convection divSchemes are not marked 'bounded'.",
        files=["system/fvSchemes"],
        evidence=unbounded,
        suggestion="Prefix steady convection schemes with 'bounded', e.g. "
                   "'bounded Gauss linearUpwind grad(U)'.",
        confidence=LIKELY,
    )


@rule("transient-fixed-large-dt", CATEGORY_NUMERICS, WARNING,
      "Transient run with a fixed time step and no Courant limit")
def transient_time_step(ctx):
    """
    A transient run with adjustTimeStep off has no protection against the
    Courant number climbing, which is the classic route to a nan.
    """
    if ctx.is_steady or ctx.delta_t is None:
        return None
    if ctx.adjustable_time_step:
        return None
    return finding(
        f"Transient run uses a fixed deltaT of {ctx.delta_t} with adjustTimeStep off, so the "
        f"Courant number is not limited.",
        files=["system/controlDict"],
        evidence=[f"deltaT={ctx.delta_t}", "adjustTimeStep=no"],
        suggestion="Set adjustTimeStep yes and maxCo (around 1 for PISO, up to 5-10 for PIMPLE), "
                   "or reduce deltaT.",
        confidence=LIKELY,
    )


@rule("courant-too-high", CATEGORY_NUMERICS, WARNING,
      "maxCo set high for the chosen algorithm")
def courant_limit(ctx):
    """PISO needs Co below about 1; only PIMPLE's outer correctors tolerate more."""
    co = ctx.max_courant
    if co is None or not ctx.adjustable_time_step:
        return None
    block = ctx.algorithm_block
    if block == "PISO" and co > 1.0:
        return finding(
            f"maxCo is {co}, but the PISO algorithm is generally only stable below about 1.",
            files=["system/controlDict", "system/fvSolution"],
            evidence=[f"maxCo={co}", "algorithm=PISO"],
            suggestion="Lower maxCo to about 0.9, or switch to PIMPLE with outer correctors.",
            confidence=LIKELY,
        )
    if co > 20:
        return finding(
            f"maxCo is {co}, which is very high even for PIMPLE.",
            files=["system/controlDict"],
            evidence=[f"maxCo={co}"],
            suggestion="Reduce maxCo, or add outer correctors (nOuterCorrectors) to cope.",
            confidence=POSSIBLE,
        )
    return None


@rule("missing-solver-entry", CATEGORY_SOLVER, WARNING,
      "A solved field has no entry in fvSolution solvers")
def solver_entries(ctx):
    """Every field the solver solves for needs a linear-solver entry."""
    if not ctx.solver_fields:
        return None
    needed = ["p", "U"] + list(ctx.required_turbulence_fields)
    present_fields = set(ctx.zero_fields)
    out = []
    for f in needed:
        if f not in present_fields or f == "nut":
            continue  # nut is computed, never solved
        if not any(s == f or s.startswith(f) for s in ctx.solver_fields):
            out.append(finding(
                f"Field '{f}' exists in 0/ but has no solver entry in fvSolution.",
                files=["system/fvSolution"],
                evidence=[f"solvers block defines: {', '.join(sorted(ctx.solver_fields))}"],
                suggestion=f"Add a '{f}' entry to the solvers block.",
            ))
    return out


@rule("write-interval-flood", CATEGORY_NUMERICS, INFO,
      "Write interval will produce a very large number of time directories")
def write_interval(ctx):
    """Writing every step fills the disk and slows the run dramatically."""
    end, interval = ctx.end_time, ctx.write_interval
    ctrl = ctx.control.get("writeControl", "").strip().lower()
    if not end or not interval or interval <= 0:
        return None
    if ctrl not in ("runtime", "adjustableruntime", ""):
        return None
    n = end / interval
    if n < 500:
        return None
    return finding(
        f"The run would write roughly {int(n)} time directories "
        f"(endTime={end}, writeInterval={interval}).",
        files=["system/controlDict"],
        evidence=[f"endTime={end}", f"writeInterval={interval}", f"writeControl={ctrl or 'default'}"],
        suggestion="Increase writeInterval, or set purgeWrite to keep only recent times.",
        confidence=LIKELY,
    )


@rule("nonorthogonal-correctors-missing", CATEGORY_NUMERICS, INFO,
      "No non-orthogonal correctors configured")
def nonorthogonal_correctors(ctx):
    """
    Real meshes are rarely orthogonal; without correctors the pressure equation
    carries an error that often shows up as slow or stalled convergence.
    """
    text = ctx.text("system/fvSolution")
    block = ctx.algorithm_block
    if not text or not block:
        return None
    from .. import openfoam_parser as ofp
    inner = ofp.content_of(text, block) or ""
    value = _as_float(ofp.scalar_entries(inner).get("nNonOrthogonalCorrectors"))
    if value is None:
        return finding(
            f"The {block} block does not set nNonOrthogonalCorrectors.",
            files=["system/fvSolution"],
            evidence=[f"algorithm={block}"],
            suggestion="Add nNonOrthogonalCorrectors 1 (or 2 for a strongly non-orthogonal mesh).",
            confidence=POSSIBLE,
        )
    if value == 0:
        return finding(
            f"nNonOrthogonalCorrectors is 0, so mesh non-orthogonality is not corrected.",
            files=["system/fvSolution"],
            evidence=["nNonOrthogonalCorrectors=0"],
            suggestion="If checkMesh reports non-orthogonality above about 60, raise this to 1-2.",
            confidence=POSSIBLE,
        )
    return None
