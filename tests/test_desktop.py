import os
import time
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from planetary_studio.app import MainWindow
from planetary_studio.cameras.simulator import discover, SimulatedCamera
from planetary_studio.workers import CameraWorker
from planetary_studio.ser import SerReader


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


def test_capture_worker_records_and_finalizes_ser(tmp_path):
    app = app_instance()
    worker = CameraWorker(discover()[0])
    state = {"ready": False, "preview": False, "saved": False, "error": ""}

    def ready(modes, controls):
        state["ready"] = True
        worker.command("start", modes[1])

    def preview(image, stats):
        if not state["preview"]:
            state["preview"] = True
            worker.command("record", str(tmp_path / "record.ser"), 0.35)

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
        assert reader.count >= 5 and reader.pattern == "RGB" and reader.bits == 16
