"""
Central configuration.

Everything that might change (model IDs, folder locations, the API key) lives
here, so the rest of the code stays simple and you only edit one place.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


# --- Where the app's files live -----------------------------------------------
def _base_dir() -> Path:
    """
    The project's base folder.

    - Running normally (from source): the project root (parent of this /src folder).
    - Running as a packaged .exe: the folder that CONTAINS the .exe, so you can
      edit /skills, /knowledge and .env right next to the app.
    """
    if getattr(sys, "frozen", False):  # True only inside a PyInstaller .exe
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
SKILLS_DIR = BASE_DIR / "skills"
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
BACKUPS_DIR = BASE_DIR / "backups"          # where original files are backed up
ENV_PATH = BASE_DIR / ".env"                # where the API key is stored

# Load the API key from .env (next to the app), then fall back to a normal search.
load_dotenv(ENV_PATH)
load_dotenv()

# --- Model choices (depth / cost tiers) ---------------------------------------
# The user picks one of these in the app. If Anthropic changes a model ID, you
# only need to edit the "id" values below. Verify current IDs at:
#   https://docs.anthropic.com/en/docs/about-claude/models
#
# Each tier has:
#   id         -> the exact Claude model name sent to the API
#   max_tokens -> how long the reply may be (covers thinking + answer)
#   thinking   -> whether to let Claude reason step-by-step before answering
#   blurb      -> plain-language description shown in the UI
MODELS = {
    "Fast": {
        "id": "claude-haiku-4-5-20251001",
        "max_tokens": 2048,
        "thinking": False,
        "blurb": "Quick error explanations. Cheapest and fastest.",
    },
    "Balanced": {
        "id": "claude-sonnet-5",
        "max_tokens": 6000,
        "thinking": True,
        "blurb": "Normal debugging and recommendations. A good default.",
    },
    "Deep": {
        "id": "claude-opus-4-8",
        "max_tokens": 12000,
        "thinking": True,
        "blurb": "Complex convergence, mesh, or solver issues.",
    },
    "Agentic": {
        "id": "claude-fable-5",
        "max_tokens": 16000,
        "thinking": True,
        "blurb": "Long, multi-step deep analysis (slowest, most thorough).",
    },
}
DEFAULT_MODEL_TIER = "Balanced"

# Legacy aliases (kept so the older Streamlit app.py keeps working unchanged).
MODEL = MODELS[DEFAULT_MODEL_TIER]["id"]
MAX_TOKENS = MODELS[DEFAULT_MODEL_TIER]["max_tokens"]

# --- Knowledge search (RAG) settings ------------------------------------------
TOP_K_SNIPPETS = 5


def get_api_key() -> str:
    """Return the Anthropic API key, or raise a clear, friendly error if missing."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or key.strip() == "" or key.startswith("sk-ant-your-real-key"):
        raise RuntimeError(
            "No Anthropic API key found.\n\n"
            "Fix: click the 'Settings' button and paste your key "
            "(it will be saved to the .env file next to the app), or put it in "
            ".env manually as:\n\n    ANTHROPIC_API_KEY=sk-ant-...\n"
        )
    return key


def save_api_key(key: str) -> None:
    """
    Write (or replace) ANTHROPIC_API_KEY in the .env file next to the app, and
    apply it immediately so the user doesn't have to restart.
    """
    key = key.strip()
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    out, found = [], False
    for line in lines:
        if line.strip().startswith("ANTHROPIC_API_KEY="):
            out.append(f"ANTHROPIC_API_KEY={key}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"ANTHROPIC_API_KEY={key}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ["ANTHROPIC_API_KEY"] = key
