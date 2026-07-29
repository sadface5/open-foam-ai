"""
Versioned, explained file modifications.

The existing FileEditor already does the important safety work: it refuses to
touch anything outside the case, backs a file up before writing, and can undo
the last change. What it does not record is WHY a change was made, how confident
we were, or what evidence justified it -- and it can only step back one change.

This module wraps FileEditor (it does not replace or modify it) and adds:

    * a full version history per file, not just the most recent change
    * rollback to ANY earlier version, not only the last one
    * the reason, the confidence, and the rule ids / findings behind each edit
    * which hypothesis and which experiment iteration produced it
    * a plain-English audit trail the user can read

Every write still goes through FileEditor, so the original guarantees hold. If
this module is never used, nothing about the app changes.
"""
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import BACKUPS_DIR
from .file_editor import FileEditor

HISTORY_FILE = Path(BACKUPS_DIR) / "edit_history.json"
MAX_VERSIONS_PER_FILE = 50


@dataclass
class EditRecord:
    """One explained, reversible change to one file."""
    version: int = 0
    file_path: str = ""                 # relative to the case root
    case_path: str = ""
    reason: str = ""
    confidence: str = ""                # high | medium | low
    evidence: list = field(default_factory=list)      # concrete observed values
    triggered_rules: list = field(default_factory=list)
    hypothesis_key: str = ""
    iteration: int = 0
    backup_abs: Optional[str] = None
    was_new: bool = False
    applied_by: str = "user"            # "user" | "autonomous-loop"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    rolled_back: bool = False

    def summary(self) -> str:
        who = "the assistant" if self.applied_by == "autonomous-loop" else "you"
        bits = [f"v{self.version} — {self.file_path} (applied by {who}, {self.timestamp})"]
        if self.reason:
            bits.append(f"  reason: {self.reason}")
        if self.confidence:
            bits.append(f"  confidence: {self.confidence}")
        if self.evidence:
            bits.append("  evidence: " + "; ".join(self.evidence[:3]))
        if self.triggered_rules:
            bits.append("  triggered by: " + ", ".join(self.triggered_rules[:4]))
        if self.rolled_back:
            bits.append("  (rolled back)")
        return "\n".join(bits)


class VersionedEditor:
    """
    FileEditor plus history. Use exactly like FileEditor, with extra context.

        editor = VersionedEditor(case_root)
        editor.apply(proposal, reason="...", confidence="high",
                     evidence=[...], triggered_rules=[...])
        editor.rollback_to(file_path, version=2)
    """

    def __init__(self, case_root: str) -> None:
        self.case_root = str(Path(case_root).resolve())
        self.editor = FileEditor(case_root)
        self.history: list[EditRecord] = self._load()

    # --- persistence ----------------------------------------------------------
    def _load(self) -> list[EditRecord]:
        if not HISTORY_FILE.is_file():
            return []
        try:
            rows = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            return [EditRecord(**r) for r in rows if isinstance(r, dict)]
        except (OSError, json.JSONDecodeError, TypeError):
            return []

    def _save(self) -> None:
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(
                json.dumps([asdict(r) for r in self.history], indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # bookkeeping must never break an edit

    # --- querying -------------------------------------------------------------
    def versions_of(self, file_path: str) -> list[EditRecord]:
        """Every recorded change to one file in this case, oldest first."""
        return [r for r in self.history
                if r.file_path == file_path and r.case_path == self.case_root]

    def next_version(self, file_path: str) -> int:
        existing = self.versions_of(file_path)
        return (max(r.version for r in existing) + 1) if existing else 1

    def for_case(self) -> list[EditRecord]:
        return [r for r in self.history if r.case_path == self.case_root]

    def can_undo(self) -> bool:
        return self.editor.can_undo()

    # --- writing --------------------------------------------------------------
    def make_diff(self, rel_path: str, new_content: str) -> str:
        return self.editor.make_diff(rel_path, new_content)

    def current_content(self, rel_path: str) -> Optional[str]:
        return self.editor.current_content(rel_path)

    def apply(self, proposal, *, reason: str = "", confidence: str = "",
              evidence: Optional[list] = None, triggered_rules: Optional[list] = None,
              hypothesis_key: str = "", iteration: int = 0,
              applied_by: str = "user") -> EditRecord:
        """
        Apply an edit and record why.

        The write itself is delegated to FileEditor, so the path check, the
        timestamped backup and the undo log all still happen exactly as before.
        """
        applied = self.editor.apply(proposal)

        record = EditRecord(
            version=self.next_version(proposal.file_path),
            file_path=proposal.file_path,
            case_path=self.case_root,
            reason=reason or getattr(proposal, "reason", ""),
            confidence=confidence,
            evidence=list(evidence or []),
            triggered_rules=list(triggered_rules or []),
            hypothesis_key=hypothesis_key,
            iteration=iteration,
            backup_abs=applied.backup_abs,
            was_new=applied.was_new,
            applied_by=applied_by,
        )
        self.history.append(record)
        self._trim(proposal.file_path)
        self._save()
        return record

    def _trim(self, file_path: str) -> None:
        """Keep the history bounded without losing the most recent versions."""
        rows = self.versions_of(file_path)
        if len(rows) <= MAX_VERSIONS_PER_FILE:
            return
        drop = set(id(r) for r in rows[:-MAX_VERSIONS_PER_FILE])
        self.history = [r for r in self.history if id(r) not in drop]

    # --- reverting ------------------------------------------------------------
    def undo_last(self):
        """Undo the most recent change (delegates to FileEditor)."""
        undone = self.editor.undo_last()
        if undone is not None:
            for record in reversed(self.history):
                if (record.file_path == undone.file_path
                        and record.case_path == self.case_root
                        and not record.rolled_back):
                    record.rolled_back = True
                    break
            self._save()
        return undone

    def rollback_to(self, file_path: str, version: int) -> bool:
        """
        Restore a file to the state it had BEFORE the given version was applied.

        Returns False when that version is unknown or its backup is missing --
        for instance if the backups folder was cleared.
        """
        import shutil

        target_record = next(
            (r for r in self.versions_of(file_path) if r.version == version), None
        )
        if target_record is None:
            return False

        target = self.editor.resolve(file_path)
        if target_record.was_new:
            # That version created the file; undoing it means removing the file.
            if target.is_file():
                target.unlink()
        else:
            if not target_record.backup_abs or not Path(target_record.backup_abs).is_file():
                return False
            shutil.copy2(target_record.backup_abs, target)

        # Everything from that version onward is no longer in effect.
        for r in self.versions_of(file_path):
            if r.version >= version:
                r.rolled_back = True
        self._save()
        return True

    # --- reporting ------------------------------------------------------------
    def audit_trail(self, limit: int = 20) -> str:
        """A readable log of what was changed and why, newest first."""
        rows = [r for r in self.for_case() if not r.rolled_back]
        if not rows:
            return "No file changes have been made to this case."
        lines = [f"{len(rows)} change(s) to this case, newest first:", ""]
        for record in reversed(rows[-limit:]):
            lines.append(record.summary())
            lines.append("")
        return "\n".join(lines).strip()


def confidence_from_hypothesis(hypothesis) -> str:
    """Map a hypothesis's numeric confidence onto the label used in records."""
    if hypothesis is None:
        return ""
    return getattr(hypothesis, "confidence_label", "") or ""
