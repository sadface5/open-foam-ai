"""
The OpenFOAM rule engine: deterministic checks that run BEFORE the AI.

DESIGN
------
Each rule is a small function that receives a CaseContext and returns either
nothing (the rule does not apply, or the case is fine) or one or more findings.
Rules register themselves with the @rule decorator, so adding a new check means
writing one function -- no wiring, no central list to update:

    @rule("my-check", CATEGORY_NUMERICS, WARNING, "Short human title")
    def check_something(ctx):
        if ctx.delta_t and ctx.delta_t > 1:
            return finding("deltaT looks large", files=["system/controlDict"])

The AI's job then changes from "find the problems" to "explain and prioritise
the problems we already proved". That is both cheaper and far more reliable,
because a rule cannot hallucinate.

This engine never runs OpenFOAM and never writes files.

Relationship to the existing deterministic.py: that module still works exactly
as before and is still used. This engine is a superset that adds cross-file
reasoning; run_all_checks() below merges both so nothing is lost.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from .context import CaseContext

# --- Severities (same vocabulary the existing deterministic.py uses) ----------
CRITICAL = "critical"
WARNING = "warning"
INFO = "info"
OK = "ok"

_SEVERITY_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2, OK: 3}

# --- Categories ---------------------------------------------------------------
CATEGORY_MESH = "mesh"
CATEGORY_NUMERICS = "numerics"
CATEGORY_TURBULENCE = "turbulence"
CATEGORY_BC = "boundary-conditions"
CATEGORY_SOLVER = "solver"
CATEGORY_PARALLEL = "parallel"
CATEGORY_THERMO = "thermophysical"
CATEGORY_SYNTAX = "dictionary-syntax"

# --- Confidence -- how sure the rule is about what it found -------------------
CONFIRMED = "confirmed"   # read directly from the files; not a judgement call
LIKELY = "likely"         # strong indication, but context could excuse it
POSSIBLE = "possible"     # worth checking, commonly a problem

# Legacy checks in deterministic.py that a newer rule now covers more precisely.
# When the newer rule fires, the older duplicate is suppressed.
SUPERSEDED_LEGACY_CHECKS = {
    "turbulence-fields": "turbulence-fields-mismatch",
    "solver-entry": "missing-solver-entry",
    "relaxation-range": "relaxation-out-of-range",
    "steady-transient": "steady-transient-mismatch",
}


@dataclass
class RuleFinding:
    """One thing a rule noticed. Deliberately richer than the legacy Finding."""
    rule_id: str = ""
    category: str = ""
    severity: str = WARNING
    title: str = ""
    detail: str = ""
    files: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)   # concrete observed values
    suggestion: Optional[str] = None
    confidence: str = CONFIRMED

    def as_line(self) -> str:
        """One-line rendering for prompts and logs."""
        ev = f"  [evidence: {'; '.join(self.evidence)}]" if self.evidence else ""
        return f"({self.severity.upper()}/{self.confidence}) {self.title}: {self.detail}{ev}"

    # --- compatibility with the original deterministic.Finding -----------------
    # These let a RuleFinding be used anywhere the older Finding was expected
    # (notably diagnoser.format_findings), so no existing code has to change.
    @property
    def status(self) -> str:
        return self.severity

    @property
    def check(self) -> str:
        return self.rule_id or self.title


@dataclass
class Rule:
    id: str
    category: str
    severity: str
    title: str
    func: Callable[[CaseContext], object]


# The registry. Rule modules append to this simply by being imported.
REGISTRY: list[Rule] = []


def rule(rule_id: str, category: str, severity: str, title: str):
    """Decorator that registers a check function as a rule."""
    def decorator(func):
        REGISTRY.append(Rule(rule_id, category, severity, title, func))
        return func
    return decorator


def finding(detail: str, *, files=None, evidence=None, suggestion=None,
            confidence: str = CONFIRMED, severity: Optional[str] = None) -> RuleFinding:
    """
    Convenience builder used inside rule functions. The rule's own id, category,
    title and default severity are filled in by the engine afterwards, so a rule
    body only has to describe what it actually saw.
    """
    f = RuleFinding(
        detail=detail,
        files=list(files or []),
        evidence=list(evidence or []),
        suggestion=suggestion,
        confidence=confidence,
    )
    if severity:
        f.severity = severity
        f._severity_overridden = True  # type: ignore[attr-defined]
    return f


def _normalise(result: Union[None, RuleFinding, list], r: Rule) -> list[RuleFinding]:
    """Turn whatever a rule returned into a list of fully-populated findings."""
    if result is None:
        return []
    items = result if isinstance(result, list) else [result]
    out = []
    for item in items:
        if not isinstance(item, RuleFinding):
            continue
        item.rule_id = item.rule_id or r.id
        item.category = item.category or r.category
        item.title = item.title or r.title
        if not getattr(item, "_severity_overridden", False):
            item.severity = r.severity
        out.append(item)
    return out


def run_rules(ctx: CaseContext, categories: Optional[list[str]] = None) -> list[RuleFinding]:
    """
    Run every registered rule against the case, most severe first.

    A rule that raises is skipped rather than crashing the app -- a broken rule
    must never stop the user getting an answer. Restrict to certain categories
    with `categories`.
    """
    _load_rule_modules()
    results: list[RuleFinding] = []
    for r in REGISTRY:
        if categories and r.category not in categories:
            continue
        try:
            results.extend(_normalise(r.func(ctx), r))
        except Exception:  # noqa: BLE001 - a faulty rule must not break the run
            continue
    results.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.category, f.rule_id))
    return results


_LOADED = False


def _load_rule_modules() -> None:
    """Import the rule modules once, so their decorators populate REGISTRY."""
    global _LOADED
    if _LOADED:
        return
    from . import consistency, diagnostics, numerics  # noqa: F401  (side effects)
    _LOADED = True


def rule_count() -> int:
    _load_rule_modules()
    return len(REGISTRY)


def run_all_checks(files: dict[str, str], survey: dict | None = None,
                   case_path: str | None = None) -> list[RuleFinding]:
    """
    The convenient entry point: legacy deterministic checks PLUS the new rules,
    merged into one ranked list.

    Existing callers of deterministic.run_deterministic_checks() keep working
    untouched; this is for callers that want everything.
    """
    from ..deterministic import run_deterministic_checks

    ctx = CaseContext(files, survey=survey, case_path=case_path)
    out = run_rules(ctx)
    fired = {f.rule_id for f in out}

    # Fold in the original checks, converted to the richer shape. Where a new
    # rule already covers the same ground (with better evidence), the legacy
    # version is dropped so the user does not read the same problem twice.
    for legacy in run_deterministic_checks(files):
        if legacy.status == OK:
            continue
        if SUPERSEDED_LEGACY_CHECKS.get(legacy.check) in fired:
            continue
        out.append(RuleFinding(
            rule_id=f"legacy:{legacy.check}",
            category=CATEGORY_SYNTAX,
            severity=legacy.status,
            title=legacy.check,
            detail=legacy.detail,
            files=list(legacy.files),
            confidence=CONFIRMED,
        ))

    # Fold the evidence and suggestion into `detail`. The existing prompt builder
    # (diagnoser.format_findings) only reads `detail`, and the whole point of the
    # rule engine is that Claude explains findings we have already proved -- so
    # the proof has to travel with them.
    for f in out:
        extra = []
        if f.evidence:
            extra.append("observed: " + "; ".join(f.evidence))
        if f.confidence and f.confidence != CONFIRMED:
            extra.append(f"confidence: {f.confidence}")
        if f.suggestion:
            extra.append(f"usual fix: {f.suggestion}")
        if extra:
            f.detail = f"{f.detail} ({' | '.join(extra)})"

    out.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.category, f.rule_id))
    return out
