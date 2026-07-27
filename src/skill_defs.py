"""
Loads the structured 10-step skill definitions from the YAML files in /skills.

Each skill is a required, ordered procedure (the "foundation"). The YAML keeps the
procedure as data so you can edit steps without touching Python.
"""
from dataclasses import dataclass, field

import yaml

from .config import SKILLS_DIR

# Friendly name (shown in the GUI)  ->  YAML file in /skills.
SKILL_FILES = {
    "Solver Divergence Debugger": "divergence_debugger.yaml",
    "Mesh Doctor": "mesh_doctor.yaml",
    "Boundary Condition Checker": "boundary_condition_checker.yaml",
    "Numerical Settings Optimizer": "numerical_settings_optimizer.yaml",
    "Solver & Turbulence Model Recommender": "solver_model_recommender.yaml",
}


@dataclass
class Step:
    number: int
    title: str
    purpose: str = ""
    required_files: list = field(default_factory=list)
    optional_files: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    evidence_to_extract: list = field(default_factory=list)
    severity_rules: str = ""
    confidence_rules: str = ""
    missing_information_behavior: str = ""
    allowed_recommendation_categories: list = field(default_factory=list)


@dataclass
class SkillDef:
    id: str
    name: str
    purpose: str = ""
    description: str = ""
    relevant_files: list = field(default_factory=list)
    steps: list = field(default_factory=list)  # list[Step]
    post_checklist_reasoning: str = ""


def list_skill_names() -> list[str]:
    return list(SKILL_FILES.keys())


# Simple cache so we only read + parse each YAML once.
_CACHE: dict[str, SkillDef] = {}


def load_skill(name: str) -> SkillDef:
    if name in _CACHE:
        return _CACHE[name]
    if name not in SKILL_FILES:
        raise ValueError(f"Unknown skill: {name}")
    path = SKILLS_DIR / SKILL_FILES[name]
    if not path.exists():
        raise FileNotFoundError(f"Skill definition not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = [Step(**s) for s in data.get("steps", [])]
    skill = SkillDef(
        id=data["id"],
        name=data["name"],
        purpose=data.get("purpose", ""),
        description=data.get("description", ""),
        relevant_files=data.get("relevant_files", []),
        steps=steps,
        post_checklist_reasoning=data.get("post_checklist_reasoning", ""),
    )
    _CACHE[name] = skill
    return skill


def skill_descriptions() -> dict[str, str]:
    """Short one-line descriptions for the GUI, read from the YAML files."""
    out = {}
    for name in SKILL_FILES:
        try:
            out[name] = load_skill(name).description
        except Exception:
            out[name] = ""
    return out


def render_procedure(skill: SkillDef) -> str:
    """Render the 10-step procedure as Markdown, for inclusion in the prompt."""
    lines = [f"# Skill: {skill.name}", f"Purpose: {skill.purpose}", ""]
    lines.append("## Required 10-step procedure — perform these IN ORDER, first")
    for s in skill.steps:
        lines.append(f"### Step {s.number}: {s.title}")
        if s.purpose:
            lines.append(f"- Purpose: {s.purpose}")
        if s.checks:
            lines.append("- Checks to perform:")
            for c in s.checks:
                lines.append(f"    - {c}")
        if s.evidence_to_extract:
            lines.append("- Evidence to extract: " + "; ".join(s.evidence_to_extract))
        if s.severity_rules:
            lines.append(f"- Severity rule: {s.severity_rules}")
        if s.confidence_rules:
            lines.append(f"- Confidence rule: {s.confidence_rules}")
        if s.missing_information_behavior:
            lines.append(f"- If evidence is missing: {s.missing_information_behavior}")
        if s.allowed_recommendation_categories:
            lines.append("- Allowed recommendation categories: " + ", ".join(s.allowed_recommendation_categories))
        lines.append("")
    if skill.post_checklist_reasoning:
        lines.append("## After the 10 steps — REQUIRED broader reasoning")
        lines.append(skill.post_checklist_reasoning.strip())
    return "\n".join(lines)
