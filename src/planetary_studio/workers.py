"""All camera I/O, disk recording, and image processing run off the UI thread."""

import json
import queue
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .cameras import open_camera
from .cameras.base import InvalidFrameError
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
    capture_state = Signal(bool, bool)  # streaming, recording
    MAX_INVALID_FRAMES = 30

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
        mode, invalid, consecutive_invalid = None, 0, 0
        recording_invalid, recording_driver_dropped = 0, 0
        recording_last_error, stop_reason, stop_error = "", "disconnected", ""

        def emit_state():
            self.capture_state.emit(streaming, writer is not None)

        def end_recording(reason="user_stopped", error="", *, notify=True):
            nonlocal writer
            if writer is not None:
                completed, writer = writer, None
                count, elapsed = completed.count, time.monotonic() - recording_started
                finalized, final_error = True, ""
                try:
                    completed.close()
                except Exception as e:
                    finalized, final_error = False, str(e)
                report = {
                    "camera": self.device.name,
                    "backend": self.device.backend,
                    "frames": count,
                    "elapsed_seconds": elapsed,
                    "requested_seconds": record_limit,
                    "pattern": completed.pattern,
                    "bits": completed.bits,
                    "width": completed.shape[1],
                    "height": completed.shape[0],
                    "selected_fps": mode.fps,
                    "native_fourcc": mode.details.get("fourcc"),
                    "dropped_frames_reported_by_driver": max(
                        0, (last_frame.dropped if last_frame else 0) - recording_driver_dropped
                    ),
                    "invalid_frames_skipped": invalid - recording_invalid,
                    "last_frame_error": recording_last_error,
                    "stop_reason": reason,
                    "error": error,
                    "finalized": finalized,
                    "finalization_error": final_error,
                }
                try:
                    Path(recording_path + ".json").write_text(json.dumps(report, indent=2), encoding="utf8")
                except Exception as e:
                    self.status.emit(f"Could not save the recording report: {e}")
                if finalized:
                    self.recorded.emit(recording_path, count)
                    self.status.emit(
                        f"Recording saved · {count:,} frames · {elapsed:.1f} seconds · {reason.replace('_', ' ')}\n"
                        f"File: {recording_path} · Report: {recording_path}.json"
                    )
                else:
                    self.failed.emit(
                        f"Could not finish {recording_path}: {final_error}. Keep the file for recovery."
                    )
                if notify:
                    emit_state()

        def stop_preview(reason="live_view_stopped", error=""):
            nonlocal streaming, last_frame
            end_recording(reason, error, notify=False)
            camera.stop()
            streaming, last_frame = False, None
            emit_state()

        try:
            camera = open_camera(self.device)
            modes = camera.modes()
            if not modes:
                raise ValueError("Camera did not provide any capture modes.")
            self.ready.emit(modes, camera.controls())
            emit_state()
            while not self.stop_event.is_set():
                while not self.commands.empty():
                    items = self.commands.get_nowait()
                    action = items[0]
                    if action == "start":
                        if streaming:
                            self.failed.emit("Stop live view before changing the camera format.")
                            continue
                        mode, last_frame = items[1], None
                        total, invalid, consecutive_invalid, last_preview = 0, 0, 0, 0.0
                        try:
                            camera.start(mode)
                        except Exception as e:
                            stop_preview()
                            self.failed.emit(
                                f"Could not start {mode.label}: {e}. Choose another mode and try again."
                            )
                            continue
                        streaming, started_at, last_received = True, time.monotonic(), time.monotonic()
                        emit_state()
                        self.status.emit("Live view started · " + mode.label)
                    elif action == "stop":
                        stop_preview()
                        self.status.emit(
                            "Live view stopped. Change capture settings, then start live view again."
                        )
                    elif action == "control":
                        try:
                            camera.set_control(items[1], items[2])
                        except Exception as e:
                            self.status.emit(str(e))
                    elif action == "record":
                        if writer is not None:
                            self.failed.emit("A recording is already in progress.")
                            continue
                        if not streaming or last_frame is None:
                            self.failed.emit("Start live view and wait for a frame before recording.")
                            emit_state()
                            continue
                        recording_path, record_limit = items[1:3]
                        try:
                            writer = SerWriter(
                                recording_path,
                                last_frame.pixels.shape,
                                last_frame.bits,
                                last_frame.pattern,
                                instrument=self.device.name,
                                overwrite=bool(items[3]) if len(items) > 3 else False,
                            )
                        except Exception as e:
                            self.failed.emit(f"Could not start recording: {e}")
                            emit_state()
                            continue
                        recording_started = time.monotonic()
                        recording_invalid, recording_driver_dropped = invalid, last_frame.dropped
                        recording_last_error = ""
                        emit_state()
                        self.status.emit(f"Recording raw SER frames: {recording_path} · {mode.label}")
                    elif action == "stop_record":
                        end_recording()
                if not streaming:
                    self.stop_event.wait(0.05)
                    continue
                if writer and record_limit > 0 and time.monotonic() - recording_started >= record_limit:
                    end_recording("time_limit")
                try:
                    frame = camera.read(500)
                except InvalidFrameError as e:
                    invalid += 1
                    consecutive_invalid += 1
                    if writer:
                        recording_last_error = str(e)
                    if consecutive_invalid >= self.MAX_INVALID_FRAMES:
                        stop_preview("invalid_frames", str(e))
                        self.failed.emit(
                            f"Live view stopped after {consecutive_invalid} consecutive invalid frames: {e}\n"
                            "Check the recording report for saved frames. Try a lower frame rate or a different format, "
                            "and check the USB connection. The camera is still connected."
                        )
                    elif invalid == 1 or invalid % 30 == 0:
                        self.status.emit(f"Skipped damaged camera frame ({invalid:,} total): {e}")
                    continue
                if frame is None:
                    if time.monotonic() - last_received > 15:
                        self.status.emit(
                            "Waiting for camera frames. Check exposure, USB cable, and other camera apps."
                        )
                        last_received = time.monotonic()
                    continue
                last_frame, last_received = frame, time.monotonic()
                consecutive_invalid = 0
                total += 1
                if writer:
                    try:
                        if frame.bits != writer.bits or frame.pattern != writer.pattern:
                            raise ValueError("Camera changed pixel format during recording.")
                        writer.write(frame.pixels)
                    except Exception as e:
                        end_recording("recording_error", str(e))
                        hint = (
                            "Check available disk space and the save location."
                            if isinstance(e, OSError)
                            else "The camera pixels no longer match this recording's format."
                        )
                        self.failed.emit(
                            f"Recording stopped: {e}\nFile: {recording_path}\n"
                            f"{hint} Details are in the report beside the SER file."
                        )
                    if writer and record_limit > 0 and time.monotonic() - recording_started >= record_limit:
                        end_recording("time_limit")
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
                            "invalid": invalid,
                            "shape": frame.pixels.shape,
                            "bits": frame.bits,
                            "pattern": frame.pattern,
                        },
                    )
                    last_preview = now
        except Exception as e:
            stop_reason, stop_error = "camera_error", str(e)
            self.failed.emit(f"Camera stopped: {e}. Check diagnostics for the recording's saved status.")
        finally:
            try:
                end_recording(stop_reason, stop_error, notify=False)
            except Exception as e:
                self.failed.emit(f"Recording finalization failed: {e}")
            if camera:
                try:
                    camera.close()
                except Exception as e:
                    self.status.emit(f"Camera cleanup: {e}")
