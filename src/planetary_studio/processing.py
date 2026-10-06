"""Streaming planetary preparation and lucky imaging with local alignment points."""

from dataclasses import dataclass, asdict, field
from pathlib import Path
import json
import math
import threading
import cv2
import numpy as np
from scipy.ndimage import gaussian_filter
from .imaging import normalized, debayer, luminance, write_image, read_image
from .sources import open_source, IndexedSource
from .ser import SerWriter
from . import __version__


class Cancelled(Exception):
    pass


class FrameRejected(ValueError):
    """A frame excluded by the user's object detection settings."""


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
    alignment_min_brightness: float = 0.1
    manual_alignment_points: list[tuple[int, int]] | None = None
    local_quality: bool = True
    first_frame: int = 1
    last_frame: int = 0
    quality_method: str = "gradient"
    quality_noise_sigma: float = 0.7
    reject_missing_object: bool = False
    reject_cutoff_object: bool = False
    object_detection_threshold: float = 0.0
    min_object_size: int = 15

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
        if not np.isfinite(self.alignment_min_brightness) or not 0 <= self.alignment_min_brightness <= 1:
            raise ValueError("Minimum alignment brightness must be between 0 and 100%.")
        if self.manual_alignment_points is not None:
            for point in self.manual_alignment_points:
                if (not isinstance(point, (list, tuple)) or len(point) != 2
                        or any(not isinstance(v, (int, np.integer)) or v < 0 for v in point)):
                    raise ValueError("Alignment points must contain nonnegative integer x and y coordinates.")
        if (not isinstance(self.first_frame, int) or not isinstance(self.last_frame, int)
                or self.first_frame < 1 or self.last_frame < 0
                or (self.last_frame and self.last_frame < self.first_frame)):
            raise ValueError("Frame range must start at 1 or later and end at or after its first frame.")
        if self.quality_method not in ("gradient", "laplacian", "brenner", "brightness"):
            raise ValueError("Choose Gradient, Laplacian, Brenner, or Brightness for quality estimation.")
        if not np.isfinite(self.quality_noise_sigma) or not 0 <= self.quality_noise_sigma <= 3:
            raise ValueError("Quality noise smoothing must be between 0 and 3 pixels.")
        if not np.isfinite(self.object_detection_threshold) or not 0 <= self.object_detection_threshold <= 1:
            raise ValueError("Object detection threshold must be between 0 and 100% (0 uses Auto).")
        if not isinstance(self.min_object_size, int) or not 2 <= self.min_object_size <= 20000:
            raise ValueError("Minimum object size must be between 2 and 20,000 pixels.")


@dataclass
class StackResult:
    image: np.ndarray
    quality: np.ndarray
    selected: list[int]
    shifts: list[tuple[float, float]]
    alignment_points: list[tuple[int, int]]
    input_frames: int
    options: StackOptions
    sample_indices: list[int] | None = None
    point_size: int = 0
    source_indices: list[int] | None = None
    source_count: int = 0
    aligned_indices: list[int] | None = None
    rejected_frames: dict[int, str] = field(default_factory=dict)

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
                    "application_version": __version__,
                    "registration_method": "Smoothed intensity correlation and ECC translation",
                    "local_patch_blending": "Feathered coverage over the global stack",
                    "input_frames": self.input_frames,
                    "source_frames": self.source_count or self.input_frames,
                    "source_frame_indices": self.source_indices,
                    "globally_selected_frames": self.selected,
                    "globally_selected_source_frames": [self.source_indices[i] for i in self.selected]
                    if self.source_indices is not None else self.selected,
                    "globally_aligned_frames": self.aligned_indices,
                    "quality": self.quality.tolist(),
                    "frame_indices_are_zero_based": True,
                    "rejected_source_frames": {
                        str(self.source_indices[i] if self.source_indices is not None else i): reason
                        for i, reason in self.rejected_frames.items()
                    },
                    "shifts_xy": self.shifts,
                    "alignment_points_xy": self.alignment_points,
                    "alignment_point_size_pixels": self.point_size,
                    "options": asdict(self.options),
                    "sample_source_frame_indices": self.sample_indices,
                    "output_scale": "Lanczos resampling (not drizzle)"
                    if self.options.scale != 1
                    else "native",
                },
                indent=2,
            ),
            encoding="utf8",
        )


