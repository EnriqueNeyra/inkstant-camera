"""Display jobs and the display worker thread.

One job runs at a time, with one pending slot. The worker reports back by posting
events to the event loop; it never touches the app's state.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from inkstant.hw import epd as epd_mod
from inkstant.imaging import epdfile

DEVELOP, SHOW, SHARE_SCREEN, BLANK = "DEVELOP", "SHOW", "SHARE_SCREEN", "BLANK"


@dataclass
class Job:
    kind: str
    photo_id: str | None = None
    why: str | None = None  # SHOW: show_on_camera, restore, slideshow
    frame: bytes | None = None  # SHARE_SCREEN: rendered frame
    epd_path: Path | None = None
    seq: int = field(default=0, compare=False)

    def describe(self) -> str:
        return f"{self.kind}({self.photo_id or self.why or ''})"


class DisplayWorker:
    def __init__(self, driver, post: Callable[..., None], rotation: int = 0, log=None):
        self.driver = driver
        self.post = post  # thread-safe: post(event_name, job, detail)
        self.rotation = rotation
        self.log = log or (lambda *_: None)
        self._q: queue.Queue[Job | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="display", daemon=True)
        self.busy = threading.Event()

    def start(self) -> None:
        self._thread.start()

    def submit(self, job: Job) -> None:
        self._q.put(job)

    def abort(self) -> None:
        self.driver.request_abort()

    def stop(self) -> None:
        self._q.put(None)

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _frame(self, job: Job) -> bytes:
        if job.kind in (DEVELOP, SHOW):
            frame, _palette = epdfile.read(job.epd_path)
            return frame
        if job.kind == SHARE_SCREEN:
            return job.frame
        if job.kind == BLANK:
            return epdfile.solid(epdfile.WHITE_CODE)
        raise ValueError(job.kind)

    def _run(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            self.busy.set()
            try:
                frame = epdfile.oriented(self._frame(job), self.rotation)
            except (OSError, epdfile.EpdFileError) as e:
                self.log(f"display: {job.describe()}: {e}")
                self.post("display_finished", job, "unreadable")
                self.busy.clear()
                continue
            try:
                self.driver.show(frame, on_shown=lambda j=job: self.post("display_shown", j, None))
                result = "ok"
            except epd_mod.Aborted:
                result = "aborted"
            except epd_mod.ModuleMissing:
                result = "missing"
            except epd_mod.DisplayTimeout as e:
                self.log(f"display: {job.describe()}: {e}")
                result = "timeout"
            except Exception as e:
                self.log(f"display: {job.describe()}: {e!r}")
                result = "error"
            self.busy.clear()
            self.post("display_finished", job, result)
