"""
Backward-compatible skill helpers.

The real skill data now lives in the YAML files loaded by skill_defs.py. This
module keeps the old function names (list_skills, load_sop, SKILL_DESCRIPTIONS)
working so the legacy Streamlit app and any other callers don't break.
"""
from .skill_defs import list_skill_names, load_skill, render_procedure, skill_descriptions

# Kept for compatibility with older code that imported these names.
SKILL_DESCRIPTIONS = skill_descriptions()


def list_skills() -> list[str]:
    """Friendly names of all skills (for the GUI)."""
    return list_skill_names()


def load_sop(skill_name: str) -> str:
    """
    Return the skill's 10-step procedure rendered as text.

    (Previously this returned a hand-written .md SOP; now it is generated from the
    structured YAML definition so there is a single source of truth.)
    """
    return render_procedure(load_skill(skill_name))
