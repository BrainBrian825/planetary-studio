"""Streaming planetary preparation and lucky imaging with local alignment points."""

from dataclasses import dataclass, asdict
from pathlib import Path
import json
import math
import threading
import cv2
import numpy as np
from scipy.ndimage import gaussian_filter, median_filter
from .imaging import normalized, debayer, luminance, write_image, read_image
from .sources import open_source
from .ser import SerWriter


class Cancelled(Exception):
    pass


@dataclass
class StackOptions:
    keep_percent: float = 25.0
    bayer: str = "AUTO"
    center_object: bool = True
    crop_width: int = 0
    crop_height: int = 0
    dark_path: str = ""
    flat_path: str = ""
    remove_hot_pixels: bool = False
    local_alignment: bool = True
    alignment_size: int = 64
    max_local_shift: float = 8.0
    scale: float = 1.0
    normalize_brightness: bool = False

    def validate(self):
        if not 0 < self.keep_percent <= 100:
            raise ValueError("Keep percentage must be greater than 0 and no more than 100.")
        if self.bayer not in ("AUTO", "MONO", "RGGB", "GRBG", "GBRG", "BGGR"):
            raise ValueError("Invalid Bayer pattern.")
        if self.alignment_size < 16 or self.alignment_size > 512:
            raise ValueError("Alignment point size must be between 16 and 512 pixels.")
        if not 0 <= self.max_local_shift <= 32:
            raise ValueError("Local displacement must be between 0 and 32 pixels.")
        if self.scale not in (1.0, 1.5, 2.0):
            raise ValueError("Output scale must be 1, 1.5, or 2.")
        if self.crop_width < 0 or self.crop_height < 0:
            raise ValueError("Crop size cannot be negative.")


@dataclass
class StackResult:
    image: np.ndarray
    quality: np.ndarray
    selected: list[int]
    shifts: list[tuple[float, float]]
    alignment_points: list[tuple[int, int]]
    input_frames: int
    options: StackOptions

    def save(self, path, overwrite=False):
        path = Path(path)
        report_path = path.with_suffix(path.suffix + ".json")
        if not overwrite and (path.exists() or report_path.exists()):
            raise FileExistsError("Image or processing report already exists.")
        write_image(path, self.image, overwrite)
        report_path.write_text(
            json.dumps(
                {
                    "application": "Planetary Studio",
                    "input_frames": self.input_frames,
                    "globally_selected_frames": self.selected,
                    "quality": self.quality.tolist(),
                    "shifts_xy": self.shifts,
                    "alignment_points_xy": self.alignment_points,
                    "options": asdict(self.options),
                    "output_scale": "Lanczos resampling (not drizzle)"
                    if self.options.scale != 1
                    else "native",
                },
                indent=2,
            ),
            encoding="utf8",
        )


def quality_score(image):
    gray = luminance(image)
    # Suppress sensor noise before scoring spatial detail.
    smooth = cv2.GaussianBlur(gray, (0, 0), 0.7)
    gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(gx * gx + gy * gy))


def object_center(image):
    gray = luminance(image)
    smooth = cv2.GaussianBlur(gray, (0, 0), 2)
    background = float(np.median(smooth))
    high = float(np.percentile(smooth, 99.5))
    if high - background < 1e-6:
        return ((gray.shape[1] - 1) / 2, (gray.shape[0] - 1) / 2)
    mask = (smooth > background + (high - background) * 0.2).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
    if count <= 1:
        return ((gray.shape[1] - 1) / 2, (gray.shape[0] - 1) / 2)
    label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    weights = np.where(labels == label, np.maximum(smooth - background, 0), 0)
    yy, xx = np.indices(gray.shape)
    total = weights.sum()
    return (float((weights * xx).sum() / total), float((weights * yy).sum() / total))


def warp_shift(image, dx, dy):
    h, w = image.shape[:2]
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(
        image, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0
    )


def register(reference, frame):
    ref, moving = luminance(reference), luminance(frame)
    if float(ref.std()) < 1e-7 or float(moving.std()) < 1e-7:
        return 0.0, 0.0, 0.0
    h, w = ref.shape
    window = cv2.createHanningWindow((w, h), cv2.CV_32F)
    shift, response = cv2.phaseCorrelate(ref - ref.mean(), moving - moving.mean(), window)
    dx, dy = -float(shift[0]), -float(shift[1])
    if not np.isfinite([dx, dy, response]).all() or abs(dx) > w * 0.4 or abs(dy) > h * 0.4:
        return 0.0, 0.0, 0.0
    return dx, dy, float(response)


