# Building Planetary Studio

Python 3.12, a supported Qt desktop, and the packages pinned in `requirements-build.txt` are used for release builds. Python 3.12 is the tested version.

## macOS Apple silicon

Install the Command Line Tools and Homebrew's `libusb` and `pkgconf`. Create a virtual environment, install the requirements and project, and run `python native/build.py`. The bridge compiles the vendored libuvc and bundles libusb with relative library references. `python -m planetary_studio` starts the desktop.

The prebuilt release targets macOS 27. Supporting earlier macOS versions requires rebuilding the native bridge and all bundled native dependencies with matching deployment targets, then testing on those systems.

For a custom libusb location, `LIBUSB_INCLUDE_DIR` and `LIBUSB_LIBRARY` can identify a header directory and full library path. An installed libusb is otherwise found with pkg-config.

## Linux

Install `libusb-1.0-0-dev`, `pkg-config`, a C compiler, and Qt's runtime libraries. The workflow lists the tested Ubuntu dependencies, including `libegl1`, `libgl1`, and the XCB libraries. The build targets Ubuntu 24.04 or a compatible/newer glibc distribution; it is not a universal Linux binary for every distribution.

## Windows

Use x64 Python 3.12. Install the pinned packages and project. `native/build.py` intentionally skips the Unix UVC bridge. DirectShow and the configured vendor SDKs handle cameras. INDI and Alpaca connections are also available.

## Test and package

```sh
python -m pytest -q
python -m planetary_studio --self-test --report build/source-selftest.json
python -m PyInstaller --clean --noconfirm packaging/planetary-studio.spec
python packaging/archive.py
```

For headless Linux test runs set `QT_QPA_PLATFORM=offscreen`. The application self-test sets it automatically. The CI workflow also launches each packaged executable with `--self-test` before publishing a download.

The macOS app is signed with an ad hoc signature; official notarization requires a developer's Apple signing identity and credentials. Windows is a portable folder, not an installer, and is not Authenticode signed. Linux is a portable tar archive. A `v*` Git tag triggers a prerelease after all matrix jobs pass.

## Architecture

`cameras/` holds adapters behind a common Device/Mode/Frame interface. `native/bridge.c` transfers raw UVC frames into a protected native buffer so Python does not run on libusb's callback thread. The application camera worker owns the camera and recorder, and all control changes are routed to that worker. Frames are copied before reuse.

`ser.py`, `sources.py`, and `imaging.py` handle disk formats. `processing.py` is independent of Qt and contains preprocessing, registration, quality ranking, local stacking, and finishing algorithms. `workers.py` runs processing away from the event loop. `app.py` supplies the desktop workflow, projects, presets, and batch queue. The same processing engine is exposed through the CLI.
