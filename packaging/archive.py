from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

directory = Path("dist")
if sys.platform == "darwin":
    app = directory / "Planetary Studio.app"
    # Synced folders can immediately reattach Finder metadata after xattr clears
    # it. Sign and verify a stable copy outside the sync provider's directory.
    with tempfile.TemporaryDirectory(prefix="planetary-studio-package-") as temporary:
        staged = Path(temporary) / app.name
        shutil.copytree(app, staged, symlinks=True, copy_function=shutil.copyfile)
        subprocess.run(["xattr", "-crs", str(staged)], check=True)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(staged)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(staged)], check=True)
        subprocess.run(
            [
                "ditto",
                "-c",
                "-k",
                "--keepParent",
                str(staged),
                str(directory / "PlanetaryStudio-macOS-arm64.zip"),
            ],
            check=True,
        )
elif sys.platform == "win32":
    shutil.make_archive(str(directory / "PlanetaryStudio-Windows-x64"), "zip", directory, "PlanetaryStudio")
else:
    arch = "arm64" if platform.machine() == "aarch64" else "x64"
    shutil.make_archive(
        str(directory / ("PlanetaryStudio-Linux-" + arch)), "gztar", directory, "PlanetaryStudio"
    )
