# Third-party notices

Planetary Studio's source is GPL-3.0-or-later. It independently implements its imaging workflow and is not endorsed by or affiliated with the named camera manufacturers or the authors of oaCapture, PIPP, AutoStakkert, or waveSharp.

- **libuvc 0.0.7**, copyright its contributors, BSD license, vendored under `native/vendor/libuvc`; full license at `native/vendor/libuvc/LICENSE.txt`. Source originated from the libuvc snapshot in `BrainBrian825/openastro` with the macOS interface-claim fix from commit `c5dd00c`. Additional bridge changes are in this repository.
- **libusb**, LGPL-2.1-or-later, [source and license](https://github.com/libusb/libusb). Binary dependencies may be bundled in portable packages; users can rebuild the bridge with a replacement compatible libusb library. The included source build instructions permit relinking.
- **PySide6 / Qt**, LGPL-3.0/GPL-3.0 and applicable component licenses, [Qt licensing](https://doc.qt.io/qt-6/licensing.html). Bundled dynamically by PyInstaller; source available from Qt and the corresponding PySide release.
- **NumPy**, BSD-3-Clause; **SciPy**, BSD-3-Clause; **Astropy**, BSD-3-Clause; **tifffile**, BSD-3-Clause; **Pillow**, HPND; **OpenCV**, Apache-2.0 with its third-party codec notices; **certifi**, MPL-2.0. Their wheels contain their component notices.
- **PyInstaller**, GPL-2.0-or-later with its bootloader distribution exception. The exception permits packaged application distribution under the application's license.
- **FFDNet clipped-image color and monochrome pretrained models**, copyright (c) 2019 Kai Zhang, MIT license, obtained from the author's [KAIR v1.0 release](https://github.com/cszn/KAIR/releases/tag/v1.0). The convolution weights are converted without retraining into bundled ONNX graphs. The full upstream license and provenance/checksums are included under `src/planetary_studio/assets/models`. `packaging/export_ffdnet.py` reproduces the conversion from checksum-verified original weights. PyTorch and ONNX Python are conversion tools and are not application dependencies.

The build packaging step copies dependency license directories into the distribution. The pinned versions are recorded in `requirements-build.txt`. Vendor camera SDKs are not bundled; users obtain them under the manufacturer's own terms.