class Preprocessor:
    def __init__(self, source, options: StackOptions):
        self.source, self.options = source, options
        self.bits = source.bits
        self.pattern = source.pattern if options.bayer == "AUTO" else options.bayer
        if self.pattern in ("RGB", "BGR"):
            self.pattern = "MONO"
        self.dark = read_image(options.dark_path) if options.dark_path else None
        self.flat = read_image(options.flat_path) if options.flat_path else None
        self._shape = None

    def read(self, index):
        raw = self.source.read(index)
        image = normalized(raw, self.bits)
        for name, master in (("Dark", self.dark), ("Flat", self.flat)):
            if master is not None and master.shape != raw.shape:
                raise ValueError(f"{name} master dimensions must match the original sensor image.")
        if self.dark is not None:
            image = np.maximum(image - normalized(self.dark, self.bits), 0)
        if self.flat is not None:
            flat = normalized(self.flat, self.bits)
            if self.dark is not None:
                flat = np.maximum(flat - normalized(self.dark, self.bits), 0)
            valid = flat > 1e-6
            if not valid.any():
                raise ValueError("Flat master has no usable illuminated pixels.")
            flat = flat / float(np.median(flat[valid]))
            image = image / np.maximum(flat, 0.05)
        if self.options.remove_hot_pixels:
            if self.pattern in ("RGGB", "GRBG", "GBRG", "BGGR"):
                # Filter each CFA plane separately to preserve its color pattern.
                for y in range(2):
                    for x in range(2):
                        plane = image[y::2, x::2]
                        med = median_filter(plane, size=3)
                        plane[:] = np.where(plane > med + 0.15, med, plane)
            else:
                med = median_filter(image, size=(3, 3, 1) if image.ndim == 3 else 3)
                image = np.where(image > med + 0.15, med, image)
        if self.pattern in ("RGGB", "GRBG", "GBRG", "BGGR") and image.ndim == 2:
            image = normalized(debayer(np.rint(np.clip(image, 0, 1) * 65535).astype(np.uint16), self.pattern))
        if self.options.center_object:
            cx, cy = object_center(image)
            image = warp_shift(image, (image.shape[1] - 1) / 2 - cx, (image.shape[0] - 1) / 2 - cy)
        w = self.options.crop_width or image.shape[1]
        h = self.options.crop_height or image.shape[0]
        if w > image.shape[1] or h > image.shape[0]:
            raise ValueError("Crop size exceeds the sensor image size.")
        x, y = (image.shape[1] - w) // 2, (image.shape[0] - h) // 2
        image = np.ascontiguousarray(image[y : y + h, x : x + w], dtype=np.float32)
        if min(h, w) < 8:
            raise ValueError("Images must be at least 8×8 pixels.")
        if self._shape is not None and image.shape != self._shape:
            raise ValueError("All input frames must have the same dimensions.")
        self._shape = image.shape
        return image


def alignment_points(reference, size):
    gray = luminance(reference)
    h, w = gray.shape
    size = min(size, h, w)
    radius, step = size // 2, max(8, size // 2)
    result = []
    for y in range(radius, h - radius + 1, step):
        for x in range(radius, w - radius + 1, step):
            patch = gray[y - radius : y + radius, x - radius : x + radius]
            if float(patch.std()) > 0.005 and quality_score(patch) > 1e-6:
                result.append((x, y))
    return result


