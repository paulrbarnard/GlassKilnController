"""Runs a Sequencer call on a background QThread.

A firing sequence can legitimately take hours (a Slow Full Fuse anneal cools
over an extended period) and every step blocks on camera polling — running
that on the Qt main thread would freeze the touchscreen for the entire
firing. Screens should always kick off Sequencer work through
``SequencerWorker`` rather than calling ``Sequencer`` methods directly.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal


class SequencerWorker(QObject):
    progress = Signal(str)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, task: Callable[[], None]) -> None:
        super().__init__()
        self._task = task

    def run(self) -> None:
        try:
            self._task()
        except Exception as exc:  # noqa: BLE001 - surface any failure to UI
            self.failed.emit(str(exc))
            return
        self.finished.emit()


def run_in_background(
    task: Callable[[], None],
    on_progress: Callable[[str], None] | None = None,
    on_finished: Callable[[], None] | None = None,
    on_failed: Callable[[str], None] | None = None,
) -> tuple[QThread, SequencerWorker]:
    """Starts ``task`` on a new QThread and wires up the given callbacks.

    Returns ``(thread, worker)`` — the caller must keep a reference to both
    (e.g. as attributes on the screen) for the duration of the run, or Qt
    will garbage-collect them mid-flight.
    """

    thread = QThread()
    worker = SequencerWorker(task)
    worker.moveToThread(thread)

    thread.started.connect(worker.run)
    if on_progress:
        worker.progress.connect(on_progress)
    if on_finished:
        worker.finished.connect(on_finished)
    if on_failed:
        worker.failed.connect(on_failed)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)

    thread.start()
    return thread, worker
