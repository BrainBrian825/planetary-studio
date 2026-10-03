import pytest


@pytest.fixture(autouse=True)
def isolate_desktop_preferences(tmp_path, monkeypatch):
    """Desktop checks must not overwrite the user's theme or recent folder."""
    from PySide6.QtCore import QSettings
    import planetary_studio.app as desktop
    monkeypatch.setattr(desktop, "QSettings", lambda *_: QSettings(str(tmp_path / "desktop.ini"), QSettings.IniFormat))
