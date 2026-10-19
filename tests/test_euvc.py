"""Exercise the real native EUVC port against a synthetic USB device."""
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Direct USB bridge is Unix-only")
def test_euvc_native_protocol(tmp_path):
    root = Path(__file__).resolve().parents[1]
    if not shutil.which("cc") or not shutil.which("pkg-config"):
        pytest.skip("Native protocol fixture needs cc and pkg-config")
    usb = subprocess.run(["pkg-config", "--cflags", "--libs", "libusb-1.0"], capture_output=True, text=True)
    if usb.returncode:
        pytest.skip("Native protocol fixture needs libusb development files")
    native = root / "src/planetary_studio/native"
    executable = tmp_path / "euvc-test"
    subprocess.run([
        "cc", "-std=gnu17", "-pthread", "-I" + str(root / "build/include"),
        "-I" + str(root / "native/vendor/libuvc/include"),
        str(root / "tests/native_euvc.c"), "-L" + str(native), "-lplanetary_uvc",
        "-Wl,-rpath," + str(native), *shlex.split(usb.stdout), "-o", str(executable),
    ], check=True, capture_output=True, text=True)
    result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
    assert "EUVC protocol checks passed" in result.stdout
