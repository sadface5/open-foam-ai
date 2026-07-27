"""
Intent classification and automatic skill selection.

Given the user's latest message (plus whether a case is loaded and whether there
is a previous diagnosis), this decides:
  - what the user is actually asking for (the "intent"),
  - which internal skills to run (can be several),
  - whether a FRESH internal diagnosis is needed, or we can reuse the previous one,
  - how detailed the answer should be, and a short "focus" topic.

This is deliberately rule-based (no API call): it is fast, free, and easy to test.
The heavy lifting (the actual diagnosis and the natural reply) is done by Claude.
"""
import re
from dataclasses import dataclass, field

# The five skills, by their friendly names (must match skill_defs.SKILL_FILES).
DIVERGENCE = "Solver Divergence Debugger"
MESH = "Mesh Doctor"
BC = "Boundary Condition Checker"
NUM = "Numerical Settings Optimizer"
TURB = "Solver & Turbulence Model Recommender"
ALL_SKILLS = [DIVERGENCE, MESH, BC, NUM, TURB]


@dataclass
class Intent:
    name: str                       # e.g. "why_failing", "targeted_inspect", ...
    skills: list = field(default_factory=list)
    needs_diagnosis: bool = True    # run a fresh internal diagnosis?
    detail_level: str = "normal"    # brief | normal | detailed | simpler
    focus: str = ""                 # short topic, e.g. "outlet boundary condition"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "skills": self.skills,
            "needs_diagnosis": self.needs_diagnosis,
            "detail_level": self.detail_level,
            "focus": self.focus,
        }


# --- keyword groups (all matched case-insensitively) -------------------------
_DIVERGENCE_KW = ["diverg", "blow up", "blew up", "blowing up", "crash", "nan", "inf ",
                  "floating point", "not converg", "isn't converg", "unstable", "instab",
                  "explod", "residual", "courant", "co number", "stuck", "oscillat", "fail"]
_MESH_KW = ["mesh", "checkmesh", "skew", "non-orthogon", "nonorthogon", "blockmesh",
            "snappy", "cell", "aspect ratio", "grading", "polymesh", "boundary layer"]
_BC_KW = ["boundary condition", " bc ", "outlet", "inlet", "wall", "patch", "0/u", "0/p",
          "fixedvalue", "zerogradient", "noslip", "symmetry", "freestream", "far field"]
_NUM_KW = ["fvschemes", "fvsolution", "scheme", "relaxation", "tolerance", "solver setting",
           "numeric", "convergence", "discretiz", "gauss", "upwind", "linear solver",
           "deltat", "time step", "timestep", "correctors", "gamg"]
_TURB_KW = ["turbulen", "komega", "k-omega", "kepsilon", "k-epsilon", "sst", "les", "rans",
            "ras ", "spalart", "nut", "y+", "yplus", "wall function", "reynolds"]
_PRESSURE_VELOCITY_KW = ["pressure", " p ", "velocity", " u ", "flow"]

_FOLLOWUP_REFS = ["that", "it", "this", "you said", "you mentioned", "earlier", "previous",
                  "more on", "elaborate", "expand", "tell me more", "go on", "what about",
                  "and the", "the above", "your answer"]

_FOCUS_WORDS = {
    "outlet boundary condition": ["outlet"],
    "inlet boundary condition": ["inlet"],
    "wall boundary condition": [" wall", "no-slip", "noslip"],
    "pressure": ["pressure", "0/p", " p "],
    "velocity": ["velocity", "0/u", " u "],
    "the mesh": ["mesh", "checkmesh", "skew", "non-orthogon"],
    "turbulence model": ["turbulen", "komega", "kepsilon", "sst"],
    "numerical schemes": ["fvschemes", "scheme", "relaxation", "fvsolution"],
    "the time step": ["deltat", "time step", "timestep", "courant"],
}


_STOPWORDS = {"the", "a", "an", "is", "my", "why", "of", "to", "in", "on", "and", "for",
              "it", "this", "that", "with", "do", "does", "i", "me", "am", "are", "was"}


def _has(text: str, keywords) -> bool:
    return any(k in text for k in keywords)


def _keywords_of(text: str) -> set:
    # Split on non-alphanumerics so "diverging?" matches "diverging".
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _similar(a: str, b: str) -> bool:
    """Rough Jaccard similarity on content words — used to spot a re-asked question."""
    ka, kb = _keywords_of(a), _keywords_of(b)
    if not ka or not kb:
        return False
    overlap = len(ka & kb) / len(ka | kb)
    return overlap >= 0.5


def _focus_of(text: str) -> str:
    for label, kws in _FOCUS_WORDS.items():
        if _has(text, kws):
            return label
    return ""


def select_skills(text: str, whole_case: bool = False) -> list:
    """Pick the internal skills relevant to the message (order = priority)."""
    if whole_case:
        return list(ALL_SKILLS)
    skills = []
    if _has(text, _DIVERGENCE_KW):
        skills.append(DIVERGENCE)
    if _has(text, _MESH_KW):
        skills.append(MESH)
    if _has(text, _BC_KW):
        skills.append(BC)
    if _has(text, _NUM_KW):
        skills.append(NUM)
    if _has(text, _TURB_KW):
        skills.append(TURB)
    # Divergence problems usually need BC + numerical cross-checks too.
    if DIVERGENCE in skills or _has(text, _PRESSURE_VELOCITY_KW):
        for s in (BC, NUM):
            if s not in skills:
                skills.append(s)
    if not skills:
        skills = [DIVERGENCE, BC, NUM]  # a sensible default for "why is it failing"
    return skills[:4]


