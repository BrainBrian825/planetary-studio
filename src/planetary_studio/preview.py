"""Quick previews use the same processing settings as a full stack."""

from dataclasses import dataclass
import numpy as np
import cv2
from .imaging import normalized
from .processing import Preprocessor, alignment_points, stack_source, Cancelled
from .sources import open_source


@dataclass
class FramePreview:
    image: np.ndarray
    index: int
    points: list[tuple[int, int]]
    point_size: float


def preview_frame(path, index, options, prepared=True, progress=None, cancel=None):
    options.validate()
    source = open_source(path)
    try:
        if cancel and cancel.is_set():
            raise Cancelled("Preview cancelled.")
        image = Preprocessor(source, options).read(index) if prepared else normalized(source.read(index), source.bits)
        points = alignment_points(image, options.alignment_size) if prepared and options.local_alignment else []
        if prepared and options.scale != 1:
            h, w = image.shape[:2]
            image = np.clip(cv2.resize(image, (round(w * options.scale), round(h * options.scale)),
                                       interpolation=cv2.INTER_LANCZOS4), 0, 1)
            points = [(x * options.scale, y * options.scale) for x, y in points]
        if cancel and cancel.is_set():
            raise Cancelled("Preview cancelled.")
        return FramePreview(image, index, points, options.alignment_size * (options.scale if prepared else 1))
    finally:
        source.close()


def preview_sample(path, options, frame_count=12, progress=None, cancel=None):
    if not 2 <= frame_count <= 64:
        raise ValueError("A sample preview uses between 2 and 64 frames.")
    source = open_source(path)
    try:
        indices = np.linspace(0, source.count - 1, min(frame_count, source.count), dtype=int).tolist()
    finally:
        source.close()
    return stack_source(path, options, progress, cancel, frame_indices=indices)
