"""
Advanced solver-log analysis.

Keyword matching ("does the log contain nan?") answers the easy question. The
useful questions are about TRENDS:

    Were residuals falling, flat, or climbing before it died?
    Which field went bad FIRST -- that is usually the culprit, not the one that
      finally produced the nan.
    Was the Courant number creeping up, meaning the time step was too large?
    Did continuity error accumulate, meaning the pressure solve never really
      converged?
    Was a turbulence field being clipped ("bounding k") every step?

This module reconstructs the run's history from the log text and reports those
patterns, so the AI reasons about evidence rather than guessing from a snippet.

It parses text only -- it never runs OpenFOAM.
"""
import re
from dataclasses import dataclass, field

# --- Line patterns ------------------------------------------------------------
RE_TIME = re.compile(r"^Time = ([\d.eE+-]+)\s*$", re.MULTILINE)
RE_SOLVING = re.compile(
    r"Solving for (\w+), Initial residual = ([\d.eE+-]+), "
    r"Final residual = ([\d.eE+-]+), No Iterations (\d+)"
)
RE_COURANT = re.compile(r"Courant Number mean: ([\d.eE+-]+) max: ([\d.eE+-]+)")
RE_CONTINUITY = re.compile(r"cumulative = ([\d.eE+-]+)")
RE_BOUNDING = re.compile(r"bounding (\w+), min: ([\d.eE+-]+)")
RE_EXEC = re.compile(r"ExecutionTime = ([\d.eE+-]+) s")

# Failure signatures, checked in order -- the first match wins.
CRASH_SIGNATURES = [
    (re.compile(r"Foam::sigFpe|Floating point exception|SIGFPE"), "floating-point exception (divide by zero, or a nan/inf appeared)"),
    (re.compile(r"Maximum number of iterations exceeded"), "a linear solver hit its iteration limit"),
    (re.compile(r"Foam::sigSegv|Segmentation fault|SIGSEGV"), "segmentation fault (often a corrupt mesh or a bad dictionary entry)"),
    (re.compile(r"--> FOAM FATAL IO ERROR"), "a dictionary could not be read (FOAM FATAL IO ERROR)"),
    (re.compile(r"--> FOAM FATAL ERROR"), "OpenFOAM raised a fatal error"),
    (re.compile(r"MPI_ABORT|mpirun noticed that|MPI_ERR"), "an MPI/parallel communication failure"),
    (re.compile(r"std::bad_alloc|Out of memory|Killed"), "the run ran out of memory"),
]

NAN_PATTERN = re.compile(r"\b(nan|-nan|inf|-inf)\b", re.IGNORECASE)


def _f(text: str):
    """Float, tolerating 'nan'/'inf' and malformed numbers."""
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


@dataclass
class TimeStep:
    """One 'Time = ...' block from the log."""
    time: float
    residuals: dict = field(default_factory=dict)   # field -> initial residual
    courant_max: float | None = None
    continuity_cumulative: float | None = None
    bounded: list = field(default_factory=list)     # fields that were clipped


