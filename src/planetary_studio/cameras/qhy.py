import ctypes as C
import time
import numpy as np
from .base import Camera, CameraError, Device, Mode, Frame
from .sdk import load_sdk, bind


def library(path=""):
    lib = load_sdk("QHY", path)
    ptr, uint, double = C.c_void_p, C.c_uint32, C.c_double
    for name, args, result in [
        ("InitQHYCCDResource", [], uint),
        ("ReleaseQHYCCDResource", [], uint),
        ("ScanQHYCCD", [], uint),
        ("GetQHYCCDId", [uint, C.c_void_p], uint),
        ("OpenQHYCCD", [C.c_char_p], ptr),
        ("CloseQHYCCD", [ptr], uint),
        ("SetQHYCCDStreamMode", [ptr, C.c_uint8], uint),
        ("InitQHYCCD", [ptr], uint),
        ("SetQHYCCDBitsMode", [ptr, uint], uint),
        ("SetQHYCCDResolution", [ptr, uint, uint, uint, uint], uint),
        (
            "GetQHYCCDChipInfo",
            [
                ptr,
                C.POINTER(double),
                C.POINTER(double),
                C.POINTER(uint),
                C.POINTER(uint),
                C.POINTER(double),
                C.POINTER(double),
                C.POINTER(uint),
            ],
            uint,
        ),
        ("IsQHYCCDControlAvailable", [ptr, C.c_int], uint),
        ("SetQHYCCDParam", [ptr, C.c_int, double], uint),
        (
            "GetQHYCCDParamMinMaxStep",
            [ptr, C.c_int, C.POINTER(double), C.POINTER(double), C.POINTER(double)],
            uint,
        ),
        ("GetQHYCCDParam", [ptr, C.c_int], double),
        ("GetQHYCCDMemLength", [ptr], uint),
        ("BeginQHYCCDLive", [ptr], uint),
        ("StopQHYCCDLive", [ptr], uint),
        (
            "GetQHYCCDLiveFrame",
            [ptr, C.POINTER(uint), C.POINTER(uint), C.POINTER(uint), C.POINTER(uint), ptr],
            uint,
        ),
    ]:
        bind(lib, name, args, result)
    return lib


def check(code, operation):
    if code != 0:
        raise CameraError(f"QHY {operation} failed (SDK error {code}).")


def discover(path=""):
    lib = library(path)
    check(lib.InitQHYCCDResource(), "driver initialization")
    try:
        count = lib.ScanQHYCCD()
        if count > 256:
            raise CameraError("QHY camera discovery failed.")
        result = []
        for i in range(count):
            name = C.create_string_buffer(128)
            check(lib.GetQHYCCDId(i, name), "discovery")
            ident = name.value.decode("utf8", "replace")
            result.append(Device("QHY", ident, ident, {"sdk": path}))
        return result
    finally:
        lib.ReleaseQHYCCDResource()


class QhyCamera(Camera):
    def __init__(self, device):
        self.lib = library(device.details.get("sdk", ""))
        self.handle, self.started = None, False
        check(self.lib.InitQHYCCDResource(), "driver initialization")
        try:
            self.handle = self.lib.OpenQHYCCD(device.id.encode())
            if not self.handle:
                raise CameraError("QHY could not open the camera.")
            check(self.lib.SetQHYCCDStreamMode(self.handle, 1), "video mode")
            check(self.lib.InitQHYCCD(self.handle), "initialization")
            doubles = [C.c_double() for _ in range(4)]
            self.w, self.h, bpp = C.c_uint32(), C.c_uint32(), C.c_uint32()
            check(
                self.lib.GetQHYCCDChipInfo(
                    self.handle,
                    C.byref(doubles[0]),
                    C.byref(doubles[1]),
                    C.byref(self.w),
                    C.byref(self.h),
                    C.byref(doubles[2]),
                    C.byref(doubles[3]),
                    C.byref(bpp),
                ),
                "sensor information",
            )
            color = self.lib.IsQHYCCDControlAvailable(self.handle, 20)
            self.pattern = {1: "GBRG", 2: "GRBG", 3: "BGGR", 4: "RGGB"}.get(color, "MONO")
            self._controls, self._types = {}, {}
            for name, control, scale in [
                ("Gain", 6, 1),
                ("Offset", 7, 1),
                ("Exposure (ms)", 8, 1000),
                ("USB traffic", 12, 1),
            ]:
                if self.lib.IsQHYCCDControlAvailable(self.handle, control) != 0:
                    continue
                lo, hi, step = C.c_double(), C.c_double(), C.c_double()
                if self.lib.GetQHYCCDParamMinMaxStep(
                    self.handle, control, C.byref(lo), C.byref(hi), C.byref(step)
                ):
                    continue
                current = self.lib.GetQHYCCDParam(self.handle, control)
                self._controls[name] = (lo.value / scale, hi.value / scale, current / scale)
                self._types[name] = (control, scale)
        except Exception:
            self.close()
            raise

    def modes(self):
        sizes = sorted(
            set(
                [
                    (self.w.value, self.h.value),
                    (min(self.w.value, 640) // 8 * 8, min(self.h.value, 480) // 2 * 2),
                ]
            )
        )
        bits = [8, 16] if self.lib.IsQHYCCDControlAvailable(self.handle, 10) == 0 else [8]
        return [Mode(w, h, 30, self.pattern, bit) for w, h in sizes for bit in bits]

    def start(self, mode):
        self.mode = mode
        check(self.lib.SetQHYCCDBitsMode(self.handle, mode.bits), "bit depth")
        x, y = ((self.w.value - mode.width) // 2) // 2 * 2, ((self.h.value - mode.height) // 2) // 2 * 2
        check(self.lib.SetQHYCCDResolution(self.handle, x, y, mode.width, mode.height), "ROI")
        length = self.lib.GetQHYCCDMemLength(self.handle)
        if length == 0 or length > 1024**3:
            raise CameraError("QHY reported an invalid image buffer size.")
        self.buffer = C.create_string_buffer(length)
        check(self.lib.BeginQHYCCDLive(self.handle), "stream start")
        self.started = True

    def read(self, timeout=1000):
        end = time.monotonic() + timeout / 1000
        while time.monotonic() < end:
            w, h, bits, channels = [C.c_uint32() for _ in range(4)]
            code = self.lib.GetQHYCCDLiveFrame(
                self.handle, C.byref(w), C.byref(h), C.byref(bits), C.byref(channels), self.buffer
            )
            if code == 0:
                if bits.value not in (8, 16) or channels.value not in (1, 3):
                    raise CameraError("QHY returned an unsupported image format.")
                count = w.value * h.value * channels.value
                if count * (bits.value // 8) > len(self.buffer):
                    raise CameraError("QHY returned invalid frame dimensions.")
                shape = (h.value, w.value, 3) if channels.value == 3 else (h.value, w.value)
                image = (
                    np.frombuffer(self.buffer, dtype=np.uint16 if bits.value == 16 else np.uint8, count=count)
                    .reshape(shape)
                    .copy()
                )
                return Frame(image, "RGB" if channels.value == 3 else self.pattern, bits.value)
            time.sleep(0.005)
        return None

    def controls(self):
        return self._controls

    def set_control(self, name, value):
        control, scale = self._types[name]
        check(self.lib.SetQHYCCDParam(self.handle, control, value * scale), name)

    def close(self):
        if self.handle:
            if self.started:
                self.lib.StopQHYCCDLive(self.handle)
            self.lib.CloseQHYCCD(self.handle)
            self.handle = None
        self.lib.ReleaseQHYCCDResource()
