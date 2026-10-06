import ctypes as C
import json
import queue
import time

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from planetary_studio import workers
from planetary_studio.app import MainWindow
from planetary_studio.cameras import uvc
from planetary_studio.cameras.base import Camera, CameraError, Device, Frame, InvalidFrameError, Mode
from planetary_studio.ser import SerReader, SerWriter


class TestCamera(Camera):
    __test__ = False

    def __init__(self):
        self.calls, self.failures, self.received = [], queue.Queue(), 0

    def modes(self):
        return [Mode(16, 12, 30, "GRBG", 16), Mode(24, 18, 60, "RGB", 8), Mode(24, 18, 30, "MONO", 8)]

    def controls(self):
        return {"Gain": (0, 100, 10)}

    def start(self, mode):
        self.calls.append(("start", mode))
        if mode.fps == 1:
            raise CameraError("USB bandwidth unavailable")
        self.mode = mode

    def stop(self):
        self.calls.append("stop")

    def close(self):
        self.calls.append("close")

    def read(self, timeout=1000):
        time.sleep(0.005)
        if not self.failures.empty():
            failure = self.failures.get_nowait()
            if failure is None:
                return None
            raise failure
        self.received += 1
        shape = (self.mode.height, self.mode.width) + ((3,) if self.mode.format == "RGB" else ())
        return Frame(
            np.full(shape, 120, np.uint16 if self.mode.bits == 16 else np.uint8),
            self.mode.format,
            self.mode.bits,
        )


def wait_until(predicate, timeout=5):
    app = QApplication.instance() or QApplication([])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "Capture operation did not complete"


@pytest.fixture
def connected_camera(monkeypatch):
    app = QApplication.instance() or QApplication([])
    camera = TestCamera()
    monkeypatch.setattr(workers, "open_camera", lambda _: camera)
    worker = workers.CameraWorker(Device("Test", "1", "Test camera"))
    state = {"ready": False, "streaming": False, "recording": False, "previews": 0, "saved": [], "errors": []}
    worker.ready.connect(lambda *_: state.update(ready=True))
    worker.capture_state.connect(lambda stream, record: state.update(streaming=stream, recording=record))
    worker.preview.connect(lambda *_: state.update(previews=state["previews"] + 1))
    worker.recorded.connect(lambda path, count: state["saved"].append((path, count)))
    worker.failed.connect(state["errors"].append)
    worker.start()
    wait_until(lambda: state["ready"])
    try:
        yield camera, worker, state
    finally:
        worker.stop_event.set()
        assert worker.wait(5000)
        app.processEvents()


def begin_capture(camera, worker, state, index=0):
    previews = state["previews"]
    worker.command("start", camera.modes()[index])
    wait_until(lambda: state["streaming"] and state["previews"] > previews)


def test_stop_live_view_finalizes_recording_and_restarts_all_formats(connected_camera, tmp_path):
    camera, worker, state = connected_camera
    for index in range(3):
        begin_capture(camera, worker, state, index)
        path = tmp_path / f"mode-{index}.ser"
        worker.command("record", str(path), 0)
        wait_until(lambda: state["recording"])
        initial = camera.received
        wait_until(lambda initial=initial: camera.received > initial + 3)
        worker.command("stop")
        wait_until(lambda: not state["streaming"])
        assert worker.isRunning() and "close" not in camera.calls
        with SerReader(path) as reader:
            mode = camera.modes()[index]
            assert (reader.width, reader.height, reader.bits, reader.pattern) == (
                mode.width,
                mode.height,
                mode.bits,
                mode.format,
            )
            assert reader.count >= 3
            np.testing.assert_array_equal(reader.read(reader.count - 1), 120)
        report = json.loads(path.with_suffix(".ser.json").read_text())
        assert report["stop_reason"] == "live_view_stopped" and report["finalized"]
    assert not state["errors"] and len(state["saved"]) == 3


@pytest.mark.parametrize("index", [0, 1])
def test_occasionally_damaged_frames_do_not_end_recording(connected_camera, tmp_path, index):
    camera, worker, state = connected_camera
    begin_capture(camera, worker, state, index)
    path = tmp_path / "damaged.ser"
    worker.command("record", str(path), 0.3)
    wait_until(lambda: state["recording"])
    for _ in range(4):
        camera.failures.put(InvalidFrameError("Incomplete raw frame: 100 of 384 bytes"))
    wait_until(lambda: bool(state["saved"]))
    assert worker.isRunning() and state["streaming"] and not state["errors"]
    with SerReader(path) as reader:
        assert reader.count > 4 and reader.bits == camera.modes()[index].bits
        assert reader.timestamp(reader.count - 1) is not None
    report = json.loads(path.with_suffix(".ser.json").read_text())
    assert report["stop_reason"] == "time_limit" and report["invalid_frames_skipped"] == 4
    assert "Incomplete" in report["last_frame_error"] and report["elapsed_seconds"] >= 0.3


