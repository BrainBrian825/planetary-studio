from pathlib import Path
import platform
import shutil
import subprocess
import sys

directory = Path("dist")
if sys.platform == "darwin":
    app = directory / "Planetary Studio.app"
    subprocess.run(["xattr", "-cr", str(app)], check=True)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    subprocess.run(
        ["ditto", "-c", "-k", "--keepParent", str(app), str(directory / "PlanetaryStudio-macOS-arm64.zip")],
        check=True,
    )
elif sys.platform == "win32":
    shutil.make_archive(str(directory / "PlanetaryStudio-Windows-x64"), "zip", directory, "PlanetaryStudio")
else:
    arch = "arm64" if platform.machine() == "aarch64" else "x64"
    shutil.make_archive(
        str(directory / ("PlanetaryStudio-Linux-" + arch)), "gztar", directory, "PlanetaryStudio"
    )
