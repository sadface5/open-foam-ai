"""
The chat window: message "bubbles" and the scrolling area that holds them.

Assistant messages are rendered as Markdown, so the 7 diagnosis sections show up
with nice headings, bold text, and code formatting.
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class ChatBubble(QWidget):
    """One message. Blue on the right for the user, white on the left for the AI."""

    def __init__(self, role: str, text: str, footer: str | None = None, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)

        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)

        self._label = label = QLabel()
        label.setWordWrap(True)
        label.setMaximumWidth(680)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if role == "assistant":
            label.setObjectName("assistantBubble")
            label.setTextFormat(Qt.TextFormat.MarkdownText)
        else:
            label.setObjectName("userBubble")
            label.setTextFormat(Qt.TextFormat.PlainText)
        label.setText(text)
        col.addWidget(label)

        if footer:
            foot = QLabel(footer)
            foot.setObjectName("bubbleFooter")
            col.addWidget(foot)

        if role == "user":
            row.addStretch(1)
            row.addWidget(wrap)
        else:
            row.addWidget(wrap)
            row.addStretch(1)

    def set_text(self, text: str) -> None:
        """Replace the bubble's text (used to update a streaming reply live)."""
        self._label.setText(text)


class ChatView(QScrollArea):
    """A vertical, scrolling list of chat bubbles."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chatArea")
        self.setWidgetResizable(True)

        self._inner = QWidget()
        self._vbox = QVBoxLayout(self._inner)
        self._vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._vbox.addStretch(1)  # keeps bubbles pinned to the top
        self.setWidget(self._inner)

    def add_message(self, role: str, text: str, footer: str | None = None) -> ChatBubble:
        bubble = ChatBubble(role, text, footer)
        # Insert just before the trailing stretch item.
        self._vbox.insertWidget(self._vbox.count() - 1, bubble)
        QTimer.singleShot(30, self._scroll_to_bottom)
        return bubble

    def remove(self, bubble: ChatBubble) -> None:
        bubble.setParent(None)
        bubble.deleteLater()

    def clear(self) -> None:
        # Remove everything except the trailing stretch (the last item).
        while self._vbox.count() > 1:
            item = self._vbox.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def scroll_to_bottom(self) -> None:
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
