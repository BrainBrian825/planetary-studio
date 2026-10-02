"""ASCOM Alpaca camera client, with ImageBytes v1 and JSON fallback."""

import json
import math
import ssl
import struct
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import certifi
import numpy as np
from .base import Camera, CameraError, Device, Mode, Frame


def valid_endpoint(endpoint):
    if not endpoint.startswith(("http://", "https://")):
        endpoint = "http://" + endpoint
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
        raise CameraError("Enter an Alpaca server address such as http://localhost:11111.")
    return endpoint.rstrip("/")


class Client:
    def __init__(self, endpoint):
        self.endpoint = valid_endpoint(endpoint)
        self.transaction = 0

    def request(self, path, method="GET", params=None, binary=False):
        self.transaction += 1
        params = {"ClientID": 2101, "ClientTransactionID": self.transaction, **(params or {})}
        encoded = urlencode(
            {k: str(v).lower() if isinstance(v, bool) else v for k, v in params.items()}
        ).encode()
        url = self.endpoint + path
        if method == "GET":
            url += "?" + encoded.decode()
        request = Request(
            url,
            data=encoded if method == "PUT" else None,
            method=method,
            headers={
                "Accept": "application/imagebytes, application/json" if binary else "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "PlanetaryStudio/0.1",
            },
        )
        try:
            with urlopen(
                request, timeout=10, context=ssl.create_default_context(cafile=certifi.where())
            ) as response:
                payload = response.read(512 * 1024 * 1024 + 1)
                if len(payload) > 512 * 1024 * 1024:
                    raise CameraError("Alpaca image exceeds the safety size limit.")
                if binary and "application/imagebytes" in response.headers.get("Content-Type", ""):
                    return decode_imagebytes(payload)
                data = json.loads(payload)
        except CameraError:
            raise
        except Exception as e:
            raise CameraError(f"Alpaca request failed: {e}") from e
        if data.get("ErrorNumber", 0):
            raise CameraError(
                f"Alpaca error {data['ErrorNumber']}: {data.get('ErrorMessage', 'Unknown error')}"
            )
        return data.get("Value")


def decode_imagebytes(data):
    if len(data) < 44:
        raise CameraError("Truncated Alpaca ImageBytes header.")
    version, error, _, _, offset, _, dtype, rank, x, y, z = struct.unpack_from("<11i", data)
    if error:
        raise CameraError(data[max(44, offset) :].decode("utf8", "replace"))
    dtypes = {1: "<i2", 2: "<i4", 3: "<f8", 4: "<f4", 5: "<u8", 6: "u1", 7: "<i8", 8: "<u2", 9: "<u4"}
    if version != 1 or dtype not in dtypes or rank not in (2, 3) or min(x, y) <= 0 or offset < 44:
        raise CameraError("Invalid Alpaca ImageBytes metadata.")
    if rank == 3 and z != 3:
        raise CameraError("Only RGB or monochrome Alpaca images are supported.")
    shape = (x, y, z) if rank == 3 else (x, y)
    count = math.prod(shape)
    size = count * np.dtype(dtypes[dtype]).itemsize
    if offset + size > len(data):
        raise CameraError("Truncated Alpaca image payload.")
    return np.frombuffer(data, dtypes[dtype], count=count, offset=offset).reshape(shape).swapaxes(0, 1).copy()


def discover(endpoint):
    endpoint = valid_endpoint(endpoint)
    items = Client(endpoint).request("/management/v1/configureddevices")
    return [
        Device("Alpaca", str(d["DeviceNumber"]), d["DeviceName"], {"endpoint": endpoint})
        for d in items
        if d["DeviceType"].lower() == "camera"
    ]


class AlpacaCamera(Camera):
    def __init__(self, device):
        self.client = Client(device.details["endpoint"])
        self.prefix = "/api/v1/camera/" + device.id + "/"
        self.exposure, self.pending = 0.01, False
        self.call("connected", "PUT", {"Connected": True})
        try:
            self.width = int(self.call("cameraxsize"))
            self.height = int(self.call("cameraysize"))
            self.maxadu = int(self.call("maxadu"))
            if not 0 < self.maxadu <= 65535:
                raise CameraError(
                    "This camera needs a floating-point capture format; SER capture supports up to 16 bits."
                )
            self.sensor = self.optional("sensortype", 0)
            self.pattern = "RGGB" if self.sensor == 2 else "MONO"
            if self.sensor == 2:
                ox, oy = int(self.optional("bayeroffsetx", 0)) % 2, int(self.optional("bayeroffsety", 0)) % 2
                self.pattern = {(0, 0): "RGGB", (1, 0): "GRBG", (0, 1): "GBRG", (1, 1): "BGGR"}[(ox, oy)]
            self.bits = max(1, math.ceil(math.log2(self.maxadu + 1)))
        except Exception:
            self.close()
            raise

    def call(self, command, method="GET", params=None, binary=False):
        return self.client.request(self.prefix + command, method, params, binary)

    def optional(self, command, default):
        try:
            return self.call(command)
        except CameraError:
            return default

    def modes(self):
        return [Mode(self.width, self.height, 1, "RGB" if self.sensor == 1 else self.pattern, self.bits)]

    def start(self, mode):
        for key, value in [
            ("binx", 1),
            ("biny", 1),
            ("startx", 0),
            ("starty", 0),
            ("numx", mode.width),
            ("numy", mode.height),
        ]:
            self.call(key, "PUT", {key: value})

    def read(self, timeout=1000):
        if not self.pending:
            self.call("startexposure", "PUT", {"Duration": self.exposure, "Light": True})
            self.pending = True
            self.deadline = time.monotonic() + max(30.0, self.exposure + 30.0)
        end = time.monotonic() + timeout / 1000
        while time.monotonic() < end:
            if self.call("imageready"):
                image = self.call("imagearray", binary=True)
                if not isinstance(image, np.ndarray):
                    image = np.asarray(image).swapaxes(0, 1).copy()
                if image.ndim not in (2, 3) or image.size == 0 or not np.isfinite(image).all():
                    raise CameraError("Invalid Alpaca camera image.")
                if image.min() < 0 or image.max() > self.maxadu:
                    raise CameraError("Alpaca camera pixels exceed its reported range.")
                self.pending = False
                return Frame(
                    image.astype(np.uint8 if self.bits <= 8 else np.uint16),
                    "RGB" if image.ndim == 3 else self.pattern,
                    self.bits,
                )
            if time.monotonic() > self.deadline:
                raise CameraError("Alpaca exposure timed out.")
            time.sleep(0.02)
        return None

    def controls(self):
        return {
            "Exposure (ms)": (
                float(self.optional("exposuremin", 0.001)) * 1000,
                float(self.optional("exposuremax", 3600)) * 1000,
                self.exposure * 1000,
            )
        }

    def set_control(self, name, value):
        self.exposure = value / 1000

    def close(self):
        try:
            if self.pending:
                self.call("abortexposure", "PUT")
            self.call("connected", "PUT", {"Connected": False})
        except CameraError:
            pass
