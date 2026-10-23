"""Camera: Picamera2 wrapper, tuning override, sleep and wake.

Picamera2 is imported only when the camera first opens, in the capture worker thread,
so slideshow wakes never load it. Every method here blocks; call them from that thread.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

CAMERA_FPS = 5  # set from bench measurements; device.toml's camera_fps overrides it
SAVER_FPS = 5
SETTLE_FPS = 14  # the full-resolution maximum is 14.35 fps
SETTLE_FRAMES = 10
# Measured on a Pi 4 at full resolution (2026-10-16): the first frame comes ~1.6 s after start,
# then one every ~0.2 s (about 5 fps reach us, whatever the sensor's rate). So wait per frame.
FIRST_FRAME_TIMEOUT = 10.0
FRAME_TIMEOUT = 2.0
MAIN_SIZE = (4608, 2592)
LORES_SIZE = (1152, 648)
CAPTURE_TIMEOUT = 5.0
WAKE_FOCUS_TIMEOUT = 1.5
WAKE_MIN_FRAMES = 4
TUNING_ENV = "LIBCAMERA_RPI_TUNING_FILE"

# The tuning override, worked out for 5 fps. Absolute values, used at every frame rate.
# pdaf_gain was measured on a Pi 3B+, whose ISP passes about 3 full-resolution frames a second:
# Raspberry Pi's -0.016 closes about 12% of the focus error a frame, -0.045 settles twice as fast
# with no overshoot.
TUNING_OVERRIDE = {
    "rpi.agc": {"speed": 0.62},
    "rpi.awb": {"speed": 0.26, "frame_period": 2},
    "rpi.af": {"skip_frames": 1},
    "rpi.af.speeds.normal": {"step_frames": 2, "dropout_frames": 2, "retrigger_delay": 2, "max_slew": 3.0,
                             "pdaf_gain": -0.045},
}


def frame_duration_us(fps: int) -> int:
    return 1_000_000 // fps


def find_algo(tuning: dict, name: str) -> dict | None:
    """Like Picamera2.find_tuning_algo, for both tuning file versions."""
    algos = tuning.get("algorithms")
    if isinstance(algos, list):
        for entry in algos:
            if isinstance(entry, dict) and name in entry:
                return entry[name]
        return None
    value = tuning.get(name)
    return value if isinstance(value, dict) else None


def build_tuning(base: dict, exposure_cap_us: int | None = None) -> dict:
    """Apply the frame-rate override to a camera tuning (a dict loaded from imx708.json)."""
    tuning = copy.deepcopy(base)
    agc = find_algo(tuning, "rpi.agc")
    if agc is not None:
        channels = agc.get("channels")
        targets = channels if isinstance(channels, list) and channels else [agc]
        for ch in targets:
            ch.update(TUNING_OVERRIDE["rpi.agc"])
            if exposure_cap_us:
                for mode in ch.get("exposure_modes", {}).values():
                    shutter = mode.get("shutter")
                    if isinstance(shutter, list) and shutter:
                        mode["shutter"] = [min(s, exposure_cap_us) for s in shutter]
    awb = find_algo(tuning, "rpi.awb")
    if awb is not None:
        awb.update(TUNING_OVERRIDE["rpi.awb"])
    af = find_algo(tuning, "rpi.af")
    if af is not None:
        af.update(TUNING_OVERRIDE["rpi.af"])
        normal = af.setdefault("speeds", {}).setdefault("normal", {})
        normal.update(TUNING_OVERRIDE["rpi.af.speeds.normal"])
    return tuning


def cma_free_kb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("CmaFree:"):
                return int(line.split()[1])
    except OSError:
        pass
    return None


@dataclass
class Shot:
    jpeg: bytes
    metadata: dict


class CameraError(Exception):
    pass


class Camera:
    """The real camera. Not thread-safe: one worker thread owns it."""

    def __init__(self, device, log=print, fps: int | None = None):
        self.device = device
        self.log = log
        self.fps = fps or device.camera_fps or CAMERA_FPS
        self.picam2 = None
        self.mode = "off"  # off, full, fallback, asleep
        self._full_config = None
        self._preview_config = None
        self._tuning_file: str | None = None

    # Opening -------------------------------------------------------------------
    def _tuning(self, model: str) -> dict | None:
        from picamera2 import Picamera2

        for name in (f"{model}.json", "imx708.json"):
            try:
                return build_tuning(Picamera2.load_tuning_file(name))
            except Exception as e:
                self.log(f"camera: tuning {name}: {e}")
        return None

    def _use_tuning(self, tuning: dict) -> None:
        """Make the camera use `tuning`.

        libcamera reads the tuning file when it lists the camera, not when Picamera2 opens it,
        and listing it once already happened above. Picamera2(tuning=dict) also deletes its
        temporary file but leaves LIBCAMERA_RPI_TUNING_FILE set, which hides the camera at the
        next listing. So: our own file, kept until close(), and the camera listed again."""
        from picamera2 import Picamera2

        fd, self._tuning_file = tempfile.mkstemp(prefix="inkstant-tuning-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(tuning, f)
        os.environ[TUNING_ENV] = self._tuning_file
        reset = getattr(Picamera2._cm, "reset", None)
        if reset is not None:
            reset()
        else:
            self.log("camera: this Picamera2 can't list the camera again; the tuning override may not apply")

    def open(self) -> str:
        """Open and start the camera, settling at 14 fps. Returns "full" or "fallback"."""
        from libcamera import Transform, controls
        from picamera2 import Picamera2

        os.environ.pop(TUNING_ENV, None)  # list the camera with its default tuning first
        info = Picamera2.global_camera_info()
        if not info:
            raise CameraError("No camera found")
        model = info[0].get("Model", "imx708")
        tuning = self._tuning(model)
        if tuning is not None:
            self._use_tuning(tuning)
        self.picam2 = Picamera2(tuning=self._tuning_file)
        transform = Transform(hflip=self.device.camera_hflip, vflip=self.device.camera_vflip)
        settle = frame_duration_us(SETTLE_FPS)
        ctrl = {
            "FrameDurationLimits": (settle, settle),
            "AfMode": controls.AfModeEnum.Continuous,
            "AeExposureMode": controls.AeExposureModeEnum.Short,
        }
        self.picam2.options["quality"] = 90
        last_error = None
        for buffers in (2, 1):  # failure ladder, steps 1 and 2: two buffers, then one
            try:
                self._full_config = self.picam2.create_still_configuration(
                    main={"size": MAIN_SIZE, "format": "YUV420"},
                    lores={"size": LORES_SIZE, "format": "YUV420"},
                    buffer_count=buffers, transform=transform, controls=ctrl)
                self.picam2.configure(self._full_config)
                self.picam2.start()
                self.mode = "full"
                break
            except Exception as e:
                last_error = e
                self.log(f"camera: start with {buffers} buffer(s) failed: {e}")
                self._safe_stop()
        else:
            try:  # step 3: a small preview, switching to full resolution at each press
                self._preview_config = self.picam2.create_preview_configuration(
                    main={"size": LORES_SIZE, "format": "YUV420"}, transform=transform, controls=ctrl)
                self._full_config = self.picam2.create_still_configuration(
                    main={"size": MAIN_SIZE, "format": "YUV420"},
                    lores={"size": LORES_SIZE, "format": "YUV420"},
                    buffer_count=1, transform=transform)
                self.picam2.configure(self._preview_config)
                self.picam2.start()
                self.mode = "fallback"
            except Exception as e:
                self.close()
                raise CameraError(f"Camera won't start: {e or last_error}") from e
        self._settle(SETTLE_FRAMES)
        self.set_fps(self.fps)
        try:
            sensor = self.picam2.camera_configuration().get("sensor")
        except Exception:
            sensor = None
        self.log(f"camera: {model} started ({self.mode}), sensor {sensor}, CmaFree {cma_free_kb()} kB")
        return self.mode

    def _settle(self, frames: int) -> None:
        for i in range(frames):
            try:
                self.picam2.capture_metadata(wait=FIRST_FRAME_TIMEOUT if i == 0 else FRAME_TIMEOUT)
            except TimeoutError as e:
                raise CameraError("Camera didn't deliver frames") from e

    def _safe_stop(self) -> None:
        try:
            self.picam2.stop()
        except Exception:
            pass

    # Running -------------------------------------------------------------------
    def set_fps(self, fps: int) -> None:
        self.fps = fps
        d = frame_duration_us(fps)
        if self.picam2 is not None:
            self.picam2.set_controls({"FrameDurationLimits": (d, d)})

    def capture(self, flush: int, make_exif: Callable[[dict], dict],
                on_lores: Callable[[object, dict], None]) -> Shot:
        """Capture the first frame whose exposure began at or after `flush` (monotonic ns).

        `on_lores(array, metadata)` gets a copy of the lores frame first, so the screen image
        can be made while the full JPEG encodes."""
        if self.mode == "fallback":
            req = self.picam2.switch_mode_and_capture_request(self._full_config, wait=CAPTURE_TIMEOUT * 2)
        else:
            req = self.picam2.capture_request(wait=CAPTURE_TIMEOUT, flush=flush)
        try:
            md = req.get_metadata()
            lores = req.make_array("lores")
            on_lores(lores, md)
            buf = io.BytesIO()
            req.save("main", buf, format="jpeg", exif_data=make_exif(md))
            return Shot(buf.getvalue(), md)
        finally:
            req.release()

    def sleep(self) -> None:
        """Stop the stream; the configuration and its buffers stay allocated."""
        if self.picam2 is not None and self.mode in ("full", "fallback"):
            self.picam2.stop()
            self._asleep_from = self.mode
            self.mode = "asleep"

    def wake(self, for_shot: bool) -> None:
        """Restart at 14 fps; for a shot, wait for focus to settle."""
        from libcamera import controls

        if self.mode != "asleep":
            return
        settle = frame_duration_us(SETTLE_FPS)
        self.picam2.set_controls({"FrameDurationLimits": (settle, settle)})
        self.picam2.start()
        self.mode = getattr(self, "_asleep_from", "full")
        deadline = time.monotonic() + WAKE_FOCUS_TIMEOUT
        frames = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                md = self.picam2.capture_metadata(wait=max(remaining, 0.1))
            except TimeoutError:
                break  # out of time; the capture that follows has its own timeout
            frames += 1
            if frames < WAKE_MIN_FRAMES:
                continue
            if not for_shot:
                break
            state = md.get("AfState")
            if state in (controls.AfStateEnum.Focused, controls.AfStateEnum.Failed):
                break
        # The caller captures (for a shot) and then sets the running rate.
        if not for_shot:
            self.set_fps(self.fps)

    def close(self) -> None:
        if self.picam2 is not None:
            self._safe_stop()
            try:
                self.picam2.close()
            except Exception:
                pass
        self.picam2 = None
        self.mode = "off"
        os.environ.pop(TUNING_ENV, None)
        if self._tuning_file:
            with contextlib.suppress(OSError):
                os.unlink(self._tuning_file)
            self._tuning_file = None


class FakeCamera:
    """The simulator's camera: serves sample pictures at the real sizes."""

    def __init__(self, device=None, log=print, fps: int | None = None, samples_dir: Path | None = None,
                 fail_open: bool = False, delay: float = 0.05):
        self.device = device
        self.log = log
        self.fps = fps or CAMERA_FPS
        self.mode = "off"
        self.samples = sorted(Path(samples_dir).glob("*.jp*g")) if samples_dir else []
        self.fail_open = fail_open
        self.delay = delay
        self.count = 0
        self.history: list[str] = []

    def open(self) -> str:
        if self.fail_open:
            raise CameraError("Camera won't start (fake)")
        time.sleep(self.delay)
        self.mode = "full"
        self.history.append("open")
        return "full"

    def set_fps(self, fps: int) -> None:
        self.fps = fps
        self.history.append(f"fps {fps}")

    def _picture(self):
        from PIL import Image, ImageDraw

        self.count += 1
        if self.samples:
            with Image.open(self.samples[(self.count - 1) % len(self.samples)]) as im:
                return im.convert("RGB").resize(MAIN_SIZE)
        img = Image.new("RGB", MAIN_SIZE)
        draw = ImageDraw.Draw(img)
        hue = (self.count * 47) % 255
        for i in range(0, MAIN_SIZE[1], 32):
            draw.rectangle((0, i, MAIN_SIZE[0], i + 32), fill=((hue + i // 8) % 255, (i // 12) % 255, 255 - hue))
        draw.ellipse((1500, 600, 3100, 2000), fill=(240, 200, 40))
        draw.text((200, 200), f"Fake photo {self.count}", fill=(255, 255, 255))
        return img

    def capture(self, flush: int, make_exif, on_lores) -> Shot:
        if self.mode not in ("full", "fallback"):
            raise CameraError("camera not running")
        from inkstant.imaging import pipeline

        time.sleep(self.delay)
        img = self._picture()
        md = {"ExposureTime": 10000, "AnalogueGain": 1.5, "DigitalGain": 1.0, "SensorTimestamp": time.monotonic_ns()}
        on_lores(pipeline.rgb_to_yuv420(img.resize(LORES_SIZE)), md)
        self.history.append("capture")
        return Shot(pipeline.encode_jpeg(img, make_exif(md)), md)

    def sleep(self) -> None:
        if self.mode in ("full", "fallback"):
            self.mode = "asleep"
            self.history.append("sleep")

    def wake(self, for_shot: bool) -> None:
        if self.mode == "asleep":
            time.sleep(self.delay * 4)
            self.mode = "full"
            self.history.append("wake shot" if for_shot else "wake")

    def close(self) -> None:
        self.mode = "off"
        self.history.append("close")