def test_persistent_damaged_frames_save_valid_frames_and_allow_mode_retry(connected_camera, tmp_path):
    camera, worker, state = connected_camera
    begin_capture(camera, worker, state)
    path = tmp_path / "persistent.ser"
    worker.command("record", str(path), 0)
    wait_until(lambda: state["recording"])
    wait_until(lambda: camera.received > 4)
    for _ in range(worker.MAX_INVALID_FRAMES):
        camera.failures.put(InvalidFrameError("Incomplete 16-bit BA16 frame"))
    wait_until(lambda: not state["streaming"])
    assert worker.isRunning() and "close" not in camera.calls and state["errors"]
    with SerReader(path) as reader:
        assert reader.count > 0
    report = json.loads(path.with_suffix(".ser.json").read_text())
    assert report["stop_reason"] == "invalid_frames" and report["invalid_frames_skipped"] == 30
    assert "BA16" in report["error"]
    begin_capture(camera, worker, state, 1)


def test_disk_write_failure_keeps_camera_live_and_saves_complete_frames(
    connected_camera, tmp_path, monkeypatch
):
    camera, worker, state = connected_camera
    original = SerWriter.write

    def disk_full(writer, frame):
        if writer.count == 3:
            # Simulate a partially written frame before the disk reports failure.
            writer._file.write(b"partial frame")
            raise OSError(28, "No space left on device")
        original(writer, frame)

    monkeypatch.setattr(SerWriter, "write", disk_full)
    begin_capture(camera, worker, state)
    path = tmp_path / "full-disk.ser"
    worker.command("record", str(path), 0)
    wait_until(lambda: bool(state["saved"]))
    assert worker.isRunning() and state["streaming"] and not state["recording"]
    assert "No space" in state["errors"][0]
    with SerReader(path) as reader:
        assert reader.count == 3 and reader.timestamp(2) is not None
        assert path.stat().st_size == reader.data_end + 24
    report = json.loads(path.with_suffix(".ser.json").read_text())
    assert report["stop_reason"] == "recording_error" and "No space" in report["error"]


def test_recording_time_limit_works_even_when_camera_sends_no_frames(connected_camera, tmp_path):
    camera, worker, state = connected_camera
    begin_capture(camera, worker, state)
    path = tmp_path / "no-frames.ser"
    worker.command("record", str(path), 0.2)
    wait_until(lambda: state["recording"])
    for _ in range(150):
        camera.failures.put(None)
    wait_until(lambda: bool(state["saved"]))
    report = json.loads(path.with_suffix(".ser.json").read_text())
    assert report["stop_reason"] == "time_limit" and 0.2 <= report["elapsed_seconds"] < 1.5


def test_failed_start_and_recording_file_error_do_not_disconnect_camera(connected_camera, tmp_path):
    camera, worker, state = connected_camera
    worker.command("start", Mode(16, 12, 1, "GRBG", 16))
    wait_until(lambda: bool(state["errors"]))
    assert worker.isRunning() and not state["streaming"]
    begin_capture(camera, worker, state)
    path = tmp_path / "exists.ser"
    path.write_bytes(b"keep this")
    worker.command("record", str(path), 1)
    wait_until(lambda: len(state["errors"]) == 2)
    assert worker.isRunning() and state["streaming"] and path.read_bytes() == b"keep this"


def test_fatal_camera_error_is_saved_in_recording_report(connected_camera, tmp_path):
    camera, worker, state = connected_camera
    begin_capture(camera, worker, state)
    path = tmp_path / "unplugged.ser"
    worker.command("record", str(path), 60)
    wait_until(lambda: state["recording"])
    wait_until(lambda: camera.received > 4)
    camera.failures.put(CameraError("USB device removed"))
    wait_until(lambda: not worker.isRunning())
    with SerReader(path) as reader:
        assert reader.count > 0
    report = json.loads(path.with_suffix(".ser.json").read_text())
    assert report["stop_reason"] == "camera_error" and report["error"] == "USB device removed"
    assert report["requested_seconds"] == 60 and report["elapsed_seconds"] < 60


def test_finalization_failure_is_reported_without_disconnect(connected_camera, tmp_path, monkeypatch):
    camera, worker, state = connected_camera
    original = SerWriter.close

    def failed_sync(writer):
        original(writer)
        raise OSError("Disk sync failed")

    monkeypatch.setattr(SerWriter, "close", failed_sync)
    begin_capture(camera, worker, state)
    path = tmp_path / "sync-failure.ser"
    worker.command("record", str(path), 0.1)
    wait_until(lambda: bool(state["errors"]))
    assert worker.isRunning() and state["streaming"] and not state["recording"] and not state["saved"]
    report = json.loads(path.with_suffix(".ser.json").read_text())
    assert not report["finalized"] and "Disk sync failed" in report["finalization_error"]