@dataclass
class LogAnalysis:
    """What the log tells us about how the run behaved."""
    parsed: bool = False
    n_steps: int = 0
    first_time: float | None = None
    last_time: float | None = None
    completed: bool = False              # reached 'End'
    converged: bool = False              # SIMPLE reported convergence
    crashed: bool = False
    crash_reason: str | None = None
    saw_nan: bool = False
    first_diverging_field: str | None = None
    residual_trend: dict = field(default_factory=dict)   # field -> improving|flat|diverging
    courant_start: float | None = None
    courant_end: float | None = None
    courant_max_seen: float | None = None
    continuity_growing: bool = False
    bounding_counts: dict = field(default_factory=dict)  # field -> how many steps clipped
    fields_seen: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        """Plain-English summary, suitable for handing to the AI or the user."""
        if not self.parsed:
            return "The log could not be parsed as OpenFOAM solver output."
        bits = []
        if self.completed:
            bits.append(f"The run completed normally after {self.n_steps} time step(s)")
        elif self.crashed:
            bits.append(f"The run stopped after {self.n_steps} time step(s): {self.crash_reason}")
        else:
            bits.append(f"The log ends after {self.n_steps} time step(s) without reaching 'End'")
        if self.last_time is not None:
            bits[-1] += f" (last Time = {self.last_time})"
        if self.converged:
            bits.append("the solver reported convergence")
        if self.first_diverging_field:
            bits.append(f"residuals for '{self.first_diverging_field}' started rising first")
        diverging = [k for k, v in self.residual_trend.items() if v == "diverging"]
        if diverging:
            bits.append(f"rising residuals for: {', '.join(diverging)}")
        stalled = [k for k, v in self.residual_trend.items() if v == "flat"]
        if stalled and not diverging:
            bits.append(f"residuals flat (stalled) for: {', '.join(stalled)}")
        if self.courant_start is not None and self.courant_end is not None:
            bits.append(f"max Courant went from {self.courant_start:g} to {self.courant_end:g}")
        if self.continuity_growing:
            bits.append("cumulative continuity error was growing")
        if self.bounding_counts:
            worst = max(self.bounding_counts.items(), key=lambda kv: kv[1])
            bits.append(f"'{worst[0]}' was clipped on {worst[1]} step(s)")
        if self.saw_nan:
            bits.append("nan/inf appeared in the output")
        return "; ".join(bits) + "."


