"""Local FFDNet inference, using OpenCV's CPU backend and bundled MIT models."""

import hashlib
import json
import threading
from pathlib import Path

import cv2
import numpy as np

MODELS = Path(__file__).resolve().parent / "assets/models"
_models = {}
_load_lock = threading.Lock()


def _network(kind):
    with _load_lock:
        if kind not in _models:
            try:
                entry = json.loads((MODELS / "manifest.json").read_text())["variants"][kind]
                path = MODELS / entry["file"]
                if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                    raise ValueError("model checksum differs")
                net = cv2.dnn.readNetFromONNX(str(path))
                net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                # CPU is the OpenCV backend's default target. OpenCV 5's graph
                # engine does not accept an explicit target override.
            except (OSError, ValueError, KeyError, cv2.error) as error:
                raise RuntimeError("The bundled AI denoiser could not load. Reinstall Planetary Studio.") from error
            _models[kind] = net, threading.Lock()
        return _models[kind]


def _infer(net, image, noise_level):
    """FFDNet pixel unshuffle, noise map, convolution stack, pixel shuffle."""
    height, width, channels = image.shape
    packed = image.transpose(2, 0, 1).reshape(channels, height // 2, 2, width // 2, 2)
    packed = packed.transpose(0, 2, 4, 1, 3).reshape(1, channels * 4, height // 2, width // 2)
    noise = np.full((1, 1, height // 2, width // 2), noise_level / 255, dtype=np.float32)
    net.setInput(np.ascontiguousarray(np.concatenate((packed, noise), axis=1)))
    result = net.forward()
    result = result.reshape(channels, 2, 2, height // 2, width // 2)
    return result.transpose(3, 1, 4, 2, 0).reshape(height, width, channels)


def denoise_image(image, amount=0.5, noise_level=3.0, *, tile_size=192):
    """Blend FFDNet cleanup into a normalized mono/RGB image without quantization.

    Noise level is a standard deviation on a 0–255 brightness scale. A value of
    zero, or zero amount, bypasses inference exactly. The 32-pixel context exceeds
    both networks' receptive radii; even tile boundaries preserve shuffle phase.
    Inference is serialized per model because OpenCV networks contain mutable state.
    """
    if not np.isfinite(amount) or not 0 <= amount <= 1:
        raise ValueError("AI denoise amount must be between 0 and 1.")
    if not np.isfinite(noise_level) or not 0 <= noise_level <= 50:
        raise ValueError("AI noise level must be between 0 and 50.")
    if not isinstance(tile_size, int) or tile_size < 2 or tile_size % 2:
        raise ValueError("AI tile size must be a positive even number.")
    original = np.asarray(image, dtype=np.float32)
    if original.ndim not in (2, 3) or (original.ndim == 3 and original.shape[2] != 3):
        raise ValueError("AI denoising needs a monochrome or RGB image.")
    if 0 in original.shape or not np.isfinite(original).all():
        raise ValueError("AI denoising needs a nonempty image with finite values.")
    if amount == 0 or noise_level == 0:
        return original.copy()
    source = original[..., None] if original.ndim == 2 else original
    height, width = source.shape[:2]
    source = np.pad(source, ((0, height % 2), (0, width % 2), (0, 0)), mode="edge")
    result = np.empty((height, width, source.shape[2]), dtype=np.float32)
    net, lock = _network("gray" if original.ndim == 2 else "color")
    with lock:
        for y in range(0, height, tile_size):
            for x in range(0, width, tile_size):
                bottom, right = min(height, y + tile_size), min(width, x + tile_size)
                top, left = max(0, y - 32), max(0, x - 32)
                end_y, end_x = min(source.shape[0], bottom + 32), min(source.shape[1], right + 32)
                tile = _infer(net, source[top:end_y, left:end_x], noise_level)
                result[y:bottom, x:right] = tile[y - top:bottom - top, x - left:right - left]
    if original.ndim == 2:
        result = result[..., 0]
    if not np.isfinite(result).all():
        raise RuntimeError("AI denoising produced invalid pixels; no image was saved.")
    result = np.clip(result, 0, 1)
    return np.clip(original + amount * (result - original), 0, 1).astype(np.float32)
