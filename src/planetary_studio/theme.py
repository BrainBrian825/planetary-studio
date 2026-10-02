"""Neutral palettes with a persistent override and live system appearance."""

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


COLORS = {
    "dark": dict(
        window="#202020",
        surface="#292929",
        base="#181818",
        text="#eeeeee",
        muted="#b0b0b0",
        border="#484848",
        button="#343434",
        hover="#444444",
        selected="#454545",
        primary="#e5e5e5",
        primary_text="#202020",
        disabled="#858585",
    ),
    "light": dict(
        window="#f4f4f4",
        surface="#ffffff",
        base="#ffffff",
        text="#242424",
        muted="#606060",
        border="#cccccc",
        button="#eeeeee",
        hover="#e0e0e0",
        selected="#dedede",
        primary="#333333",
        primary_text="#ffffff",
        disabled="#8a8a8a",
    ),
}


def stylesheet(c):
    return f"""QWidget {{ background: {c["surface"]}; color: {c["text"]}; font-size: 13px; }}
QMainWindow, QStackedWidget {{ background: {c["window"]}; }}
QLabel[secondary="true"] {{ color: {c["muted"]}; }}
QGroupBox {{ border: 1px solid {c["border"]}; border-radius: 7px; margin-top: 14px; padding: 15px 10px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; }}
QPushButton {{ background: {c["button"]}; border: 1px solid {c["border"]}; padding: 9px 13px; border-radius: 5px; }}
QPushButton:hover {{ background: {c["hover"]}; }}
QPushButton:disabled {{ color: {c["disabled"]}; background: {c["button"]}; }}
QPushButton[primary="true"] {{ background: {c["primary"]}; color: {c["primary_text"]}; border-color: {c["primary"]}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {c["muted"]}; }}
QPushButton[primary="true"]:disabled {{ background: {c["button"]}; color: {c["disabled"]}; border-color: {c["border"]}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{ background: {c["base"]}; border: 1px solid {c["border"]}; border-radius: 4px; padding: 6px; selection-background-color: {c["selected"]}; selection-color: {c["text"]}; }}
QComboBox::drop-down {{ width: 22px; border: 0; }}
QComboBox QAbstractItemView {{ background: {c["base"]}; selection-background-color: {c["selected"]}; selection-color: {c["text"]}; }}
QListWidget {{ background: {c["window"]}; border: 0; padding: 8px; font-size: 15px; outline: 0; }}
QListWidget::item {{ padding: 15px 10px; border-radius: 5px; margin: 3px 0; }}
QListWidget::item:selected {{ background: {c["selected"]}; color: {c["text"]}; }}
QProgressBar {{ border: 1px solid {c["border"]}; border-radius: 4px; text-align: center; height: 19px; }}
QProgressBar::chunk {{ background: {c["muted"]}; }}
QScrollArea, QGraphicsView, QTableWidget {{ border: 1px solid {c["border"]}; border-radius: 5px; }}
QScrollBar:vertical {{ background: {c["window"]}; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: {c["window"]}; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {c["border"]}; border-radius: 4px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {c["muted"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QHeaderView::section {{ background: {c["button"]}; border: 0; padding: 8px; }}
QCheckBox {{ spacing: 8px; }}
QSlider::groove:horizontal {{ height: 4px; background: {c["border"]}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c["muted"]}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c["text"]}; width: 14px; margin: -5px 0; border-radius: 6px; }}
QSlider::handle:horizontal:disabled {{ background: {c["disabled"]}; }}
QTextBrowser {{ background: {c["surface"]}; padding: 12px; border: 0; }}
QStatusBar {{ background: {c["window"]}; color: {c["muted"]}; }}
QToolTip {{ background: {c["base"]}; color: {c["text"]}; border: 1px solid {c["border"]}; }}
"""


class ThemeController(QObject):
    changed = Signal(str)

    def __init__(self, window, settings, hints=None):
        super().__init__(window)
        self.window, self.settings = window, settings
        self.mode = settings.value("appearance", "system")
        if self.mode not in ("system", "light", "dark"):
            self.mode = "system"
        self.hints = hints if hints is not None else QApplication.instance().styleHints()
        self.hints.colorSchemeChanged.connect(self.system_changed)
        self.apply()

    @property
    def effective_mode(self):
        if self.mode != "system":
            return self.mode
        return "dark" if self.hints.colorScheme() == Qt.ColorScheme.Dark else "light"

    def set_mode(self, mode):
        if mode not in ("system", "light", "dark"):
            raise ValueError("Choose System, Light, or Dark appearance.")
        self.mode = mode
        self.settings.setValue("appearance", mode)
        self.apply()

    def system_changed(self, *_):
        if self.mode == "system":
            self.apply()

    def apply(self):
        c = COLORS[self.effective_mode]
        palette = QPalette()
        roles = {
            "Window": "window",
            "WindowText": "text",
            "Base": "base",
            "AlternateBase": "window",
            "Text": "text",
            "Button": "button",
            "ButtonText": "text",
            "ToolTipBase": "base",
            "ToolTipText": "text",
            "PlaceholderText": "muted",
            "Highlight": "selected",
            "HighlightedText": "text",
            "Accent": "muted",
            "Link": "text",
            "LinkVisited": "muted",
        }
        for role, color in roles.items():
            palette.setColor(getattr(QPalette.ColorRole, role), QColor(c[color]))
        for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
            palette.setColor(QPalette.Disabled, role, QColor(c["disabled"]))
        QApplication.instance().setPalette(palette)
        self.window.setPalette(palette)
        self.window.setStyleSheet(stylesheet(c))
        self.changed.emit(self.mode)
