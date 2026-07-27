"""
The main application window.

Conversation flow (per user message):
  classify intent  ->  (auto) pick skills  ->  if needed: deterministic checks +
  RAG + internal 10-step analysis (hidden)  ->  natural chat reply (streamed).

The 10-step process is INTERNAL only. The GUI shows a simple rotating loading
state, then a polished conversational answer — never the steps or internal checks.
"""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..case_reader import case_inventory, read_case
from ..config import DEFAULT_MODEL_TIER, MODELS
from ..deterministic import run_deterministic_checks
from ..diagnoser import propose_edits, run_full_turn
from ..file_editor import FileEditor
from ..intent import classify_intent
from ..knowledge_base import KnowledgeBase
from ..skills import SKILL_DESCRIPTIONS, list_skills
from .chat import ChatView
from .dialogs import EditReviewDialog, SettingsDialog
from .style import STYLE
from .workers import ApiWorker, StreamWorker

MAX_ATTACH_CHARS = 15000
AUTO_LABEL = "Auto (recommended)"
LOADING_MESSAGES = [
    "Analyzing case files…",
    "Checking boundary conditions…",
    "Reviewing numerical settings…",
    "Preparing response…",
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OpenFOAM / SPUMA Debugging Assistant")
        self.resize(1120, 800)
        self.setStyleSheet(STYLE)

        # --- state ---
        self.kb = KnowledgeBase()
        self.model_tier = DEFAULT_MODEL_TIER
        self.active_skill = AUTO_LABEL
        self.case_root: str | None = None
        self.case_files: dict[str, str] = {}
        self.inventory: dict = {}
        self.attachments: dict[str, str] = {}
        self.editor: FileEditor | None = None
        self.applied_edits: list[str] = []
        self.last_diagnosis = ""
        self.active_worker = None
        self.typing = None
        self.stream_bubble = None
        self._stream_buffer = ""
        self.loading_bubble = None
        self._loading_timer: QTimer | None = None
        self._loading_i = 0
        self.conversations: list[dict] = []
        self.convo_index = -1
        self._loading = False

        self._build_ui()
        self._new_conversation()

    # ==================================================================== UI ==
    def _build_ui(self):
        self._build_toolbar()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_center())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([290, 830])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Ready.")

    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addWidget(QLabel("  Mode: "))
        self.model_combo = QComboBox()
        self.model_combo.addItems(list(MODELS.keys()))
        self.model_combo.setCurrentText(self.model_tier)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        self.model_combo.setToolTip("\n".join(f"{k}: {v['blurb']}" for k, v in MODELS.items()))
        tb.addWidget(self.model_combo)
        self.mode_blurb = QLabel("   " + MODELS[self.model_tier]["blurb"])
        self.mode_blurb.setObjectName("blurb")
        tb.addWidget(self.mode_blurb)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        folder_btn = QPushButton("📁 Select Case Folder")
        folder_btn.clicked.connect(self._select_case_folder)
        tb.addWidget(folder_btn)
        self.undo_btn = QPushButton("↩ Undo last change")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._undo_last)
        tb.addWidget(self.undo_btn)
        settings_btn = QPushButton("⚙ Settings")
        settings_btn.clicked.connect(lambda: SettingsDialog(self).exec())
        tb.addWidget(settings_btn)

    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("sidebar")
        panel.setMaximumWidth(320)
        col = QVBoxLayout(panel)

        h1 = QLabel("SKILL (AUTO BY DEFAULT)")
        h1.setObjectName("sectionHeader")
        col.addWidget(h1)
        self.skill_list = QListWidget()
        self.skill_list.addItems([AUTO_LABEL] + list_skills())
        self.skill_list.setCurrentRow(0)
        self.skill_list.currentTextChanged.connect(self._on_skill_changed)
        col.addWidget(self.skill_list)
        self.skill_desc = QLabel("The assistant picks the right skill(s) automatically from your question.")
        self.skill_desc.setObjectName("hint")
        self.skill_desc.setWordWrap(True)
        col.addWidget(self.skill_desc)

        col.addSpacing(10)
        h2 = QLabel("CONVERSATIONS")
        h2.setObjectName("sectionHeader")
        col.addWidget(h2)
        new_btn = QPushButton("+ New chat")
        new_btn.clicked.connect(self._new_conversation)
        col.addWidget(new_btn)
        self.convo_list = QListWidget()
        self.convo_list.currentRowChanged.connect(self._on_convo_selected)
        col.addWidget(self.convo_list, 1)
        return panel

    def _build_center(self) -> QWidget:
        panel = QWidget()
        col = QVBoxLayout(panel)
        self.chat = ChatView()
        col.addWidget(self.chat, 1)
        self.case_label = QLabel("No case folder selected.")
        self.case_label.setObjectName("hint")
        self.case_label.setWordWrap(True)
        col.addWidget(self.case_label)

        row = QHBoxLayout()
        self.input = QTextEdit()
        self.input.setPlaceholderText("Ask anything — e.g. “why is my pressure diverging?”   (Ctrl+Enter to send)")
        self.input.setFixedHeight(92)
        row.addWidget(self.input, 1)
        buttons = QVBoxLayout()
        self.send_btn = QPushButton("➤ Send")
        self.send_btn.setObjectName("primaryButton")
        self.send_btn.clicked.connect(self._on_send)
        buttons.addWidget(self.send_btn)
        self.edits_btn = QPushButton("✎ Propose Edits")
        self.edits_btn.clicked.connect(self._on_propose_edits_button)
        buttons.addWidget(self.edits_btn)
        attach_btn = QPushButton("📎 Attach file")
        attach_btn.clicked.connect(self._attach_file)
        buttons.addWidget(attach_btn)
        row.addLayout(buttons)
        col.addLayout(row)
        QShortcut(QKeySequence("Ctrl+Return"), self.input, activated=self._on_send)
        return panel

    # =============================================================== helpers ==
    def _add(self, role, text, footer=None, store=True):
        self.chat.add_message(role, text, footer)
        if store:
            self._store(role, text, footer)

    def _store(self, role, text, footer=None):
        if 0 <= self.convo_index < len(self.conversations):
            convo = self.conversations[self.convo_index]
            convo["messages"].append({"role": role, "text": text, "footer": footer})
            if role == "user" and convo["title"] == "New chat":
                title = (text[:26] + "…") if len(text) > 26 else (text or "New chat")
                convo["title"] = title
                self.convo_list.item(self.convo_index).setText(title)

    def _compose_input(self) -> str:
        parts = []
        typed = self.input.toPlainText().strip()
        if typed:
            parts.append(typed)
        for name, content in self.attachments.items():
            parts.append(f"[Attached file: {name}]\n{content}")
        return "\n\n".join(parts)

    def _history_text(self) -> str:
        if not (0 <= self.convo_index < len(self.conversations)):
            return ""
        msgs = self.conversations[self.convo_index]["messages"]
        lines = []
        for m in msgs[-7:-1]:  # a few prior turns, excluding the current user message
            who = "User" if m["role"] == "user" else "Assistant"
            text = m["text"]
            if len(text) > 700:
                text = text[:700] + "…"
            lines.append(f"{who}: {text}")
        return "\n".join(lines)

    def _last_assistant_text(self) -> str:
        if not (0 <= self.convo_index < len(self.conversations)):
            return ""
        for m in reversed(self.conversations[self.convo_index]["messages"]):
            if m["role"] == "assistant":
                return m["text"]
        return ""

    def _context_notes(self) -> str:
        notes = []
        if self.case_root:
            notes.append(f"A case folder is loaded: {self.case_root} ({len(self.case_files)} files readable).")
        if self.applied_edits:
            notes.append("Edits already applied this session: " + "; ".join(self.applied_edits) + ".")
        return " ".join(notes)

    def _set_busy(self, busy):
        self.send_btn.setEnabled(not busy)
        self.edits_btn.setEnabled(not busy)

    def _on_worker_finished(self):
        self.active_worker = None
        self._set_busy(False)

    # --- rotating loading indicator (a generic, non-revealing status) ---
    def _begin_loading(self):
        self._loading_i = 0
        self.loading_bubble = self.chat.add_message("assistant", LOADING_MESSAGES[0])
        if self._loading_timer is None:
            self._loading_timer = QTimer(self)
            self._loading_timer.timeout.connect(self._cycle_loading)
        self._loading_timer.start(1200)

    def _cycle_loading(self):
        if self.loading_bubble is None:
            return
        self._loading_i = (self._loading_i + 1) % len(LOADING_MESSAGES)
        self.loading_bubble.set_text(LOADING_MESSAGES[self._loading_i])
        self.chat.scroll_to_bottom()

    def _end_loading(self):
        if self._loading_timer is not None:
            self._loading_timer.stop()
        if self.loading_bubble is not None:
            self.chat.remove(self.loading_bubble)
            self.loading_bubble = None

    # ============================================================== actions ==
    def _on_model_changed(self, tier):
        self.model_tier = tier
        self.mode_blurb.setText("   " + MODELS[tier]["blurb"])
        self.statusBar().showMessage(f"Mode: {tier} — {MODELS[tier]['id']}")

    def _on_skill_changed(self, name):
        if not name:
            return
        self.active_skill = name
        if name == AUTO_LABEL:
            self.skill_desc.setText("The assistant picks the right skill(s) automatically from your question.")
        else:
            self.skill_desc.setText(SKILL_DESCRIPTIONS.get(name, ""))

    def _select_case_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select your OpenFOAM/SPUMA case folder")
        if not folder:
            return
        try:
            self.case_files = read_case(folder)
            self.inventory = case_inventory(folder)
        except Exception as e:
            self._add("assistant", f"❌ Could not read that folder: {e}")
            return
        self.case_root = folder
        self.editor = FileEditor(folder)
        self.undo_btn.setEnabled(self.editor.can_undo())
        self.case_label.setText(f"📁 Case: {folder}   ({len(self.case_files)} file(s) loaded)")
        self._add("assistant", f"Loaded the case folder ({len(self.case_files)} files). "
                               "Ask me anything about it — for example, *why is it diverging?* or "
                               "*is my outlet boundary condition right?*")

    def _attach_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach log or text file(s)")
        added = []
        for p in paths:
            try:
                content = Path(p).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if len(content) > MAX_ATTACH_CHARS:
                content = content[:MAX_ATTACH_CHARS] + "\n... [truncated] ..."
            self.attachments[Path(p).name] = content
            added.append(Path(p).name)
        if added:
            self._add("assistant", "📎 Attached (will be used as context): " + ", ".join(added))

    # ---- main send: intent -> (optional internal diagnosis) -> chat reply ----
    def _on_send(self):
        if self.active_worker is not None:
            return
        typed = self.input.toPlainText().strip()
        if not typed and not self.attachments and not self.case_files:
            self._add("assistant", "Ask a question, paste a log, or select a case folder to start.")
            return
        combined = self._compose_input()
        self._add("user", typed or "(please look at the loaded case)")
        self.input.clear()

        convo = self.conversations[self.convo_index]
        has_prior = bool(convo.get("last_internal") or convo.get("last_response"))
        prior_questions = [m["text"] for m in convo["messages"][:-1] if m["role"] == "user"]
        intent = classify_intent(typed, has_case=bool(self.case_files), has_prior=has_prior,
                                 prior_questions=prior_questions)
        if self.active_skill != AUTO_LABEL:  # manual override
            intent.skills = [self.active_skill]

        if intent.name == "propose_edit":
            self._start_edit_flow(combined, intent.focus)
            return

        intent_d = intent.as_dict()
        findings = run_deterministic_checks(self.case_files, "") if (intent.needs_diagnosis and self.case_files) else []
        do_rag = intent.needs_diagnosis or intent.name in ("general_question", "recommendation", "compare_options")
        snippets = self.kb.search(f"{intent.focus} {typed}") if do_rag else []

        self._begin_loading()
        self._set_busy(True)
        worker = StreamWorker(
            run_full_turn, combined, intent_d, self.case_files, self.inventory, findings, snippets,
            self._history_text(), convo.get("last_internal"), self._last_assistant_text(),
            self._context_notes(), self.model_tier,
        )
        worker.delta.connect(self._on_delta)
        worker.ok.connect(self._on_turn_done)
        worker.fail.connect(self._on_turn_error)
        worker.finished.connect(self._on_worker_finished)
        self.active_worker = worker
        worker.start()

    def _on_delta(self, chunk):
        if self.loading_bubble is not None:  # first chunk: swap loading for the real bubble
            self._end_loading()
            self.stream_bubble = self.chat.add_message("assistant", "")
            self._stream_buffer = ""
        self._stream_buffer += chunk
        if self.stream_bubble is not None:
            self.stream_bubble.set_text(self._stream_buffer)
            self.chat.scroll_to_bottom()

    def _on_turn_done(self, result):
        reply, internal = result
        self._end_loading()
        if self.stream_bubble is not None:
            self.chat.remove(self.stream_bubble)
            self.stream_bubble = None
        cfg = MODELS[self.model_tier]
        self._add("assistant", reply, footer=f"Mode: {self.model_tier} · {cfg['id']}")
        convo = self.conversations[self.convo_index]
        convo["last_response"] = reply
        if internal:
            convo["last_internal"] = internal
        self.last_diagnosis = reply

    def _on_turn_error(self, message):
        self._end_loading()
        if self.stream_bubble is not None:
            self.chat.remove(self.stream_bubble)
            self.stream_bubble = None
        self._add("assistant", f"❌ Something went wrong talking to Claude:\n\n{message}")

    # ---- edits (button OR "propose_edit" intent) ----
    def _on_propose_edits_button(self):
        self._start_edit_flow(self._compose_input_for_edit_button(), "")

    def _compose_input_for_edit_button(self):
        typed = self.input.toPlainText().strip()
        self._add("user", typed or "Propose a safe edit for the recommended fix.")
        self.input.clear()
        return self._compose_input() if typed else "Propose a safe edit for the recommended fix."

    def _start_edit_flow(self, combined, focus):
        if self.active_worker is not None:
            return
        if not self.editor or not self.case_files:
            self._add("assistant", "To edit files I first need your case folder. Click “📁 Select Case Folder”.")
            return
        convo = self.conversations[self.convo_index]
        snippets = self.kb.search(f"{focus} {combined}")
        self._set_busy(True)
        self.typing = self.chat.add_message("assistant", "Preparing a safe edit proposal…  ⏳")
        worker = ApiWorker(propose_edits, combined, self.case_files, snippets,
                           convo.get("last_internal") or self.last_diagnosis, self.model_tier)
        worker.ok.connect(self._on_edits_done)
        worker.fail.connect(self._on_edits_error)
        worker.finished.connect(self._on_worker_finished)
        self.active_worker = worker
        worker.start()

    def _remove_typing(self):
        if self.typing is not None:
            self.chat.remove(self.typing)
            self.typing = None

    def _on_edits_error(self, message):
        self._remove_typing()
        self._add("assistant", f"❌ Something went wrong preparing edits:\n\n{message}")

    def _on_edits_done(self, result):
        self._remove_typing()
        summary, proposals = result
        if summary:
            self._add("assistant", summary)
        if proposals:
            self._add("assistant", f"I've prepared **{len(proposals)}** proposed edit(s). Opening the review window…")
            dialog = EditReviewDialog(self.editor, proposals, self)
            dialog.applied.connect(self._on_edit_applied)
            dialog.exec()
        elif not summary:
            self._add("assistant", "No safe edit could be proposed from the current evidence.")

    def _on_edit_applied(self, record):
        self.applied_edits.append(record.file_path)
        self.undo_btn.setEnabled(self.editor.can_undo())
        note = f"✔ Applied change to `{record.file_path}`."
        note += f"\nBackup: {record.backup_abs}" if record.backup_abs else " (new file created)"
        self._add("assistant", note)
        self._refresh_case_files()

    def _undo_last(self):
        if not self.editor:
            return
        record = self.editor.undo_last()
        if record:
            if self.applied_edits:
                self.applied_edits.pop()
            self._add("assistant", f"↩ Undid the change to `{record.file_path}` (restored the previous version).")
            self._refresh_case_files()
        self.undo_btn.setEnabled(self.editor.can_undo())

    def _refresh_case_files(self):
        if self.case_root:
            try:
                self.case_files = read_case(self.case_root)
            except Exception:
                pass

    # ======================================================== conversations ==
    def _new_conversation(self):
        self.conversations.append({"title": "New chat", "messages": [], "last_internal": None, "last_response": ""})
        self.convo_index = len(self.conversations) - 1
        self._loading = True
        self.convo_list.addItem("New chat")
        self.convo_list.setCurrentRow(self.convo_index)
        self._loading = False
        self.chat.clear()
        self._add(
            "assistant",
            "👋 Hi! Ask me anything about your OpenFOAM/SPUMA case — for example "
            "*why is my run diverging?*, *is my outlet BC right?*, or *what should I change first?* "
            "Select a case folder so I can read the files. I only *suggest* edits (you approve each one) "
            "and I never run OpenFOAM.",
        )

    def _on_convo_selected(self, row):
        if self._loading or not (0 <= row < len(self.conversations)):
            return
        self.convo_index = row
        self.chat.clear()
        for m in self.conversations[row]["messages"]:
            self.chat.add_message(m["role"], m["text"], m.get("footer"))