def quality_score(image, method="gradient", noise_sigma=0.7):
    gray = luminance(image)
    # Suppress sensor noise before scoring spatial detail.
    smooth = cv2.GaussianBlur(gray, (0, 0), noise_sigma) if noise_sigma else gray
    if method == "gradient":
        gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3)
        return float(np.mean(gx * gx + gy * gy))
    if method == "laplacian":
        return float(cv2.Laplacian(smooth, cv2.CV_32F, ksize=3).var())
    if method == "brenner":
        return float(np.mean((smooth[2:, :] - smooth[:-2, :]) ** 2) +
                     np.mean((smooth[:, 2:] - smooth[:, :-2]) ** 2))
    if method == "brightness":
        # Useful for cloud attenuation, rather than a measure of sharp detail.
        return float(gray.mean())
    raise ValueError("Unknown quality estimator.")


def detect_object(image, threshold=0.0, min_size=15):
    """Return a largest-object box and intensity centroid, or None.

    Detection uses calibrated luminance before centering or cropping. A robust
    border noise estimate prevents blank noisy frames from looking like planets.
    """
    gray = luminance(image)
    smooth = cv2.GaussianBlur(gray, (0, 0), 1.5)
    h, w = gray.shape
    edge = max(1, min(h, w) // 20)
    border = np.concatenate((smooth[:edge].ravel(), smooth[-edge:].ravel(),
                             smooth[:, :edge].ravel(), smooth[:, -edge:].ravel()))
    background = float(np.median(border))
    noise = 1.4826 * float(np.median(np.abs(border - background)))
    peak = float(smooth.max())
    floor = max(0.001, 5 * noise)
    if not threshold and peak <= background + floor:
        return None
    level = threshold or background + max(floor, (peak - background) * 0.2)
    mask = (smooth > level).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count <= 1:
        return None
    label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, bw, bh, area = map(int, stats[label])
    if min(bw, bh) < min_size or area < max(4, min_size * min_size * 0.2):
        return None
    weights = np.where(labels == label, np.maximum(smooth - background, 0), 0)
    moments = cv2.moments(weights)
    center = (moments["m10"] / moments["m00"], moments["m01"] / moments["m00"])
    return (x, y, bw, bh), center


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


def register(reference, frame, *, max_shift=None):
    """Match image structure, without letting phase whitening amplify sensor noise.

    Phase correlation proposes a coarse translation. Compare it with no motion
    using smoothed intensity correlation, then refine the better candidate with
    ECC. Downsampling bounds the cost of global registration; output coordinates
    retain the original subpixel scale. Local callers can bound displacement.
    """
    ref, moving = luminance(reference), luminance(frame)
    h, w = ref.shape
    factor = min(1.0, 256 / max(h, w))
    if factor < 1:
        size = (max(8, round(w * factor)), max(8, round(h * factor)))
        ref = cv2.resize(ref, size, interpolation=cv2.INTER_AREA)
        moving = cv2.resize(moving, size, interpolation=cv2.INTER_AREA)
    sh, sw = ref.shape
    sx, sy = sw / w, sh / h
    ref = cv2.GaussianBlur(ref, (0, 0), 1.2)
    moving = cv2.GaussianBlur(moving, (0, 0), 1.2)
    if float(ref.std()) < 1e-7 or float(moving.std()) < 1e-7:
        return 0.0, 0.0, 0.0

    def allowed(dx, dy):
        if not np.isfinite([dx, dy]).all():
            return False
        if abs(dx) > sw * 0.4 or abs(dy) > sh * 0.4:
            return False
        if max_shift is not None:
            return math.hypot(dx / sx, dy / sy) <= max_shift
        return True

    def correlation(dx, dy):
        moved = warp_shift(moving, dx, dy)
        # Ignore the unsupported border instead of correlating black padding.
        x0, x1 = max(0, math.ceil(dx)), min(sw, math.floor(sw + dx))
        y0, y1 = max(0, math.ceil(dy)), min(sh, math.floor(sh + dy))
        a, b = ref[y0:y1, x0:x1], moved[y0:y1, x0:x1]
        a, b = a - a.mean(), b - b.mean()
        denominator = math.sqrt(float(np.sum(a * a)) * float(np.sum(b * b)))
        return float(np.sum(a * b)) / denominator if denominator > 1e-12 else 0.0

    dx, dy, score = 0.0, 0.0, correlation(0, 0)
    window = cv2.createHanningWindow((sw, sh), cv2.CV_32F)
    phase, _ = cv2.phaseCorrelate(ref - ref.mean(), moving - moving.mean(), window)
    px, py = -float(phase[0]), -float(phase[1])
    if allowed(px, py):
        candidate_score = correlation(px, py)
        if candidate_score > score:
            dx, dy, score = px, py, candidate_score
    matrix = np.float32([[1, 0, -dx], [0, 1, -dy]])
    try:
        _, matrix = cv2.findTransformECC(
            ref, moving, matrix, cv2.MOTION_TRANSLATION,
            (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 40, 1e-5), None, 5,
        )
        ex, ey = -float(matrix[0, 2]), -float(matrix[1, 2])
        if allowed(ex, ey):
            refined_score = correlation(ex, ey)
            if refined_score >= score:
                dx, dy, score = ex, ey, refined_score
    except cv2.error:
        pass  # A flat patch or failed refinement retains the best coarse match.
    if score < 0.2:
        return 0.0, 0.0, 0.0
    return dx / sx, dy / sy, min(1.0, score)


class Preprocessor:
    def __init__(self, source, options: StackOptions, *, inspect_object=False):
        self.source, self.options = source, options
        self.bits = source.bits
        self.pattern = source.pattern if options.bayer == "AUTO" else options.bayer
        if self.pattern in ("RGB", "BGR"):
            self.pattern = "MONO"
        self.dark = read_image(options.dark_path) if options.dark_path else None
        self.flat = read_image(options.flat_path) if options.flat_path else None
        self._shape = None
        self.inspect_object = inspect_object
        self.object_bounds = None
        self.rejection_reason = ""

    def read(self, index, *, enforce_rejection=True):
        self.object_bounds, self.rejection_reason = None, ""
        raw = self.source.read(index)
        if self.pattern in ("RGGB", "GRBG", "GBRG", "BGGR") and raw.ndim == 3:
            # Video decoders expand raw grayscale AVI samples into three equal
            # channels. Recover the original mosaic before calibration/debayering.
            if not (np.array_equal(raw[..., 0], raw[..., 1]) and np.array_equal(raw[..., 1], raw[..., 2])):
                raise ValueError("This frame already contains color. Choose AUTO instead of a Bayer pattern.")
            raw = raw[..., 0]
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
                        med = cv2.medianBlur(np.ascontiguousarray(plane), 3)
                        plane[:] = np.where(plane > med + 0.15, med, plane)
            else:
                med = cv2.medianBlur(np.ascontiguousarray(image), 3)
                image = np.where(image > med + 0.15, med, image)
        if self.pattern in ("RGGB", "GRBG", "GBRG", "BGGR") and image.ndim == 2:
            image = normalized(debayer(np.rint(np.clip(image, 0, 1) * 65535).astype(np.uint16), self.pattern))
        detection_enabled = self.options.center_object and (
            self.inspect_object or self.options.reject_missing_object or self.options.reject_cutoff_object
            or self.options.object_detection_threshold > 0)
        detected = (detect_object(image, self.options.object_detection_threshold, self.options.min_object_size)
                    if detection_enabled else None)
        dx, dy = 0.0, 0.0
        if detection_enabled:
            if detected is None:
                if self.options.reject_missing_object or self.options.reject_cutoff_object:
                    self.rejection_reason = "Object missing or smaller than the minimum object size."
            else:
                self.object_bounds, _ = detected
                bx, by, bw, bh = self.object_bounds
                if self.options.reject_cutoff_object and (
                        bx <= 1 or by <= 1 or bx + bw >= image.shape[1] - 1 or by + bh >= image.shape[0] - 1):
                    self.rejection_reason = "Object cut off at the recording boundary."
        if self.options.center_object:
            # Keep automatic centering identical when object filtering is off.
            cx, cy = (detected[1] if detected is not None and
                      (self.options.reject_missing_object or self.options.reject_cutoff_object
                       or self.options.object_detection_threshold > 0) else object_center(image))
            dx, dy = (image.shape[1] - 1) / 2 - cx, (image.shape[0] - 1) / 2 - cy
            image = warp_shift(image, dx, dy)
        w = self.options.crop_width or image.shape[1]
        h = self.options.crop_height or image.shape[0]
        if w > image.shape[1] or h > image.shape[0]:
            raise ValueError("Crop size exceeds the sensor image size.")
        x, y = (image.shape[1] - w) // 2, (image.shape[0] - h) // 2
        if self.object_bounds is not None:
            bx, by, bw, bh = self.object_bounds
            bx, by = bx + dx - x, by + dy - y
            if self.options.reject_cutoff_object and not self.rejection_reason and (
                    bx < 0 or by < 0 or bx + bw > w or by + bh > h):
                self.rejection_reason = "Object cut off by the chosen crop size."
            self.object_bounds = (bx, by, bw, bh)
        image = np.ascontiguousarray(image[y : y + h, x : x + w], dtype=np.float32)
        if min(h, w) < 8:
            raise ValueError("Images must be at least 8×8 pixels.")
        if self._shape is not None and image.shape != self._shape:
            raise ValueError("All input frames must have the same dimensions.")
        self._shape = image.shape
        if enforce_rejection and self.rejection_reason:
            raise FrameRejected(self.rejection_reason)
        return image


def alignment_patch_size(reference, size):
    """The engine uses even patches that fit inside the prepared image."""
    return 2 * (min(size, *reference.shape[:2]) // 2)


def alignment_points(reference, size, min_brightness=0.1):
    gray = luminance(reference)
    h, w = gray.shape
    size = alignment_patch_size(reference, size)
    radius, step = size // 2, max(8, size // 2)
    smooth = cv2.GaussianBlur(gray, (0, 0), 0.8)
    # Anchor the overlapping grid at the bright region, rather than the top-left
    # of the sensor. Test each point's center separately from the whole patch.
    weight = np.maximum(smooth - min_brightness, 0)
    moments = cv2.moments(weight)
    if moments["m00"] > 1e-8:
        cx = int(round(moments["m10"] / moments["m00"]))
        cy = int(round(moments["m01"] / moments["m00"]))
    else:
        cx, cy = w // 2, h // 2
    xs = range(cx + math.ceil((radius - cx) / step) * step, w - radius + 1, step)
    ys = range(cy + math.ceil((radius - cy) / step) * step, h - radius + 1, step)
    result = []
    for y in ys:
        for x in xs:
            if float(np.median(smooth[max(0, y - 2):y + 3, max(0, x - 2):x + 3])) < min_brightness:
                continue
            patch = gray[y - radius : y + radius, x - radius : x + radius]
            if float(patch.std()) > 0.005 and quality_score(patch) > 1e-6:
                result.append((x, y))
    return result


def planned_alignment_points(reference, options):
    if not options.local_alignment:
        return []
    if options.manual_alignment_points is None:
        return alignment_points(reference, options.alignment_size, options.alignment_min_brightness)
    radius = alignment_patch_size(reference, options.alignment_size) // 2
    h, w = reference.shape[:2]
    points = list(dict.fromkeys(tuple(p) for p in options.manual_alignment_points))
    if any(not (radius <= x <= w - radius and radius <= y <= h - radius) for x, y in points):
        raise ValueError("An alignment point falls outside the prepared image. Regenerate the grid or move the point inward.")
    return points


def input_frame_range(count, options):
    """Return zero-based source indices from the inclusive UI frame range."""
    first = options.first_frame - 1
    end = min(options.last_frame or count, count)
    if first >= end:
        raise ValueError(f"The selected frame range is empty. This recording contains {count:,} frames.")
    return range(first, end)


def stack_source(path, options: StackOptions | None = None, progress=None, cancel=None,
                 frame_indices=None) -> StackResult:
    options = options or StackOptions()
    options.validate()
    progress = progress or (lambda *_: None)
    cancel = cancel or threading.Event()

    def check():
        if cancel.is_set():
            raise Cancelled("Processing cancelled.")

    source = open_source(path)
    try:
        source_count = source.count
        eligible = input_frame_range(source_count, options)
        if frame_indices is not None:
            if any(i not in eligible for i in frame_indices):
                raise ValueError("A preview sample frame lies outside the selected frame range.")
            source = IndexedSource(source, frame_indices)
            source_indices = list(frame_indices)
        elif len(eligible) != source_count:
            source_indices = list(eligible)
            source = IndexedSource(source, source_indices)
        else:
            source_indices = None
        prep = Preprocessor(source, options)
        n = source.count
        scores = np.zeros(n, np.float32)
        rejected = {}
        best, best_index, best_score = None, 0, -1
        for i in range(n):
            check()
            try:
                frame = prep.read(i)
            except FrameRejected as exc:
                rejected[i] = str(exc)
                progress(15 * (i + 1) / n, f"Rejected frame {i + 1} of {n}: {exc}")
                continue
            score = quality_score(frame, options.quality_method, options.quality_noise_sigma)
            scores[i] = score
            if score > best_score:
                best, best_index, best_score = frame, i, score
            progress(15 * (i + 1) / n, f"Assessing frame {i + 1} of {n}")
        accepted = np.array([i for i in range(n) if i not in rejected], dtype=int)
        if not len(accepted):
            raise ValueError("Every frame was rejected. Preview a frame and adjust the detection threshold, minimum object size, or crop; or turn off object rejection.")
        if best_score <= 1e-10:
            raise ValueError("No usable image detail found in this recording.")
        keep = max(1, math.ceil(len(accepted) * options.keep_percent / 100))
        ranked = accepted[np.argsort(-scores[accepted], kind="stable")]
        selected = sorted(ranked[:keep].tolist())
        # Build a low-noise reference from the best frames after global registration.
        reference_indices = ranked[: min(32, keep)]
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
        points = planned_alignment_points(reference, options)
        radius = alignment_patch_size(reference, options.alignment_size) // 2
        shifts = [(0.0, 0.0)] * n
        local_scores = np.zeros((n, len(points)), np.float32)
        valid_frames = np.zeros(n, bool)
        # Local quality needs to rank every frame at each point. Global quality
        # uses the same selected frames at every point and can skip the rest.
        alignment_indices = accepted.tolist() if points and options.local_quality else selected
        for j, i in enumerate(alignment_indices):
            check()
            frame = prep.read(i)
            dx, dy, response = register(reference, frame)
            valid_frames[i] = response >= 0.02 or i == best_index
            shifts[i] = (dx, dy)
            moved = warp_shift(frame, dx, dy)
            if options.local_quality:
                for p, (x, y) in enumerate(points):
                    local_scores[i, p] = quality_score(moved[y - radius : y + radius, x - radius : x + radius],
                                                     options.quality_method, options.quality_noise_sigma)
            progress(25 + 25 * (j + 1) / len(alignment_indices),
                     f"Aligning selected frame {j + 1} of {len(alignment_indices)}")
        valid_indices = np.flatnonzero(valid_frames)
        if not len(valid_indices):
            raise ValueError("Alignment failed for every frame.")
        global_keep = min(keep, len(valid_indices))
        selected = sorted(
            valid_indices[np.argsort(-scores[valid_indices], kind="stable")[:global_keep]].tolist()
        )
        membership = np.zeros((n, len(points)), bool)
        for p in range(len(points)):
            indices = (valid_indices[np.argsort(-local_scores[valid_indices, p], kind="stable")[:global_keep]]
                       if options.local_quality else selected)
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
                lx, ly, response = register(ref_patch, patch, max_shift=options.max_local_shift)
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
        local_result = np.divide(local_sum, divisor, out=np.zeros_like(local_sum), where=divisor > 1e-6)
        # Keep the global stack under the patches. Normalizing a lone Hann patch
        # cancels its feathering, so replacing pixels wherever weight > 0 makes
        # rectangular seams. Blend according to coverage to retain the taper.
        coverage = np.clip(local_weight / global_keep, 0, 1)
        if reference.ndim == 3:
            coverage = coverage[..., None]
        result = result * (1 - coverage) + local_result * coverage
        result = np.clip(result, 0, 1).astype(np.float32)
        if options.scale != 1:
            h, w = result.shape[:2]
            result = cv2.resize(
                result, (round(w * options.scale), round(h * options.scale)), interpolation=cv2.INTER_LANCZOS4
            )
            result = np.clip(result, 0, 1)
        progress(100, f"Stacked {len(selected)} of {len(accepted)} usable frames; {len(rejected)} rejected")
        return StackResult(result, scores, selected, shifts, points, n, options,
                           list(frame_indices) if frame_indices is not None else None, point_size=2 * radius,
                           source_indices=source_indices, source_count=source_count,
                           aligned_indices=list(alignment_indices), rejected_frames=rejected)
    finally:
        source.close()


def protect_source_output(source, output):
    paths = source.paths if hasattr(source, "paths") else [source.path]
    destination = Path(output).resolve()
    if any(Path(path).resolve() == destination for path in paths):
        raise ValueError("Choose a different output file to preserve the source recording or images.")


def export_prepared(path, output, options=None, progress=None, cancel=None, *, overwrite=False):
    options = options or StackOptions()
    options.validate()
    source = open_source(path)
    writer = None
    try:
        protect_source_output(source, output)
        if Path(output).exists() and not overwrite:
            raise FileExistsError("Output recording already exists.")
        indices = input_frame_range(source.count, options)
        if len(indices) != source.count:
            source = IndexedSource(source, indices)
        prep = Preprocessor(source, options)
        rejected = 0
        for i in range(source.count):
            if cancel and cancel.is_set():
                raise Cancelled("Export cancelled; partial recording has been finalized.")
            try:
                frame = prep.read(i)
            except FrameRejected:
                rejected += 1
            else:
                if writer is None:
                    writer = SerWriter(output, frame.shape, 16, "RGB" if frame.ndim == 3 else "MONO",
                                       overwrite=overwrite)
                writer.write(np.rint(np.clip(frame, 0, 1) * 65535).astype(np.uint16))
            if progress:
                progress(100 * (i + 1) / source.count,
                         f"Prepared {i + 1 - rejected} frames; rejected {rejected}")
        if writer is None:
            raise ValueError("Every frame was rejected. Adjust the object detection settings before exporting.")
    finally:
        if writer is not None:
            writer.close()
        source.close()


def create_master(path, output, progress=None, cancel=None, *, overwrite=False):
    source = open_source(path)
    try:
        protect_source_output(source, output)
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
        write_image(output, (total / source.count).astype(np.float32), overwrite=overwrite)
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
    ai_denoise_amount=0.0,
    ai_denoise_noise=3.0,
):
    if not 0.1 <= gamma <= 5 or not 0 <= saturation <= 3 or len(balance) != 3:
        raise ValueError("Invalid color adjustment settings.")
    out = rgb_align(image) if align_rgb else image.copy()
    out = wavelet_sharpen(out, gains, denoise)
    if rl_iterations:
        out = richardson_lucy(out, rl_sigma, rl_iterations)
    if ai_denoise_amount:
        from .ai_denoise import denoise_image

        out = denoise_image(out, amount=ai_denoise_amount, noise_level=ai_denoise_noise)
    if out.ndim == 3:
        out *= np.asarray(balance, np.float32)
        gray = luminance(out)[..., None]
        out = gray + (out - gray) * saturation
    return np.power(np.clip(out, 0, 1), 1 / gamma).astype(np.float32)
