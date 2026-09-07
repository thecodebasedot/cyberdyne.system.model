from __future__ import annotations

import asyncio
import json
import math
import queue
import shutil
import subprocess
import threading
import time

from ...kernel.config import HardwareConfig
from ..interfaces import Camera, Detection, Frame, Microphone, Speaker, Utterance
from ..registry import DeviceRegistry
from ..serial import build_serial_devices


class OpenCVCamera(Camera):
    """USB / Pi camera through OpenCV with the built-in HOG person detector.

    Identity signatures are not computed here (no face model shipped); a
    face-embedding model can fill ``Detection.signature`` later without
    changing anything above the HAL. Distance is estimated from the box
    height and ``person_height_m``; bearing from the horizontal position.
    """
    device_id = "camera.opencv"

    def __init__(self, index: int = 0, fov: float = 1.05, max_range: float = 6.0, width: int = 640,
                 height: int = 480, person_height_m: float = 1.7, focal_px: float = 500.0) -> None:
        self.index, self.fov, self.max_range = index, fov, max_range
        self.width, self.height = width, height
        self.person_height_m, self.focal_px = person_height_m, focal_px
        self._cap = None
        self._hog = None
        self.frames = 0

    async def open(self) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("opencv not installed: pip install opencv-python-headless") from exc
        self._cv2 = cv2
        self._cap = cv2.VideoCapture(self.index)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    async def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    async def self_test(self) -> tuple[bool, str]:
        if self._cap is None or not self._cap.isOpened():
            return False, f"camera {self.index} not opened"
        ok, _ = self._cap.read()
        return ok, "frame captured" if ok else "no frame"

    def _detect(self):
        ok, img = self._cap.read()
        if not ok:
            raise OSError("camera read failed")
        small = self._cv2.resize(img, (320, 240))
        boxes, weights = self._hog.detectMultiScale(small, winStride=(8, 8), scale=1.05)
        dets = []
        for (x, _y, w, h), conf in zip(boxes, weights, strict=False):
            cx = (x + w / 2) / 320 - 0.5
            bearing = -cx * self.fov
            dist = self.focal_px * self.person_height_m / (h * (self.height / 240)) if h else self.max_range
            dets.append(Detection("person", "person", bearing, min(dist, self.max_range), float(min(1.0, conf))))
        return dets

    async def capture(self) -> Frame:
        dets = await asyncio.get_running_loop().run_in_executor(None, self._detect)
        self.frames += 1
        return Frame(time.monotonic(), self.width, self.height, dets)


class VoskMicrophone(Microphone):
    """Offline speech recognition (``pip install vosk sounddevice`` + a model dir).

    A background thread feeds audio into the recogniser; ``listen`` drains
    finished utterances. Speaker signatures are empty until a speaker-id
    model is wired in (the interface already carries them).
    """
    device_id = "mic.vosk"

    def __init__(self, model_dir: str = "model", sample_rate: int = 16000, device: int | None = None) -> None:
        self.model_dir, self.sample_rate, self.device = model_dir, sample_rate, device
        self._q: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error = ""

    async def open(self) -> None:
        try:
            import sounddevice as sd
            import vosk
        except ImportError as exc:
            raise RuntimeError("vosk/sounddevice not installed: pip install vosk sounddevice") from exc
        model = vosk.Model(self.model_dir)
        rec = vosk.KaldiRecognizer(model, self.sample_rate)

        def worker() -> None:
            try:
                with sd.RawInputStream(samplerate=self.sample_rate, blocksize=8000, dtype="int16", channels=1,
                                       device=self.device) as stream:
                    while not self._stop.is_set():
                        data, _ = stream.read(4000)
                        if rec.AcceptWaveform(bytes(data)):
                            text = json.loads(rec.Result()).get("text", "").strip()
                            if text:
                                self._q.put(text)
            except Exception as exc:  # noqa: BLE001
                self.error = repr(exc)
        self._thread = threading.Thread(target=worker, daemon=True, name="vosk")
        self._thread.start()

    async def close(self) -> None:
        self._stop.set()

    async def self_test(self) -> tuple[bool, str]:
        alive = self._thread is not None and self._thread.is_alive()
        return alive, "listening" if alive else (self.error or "not started")

    async def listen(self) -> list[Utterance]:
        out = []
        while True:
            try:
                out.append(Utterance(self._q.get_nowait(), time.monotonic()))
            except queue.Empty:
                return out


class ShellSpeaker(Speaker):
    """Text to speech through ``espeak-ng`` / ``espeak`` / ``say`` / ``pico2wave``, whichever exists."""
    device_id = "speaker.shell"

    def __init__(self, voice: str = "en", rate: int = 160) -> None:
        self.voice, self.rate = voice, rate
        self.cmd = next((c for c in ("espeak-ng", "espeak", "say") if shutil.which(c)), None)
        self.spoken = 0

    async def self_test(self) -> tuple[bool, str]:
        return (self.cmd is not None), (f"using {self.cmd}" if self.cmd else "no TTS binary (apt install espeak-ng)")

    async def say(self, text: str, voice: str = "neutral") -> None:
        if self.cmd is None:
            return
        rate = {"alert": self.rate + 40, "friendly": self.rate - 10}.get(voice, self.rate)
        args = [self.cmd, text] if self.cmd == "say" else [self.cmd, "-v", self.voice, "-s", str(rate), text]
        proc = await asyncio.create_subprocess_exec(*args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await proc.wait()
        self.spoken += 1


def build_rpi_devices(cfg: HardwareConfig, registry: DeviceRegistry, camera_index: int | None = 0,
                      vosk_model: str | None = "model", tts: bool = True):
    """Serial body + optional Pi peripherals. ``None`` skips a peripheral."""
    bridge = build_serial_devices(cfg, registry)
    if camera_index is not None:
        registry.register(OpenCVCamera(camera_index))
    if vosk_model is not None:
        registry.register(VoskMicrophone(vosk_model))
    if tts:
        registry.register(ShellSpeaker())
    return bridge


def bearing_to_pixel(bearing: float, fov: float, width: int) -> int:
    """Inverse of the camera's bearing estimate; used by tests."""
    return int((0.5 - bearing / fov) * width)


__all__ = ["OpenCVCamera", "VoskMicrophone", "ShellSpeaker", "build_rpi_devices", "bearing_to_pixel", "math"]
