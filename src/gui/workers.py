"""
A tiny background worker.

Calling the Claude API takes several seconds (longer for the deep models). If we
did that on the main GUI thread, the whole window would freeze. So we run the
work on a separate thread and report back with Qt "signals".
"""
from PySide6.QtCore import QThread, Signal


class ApiWorker(QThread):
    """Runs any function off the GUI thread and emits its result (or the error)."""

    ok = Signal(object)    # emitted with the function's return value on success
    fail = Signal(str)     # emitted with an error message on failure

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            self.ok.emit(self._fn(*self._args, **self._kwargs))
        except Exception as e:  # noqa: BLE001 - we want to surface any error to the UI
            self.fail.emit(str(e))


class StreamWorker(QThread):
    """
    Like ApiWorker, but for a function that STREAMS its output.

    The wrapped function must accept an `on_delta` keyword argument; we pass one
    that re-emits each chunk as the `delta` signal (so the UI can update live).
    """

    delta = Signal(str)      # emitted for each streamed text chunk
    ok = Signal(object)      # emitted with the function's return value (may be a tuple)
    fail = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            full = self._fn(*self._args, on_delta=self._emit, **self._kwargs)
            self.ok.emit(full)
        except Exception as e:  # noqa: BLE001
            self.fail.emit(str(e))

    def _emit(self, chunk: str):
        self.delta.emit(chunk)
