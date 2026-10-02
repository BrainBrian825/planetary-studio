from pathlib import Path
import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser(description="Archive and test the portable application")
parser.add_argument("--dist-dir", default="dist")
directory = Path(parser.parse_args().dist_dir)


def copy_with_permissions(source, destination):
    result = shutil.copyfile(source, destination)
    shutil.copymode(source, destination)
    return result


if sys.platform == "darwin":
    app = directory / "Planetary Studio.app"
    # Synced folders can immediately reattach Finder metadata after xattr clears
    # it. Sign and verify a stable copy outside the sync provider's directory.
    with tempfile.TemporaryDirectory(prefix="planetary-studio-package-") as temporary:
        staged = Path(temporary) / app.name
        shutil.copytree(app, staged, symlinks=True, copy_function=copy_with_permissions)
        if not os.access(staged / "Contents/MacOS/PlanetaryStudio", os.X_OK):
            raise RuntimeError("The staged macOS executable lost its execute permission.")
        subprocess.run(["xattr", "-crs", str(staged)], check=True)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(staged)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(staged)], check=True)
        archive = directory / "PlanetaryStudio-macOS-arm64.zip"
        subprocess.run(
            [
                "ditto",
                "-c",
                "-k",
                "--keepParent",
                str(staged),
                str(archive),
            ],
            check=True,
        )
elif sys.platform == "win32":
    archive = Path(
        shutil.make_archive(
            str(directory / "PlanetaryStudio-Windows-x64"), "zip", directory, "PlanetaryStudio"
        )
    )
else:
    arch = "arm64" if platform.machine() == "aarch64" else "x64"
    archive = Path(
        shutil.make_archive(
            str(directory / ("PlanetaryStudio-Linux-" + arch)), "gztar", directory, "PlanetaryStudio"
        )
    )

# Test exactly what a user downloads, including archive permissions and links.
with tempfile.TemporaryDirectory(prefix="planetary-studio-download-test-") as temporary:
    extracted = Path(temporary)
    if sys.platform == "darwin":
        subprocess.run(["ditto", "-x", "-k", str(archive), temporary], check=True)
        downloaded_app = extracted / "Planetary Studio.app"
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(downloaded_app)], check=True)
        executable = downloaded_app / "Contents/MacOS/PlanetaryStudio"
    else:
        shutil.unpack_archive(str(archive), temporary)
        executable = (
            extracted
            / "PlanetaryStudio"
            / ("PlanetaryStudio.exe" if sys.platform == "win32" else "PlanetaryStudio")
        )
    subprocess.run([str(executable), "--self-test", "--report", "build/archive-selftest.json"], check=True)
