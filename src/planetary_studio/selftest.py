"""End-to-end smoke test used on source and every packaged platform build."""

from pathlib import Path
import json
import tempfile
import sys
import numpy as np
import cv2
from . import __version__
from .cameras.simulator import planet_image
from .ser import SerWriter, SerReader
from .imaging import read_image, normalized, write_image
from .processing import StackOptions, stack_source, finish_image
from .preview import preview_frame, preview_sample


def generate_demo(path, frames=60, width=256, height=192):
    truth = planet_image(width, height)
    rng = np.random.default_rng(821)
    with SerWriter(path, truth.shape, 16, "RGB", instrument="Simulated planetary sequence") as writer:
        for i in range(frames):
            dx, dy = rng.normal(0, 1.8, 2)
            image = cv2.warpAffine(truth, np.float32([[1, 0, dx], [0, 1, dy]]), (width, height))
            image = cv2.GaussianBlur(image, (0, 0), 0.35 + (i % 5) * 0.3)
            image += rng.normal(0, 0.018, image.shape)
            writer.write(np.rint(np.clip(image, 0, 1) * 65535).astype(np.uint16))
    return truth


def run_self_test(report_path=None):
    checks = []
    if sys.platform != "win32":
        from .cameras.uvc import _library

        _library()
        checks.append("Native UVC bridge and bundled USB dependency loading")
    with tempfile.TemporaryDirectory(prefix="planetary-studio-selftest-") as temp:
        directory = Path(temp)
        path = directory / "input.ser"
        truth = generate_demo(path, 24, 128, 96)
        with SerReader(path) as reader:
            assert reader.count == 24 and reader.bits == 16 and reader.pattern == "RGB"
            assert reader.timestamp(23) >= reader.timestamp(0)
        checks.append("SER recording, raw depth, and timestamps")
        result = stack_source(path, StackOptions(keep_percent=50, center_object=False, alignment_size=32))
        assert result.image.shape == truth.shape and np.isfinite(result.image).all()
        assert len(result.selected) == 12 and len(result.alignment_points) > 0
        checks.append("Quality ranking, registration, local alignment, stacking")
        frame = preview_frame(path, 3, StackOptions(crop_width=64, crop_height=64))
        assert frame.image.shape == (64, 64, 3)
        sample = preview_sample(path, StackOptions(keep_percent=50, alignment_size=32), 8)
        assert sample.input_frames == 8 and len(sample.sample_indices) == 8
        assert len(sample.selected) == 4
        checks.append("Selected-frame settings preview and limited sample stacking")
        finished = finish_image(result.image, gains=(0.4, 0.2, 0.1, 0, 0, 0), rl_iterations=2)
        assert finished.min() >= 0 and finished.max() <= 1
        checks.append("Wavelet sharpening and deconvolution")
        for ext in ("tif", "png", "fits"):
            output = directory / ("result." + ext)
            write_image(output, finished)
            restored = normalized(read_image(output))
            assert restored.shape == finished.shape
            assert np.max(np.abs(restored - finished)) < 4e-5
        checks.append("16-bit TIFF/PNG and float FITS exports")
        from PySide6.QtWidgets import QApplication
        from . import app as desktop
        from PySide6.QtCore import QSettings

        app = QApplication.instance() or QApplication([])
        original_settings = desktop.QSettings
        desktop.QSettings = lambda *_: QSettings(str(directory / "settings.ini"), QSettings.IniFormat)
        try:
            window = desktop.MainWindow()
        finally:
            desktop.QSettings = original_settings
        assert not window.windowIcon().isNull()
        from .cameras.simulator import SimulatedCamera

        simulator = SimulatedCamera()
        modes = simulator.modes()
        window.camera_ready(modes, simulator.controls())
        assert window.mode_selector.selected_mode() is modes[0]
        window.mode_selector.color.setCurrentIndex(window.mode_selector.color.findData("Mono"))
        window.mode_selector.depth.setCurrentIndex(window.mode_selector.depth.findData(8))
        window.mode_selector.fps_slider.setValue(window.mode_selector.fps_slider.maximum())
        selected = window.mode_selector.selected_mode()
        assert selected.format == "MONO" and selected.bits == 8 and selected.fps == 60
        checks.append("Logo loading, independent capture selectors, and sensor slider widgets")
        window.resize(1240, 830)
        window.set_source(str(path))
        window.stack_done(result)
        window.nav.setCurrentRow(2)
        window.show()
        app.processEvents()
        window.close()
        app.processEvents()
        checks.append("Desktop startup, input loading, stack-to-sharpen handoff, shutdown")
    report = {"passed": True, "version": __version__, "checks": checks}
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf8")
    return report