def test_ser_file_is_closed_even_if_final_sync_fails(tmp_path, monkeypatch):
    import os

    writer = SerWriter(tmp_path / "sync.ser", (12, 16), 16, "GRBG")
    writer.write(np.zeros((12, 16), np.uint16))

    def failed_sync(_):
        raise OSError("Disk sync failed")

    monkeypatch.setattr(os, "fsync", failed_sync)
    with pytest.raises(OSError, match="Disk sync"):
        writer.close()
    assert writer._file.closed


def test_capture_controls_unlock_only_after_stop_acknowledgement():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    commands = []

    class Worker:
        def command(self, *items):
            commands.append(items)

    window.camera_worker = Worker()
    try:
        window.camera_ready(TestCamera().modes(), {})
        window.start_preview()
        assert not window.mode_selector.isEnabled() and not window.record_button.isEnabled()
        window.camera_state_changed(True, False)
        assert window.preview_button.text() == "Stop live view" and window.preview_button.isEnabled()
        image = np.zeros((12, 16, 3), np.float32)
        stats = dict(shape=(12, 16), bits=16, pattern="GRBG", fps=30, recorded=0, dropped=0, recording=False)
        window.camera_preview(image, stats)
        assert window.record_button.isEnabled()
        window.start_preview()
        window.camera_preview(image, stats)  # A queued old preview must not re-enable recording.
        assert not window.record_button.isEnabled() and not window.mode_selector.isEnabled()
        window.camera_state_changed(False, False)
        assert window.mode_selector.isEnabled() and not window.record_button.isEnabled()
        assert commands[-1] == ("stop",)
        window.mode_selector.depth.setCurrentIndex(window.mode_selector.depth.findData(8))
        window.start_preview()
        assert commands[-1][0] == "start"
    finally:
        window.camera_worker = None
        window.close()
        app.processEvents()


@pytest.mark.parametrize(
    "code,bits,channels", [("GRBG", 8, 1), ("BA16", 16, 1), ("YUY2", 8, 2), ("NV12", 8, 1)]
)
def test_uvc_incomplete_frames_are_recoverable(code, bits, channels):
    camera = uvc.UvcCamera.__new__(uvc.UvcCamera)
    camera.mode = Mode(8, 6, 30, "GRBG", bits, {"fourcc": code})
    camera.handle, camera.buffer = 1, C.create_string_buffer(4096)

    class Library:
        def ps_read(self, handle, buffer, capacity, pointer, timeout):
            meta = C.cast(pointer, C.POINTER(uvc._Frame))[0]
            meta.width, meta.height, meta.bytes = 8, 6, 10
            return 1

    camera.lib = Library()
    with pytest.raises(InvalidFrameError, match="Incomplete"):
        camera.read()


@pytest.mark.parametrize("bits,code", [(8, "GRBG"), (16, "BA16")])
def test_uvc_padded_raw_rows_preserve_pixels_without_final_padding(bits, code):
    camera = uvc.UvcCamera.__new__(uvc.UvcCamera)
    camera.mode = Mode(8, 6, 30, "GRBG", bits, {"fourcc": code})
    camera.handle, camera.buffer = 1, C.create_string_buffer(4096)
    pixels = np.arange(48, dtype="u1" if bits == 8 else "<u2").reshape(6, 8)
    rows = [row.tobytes() for row in pixels]
    data = b"xx".join(rows)

    class Library:
        def ps_read(self, handle, buffer, capacity, pointer, timeout):
            C.memmove(buffer, data, len(data))
            meta = C.cast(pointer, C.POINTER(uvc._Frame))[0]
            meta.width, meta.height, meta.bytes, meta.step = 8, 6, len(data), 8 * (bits // 8) + 2
            return 1

    camera.lib = Library()
    frame = camera.read()
    assert frame.bits == bits and frame.pattern == "GRBG"
    np.testing.assert_array_equal(frame.pixels, pixels)


def test_diagnostics_survive_a_new_window_and_can_be_saved(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.log("Incomplete 16-bit BA16 frame")
    window.close()
    window = MainWindow()
    try:
        assert "Incomplete 16-bit BA16" in window.diagnostics.toPlainText()
        path = tmp_path / "diagnostics.txt"
        monkeypatch.setattr(window, "save_file", lambda *_: str(path))
        window.save_diagnostics()
        assert "Incomplete 16-bit BA16" in path.read_text()
    finally:
        window.close()
        app.processEvents()
