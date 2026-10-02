"""Pixel handling. Internal color order is RGB; processing uses normalized float32."""

from pathlib import Path
import cv2
import numpy as np
import tifffile
from astropy.io import fits

BAYER_CODES = {p: getattr(cv2, f"COLOR_Bayer{p}2RGB") for p in ("RGGB", "GRBG", "GBRG", "BGGR")}


def debayer(frame: np.ndarray, pattern: str | None) -> np.ndarray:
    if not pattern or pattern == "MONO" or frame.ndim == 3:
        return frame
    if pattern not in BAYER_CODES:
        raise ValueError(f"Unknown Bayer pattern: {pattern}")
    if frame.dtype not in (np.dtype("uint8"), np.dtype("uint16")):
        raise ValueError("Debayering requires raw 8-bit or 16-bit integer pixels.")
    return cv2.cvtColor(np.ascontiguousarray(frame), BAYER_CODES[pattern])


def normalized(frame: np.ndarray, bits: int | None = None) -> np.ndarray:
    if np.issubdtype(frame.dtype, np.integer):
        maximum = (1 << bits) - 1 if bits else np.iinfo(frame.dtype).max
        out = frame.astype(np.float32) / maximum
    else:
        out = frame.astype(np.float32)
    if not np.isfinite(out).all():
        raise ValueError("Image contains non-finite pixels.")
    return out


def luminance(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame.astype(np.float32, copy=False)
    return (frame[..., 0] * 0.2126 + frame[..., 1] * 0.7152 + frame[..., 2] * 0.0722).astype(np.float32)


def read_image(path: str | Path) -> np.ndarray:
    path = Path(path)
    if path.suffix.lower() in (".fits", ".fit", ".fts"):
        with fits.open(path, memmap=False) as hdus:
            image = next((h.data for h in hdus if h.data is not None), None)
            if image is None:
                raise ValueError("FITS file has no image data.")
            image = np.array(image)
        if image.ndim == 3 and image.shape[0] in (3, 4):
            image = np.moveaxis(image[:3], 0, -1)
    elif path.suffix.lower() in (".tif", ".tiff"):
        image = tifffile.imread(path)
    else:
        # imdecode supports Unicode paths on Windows.
        image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError(f"Cannot read image: {path.name}")
        if image.ndim == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB if image.shape[2] == 4 else cv2.COLOR_BGR2RGB)
    if image.ndim not in (2, 3) or (image.ndim == 3 and image.shape[2] not in (3, 4)):
        raise ValueError(f"Unsupported image shape: {image.shape}")
    if image.ndim == 3:
        image = image[..., :3]
    # Convert FITS big-endian arrays before passing them to OpenCV.
    return np.ascontiguousarray(image.astype(image.dtype.newbyteorder("="), copy=False))


def write_image(path: str | Path, image: np.ndarray, overwrite: bool = False) -> None:
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.clip(image, 0, 1)
    suffix = path.suffix.lower()
    if suffix in (".fits", ".fit", ".fts"):
        data = np.moveaxis(image, -1, 0) if image.ndim == 3 else image
        fits.PrimaryHDU(data.astype(np.float32)).writeto(path, overwrite=overwrite)
    elif suffix in (".tif", ".tiff"):
        tifffile.imwrite(
            path,
            np.rint(image * 65535).astype(np.uint16),
            photometric="rgb" if image.ndim == 3 else "minisblack",
        )
    elif suffix == ".png":
        data = np.rint(image * 65535).astype(np.uint16)
        if image.ndim == 3:
            data = cv2.cvtColor(data, cv2.COLOR_RGB2BGR)
        ok, encoded = cv2.imencode(".png", data)
        if not ok:
            raise ValueError("PNG encoder failed.")
        encoded.tofile(path)
    else:
        raise ValueError("Export to 16-bit TIFF, 16-bit PNG, or floating-point FITS.")


def preview_pixels(image: np.ndarray, stretch: bool = False) -> np.ndarray:
    data = normalized(image)
    if stretch:
        lo, hi = np.percentile(data, [0.5, 99.5])
        if hi > lo:
            data = (data - lo) / (hi - lo)
    data = np.clip(data, 0, 1)
    return np.ascontiguousarray(np.rint(data * 255).astype(np.uint8))
