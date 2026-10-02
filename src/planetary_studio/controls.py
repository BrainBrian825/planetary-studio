"""Camera controls that preserve the driver's valid mode combinations."""

import math
from PySide6.QtCore import Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QSlider,
    QComboBox,
    QLabel,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QVBoxLayout,
)


def color_kind(mode):
    return "Mono" if mode.format == "MONO" else "Color"


class CameraModeSelector(QWidget):
    modeChanged = Signal(object)

    def __init__(self):
        super().__init__()
        self.modes, self.rates, self._mode = [], [], None
        self.resolution = QComboBox()
        self.color = QComboBox()
        self.depth = QComboBox()
        self.fps_slider = QSlider(Qt.Horizontal)
        self.fps_slider.setTickPosition(QSlider.TicksBelow)
        self.fps_slider.setTickInterval(1)
        self.fps_slider.setAccessibleName("Frame rate")
        self.fps_value = QLabel("— fps")
        self.fps_value.setMinimumWidth(68)
        self.info = QLabel("Connect a camera to choose capture settings.")
        self.info.setWordWrap(True)
        self.info.setProperty("secondary", True)
        form = QFormLayout()
        form.addRow("Resolution", self.resolution)
        form.addRow("Color", self.color)
        form.addRow("Bit depth", self.depth)
        row = QHBoxLayout()
        row.addWidget(self.fps_slider, 1)
        row.addWidget(self.fps_value)
        form.addRow("Frame rate", row)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addWidget(self.info)
        for combo in (self.resolution, self.color, self.depth):
            combo.currentIndexChanged.connect(self.refresh)
        self.fps_slider.valueChanged.connect(self.choose_rate)
        self.fps_slider.setToolTip(
            "Available camera rates for these settings. Actual FPS depends on exposure and transfer speed."
        )
        self.setEnabled(False)

    @staticmethod
    def refill(combo, choices, previous):
        with QSignalBlocker(combo):
            combo.clear()
            for text, value in choices:
                combo.addItem(text, value)
            # Qt compares opaque tuple data by identity; compare values in Python.
            index = next((i for i, (_, value) in enumerate(choices) if value == previous), 0)
            combo.setCurrentIndex(index)
        combo.setEnabled(len(choices) > 1)

    def set_modes(self, modes):
        self.modes = list(modes)
        if not self.modes:
            self._mode = None
            self.setEnabled(False)
            return
        first = self.modes[0]
        sizes = sorted({(m.width, m.height) for m in self.modes}, key=lambda s: s[0] * s[1], reverse=True)
        self.refill(self.resolution, [(f"{w} × {h}", (w, h)) for w, h in sizes], (first.width, first.height))
        self.refresh(preferred=first)
        self.setEnabled(True)

    def refresh(self, *_, preferred=None):
        if not self.modes:
            return
        size = self.resolution.currentData()
        candidates = [m for m in self.modes if (m.width, m.height) == size]
        previous_color = color_kind(preferred) if preferred else self.color.currentData()
        kinds = [kind for kind in ("Color", "Mono") if any(color_kind(m) == kind for m in candidates)]
        self.refill(self.color, [(kind, kind) for kind in kinds], previous_color)
        candidates = [m for m in candidates if color_kind(m) == self.color.currentData()]
        previous_bits = preferred.bits if preferred else self.depth.currentData()
        bits = sorted({m.bits for m in candidates})
        self.refill(self.depth, [(f"{depth}-bit", depth) for depth in bits], previous_bits)
        candidates = [m for m in candidates if m.bits == self.depth.currentData()]
        previous_rate = preferred.fps if preferred else self._mode.fps if self._mode else 30
        self.rates = sorted({m.fps for m in candidates})
        nearest = min(range(len(self.rates)), key=lambda i: abs(self.rates[i] - previous_rate))
        with QSignalBlocker(self.fps_slider):
            self.fps_slider.setRange(0, len(self.rates) - 1)
            self.fps_slider.setValue(nearest)
        self.fps_slider.setEnabled(len(self.rates) > 1)
        self.choose_rate(nearest)

    def choose_rate(self, index):
        if not self.rates:
            return
        rate = self.rates[index]
        size = self.resolution.currentData()
        choices = [
            m
            for m in self.modes
            if (m.width, m.height) == size
            and color_kind(m) == self.color.currentData()
            and m.bits == self.depth.currentData()
            and m.fps == rate
        ]
        # Prefer a raw mosaic to converted RGB when both describe the same choice.
        self._mode = min(choices, key=lambda m: m.format == "RGB")
        self.fps_value.setText(f"{rate:g} fps")
        label = (
            "Raw " + self._mode.format
            if self._mode.format in ("GRBG", "RGGB", "GBRG", "BGGR")
            else self._mode.format
        )
        self.info.setText(
            label + (" · one available rate" if len(self.rates) == 1 else " · camera-supported rates")
        )
        self.modeChanged.emit(self._mode)

    def selected_mode(self):
        return self._mode


class SliderControl(QWidget):
    valueChanged = Signal(float)

    def __init__(self, lo, hi, current, name):
        super().__init__()
        self.lo, self.hi = float(lo), float(hi)
        self.logarithmic = name.startswith("Exposure") and lo > 0 and hi / lo > 100
        integer = not name.startswith("Exposure") and float(lo).is_integer() and float(hi).is_integer()
        self.steps = max(1, min(10000, int(hi - lo))) if integer else 1000
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, self.steps)
        self.slider.setAccessibleName(name)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setDecimals(0 if integer else 2)
        self.spin.setSingleStep(1 if integer else 0.1)
        self.spin.setKeyboardTracking(False)
        self.spin.setAccessibleName(name + " exact value")
        self.spin.setMinimumWidth(90)
        self.spin.setMaximumWidth(130)
        self.slider.setEnabled(hi > lo)
        self.spin.setEnabled(hi > lo)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(75)
        self.timer.timeout.connect(lambda: self.valueChanged.emit(self.spin.value()))
        self.slider.valueChanged.connect(self.slider_changed)
        self.slider.sliderReleased.connect(self.commit)
        self.spin.valueChanged.connect(self.spin_changed)
        self.set_value(current)
        if self.logarithmic:
            self.slider.setToolTip(
                "Fine control for short exposures, with longer exposures toward the right. You can also type an exact value."
            )

    def position(self, value):
        if self.hi == self.lo:
            return 0
        value = max(self.lo, min(self.hi, value))
        fraction = (
            math.log(value / self.lo) / math.log(self.hi / self.lo)
            if self.logarithmic
            else (value - self.lo) / (self.hi - self.lo)
        )
        return round(fraction * self.steps)

    def value_at(self, position):
        fraction = position / self.steps
        return (
            self.lo * (self.hi / self.lo) ** fraction
            if self.logarithmic
            else self.lo + fraction * (self.hi - self.lo)
        )

    def set_value(self, value):
        with QSignalBlocker(self.spin), QSignalBlocker(self.slider):
            self.spin.setValue(value)
            self.slider.setValue(self.position(self.spin.value()))

    def slider_changed(self, position):
        with QSignalBlocker(self.spin):
            self.spin.setValue(self.value_at(position))
        self.timer.start()

    def spin_changed(self, value):
        with QSignalBlocker(self.slider):
            self.slider.setValue(self.position(value))
        self.timer.start()

    def commit(self):
        self.timer.stop()
        self.valueChanged.emit(self.spin.value())
