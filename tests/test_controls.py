import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal, QSettings
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QWidget
from planetary_studio.cameras.base import Mode
from planetary_studio.cameras.simulator import SimulatedCamera
from planetary_studio.controls import CameraModeSelector, SliderControl
from planetary_studio.theme import ThemeController


def application():
    return QApplication.instance() or QApplication([])


def test_camera_choices_never_create_unsupported_combinations():
    app = application()
    modes = [
        Mode(640, 480, 90, "GRBG", 8, {"interval": 111111}),
        Mode(640, 480, 30, "GRBG", 8),
        Mode(640, 480, 30, "RGB", 8),
        Mode(640, 480, 15, "GRBG", 16),
        Mode(640, 480, 60, "MONO", 8),
        Mode(1280, 960, 20, "GRBG", 16),
    ]
    selector = CameraModeSelector()
    selector.set_modes(modes)
    assert selector.selected_mode() is modes[0]
    assert selector.rates == [30, 90]
    selector.fps_slider.setValue(0)
    assert selector.selected_mode() is modes[1]  # Raw is preferred to converted RGB.
    selector.depth.setCurrentIndex(selector.depth.findData(16))
    assert selector.selected_mode() is modes[3]
    assert selector.rates == [15] and not selector.fps_slider.isEnabled()
    selector.color.setCurrentIndex(selector.color.findData("Mono"))
    assert selector.selected_mode() is modes[4]
    assert selector.depth.currentData() == 8 and selector.rates == [60]
    selector.resolution.setCurrentIndex(selector.resolution.findText("1280 × 960"))
    assert selector.selected_mode() is modes[5]
    assert selector.color.currentData() == "Color" and selector.depth.currentData() == 16
    assert selector.selected_mode().details == modes[5].details
    app.processEvents()


def test_exposure_slider_reaches_driver_limits_and_preserves_exact_input():
    app = application()
    control = SliderControl(0.1, 30000, 10, "Exposure (ms)")
    values = []
    control.valueChanged.connect(values.append)
    control.slider.setValue(control.slider.minimum())
    control.commit()
    assert values[-1] == 0.1
    control.slider.setValue(control.slider.maximum())
    control.commit()
    assert values[-1] == 30000
    control.spin.setValue(12.34)
    control.commit()
    assert values[-1] == 12.34
    control.slider.setValue(500)
    assert abs(control.spin.value() - (0.1 * 30000) ** 0.5) < 0.01
    control.timer.stop()
    app.processEvents()


def test_gain_slider_uses_integer_native_values():
    app = application()
    control = SliderControl(100, 383, 100, "Gain")
    values = []
    control.valueChanged.connect(values.append)
    control.slider.setValue(37)
    control.commit()
    assert values[-1] == 137 and control.spin.value() == 137
    control.spin.setValue(350)
    assert control.slider.value() == 250
    control.timer.stop()
    app.processEvents()


def test_system_appearance_follows_changes_and_manual_override_persists(tmp_path):
    app = application()

    class SystemHints(QObject):
        colorSchemeChanged = Signal(object)
        scheme = Qt.ColorScheme.Light

        def colorScheme(self):
            return self.scheme

        def change(self, scheme):
            self.scheme = scheme
            self.colorSchemeChanged.emit(scheme)

    hints = SystemHints()
    settings = QSettings(str(tmp_path / "appearance.ini"), QSettings.IniFormat)
    window = QWidget()
    theme = ThemeController(window, settings, hints)
    assert theme.mode == "system" and theme.effective_mode == "light"
    hints.change(Qt.ColorScheme.Dark)
    assert theme.effective_mode == "dark" and window.palette().color(QPalette.Window).lightness() < 60
    theme.set_mode("light")
    hints.change(Qt.ColorScheme.Dark)
    assert theme.effective_mode == "light" and window.palette().color(QPalette.Window).lightness() > 200
    reopened = ThemeController(QWidget(), settings, hints)
    assert reopened.mode == "light"
    theme.set_mode("system")
    assert theme.effective_mode == "dark"
    hints.change(Qt.ColorScheme.Light)
    assert theme.effective_mode == "light"
    app.processEvents()


def test_simulator_honors_mono_depth_and_rate_selections():
    app = application()
    camera = SimulatedCamera()
    selector = CameraModeSelector()
    selector.set_modes(camera.modes())
    selector.color.setCurrentIndex(selector.color.findData("Mono"))
    selector.depth.setCurrentIndex(selector.depth.findData(8))
    selector.fps_slider.setValue(selector.fps_slider.maximum())
    mode = selector.selected_mode()
    camera.start(mode)
    frame = camera.read()
    assert mode.fps == 60 and frame.bits == 8 and frame.pattern == "MONO"
    assert frame.pixels.ndim == 2 and frame.pixels.dtype == np.uint8
    camera.close()
    app.processEvents()
