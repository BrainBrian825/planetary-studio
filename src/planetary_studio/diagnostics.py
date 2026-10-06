"""Bounded local diagnostics, including errors from earlier app sessions."""

import time
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths


class DiagnosticLog:
    def __init__(self, settings):
        # Tests and portable self-tests supply an isolated INI settings file.
        directory = (
            Path(settings.fileName()).parent
            if settings.format() == QSettings.IniFormat
            else Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
        )
        self.path = directory / "diagnostics.log"

    def read(self):
        try:
            return self.path.read_text(encoding="utf8")[-200_000:]
        except OSError:
            return ""

    def append(self, message):
        line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + message
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > 1_000_000:
                self.path.replace(self.path.with_suffix(".previous.log"))
            with self.path.open("a", encoding="utf8") as file:
                file.write(line + "\n")
        except OSError:
            pass  # A log write failure must not interrupt capture.
        return line
