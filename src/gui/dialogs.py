"""
Two pop-up windows:

  SettingsDialog     -> paste/save the API key; shows where the folders live.
  EditReviewDialog   -> shows a colored diff for each proposed file edit and an
                        "Apply this change" button. Nothing is written until the
                        user clicks Apply; a backup is made automatically.
"""
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import BACKUPS_DIR, KNOWLEDGE_DIR, SKILLS_DIR, save_api_key


def _make_diff_view(diff_text: str) -> QTextEdit:
    """A read-only, monospaced, color-coded view of a unified diff."""
    view = QTextEdit()
    view.setReadOnly(True)
    view.setObjectName("diffView")
    view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

    mono = QFont("Consolas")
    mono.setStyleHint(QFont.StyleHint.Monospace)
    mono.setPointSize(9)
    view.setFont(mono)

    cursor = view.textCursor()
    for line in diff_text.splitlines() or ["(no differences)"]:
        fmt = QTextCharFormat()
        if line.startswith("+") and not line.startswith("+++"):
            fmt.setForeground(QColor("#116329"))   # added -> green
        elif line.startswith("-") and not line.startswith("---"):
            fmt.setForeground(QColor("#a40e26"))   # removed -> red
        elif line.startswith("@@"):
            fmt.setForeground(QColor("#0550ae"))   # location -> blue
        else:
            fmt.setForeground(QColor("#57606a"))   # context -> grey
        cursor.insertText(line + "\n", fmt)
    view.moveCursor(QTextCursor.MoveOperation.Start)
    return view


class EditReviewDialog(QDialog):
    """Review and apply Claude's proposed edits, one file per tab."""

    applied = Signal(object)  # emits the AppliedEdit record after a change is applied

    def __init__(self, editor, proposals, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setWindowTitle("Review proposed changes")
        self.resize(860, 640)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Review each proposed change below. <b>Nothing is written until you click "
            "Apply.</b> A timestamped backup of the original is made automatically "
            "before every change, and you can use “Undo last change” in the main window."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)

        tabs = QTabWidget()
        for proposal in proposals:
            tabs.addTab(self._make_tab(proposal), proposal.file_path or "(unknown)")
        layout.addWidget(tabs, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

    def _make_tab(self, proposal) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)

        exists = True
        try:
            exists = self.editor.current_content(proposal.file_path) is not None
        except Exception:
            exists = True

        header = QLabel(
            f"<b>File:</b> {proposal.file_path}"
            + ("" if exists else "  <i>(new file — will be created)</i>")
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        col.addWidget(header)

        reason = QLabel(f"<b>Why:</b> {proposal.reason or '(no reason given)'}")
        reason.setTextFormat(Qt.TextFormat.RichText)
        reason.setWordWrap(True)
        col.addWidget(reason)

        can_apply = True
        try:
            diff_text = self.editor.make_diff(proposal.file_path, proposal.new_content)
        except Exception as e:
            diff_text = f"Cannot preview this change: {e}"
            can_apply = False
        col.addWidget(_make_diff_view(diff_text), 1)

        status = QLabel("")
        status.setWordWrap(True)
        apply_btn = QPushButton("✔ Apply this change")
        apply_btn.setObjectName("primaryButton")
        apply_btn.setEnabled(can_apply)

        def do_apply():
            try:
                record = self.editor.apply(proposal)
            except Exception as e:
                status.setText(f"❌ Could not apply: {e}")
                return
            apply_btn.setEnabled(False)
            if record.backup_abs:
                status.setText(f"✔ Applied. Backup of the original saved to:\n{record.backup_abs}")
            else:
                status.setText("✔ Applied (new file created; nothing to back up).")
            self.applied.emit(record)

        apply_btn.clicked.connect(do_apply)
        col.addWidget(apply_btn)
        col.addWidget(status)
        return page


class SettingsDialog(QDialog):
    """Set the API key and see where the app's folders are."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(600, 380)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Anthropic API key</b>"))

        self.status = QLabel(
            "A key is currently set." if os.environ.get("ANTHROPIC_API_KEY")
            else "No key set yet — paste one below to start."
        )
        layout.addWidget(self.status)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste your sk-ant-... key here")
        layout.addWidget(self.key_edit)

        save = QPushButton("Save key")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save)
        layout.addWidget(save)

        self.save_status = QLabel("")
        self.save_status.setWordWrap(True)
        layout.addWidget(self.save_status)

        layout.addSpacing(14)
        layout.addWidget(QLabel("<b>Folders (you can edit these on disk)</b>"))
        for name, path in [
            ("Skills", SKILLS_DIR),
            ("Knowledge", KNOWLEDGE_DIR),
            ("Backups", BACKUPS_DIR),
        ]:
            lbl = QLabel(f"{name}:  {path}")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

        tip = QLabel(
            "Tip: add your own SPUMA docs and solved errors as .md/.txt files in the "
            "Knowledge folder to make the assistant smarter."
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        layout.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def _save(self):
        key = self.key_edit.text().strip()
        if not key:
            self.save_status.setText("Please paste a key first.")
            return
        try:
            save_api_key(key)
        except Exception as e:
            self.save_status.setText(f"Could not save: {e}")
            return
        self.status.setText("A key is currently set.")
        self.save_status.setText("✔ Saved. You can start analyzing now.")
        self.key_edit.clear()
