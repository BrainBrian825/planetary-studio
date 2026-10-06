import sys
import cv2
from .base import Camera, CameraError, Device, Mode, Frame


def backend():
    return (
        cv2.CAP_DSHOW
        if sys.platform == "win32"
        else (cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_V4L2)
    )


def discover():
    # Native Qt enumerates camera names without opening a device or prompting for permission.
    from PySide6.QtMultimedia import QMediaDevices

    return [
        Device("System", str(i), d.description(), {"index": i})
        for i, d in enumerate(QMediaDevices.videoInputs())
    ]


class SystemCamera(Camera):
    def __init__(self, device):
        self.cap = cv2.VideoCapture(device.details["index"], backend())
        if not self.cap.isOpened():
            self.cap.release()
            raise CameraError(
                "System camera could not open. Allow camera access in system settings, "
                "close other camera applications, or choose Direct UVC for raw astronomy cameras."
            )

    def modes(self):
        w, h = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return [Mode(w or 640, h or 480, max(1, round(self.cap.get(cv2.CAP_PROP_FPS)) or 30))]

    def start(self, mode):
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, mode.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, mode.height)
        self.cap.set(cv2.CAP_PROP_FPS, mode.fps)

    def read(self, timeout=1000):
        ok, frame = self.cap.read()
        if not ok:
            raise CameraError("System camera stopped delivering frames.")
        return Frame(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), "RGB", 8)

    def close(self):
        self.cap.release()

    def stop(self):
        # The worker stops reading. OpenCV retains the system-driver connection.
        pass
