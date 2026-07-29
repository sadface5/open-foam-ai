"""
Persistent memory for a debugging session (and across sessions).

Two jobs:

1. WITHIN a session -- remember what has already been tried so the loop never
   suggests the same failed fix twice, and so each iteration builds on the last
   instead of restarting from scratch.

2. ACROSS sessions -- once a case is solved, record what actually worked. A
   local database of solved cases makes future diagnoses better, because
   "kOmegaSST with a missing omega, fixed by adding 0/omega" is far more useful
   evidence than a general rule.

The database is a plain JSON file under the app's backups folder. It contains
only text the user already had (settings, rule ids, outcomes) -- no file
contents and nothing is ever sent anywhere by this module.
"""
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .config import BACKUPS_DIR

LEARNED_DB = Path(BACKUPS_DIR) / "solved_cases.json"
MAX_LEARNED_CASES = 500


@dataclass
class AttemptRecord:
    """One thing that was tried, and what came of it."""
    iteration: int = 0
    hypothesis_key: str = ""
    description: str = ""
    files_changed: list = field(default_factory=list)
    commands_run: list = field(default_factory=list)
    outcome: str = ""
    observation: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class DebugSession:
    """Everything the loop knows about the case it is currently working on."""
    case_path: str = ""
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    iterations: int = 0
    attempts: list = field(default_factory=list)          # AttemptRecord
    files_modified: list = field(default_factory=list)
    commands_executed: list = field(default_factory=list)
    successful_fixes: list = field(default_factory=list)
    failed_fixes: list = field(default_factory=list)
    refuted_hypotheses: list = field(default_factory=list)
    confirmed_hypotheses: list = field(default_factory=list)
    resolved: bool = False
    stop_reason: str = ""

    # --- recording ------------------------------------------------------------
    def record_attempt(self, attempt: AttemptRecord) -> None:
        self.attempts.append(attempt)
        for f in attempt.files_changed:
            if f not in self.files_modified:
                self.files_modified.append(f)
        for c in attempt.commands_run:
            self.commands_executed.append(c)
        if attempt.outcome == "success":
            self.successful_fixes.append(attempt.description)
            if attempt.hypothesis_key not in self.confirmed_hypotheses:
                self.confirmed_hypotheses.append(attempt.hypothesis_key)
        elif attempt.outcome == "failure":
            self.failed_fixes.append(attempt.description)
            if attempt.hypothesis_key not in self.refuted_hypotheses:
                self.refuted_hypotheses.append(attempt.hypothesis_key)

    # --- querying -------------------------------------------------------------
    def already_tried(self, hypothesis_key: str) -> bool:
        """True if this explanation has been tested and did not pan out."""
        return hypothesis_key in self.refuted_hypotheses

    def tried_descriptions(self) -> set:
        return {a.description for a in self.attempts}

    def remaining(self, hypotheses) -> list:
        """Hypotheses not yet refuted, still worth testing."""
        return [h for h in hypotheses if not self.already_tried(h.key)]

    def summary(self) -> str:
        if not self.attempts:
            return "Nothing has been tried yet in this session."
        lines = [f"{self.iterations} iteration(s), {len(self.attempts)} attempt(s):"]
        for a in self.attempts:
            lines.append(f"  {a.iteration}. {a.description} -> {a.outcome or 'pending'}"
                         + (f" ({a.observation})" if a.observation else ""))
        if self.files_modified:
            lines.append("Files changed: " + ", ".join(self.files_modified))
        if self.refuted_hypotheses:
            lines.append("Ruled out: " + ", ".join(self.refuted_hypotheses))
        if self.stop_reason:
            lines.append(f"Stopped because: {self.stop_reason}")
        return "\n".join(lines)

    def as_context(self) -> str:
        """
        Compact text for the AI prompt, so the model does not re-suggest a fix
        that has already failed.
        """
        if not self.attempts:
            return ""
        parts = ["Previously attempted in this session (do not repeat what failed):"]
        for a in self.attempts:
            parts.append(f"- {a.description}: {a.outcome or 'pending'}"
                         + (f" — {a.observation}" if a.observation else ""))
        if self.refuted_hypotheses:
            parts.append("Ruled out: " + ", ".join(self.refuted_hypotheses))
        return "\n".join(parts)


