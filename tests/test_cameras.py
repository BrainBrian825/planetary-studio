import ctypes as C
import sys
import numpy as np
import pytest
from planetary_studio.cameras import asi, qhy
from planetary_studio.cameras.base import Device


def test_vendor_struct_layout_matches_published_abi():
    assert C.sizeof(asi.CameraInfo) == (240 if C.sizeof(C.c_long) == 4 else 248)
    assert C.sizeof(asi.ControlCaps) == (248 if C.sizeof(C.c_long) == 4 else 264)


@pytest.mark.skipif(sys.platform == "win32", reason="Direct UVC bridge is Unix-only")
def test_native_bridge_loads_and_matches_python_abi():
    from planetary_studio.cameras.uvc import _library

    lib = _library()
    assert lib.ps_abi_size(0) == 400
    assert lib.ps_error(-3) == b"Access denied"


def test_asi_adapter_obeys_video_lifecycle_and_preserves_raw16(monkeypatch):
    calls = []

    class FakeSDK:
        def ASIOpenCamera(self, ident):
            calls.append("open")
            return 0

        def ASIInitCamera(self, ident):
            calls.append("init")
            return 0

        def ASIGetNumOfControls(self, ident, ptr):
            C.cast(ptr, C.POINTER(C.c_int))[0] = 0
            return 0

        def ASISetROIFormat(self, ident, w, h, binning, kind):
            calls.append(("roi", w, h, kind))
            return 0

        def ASISetStartPos(self, *_):
            return 0

        def ASIStartVideoCapture(self, ident):
            calls.append("start")
            return 0

        def ASIGetVideoData(self, ident, ptr, length, timeout):
            view = np.ctypeslib.as_array((C.c_uint16 * (length // 2)).from_address(ptr))
            view[:] = 12345
            return 0

        def ASIGetDroppedFrames(self, ident, ptr):
            C.cast(ptr, C.POINTER(C.c_int))[0] = 2
            return 0

        def ASIStopVideoCapture(self, ident):
            calls.append("stop")
            return 0

        def ASICloseCamera(self, ident):
            calls.append("close")
            return 0

    monkeypatch.setattr(asi, "library", lambda *_: FakeSDK())
    info = asi.CameraInfo()
    info.CameraID, info.MaxWidth, info.MaxHeight, info.IsColorCam, info.BayerPattern = 3, 640, 480, 1, 2
    info.SupportedVideoFormat[:] = [2, -1, -1, -1, -1, -1, -1, -1]
    camera = asi.AsiCamera(Device("ASI", "3", "SDK test", {"info": info}))
    try:
        mode = camera.modes()[0]
        assert mode.format == "GRBG" and mode.bits == 16
        camera.start(mode)
        frame = camera.read()
        assert frame.pattern == "GRBG" and frame.pixels.dtype == np.uint16 and frame.dropped == 2
        np.testing.assert_array_equal(frame.pixels, 12345)
    finally:
        camera.close()
    assert calls[:2] == ["open", "init"] and calls[-3:] == ["start", "stop", "close"]


def test_qhy_adapter_initializes_stream_mode_and_preserves_raw8(monkeypatch):
    calls = []

    class FakeSDK:
        def InitQHYCCDResource(self):
            calls.append("resource")
            return 0

        def OpenQHYCCD(self, ident):
            calls.append("open")
            return 123

        def SetQHYCCDStreamMode(self, handle, mode):
            calls.append(("mode", mode))
            return 0

        def InitQHYCCD(self, handle):
            calls.append("init")
            return 0

        def GetQHYCCDChipInfo(self, handle, cw, ch, w, h, pw, ph, bits):
            C.cast(w, C.POINTER(C.c_uint32))[0] = 640
            C.cast(h, C.POINTER(C.c_uint32))[0] = 480
            return 0

        def IsQHYCCDControlAvailable(self, handle, control):
            return 2 if control == 20 else 0xFFFFFFFF

        def SetQHYCCDBitsMode(self, *_):
            return 0

        def SetQHYCCDResolution(self, handle, x, y, w, h):
            self.shape = (h, w)
            return 0

        def GetQHYCCDMemLength(self, handle):
            return 640 * 480 * 2

        def BeginQHYCCDLive(self, handle):
            calls.append("start")
            return 0

        def GetQHYCCDLiveFrame(self, handle, w, h, bits, channels, buffer):
            for ptr, value in [(w, 640), (h, 480), (bits, 8), (channels, 1)]:
                C.cast(ptr, C.POINTER(C.c_uint32))[0] = value
            C.memset(buffer, 100, 640 * 480)
            return 0

        def StopQHYCCDLive(self, handle):
            calls.append("stop")
            return 0

        def CloseQHYCCD(self, handle):
            calls.append("close")
            return 0

        def ReleaseQHYCCDResource(self):
            calls.append("release")
            return 0

    monkeypatch.setattr(qhy, "library", lambda *_: FakeSDK())
    camera = qhy.QhyCamera(Device("QHY", "test", "SDK test"))
    try:
        camera.start(camera.modes()[0])
        frame = camera.read()
        assert frame.pattern == "GRBG" and frame.bits == 8
        np.testing.assert_array_equal(frame.pixels, 100)
    finally:
        camera.close()
    assert calls[:4] == ["resource", "open", ("mode", 1), "init"]
    assert calls[-3:] == ["stop", "close", "release"]