def stack_source(path, options: StackOptions | None = None, progress=None, cancel=None) -> StackResult:
    options = options or StackOptions()
    options.validate()
    progress = progress or (lambda *_: None)
    cancel = cancel or threading.Event()

    def check():
        if cancel.is_set():
            raise Cancelled("Processing cancelled.")

    source = open_source(path)
    try:
        prep = Preprocessor(source, options)
        n = source.count
        scores = np.zeros(n, np.float32)
        best, best_index, best_score = None, 0, -1
        for i in range(n):
            check()
            frame = prep.read(i)
            score = quality_score(frame)
            scores[i] = score
            if score > best_score:
                best, best_index, best_score = frame, i, score
            progress(15 * (i + 1) / n, f"Assessing frame {i + 1} of {n}")
        if best_score <= 1e-10:
            raise ValueError("No usable image detail found in this recording.")
        keep = max(1, math.ceil(n * options.keep_percent / 100))
        selected = sorted(np.argsort(-scores, kind="stable")[:keep].tolist())
        # Build a low-noise reference from the best frames after global registration.
        reference_indices = np.argsort(-scores, kind="stable")[: min(32, keep)]
        ref_sum = np.zeros_like(best, dtype=np.float64)
        ref_weight = np.zeros(best.shape[:2], dtype=np.float64)
        for j, i in enumerate(reference_indices):
            check()
            frame = prep.read(int(i))
            dx, dy, response = register(best, frame)
            if response < 0.02 and int(i) != best_index:
                continue
            mask = warp_shift(np.ones(frame.shape[:2], np.float32), dx, dy)
            ref_sum += warp_shift(frame, dx, dy)
            ref_weight += mask
            progress(15 + 10 * (j + 1) / len(reference_indices), "Building alignment reference")
        divisor = ref_weight[..., None] if best.ndim == 3 else ref_weight
        reference = np.divide(ref_sum, divisor, out=np.zeros_like(ref_sum), where=divisor > 0).astype(
            np.float32
        )
        points = alignment_points(reference, options.alignment_size) if options.local_alignment else []
        radius = min(options.alignment_size, *reference.shape[:2]) // 2
        shifts = [(0.0, 0.0)] * n
        local_scores = np.zeros((n, len(points)), np.float32)
        valid_frames = np.zeros(n, bool)
        for i in range(n):
            check()
            frame = prep.read(i)
            dx, dy, response = register(reference, frame)
            valid_frames[i] = response >= 0.02 or i == best_index
            shifts[i] = (dx, dy)
            moved = warp_shift(frame, dx, dy)
            for p, (x, y) in enumerate(points):
                local_scores[i, p] = quality_score(moved[y - radius : y + radius, x - radius : x + radius])
            progress(25 + 25 * (i + 1) / n, f"Aligning frame {i + 1} of {n}")
        valid_indices = np.flatnonzero(valid_frames)
        if not len(valid_indices):
            raise ValueError("Alignment failed for every frame.")
        global_keep = min(keep, len(valid_indices))
        selected = sorted(
            valid_indices[np.argsort(-scores[valid_indices], kind="stable")[:global_keep]].tolist()
        )
        membership = np.zeros((n, len(points)), bool)
        for p in range(len(points)):
            indices = valid_indices[np.argsort(-local_scores[valid_indices, p], kind="stable")[:global_keep]]
            membership[indices, p] = True
        global_sum = np.zeros_like(reference, dtype=np.float64)
        global_weight = np.zeros(reference.shape[:2], np.float64)
        local_sum = np.zeros_like(reference, dtype=np.float64)
        local_weight = np.zeros(reference.shape[:2], np.float64)
        feather_1d = np.hanning(2 * radius).astype(np.float32)
        feather = np.outer(feather_1d, feather_1d)
        selected_set = set(selected)
        needed = sorted(selected_set | set(np.flatnonzero(membership.any(axis=1))))
        for j, i in enumerate(needed):
            check()
            frame = prep.read(i)
            dx, dy = shifts[i]
            moved = warp_shift(frame, dx, dy)
            valid = warp_shift(np.ones(frame.shape[:2], np.float32), dx, dy)
            if options.normalize_brightness:
                target = float(luminance(reference).mean())
                actual = float(luminance(moved).mean())
                if actual > 1e-7:
                    moved *= np.clip(target / actual, 0.5, 2.0)
            if i in selected_set:
                global_sum += moved
                global_weight += valid
            for p in np.flatnonzero(membership[i]):
                x, y = points[p]
                region = np.s_[y - radius : y + radius, x - radius : x + radius]
                patch, ref_patch = moved[region], reference[region]
                lx, ly, response = register(ref_patch, patch)
                if response < 0.05 or math.hypot(lx, ly) > options.max_local_shift:
                    lx, ly = 0.0, 0.0
                # Sample the full globally aligned image so patch edges remain valid.
                xx, yy = np.meshgrid(
                    np.arange(x - radius, x + radius, dtype=np.float32) - lx,
                    np.arange(y - radius, y + radius, dtype=np.float32) - ly,
                )
                patch = cv2.remap(moved, xx, yy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
                mask = cv2.remap(valid, xx, yy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
                weight = feather * mask
                local_sum[region] += patch * (feather[..., None] if patch.ndim == 3 else feather)
                local_weight[region] += weight
            progress(50 + 48 * (j + 1) / len(needed), f"Stacking frame {j + 1} of {len(needed)}")
        divisor = global_weight[..., None] if reference.ndim == 3 else global_weight
        result = np.divide(global_sum, divisor, out=np.zeros_like(global_sum), where=divisor > 0)
        divisor = local_weight[..., None] if reference.ndim == 3 else local_weight
        np.divide(local_sum, divisor, out=result, where=divisor > 1e-6)
        result = np.clip(result, 0, 1).astype(np.float32)
        if options.scale != 1:
            h, w = result.shape[:2]
            result = cv2.resize(
                result, (round(w * options.scale), round(h * options.scale)), interpolation=cv2.INTER_LANCZOS4
            )
            result = np.clip(result, 0, 1)
        progress(100, f"Stacked {len(selected)} of {n} frames")
        return StackResult(result, scores, selected, shifts, points, n, options)
    finally:
        source.close()


def export_prepared(path, output, options=None, progress=None, cancel=None):
    options = options or StackOptions()
    options.validate()
    source = open_source(path)
    try:
        prep = Preprocessor(source, options)
        first = prep.read(0)
        with SerWriter(output, first.shape, 16, "RGB" if first.ndim == 3 else "MONO") as writer:
            for i in range(source.count):
                if cancel and cancel.is_set():
                    raise Cancelled("Export cancelled; partial recording has been finalized.")
                frame = first if i == 0 else prep.read(i)
                writer.write(np.rint(np.clip(frame, 0, 1) * 65535).astype(np.uint16))
                if progress:
                    progress(100 * (i + 1) / source.count, f"Prepared frame {i + 1} of {source.count}")
    finally:
        source.close()


def create_master(path, output, progress=None, cancel=None):
    source = open_source(path)
    try:
        total = normalized(source.read(0), source.bits).astype(np.float64)
        for i in range(1, source.count):
            if cancel and cancel.is_set():
                raise Cancelled("Master creation cancelled.")
            frame = normalized(source.read(i), source.bits)
            if frame.shape != total.shape:
                raise ValueError("Calibration dimensions differ.")
            total += frame
            if progress:
                progress(100 * (i + 1) / source.count, "Averaging calibration frames")
        # Float FITS retains native normalized calibration values without quantization.
        write_image(output, (total / source.count).astype(np.float32))
    finally:
        source.close()


def wavelet_sharpen(image, gains=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0), denoise=0.003):
    if len(gains) != 6 or any(not -1 <= g <= 8 for g in gains) or denoise < 0:
        raise ValueError("Invalid wavelet settings.")
    original = np.asarray(image, dtype=np.float32)
    current = original.copy()
    result = original.copy()
    for level, gain in enumerate(gains):
        sigma = 2**level * 0.65
        blur = gaussian_filter(
            current, sigma=(sigma, sigma, 0) if current.ndim == 3 else sigma, mode="reflect"
        )
        detail = current - blur
        if denoise:
            detail = np.sign(detail) * np.maximum(np.abs(detail) - denoise, 0)
        result += gain * detail
        current = blur
    return np.clip(result, 0, 1)


def richardson_lucy(image, sigma=1.0, iterations=10):
    if not 0.3 <= sigma <= 5 or not 0 <= iterations <= 100:
        raise ValueError("Invalid deconvolution settings.")
    spatial = (sigma, sigma, 0) if image.ndim == 3 else sigma
    estimate = np.maximum(image, 1e-6).astype(np.float32)
    for _ in range(iterations):
        relative = image / np.maximum(gaussian_filter(estimate, spatial, mode="reflect"), 1e-6)
        estimate *= gaussian_filter(relative, spatial, mode="reflect")
    return np.clip(estimate, 0, 1)


def rgb_align(image):
    if image.ndim != 3:
        return image.copy()
    result = image.copy()
    for channel in (0, 2):
        dx, dy, response = register(image[..., 1], image[..., channel])
        if response > 0.05:
            result[..., channel] = warp_shift(image[..., channel], dx, dy)
    return result


def finish_image(
    image,
    gains=(0.0,) * 6,
    denoise=0.003,
    rl_iterations=0,
    rl_sigma=1.0,
    gamma=1.0,
    saturation=1.0,
    balance=(1.0, 1.0, 1.0),
    align_rgb=False,
):
    if not 0.1 <= gamma <= 5 or not 0 <= saturation <= 3 or len(balance) != 3:
        raise ValueError("Invalid color adjustment settings.")
    out = rgb_align(image) if align_rgb else image.copy()
    out = wavelet_sharpen(out, gains, denoise)
    if rl_iterations:
        out = richardson_lucy(out, rl_sigma, rl_iterations)
    if out.ndim == 3:
        out *= np.asarray(balance, np.float32)
        gray = luminance(out)[..., None]
        out = gray + (out - gray) * saturation
    return np.power(np.clip(out, 0, 1), 1 / gamma).astype(np.float32)