def _trend(values: list[float]) -> str:
    """
    Classify a residual history as improving, flat, or diverging.

    Compares the first and last thirds so a single noisy step does not decide it.
    """
    clean = [v for v in values if v is not None and v == v and v not in (float("inf"),)]
    if len(clean) < 4:
        return "unknown"
    third = max(1, len(clean) // 3)
    start = sum(clean[:third]) / third
    end = sum(clean[-third:]) / third
    if start <= 0:
        return "unknown"
    ratio = end / start
    if ratio > 2.0:
        return "diverging"
    if ratio < 0.5:
        return "improving"
    return "flat"


def parse_steps(text: str) -> list[TimeStep]:
    """Rebuild the per-time-step history from the log."""
    steps: list[TimeStep] = []
    marks = list(RE_TIME.finditer(text))
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        block = text[start:end]
        t = _f(m.group(1))
        if t is None:
            continue
        step = TimeStep(time=t)
        for sm in RE_SOLVING.finditer(block):
            name, initial = sm.group(1), _f(sm.group(2))
            # Keep the FIRST residual per field per step (Ux before UxFinal etc.)
            if name not in step.residuals and initial is not None:
                step.residuals[name] = initial
        cm = RE_COURANT.search(block)
        if cm:
            step.courant_max = _f(cm.group(2))
        contm = RE_CONTINUITY.search(block)
        if contm:
            step.continuity_cumulative = _f(contm.group(1))
        step.bounded = [bm.group(1) for bm in RE_BOUNDING.finditer(block)]
        steps.append(step)
    return steps


def analyze_log(text: str) -> LogAnalysis:
    """Analyse one solver log. Never raises -- unparseable input yields parsed=False."""
    result = LogAnalysis()
    if not text or not text.strip():
        return result

    steps = parse_steps(text)
    result.saw_nan = bool(NAN_PATTERN.search(text))
    result.completed = bool(re.search(r"^End\s*$", text, re.MULTILINE))
    result.converged = "solution converged" in text

    for pattern, reason in CRASH_SIGNATURES:
        if pattern.search(text):
            result.crashed, result.crash_reason = True, reason
            break
    if result.saw_nan and not result.crashed:
        result.crashed = True
        result.crash_reason = "the solution went to nan/inf"

    if not steps:
        # Still useful: a case can fail before the first time step (bad dict, no mesh).
        result.parsed = bool(result.crashed or result.completed)
        if result.crashed:
            result.notes.append("The run failed before completing any time step, "
                                "so the problem is in the setup rather than in the solution.")
        return result

    result.parsed = True
    result.n_steps = len(steps)
    result.first_time = steps[0].time
    result.last_time = steps[-1].time

    # Residual trend per field.
    histories: dict[str, list[float]] = {}
    for s in steps:
        for name, value in s.residuals.items():
            histories.setdefault(name, []).append(value)
    result.fields_seen = sorted(histories)
    for name, values in histories.items():
        result.residual_trend[name] = _trend(values)

    # Which field turned bad first? Find the earliest step where a field's
    # residual rose an order of magnitude above its own running minimum.
    earliest_step, earliest_field = None, None
    for name, values in histories.items():
        best = None
        for idx, v in enumerate(values):
            if v is None or v != v:
                continue
            if best is None or v < best:
                best = v
            elif best > 0 and v > best * 10:
                if earliest_step is None or idx < earliest_step:
                    earliest_step, earliest_field = idx, name
                break
    result.first_diverging_field = earliest_field

    # Courant evolution.
    courants = [s.courant_max for s in steps if s.courant_max is not None]
    if courants:
        result.courant_start = courants[0]
        result.courant_end = courants[-1]
        result.courant_max_seen = max(courants)

    # Continuity error growth.
    conts = [abs(s.continuity_cumulative) for s in steps
             if s.continuity_cumulative is not None and s.continuity_cumulative == s.continuity_cumulative]
    if len(conts) >= 4:
        third = max(1, len(conts) // 3)
        start = sum(conts[:third]) / third
        end = sum(conts[-third:]) / third
        result.continuity_growing = end > max(start * 10, 1e-12)

    # Bounding (clipping) frequency.
    for s in steps:
        for name in s.bounded:
            result.bounding_counts[name] = result.bounding_counts.get(name, 0) + 1

    _add_interpretation(result)
    return result


def _add_interpretation(r: LogAnalysis) -> None:
    """Turn the measured patterns into engineering hypotheses."""
    if r.courant_start and r.courant_end and r.courant_end > r.courant_start * 3 and r.courant_end > 1:
        r.notes.append(
            f"The Courant number climbed from {r.courant_start:g} to {r.courant_end:g}. "
            "That usually means the time step is too large for the local cell size; "
            "enable adjustTimeStep with maxCo, or reduce deltaT."
        )
    if r.continuity_growing:
        r.notes.append(
            "Cumulative continuity error grew steadily, which points at the pressure "
            "equation not converging: tighten the p solver tolerance, add "
            "nNonOrthogonalCorrectors, or check for an over-constrained pressure setup."
        )
    for name, count in r.bounding_counts.items():
        if count > max(3, r.n_steps * 0.2):
            r.notes.append(
                f"'{name}' was clipped on {count} of {r.n_steps} steps. Persistent bounding "
                f"means the turbulence field is going negative -- inspect its inlet value "
                f"and wall treatment."
            )
    if r.first_diverging_field:
        r.notes.append(
            f"'{r.first_diverging_field}' was the first field whose residual rose sharply, so "
            "it is a better starting point than whichever field finally produced the nan."
        )
    stalled = [k for k, v in r.residual_trend.items() if v == "flat"]
    if stalled and not r.crashed and not r.converged:
        r.notes.append(
            f"Residuals for {', '.join(stalled)} stopped improving without converging. "
            "That is typically under-relaxation set too low, a mesh quality limit, or a "
            "boundary condition that cannot settle."
        )


def analyze_logs(files: dict[str, str]) -> dict[str, LogAnalysis]:
    """Analyse every log-looking entry in a case-files mapping."""
    out = {}
    for rel, text in (files or {}).items():
        if rel.startswith("log") or rel.endswith(".log") or rel == "nohup.out":
            analysis = analyze_log(text)
            if analysis.parsed:
                out[rel] = analysis
    return out
