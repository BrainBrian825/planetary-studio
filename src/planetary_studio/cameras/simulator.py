import time
import cv2
import numpy as np
from .base import Camera, Device, Mode, Frame


def planet_image(width=640, height=480):
    yy, xx = np.indices((height, width), dtype=np.float32)
    x, y = (xx - width / 2) / (min(width, height) * 0.25), (yy - height / 2) / (min(width, height) * 0.25)
    radius = np.sqrt(x * x + y * y)
    disk = np.clip(1 - radius**8, 0, 1)
    bands = 0.72 + 0.16 * np.sin(y * 22) + 0.05 * np.sin(x * 15 + y * 40)
    spot = np.exp(-((x - 0.3) ** 2 / 0.07 + (y - 0.25) ** 2 / 0.008))
    image = np.stack((bands + 0.15 * spot, bands * 0.79 - 0.08 * spot, bands * 0.60 - 0.10 * spot), axis=-1)
    return np.clip(
        image * disk[..., None] * np.sqrt(np.clip(1 - radius**2, 0.15, 1))[..., None] + 0.015, 0, 1
    )


class SimulatedCamera(Camera):
    def __init__(self, device=None):
        self.rng = np.random.default_rng(42)
        self.exposure, self.gain, self.next_time = 10.0, 10.0, 0.0

    def modes(self):
        preferred = [Mode(640, 480, 30, "RGB", 16), Mode(320, 240, 30, "RGB", 16)]
        return preferred + [
            Mode(w, h, fps, color, bits)
            for w, h in ((640, 480), (320, 240))
            for color in ("RGB", "MONO")
            for bits in (8, 16)
            for fps in (15, 30, 60)
            if not (color == "RGB" and bits == 16 and fps == 30)
        ]

    def start(self, mode):
        self.mode = mode
        self.truth = planet_image(mode.width, mode.height)
        self.next_time = time.monotonic()

    def read(self, timeout=1000):
        delay = self.next_time - time.monotonic()
        if delay > 0:
            time.sleep(min(delay, timeout / 1000))
        self.next_time = time.monotonic() + 1 / self.mode.fps
        dx, dy = self.rng.normal(0, 1.3, 2)
        matrix = np.float32([[1, 0, dx], [0, 1, dy]])
        image = cv2.warpAffine(self.truth, matrix, (self.mode.width, self.mode.height))
        image = cv2.GaussianBlur(image, (0, 0), float(self.rng.uniform(0.3, 1.3)))
        image = image * min(2.0, self.exposure / 10) + self.rng.normal(
            0, 0.008 + self.gain * 0.0002, image.shape
        )
        if self.mode.format == "MONO":
            image = cv2.cvtColor(image.astype(np.float32), cv2.COLOR_RGB2GRAY)
        maximum = (1 << self.mode.bits) - 1
        return Frame(
            np.rint(np.clip(image, 0, 1) * maximum).astype(np.uint16 if self.mode.bits == 16 else np.uint8),
            self.mode.format,
            self.mode.bits,
        )

    def controls(self):
        return {"Exposure (ms)": (0.1, 1000.0, 10.0), "Gain": (0.0, 100.0, 10.0)}

    def set_control(self, name, value):
        if name == "Exposure (ms)":
            self.exposure = value
        else:
            self.gain = value

    def close(self):
        pass

    def stop(self):
        pass


def discover():
    return [Device("Simulator", "planet", "Simulated Jupiter · practice and test the full workflow")]
