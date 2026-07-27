"""
The look of the app, written as a Qt stylesheet (QSS) -- similar idea to CSS.

It's kept as a Python string (not a separate file) so it is automatically
included when the app is packaged into a .exe.
"""

STYLE = """
QMainWindow, QDialog { background: #f4f5f7; }

QToolBar {
    background: #ffffff;
    border-bottom: 1px solid #e3e5e8;
    padding: 6px;
    spacing: 6px;
}

#sidebar {
    background: #ffffff;
    border-right: 1px solid #e3e5e8;
}

QLabel#sectionHeader {
    color: #6b7280;
    font-size: 11px;
    font-weight: bold;
    padding: 6px 2px 2px 2px;
}

QLabel#hint  { color: #6b7280; font-size: 12px; }
QLabel#blurb { color: #6b7280; font-size: 12px; }
QLabel#bubbleFooter { color: #9aa0a6; font-size: 10px; padding-left: 4px; }

QListWidget {
    border: 1px solid #e3e5e8;
    border-radius: 8px;
    padding: 4px;
    background: #ffffff;
}
QListWidget::item { padding: 8px; border-radius: 6px; }
QListWidget::item:selected { background: #e8f0fe; color: #1a56db; }
QListWidget::item:hover { background: #f1f3f4; }

QScrollArea#chatArea { border: none; background: #f4f5f7; }

QLabel#userBubble {
    background: #1a56db;
    color: #ffffff;
    border-radius: 12px;
    padding: 10px 14px;
}
QLabel#assistantBubble {
    background: #ffffff;
    color: #202124;
    border: 1px solid #e3e5e8;
    border-radius: 12px;
    padding: 10px 14px;
}

QTextEdit {
    border: 1px solid #d2d5da;
    border-radius: 8px;
    padding: 8px;
    background: #ffffff;
}

QPushButton {
    background: #ffffff;
    border: 1px solid #d2d5da;
    border-radius: 8px;
    padding: 8px 12px;
}
QPushButton:hover { background: #f1f3f4; }
QPushButton:disabled { color: #9aa0a6; background: #f4f5f7; }

QPushButton#primaryButton {
    background: #1a56db;
    color: #ffffff;
    border: none;
    font-weight: bold;
}
QPushButton#primaryButton:hover { background: #1749b8; }
QPushButton#primaryButton:disabled { background: #9db8ef; }

QComboBox {
    border: 1px solid #d2d5da;
    border-radius: 8px;
    padding: 5px 10px;
    background: #ffffff;
    min-width: 110px;
}

QTextEdit#diffView { background: #fbfbfb; }
"""
