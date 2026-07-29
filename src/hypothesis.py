"""
Ranked root-cause hypotheses.

A list of findings is not a diagnosis. An engineer holds a small number of
competing EXPLANATIONS, each with a confidence, the evidence behind it, and a
way to test it -- and works the most likely one first.

This module turns rule-engine findings into that structure. Related findings are
grouped into one hypothesis (a missing field, a bad wall function and a stalled
residual may all be the same story), scored, and ordered so the caller always
knows what to try next.

Nothing here calls the AI or runs OpenFOAM; it is deterministic bookkeeping over
findings that have already been proved.
"""
import re
from dataclasses import dataclass, field

# Status of a hypothesis as the debugging session progresses.
OPEN = "open"              # not yet tested
TESTING = "testing"        # an experiment is in flight
CONFIRMED = "confirmed"    # evidence supports it and the fix helped
REFUTED = "refuted"        # tested and it was not the cause
INCONCLUSIVE = "inconclusive"

# Base confidence from a finding's severity and how sure the rule was.
_SEVERITY_SCORE = {"critical": 0.60, "warning": 0.35, "info": 0.15, "ok": 0.0}
_CONFIDENCE_MULTIPLIER = {"confirmed": 1.0, "likely": 0.8, "possible": 0.6}

# Findings that describe the same underlying problem are merged into one
# hypothesis. Each group is (hypothesis key, human title, matching rule ids).
GROUPS: list[tuple[str, str, tuple]] = [
    ("no-mesh", "The case has no usable mesh",
     ("no-mesh",)),
    # legacy:missing-file fires for ANY absent file (turbulenceProperties, 0/,
    # fvSchemes ...), so it must not be folded into the mesh hypothesis.
    ("missing-files", "Required case files are missing",
     ("legacy:missing-file",)),
    ("turbulence-setup", "The turbulence setup is incomplete or inconsistent",
     ("turbulence-fields-mismatch", "turbulence-leftover-fields",
      "legacy:turbulence-fields")),
    ("boundary-conditions", "The boundary conditions are inconsistent or over-constrained",
     ("wall-function-on-non-wall", "wall-missing-wall-function",
      "patch-type-disagreement", "no-pressure-reference",
      "legacy:patch-coverage", "legacy:patch-case", "legacy:patch-extra")),
    ("mesh-quality", "Mesh quality is driving the solver behaviour",
     ("mesh-quality", "mesh-explains-solver")),
    ("time-step", "The time step is too large for the mesh",
     ("transient-fixed-large-dt", "courant-too-high", "log-courant-growth")),
    ("numerical-stability", "The numerical settings are too aggressive to be stable",
     ("unbounded-div-scheme", "relaxation-aggressive", "relaxation-out-of-range",
      "steady-bounded", "log-diverging-residuals", "legacy:relaxation-range")),
    ("pressure-convergence", "The pressure equation is not converging",
     ("nonorthogonal-correctors-missing", "log-continuity-growth",
      "log-stalled-residuals")),
    ("solver-mismatch", "The solver and its configuration disagree",
     ("solver-algorithm-mismatch", "steady-transient-mismatch",
      "missing-solver-entry", "legacy:solver-entry", "legacy:steady-transient",
      "legacy:solvers-block")),
    ("parallel-setup", "The parallel decomposition is inconsistent",
     ("decomposition-mismatch", "decomposed-but-no-dict")),
    ("thermophysical", "The thermophysical setup does not match the solver",
     ("compressible-missing-thermo", "incompressible-with-thermo")),
    ("field-clipping", "A turbulence field is going negative and being clipped",
     ("log-field-clipping",)),
    ("dictionary-syntax", "A dictionary is malformed or incomplete",
     ("legacy:malformed-dict", "legacy:scheme-section")),
    ("run-failure", "The run terminated with an error",
     ("log-crash",)),
]

# How to test each hypothesis. These become the experiment's validation steps.
VALIDATION_STEPS = {
    "no-mesh": ["Run blockMesh (or snappyHexMesh) to generate the mesh.",
                "Run checkMesh and confirm it reports a valid mesh."],
    "missing-files": ["Restore or create the missing file(s).",
                      "Re-check the case and confirm the warning clears."],
    "turbulence-setup": ["Add the field(s) the turbulence model requires to 0/.",
                         "Re-run the solver for a few iterations and confirm it starts."],
    "boundary-conditions": ["Correct the boundary condition so velocity and pressure are not "
                            "both fixed on the same patch.",
                            "Re-run and check whether the first residuals are finite."],
    "mesh-quality": ["Run checkMesh to quantify non-orthogonality and skewness.",
                     "Raise nNonOrthogonalCorrectors to the recommended value and re-run."],
    "time-step": ["Enable adjustTimeStep with a maxCo limit, or reduce deltaT.",
                  "Re-run and confirm the Courant number stays bounded."],
    "numerical-stability": ["Switch convection to 'bounded Gauss upwind' and lower relaxation.",
                            "Re-run: if it becomes stable, the numerics were the cause."],
    "pressure-convergence": ["Add nNonOrthogonalCorrectors and tighten the p solver tolerance.",
                             "Re-run and check whether the continuity error stops growing."],
    "solver-mismatch": ["Make the algorithm block match the solver in controlDict.",
                        "Re-run and confirm the solver initialises."],
    "parallel-setup": ["Delete the stale processor* folders and re-run decomposePar.",
                       "Confirm the folder count matches numberOfSubdomains."],
    "thermophysical": ["Provide the properties file the solver expects.",
                       "Re-run and confirm the thermophysical model is constructed."],
    "field-clipping": ["Check the field's inlet value and wall treatment.",
                       "Re-run and confirm the bounding warnings stop."],
    "dictionary-syntax": ["Fix the malformed dictionary.",
                          "Run foamDictionary on the file to confirm OpenFOAM can parse it."],
    "run-failure": ["Read the end of the log for the first error.",
                    "Address that error and re-run."],
}


