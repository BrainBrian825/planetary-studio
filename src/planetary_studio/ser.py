"""SER v3 read/write, with raw Bayer data, 8/16-bit depth and UTC timestamps.

The historical LittleEndian field is inverted: 0 is little endian, 1 is big endian.
See the SER Player reference implementation linked in docs/formats.md.
"""

import os
from pathlib import Path
import struct
import time
import numpy as np

HEADER = struct.Struct("<14s7I40s40s40sQQ")
COLOR_IDS = {"MONO": 0, "RGGB": 8, "GRBG": 9, "GBRG": 10, "BGGR": 11, "RGB": 100, "BGR": 101}
PATTERNS = {v: k for k, v in COLOR_IDS.items()}
TICKS_EPOCH = 621355968000000000


def utc_ticks() -> int:
    return TICKS_EPOCH + time.time_ns() // 100


def _text(value: str) -> bytes:
    return value.encode("utf-8")[:39].ljust(40, b"\0")


class SerReader:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with self.path.open("rb") as f:
            data = f.read(HEADER.size)
        if len(data) != HEADER.size:
            raise ValueError("Truncated SER header.")
        values = HEADER.unpack(data)
        magic, _, color, endian, self.width, self.height, self.bits, self.count = values[:8]
        if magic != b"LUCAM-RECORDER" or color not in PATTERNS or endian not in (0, 1):
            raise ValueError("Invalid or unsupported SER header.")
        if not (
            0 < self.width <= 65536 and 0 < self.height <= 65536 and 0 < self.bits <= 16 and self.count > 0
        ):
            raise ValueError("Invalid SER dimensions, bit depth, or empty recording.")
        self.pattern = PATTERNS[color]
        self.channels = 3 if color in (100, 101) else 1
        self.dtype = np.dtype("u1" if self.bits <= 8 else (">u2" if endian else "<u2"))
        self.shape = (self.height, self.width) + ((3,) if self.channels == 3 else ())
        self.frame_bytes = self.width * self.height * self.channels * self.dtype.itemsize
        self.data_end = HEADER.size + self.frame_bytes * self.count
        if self.path.stat().st_size < self.data_end:
            raise ValueError("Truncated SER pixel data; recording is incomplete.")
        self.observer, self.instrument, self.telescope = (
            x.rstrip(b"\0").decode("utf8", "replace") for x in values[8:11]
        )
        self._map = np.memmap(
            self.path, mode="r", dtype=self.dtype, offset=HEADER.size, shape=(self.count, *self.shape)
        )

    def read(self, index: int) -> np.ndarray:
        if not 0 <= index < self.count:
            raise IndexError(index)
        frame = np.array(self._map[index], dtype=self.dtype.newbyteorder("="))
        return frame[..., ::-1].copy() if self.pattern == "BGR" else frame

    def timestamp(self, index: int) -> int | None:
        if not 0 <= index < self.count:
            raise IndexError(index)
        if self.path.stat().st_size < self.data_end + 8 * self.count:
            return None
        with self.path.open("rb") as f:
            f.seek(self.data_end + 8 * index)
            return struct.unpack("<Q", f.read(8))[0]

    def close(self):
        if getattr(self, "_map", None) is not None:
            self._map._mmap.close()
            self._map = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class SerWriter:
    def __init__(
        self,
        path: str | Path,
        shape: tuple,
        bits: int = 8,
        pattern: str = "MONO",
        instrument: str = "Planetary Studio",
        observer: str = "",
        telescope: str = "",
        *,
        overwrite: bool = False,
    ):
        self.path = Path(path)
        self.shape = tuple(shape)
        self.bits = bits
        self.pattern = pattern
        if bits not in range(1, 17) or pattern not in COLOR_IDS:
            raise ValueError("Unsupported SER format.")
        expected_channels = 3 if pattern in ("RGB", "BGR") else 1
        if len(shape) not in (2, 3) or (len(shape) == 3) != (expected_channels == 3):
            raise ValueError("SER color format does not match pixel shape.")
        if len(shape) == 3 and shape[2] != 3:
            raise ValueError("SER supports three color channels.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("wb+" if overwrite else "xb+")
        self.count = 0
        self.timestamps = []
        self.start = utc_ticks()
        self.metadata = (observer, instrument, telescope)
        self._write_header()

    def _write_header(self):
        self._file.seek(0)
        self._file.write(
            HEADER.pack(
                b"LUCAM-RECORDER",
                0,
                COLOR_IDS[self.pattern],
                0,
                self.shape[1],
                self.shape[0],
                self.bits,
                self.count,
                *map(_text, self.metadata),
                self.start,
                self.start,
            )
        )

    def write(self, frame: np.ndarray, timestamp: int | None = None):
        if self._file.closed:
            raise ValueError("Recording is closed.")
        if tuple(frame.shape) != self.shape:
            raise ValueError("Camera changed image size during recording.")
        if frame.dtype.kind != "u" or frame.dtype.itemsize != (1 if self.bits <= 8 else 2):
            raise ValueError("SER pixels must match the recording bit depth.")
        data = np.ascontiguousarray(frame, dtype="u1" if self.bits <= 8 else "<u2")
        self._file.seek(HEADER.size + self.count * data.nbytes)
        self._file.write(data.tobytes())
        self.count += 1
        self.timestamps.append(timestamp or utc_ticks())
        # Keep a recoverable frame count even if power is lost mid-recording.
        if self.count % 32 == 0:
            self._write_header()
            self._file.flush()

    def close(self):
        if self._file.closed:
            return
        try:
            self._file.seek(HEADER.size + self.count * int(np.prod(self.shape)) * (1 if self.bits <= 8 else 2))
            self._file.write(np.asarray(self.timestamps, dtype="<u8").tobytes())
            self._file.truncate()
            self._write_header()
            self._file.flush()
            os.fsync(self._file.fileno())
        finally:
            self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
