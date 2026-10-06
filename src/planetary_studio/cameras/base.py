from dataclasses import dataclass, field
import numpy as np


class CameraError(RuntimeError):
    pass


class InvalidFrameError(CameraError):
    """A damaged individual frame; the camera connection is still usable."""


@dataclass
class Device:
    backend: str
    id: str
    name: str
    details: dict = field(default_factory=dict)


@dataclass
class Mode:
    width: int
    height: int
    fps: int = 30
    format: str = "RGB"
    bits: int = 8
    details: dict = field(default_factory=dict)

    @property
    def label(self):
        return f"{self.width} × {self.height} · {self.format} · {self.bits}-bit · {self.fps} fps"


@dataclass
class Frame:
    pixels: np.ndarray
    pattern: str = "MONO"
    bits: int = 8
    dropped: int = 0


class Camera:
    def modes(self):
        raise NotImplementedError

    def start(self, mode):
        raise NotImplementedError

    def read(self, timeout=1000):
        raise NotImplementedError

    def stop(self):
        """Stop acquisition while retaining the camera connection and controls."""
        raise NotImplementedError

    def controls(self):
        return {}

    def set_control(self, name, value):
        raise CameraError(f"{name} is unavailable for this camera.")

    def close(self):
        raise NotImplementedError
