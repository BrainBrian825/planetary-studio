from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from planetary_studio.ai_denoise import denoise_image
from planetary_studio.cameras.simulator import planet_image
from planetary_studio.processing import finish_image, richardson_lucy, wavelet_sharpen


@pytest.mark.parametrize("mono", [False, True])
def test_pretrained_models_reduce_error_against_known_planet(mono):
    truth = planet_image(128, 96).astype(np.float32)
    if mono:
        truth = truth[..., 1]
    noisy = np.clip(truth + np.random.default_rng(42).normal(0, 6 / 255, truth.shape), 0, 1)
    cleaned = denoise_image(noisy, amount=1, noise_level=6)
    assert cleaned.shape == truth.shape and cleaned.dtype == np.float32
    assert np.isfinite(cleaned).all() and 0 <= cleaned.min() <= cleaned.max() <= 1
    assert np.mean((cleaned - truth) ** 2) < np.mean((noisy - truth) ** 2) * 0.2


@pytest.mark.parametrize("mono", [False, True])
def test_tiled_output_matches_whole_image_including_odd_edges(mono):
    shape = (145, 179) if mono else (145, 179, 3)
    image = np.random.default_rng(16).random(shape, dtype=np.float32)
    tiled = denoise_image(image, 0.7, 5, tile_size=64)
    whole = denoise_image(image, 0.7, 5, tile_size=512)
    assert np.max(np.abs(tiled - whole)) < 3e-6


@pytest.mark.parametrize("shape", [(1, 1), (1, 3, 3), (3, 1, 3)])
def test_small_images_are_supported(shape):
    result = denoise_image(np.full(shape, 0.3, dtype=np.float32), 0.3, 3)
    assert result.shape == shape and np.isfinite(result).all()


def test_zero_strength_and_zero_noise_bypass_model_loading(monkeypatch):
    import planetary_studio.ai_denoise as ai

    def fail(*_):
        raise AssertionError("Disabled cleanup must not load a model")

    monkeypatch.setattr(ai, "_network", fail)
    image = np.random.default_rng(2).random((16, 18, 3), dtype=np.float32)
    assert np.array_equal(ai.denoise_image(image, amount=0), image)
    assert np.array_equal(ai.denoise_image(image, noise_level=0), image)


def test_cleanup_preserves_float_precision_and_blends_strength():
    image = np.full((24, 32, 3), 0.3, dtype=np.float32)
    image += np.random.default_rng(31).normal(0, 0.02, image.shape).astype(np.float32)
    full = denoise_image(image, 1, 5)
    half = denoise_image(image, 0.5, 5)
    assert np.max(np.abs(half - (image + full) / 2)) < 2e-7
    assert np.any(np.abs(half * 255 - np.rint(half * 255)) > 0.001)


def test_finishing_order_is_sharpen_then_ai_then_tone():
    image = planet_image(80, 64).astype(np.float32)
    image += np.random.default_rng(44).normal(0, 0.01, image.shape).astype(np.float32)
    options = {"gains": (0.5, 0.3, 0, 0, 0, 0), "rl_iterations": 2}
    # Apply neutral tone arithmetic only once, after cleanup. Calling the full
    # finisher before the model introduces another RGB subtraction/addition and
    # makes this reference depend on tiny platform-specific float rounding.
    sharpened = richardson_lucy(wavelet_sharpen(image, gains=options["gains"]), iterations=2)
    expected = finish_image(denoise_image(sharpened, 0.4, 3), gamma=1.2)
    actual = finish_image(image, **options, ai_denoise_amount=0.4, ai_denoise_noise=3, gamma=1.2)
    assert np.max(np.abs(actual - expected)) < 2e-7
    assert np.array_equal(finish_image(image, **options, ai_denoise_amount=0), finish_image(image, **options))


def test_concurrent_inference_does_not_mix_images():
    images = [np.random.default_rng(i).random((26, 32, 3), dtype=np.float32) for i in (1, 2)]
    expected = [denoise_image(image, 0.5, 3) for image in images]
    with ThreadPoolExecutor(2) as workers:
        actual = list(workers.map(lambda image: denoise_image(image, 0.5, 3), images))
    for result, reference in zip(actual, expected):
        assert np.array_equal(result, reference)


@pytest.mark.parametrize("kwargs", [{"amount": -1}, {"amount": 2}, {"amount": np.nan},
                                    {"noise_level": -1}, {"noise_level": 51}, {"noise_level": np.inf}])
def test_invalid_settings_fail_clearly(kwargs):
    with pytest.raises(ValueError):
        denoise_image(np.zeros((4, 4), dtype=np.float32), **kwargs)


def test_missing_model_does_not_silently_skip_requested_cleanup(tmp_path, monkeypatch):
    import planetary_studio.ai_denoise as ai

    monkeypatch.setattr(ai, "MODELS", tmp_path)
    monkeypatch.setattr(ai, "_models", {})
    with pytest.raises(RuntimeError, match="Reinstall"):
        ai.denoise_image(np.zeros((4, 4), dtype=np.float32), 0.5, 3)