def classify_intent(message: str, has_case: bool = False, has_prior: bool = False,
                    prior_questions=None) -> Intent:
    """Classify the user's message into an Intent (see the module docstring).

    prior_questions: earlier user messages in this conversation. If the new message
    closely repeats one of them, we treat it as a follow-up and reuse the previous
    analysis instead of re-running a full diagnosis.
    """
    text = f" {message.lower().strip()} "
    words = message.split()

    # 1) Ask for a simpler explanation of the previous answer.
    if _has(text, ["simpl", "explain that", "in plain", "eli5", "easier", "layman",
                   "dumb it down", "more clearly", "rephrase", "less technical"]):
        return Intent("simpler_explanation", skills=[], needs_diagnosis=False, detail_level="simpler")

    # 2) "What should I change first?"
    if _has(text, ["change first", "what should i change", "what to change", "first change",
                   "highest priority", "most important fix", "start with", "prioriti"]):
        return Intent("what_to_change_first", skills=select_skills(text),
                      needs_diagnosis=not has_prior, detail_level="brief", focus=_focus_of(text))

    # 3) Ask the assistant to actually make/propose an edit.
    if _has(text, ["propose an edit", "propose a change", "make the edit", "make the change",
                   "apply the change", "edit the file", "edit my", "write the change",
                   "fix it for me", "implement", "update the file", "change the file"]):
        return Intent("propose_edit", skills=select_skills(text), needs_diagnosis=False,
                      focus=_focus_of(text))

    # 4) Is a (proposed) change safe?
    if _has(text, ["is it safe", "safe to change", "safe to set", "will it break", "will this break",
                   "any risk", "is this risky", "break the case", "safe to use"]):
        return Intent("is_change_safe", skills=select_skills(text), needs_diagnosis=False,
                      detail_level="brief", focus=_focus_of(text))

    # 5) Compare two options.
    if _has(text, ["compare", "versus", " vs ", "difference between", "which is better",
                   "better choice", "tradeoff between", "trade-off between"]):
        return Intent("compare_options", skills=select_skills(text), needs_diagnosis=False,
                      focus=_focus_of(text))

    # 6) Solver / turbulence-model recommendation.
    if _has(text, ["recommend", "which solver", "which turbulence", "what turbulence",
                   "what solver", "which model", "what model", "suggest a", "should i use",
                   "best solver", "best model", "choose a solver", "pick a solver"]):
        return Intent("recommendation", skills=[TURB, BC], needs_diagnosis=has_case,
                      detail_level="normal", focus="solver / turbulence model")

    # 7) Whole-case audit.
    if _has(text, ["whole case", "entire case", "full audit", "audit the", "review the case",
                   "check the case", "check everything", "look at the whole", "complete review",
                   "inspect the case", "review everything", "go through the case", "check my case",
                   "check the whole"]):
        return Intent("whole_case_audit", skills=list(ALL_SKILLS), needs_diagnosis=True,
                      detail_level="detailed", focus="")

    # 7b) The user is re-asking something already explained -> reuse the prior analysis.
    if has_prior and prior_questions and any(_similar(message, q) for q in prior_questions):
        return Intent("follow_up", skills=[], needs_diagnosis=False, detail_level="normal",
                      focus=_focus_of(text))

    # 8) Follow-up about the previous answer (short, refers back, no fresh diagnostic nouns).
    if has_prior and len(words) <= 14 and _has(text, _FOLLOWUP_REFS) \
            and not _has(text, _DIVERGENCE_KW + _MESH_KW):
        return Intent("follow_up", skills=[], needs_diagnosis=False, detail_level="normal",
                      focus=_focus_of(text))

    # 9) General concept question (no strong tie to "my case").
    concept = _has(text, ["what is", "what does", "what's the", "how do i", "how does",
                          "explain ", "definition of", "meaning of", "when should i use"])
    case_ref = _has(text, ["my case", "this case", "the case", "my simulation", "my run", "my setup"])
    if concept and not case_ref and not has_case:
        return Intent("general_question", skills=[], needs_diagnosis=False, detail_level="normal",
                      focus=_focus_of(text))

    # 10) Targeted inspection of one file/patch/setting (but a failure symptom ->
    #     that is "why_failing", handled below, not a calm targeted inspection).
    focus = _focus_of(text)
    if focus and not _has(text, _DIVERGENCE_KW) and _has(text, ["wrong", "correct", "right",
                             "look at", "inspect", "check my", "is my", "review my",
                             "what's wrong with", "problem with"]):
        return Intent("targeted_inspect", skills=select_skills(text),
                      needs_diagnosis=has_case, detail_level="normal", focus=focus)

    # 11) "Why is it failing / diverging?"
    if _has(text, _DIVERGENCE_KW) or case_ref:
        return Intent("why_failing", skills=select_skills(text),
                      needs_diagnosis=has_case, detail_level="normal", focus=focus)

    # 12) Fallback: general question if no case; otherwise a light case look.
    if has_case:
        return Intent("why_failing", skills=select_skills(text), needs_diagnosis=True,
                      detail_level="normal", focus=focus)
    return Intent("general_question", skills=[], needs_diagnosis=False, detail_level="normal",
                  focus=focus)