@dataclass
class Hypothesis:
    """One competing explanation for why the case is failing."""
    key: str
    title: str
    confidence: float = 0.0
    category: str = ""
    detail: str = ""
    evidence: list = field(default_factory=list)
    triggered_rules: list = field(default_factory=list)
    related_files: list = field(default_factory=list)
    validation_steps: list = field(default_factory=list)
    status: str = OPEN
    notes: list = field(default_factory=list)

    @property
    def confidence_label(self) -> str:
        if self.confidence >= 0.75:
            return "high"
        if self.confidence >= 0.45:
            return "medium"
        return "low"

    def as_line(self) -> str:
        return (f"[{self.confidence:.2f} {self.confidence_label}] {self.title} "
                f"({len(self.triggered_rules)} rule(s), status={self.status})")

    def summary(self) -> str:
        lines = [f"**{self.title}** — confidence {self.confidence_label} "
                 f"({self.confidence:.0%}), status: {self.status}"]
        if self.detail:
            lines.append(self.detail)
        if self.evidence:
            lines.append("Evidence: " + "; ".join(self.evidence[:4]))
        if self.related_files:
            lines.append("Files: " + ", ".join(sorted(set(self.related_files))[:5]))
        if self.validation_steps:
            lines.append("To test: " + " ".join(self.validation_steps))
        return "\n".join(lines)


def _score(finding) -> float:
    base = _SEVERITY_SCORE.get(getattr(finding, "severity", "warning"), 0.3)
    mult = _CONFIDENCE_MULTIPLIER.get(getattr(finding, "confidence", "confirmed"), 0.8)
    return base * mult


def _group_for(rule_id: str) -> tuple[str, str] | None:
    for key, title, members in GROUPS:
        if rule_id in members:
            return key, title
        # Allow prefix matching so new rules join the right group automatically.
        for m in members:
            if rule_id.startswith(m):
                return key, title
    return None


def _clean(text: str) -> str:
    """Strip the parenthetical evidence the rule engine appends to detail."""
    return re.sub(r"\s*\((observed|confidence|usual fix):.*\)\s*$", "", text or "").strip()


def build_hypotheses(findings) -> list[Hypothesis]:
    """
    Turn rule findings into ranked hypotheses, most likely first.

    Confidence combines the strongest finding in a group with a bonus for
    corroboration: several independent rules pointing at the same cause is more
    convincing than one, but the score never reaches certainty from rules alone.
    """
    buckets: dict[str, Hypothesis] = {}

    for f in findings or []:
        rule_id = getattr(f, "rule_id", "") or getattr(f, "check", "")
        group = _group_for(rule_id)
        if group is None:
            key = f"other:{rule_id}"
            title = (getattr(f, "title", "") or rule_id or "Unclassified finding")
        else:
            key, title = group

        h = buckets.get(key)
        if h is None:
            h = Hypothesis(key=key, title=title,
                           category=getattr(f, "category", "") or "",
                           validation_steps=list(VALIDATION_STEPS.get(key, [])))
            buckets[key] = h

        h.triggered_rules.append(rule_id)
        h.related_files.extend(getattr(f, "files", []) or [])
        for ev in (getattr(f, "evidence", []) or []):
            if ev not in h.evidence:
                h.evidence.append(ev)
        if not h.detail:
            h.detail = _clean(getattr(f, "detail", ""))

        score = _score(f)
        # Strongest single finding, plus a diminishing bonus for corroboration.
        h.confidence = max(h.confidence, score)

    result = []
    for h in buckets.values():
        extra = max(0, len(set(h.triggered_rules)) - 1)
        h.confidence = min(0.95, h.confidence + 0.05 * extra)
        h.related_files = sorted({p for p in h.related_files if p})
        h.triggered_rules = sorted(set(h.triggered_rules))
        if not h.validation_steps:
            h.validation_steps = ["Inspect the files involved and re-run to see whether the "
                                  "behaviour changes."]
        result.append(h)

    result.sort(key=lambda h: (-h.confidence, h.title))
    return result


def top_hypothesis(hypotheses: list[Hypothesis]) -> Hypothesis | None:
    """The highest-confidence hypothesis that is still worth testing."""
    for h in hypotheses:
        if h.status in (OPEN, TESTING):
            return h
    return None


def format_hypotheses(hypotheses: list[Hypothesis], limit: int = 5) -> str:
    """Markdown rendering for the chat and for prompts."""
    if not hypotheses:
        return "No ranked hypotheses were produced (no findings to work from)."
    lines = ["**Ranked causes, most likely first:**", ""]
    for i, h in enumerate(hypotheses[:limit], 1):
        lines.append(f"{i}. {h.summary()}")
        lines.append("")
    return "\n".join(lines).strip()
