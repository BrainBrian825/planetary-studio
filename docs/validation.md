# Validation and known limits

CI runs the numerical/format/protocol/desktop tests, then repeats an end-to-end self-test **inside the packaged executable** on macOS arm64, Windows x64, Linux x64, and Linux arm64. Each portable download is then unpacked into a temporary directory and self-tested again. macOS code signatures are verified after extraction. This tests import availability, Qt plugin/dependency packaging, archive permissions and links, as well as source functionality.

The automated suite checks raw SER pixel preservation and timestamps at multiple depths and Bayer patterns, external big-endian SER data, truncated recordings, file overwrite protection, Bayer RGB color order, recovery of known subpixel shifts, preference for sharp frames, reduction of noise in aligned stacks, calibration, prepared SER and master creation, high-depth TIFF/PNG/FITS round trips, sharpening identity and deconvolution, cancellation and file release, natural sorting, Alpaca ImageBytes types/orientation and an HTTP camera server, INDI FITS BLOB decoding, desktop startup, and threaded simulator recording.

Appearance tests verify live system changes, manual overrides, and preference persistence. Camera UI tests verify constrained resolution/color/depth/rate combinations, preservation of the native mode object, integer gain values, logarithmic exposure limits and exact-value input, and simulator mono/depth/rate behavior. Packaged self-tests also load the logo and operate the separate selectors. macOS archive checks reject a background-only or menu-only app bundle and require the new Dock icon.

Synthetic truth images let tests measure registration and image error; this is stronger than testing only that a function returns an array. It is still not a comparative benchmark against AutoStakkert on actual atmospheric seeing. A new algorithm needs broad real-data review before claims of equivalent quality or speed.

The physically connected NexImage 10 has been opened and streamed by the application's direct UVC adapter on macOS 27 arm64. The first raw GRBG8 hardware recording contains 259 frames in a three-second probe. Vendor SDK adapters and other camera models require physical hardware to confirm firmware behavior and vendor-library architecture compatibility. No such absent camera is labeled hardware tested.

The NexImage 10's BA16 descriptor is mapped to GRBG16 for optional 16-bit capture. This mode remains experimentally supported; the hardware recording above validates GRBG8 only.

Current limitations are described in the README and camera guide. In particular: no rotational derotation or drizzle; converted system-camera frames are 8-bit; INDI/Alpaca use repeated exposures; vendor SDK binaries need user installation; no hardware certification across every model; portable Linux requires compatible system desktop libraries. macOS/Windows packages do not have paid developer signing certificates.
