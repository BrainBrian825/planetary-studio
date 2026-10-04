import json
import time
import cv2
import numpy as np
import pytest
from planetary_studio.processing import (
    StackOptions, alignment_points, planned_alignment_points, stack_source, export_prepared,
)
from planetary_studio.preview import preview_frame, preview_sample
from planetary_studio.cameras.simulator import planet_image
from planetary_studio.ser import SerWriter, SerReader


def recording(path, count=20, width=96, height=80):
    image = planet_image(width, height)
    with SerWriter(path, image.shape, 16, "RGB") as writer:
        for i in range(count):
            frame = cv2.GaussianBlur(image, (0, 0), .3 + i * .08)
            writer.write(np.rint(frame * 65535).astype(np.uint16))
    return path


def test_automatic_points_exclude_dark_centers_even_when_the_box_contains_the_planet():
    y, x = np.indices((160, 240))
    image = (.65 * np.exp(-((x - 117)**2 + (y - 83)**2) / 1200)).astype(np.float32)
    image *= 1 + .1 * np.sin(x * .5) * np.cos(y * .4)
    points = alignment_points(image, 48, .15)
    assert points
    smooth = cv2.GaussianBlur(image, (0, 0), .8)
    assert all(float(np.median(smooth[py - 2:py + 3, px - 2:px + 3])) >= .15 for px, py in points)
    assert (117, 83) in points
    assert alignment_points(image, 48, .8) == []
    noisy_background = np.random.default_rng(8).uniform(0, .08, image.shape).astype(np.float32)
    assert alignment_points(noisy_background, 48, .1) == []


def test_manual_points_are_preserved_and_boundary_errors_are_explained():
    image = np.zeros((64, 80), np.float32)
    options = StackOptions(alignment_size=32, alignment_min_brightness=.9,
                           manual_alignment_points=[(32, 24), (32, 24), (56, 40)])
    options.validate()
    assert planned_alignment_points(image, options) == [(32, 24), (56, 40)]
    options.manual_alignment_points = [(2, 2)]
    with pytest.raises(ValueError, match="outside the prepared image"):
        planned_alignment_points(image, options)


def test_overlay_size_matches_the_actual_patch_on_a_small_odd_crop(tmp_path):
    path = recording(tmp_path / "odd.ser")
    options = StackOptions(crop_width=35, crop_height=33, alignment_size=64, scale=1.5,
                           manual_alignment_points=[(17, 16)])
    preview = preview_frame(path, 2, options)
    assert preview.point_size == 48
    assert preview.points == [(25.5, 24.0)]
    result = preview_sample(path, options, 4)
    assert result.point_size == 32
    assert result.alignment_points == [(17, 16)]


@pytest.mark.parametrize("local_quality", [True, False])
def test_global_ranking_skips_unselected_global_registration(tmp_path, monkeypatch, local_quality):
    import planetary_studio.processing as processing
    path = recording(tmp_path / "ranking.ser")
    full_calls = []
    original_register = processing.register
    def register(reference, frame):
        if reference.shape[:2] == (80, 96):
            full_calls.append(True)
        return original_register(reference, frame)
    monkeypatch.setattr(processing, "register", register)
    options = StackOptions(keep_percent=25, center_object=False, alignment_size=32,
                           manual_alignment_points=[(48, 40)], local_quality=local_quality)
    result = stack_source(path, options)
    assert len(result.selected) == 5
    assert len(full_calls) == (25 if local_quality else 10)
    assert len(result.aligned_indices) == (20 if local_quality else 5)
    assert np.isfinite(result.image).all()


def test_trimmed_stack_sample_and_prepared_export_use_the_same_source_range(tmp_path):
    path = recording(tmp_path / "range.ser")
    options = StackOptions(first_frame=5, last_frame=12, local_alignment=False, keep_percent=50)
    result = stack_source(path, options)
    assert result.input_frames == 8 and result.source_count == 20
    assert result.source_indices == list(range(4, 12))
    sample = preview_sample(path, options, 4)
    assert sample.sample_indices == [4, 6, 8, 11]
    assert sample.source_indices == sample.sample_indices
    output = tmp_path / "prepared.ser"
    export_prepared(path, output, options)
    with SerReader(output) as reader:
        assert reader.count == 8
    image = tmp_path / "stack.tif"
    result.save(image)
    report = json.loads((tmp_path / "stack.tif.json").read_text())
    assert report['globally_selected_source_frames'] == [result.source_indices[i] for i in result.selected]
    assert report['source_frames'] == 20
    with pytest.raises(ValueError, match="range is empty"):
        stack_source(path, StackOptions(first_frame=50))


def test_desktop_scaled_point_editing_and_project_round_trip(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QScrollArea
    from planetary_studio.app import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.options().quality_noise_sigma == .7
    window.resize(1240, 830)
    window.nav.setCurrentRow(1)
    window.show()
    app.processEvents()
    panel = window.pages.widget(1).findChild(QScrollArea)
    assert panel.horizontalScrollBar().maximum() == 0
    panel.setFixedWidth(400)
    window.resize(1000, 640)
    app.processEvents()
    assert panel.horizontalScrollBar().maximum() == 0
    def until(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not predicate():
            app.processEvents()
            time.sleep(.01)
        assert predicate()
    path = recording(tmp_path / "desktop.ser")
    try:
        window.set_source(str(path))
        window.ap_size.setValue(32)
        window.scale_combo.setCurrentIndex(2)
        window.edit_points.setChecked(True)
        until(lambda: window.stack_view.marker_editing)
        window.clear_alignment_points()
        assert window.manual_alignment_points == []
        window.edit_alignment_point(96, 80, False)
        assert window.manual_alignment_points == [(48, 40)]
        assert window.last_frame_preview.points == [(96, 80)]
        box = window.stack_view.markers[0].rect()
        assert box.width() == 64 and box.center().x() == 96 and box.center().y() == 80
        # Several edits need no fresh disk reads or preview worker.
        generation = window.preview_generation
        window.edit_alignment_point(128, 80, False)
        window.edit_alignment_point(128, 80, True)
        assert window.manual_alignment_points == [(48, 40)]
        assert window.preview_generation == generation + 2
        window.ap_min_brightness.set_value(17.5)
        window.quality_mode.setCurrentIndex(1)
        window.first_frame.setValue(3)
        window.last_frame.setValue(9)
        window.quality_method.setCurrentIndex(2)
        window.quality_noise.setValue(1.2)
        assert window.options().quality_noise_sigma == 1.2
        window.reject_missing.setChecked(True)
        window.reject_cutoff.setChecked(True)
        window.object_threshold.set_value(8.5)
        window.min_object_size.setValue(20)
        project = tmp_path / 'session.planetary.json'
        monkeypatch.setattr(window, 'save_file', lambda *a, **k: str(project))
        window.save_project()
        expected = window.options()
        window.manual_alignment_points = None
        window.ap_min_brightness.set_value(10)
        monkeypatch.setattr(window, 'open_file', lambda *a, **k: str(project))
        window.load_project()
        assert window.options() == expected
        assert window.frame_slider.minimum() == 2 and window.frame_slider.maximum() == 8
    finally:
        window.close()
        app.processEvents()
