import threading
import cv2
import numpy as np
import pytest
from planetary_studio.cameras.simulator import planet_image
from planetary_studio.imaging import normalized, read_image, write_image
from planetary_studio.processing import (
    quality_score,
    register,
    warp_shift,
    stack_source,
    StackOptions,
    finish_image,
    richardson_lucy,
    Cancelled,
    export_prepared,
    create_master,
    Preprocessor,
)
from planetary_studio.ser import SerWriter, SerReader
from planetary_studio.sources import open_source


def test_registration_recovers_known_subpixel_motion():
    image = planet_image(192, 160).astype(np.float32)
    moved = warp_shift(image, 4.25, -3.5)
    dx, dy, response = register(image, moved)
    assert abs(dx + 4.25) < 0.4 and abs(dy - 3.5) < 0.4 and response > 0.5


def test_quality_prefers_sharp_frames_to_blurred_frames():
    image = planet_image(128, 96).astype(np.float32)
    blur = cv2.GaussianBlur(image, (0, 0), 2.0)
    assert quality_score(image) > quality_score(blur) * 1.5


@pytest.mark.parametrize("local", [False, True])
def test_stacking_corrects_motion_and_reduces_noise(tmp_path, local):
    truth = planet_image(128, 96).astype(np.float32)
    rng = np.random.default_rng(742)
    path = tmp_path / "input.ser"
    noisy_errors = []
    with SerWriter(path, truth.shape, 16, "RGB") as writer:
        for _ in range(32):
            dx, dy = rng.normal(0, 1.7, 2)
            noisy = np.clip(truth + rng.normal(0, 0.03, truth.shape), 0, 1).astype(np.float32)
            noisy_errors.append(np.mean((noisy - truth) ** 2))
            moved = warp_shift(noisy, dx, dy)
            writer.write(np.rint(moved * 65535).astype(np.uint16))
    result = stack_source(
        path, StackOptions(keep_percent=75, center_object=False, local_alignment=local, alignment_size=32)
    )
    assert len(result.selected) == 24
    # A stack uses its selected reference's coordinates; compare after removing
    # that one common offset, rather than requiring an arbitrary absolute origin.
    dx, dy, response = register(truth, result.image)
    assert response > 0.5
    aligned = warp_shift(result.image, dx, dy)
    error = np.mean((aligned[15:-15, 15:-15] - truth[15:-15, 15:-15]) ** 2)
    assert error < np.mean(noisy_errors) * 0.5
    assert np.isfinite(result.image).all()
    if local:
        assert len(result.alignment_points) > 0


@pytest.mark.parametrize("ext", ["tif", "png", "fits"])
@pytest.mark.parametrize("color", [False, True])
def test_high_depth_exports_round_trip(tmp_path, ext, color):
    image = np.random.default_rng(77).random((24, 32, 3) if color else (24, 32), dtype=np.float32)
    path = tmp_path / ("image." + ext)
    write_image(path, image)
    recovered = normalized(read_image(path))
    assert recovered.shape == image.shape
    assert np.max(np.abs(recovered - image)) < 2e-5
    with pytest.raises(FileExistsError):
        write_image(path, image)


def test_zero_adjustments_are_identity_and_sharpening_is_finite():
    image = planet_image(128, 96).astype(np.float32)
    np.testing.assert_array_equal(finish_image(image), image)
    sharpened = finish_image(image, gains=(1, 0.5, 0.1, 0, 0, 0), rl_iterations=3, gamma=1.1, align_rgb=True)
    assert np.isfinite(sharpened).all() and sharpened.min() >= 0 and sharpened.max() <= 1
    assert np.max(np.abs(sharpened - image)) > 0.01


def test_deconvolution_increases_detail_for_known_blur():
    truth = planet_image(128, 96).astype(np.float32)
    blur = cv2.GaussianBlur(truth, (0, 0), 1.0)
    result = richardson_lucy(blur, 1.0, 10)
    assert np.mean((result - truth) ** 2) < np.mean((blur - truth) ** 2)


def test_calibration_subtracts_dark_and_divides_flat(tmp_path):
    shape = (32, 48)
    dark = np.full(shape, 0.1, np.float32)
    flat = np.tile(np.linspace(0.3, 0.7, 48, dtype=np.float32), (32, 1))
    source_frame = 0.4 * ((flat - dark) / np.median(flat - dark)) + dark
    write_image(tmp_path / "dark.fits", dark)
    write_image(tmp_path / "flat.fits", flat)
    write_image(tmp_path / "raw.fits", source_frame)
    source = open_source(tmp_path / "raw.fits")
    try:
        prep = Preprocessor(
            source,
            StackOptions(
                center_object=False,
                dark_path=str(tmp_path / "dark.fits"),
                flat_path=str(tmp_path / "flat.fits"),
            ),
        )
        np.testing.assert_allclose(prep.read(0), 0.4, atol=1e-6)
    finally:
        source.close()


def test_prepared_ser_and_master_keep_calibration_values(tmp_path):
    path = tmp_path / "input.ser"
    with SerWriter(path, (24, 32), 16) as writer:
        for value in (12000, 16000, 20000):
            writer.write(np.full((24, 32), value, np.uint16))
    export_prepared(
        path, tmp_path / "prepared.ser", StackOptions(center_object=False, crop_width=16, crop_height=16)
    )
    with SerReader(tmp_path / "prepared.ser") as reader:
        assert reader.shape == (16, 16) and reader.count == 3
        np.testing.assert_array_equal(reader.read(0), 12000)
    create_master(path, tmp_path / "master.fits")
    np.testing.assert_allclose(read_image(tmp_path / "master.fits"), 16000 / 65535, atol=1e-6)


def test_cancelled_processing_releases_input_file(tmp_path):
    path = tmp_path / "input.ser"
    with SerWriter(path, (24, 32)) as w:
        w.write(np.zeros((24, 32), np.uint8))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        stack_source(path, cancel=cancel)
    path.unlink()


def test_invalid_options_and_blank_frames_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        StackOptions(keep_percent=0).validate()
    path = tmp_path / "blank.ser"
    with SerWriter(path, (24, 32)) as w:
        w.write(np.zeros((24, 32), np.uint8))
    with pytest.raises(ValueError, match="detail"):
        stack_source(path)


def test_image_sequence_uses_natural_sort(tmp_path):
    from planetary_studio.sources import ImageSequence

    for name, value in [("frame10.png", 0.8), ("frame2.png", 0.2), ("frame1.png", 0.1)]:
        write_image(tmp_path / name, np.full((16, 16), value, np.float32))
    source = ImageSequence(tmp_path.glob("*.png"))
    assert [p.name for p in source.paths] == ["frame1.png", "frame2.png", "frame10.png"]
