Planetary Studio 0.2.0 updates the desktop appearance and camera controls.

- Simple neutral colors with Follow system, Light, and Dark appearance. Manual choices persist across restarts; system appearance changes are followed where the desktop reports them.
- A new monochrome planet-and-star logo, used in the app and native macOS/Windows icons.
- macOS runs as a normal Dock application. The background-only bundle flag has been removed.
- Separate resolution, Color/Mono, bit depth, and frame rate selectors. The FPS slider steps through valid camera rates and preserves the driver's native mode and exact USB interval.
- Exposure and gain sliders with precise numeric fields. Wide exposure ranges use a logarithmic slider for easier control of short planetary exposures.
- The simulator now offers Color/Mono, 8/16-bit capture, two resolutions, and 15/30/60 fps.

Camera format controls are set before preview; disconnect to change capture format. Exposure and gain remain adjustable during preview. Cameras with one available choice display that choice with its control disabled. Actual FPS depends on exposure and transfer speed.

All four downloads are unpacked and self-tested before publication: macOS 27 Apple silicon, Windows x64, Linux x64, and Linux arm64. macOS signatures, executable permissions, normal application bundle flags, and the Dock icon are checked. The prebuilt Mac app requires macOS 27 or newer.

This remains a prerelease. The core capture, preparation, alignment, stacking, wavelet sharpening, deconvolution, and batch workflows are included. NexImage 10 raw GRBG8 hardware capture has been tested; other camera models and vendor SDKs require hardware validation. Vendor SDK binaries are supplied separately. Drizzle and planetary derotation are not implemented. macOS is ad hoc signed and Windows is unsigned.
