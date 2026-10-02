"""INDI XML camera client with FITS BLOB acquisition over a persistent socket."""

import base64
import codecs
from io import BytesIO
import socket
import time
import zlib
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
import numpy as np
from astropy.io import fits
from .base import Camera, CameraError, Device, Mode, Frame


def address(endpoint):
    parsed = urlparse(endpoint if "://" in endpoint else "indi://" + endpoint)
    if not parsed.hostname:
        raise CameraError("Enter an INDI address such as localhost:7624.")
    return parsed.hostname, parsed.port or 7624


class Client:
    def __init__(self, endpoint):
        try:
            self.sock = socket.create_connection(address(endpoint), timeout=5)
            self.sock.settimeout(0.1)
        except OSError as e:
            raise CameraError(f"Cannot connect to INDI server: {e}") from e
        self.parser = ET.XMLPullParser(events=("start", "end"))
        self.decoder = codecs.getincrementaldecoder("utf8")()
        self.parser.feed("<root>")
        self.root = next(self.parser.read_events())[1]
        self.properties = {}
        self.blobs = []
        self.send(ET.Element("getProperties", {"version": "1.7"}))

    def send(self, node):
        try:
            self.sock.sendall(ET.tostring(node, encoding="utf8"))
        except OSError as e:
            raise CameraError(f"INDI connection failed: {e}") from e

    def poll(self):
        try:
            data = self.sock.recv(1024 * 1024)
        except socket.timeout:
            return
        except OSError as e:
            raise CameraError(f"INDI receive failed: {e}") from e
        if not data:
            raise CameraError("INDI server closed the connection.")
        try:
            self.parser.feed(self.decoder.decode(data))
            for event, node in self.parser.read_events():
                if event != "end":
                    continue
                if node.tag.endswith("Vector") and node.tag.startswith(("def", "set")):
                    device, name = node.get("device"), node.get("name")
                    self.properties[(device, name)] = {
                        "state": node.get("state"),
                        "values": {v.get("name"): (v.text or "").strip() for v in node},
                        "members": {v.get("name"): dict(v.attrib) for v in node},
                        "tag": node.tag,
                    }
                    if node.tag == "setBLOBVector":
                        for blob in node:
                            self.blobs.append((device, blob.get("format", ""), (blob.text or "").strip()))
                    node.clear()
                    self.root.remove(node)
        except (ET.ParseError, UnicodeError) as e:
            raise CameraError(f"Invalid INDI XML stream: {e}") from e

    def set_vector(self, device, name, values, kind="Number"):
        node = ET.Element("new" + kind + "Vector", {"device": device, "name": name})
        for key, value in values.items():
            ET.SubElement(node, "one" + kind, {"name": key}).text = str(value)
        self.send(node)

    def close(self):
        self.sock.close()


def discover(endpoint):
    client = Client(endpoint)
    try:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            client.poll()
        names = sorted(
            {device for device, prop in client.properties if prop in ("CCD_EXPOSURE", "CCD_INFO", "CCD1")}
        )
        return [Device("INDI", name, name, {"endpoint": endpoint}) for name in names]
    finally:
        client.close()


def decode_fits_blob(format, payload):
    try:
        if len(payload) > 700 * 1024 * 1024:
            raise CameraError("INDI image exceeds the size limit.")
        data = base64.b64decode(payload, validate=False)
        if format.endswith(".z"):
            decoder = zlib.decompressobj()
            data = decoder.decompress(data, 512 * 1024 * 1024 + 1)
            if len(data) > 512 * 1024 * 1024 or decoder.unconsumed_tail:
                raise CameraError("Compressed INDI image exceeds the size limit.")
        if not format.startswith((".fits", ".fit", ".fts")):
            raise CameraError(
                "INDI driver must send FITS images. Disable compressed video streaming in its driver."
            )
        with fits.open(BytesIO(data), memmap=False) as hdus:
            hdu = next(h for h in hdus if h.data is not None)
            image, pattern = np.array(hdu.data), hdu.header.get("BAYERPAT", "MONO").strip()
            if image.ndim == 3 and image.shape[0] == 3:
                image = np.moveaxis(image, 0, -1)
            if image.ndim not in (2, 3) or image.size == 0:
                raise CameraError("Invalid INDI FITS dimensions.")
            if image.dtype.kind != "u" or image.dtype.itemsize > 2:
                if image.min() < 0 or image.max() > 65535:
                    raise CameraError("INDI SER capture requires unsigned pixels up to 16 bits.")
                image = image.astype(np.uint16)
            else:
                image = image.astype(image.dtype.newbyteorder("="))
            return Frame(
                np.ascontiguousarray(image), "RGB" if image.ndim == 3 else pattern, image.dtype.itemsize * 8
            )
    except CameraError:
        raise
    except Exception as e:
        raise CameraError(f"Cannot decode INDI FITS image: {e}") from e


class IndiCamera(Camera):
    def __init__(self, device):
        self.device, self.client = device, Client(device.details["endpoint"])
        self.exposure, self.pending = 0.01, False
        try:
            end = time.monotonic() + 1
            while time.monotonic() < end:
                self.client.poll()
            self.client.set_vector(device.id, "CONNECTION", {"CONNECT": "On", "DISCONNECT": "Off"}, "Switch")
            blob = ET.Element("enableBLOB", {"device": device.id})
            blob.text = "Also"
            self.client.send(blob)
            end = time.monotonic() + 3
            while time.monotonic() < end:
                self.client.poll()
                if (device.id, "CCD_INFO") in self.client.properties:
                    break
            if (device.id, "CCD_INFO") not in self.client.properties:
                raise CameraError("INDI camera did not provide sensor information after connecting.")
        except Exception:
            self.client.close()
            raise

    def modes(self):
        info = self.client.properties[(self.device.id, "CCD_INFO")]["values"]
        return [
            Mode(
                int(float(info["CCD_MAX_X"])),
                int(float(info["CCD_MAX_Y"])),
                1,
                "MONO",
                int(float(info.get("CCD_BITSPERPIXEL", 16))),
            )
        ]

    def start(self, mode):
        pass

    def read(self, timeout=1000):
        if not self.pending:
            self.client.blobs.clear()
            prop = self.client.properties.get((self.device.id, "CCD_EXPOSURE"))
            if prop:
                prop["state"] = "Busy"
            self.client.set_vector(self.device.id, "CCD_EXPOSURE", {"CCD_EXPOSURE_VALUE": self.exposure})
            self.deadline = time.monotonic() + self.exposure + 30
            self.pending = True
        end = time.monotonic() + timeout / 1000
        while time.monotonic() < end:
            self.client.poll()
            prop = self.client.properties.get((self.device.id, "CCD_EXPOSURE"), {})
            if prop.get("state") == "Alert":
                raise CameraError("INDI driver reported an exposure failure.")
            for device, format, data in self.client.blobs:
                if device == self.device.id:
                    self.client.blobs.clear()
                    self.pending = False
                    return decode_fits_blob(format, data)
            if time.monotonic() > self.deadline:
                raise CameraError("INDI camera exposure timed out.")
        return None

    def controls(self):
        return {"Exposure (ms)": (0.1, 3600000.0, self.exposure * 1000)}

    def set_control(self, name, value):
        self.exposure = value / 1000

    def close(self):
        if self.pending:
            try:
                self.client.set_vector(self.device.id, "CCD_ABORT_EXPOSURE", {"ABORT": "On"}, "Switch")
            except CameraError:
                pass
        self.client.close()
