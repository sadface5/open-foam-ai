"""
Rules that read the solver log and the checkMesh report.

The other rule modules inspect the case's dictionaries -- what the user INTENDED.
These read the evidence of what actually HAPPENED, and are usually the strongest
signal available, because a log records real behaviour rather than intent.

Wrapping the log and mesh analysers as rules means they reach the AI through the
same path as every other finding, with no extra wiring in the GUI.
"""
from ..log_intelligence import analyze_log
from ..mesh_intelligence import (analyze_checkmesh, find_checkmesh_output,
                                 relate_to_solver)
from . import (CATEGORY_MESH, CATEGORY_NUMERICS, CATEGORY_SOLVER, CONFIRMED,
               CRITICAL, INFO, LIKELY, WARNING, finding, rule)


def _primary_log(ctx):
    """
    The most informative log in the case: prefer a solver log over checkMesh,
    and among those prefer the one that actually recorded time steps.
    """
    best, best_steps = None, -1
    for rel, text in ctx.log_texts.items():
        if "checkmesh" in rel.lower():
            continue
        a = analyze_log(text)
        if not a.parsed:
            continue
        if a.n_steps > best_steps:
            best, best_steps = a, a.n_steps
    return best


@rule("log-crash", CATEGORY_SOLVER, CRITICAL, "The solver log records a failure")
def log_crash(ctx):
    a = _primary_log(ctx)
    if a is None or not a.crashed:
        return None
    evidence = [f"reason: {a.crash_reason}", f"time steps completed: {a.n_steps}"]
    if a.last_time is not None:
        evidence.append(f"last Time = {a.last_time}")
    if a.first_diverging_field:
        evidence.append(f"first field to rise: {a.first_diverging_field}")
    return finding(
        f"The run stopped: {a.crash_reason}.",
        files=list(ctx.log_texts),
        evidence=evidence,
        suggestion=(f"Start from '{a.first_diverging_field}', which went bad first."
                    if a.first_diverging_field else None),
    )


@rule("log-diverging-residuals", CATEGORY_NUMERICS, CRITICAL,
      "Residuals were rising before the run ended")
def log_diverging(ctx):
    a = _primary_log(ctx)
    if a is None:
        return None
    diverging = [k for k, v in a.residual_trend.items() if v == "diverging"]
    if not diverging:
        return None
    return finding(
        f"Residuals were climbing for: {', '.join(diverging)}.",
        files=list(ctx.log_texts),
        evidence=[f"{k}: {a.residual_trend[k]}" for k in sorted(a.residual_trend)],
        suggestion="Reduce the time step or relaxation, and switch convection to upwind "
                   "until the run is stable.",
    )


@rule("log-stalled-residuals", CATEGORY_NUMERICS, WARNING,
      "Residuals stopped improving without converging")
def log_stalled(ctx):
    a = _primary_log(ctx)
    if a is None or a.crashed or a.converged:
        return None
    stalled = [k for k, v in a.residual_trend.items() if v == "flat"]
    if not stalled:
        return None
    return finding(
        f"Residuals for {', '.join(stalled)} flattened out without reaching convergence.",
        files=list(ctx.log_texts),
        evidence=[f"{k}: flat" for k in stalled],
        suggestion="Typical causes are relaxation set too low, mesh quality, or a boundary "
                   "condition that cannot settle.",
        confidence=LIKELY,
    )


@rule("log-courant-growth", CATEGORY_NUMERICS, WARNING,
      "The Courant number grew during the run")
def log_courant(ctx):
    a = _primary_log(ctx)
    if a is None or a.courant_start is None or a.courant_end is None:
        return None
    if not (a.courant_end > a.courant_start * 3 and a.courant_end > 1):
        return None
    return finding(
        f"Maximum Courant number rose from {a.courant_start:g} to {a.courant_end:g}.",
        files=list(ctx.log_texts),
        evidence=[f"Co start={a.courant_start:g}", f"Co end={a.courant_end:g}",
                  f"Co peak={a.courant_max_seen:g}"],
        suggestion="Enable adjustTimeStep with a maxCo limit, or reduce deltaT.",
    )


@rule("log-field-clipping", CATEGORY_NUMERICS, WARNING,
      "A turbulence field was repeatedly clipped")
def log_bounding(ctx):
    a = _primary_log(ctx)
    if a is None or not a.bounding_counts:
        return None
    out = []
    for name, count in sorted(a.bounding_counts.items(), key=lambda kv: -kv[1]):
        if count > max(3, a.n_steps * 0.2):
            out.append(finding(
                f"'{name}' was clipped back to a positive value on {count} of {a.n_steps} steps.",
                files=list(ctx.log_texts),
                evidence=[f"bounding {name}: {count}/{a.n_steps} steps"],
                suggestion=f"Persistent bounding means {name} is going negative -- check its "
                           f"inlet value, its wall treatment, and the convection scheme.",
            ))
    return out


@rule("log-continuity-growth", CATEGORY_NUMERICS, WARNING,
      "Cumulative continuity error was growing")
def log_continuity(ctx):
    a = _primary_log(ctx)
    if a is None or not a.continuity_growing:
        return None
    return finding(
        "The cumulative continuity error grew steadily through the run.",
        files=list(ctx.log_texts),
        evidence=["cumulative continuity error increasing"],
        suggestion="Tighten the pressure solver tolerance, add nNonOrthogonalCorrectors, "
                   "or check for an over-constrained pressure setup.",
    )


@rule("mesh-quality", CATEGORY_MESH, WARNING, "checkMesh reports quality problems")
def mesh_quality(ctx):
    text = find_checkmesh_output(ctx.files)
    if not text:
        return None
    q = analyze_checkmesh(text)
    if not q.parsed or not q.problems:
        return None
    out = []
    for problem in q.problems:
        severity = CRITICAL if q.has_negative_volumes else WARNING
        out.append(finding(
            problem,
            files=["constant/polyMesh"],
            evidence=[q.summary()],
            severity=severity,
        ))
    return out


@rule("mesh-explains-solver", CATEGORY_MESH, CRITICAL,
      "Mesh quality explains the observed solver behaviour")
def mesh_explains_solver(ctx):
    """The cross-reference: two separate reports become one diagnosis."""
    text = find_checkmesh_output(ctx.files)
    if not text:
        return None
    q = analyze_checkmesh(text)
    a = _primary_log(ctx)
    if not q.parsed or a is None:
        return None
    notes = relate_to_solver(q, a)
    if not notes:
        return None
    return [finding(
        note,
        files=["constant/polyMesh"] + list(ctx.log_texts),
        evidence=[q.summary()],
        confidence=LIKELY,
    ) for note in notes]


@rule("no-checkmesh-output", CATEGORY_MESH, INFO,
      "Mesh quality cannot be assessed")
def no_checkmesh(ctx):
    """
    Be explicit about the limit rather than letting the AI guess. Mesh quality
    genuinely cannot be derived from the dictionaries alone.
    """
    if find_checkmesh_output(ctx.files):
        return None
    if not ctx.has_survey or not ctx.survey.get("has_mesh"):
        return None  # a separate rule already reports a missing mesh
    return finding(
        "No checkMesh output was supplied, so mesh quality is unknown.",
        files=["constant/polyMesh"],
        evidence=["no checkMesh report among the case files"],
        suggestion="Run 'checkMesh > log.checkMesh' in the case folder and attach the output "
                   "to settle any mesh-related question.",
        confidence=CONFIRMED,
    )
