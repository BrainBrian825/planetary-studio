import threading
import time
import cv2
import numpy as np
import pytest
from planetary_studio.cameras.simulator import planet_image
from planetary_studio.processing import Preprocessor, StackOptions, Cancelled
from planetary_studio.preview import preview_frame, preview_sample
from planetary_studio.sources import open_source
from planetary_studio.ser import SerWriter


@pytest.mark.parametrize("pattern,planes", [
    ("RGGB", [[0, 1], [1, 2]]), ("GRBG", [[1, 0], [2, 1]]),
    ("GBRG", [[1, 2], [0, 1]]), ("BGGR", [[2, 1], [1, 0]]),
])
def test_raw_grayscale_avi_recovers_known_color(pattern, planes, tmp_path):
    raw = np.empty((24, 32), np.uint8)
    for y in range(2):
        for x in range(2):
            raw[y::2, x::2] = [180, 100, 40][planes[y][x]]
    path = tmp_path / "raw.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"FFV1"), 10, (32, 24), False)
    assert writer.isOpened()
    writer.write(raw)
    writer.release()
    source = open_source(path)
    try:
        decoded = source.read(0)
        assert decoded.shape == (24, 32, 3)
        assert np.array_equal(decoded[..., 0], raw)
        image = Preprocessor(source, StackOptions(bayer=pattern, center_object=False)).read(0)
        np.testing.assert_allclose(image[5, 5], np.array([180, 100, 40]) / 255, atol=1e-6)
    finally:
        source.close()


def test_color_video_rejects_bayer_instead_of_silently_ignoring_it(tmp_path):
    from planetary_studio.imaging import write_image
    color = np.zeros((24, 32, 3), np.float32)
    color[..., 0], color[..., 1], color[..., 2] = 0.6, 0.3, 0.1
    path = tmp_path / "color.png"
    write_image(path, color)
    source = open_source(path)
    try:
        with pytest.raises(ValueError, match="already contains color"):
            Preprocessor(source, StackOptions(bayer="RGGB")).read(0)
    finally:
        source.close()


def test_sample_preview_only_reads_the_requested_subset(monkeypatch):
    import planetary_studio.processing as processing
    import planetary_studio.preview as preview
    reads, closed = [], []
    truth = planet_image(64, 48)

    class Source:
        count, bits, pattern = 60, 16, "RGB"

        def read(self, index):
            reads.append(index)
            rng = np.random.default_rng(index)
            return np.rint(np.clip(truth + rng.normal(0, 0.01, truth.shape), 0, 1) * 65535).astype(np.uint16)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(processing, "open_source", lambda _: Source())
    monkeypatch.setattr(preview, "open_source", lambda _: Source())
    result = preview_sample("sample", StackOptions(keep_percent=50, alignment_size=16), 12)
    expected = np.linspace(0, 59, 12, dtype=int).tolist()
    assert result.sample_indices == expected and result.input_frames == 12
    assert set(reads) == set(expected) and len(result.selected) == 6
    assert len(closed) == 2 and np.isfinite(result.image).all()


def test_frame_preview_applies_crop_scale_and_closes_cancelled_source(tmp_path):
    path = tmp_path / "frames.ser"
    frame = np.rint(planet_image(128, 96) * 65535).astype(np.uint16)
    with SerWriter(path, frame.shape, 16, "RGB") as writer:
        for _ in range(5):
            writer.write(frame)
    options = StackOptions(crop_width=64, crop_height=64, alignment_size=16, scale=2)
    result = preview_frame(path, 3, options)
    assert result.index == 3 and result.image.shape == (128, 128, 3)
    assert result.point_size == 32 and result.points
    original = preview_frame(path, 3, options, prepared=False)
    assert original.image.shape == (96, 128, 3) and not original.points
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        preview_frame(path, 0, options, cancel=cancel)
    path.unlink()


def test_desktop_live_preview_and_sample_are_distinct_from_full_stack(tmp_path):
    from PySide6.QtWidgets import QApplication
    from planetary_studio.app import MainWindow
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "frames.ser"
    frame = np.rint(planet_image(64, 48) * 65535).astype(np.uint16)
    with SerWriter(path, frame.shape, 16, "RGB") as writer:
        for _ in range(24):
            writer.write(frame)
    window = MainWindow()

    def until(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not predicate():
            app.processEvents()
            time.sleep(0.01)
        assert predicate()

    try:
        window.set_source(str(path))
        window.crop_w.setValue(32)
        window.crop_h.setValue(32)
        until(lambda: window.stack_view.image is not None and window.stack_view.image.shape[:2] == (32, 32))
        assert "Prepared frame 1 of 24" in window.preview_details.text()
        window.ap_size.setValue(16)
        window.sample_count.setValue(12)
        window.run_sample()
        until(lambda: window.sample_result is not None)
        assert window.frame_view_mode.currentText() == "Sample stack"
        assert "approximate result" in window.preview_details.text()
        assert window.result is None and not window.save_stack_button.isEnabled()
        window.continue_to_sharpen()
        assert "Sample preview" in window.sharpen_input_label.text()
        window.crop_w.setValue(24)
        assert window.sample_result is None and window.frame_view_mode.currentText() == "Prepared frame"
        assert not window.frame_view_mode.model().item(2).isEnabled()
    finally:
        window.close()
        app.processEvents()
