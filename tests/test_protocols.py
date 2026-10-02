import base64
from io import BytesIO
import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import numpy as np
import pytest
from astropy.io import fits
from planetary_studio.cameras.alpaca import decode_imagebytes, discover, AlpacaCamera
from planetary_studio.cameras.indi import decode_fits_blob
from planetary_studio.cameras.base import CameraError


@pytest.mark.parametrize("dtype,code", [("uint8", 6), ("uint16", 8), ("int32", 2), ("float64", 3)])
@pytest.mark.parametrize("rgb", [False, True])
def test_alpaca_imagebytes_orientation_and_numeric_types(dtype, code, rgb):
    shape = (8, 6, 3) if rgb else (8, 6)
    sensor = np.arange(np.prod(shape)).reshape(shape).astype(dtype)
    payload = (
        struct.pack("<11i", 1, 0, 1, 2, 44, code, code, len(shape), 8, 6, 3 if rgb else 0) + sensor.tobytes()
    )
    decoded = decode_imagebytes(payload)
    np.testing.assert_array_equal(decoded, sensor.swapaxes(0, 1))


def test_alpaca_rejects_truncated_payload():
    with pytest.raises(CameraError, match="Truncated"):
        decode_imagebytes(b"bad")
    with pytest.raises(CameraError, match="Truncated"):
        decode_imagebytes(struct.pack("<11i", 1, 0, 0, 0, 44, 8, 8, 2, 100, 100, 0))


@pytest.mark.parametrize("compressed", [False, True])
def test_indi_fits_blob_preserves_raw_bayer(compressed):
    import zlib

    image = np.arange(16 * 24, dtype=np.uint16).reshape(16, 24)
    memory = BytesIO()
    hdu = fits.PrimaryHDU(image)
    hdu.header["BAYERPAT"] = "GRBG"
    hdu.writeto(memory)
    data = zlib.compress(memory.getvalue()) if compressed else memory.getvalue()
    frame = decode_fits_blob(".fits.z" if compressed else ".fits", base64.b64encode(data).decode())
    assert frame.pattern == "GRBG" and frame.bits == 16
    np.testing.assert_array_equal(frame.pixels, image)


def test_alpaca_camera_against_real_http_server():
    state = {"connected": False, "exposed": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_PUT(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            if self.path.endswith("/connected"):
                state["connected"] = "true" in body
            if self.path.endswith("/startexposure"):
                state["exposed"] = True
            self.respond(None)

        def respond(self, value):
            data = json.dumps({"Value": value, "ErrorNumber": 0}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            command = self.path.split("?")[0].split("/")[-1]
            if command == "configureddevices":
                self.respond(
                    [{"DeviceType": "Camera", "DeviceNumber": 0, "DeviceName": "Protocol test camera"}]
                )
            elif command == "imagearray":
                assert self.headers["Accept"].startswith("application/imagebytes")
                image = np.arange(48, dtype=np.uint16).reshape(8, 6)
                data = struct.pack("<11i", 1, 0, 1, 2, 44, 2, 8, 2, 8, 6, 0) + image.tobytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/imagebytes")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.respond(
                    {
                        "cameraxsize": 8,
                        "cameraysize": 6,
                        "maxadu": 65535,
                        "sensortype": 2,
                        "bayeroffsetx": 1,
                        "bayeroffsety": 0,
                        "imageready": state["exposed"],
                    }.get(command, 1)
                )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        devices = discover(f"http://127.0.0.1:{server.server_port}")
        camera = AlpacaCamera(devices[0])
        try:
            camera.start(camera.modes()[0])
            frame = camera.read(500)
            assert frame.pixels.shape == (6, 8) and frame.pattern == "GRBG"
            assert state["connected"]
        finally:
            camera.close()
        assert not state["connected"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_indi_camera_against_fragmented_xml_socket_server():
    import socket
    import time
    from planetary_studio.cameras.indi import IndiCamera
    from planetary_studio.cameras.base import Device

    sensor = np.arange(8 * 12, dtype=np.uint16).reshape(8, 12)
    memory = BytesIO()
    fits.PrimaryHDU(sensor).writeto(memory)
    blob = base64.b64encode(memory.getvalue()).decode()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    endpoint = f"127.0.0.1:{listener.getsockname()[1]}"
    errors = []

    def serve():
        try:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(5)
                conn.recv(4096)
                xml = '<defNumberVector device="Test CCD" name="CCD_INFO" state="Ok"><defNumber name="CCD_MAX_X">12</defNumber><defNumber name="CCD_MAX_Y">8</defNumber><defNumber name="CCD_BITSPERPIXEL">16</defNumber></defNumberVector>'
                for start in range(0, len(xml), 13):
                    conn.sendall(xml[start : start + 13].encode())
                received = b""
                while b"CCD_EXPOSURE_VALUE" not in received:
                    received += conn.recv(4096)
                result = f'<setBLOBVector device="Test CCD" name="CCD1"><oneBLOB name="CCD1" format=".fits" size="{len(memory.getvalue())}">{blob}</oneBLOB></setBLOBVector>'
                for start in range(0, len(result), 97):
                    conn.sendall(result[start : start + 97].encode())
                time.sleep(0.4)
        except Exception as e:
            errors.append(e)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        camera = IndiCamera(Device("INDI", "Test CCD", "Test CCD", {"endpoint": endpoint}))
        try:
            camera.start(camera.modes()[0])
            frame = camera.read(2000)
            np.testing.assert_array_equal(frame.pixels, sensor)
        finally:
            camera.close()
    finally:
        listener.close()
        thread.join(6)
    assert not errors
