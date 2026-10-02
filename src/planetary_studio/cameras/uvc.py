import ctypes as C
from pathlib import Path
import sys
import cv2
import numpy as np
from .base import Camera, CameraError, Device, Mode, Frame


class _Device(C.Structure):
    _fields_ = [(n, C.c_int) for n in ("vid", "pid", "bus", "address")] + [
        ("name", C.c_char * 256),
        ("serial", C.c_char * 128),
    ]


class _Mode(C.Structure):
    _fields_ = [
        (n, C.c_int)
        for n in (
            "width",
            "height",
            "fps",
            "bits",
            "format_index",
            "frame_index",
            "interface_number",
            "interval",
        )
    ] + [("fourcc", C.c_char * 5)]


class _Frame(C.Structure):
    _fields_ = [
        ("width", C.c_int),
        ("height", C.c_int),
        ("bytes", C.c_size_t),
        ("step", C.c_size_t),
        ("sequence", C.c_ulonglong),
        ("dropped", C.c_ulonglong),
    ]


def _library():
    filename = "libplanetary_uvc.dylib" if sys.platform == "darwin" else "libplanetary_uvc.so"
    path = Path(__file__).resolve().parent.parent / "native" / filename
    if not path.exists():
        raise CameraError("Direct UVC support is not installed. Use a packaged build or run native/build.py.")
    try:
        lib = C.CDLL(str(path))
    except OSError as e:
        raise CameraError(f"Cannot load UVC library: {e}") from e
    lib.ps_abi_size.argtypes, lib.ps_abi_size.restype = [C.c_int], C.c_size_t
    if any(lib.ps_abi_size(i) != C.sizeof(structure) for i, structure in enumerate((_Device, _Mode, _Frame))):
        raise CameraError("Native UVC library does not match this application build.")
    lib.ps_devices.argtypes = [C.POINTER(_Device), C.c_int]
    lib.ps_devices.restype = C.c_int
    lib.ps_error.argtypes = [C.c_int]
    lib.ps_error.restype = C.c_char_p
    lib.ps_open.argtypes = [C.c_int, C.c_int, C.POINTER(C.c_int)]
    lib.ps_open.restype = C.c_void_p
    lib.ps_modes.argtypes = [C.c_void_p, C.POINTER(_Mode), C.c_int]
    lib.ps_modes.restype = C.c_int
    lib.ps_start.argtypes = [C.c_void_p, C.POINTER(_Mode)]
    lib.ps_start.restype = C.c_int
    lib.ps_read.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(_Frame), C.c_int]
    lib.ps_read.restype = C.c_int
    lib.ps_control.argtypes = [C.c_void_p, C.c_int, C.c_int]
    lib.ps_control.restype = C.c_int
    lib.ps_control_range.argtypes = [
        C.c_void_p,
        C.c_int,
        C.POINTER(C.c_double),
        C.POINTER(C.c_double),
        C.POINTER(C.c_double),
    ]
    lib.ps_control_range.restype = C.c_int
    lib.ps_close.argtypes = [C.c_void_p]
    lib.ps_close.restype = None
    return lib


def _check(lib, code, operation):
    if code < 0:
        reason = lib.ps_error(code).decode()
        hint = {
            -3: " Check USB permissions and close other camera applications.",
            -4: " Reconnect the USB cable and scan again.",
            -6: " Close any other application using the camera.",
            -7: " Try a lower frame rate or a direct USB connection.",
        }.get(code, "")
        raise CameraError(f"{operation}: {reason} ({code}).{hint}")


def discover():
    lib = _library()
    items = (_Device * 64)()
    count = lib.ps_devices(items, 64)
    _check(lib, count, "Camera discovery")
    return [
        Device(
            "UVC",
            f"{d.bus}:{d.address}",
            d.name.decode("utf8", "replace"),
            {
                "vid": d.vid,
                "pid": d.pid,
                "bus": d.bus,
                "address": d.address,
                "serial": d.serial.decode("utf8", "replace"),
            },
        )
        for d in items[:count]
    ]


FORMATS = {
    "GRBG": ("GRBG", 8),
    "GBRG": ("GBRG", 8),
    "RGGB": ("RGGB", 8),
    "BGGR": ("BGGR", 8),
    "BA81": ("BGGR", 8),
    "BY8 ": ("BGGR", 8),
    "BA16": ("GRBG", 16),
    "Y800": ("MONO", 8),
    "Y8  ": ("MONO", 8),
    "Y16 ": ("MONO", 16),
    "RGB3": ("RGB", 8),
    "BGR3": ("RGB", 8),
    "YUY2": ("RGB", 8),
    "YUYV": ("RGB", 8),
    "UYVY": ("RGB", 8),
    "MJPG": ("RGB", 8),
    "NV12": ("RGB", 8),
}


