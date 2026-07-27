"""
OpenFOAM / SPUMA Debugging Assistant -- desktop app entry point.

Run it with:   python main.py
"""
import sys

from PySide6.QtWidgets import QApplication, QMessageBox


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("OpenFOAM-AI")
    try:
        from src.gui.main_window import MainWindow
        window = MainWindow()
        window.show()
    except Exception as e:  # show a readable popup instead of silently closing
        QMessageBox.critical(None, "Startup error", f"The app could not start:\n\n{e}")
        raise
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
