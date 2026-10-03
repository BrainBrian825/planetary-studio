Planetary Studio 0.3.1 adds previews before full-recording processing, improves real AVI handling, and fixes file dialog behavior.

- Every file picker starts in the last folder selected anywhere in the app, including opening recordings, image exports, presets, projects, calibration files, camera libraries, and queue folders. The folder also survives restarts; a removed folder falls back to Downloads or the home folder.
- Accepting Replace in a save dialog now works for SER recordings, prepared SER exports, and calibration masters. Image exports also honor replacement. When adding a missing extension leads to an existing file, a replacement confirmation appears for that final filename.
- Processing exports cannot replace their source recording or source images.

- Prepared-frame preview updates when Bayer pattern, crop, centering, calibration, hot-pixel removal, or output size changes. Original/prepared views and frame numbers make comparison clear.
- Preview sample stacks 2–64 evenly spaced frames (12 by default), with the same processing settings as a full stack. Samples are labeled approximate and can be sent to Sharpen for tuning.
- Quality graphs now include percentage grids, relative-quality labels, selected-area shading, and a Keep best cutoff marker.
- Optional alignment-point overlays help review point size and coverage before running the full stack.
- Stack, export, and sharpening actions stay visible below the settings panel.
- Raw grayscale AVI samples expanded to three equal channels by the decoder are correctly recovered for explicit Bayer processing. Already converted color input rejects an incorrect Bayer override. Untagged raw AVI still requires manual pattern selection.
- Original, prepared, sample, and full-stack previews use consistent brightness. Brighten preview is explicitly display-only.
- Opening another source clears stale results, and the input chooser remembers its folder. AVI seeking has a decode-forward fallback.

All four downloads are unpacked and self-tested before publication: macOS 27 Apple silicon, Windows x64, Linux x64, and Linux arm64. This remains a prerelease. Sample quality can differ from the full recording. Camera hardware beyond the tested NexImage 10 raw GRBG8 path requires validation; vendor SDK binaries are installed separately. Drizzle and planetary derotation are not implemented. macOS is ad hoc signed and Windows is unsigned.
