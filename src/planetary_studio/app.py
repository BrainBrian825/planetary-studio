from dataclasses import asdict
from pathlib import Path
import json
import time
import numpy as np
from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QStackedWidget,
    QListWidget,
    QSplitter,
    QGroupBox,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QDialog,
    QTextBrowser,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QSlider,
    QPlainTextEdit,
)
from . import __version__
from .cameras import discover
from .imaging import normalized, read_image, write_image
from .processing import StackOptions, stack_source, finish_image, export_prepared, create_master, Cancelled
from .sources import open_source
from .widgets import ImageView, Histogram, QualityPlot
from .workers import TaskWorker, CameraWorker

STYLE = """
QWidget { background: #151e2d; color: #e8edf5; font-size: 13px; }
QMainWindow, QStackedWidget { background: #101722; }
QGroupBox { border: 1px solid #2b3a50; border-radius: 9px; margin-top: 14px; padding: 15px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #b2c7e0; }
QPushButton { background: #26364d; border: 1px solid #364b67; padding: 9px 13px; border-radius: 6px; }
QPushButton:hover { background: #334965; }
QPushButton:disabled { color: #64748b; background: #1b2636; border-color: #253348; }
QPushButton[primary="true"] { background: #68d7df; color: #101722; border-color: #68d7df; font-weight: 700; }
QPushButton[primary="true"]:hover { background: #95e7ec; }
QPushButton[primary="true"]:disabled { background: #244d58; color: #839aa5; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit { background: #0f1724; border: 1px solid #33465f; border-radius: 5px; padding: 6px; selection-background-color: #376170; }
QComboBox::drop-down { width: 22px; border: 0; }
QListWidget { background: #111a28; border: 0; padding: 12px; font-size: 15px; outline: 0; }
QListWidget::item { padding: 16px 10px; border-radius: 6px; margin: 3px 0; }
QListWidget::item:selected { background: #243b50; color: #89e1e7; }
QProgressBar { border: 1px solid #2b3a50; border-radius: 4px; text-align: center; height: 19px; }
QProgressBar::chunk { background: #377e92; }
QScrollArea, QGraphicsView, QTableWidget { border: 1px solid #26374d; border-radius: 6px; }
QHeaderView::section { background: #243248; border: 0; padding: 8px; }
QCheckBox { spacing: 8px; }
QSlider::groove:horizontal { height: 4px; background: #33465f; border-radius: 2px; }
QSlider::handle:horizontal { background: #68d7df; width: 14px; margin: -5px 0; border-radius: 6px; }
QTextBrowser { background: #111a28; padding: 12px; border: 0; }
QStatusBar { background: #0f1724; color: #a3b7cf; }
"""


def button(text, callback=None, primary=False):
    b = QPushButton(text)
    b.setProperty("primary", primary)
    if callback:
        b.clicked.connect(callback)
    return b


def group(title):
    box = QGroupBox(title)
    box.setLayout(QVBoxLayout())
    return box


def number(lo, hi, value, decimals=2, suffix=""):
    spin = QDoubleSpinBox()
    spin.setRange(lo, hi)
    spin.setDecimals(decimals)
    spin.setValue(value)
    spin.setSuffix(suffix)
    return spin


def integer(lo, hi, value):
    spin = QSpinBox()
    spin.setRange(lo, hi)
    spin.setValue(value)
    return spin


def page_layout(title, subtitle):
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(22, 18, 22, 18)
    heading = QLabel(title)
    heading.setStyleSheet("font-size: 26px; font-weight: 700;")
    layout.addWidget(heading)
    hint = QLabel(subtitle)
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #93a8c3; padding-bottom: 8px;")
    layout.addWidget(hint)
    return page, layout


