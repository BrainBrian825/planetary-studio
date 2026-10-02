Planetary Studio 0.1.1 is the first usable public prerelease: capture, prepare, align and stack, then sharpen in one desktop application.

This replaces the withdrawn 0.1.0 Mac archive. Downloaded applications are now unpacked and self-tested on every platform before publication, including macOS execute permissions and code signatures.

- Raw SER capture and preview, native NexImage 10 GRBG support with the macOS USB fix, system camera paths, optional ZWO/QHY native SDK adapters, INDI, Alpaca, and a full camera simulator.
- Dark/flat calibration, object centering, cropping, frame quality selection, subpixel global registration, overlapping local alignment points, and local stacking.
- Six-scale wavelet sharpening, Richardson–Lucy deconvolution, RGB alignment, channel balance, saturation, gamma, and 16-bit TIFF/PNG or float FITS export.
- Projects, presets, batch queue, processing reports, and command-line processing.

Downloads are tested on macOS 27 Apple silicon, Windows x64, Ubuntu 24.04 x64, and Ubuntu 24.04 arm64. The packaged application passes an end-to-end self-test on each platform before release. A physically attached NexImage 10 was recorded through the new macOS UVC adapter. Other camera models and optional vendor SDKs are not hardware certified.

The prebuilt macOS application requires macOS 27 or newer on Apple silicon; its bundled native camera library targets macOS 27.

This is a prerelease, not a claim of full parity with established applications. Read the repository's camera guide and validation notes. This release uses Lanczos enlargement and does not implement drizzle or planetary derotation. Vendor SDKs must be supplied by the user. macOS is ad hoc signed and Windows is a portable, unsigned download.
