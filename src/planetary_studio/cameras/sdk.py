import ctypes as C
import ctypes.util
import os
from pathlib import Path
import sys
from .base import CameraError


def load_sdk(name, configured_path=""):
    filenames = {
        "ASI": ("ASICamera2.dll", "libASICamera2.dylib", "libASICamera2.so"),
        "QHY": ("qhyccd.dll", "libqhyccd.dylib", "libqhyccd.so"),
    }[name]
    filename = filenames[0 if sys.platform == "win32" else 1 if sys.platform == "darwin" else 2]
    candidates = [
        configured_path,
        os.environ.get("PLANETARY_" + name + "_SDK", ""),
        str(Path.home() / ".planetary-studio/drivers" / filename),
        C.util.find_library("ASICamera2" if name == "ASI" else "qhyccd"),
    ]
    last_error = None
    for path in filter(None, candidates):
        try:
            # QHY uses STDCALL on Windows. ASI uses the C calling convention.
            loader = C.WinDLL if name == "QHY" and sys.platform == "win32" else C.CDLL
            if sys.platform == "win32" and Path(path).is_file():
                with os.add_dll_directory(str(Path(path).resolve().parent)):
                    return loader(path)
            return loader(path)
        except OSError as e:
            last_error = str(e)
    raise CameraError(
        f"{name} driver library is not configured. Select its native library in Camera settings."
        + (f" {last_error}" if last_error else "")
    )


def bind(lib, name, args, result=C.c_int):
    function = getattr(lib, name)
    function.argtypes, function.restype = args, result
    return function
