import ctypes as C
import numpy as np
from .base import Camera, CameraError, Device, Mode, Frame
from .sdk import load_sdk, bind


class CameraInfo(C.Structure):
    _fields_ = [
        ("Name", C.c_char * 64),
        ("CameraID", C.c_int),
        ("MaxHeight", C.c_long),
        ("MaxWidth", C.c_long),
        ("IsColorCam", C.c_int),
        ("BayerPattern", C.c_int),
        ("SupportedBins", C.c_int * 16),
        ("SupportedVideoFormat", C.c_int * 8),
        ("PixelSize", C.c_double),
        *[
            (n, C.c_int)
            for n in ("MechanicalShutter", "ST4Port", "IsCoolerCam", "IsUSB3Host", "IsUSB3Camera")
        ],
        ("ElecPerADU", C.c_float),
        ("BitDepth", C.c_int),
        ("IsTriggerCam", C.c_int),
        ("Unused", C.c_char * 16),
    ]


class ControlCaps(C.Structure):
    _fields_ = [
        ("Name", C.c_char * 64),
        ("Description", C.c_char * 128),
        *[(n, C.c_long) for n in ("MaxValue", "MinValue", "DefaultValue")],
        *[(n, C.c_int) for n in ("IsAutoSupported", "IsWritable", "ControlType")],
        ("Unused", C.c_char * 32),
    ]


def library(path=""):
    lib = load_sdk("ASI", path)
    for name, args in {
        "ASIGetNumOfConnectedCameras": [],
        "ASIGetCameraProperty": [C.POINTER(CameraInfo), C.c_int],
        "ASIOpenCamera": [C.c_int],
        "ASIInitCamera": [C.c_int],
        "ASICloseCamera": [C.c_int],
        "ASISetROIFormat": [C.c_int] * 5,
        "ASISetStartPos": [C.c_int] * 3,
        "ASIStartVideoCapture": [C.c_int],
        "ASIStopVideoCapture": [C.c_int],
        "ASIGetVideoData": [C.c_int, C.c_void_p, C.c_long, C.c_int],
        "ASIGetNumOfControls": [C.c_int, C.POINTER(C.c_int)],
        "ASIGetControlCaps": [C.c_int, C.c_int, C.POINTER(ControlCaps)],
        "ASISetControlValue": [C.c_int, C.c_int, C.c_long, C.c_int],
        "ASIGetDroppedFrames": [C.c_int, C.POINTER(C.c_int)],
    }.items():
        bind(lib, name, args)
    return lib


def check(code, operation):
    if code != 0:
        raise CameraError(
            f"ZWO {operation} failed (SDK error {code}). Check the connection and selected mode."
        )


def discover(path=""):
    lib = library(path)
    devices = []
    for i in range(lib.ASIGetNumOfConnectedCameras()):
        info = CameraInfo()
        check(lib.ASIGetCameraProperty(C.byref(info), i), "discovery")
        devices.append(
            Device(
                "ASI", str(info.CameraID), info.Name.decode("utf8", "replace"), {"info": info, "sdk": path}
            )
        )
    return devices


class AsiCamera(Camera):
    def __init__(self, device):
        self.info, self.lib = device.details["info"], library(device.details.get("sdk", ""))
        self.id, self.started, self.opened = self.info.CameraID, False, False
        check(self.lib.ASIOpenCamera(self.id), "open")
        self.opened = True
        try:
            check(self.lib.ASIInitCamera(self.id), "initialization")
        except Exception:
            self.close()
            raise
        self._controls = {}
        self._types = {}
        count = C.c_int()
        check(self.lib.ASIGetNumOfControls(self.id, C.byref(count)), "control discovery")
        for i in range(count.value):
            cap = ControlCaps()
            check(self.lib.ASIGetControlCaps(self.id, i, C.byref(cap)), "control description")
            if not cap.IsWritable or cap.ControlType not in (0, 1, 5, 6):
                continue
            name = {0: "Gain", 1: "Exposure (ms)", 5: "Offset", 6: "USB bandwidth"}[cap.ControlType]
            scale = 1000 if cap.ControlType == 1 else 1
            self._controls[name] = (cap.MinValue / scale, cap.MaxValue / scale, cap.DefaultValue / scale)
            self._types[name] = (cap.ControlType, scale)

    def modes(self):
        pattern = ("RGGB", "BGGR", "GRBG", "GBRG")[self.info.BayerPattern] if self.info.IsColorCam else "MONO"
        w, h = self.info.MaxWidth // 8 * 8, self.info.MaxHeight // 2 * 2
        sizes = sorted(set([(w, h), (min(w, 640) // 8 * 8, min(h, 480) // 2 * 2)]))
        result = []
        for fmt in self.info.SupportedVideoFormat:
            if fmt == -1:
                break
            if fmt not in (0, 1, 2, 3):
                continue
            for sw, sh in sizes:
                result.append(
                    Mode(
                        sw,
                        sh,
                        30,
                        "RGB" if fmt == 1 else "MONO" if fmt == 3 else pattern,
                        16 if fmt == 2 else 8,
                        {"type": fmt},
                    )
                )
        return result

    def start(self, mode):
        self.mode = mode
        check(self.lib.ASISetROIFormat(self.id, mode.width, mode.height, 1, mode.details["type"]), "ROI")
        x = ((self.info.MaxWidth - mode.width) // 2) // 8 * 8
        y = ((self.info.MaxHeight - mode.height) // 2) // 2 * 2
        check(self.lib.ASISetStartPos(self.id, x, y), "ROI position")
        channels = 3 if mode.format == "RGB" else 1
        self.buffer = np.empty(
            (mode.height, mode.width, channels) if channels == 3 else (mode.height, mode.width),
            dtype=np.uint16 if mode.bits > 8 else np.uint8,
        )
        check(self.lib.ASIStartVideoCapture(self.id), "stream start")
        self.started = True

    def read(self, timeout=1000):
        code = self.lib.ASIGetVideoData(self.id, self.buffer.ctypes.data, self.buffer.nbytes, timeout)
        if code == 11:
            return None
        check(code, "frame read")
        dropped = C.c_int()
        self.lib.ASIGetDroppedFrames(self.id, C.byref(dropped))
        pixels = self.buffer[..., ::-1].copy() if self.mode.format == "RGB" else self.buffer.copy()
        return Frame(pixels, self.mode.format, self.mode.bits, dropped.value)

    def controls(self):
        return self._controls

    def set_control(self, name, value):
        control, scale = self._types[name]
        check(self.lib.ASISetControlValue(self.id, control, round(value * scale), 0), name)

    def close(self):
        if self.started:
            self.lib.ASIStopVideoCapture(self.id)
            self.started = False
        if self.opened:
            self.lib.ASICloseCamera(self.id)
            self.opened = False