# --- the learned-case database ------------------------------------------------
def _load_db() -> list:
    if not LEARNED_DB.is_file():
        return []
    try:
        data = json.loads(LEARNED_DB.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_db(rows: list) -> None:
    try:
        LEARNED_DB.parent.mkdir(parents=True, exist_ok=True)
        LEARNED_DB.write_text(json.dumps(rows[-MAX_LEARNED_CASES:], indent=2), encoding="utf-8")
    except OSError:
        pass  # never let bookkeeping break a debugging session


def record_solved_case(session: DebugSession, *, solver: str = "", turbulence: str = "",
                       problem: str = "", fix: str = "", confidence: str = "") -> None:
    """
    Save a completed session so future diagnoses can learn from it.

    Only stores short descriptors, never file contents.
    """
    if not session.attempts:
        return
    rows = _load_db()
    rows.append({
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "solver": solver,
        "turbulence": turbulence,
        "problem": problem,
        "fix": fix or "; ".join(session.successful_fixes),
        "confirmed": session.confirmed_hypotheses,
        "refuted": session.refuted_hypotheses,
        "files_modified": session.files_modified,
        "commands": [" ".join(c) if isinstance(c, list) else str(c)
                     for c in session.commands_executed][:20],
        "iterations": session.iterations,
        "resolved": session.resolved,
        "confidence": confidence,
    })
    _save_db(rows)


def recall_similar(solver: str = "", turbulence: str = "", problem: str = "",
                   limit: int = 3) -> list:
    """
    Find previously-solved cases resembling this one.

    Deliberately simple keyword overlap -- it has to be explainable, and the
    database is small enough that cleverness would not pay for itself.
    """
    rows = _load_db()
    if not rows:
        return []
    wanted = {w for w in f"{solver} {turbulence} {problem}".lower().split() if len(w) > 2}
    if not wanted:
        return []

    scored = []
    for row in rows:
        if not row.get("resolved"):
            continue
        text = " ".join(str(row.get(k, "")) for k in
                        ("solver", "turbulence", "problem", "fix")).lower()
        words = {w for w in text.split() if len(w) > 2}
        overlap = len(wanted & words)
        if overlap:
            scored.append((overlap, row))
    scored.sort(key=lambda t: -t[0])
    return [row for _, row in scored[:limit]]


def format_recalled(rows: list) -> str:
    """Render recalled cases for the prompt."""
    if not rows:
        return ""
    lines = ["Similar cases solved previously on this machine:"]
    for r in rows:
        lines.append(f"- {r.get('problem') or 'issue'} "
                     f"({r.get('solver') or 'unknown solver'}): fixed by {r.get('fix')}")
    return "\n".join(lines)


def load_session(path: str) -> DebugSession:
    """Restore a saved session for a case, or start a fresh one."""
    store = Path(BACKUPS_DIR) / "sessions.json"
    try:
        data = json.loads(store.read_text(encoding="utf-8")) if store.is_file() else {}
        raw = data.get(str(path))
        if raw:
            attempts = [AttemptRecord(**a) for a in raw.pop("attempts", [])]
            session = DebugSession(**raw)
            session.attempts = attempts
            return session
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return DebugSession(case_path=str(path))


def save_session(session: DebugSession) -> None:
    """Persist a session so a restart does not lose what was already ruled out."""
    store = Path(BACKUPS_DIR) / "sessions.json"
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(store.read_text(encoding="utf-8")) if store.is_file() else {}
        if not isinstance(data, dict):
            data = {}
        payload = asdict(session)
        data[str(session.case_path)] = payload
        store.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError, TypeError):
        pass
