"""The capture worker: one thread owns the camera.

Picamera2, numpy and Pillow are first imported here, when the camera starts, so a
slideshow wake never loads them.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CaptureResult:
    jpeg: bytes
    epd: bytes
    thumb: bytes
    metadata: dict
    print_png: bytes


class CaptureWorker:
    def __init__(self, camera_factory, log=None):
        self._factory = camera_factory
        self.camera = None
        self.log = log or (lambda *_: None)
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capture")

    async def _call(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._exec, fn, *args)

    # Camera lifecycle ------------------------------------------------------------
    def _open(self) -> str:
        if self.camera is None:
            self.camera = self._factory()
        return self.camera.open()

    async def open(self) -> str:
        return await self._call(self._open)

    async def close(self) -> None:
        if self.camera is not None:
            await self._call(self.camera.close)

    async def set_fps(self, fps: int) -> None:
        if self.camera is not None:
            await self._call(self.camera.set_fps, fps)

    async def sleep(self) -> None:
        await self._call(self.camera.sleep)

    async def wake(self) -> None:
        await self._call(self.camera.wake, False)

    # Shots ----------------------------------------------------------------------------
    def _capture(self, flush_ns: int, taken: datetime | None, wake_first: bool, fps: int) -> CaptureResult:
        from inkstant.imaging import pipeline

        cam = self.camera
        if wake_first:
            cam.wake(True)
            flush_ns = time.monotonic_ns()
        made: dict = {}

        def screen_job(lores, md):
            try:
                made["epd"], made["thumb"] = pipeline.from_lores(lores)
                made["print"] = pipeline.print_png(made["epd"])
            except Exception as e:  # reported below
                made["error"] = e

        def on_lores(lores, md):
            # The lores array is a copy, so the screen image runs alongside the JPEG encode.
            t = threading.Thread(target=screen_job, args=(lores, md), name="screen-image")
            made["thread"] = t
            t.start()

        shot = cam.capture(flush_ns, lambda md: pipeline.exif_dict(taken, md), on_lores)
        if wake_first:
            cam.set_fps(fps)
        made["thread"].join()
        if "error" in made:
            raise made["error"]
        return CaptureResult(shot.jpeg, made["epd"], made["thumb"], shot.metadata, made["print"])

    async def capture(self, flush_ns: int, taken: datetime | None, wake_first: bool, fps: int) -> CaptureResult:
        return await self._call(self._capture, flush_ns, taken, wake_first, fps)

    async def run(self, fn, *args):
        """Run other camera-thread work (rebuilding screen images uses Pillow here too)."""
        return await self._call(fn, *args)

    def shutdown(self) -> None:
        self._exec.shutdown(wait=False, cancel_futures=True)
