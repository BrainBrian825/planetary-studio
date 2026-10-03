import json
import os
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from planetary_studio.app import MainWindow
from planetary_studio.cameras.simulator import SimulatedCamera, discover
from planetary_studio.ser import SerReader
from planetary_studio.workers import CameraWorker


def app_instance():
    return QApplication.instance() or QApplication([])


def test_desktop_workflow_and_shutdown():
    app = app_instance()
    window = MainWindow()
    window.show()
    app.processEvents()
    assert window.pages.count() == 5
    assert window.device_combo.count() >= 1
    camera = SimulatedCamera()
    camera.start(camera.modes()[1])
    frame = camera.read()
    window.set_sharpen_image(frame.pixels.astype(np.float32) / 65535)
    window.nav.setCurrentRow(2)
    window.wavelet_spins[0].setValue(0.5)
    app.processEvents()
    assert window.original.shape == (240, 320, 3)
    assert window.finish_options()["gains"][0] == 0.5
    window.close()
    app.processEvents()


def test_ai_cleanup_controls_round_trip_and_old_presets_disable_cleanup():
    app = app_instance()
    window = MainWindow()
    try:
        assert window.finish_options()["ai_denoise_amount"] == 0
        window.ai_denoise_amount.slider.setValue(35)
        window.ai_denoise_noise.slider.setValue(4)
        options = window.finish_options()
        assert options["ai_denoise_amount"] == 0.35 and options["ai_denoise_noise"] == 4
        window.reset_sharpen()
        assert window.finish_options()["ai_denoise_amount"] == 0
        window.apply_finish_options(options)
        assert window.finish_options() == options
        window.apply_finish_options({"gains": [0.5, 0, 0, 0, 0, 0]})
        assert window.finish_options()["ai_denoise_amount"] == 0
    finally:
        window.close()
        app.processEvents()


def test_live_ai_preview_matches_finished_export_and_preset(tmp_path, monkeypatch):
    from planetary_studio.cameras.simulator import planet_image
    from planetary_studio.imaging import normalized, read_image
    from planetary_studio.processing import finish_image

    app = app_instance()
    window = MainWindow()
    try:
        image = planet_image(80, 64).astype(np.float32)
        image += np.random.default_rng(47).normal(0, 0.015, image.shape).astype(np.float32)
        window.set_sharpen_image(image)
        window.wavelet_spins[0].setValue(0.8)
        window.ai_denoise_amount.slider.setValue(40)
        window.ai_denoise_noise.slider.setValue(5)
        options = window.finish_options()
        expected = finish_image(image, **options)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not np.allclose(window.finished_image, expected, atol=1e-7):
            app.processEvents()
            time.sleep(0.01)
        assert np.allclose(window.finished_image, expected, atol=1e-7)

        preset = tmp_path / "cleanup.json"
        monkeypatch.setattr(window, "save_file", lambda *_: str(preset))
        window.save_preset()
        window.reset_sharpen()
        monkeypatch.setattr(window, "open_file", lambda *_: str(preset))
        window.load_preset()
        assert window.finish_options() == options

        output = tmp_path / "finished.tif"
        monkeypatch.setattr(window, "image_save_path", lambda *_: str(output))
        window.save_finished()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (window.job and window.job.isRunning()):
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert output.is_file()
        assert np.max(np.abs(normalized(read_image(output)) - expected)) < 2e-5
        assert json.loads((tmp_path / "finished.tif.json").read_text())["sharpening"] == options
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("replace", [False, True])
def test_capture_worker_records_and_finalizes_ser(tmp_path, replace):
    app = app_instance()
    worker = CameraWorker(discover()[0])
    state = {"ready": False, "preview": False, "saved": False, "error": ""}
    if replace:
        (tmp_path / "record.ser").write_bytes(b"previous recording")

    def ready(modes, controls):
        state["ready"] = True
        worker.command("start", modes[1])

    def preview(image, stats):
        if not state["preview"]:
            state["preview"] = True
            worker.command("record", str(tmp_path / "record.ser"), 0.35, replace)

    worker.ready.connect(ready)
    worker.preview.connect(preview)
    worker.recorded.connect(lambda *_: state.update(saved=True))
    worker.failed.connect(lambda error: state.update(error=error))
    worker.start()
    deadline = time.monotonic() + 8
    try:
        while time.monotonic() < deadline and not state["saved"] and not state["error"]:
            app.processEvents()
            time.sleep(0.01)
    finally:
        worker.stop_event.set()
        assert worker.wait(5000)
        app.processEvents()
    assert not state["error"] and state["ready"] and state["saved"]
    with SerReader(tmp_path / "record.ser") as reader:
        # A timed recording can contain fewer frames on a busy hosted runner.
        assert reader.count >= 1 and reader.pattern == "RGB" and reader.bits == 16
        metadata = json.loads((tmp_path / "record.ser.json").read_text())
        assert metadata["frames"] == reader.count
        assert metadata["elapsed_seconds"] >= 0.35
        assert reader.read(reader.count - 1).shape == (240, 320, 3)
