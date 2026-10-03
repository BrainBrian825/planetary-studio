import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from planetary_studio.app import MainWindow
from planetary_studio.imaging import read_image
from planetary_studio.processing import StackOptions, export_prepared, create_master
from planetary_studio.ser import SerReader, SerWriter


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    widget = MainWindow()
    yield widget
    widget.close()
    app.processEvents()


def test_file_dialogs_share_last_selected_folder_and_restore_it(window, tmp_path, monkeypatch):
    input_dir, output_dir = tmp_path / "input", tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    opened, saved = [], []

    def choose_open(parent, title, directory, filters):
        opened.append(directory)
        return str(input_dir / "record.avi"), filters

    def choose_save(parent, title, name, filters):
        saved.append(name)
        return str(output_dir / "finished.tif"), filters

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose_open)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_save)
    window.open_file("Open image", "Images (*)")
    window.image_save_path("finished")
    assert Path(saved[0]).parent == input_dir
    window.open_file("Open preset", "JSON (*)")
    assert Path(opened[-1]) == output_dir
    window.remember_directory(output_dir, folder=True)
    window.settings.sync()
    reopened = MainWindow()
    try:
        assert reopened.dialog_directory() == str(output_dir)
    finally:
        reopened.close()


def test_folder_and_multiple_file_choosers_update_shared_folder(window, tmp_path, monkeypatch):
    selected = tmp_path / "frames"
    selected.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(selected))
    window.choose_batch_folder()
    assert window.dialog_directory() == str(selected)
    assert window.settings.value("batch_output_directory") == str(selected)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(tmp_path / "one.ser")], ""))
    window.add_batch()
    assert window.dialog_directory() == str(tmp_path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    window.open_file("Cancelled", "All (*)")
    assert window.dialog_directory() == str(tmp_path)


def test_missing_remembered_folder_uses_existing_fallback(window, tmp_path):
    window.settings.setValue("file_dialog_directory", str(tmp_path / "removed"))
    assert Path(window.dialog_directory()).is_dir()
    assert window.dialog_directory() != str(Path.cwd().anchor)


def test_extension_added_to_existing_file_requires_replace_confirmation(window, tmp_path, monkeypatch):
    existing = tmp_path / "export.tif"
    existing.write_bytes(b"original")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(existing.with_suffix("")), "16-bit TIFF (*.tif)"))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    assert window.image_save_path("export") == ""
    assert existing.read_bytes() == b"original"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    assert window.image_save_path("export") == str(existing)


def test_gui_exports_and_recording_honor_accepted_save_dialog(window, tmp_path, monkeypatch):
    source = tmp_path / "input.ser"
    with SerWriter(source, (24, 32), 16) as writer:
        writer.write(np.full((24, 32), 12000, np.uint16))
    window.source_path = str(source)
    window.target_combo.setCurrentIndex(1)
    prepared, master = tmp_path / "prepared.ser", tmp_path / "master.fits"
    export_prepared(source, prepared, StackOptions(center_object=False))
    create_master(source, master)
    errors = []
    window.error = errors.append
    monkeypatch.setattr(window, "start_job", lambda function, args, succeeded, kwargs=None: succeeded(function(*args, **(kwargs or {}))))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(prepared), "SER recording (*.ser)"))
    window.prepare_export()
    with SerReader(prepared) as reader:
        assert reader.count == 1
        np.testing.assert_array_equal(reader.read(0), 12000)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(master), "FITS image (*.fits)"))
    window.master_export()
    np.testing.assert_allclose(read_image(master), 12000 / 65535, atol=1e-6)
    commands = []
    window.camera_worker = SimpleNamespace(command=lambda *args: commands.append(args))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(prepared), "SER recording (*.ser)"))
    window.record()
    assert commands[0] == ("record", str(prepared), window.record_duration.value(), True)
    window.camera_worker = None
    assert not errors


def test_prepared_and_master_replace_require_explicit_permission_and_preserve_source(tmp_path):
    source = tmp_path / "input.ser"
    with SerWriter(source, (24, 32), 16) as writer:
        writer.write(np.full((24, 32), 8000, np.uint16))
    original = source.read_bytes()
    for function, output in ((export_prepared, tmp_path / "prepared.ser"), (create_master, tmp_path / "master.fits")):
        output.write_bytes(b"old output")
        with pytest.raises(FileExistsError):
            function(source, output)
        assert output.read_bytes() == b"old output"
        function(source, output, overwrite=True)
        with pytest.raises(ValueError, match="preserve the source"):
            function(source, source, overwrite=True)
        assert source.read_bytes() == original


def test_ser_replacement_truncates_old_recording_and_timestamps(tmp_path):
    output = tmp_path / "record.ser"
    with SerWriter(output, (24, 32), 16) as writer:
        for _ in range(5):
            writer.write(np.full((24, 32), 10000, np.uint16))
    with SerWriter(output, (8, 8), overwrite=True) as writer:
        writer.write(np.full((8, 8), 30, np.uint8), 123456)
    with SerReader(output) as reader:
        assert reader.shape == (8, 8) and reader.count == 1
        np.testing.assert_array_equal(reader.read(0), 30)
        assert reader.timestamp(0) == 123456
    assert output.stat().st_size == 178 + 64 + 8
