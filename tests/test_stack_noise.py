import cv2
import numpy as np
import pytest

from planetary_studio.cameras.simulator import planet_image
from planetary_studio.imaging import luminance
from planetary_studio.processing import StackOptions, register, stack_source, warp_shift
from planetary_studio.ser import SerWriter


@pytest.mark.parametrize("size", [(320, 256), (800, 800)])
def test_registration_tracks_soft_planet_instead_of_displaced_sensor_noise(size):
    w, h = size
    truth = cv2.GaussianBlur(luminance(planet_image(w, h)), (0, 0), 6)
    noise = np.random.default_rng(54).normal(0, 0.025, (h, w)).astype(np.float32)
    reference = truth + noise
    moved = warp_shift(truth, 3.25, -2.5) + warp_shift(noise, 37, -24)
    dx, dy, response = register(reference, moved)
    assert abs(dx + 3.25) < 0.5 and abs(dy - 2.5) < 0.5
    assert response > 0.95


def test_local_registration_never_exceeds_displacement_limit():
    truth = planet_image(64, 64)
    frame = warp_shift(truth, 7, 0)
    dx, dy, _ = register(truth, frame, max_shift=2)
    assert np.hypot(dx, dy) <= 2


def test_global_registration_still_recovers_large_real_motion():
    truth = planet_image(800, 640)
    dx, dy, confidence = register(truth, warp_shift(truth, 64.5, -41.25))
    assert abs(dx + 64.5) < 0.5 and abs(dy - 41.25) < 0.5 and confidence > 0.99


def test_irregular_local_patch_blends_into_global_stack_without_rectangular_seam(tmp_path):
    size = 160
    yy, xx = np.indices((size, size), dtype=np.float32)
    texture = cv2.resize(np.random.default_rng(31).random((20, 20), dtype=np.float32), (size, size))
    truth = 0.05 + 0.7 * texture
    bump = np.exp(-((xx - 80) ** 2 + (yy - 80) ** 2) / (2 * 32**2))
    # Whole-frame brightness favors A, while this particular point favors B.
    # Smooth spatial illumination changes must not produce a square in the stack.
    frame_a, frame_b = truth * (1 - 0.4 * bump), truth * (0.5 + 0.7 * bump)
    path = tmp_path / "illumination.ser"
    with SerWriter(path, truth.shape, 16, "MONO") as writer:
        for frame in [frame_a] * 4 + [frame_b] * 4:
            writer.write(np.rint(frame * 65535).astype(np.uint16))
    global_result = stack_source(
        path,
        StackOptions(
            keep_percent=50, center_object=False, local_alignment=False, quality_method="brightness"
        ),
    )
    local_result = stack_source(
        path,
        StackOptions(
            keep_percent=50,
            center_object=False,
            manual_alignment_points=[(80, 80)],
            alignment_size=64,
            quality_method="brightness",
            max_local_shift=0,
        ),
    )
    assert global_result.selected == [0, 1, 2, 3]
    delta = local_result.image - global_result.image
    assert delta[80, 80] > 0.05  # Local selection really contributes different pixels.
    assert np.max(np.abs(delta[79:82, 47:50])) < 0.001  # Tapered boundary, not a hard replacement.
    assert np.max(np.abs(delta[:45])) < 1e-5
