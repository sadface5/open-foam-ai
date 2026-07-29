"""
Safe, reversible file editing for OpenFOAM/SPUMA cases.

Safety guarantees (all enforced here, not left to the AI):
  * Nothing is ever written until you call apply().
  * apply() ALWAYS makes a timestamped backup of the original first.
  * Edits are refused if the target path escapes the selected case folder.
  * undo_last() restores the most recent change from its backup.

Backups live in the app's /backups folder, with a small JSON "undo log" so undo
still works after restarting the app.
"""
import difflib
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import BACKUPS_DIR
from .diagnoser import EditProposal  # reuse the same simple data shape


@dataclass
class AppliedEdit:
    """A record of one change that was actually made (used for undo / restore)."""
    file_path: str          # path relative to the case root
    original_abs: str       # absolute path of the file that was changed
    backup_abs: Optional[str]  # absolute path of the backup copy (None if new file)
    was_new: bool           # True if we created a file that did not exist before
    reason: str
    timestamp: str


class FileEditor:
    """Applies EditProposals to one case folder, with backups and undo."""

    def __init__(self, case_root: str) -> None:
        self.case_root = Path(case_root).resolve()
        self.backups_dir = Path(BACKUPS_DIR)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.backups_dir / "undo_log.json"
        self.applied: list[AppliedEdit] = self._load_manifest()

    # --- internal: the undo log ------------------------------------------------
    def _load_manifest(self) -> list[AppliedEdit]:
        if self.manifest_path.exists():
            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                return [AppliedEdit(**d) for d in data]
            except Exception:
                return []
        return []

    def _save_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps([asdict(a) for a in self.applied], indent=2), encoding="utf-8"
        )

    # --- safety: keep edits inside the case folder -----------------------------
    def resolve(self, rel_path: str) -> Path:
        target = (self.case_root / rel_path).resolve()
        try:
            inside = os.path.commonpath([str(self.case_root), str(target)]) == str(self.case_root)
        except ValueError:
            inside = False  # e.g. different Windows drive
        if not inside:
            raise ValueError(f"Refusing to edit a path outside the case folder: {rel_path}")
        return target

    def current_content(self, rel_path: str) -> Optional[str]:
        """Read the file's current full contents from disk, or None if it doesn't exist."""
        target = self.resolve(rel_path)
        if target.is_file():
            return target.read_text(encoding="utf-8", errors="replace")
        return None

    # --- preview ---------------------------------------------------------------
    def make_diff(self, rel_path: str, new_content: str) -> str:
        """Return a unified diff (text) between the file on disk and the proposal."""
        current = self.current_content(rel_path)
        current_text = current if current is not None else ""
        label = rel_path if current is not None else f"{rel_path}  (NEW FILE)"
        diff = difflib.unified_diff(
            current_text.splitlines(),
            new_content.splitlines(),
            fromfile=f"current: {label}",
            tofile=f"proposed: {rel_path}",
            lineterm="",
        )
        return "\n".join(diff)

    # --- apply (the only method that writes) -----------------------------------
    def apply(self, proposal: EditProposal) -> AppliedEdit:
        target = self.resolve(proposal.file_path)
        was_new = not target.is_file()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_abs = None

        if not was_new:
            safe_name = proposal.file_path.replace("/", "__").replace("\\", "__")
            backup = self.backups_dir / f"{timestamp}__{safe_name}.bak"
            # The timestamp only resolves to the second, so two edits to the same
            # file in quick succession would otherwise land on the same filename
            # and the newer backup would overwrite -- and destroy -- the older
            # one, losing the original content for good. Never overwrite.
            counter = 1
            while backup.exists():
                backup = self.backups_dir / f"{timestamp}-{counter}__{safe_name}.bak"
                counter += 1
            shutil.copy2(target, backup)  # back up BEFORE writing
            backup_abs = str(backup)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)

        target.write_text(proposal.new_content, encoding="utf-8")

        record = AppliedEdit(
            file_path=proposal.file_path,
            original_abs=str(target),
            backup_abs=backup_abs,
            was_new=was_new,
            reason=proposal.reason,
            timestamp=timestamp,
        )
        self.applied.append(record)
        self._save_manifest()
        return record

    # --- undo ------------------------------------------------------------------
    def can_undo(self) -> bool:
        return len(self.applied) > 0

    def undo_last(self) -> Optional[AppliedEdit]:
        """Restore the most recent change. Returns the record undone, or None."""
        if not self.applied:
            return None
        last = self.applied.pop()
        target = Path(last.original_abs)
        if last.was_new:
            if target.is_file():
                target.unlink()  # we created it; removing it is the undo
        elif last.backup_abs and Path(last.backup_abs).is_file():
            shutil.copy2(last.backup_abs, target)  # restore the backed-up original
        self._save_manifest()
        return last
