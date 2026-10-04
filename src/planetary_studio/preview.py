"""Quick previews use the same processing settings as a full stack."""

from dataclasses import dataclass
import numpy as np
import cv2
from .imaging import normalized
from .processing import (Preprocessor, planned_alignment_points, alignment_patch_size,
                         input_frame_range, stack_source, Cancelled)
from .sources import open_source


@dataclass
class FramePreview:
    image: np.ndarray
    index: int
    points: list[tuple[int, int]]
    point_size: float
    rejection_reason: str = ""
    object_bounds: tuple[float, float, float, float] | None = None


def preview_frame(path, index, options, prepared=True, progress=None, cancel=None):
    options.validate()
    source = open_source(path)
    try:
        if cancel and cancel.is_set():
            raise Cancelled("Preview cancelled.")
        prep = Preprocessor(source, options, inspect_object=True) if prepared else None
        image = prep.read(index, enforce_rejection=False) if prepared else normalized(source.read(index), source.bits)
        bounds = prep.object_bounds if prepared else None
        points = planned_alignment_points(image, options) if prepared else []
        point_size = alignment_patch_size(image, options.alignment_size)
        if prepared and options.scale != 1:
            h, w = image.shape[:2]
            image = np.clip(cv2.resize(image, (round(w * options.scale), round(h * options.scale)),
                                       interpolation=cv2.INTER_LANCZOS4), 0, 1)
            points = [(x * options.scale, y * options.scale) for x, y in points]
            bounds = tuple(v * options.scale for v in bounds) if bounds is not None else None
        if cancel and cancel.is_set():
            raise Cancelled("Preview cancelled.")
        return FramePreview(image, index, points, point_size * (options.scale if prepared else 1),
                            prep.rejection_reason if prepared else "", bounds)
    finally:
        source.close()


def preview_sample(path, options, frame_count=12, progress=None, cancel=None):
    options.validate()
    if not 2 <= frame_count <= 64:
        raise ValueError("A sample preview uses between 2 and 64 frames.")
    source = open_source(path)
    try:
        eligible = input_frame_range(source.count, options)
        indices = np.linspace(eligible.start, eligible.stop - 1,
                              min(frame_count, len(eligible)), dtype=int).tolist()
    finally:
        source.close()
    return stack_source(path, options, progress, cancel, frame_indices=indices)
