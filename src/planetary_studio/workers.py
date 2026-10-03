"""All camera I/O, disk recording, and image processing run off the UI thread."""

import json
from pathlib import Path
import queue
import threading
import time
from PySide6.QtCore import QThread, Signal
from .cameras import open_camera
from .imaging import debayer, normalized
from .ser import SerWriter


class TaskWorker(QThread):
    progress = Signal(float, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, function, *args, **kwargs):
        super().__init__()
        self.function, self.args, self.kwargs = function, args, kwargs
        self.cancel = threading.Event()

    def run(self):
        try:
            result = self.function(*self.args, progress=self.progress.emit, cancel=self.cancel, **self.kwargs)
            self.succeeded.emit(result)
        except Exception as e:
            self.failed.emit(str(e))


class CameraWorker(QThread):
    ready = Signal(object, object)
    preview = Signal(object, object)
    status = Signal(str)
    failed = Signal(str)
    recorded = Signal(str, int)

    def __init__(self, device):
        super().__init__()
        self.device, self.commands = device, queue.Queue()
        self.stop_event = threading.Event()

    def command(self, *items):
        self.commands.put(items)

    def run(self):
        camera, writer = None, None
        recording_path, recording_started, record_limit = "", 0.0, 0.0
        streaming, total, last_preview, last_frame = False, 0, 0.0, None
        started_at, last_received = time.monotonic(), time.monotonic()

        def end_recording():
            nonlocal writer
            if writer is not None:
                count = writer.count
                writer.close()
                Path(recording_path + ".json").write_text(
                    json.dumps(
                        {
                            "camera": self.device.name,
                            "backend": self.device.backend,
                            "frames": count,
                            "elapsed_seconds": time.monotonic() - recording_started,
                            "pattern": writer.pattern,
                            "bits": writer.bits,
                            "dropped_frames_reported_by_driver": last_frame.dropped if last_frame else 0,
                        },
                        indent=2,
                    ),
                    encoding="utf8",
                )
                writer = None
                self.recorded.emit(recording_path, count)
                self.status.emit(f"Recording saved · {count:,} frames")

        try:
            camera = open_camera(self.device)
            modes = camera.modes()
            if not modes:
                raise ValueError("Camera did not provide any capture modes.")
            self.ready.emit(modes, camera.controls())
            while not self.stop_event.is_set():
                while not self.commands.empty():
                    items = self.commands.get_nowait()
                    action = items[0]
                    if action == "start":
                        camera.start(items[1])
                        streaming, started_at, last_received = True, time.monotonic(), time.monotonic()
                        self.status.emit("Live preview started")
                    elif action == "control":
                        try:
                            camera.set_control(items[1], items[2])
                        except Exception as e:
                            self.status.emit(str(e))
                    elif action == "record":
                        if writer is not None:
                            raise ValueError("A recording is already in progress.")
                        if last_frame is None:
                            raise ValueError("Start live preview before recording.")
                        recording_path, record_limit = items[1:3]
                        writer = SerWriter(
                            recording_path,
                            last_frame.pixels.shape,
                            last_frame.bits,
                            last_frame.pattern,
                            instrument=self.device.name,
                            overwrite=bool(items[3]) if len(items) > 3 else False,
                        )
                        recording_started = time.monotonic()
                        self.status.emit("Recording raw SER frames…")
                    elif action == "stop_record":
                        end_recording()
                if not streaming:
                    self.stop_event.wait(0.05)
                    continue
                frame = camera.read(500)
                if frame is None:
                    if time.monotonic() - last_received > 15:
                        self.status.emit(
                            "Waiting for camera frames. Check exposure, USB cable, and other camera apps."
                        )
                        last_received = time.monotonic()
                    continue
                last_frame, last_received = frame, time.monotonic()
                total += 1
                if writer:
                    writer.write(frame.pixels)
                    if record_limit > 0 and time.monotonic() - recording_started >= record_limit:
                        end_recording()
                now = time.monotonic()
                if now - last_preview >= 0.08:
                    rgb = debayer(frame.pixels, frame.pattern)
                    self.preview.emit(
                        normalized(rgb, frame.bits),
                        {
                            "fps": total / max(now - started_at, 0.001),
                            "frames": total,
                            "recorded": writer.count if writer else 0,
                            "recording": writer is not None,
                            "dropped": frame.dropped,
                            "shape": frame.pixels.shape,
                            "bits": frame.bits,
                            "pattern": frame.pattern,
                        },
                    )
                    last_preview = now
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            try:
                end_recording()
            except Exception as e:
                self.failed.emit(f"Recording finalization failed: {e}")
            if camera:
                try:
                    camera.close()
                except Exception as e:
                    self.status.emit(f"Camera cleanup: {e}")
