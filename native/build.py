"""Build the native UVC bridge. Requires libusb development files and a C compiler."""

from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
if sys.platform == "win32":
    print("Windows uses DirectShow/Media Foundation and vendor SDKs; native UVC bridge is Unix-only.")
    raise SystemExit(0)
vendor = root / "native/vendor/libuvc"
out = root / "src/planetary_studio/native"
out.mkdir(parents=True, exist_ok=True)
generated = root / "build/include/libuvc"
generated.mkdir(parents=True, exist_ok=True)
config = (vendor / "include/libuvc/libuvc_config.h.in").read_text()
for key, value in {"MAJOR": 0, "MINOR": 0, "PATCH": 7}.items():
    config = config.replace("@libuvc_VERSION_" + key + "@", str(value))
config = config.replace("@libuvc_VERSION@", "0.0.7").replace(
    "#cmakedefine LIBUVC_HAS_JPEG 1", "/* JPEG decoded by OpenCV. */"
)
(generated / "libuvc_config.h").write_text(config)
if os.environ.get("LIBUSB_INCLUDE_DIR") and os.environ.get("LIBUSB_LIBRARY"):
    usb_flags = ["-I" + os.environ["LIBUSB_INCLUDE_DIR"], os.environ["LIBUSB_LIBRARY"]]
else:
    usb_flags = shlex.split(
        subprocess.check_output(["pkg-config", "--cflags", "--libs", "libusb-1.0"], text=True)
    )
sources = [
    vendor / "src" / (name + ".c")
    for name in ("ctrl", "ctrl-gen", "device", "diag", "frame", "init", "stream", "misc")
]
target = out / ("libplanetary_uvc.dylib" if sys.platform == "darwin" else "libplanetary_uvc.so")
args = [
    os.environ.get("CC", "cc"),
    "-shared",
    "-fPIC",
    "-O2",
    "-std=gnu17",
    "-pthread",
    "-I" + str(generated.parent),
    "-I" + str(vendor / "include"),
    str(root / "native/bridge.c"),
    *map(str, sources),
    *usb_flags,
    "-o",
    str(target),
]
if sys.platform == "darwin":
    args += ["-Wl,-install_name,@rpath/libplanetary_uvc.dylib"]
subprocess.run(args, check=True)
if sys.platform == "darwin":
    usb_library = os.environ.get("LIBUSB_LIBRARY")
    if not usb_library:
        libdir = subprocess.check_output(["pkg-config", "--variable=libdir", "libusb-1.0"], text=True).strip()
        usb_library = str(Path(libdir) / "libusb-1.0.0.dylib")
    bundled_usb = out / "libusb-1.0.0.dylib"
    shutil.copy2(usb_library, bundled_usb)
    dependencies = subprocess.check_output(["otool", "-L", str(target)], text=True).splitlines()[1:]
    for line in dependencies:
        dependency = line.strip().split(" (")[0]
        if "libusb" in dependency:
            subprocess.run(
                ["install_name_tool", "-change", dependency, "@loader_path/libusb-1.0.0.dylib", str(target)],
                check=True,
            )
    subprocess.run(["install_name_tool", "-id", "@rpath/libusb-1.0.0.dylib", str(bundled_usb)], check=True)
    for binary in (bundled_usb, target):
        subprocess.run(["codesign", "--force", "--sign", "-", str(binary)], check=True)
print("Built", target)
