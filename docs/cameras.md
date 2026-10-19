# Camera connections

## Celestron NexImage 10 on Apple silicon

Choose **Scan cameras → NexImage 10 [UVC] → Connect**. Select a raw GRBG mode and start preview. Capture records the raw mosaic; the preview debayers it to RGB.

Choose resolution, Color or Mono, bit depth, and frame rate independently. The app filters later choices to the modes reported by the camera. A Color choice uses raw GRBG when available. The frame rate slider steps through advertised rates; exposure and gain sliders use the driver's actual limits, with numeric fields for precise values. The capture statistics show the measured frame rate. **Stop live view** before changing capture format, then **Start live view**. The USB camera handle remains open; exposure/gain settings are retained. Stopping live view finalizes an active recording. System camera connections pause reads while retaining their OpenCV connection; INDI/Alpaca abort a pending exposure while retaining their server connection.

The camera identifies as USB vendor `199e`, product `8619` and exposes frame-based descriptors. This is different from cameras that expose only uncompressed UVC descriptors. The native bridge negotiates the advertised descriptor directly and preserves the exact frame interval.

The bundled libuvc includes the macOS fix found while debugging oaCapture: when driver detachment returns `LIBUSB_ERROR_ACCESS`, it attempts `libusb_claim_interface`, which may still be allowed. The result of that claim is checked. It does not ignore authorization errors or bypass OS permissions. The bridge reports the actual USB error when opening or streaming fails.

Hardware validation on October 2, 2026: macOS 27 arm64, USB 3, NexImage 10, 640×480 GRBG8 at an advertised 90 fps; **259 raw frames recorded in a three-second probe**. Other resolutions, 16-bit modes, exposure/gain changes, long-duration thermal behavior, and other hardware models need separate hardware validation.

`BA16` is offered only for the NexImage 10's known GRBG sensor; generic BA16 cannot tell the Bayer order. Other unknown FOURCCs are skipped instead of interpreted incorrectly.

## Celestron NexImage Burst C (EUVC)

The native USB bridge includes a port of OpenAstro's EUVC protocol for Imaging
Source USB vendor `199e`, products `8201`–`8208`, the family used by the Burst C.
Rebuild with `python native/build.py`, launch Planetary Studio from this source
checkout, then choose **Scan cameras → your camera [UVC] → Connect**. The USB
product string may identify an Imaging Source model rather than “NexImage”.
Exposure and gain use the existing camera controls; raw Bayer samples are
preserved in SER recordings and debayered for preview.

This port initially exposes the largest advertised raw mode at 8 bits with
binning disabled. Smaller ROI and binned modes from oaCapture are not exposed.
The Bayer order comes from the camera's EUVC color-format control rather than
interpreting its misleading YUV descriptor as converted color. Unknown Bayer
codes fail explicitly. Frame rates use OpenAstro's 60, 30, 15, 7.5 and 3.75 fps
choices, filtered against the sensor's reported pixel-clock limits. The current
UI rounds the last two labels to 8 and 4 fps; the exact intervals are retained.
Long exposures can reduce the measured rate further.

EUVC setup requests use OpenAstro's five-second control timeout. If live view
still fails to start, the error identifies the failing USB operation (for
example, stream COMMIT, binning, or pixel clock), including the selector for
sensor controls. Use **Camera support → Save diagnostics…** to retain that detail.
After rebuilding the native bridge, completely quit and restart the application
to load the updated library.

Linux USB permissions are required. An example udev rule for this EUVC family is:

```udev
SUBSYSTEM=="usb", ATTR{idVendor}=="199e", ATTR{idProduct}=="820[1-8]", TAG+="uaccess"
```

Install the rule in `/etc/udev/rules.d/99-neximage.rules`, reload udev rules, and
reconnect the camera. Close oaCapture or any other application using it first.
For a source-checkout hardware check, run:

```sh
python -m planetary_studio probe-camera 'camera product name' burst-test.ser --seconds 3
```

The Linux native build and simulated USB protocol tests pass. Physical Burst C
capture has not been validated in this development environment. The direct USB
backend is available on Linux/macOS; Windows continues to use system drivers.

## UVC and system cameras

Direct UVC on Unix supports the formats listed in the README. Mode support is read from each camera. If a driver offers a control, the application reads its range instead of guessing it. Some cameras have proprietary exposure controls or need a vendor driver; a UVC interface alone does not guarantee full control.

System connections use the platform camera driver through OpenCV. On Windows install the manufacturer's DirectShow driver when required. Converted RGB system video is generally 8-bit. DirectShow camera order can differ from the operating system's enumeration with unusual driver combinations; verify the preview identifies the selected device. Native raw UVC on Windows is not bundled because libusb would require a different USB driver binding; use the normal system/vendor driver path.

## ZWO ASI and QHY

Download a matching native SDK from [ZWO](https://www.zwoastro.com/software/product-sdk/) or [QHY](https://www.qhyccd.com/news2/). Keep the manufacturer's accompanying dependencies together. In **Camera settings**, browse to `libASICamera2.dylib` / `libASICamera2.so` / `ASICamera2.dll`, or the equivalent `qhyccd` library. The library must match arm64 or x64 and the operating system of the application. No camera drivers are downloaded or installed automatically.

Alternative library locations for source use: `PLANETARY_ASI_SDK` / `PLANETARY_QHY_SDK`, or `~/.planetary-studio/drivers/`. The UI explicitly scans configured SDKs. These adapters have automated API tests, not a tested-model certification. QHY must support live video for this adapter.

## INDI

Run an INDI server with the manufacturer's camera driver, enter `host:7624`, then scan. Planetary Studio acquires FITS exposure BLOBs and preserves raw Bayer metadata from the FITS header. It supports normal INDI camera connection, exposure, abort, and image properties. Continuous INDI `.stream` video is not currently decoded. Short repeated FITS exposures may have lower frame rates than the native USB adapters.

INDI's driver ecosystem can expose cameras from additional manufacturers, but availability depends on the installed server driver and OS. [INDI protocol documentation](https://docs.indilib.org/protocol/).

## ASCOM Alpaca

Enter an Alpaca server's URL, such as `http://localhost:11111`, then scan. Camera discovery uses `management/v1/configureddevices`. ImageBytes v1 is requested for efficient transfers; JSON is accepted as a fallback. Mono, raw RGGB and Bayer offsets, and RGB images are supported up to the unsigned 16-bit SER range. Camera exposures are repeated; this is not a guarantee of planetary video rates.

Windows ASCOM camera drivers can be served through ASCOM Remote and accessed from any of the app's platforms. [Official Alpaca reference](https://ascom-standards.org/AlpacaDeveloper/ASCOMAlpacaAPIReference.html).

## Linux USB permissions

For a NexImage 10, an example `/etc/udev/rules.d/99-neximage.rules` is:

```udev
SUBSYSTEM=="usb", ATTR{idVendor}=="199e", ATTR{idProduct}=="8619", TAG+="uaccess"
```

Reload udev rules and reconnect the camera. Distribution and remote-session policies differ; use the appropriate camera access group on systems without `uaccess`. Run the desktop as your normal account.

## Connection failures

Close other camera applications; connect directly by USB; scan again after reconnecting. Read Camera Support's diagnostic log. The app distinguishes access denial, a busy camera, a missing device, unsupported modes, and frame transfer failures. Driver-reported dropped-frame counts do not necessarily include losses inside the camera or USB transport before the driver reports a frame.