def scroll_panel(widget):
    panel = QScrollArea()
    panel.setWidgetResizable(True)
    panel.setWidget(widget)
    panel.setMinimumWidth(320)
    panel.setMaximumWidth(420)
    return panel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Planetary Studio")
        self.resize(1240, 830)
        self.setMinimumSize(920, 640)
        self.settings = QSettings("PlanetaryStudio", "PlanetaryStudio")
        self.camera_settings = json.loads(self.settings.value("camera_settings", "{}"))
        self.camera_worker, self.job, self.scan_worker = None, None, None
        self.devices, self.source_path, self.result = [], None, None
        self.original, self.finished_image, self.last_capture_image = None, None, None
        self.last_recording = ""
        self.finish_generation = 0
        self.batch_paths = []
        self.batch_directory = str(Path.home() / "Pictures/Planetary Studio")
        self._project_path = None
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.nav = QListWidget()
        self.nav.setFixedWidth(190)
        for text in ("Capture", "Prepare & Stack", "Sharpen", "Batch Queue", "Camera Support"):
            self.nav.addItem(text)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.build_capture())
        self.pages.addWidget(self.build_stack())
        self.pages.addWidget(self.build_sharpen())
        self.pages.addWidget(self.build_batch())
        self.pages.addWidget(self.build_support())
        layout.addWidget(self.nav)
        layout.addWidget(self.pages, 1)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.setCentralWidget(central)
        self.setStyleSheet(STYLE)
        self.statusBar().showMessage(f"Planetary Studio {__version__} · Capture → Prepare → Stack → Sharpen")
        menu = self.menuBar().addMenu("File")
        for text, callback, shortcut in [
            ("Open recording or images…", self.pick_source, "Ctrl+O"),
            ("Open project…", self.load_project, "Ctrl+Shift+O"),
            ("Save project…", self.save_project, "Ctrl+S"),
            ("Export finished image…", self.save_finished, "Ctrl+E"),
            ("Camera settings…", self.configure_cameras, ""),
            ("Quit", self.close, "Ctrl+Q"),
        ]:
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(callback)
            menu.addAction(action)
        help_menu = self.menuBar().addMenu("Help")
        about = QAction("Getting started", self)
        about.triggered.connect(lambda: self.nav.setCurrentRow(4))
        help_menu.addAction(about)
        self.sharpen_timer = QTimer(self)
        self.sharpen_timer.setSingleShot(True)
        self.sharpen_timer.setInterval(250)
        self.sharpen_timer.timeout.connect(self.update_sharpen)
        self.sharpen_worker = None

    def error(self, message):
        self.log(message)
        QMessageBox.warning(self, "Planetary Studio", message)

    def log(self, message):
        self.statusBar().showMessage(message, 20000)
        if hasattr(self, "diagnostics"):
            self.diagnostics.appendPlainText(time.strftime("%H:%M:%S") + "  " + message)

    def build_capture(self):
        page, layout = page_layout(
            "Capture the sky",
            "Connect a camera, choose a sensor mode, and record raw frames. Scroll to zoom; double-click the image to fit.",
        )
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        self.capture_view = ImageView("Choose a camera or use the simulator")
        self.capture_histogram = Histogram()
        self.capture_stats = QLabel("No camera connected")
        ll.addWidget(self.capture_view, 1)
        ll.addWidget(self.capture_histogram)
        ll.addWidget(self.capture_stats)
        panel = QWidget()
        pl = QVBoxLayout(panel)
        camera = group("Camera")
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(260)
        camera.layout().addWidget(self.device_combo)
        row = QHBoxLayout()
        self.scan_button = button("Scan cameras", self.scan_cameras)
        row.addWidget(self.scan_button)
        row.addWidget(button("Settings…", self.configure_cameras))
        camera.layout().addLayout(row)
        self.connect_button = button("Connect", self.connect_camera, True)
        camera.layout().addWidget(self.connect_button)
        self.mode_combo = QComboBox()
        self.mode_combo.setEnabled(False)
        camera.layout().addWidget(self.mode_combo)
        self.preview_button = button("Start live preview", self.start_preview, True)
        self.preview_button.setEnabled(False)
        camera.layout().addWidget(self.preview_button)
        pl.addWidget(camera)
        self.controls_box = group("Sensor controls")
        self.controls_form = QFormLayout()
        self.controls_box.layout().addLayout(self.controls_form)
        pl.addWidget(self.controls_box)
        recording = group("Recording")
        form = QFormLayout()
        self.record_duration = number(0, 3600, 60, 0, " seconds")
        self.record_duration.setToolTip("0 records until you press Stop recording.")
        form.addRow("Time limit", self.record_duration)
        recording.layout().addLayout(form)
        self.record_button = button("Record SER…", self.record, True)
        self.record_button.setEnabled(False)
        recording.layout().addWidget(self.record_button)
        self.use_recording_button = button("Send last recording to Stack", self.use_recording)
        self.use_recording_button.setEnabled(False)
        recording.layout().addWidget(self.use_recording_button)
        self.snapshot_button = button("Save snapshot…", self.snapshot)
        self.snapshot_button.setEnabled(False)
        recording.layout().addWidget(self.snapshot_button)
        note = QLabel(
            "SER preserves raw Bayer, monochrome, or RGB pixels at 8 or 16 bits. Preview color conversion does not alter raw recordings."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #93a8c3;")
        recording.layout().addWidget(note)
        pl.addWidget(recording)
        pl.addStretch()
        split.addWidget(left)
        split.addWidget(scroll_panel(panel))
        split.setStretchFactor(0, 1)
        layout.addWidget(split, 1)
        # The simulator is available without scanning physical cameras at launch.
        from .cameras.simulator import discover as simulated_devices

        self.devices = simulated_devices()
        for d in self.devices:
            self.device_combo.addItem(d.name, d)
        return page

    def scan_cameras(self):
        if self.camera_worker and self.camera_worker.isRunning():
            self.error("Disconnect the camera before scanning or changing driver settings.")
            return
        if self.scan_worker and self.scan_worker.isRunning():
            return
        self.scan_button.setEnabled(False)
        self.log("Scanning camera connections…")
        self.scan_worker = TaskWorker(
            lambda progress, cancel: discover(self.camera_settings, include_system=False)
        )
        self.scan_worker.succeeded.connect(self.cameras_found)
        self.scan_worker.failed.connect(self.error)
        self.scan_worker.finished.connect(lambda: self.scan_button.setEnabled(True))
        self.scan_worker.start()

    def cameras_found(self, result):
        devices, messages = result
        from .cameras.system import discover as system_discover

        try:
            devices += system_discover()
        except Exception as e:
            messages.append(str(e))
        self.devices = devices
        self.device_combo.clear()
        for d in devices:
            self.device_combo.addItem(f"{d.name} [{d.backend}]", d)
        preferred = next((i for i, d in enumerate(devices) if "NexImage" in d.name and d.backend == "UVC"), 0)
        self.device_combo.setCurrentIndex(preferred)
        for message in messages:
            self.log(message)
        self.log(f"{len(devices)} camera connections available")

    def connect_camera(self):
        if self.camera_worker and self.camera_worker.isRunning():
            self.camera_worker.stop_event.set()
            self.connect_button.setEnabled(False)
            self.log("Closing camera and finalizing any recording…")
            return
        device = self.device_combo.currentData()
        if not device:
            return
        self.connect_button.setEnabled(False)
        self.device_combo.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.camera_worker = CameraWorker(device)
        self.camera_worker.ready.connect(self.camera_ready)
        self.camera_worker.preview.connect(self.camera_preview)
        self.camera_worker.status.connect(self.log)
        self.camera_worker.failed.connect(self.error)
        self.camera_worker.recorded.connect(self.recording_saved)
        self.camera_worker.finished.connect(self.camera_closed)
        self.camera_worker.start()

    def camera_ready(self, modes, controls):
        self.mode_combo.clear()
        for mode in modes:
            self.mode_combo.addItem(mode.label, mode)
        self.mode_combo.setEnabled(True)
        self.preview_button.setEnabled(True)
        self.connect_button.setText("Disconnect")
        self.connect_button.setEnabled(True)
        while self.controls_form.rowCount():
            self.controls_form.removeRow(0)
        for name, (lo, hi, current) in controls.items():
            spin = number(lo, hi, current, 2)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(
                lambda value, n=name: (
                    self.camera_worker.command("control", n, value)
                    if self.camera_worker and self.camera_worker.isRunning()
                    else None
                )
            )
            self.controls_form.addRow(name, spin)
        self.log("Camera connected. Choose a mode and start live preview.")

    def start_preview(self):
        mode = self.mode_combo.currentData()
        if self.camera_worker and mode:
            self.camera_worker.command("start", mode)
            self.preview_button.setEnabled(False)
            self.mode_combo.setEnabled(False)

    def camera_preview(self, image, stats):
        self.last_capture_image = image
        self.capture_view.set_image(image)
        self.capture_histogram.set_image(image)
        self.capture_stats.setText(
            f"{stats['shape'][1]} × {stats['shape'][0]} · {stats['bits']}-bit {stats['pattern']} · "
            f"{stats['fps']:.1f} fps · {stats['recorded']:,} recorded · {stats['dropped']:,} dropped"
        )
        self.record_button.setEnabled(True)
        self.snapshot_button.setEnabled(True)
        self.record_button.setText("Stop recording" if stats["recording"] else "Record SER…")
        self.record_button.setProperty("recording", stats["recording"])

    def camera_closed(self):
        self.connect_button.setEnabled(True)
        self.connect_button.setText("Connect")
        self.device_combo.setEnabled(True)
        self.scan_button.setEnabled(True)
        self.preview_button.setEnabled(False)
        self.mode_combo.setEnabled(False)
        self.record_button.setEnabled(False)
        self.record_button.setProperty("recording", False)
        self.record_button.setText("Record SER…")
        self.log("Camera disconnected")

    def record(self):
        if self.record_button.property("recording"):
            self.camera_worker.command("stop_record")
            return
        default = str(Path.home() / "Movies" / ("planet-" + time.strftime("%Y%m%d-%H%M%S") + ".ser"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Record raw camera frames", default, "SER recording (*.ser)"
        )
        if not path:
            return
        if not path.lower().endswith(".ser"):
            path += ".ser"
        if Path(path).exists():
            self.error("Choose a new file name to preserve the existing recording.")
            return
        self.camera_worker.command("record", path, self.record_duration.value())
        self.record_button.setEnabled(False)

    def recording_saved(self, path, frames):
        self.last_recording = path
        self.use_recording_button.setEnabled(frames > 0)
        self.record_button.setProperty("recording", False)
        self.record_button.setText("Record SER…")

    def use_recording(self):
        if self.last_recording:
            self.set_source(self.last_recording)
            self.nav.setCurrentRow(1)

    def snapshot(self):
        if self.last_capture_image is not None:
            self.export_dialog(self.last_capture_image, "snapshot")

    def build_stack(self):
        page, layout = page_layout(
            "Prepare & Stack",
            "Rank the frames, correct camera drift, and combine the clearest detail. Large recordings are read from disk in passes.",
        )
        row = QHBoxLayout()
        row.addWidget(button("Open recording / images…", self.pick_source, True))
        row.addWidget(button("Open image folder…", self.pick_folder))
        self.source_label = QLabel("No input selected")
        self.source_label.setWordWrap(True)
        row.addWidget(self.source_label, 1)
        layout.addLayout(row)
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        self.stack_view = ImageView("Open a SER, video, or image sequence")
        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setEnabled(False)
        self.frame_slider.valueChanged.connect(self.inspect_frame)
        self.quality_plot = QualityPlot()
        ll.addWidget(self.stack_view, 1)
        ll.addWidget(self.frame_slider)
        ll.addWidget(self.quality_plot)
        panel = QWidget()
        pl = QVBoxLayout(panel)
        prep = group("Preparation")
        form = QFormLayout()
        self.target_combo = QComboBox()
        self.target_combo.addItems(["Planet · center object", "Moon / Sun · surface detail"])
        form.addRow("Target", self.target_combo)
        self.bayer_combo = QComboBox()
        self.bayer_combo.addItems(["AUTO", "MONO", "RGGB", "GRBG", "GBRG", "BGGR"])
        self.bayer_combo.setToolTip(
            "AUTO uses the SER header. For raw image sequences choose the sensor Bayer pattern."
        )
        form.addRow("Bayer pattern", self.bayer_combo)
        self.crop_w, self.crop_h = integer(0, 20000, 0), integer(0, 20000, 0)
        self.crop_w.setSpecialValueText("Full width")
        self.crop_h.setSpecialValueText("Full height")
        form.addRow("Crop width", self.crop_w)
        form.addRow("Crop height", self.crop_h)
        self.dark_path, self.flat_path = QLineEdit(), QLineEdit()
        for title, field in [("Dark master", self.dark_path), ("Flat master", self.flat_path)]:
            row = QHBoxLayout()
            row.addWidget(field, 1)
            row.addWidget(button("…", lambda checked=False, f=field: self.pick_calibration(f)))
            form.addRow(title, row)
        prep.layout().addLayout(form)
        self.hot_pixels = QCheckBox("Remove isolated hot pixels")
        self.brightness = QCheckBox("Normalize frame brightness")
        prep.layout().addWidget(self.hot_pixels)
        prep.layout().addWidget(self.brightness)
        prep.layout().addWidget(button("Export prepared SER…", self.prepare_export))
        prep.layout().addWidget(button("Build calibration master…", self.master_export))
        pl.addWidget(prep)
        stack = group("Lucky imaging")
        form = QFormLayout()
        self.keep_percent = number(0.1, 100, 25, 1, "%")
        form.addRow("Keep best", self.keep_percent)
        self.local_alignment = QCheckBox("Local alignment points")
        self.local_alignment.setChecked(True)
        form.addRow(self.local_alignment)
        self.ap_size = integer(16, 512, 64)
        self.ap_size.setSingleStep(16)
        form.addRow("Point size", self.ap_size)
        self.scale_combo = QComboBox()
        self.scale_combo.addItems(["Native size", "1.5× resample", "2× resample"])
        self.scale_combo.setToolTip("Lanczos enlargement; this is not drizzle reconstruction.")
        form.addRow("Output size", self.scale_combo)
        stack.layout().addLayout(form)
        self.stack_button = button("Align & Stack", self.run_stack, True)
        stack.layout().addWidget(self.stack_button)
        self.save_stack_button = button("Export stack…", self.save_stack)
        self.save_stack_button.setEnabled(False)
        stack.layout().addWidget(self.save_stack_button)
        stack.layout().addWidget(button("Continue to Sharpen →", lambda: self.nav.setCurrentRow(2)))
        pl.addWidget(stack)
        pl.addStretch()
        split.addWidget(left)
        split.addWidget(scroll_panel(panel))
        split.setStretchFactor(0, 1)
        layout.addWidget(split, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        row = QHBoxLayout()
        row.addWidget(self.progress, 1)
        self.cancel_button = button("Cancel", self.cancel_processing)
        self.cancel_button.setEnabled(False)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        return page

    def options(self):
        return StackOptions(
            keep_percent=self.keep_percent.value(),
            bayer=self.bayer_combo.currentText(),
            center_object=self.target_combo.currentIndex() == 0,
            crop_width=self.crop_w.value(),
            crop_height=self.crop_h.value(),
            dark_path=self.dark_path.text(),
            flat_path=self.flat_path.text(),
            remove_hot_pixels=self.hot_pixels.isChecked(),
            local_alignment=self.local_alignment.isChecked(),
            alignment_size=self.ap_size.value(),
            scale=(1.0, 1.5, 2.0)[self.scale_combo.currentIndex()],
            normalize_brightness=self.brightness.isChecked(),
        )

    def pick_source(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open recording or image sequence",
            "",
            "Imaging files (*.ser *.avi *.mov *.mp4 *.mkv *.png *.tif *.tiff *.fits *.fit *.fts *.jpg *.jpeg *.bmp);;All files (*)",
        )
        if paths:
            self.set_source(paths[0] if len(paths) == 1 else paths)

    def pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Open image sequence folder")
        if path:
            self.set_source(path)

    def set_source(self, path):
        try:
            source = open_source(path)
            try:
                frame = source.read(0)
                self.source_path = path
                from .imaging import debayer

                pattern = source.pattern if source.pattern not in ("RGB", "BGR") else None
                self.stack_view.set_image(normalized(debayer(frame, pattern), source.bits), stretch=True)
                self.frame_slider.blockSignals(True)
                self.frame_slider.setRange(0, source.count - 1)
                self.frame_slider.setValue(0)
                self.frame_slider.setEnabled(True)
                self.frame_slider.blockSignals(False)
                label = f"{len(path)} images" if isinstance(path, list) else Path(path).name
                self.source_label.setText(
                    f"{label} · {source.count:,} frames · {frame.shape[1]} × {frame.shape[0]}"
                )
            finally:
                source.close()
            self.nav.setCurrentRow(1)
        except Exception as e:
            self.error(str(e))

    def inspect_frame(self, index):
        if not self.source_path:
            return
        try:
            from .processing import Preprocessor

            source = open_source(self.source_path)
            try:
                image = Preprocessor(source, self.options()).read(index)
            finally:
                source.close()
            self.stack_view.set_image(image, stretch=True)
        except Exception as e:
            self.log(str(e))

    def pick_calibration(self, field):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select calibration master", "", "Images (*.fits *.fit *.fts *.tif *.tiff *.png)"
        )
        if path:
            field.setText(path)

    def start_job(self, function, args, succeeded, kwargs=None):
        if self.job and self.job.isRunning():
            self.error("A processing job is already running. Wait or cancel it first.")
            return False
        self.job = TaskWorker(function, *args, **(kwargs or {}))
        self.job.progress.connect(self.job_progress)
        self.job.succeeded.connect(succeeded)
        self.job.failed.connect(self.job_error)
        self.job.finished.connect(self.job_finished)
        self.stack_button.setEnabled(False)
        self.batch_start.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.batch_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.job.start()
        return True

    def job_progress(self, amount, message):
        self.progress.setValue(round(amount))
        self.batch_progress.setValue(round(amount))
        self.statusBar().showMessage(message)

    def job_error(self, message):
        if "cancelled" in message.lower():
            self.log(message)
        else:
            self.error(message)

    def job_finished(self):
        self.stack_button.setEnabled(True)
        self.batch_start.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.batch_cancel.setEnabled(False)

    def cancel_processing(self):
        if self.job:
            self.job.cancel.set()

    def run_stack(self):
        if self.source_path is None:
            self.error("Open a recording or image sequence first.")
            return
        self.start_job(stack_source, (self.source_path, self.options()), self.stack_done)

    def stack_done(self, result):
        self.result = result
        self.stack_view.set_image(result.image)
        self.quality_plot.set_scores(result.quality)
        self.save_stack_button.setEnabled(True)
        self.set_sharpen_image(result.image)
        self.log(
            f"Stack complete · {len(result.selected):,} of {result.input_frames:,} frames · {len(result.alignment_points)} local points"
        )

    def prepare_export(self):
        if self.source_path is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export prepared frames", "prepared.ser", "SER recording (*.ser)"
        )
        if path:
            if not path.lower().endswith(".ser"):
                path += ".ser"
            self.start_job(
                export_prepared,
                (self.source_path, path, self.options()),
                lambda _: self.log("Prepared recording saved"),
            )

    def master_export(self):
        if self.source_path is None:
            self.error("Open your dark or flat recording first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Build calibration master from current input", "master.fits", "FITS image (*.fits)"
        )
        if path:
            if not path.lower().endswith(".fits"):
                path += ".fits"
            self.start_job(
                create_master, (self.source_path, path), lambda _: self.log("Calibration master saved")
            )

    def save_stack(self):
        if self.result:
            path = self.image_save_path("stack")
            if path:
                try:
                    self.result.save(path, overwrite=True)
                    self.log("Stack and processing report saved")
                except Exception as e:
                    self.error(str(e))

    def build_sharpen(self):
        page, layout = page_layout(
            "Bring out the detail",
            "Six wavelet layers, noise control, deconvolution, and color adjustments. Export a 16-bit image or floating-point FITS.",
        )
        row = QHBoxLayout()
        row.addWidget(button("Open stacked image…", self.pick_sharpen))
        self.before_checkbox = QCheckBox("Show original")
        self.before_checkbox.toggled.connect(self.show_finished_preview)
        row.addWidget(self.before_checkbox)
        row.addStretch()
        row.addWidget(button("Reset adjustments", self.reset_sharpen))
        layout.addLayout(row)
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        self.sharpen_view = ImageView("Stack a recording or open an existing stacked image")
        self.sharpen_histogram = Histogram()
        ll.addWidget(self.sharpen_view, 1)
        ll.addWidget(self.sharpen_histogram)
        panel = QWidget()
        pl = QVBoxLayout(panel)
        wavelets = group("Wavelet detail")
        form = QFormLayout()
        self.wavelet_spins = []
        for i, label in enumerate(
            ("1 · Finest", "2 · Fine", "3 · Medium", "4 · Broad", "5 · Coarse", "6 · Largest")
        ):
            spin = number(-1, 8, 0, 2)
            spin.setSingleStep(0.1)
            spin.valueChanged.connect(self.schedule_sharpen)
            self.wavelet_spins.append(spin)
            form.addRow(label, spin)
        self.denoise_spin = number(0, 0.1, 0.003, 4)
        self.denoise_spin.setSingleStep(0.001)
        form.addRow("Noise threshold", self.denoise_spin)
        wavelets.layout().addLayout(form)
        pl.addWidget(wavelets)
        deconv = group("Deconvolution")
        form = QFormLayout()
        self.rl_iterations = integer(0, 100, 0)
        self.rl_sigma = number(0.3, 5, 1, 2, " px")
        form.addRow("RL iterations", self.rl_iterations)
        form.addRow("Blur radius", self.rl_sigma)
        deconv.layout().addLayout(form)
        pl.addWidget(deconv)
        color = group("Color & tone")
        form = QFormLayout()
        self.gamma_spin = number(0.1, 5, 1)
        self.saturation_spin = number(0, 3, 1)
        form.addRow("Gamma", self.gamma_spin)
        form.addRow("Saturation", self.saturation_spin)
        self.balance_spins = [number(0.1, 3, 1) for _ in range(3)]
        for name, spin in zip(("Red balance", "Green balance", "Blue balance"), self.balance_spins):
            form.addRow(name, spin)
        self.rgb_alignment = QCheckBox("Align RGB channels")
        form.addRow(self.rgb_alignment)
        color.layout().addLayout(form)
        pl.addWidget(color)
        for spin in [
            self.denoise_spin,
            self.rl_iterations,
            self.rl_sigma,
            self.gamma_spin,
            self.saturation_spin,
            *self.balance_spins,
        ]:
            spin.valueChanged.connect(self.schedule_sharpen)
        self.rgb_alignment.toggled.connect(self.schedule_sharpen)
        pl.addWidget(button("Export finished image…", self.save_finished, True))
        pl.addWidget(button("Save sharpening preset…", self.save_preset))
        pl.addWidget(button("Load sharpening preset…", self.load_preset))
        pl.addStretch()
        split.addWidget(left)
        split.addWidget(scroll_panel(panel))
        split.setStretchFactor(0, 1)
        layout.addWidget(split, 1)
        return page

    def finish_options(self):
        return {
            "gains": [s.value() for s in self.wavelet_spins],
            "denoise": self.denoise_spin.value(),
            "rl_iterations": self.rl_iterations.value(),
            "rl_sigma": self.rl_sigma.value(),
            "gamma": self.gamma_spin.value(),
            "saturation": self.saturation_spin.value(),
            "balance": [s.value() for s in self.balance_spins],
            "align_rgb": self.rgb_alignment.isChecked(),
        }

    def pick_sharpen(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open stacked image", "", "Images (*.tif *.tiff *.fits *.fit *.fts *.png)"
        )
        if path:
            try:
                self.set_sharpen_image(normalized(read_image(path)))
                self.nav.setCurrentRow(2)
            except Exception as e:
                self.error(str(e))

    def set_sharpen_image(self, image):
        self.original = np.asarray(image, dtype=np.float32).copy()
        self.finished_image = self.original.copy()
        self.show_finished_preview()
        self.schedule_sharpen()

    def schedule_sharpen(self, *_):
        self.finish_generation += 1
        if hasattr(self, "sharpen_timer"):
            self.sharpen_timer.start()

    def update_sharpen(self):
        if self.original is None:
            return
        if self.sharpen_worker and self.sharpen_worker.isRunning():
            self.sharpen_timer.start()
            return
        generation = self.finish_generation
        image, options = self.original.copy(), self.finish_options()
        self.sharpen_worker = TaskWorker(lambda progress, cancel: finish_image(image, **options))
        self.sharpen_worker.succeeded.connect(lambda result, g=generation: self.sharpen_done(result, g))
        self.sharpen_worker.failed.connect(self.error)
        self.sharpen_worker.start()

    def sharpen_done(self, image, generation):
        if generation == self.finish_generation:
            self.finished_image = image
            self.show_finished_preview()

    def show_finished_preview(self, *_):
        image = self.original if self.before_checkbox.isChecked() else self.finished_image
        if image is not None:
            self.sharpen_view.set_image(image)
            self.sharpen_histogram.set_image(image)

    def reset_sharpen(self):
        self.apply_finish_options(
            {
                "gains": [0] * 6,
                "denoise": 0.003,
                "rl_iterations": 0,
                "rl_sigma": 1.0,
                "gamma": 1.0,
                "saturation": 1.0,
                "balance": [1] * 3,
                "align_rgb": False,
            }
        )

    def apply_finish_options(self, values):
        for spin, value in zip(self.wavelet_spins, values.get("gains", [0] * 6)):
            spin.setValue(float(value))
        for key, spin in [
            ("denoise", self.denoise_spin),
            ("rl_iterations", self.rl_iterations),
            ("rl_sigma", self.rl_sigma),
            ("gamma", self.gamma_spin),
            ("saturation", self.saturation_spin),
        ]:
            if key in values:
                spin.setValue(values[key])
        for spin, value in zip(self.balance_spins, values.get("balance", [1] * 3)):
            spin.setValue(float(value))
        self.rgb_alignment.setChecked(bool(values.get("align_rgb", False)))

    def save_preset(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save sharpening preset", "sharpening.json", "JSON (*.json)"
        )
        if path:
            Path(path).write_text(json.dumps(self.finish_options(), indent=2), encoding="utf8")

    def load_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open sharpening preset", "", "JSON (*.json)")
        if path:
            try:
                self.apply_finish_options(json.loads(Path(path).read_text(encoding="utf8")))
            except Exception as e:
                self.error(f"Cannot load preset: {e}")

    def image_save_path(self, name):
        path, selected = QFileDialog.getSaveFileName(
            self,
            "Export image",
            name + ".tif",
            "16-bit TIFF (*.tif);;16-bit PNG (*.png);;Float FITS (*.fits)",
        )
        if path and Path(path).suffix.lower() not in (".tif", ".tiff", ".png", ".fits", ".fit", ".fts"):
            path += ".png" if "PNG" in selected else ".fits" if "FITS" in selected else ".tif"
        return path

    def export_dialog(self, image, name):
        path = self.image_save_path(name)
        if path:
            try:
                write_image(path, image, overwrite=True)
                self.log("Image saved: " + path)
            except Exception as e:
                self.error(str(e))

    def save_finished(self):
        if self.original is None:
            self.error("Stack a recording or open an image in Sharpen first.")
            return
        # Recompute with current settings so an export cannot race a pending preview.
        path = self.image_save_path("planet-finished")
        if path:
            image, options = self.original.copy(), self.finish_options()

            def export(progress, cancel):
                result = finish_image(image, **options)
                if cancel.is_set():
                    raise Cancelled("Export cancelled.")
                write_image(path, result, overwrite=True)
                Path(path + ".json").write_text(
                    json.dumps({"sharpening": options}, indent=2), encoding="utf8"
                )
                return path

            self.start_job(export, (), lambda p: self.log("Finished image and settings saved: " + p))

    def build_batch(self):
        page, layout = page_layout(
            "Batch Queue",
            "Process multiple recordings with the current Prepare & Stack and Sharpen settings. Each job writes its own image and report.",
        )
        row = QHBoxLayout()
        row.addWidget(button("Add recordings…", self.add_batch))
        row.addWidget(button("Remove selected", self.remove_batch))
        row.addStretch()
        row.addWidget(button("Output folder…", self.choose_batch_folder))
        layout.addLayout(row)
        self.batch_output_label = QLabel("Output: " + self.batch_directory)
        self.batch_output_label.setWordWrap(True)
        layout.addWidget(self.batch_output_label)
        self.batch_table = QTableWidget(0, 2)
        self.batch_table.setHorizontalHeaderLabels(["Recording", "Status"])
        self.batch_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.batch_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.batch_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.batch_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.batch_table, 1)
        row = QHBoxLayout()
        self.batch_start = button("Run queue", self.run_batch, True)
        self.batch_cancel = button("Cancel queue", self.cancel_processing)
        self.batch_cancel.setEnabled(False)
        row.addWidget(self.batch_start)
        row.addWidget(self.batch_cancel)
        self.batch_progress = QProgressBar()
        row.addWidget(self.batch_progress, 1)
        layout.addLayout(row)
        return page

    def add_batch(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add recordings to queue", "", "Recordings (*.ser *.avi *.mov *.mp4 *.mkv)"
        )
        for path in paths:
            if path in self.batch_paths:
                continue
            self.batch_paths.append(path)
            row = self.batch_table.rowCount()
            self.batch_table.insertRow(row)
            self.batch_table.setItem(row, 0, QTableWidgetItem(path))
            self.batch_table.setItem(row, 1, QTableWidgetItem("Queued"))

    def remove_batch(self):
        if self.job and self.job.isRunning():
            return
        rows = sorted({item.row() for item in self.batch_table.selectedItems()}, reverse=True)
        for row in rows:
            self.batch_paths.pop(row)
            self.batch_table.removeRow(row)

    def choose_batch_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select queue output folder", self.batch_directory)
        if path:
            self.batch_directory = path
            self.batch_output_label.setText("Output: " + path)

    def run_batch(self):
        if not self.batch_paths:
            self.error("Add at least one recording to the queue.")
            return
        paths, directory, stack_options, finish_options = (
            list(self.batch_paths),
            Path(self.batch_directory),
            self.options(),
            self.finish_options(),
        )

        def process(progress, cancel):
            reports = []
            directory.mkdir(parents=True, exist_ok=True)
            for i, path in enumerate(paths):
                if cancel.is_set():
                    raise Cancelled("Queue cancelled.")
                stem = Path(path).stem
                output = directory / (stem + "-stack.tif")
                index = 1
                while output.exists() or output.with_suffix(".tif.json").exists():
                    output = directory / f"{stem}-{index}-stack.tif"
                    index += 1
                try:
                    result = stack_source(
                        path,
                        stack_options,
                        lambda amount, text: progress(
                            (i * 100 + amount) / len(paths), f"{i + 1}/{len(paths)} · {text}"
                        ),
                        cancel,
                    )
                    result.save(output)
                    finished = finish_image(result.image, **finish_options)
                    final_path = output.with_name(output.stem.replace("-stack", "-finished") + ".tif")
                    write_image(final_path, finished)
                    final_path.with_suffix(".tif.json").write_text(
                        json.dumps({"sharpening": finish_options}, indent=2), encoding="utf8"
                    )
                    reports.append((path, "Saved " + final_path.name))
                except Cancelled:
                    raise
                except Exception as e:
                    reports.append((path, "Failed: " + str(e)))
            return reports

        self.start_job(process, (), self.batch_done)

    def batch_done(self, reports):
        for path, status in reports:
            if path in self.batch_paths:
                self.batch_table.setItem(self.batch_paths.index(path), 1, QTableWidgetItem(status))
        self.log(
            f"Queue finished · {sum(s.startswith('Saved') for _, s in reports)} of {len(reports)} succeeded"
        )

    def build_support(self):
        page, layout = page_layout(
            "Camera support & guidance",
            "Connection details are shown here so a driver failure does not get hidden behind “Unable to connect camera”.",
        )
        guide = QTextBrowser()
        guide.setOpenExternalLinks(True)
        guide.setHtml("""<h2>Getting started</h2><p>1. In <b>Capture</b>, scan and connect a camera, choose a mode, start preview, then record SER.
        Try the simulator to practice the entire workflow.</p><p>2. Send the recording to <b>Prepare & Stack</b>. Choose Planet for object centering,
        or Moon / Sun for surface images. Keep the best 25% as a starting point and run Align & Stack.</p>
        <p>3. In <b>Sharpen</b>, increase the fine wavelet layers gradually. Use Show original to compare. Export TIFF, PNG, or FITS.</p>
        <h2>Camera connections</h2><p><b>Direct UVC:</b> raw Bayer, mono, RGB, YUV, and MJPEG modes on macOS and Linux.
        The macOS USB interface fix from oaCapture is included. NexImage 10 GRBG is supported.</p>
        <p><b>System cameras:</b> DirectShow on Windows, AVFoundation on macOS, V4L2 on Linux. Installed system drivers are required.
        These paths usually deliver converted 8-bit color rather than sensor raw data.</p>
        <p><b>ZWO ASI / QHY:</b> select the matching vendor driver library in Camera settings. Its architecture must match the app.
        Vendor binaries are supplied by the manufacturer and are not bundled in this release.</p>
        <p><b>INDI:</b> connect to a running INDI server (normally port 7624). FITS exposures from its camera drivers are supported.</p>
        <p><b>ASCOM Alpaca:</b> connect to an Alpaca server. ImageBytes and JSON images are supported. This also allows access to
        Windows ASCOM cameras through ASCOM Remote. INDI and Alpaca throughput depends on the server and camera.</p>
        <h2>If connection fails</h2><p>Close other camera applications, reconnect USB directly, and scan again. On Linux, grant your account
        access to the camera with a udev rule. For system cameras, allow camera access in your OS privacy settings.</p>
        <p>Camera discovery does not prove every model works. See the repository's camera support table for tested hardware and driver requirements.</p>
        <h2>About the processing</h2><p>Local alignment uses overlapping patches and ranks frames independently at each patch.
        Enlarged outputs use Lanczos resampling. This release does not implement rotational derotation or drizzle reconstruction.</p>
        <p><a href="https://github.com/BrainBrian825/planetary-studio">Source code, releases, and camera support documentation</a></p>""")
        layout.addWidget(guide, 1)
        layout.addWidget(button("Configure camera connections…", self.configure_cameras))
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(150)
        self.diagnostics.setPlaceholderText("Camera discovery and connection messages will appear here.")
        layout.addWidget(self.diagnostics)
        return page

    def configure_cameras(self):
        if self.camera_worker and self.camera_worker.isRunning():
            self.error("Disconnect the active camera before changing driver settings.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Camera connections")
        dialog.resize(620, 300)
        layout, form = QVBoxLayout(dialog), QFormLayout()
        fields = {}
        for key, label in [
            ("ASI_sdk", "ZWO ASI library"),
            ("QHY_sdk", "QHY library"),
            ("indi", "INDI host:port"),
            ("alpaca", "Alpaca URL"),
        ]:
            field = QLineEdit(self.camera_settings.get(key, ""))
            fields[key] = field
            if key.endswith("_sdk"):
                row = QHBoxLayout()
                row.addWidget(field, 1)

                def pick(checked=False, f=field):
                    path, _ = QFileDialog.getOpenFileName(
                        dialog,
                        "Select native vendor library",
                        "",
                        "Libraries (*.dylib *.so *.so.* *.dll);;All files (*)",
                    )
                    if path:
                        f.setText(path)

                row.addWidget(button("Browse…", pick))
                form.addRow(label, row)
            else:
                form.addRow(label, field)
        fields["indi"].setPlaceholderText("localhost:7624")
        fields["alpaca"].setPlaceholderText("http://localhost:11111")
        layout.addLayout(form)
        hint = QLabel(
            "Leave a connection blank to skip it when scanning. Choose the vendor library for your operating system and CPU."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(button("Save connections", dialog.accept, True))
        if dialog.exec() == QDialog.Accepted:
            self.camera_settings = {key: field.text().strip() for key, field in fields.items()}
            self.settings.setValue("camera_settings", json.dumps(self.camera_settings))
            self.scan_cameras()

    def save_project(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save imaging project",
            self._project_path or "project.planetary.json",
            "Planetary project (*.json)",
        )
        if not path:
            return
        try:
            Path(path).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "source": self.source_path,
                        "stack": asdict(self.options()),
                        "sharpen": self.finish_options(),
                        "batch": self.batch_paths,
                        "batch_output": self.batch_directory,
                    },
                    indent=2,
                ),
                encoding="utf8",
            )
            self._project_path = path
            self.log("Project saved. Source recordings remain at their original paths.")
        except Exception as e:
            self.error(str(e))

    def load_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open imaging project", "", "Planetary project (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf8"))
            if data.get("version") != 1:
                raise ValueError("Unsupported project version.")
            options = StackOptions(**data["stack"])
            options.validate()
            self.keep_percent.setValue(options.keep_percent)
            self.bayer_combo.setCurrentText(options.bayer)
            self.target_combo.setCurrentIndex(0 if options.center_object else 1)
            self.crop_w.setValue(options.crop_width)
            self.crop_h.setValue(options.crop_height)
            self.dark_path.setText(options.dark_path)
            self.flat_path.setText(options.flat_path)
            self.hot_pixels.setChecked(options.remove_hot_pixels)
            self.brightness.setChecked(options.normalize_brightness)
            self.local_alignment.setChecked(options.local_alignment)
            self.ap_size.setValue(options.alignment_size)
            self.scale_combo.setCurrentIndex((1.0, 1.5, 2.0).index(options.scale))
            self.apply_finish_options(data.get("sharpen", {}))
            if data.get("source"):
                self.set_source(data["source"])
            self.batch_paths = list(data.get("batch", []))
            self.batch_table.setRowCount(len(self.batch_paths))
            for i, p in enumerate(self.batch_paths):
                self.batch_table.setItem(i, 0, QTableWidgetItem(p))
                self.batch_table.setItem(i, 1, QTableWidgetItem("Queued"))
            self.batch_directory = data.get("batch_output", self.batch_directory)
            self.batch_output_label.setText("Output: " + self.batch_directory)
            self._project_path = path
            self.log("Project loaded")
        except Exception as e:
            self.error(f"Cannot load project: {e}")

    def closeEvent(self, event):
        self.sharpen_timer.stop()
        for worker in (self.job, self.scan_worker, self.sharpen_worker):
            if worker and worker.isRunning():
                worker.cancel.set()
        if self.camera_worker and self.camera_worker.isRunning():
            self.camera_worker.stop_event.set()
        for worker in (self.camera_worker, self.job, self.scan_worker, self.sharpen_worker):
            if worker and worker.isRunning() and not worker.wait(12000):
                self.log("Finishing camera or processing cleanup. Try closing again in a moment.")
                event.ignore()
                return
        event.accept()


def run():
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Planetary Studio")
    app.setOrganizationName("PlanetaryStudio")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()
