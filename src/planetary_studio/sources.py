from pathlib import Path
import re
import cv2
from .imaging import read_image
from .ser import SerReader

IMAGE_EXTENSIONS = {".png", ".tif", ".tiff", ".fits", ".fit", ".fts", ".jpg", ".jpeg", ".bmp"}


def natural_key(path):
    return [int(s) if s.isdigit() else s.casefold() for s in re.split(r"(\d+)", str(path))]


class ImageSequence:
    def __init__(self, paths):
        self.paths = sorted(map(Path, paths), key=natural_key)
        if not self.paths:
            raise ValueError("No supported images found.")
        self.count = len(self.paths)
        first = read_image(self.paths[0])
        self.shape = first.shape
        self.bits = first.dtype.itemsize * 8 if first.dtype.kind == "u" else None
        self.pattern = "RGB" if first.ndim == 3 else "MONO"

    def read(self, index):
        if not 0 <= index < self.count:
            raise IndexError(index)
        frame = read_image(self.paths[index])
        if frame.shape != self.shape:
            raise ValueError(f"Image dimensions differ: {self.paths[index].name}")
        return frame

    def close(self):
        pass


class VideoSource:
    def __init__(self, path):
        self.path = str(path)
        self._cap = cv2.VideoCapture(self.path)
        if not self._cap.isOpened():
            raise ValueError("Cannot decode video. Use SER, AVI, MOV, MP4, or an image sequence.")
        self.count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if self.count <= 0:
            self._cap.release()
            raise ValueError("Video has no readable frame count; convert it to SER or AVI first.")
        self.bits, self.pattern, self._next = 8, "RGB", 0
        self.fps = float(self._cap.get(cv2.CAP_PROP_FPS))

    def read(self, index):
        if not 0 <= index < self.count:
            raise IndexError(index)
        if index != self._next:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self._cap.read()
        if not ok:
            # Some AVI decoders cannot seek back to the first frame reliably.
            # Reopen and decode forward when their seek did not return a frame.
            self._cap.release()
            self._cap = cv2.VideoCapture(self.path)
            for _ in range(index + 1):
                ok, frame = self._cap.read()
                if not ok:
                    break
        if not ok:
            raise ValueError(f"Cannot decode video frame {index + 1}.")
        self._next = index + 1
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def close(self):
        self._cap.release()


class IndexedSource:
    """Expose a small set of original frames without decoding the full recording."""

    def __init__(self, source, indices):
        self.source = source
        self.indices = list(indices)
        if not self.indices or any(not 0 <= i < source.count for i in self.indices):
            raise ValueError("Preview frame indices must be inside the recording.")
        if self.indices != sorted(set(self.indices)):
            raise ValueError("Preview frame indices must be unique and in order.")
        self.count, self.bits, self.pattern = len(self.indices), source.bits, source.pattern

    def read(self, index):
        if not 0 <= index < self.count:
            raise IndexError(index)
        return self.source.read(self.indices[index])

    def close(self):
        self.source.close()


def open_source(path):
    if isinstance(path, (tuple, list)):
        return ImageSequence(path)
    path = Path(path)
    if path.is_dir():
        return ImageSequence(p for p in path.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)
    if path.suffix.lower() == ".ser":
        return SerReader(path)
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        return ImageSequence([path])
    return VideoSource(path)