class UvcCamera(Camera):
    def __init__(self, device):
        self.device, self.lib = device, _library()
        error = C.c_int()
        self.handle = self.lib.ps_open(device.details["bus"], device.details["address"], C.byref(error))
        _check(self.lib, error.value, "Opening " + device.name)
        self.mode = None
        self.buffer = None

    def modes(self):
        modes = (_Mode * 512)()
        count = self.lib.ps_modes(self.handle, modes, 512)
        result = []
        for m in modes[:count]:
            code = bytes(m.fourcc).decode("ascii", "replace")
            if code in FORMATS:
                pattern, bits = FORMATS[code]
                # BA16 is camera-specific. NexImage 10's native Bayer order is GRBG.
                if code == "BA16" and (self.device.details["vid"], self.device.details["pid"]) != (
                    0x199E,
                    0x8619,
                ):
                    continue
                result.append(Mode(m.width, m.height, m.fps, pattern, bits, {"native": m, "fourcc": code}))
        if not result:
            raise CameraError("Camera exposes no supported raw, YUV, or MJPEG image formats.")
        return sorted(result, key=lambda m: (m.width * m.height, m.bits, m.format != "GRBG", -m.fps))

    def start(self, mode):
        self.mode = mode
        self.buffer = C.create_string_buffer(mode.width * mode.height * 6 + 65536)
        _check(
            self.lib,
            self.lib.ps_start(self.handle, C.byref(mode.details["native"])),
            "Starting camera stream",
        )

    def read(self, timeout=1000):
        meta = _Frame()
        result = self.lib.ps_read(self.handle, self.buffer, len(self.buffer), C.byref(meta), timeout)
        if result == 0:
            return None
        if result < 0:
            raise CameraError(
                "UVC frame transfer failed or exceeded its buffer. Reconnect and choose another mode."
            )
        code, mode = self.mode.details["fourcc"], self.mode
        data = np.frombuffer(self.buffer, np.uint8, count=meta.bytes).copy()
        h, w = mode.height, mode.width
        if code == "MJPG":
            image = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if image is None:
                raise CameraError("Camera sent an invalid MJPEG frame.")
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        elif code in ("YUY2", "YUYV", "UYVY"):
            if data.size != h * w * 2:
                raise CameraError("Incomplete YUV frame received.")
            image = cv2.cvtColor(
                data.reshape(h, w, 2), cv2.COLOR_YUV2RGB_UYVY if code == "UYVY" else cv2.COLOR_YUV2RGB_YUY2
            )
        elif code == "NV12":
            image = cv2.cvtColor(data.reshape(h * 3 // 2, w), cv2.COLOR_YUV2RGB_NV12)
        else:
            channels = 3 if code in ("RGB3", "BGR3") else 1
            dtype = np.dtype("<u2" if mode.bits > 8 else "u1")
            rowbytes = w * channels * dtype.itemsize
            stride = meta.step if meta.step >= rowbytes else rowbytes
            if meta.bytes < h * stride:
                raise CameraError("Incomplete raw camera frame received.")
            image = data[: h * stride].reshape(h, stride)[:, :rowbytes].copy().view(dtype)
            image = image.reshape((h, w, channels) if channels > 1 else (h, w))
            if code == "BGR3":
                image = image[..., ::-1].copy()
        return Frame(image, mode.format, mode.bits, meta.dropped)

    def controls(self):
        result = {}
        for code, name in enumerate(("Exposure (ms)", "Gain")):
            lo, hi, current = C.c_double(), C.c_double(), C.c_double()
            if self.lib.ps_control_range(self.handle, code, C.byref(lo), C.byref(hi), C.byref(current)) == 0:
                result[name] = (lo.value, hi.value, current.value)
        return result

    def set_control(self, name, value):
        code, amount = (0, round(value * 10)) if name == "Exposure (ms)" else (1, round(value))
        _check(self.lib, self.lib.ps_control(self.handle, code, amount), "Setting " + name)

    def close(self):
        if self.handle:
            self.lib.ps_close(self.handle)
            self.handle = None
