import json
from dataclasses import replace
import cv2
import numpy as np
import pytest
from planetary_studio.processing import (
    StackOptions, FrameRejected, Preprocessor, detect_object, quality_score, stack_source, export_prepared,
)
from planetary_studio.preview import preview_frame, preview_sample
from planetary_studio.sources import open_source
from planetary_studio.ser import SerReader, SerWriter


def planet(cx=64, cy=48):
    y, x = np.indices((96, 128))
    disk = (x - cx) ** 2 + (y - cy) ** 2 < 24 ** 2
    image = np.where(disk, .45 + .12 * np.sin(x / 2) * np.cos(y / 3), .005)
    return image.astype(np.float32)


def recording(path, frames):
    with SerWriter(path, frames[0].shape, 16, "MONO") as writer:
        for frame in frames:
            writer.write(np.rint(np.clip(frame, 0, 1) * 65535).astype(np.uint16))
    return path


@pytest.mark.parametrize("method", ["gradient", "laplacian", "brenner"])
def test_detail_estimators_rank_known_blur_below_sharp_detail(method):
    sharp = planet()
    blurred = cv2.GaussianBlur(sharp, (0, 0), 2)
    assert quality_score(sharp, method) > quality_score(blurred, method) * 1.5
    noisy = sharp + np.random.default_rng(1).normal(0, .05, sharp.shape).astype(np.float32)
    assert quality_score(noisy, method, 1.5) < quality_score(noisy, method, 0)


def test_brightness_estimator_is_a_cloud_filter_not_a_detail_score():
    image = planet()
    assert quality_score(image, "brightness") == pytest.approx(float(image.mean()))
    assert quality_score(image, "brightness") > quality_score(image * .6, "brightness")


@pytest.mark.parametrize("frames", [
    [np.zeros((96, 128), np.float32)],
    [np.random.default_rng(23).uniform(0, .08, (96, 128)).astype(np.float32)],
    [np.random.default_rng(51).normal(.04, .02, (96, 128)).astype(np.float32)],
])
def test_detection_ignores_blank_sensor_noise(frames):
    assert detect_object(frames[0]) is None


def test_preview_identifies_missing_clipped_and_crop_clipped_objects(tmp_path):
    path = recording(tmp_path / "objects.ser", [planet(), planet(5), np.zeros((96, 128), np.float32)])
    options = StackOptions(reject_missing_object=True, reject_cutoff_object=True, scale=2)
    accepted = preview_frame(path, 0, options)
    assert accepted.rejection_reason == "" and accepted.object_bounds is not None
    x, y, w, h = accepted.object_bounds
    assert 2 * 40 < x + w / 2 < 2 * 88 and w > 80
    assert "recording boundary" in preview_frame(path, 1, options).rejection_reason
    assert "missing" in preview_frame(path, 2, options).rejection_reason
    assert "crop size" in preview_frame(path, 0, replace(options, crop_width=30)).rejection_reason
    assert "missing" in preview_frame(path, 0, replace(options, object_detection_threshold=.9)).rejection_reason
    assert preview_frame(path, 1, StackOptions()).rejection_reason == ""
    assert preview_frame(path, 2, replace(options, center_object=False)).rejection_reason == ""
    source = open_source(path)
    try:
        with pytest.raises(FrameRejected, match="recording boundary"):
            Preprocessor(source, options).read(1)
    finally:
        source.close()


@pytest.mark.parametrize("method", ["gradient", "laplacian", "brenner", "brightness"])
def test_trimmed_sample_stack_export_and_report_agree_on_rejection(tmp_path, method):
    good = planet()
    blank = np.zeros_like(good)
    path = recording(tmp_path / "sequence.ser", [good, blank, good, planet(5), good, good, blank])
    options = StackOptions(first_frame=2, last_frame=6, keep_percent=50, local_alignment=False,
                           reject_missing_object=True, reject_cutoff_object=True, quality_method=method)
    result = stack_source(path, options)
    assert result.source_indices == [1, 2, 3, 4, 5]
    assert result.input_frames == 5 and set(result.rejected_frames) == {0, 2}
    assert len(result.selected) == 2  # Half of 3 accepted frames, rounded up.
    assert set(result.selected) <= {1, 3, 4}
    assert set(result.aligned_indices) == set(result.selected)
    sample = preview_sample(path, options, 5)
    assert sample.rejected_frames == result.rejected_frames
    assert np.allclose(sample.image, result.image)
    output = tmp_path / "prepared.ser"
    export_prepared(path, output, options)
    with SerReader(output) as reader:
        assert reader.count == 3
    result.save(tmp_path / "stack.tif")
    report = json.loads((tmp_path / "stack.tif.json").read_text())
    assert set(report["rejected_source_frames"]) == {"1", "3"}
    assert report["options"]["quality_method"] == method
    assert report["frame_indices_are_zero_based"]


def test_all_rejected_gives_an_actionable_error_and_preserves_existing_export(tmp_path):
    path = recording(tmp_path / "blank.ser", [np.zeros((96, 128), np.float32)])
    options = StackOptions(reject_missing_object=True)
    with pytest.raises(ValueError, match="Every frame was rejected"):
        stack_source(path, options)
    output = tmp_path / "prepared.ser"
    with pytest.raises(ValueError, match="Every frame was rejected"):
        export_prepared(path, output, options)
    assert not output.exists()
    output.write_bytes(b"preserve me")
    with pytest.raises(ValueError, match="Every frame was rejected"):
        export_prepared(path, output, options, overwrite=True)
    assert output.read_bytes() == b"preserve me"


def test_quality_plot_excludes_rejected_frames_from_ranking_and_keeps_recording_positions():
    from PySide6.QtWidgets import QApplication
    from planetary_studio.widgets import QualityPlot
    app = QApplication.instance() or QApplication([])
    graph = QualityPlot()
    graph.set_scores([4, 0, 3, 2], [1])
    assert graph.scores.tolist() == [4, 3, 2]
    graph.set_order("recording")
    assert graph.scores.tolist() == [4, 0, 3, 2]
    assert graph.accepted_scores().tolist() == [4, 3, 2]
    assert graph.rejected_indices == [1]
    graph.resize(600, 150)
    graph.show()
    app.processEvents()
    assert not graph.grab().isNull()
    graph.close()


@pytest.mark.parametrize("kwargs", [
    {"quality_method": "unknown"}, {"quality_noise_sigma": float("nan")},
    {"object_detection_threshold": 1.1}, {"min_object_size": 1},
])
def test_invalid_selection_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        StackOptions(**kwargs).validate()
