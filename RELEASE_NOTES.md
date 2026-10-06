Planetary Studio 0.5.4 fixes false planetary motion caused by fixed sensor noise and rectangular local stack seams.

- Registration compares smoothed image structure before accepting a phase-correlation proposal and refines translation using intensity correlation. A fixed noise pattern cannot win merely because phase whitening amplified its high-frequency signal. Registration works at bounded resolution and retains coordinates at the original subpixel scale.
- Local registration applies the requested displacement limit while evaluating candidates. Failed refinement retains the best coarse match; poorly correlated frames are rejected.
- Local patch coverage now feathers into the underlying global stack, including sparse or irregular alignment grids. The patch window is no longer canceled by normalization at uncovered boundaries.
- Hot-pixel filtering uses a faster median operation, preserving separate Bayer planes and the existing brightness threshold.
- Stack reports include the application version, registration method and patch blending method.
- Regression tests cover soft planetary images with displaced fixed sensor noise, subpixel translations, local displacement limits and smooth transitions between local/global frame selections. The packaged self-test also exercises sensor-noise rejection.
- Recording recovery tests wait for all completion signals, including the final recording state, so their results are consistent across platforms.

Previous 0.5.2 features:

- Stop live view, change resolution, color/mono, bit depth or frame rate, then start again. The camera stays connected. Stopping live view finishes any active recording. Exposure/gain controls remain available.
- UVC stream restarts discard old frames, reset counters and preserve the camera handle. ZWO ASI/QHY video and INDI/Alpaca exposures have matching stop/restart lifecycles; system cameras pause reads while retaining their driver connection.
- Individual damaged UVC frames are skipped and counted. Thirty consecutive invalid frames stop live view, finalize any recording and allow a different mode to be selected.
- A failed mode start or recording file open leaves the camera connected. Disk write errors stop recording while retaining live view. SER finalization closes the file even if disk synchronization fails and removes any partial final frame when possible.
- Recording reports include selected resolution/rate/FOURCC, requested and actual duration, skipped frames, stopping reason, exact error and finalization status. Time limits also work while the camera is not delivering frames.
- Camera support retains a bounded local diagnostic log across app sessions and provides Save diagnostics. Reports and logs stay on the computer unless you choose to share them.
- Automated checks exercise 8/16-bit damaged-frame recovery, format changes, persistent corruption, camera disconnection, disk errors, recording deadlines, UI state and vendor SDK lifecycles. A hardware test cannot identify the cause of a past recording failure whose error was not retained.

Previous 0.5.1 features:

- Page headings use Capture, Prepare & Stack, Sharpen, Batch Queue, and Camera support, with direct descriptions of their controls.
- Minimum brightness filters automatic alignment point centers using real image brightness; the grid is centered on the bright region. Preview display stretching does not affect placement.
- Alignment boxes use the actual even patch size after small/odd crops and output enlargement. Center markers make their positions clear.
- Manual alignment editing adds points with a click and removes points with a right-click inside a box. Edits update immediately; points are saved in projects. Auto grid restores automatic points and Clear selects global-only alignment.
- Local and Global frame ranking provide independent frame selection per point or a shared selection at every point. Global ranking and stacks without local points skip registration of discarded frames.
- First frame and Last frame trim recordings for sample preview, stacking, and prepared SER export. Processing reports preserve original source frame numbers.
- Optional missing-object and cut-off object rejection works before centering, checks crop boundaries, and applies to both stacking and prepared SER export. A detection box, automatic or manual brightness threshold, minimum object size, and preview rejection reason help review settings. Moon / Sun mode disables object rejection.
- Gradient, Laplacian, Brenner, and Brightness quality estimators are available. Noise smoothing affects detail scoring only. Keep best is calculated after object rejection; reports include original source indices and rejection reasons.
- The gridded quality graph can show best-to-worst ranking or recording order, with a corresponding selection threshold.
- Ranked graphs omit rejected frames; recording-order graphs mark them in amber. All frame selection and detection settings are saved in projects and apply to sample previews and batch processing.
- Settings rows wrap when needed to fit the native controls on macOS, Windows, and Linux. Quality noise smoothing accepts decimal values.

The experimental neural wavelet sharpening prototype remains local and is not part of this release.

Previous AI cleanup features:

- Bundled MIT-licensed FFDNet color and monochrome models clean up noise after wavelets and deconvolution, before color/tone adjustments.
- Strength and Noise level each have a slider and an exact-value field. Strength 0 disables cleanup; cleanup is off by default and stays off for old presets/projects.
- Live previews, presets, project settings, batch jobs, and export reports use the same cleanup settings. Processing keeps float precision and supports 16-bit TIFF/PNG or float FITS.
- Models run locally on the CPU with no separate downloads, GPU, or image uploads. Approximately 5.3 MB of model data is included with provenance and the upstream license.
- Context around processing tiles prevents seams and supports odd image dimensions. Concurrent jobs cannot mix model inputs.
- Each source and packaged-platform self-test executes both models and verifies noise reduction against a known synthetic reference.
- A tooltip explains how the wavelet Noise threshold can suppress sharpening. This threshold is separate from post-sharpen AI cleanup.

FFDNet is trained on general photographs. Start with low strength and compare fine planetary detail. It may soften real features and cannot repair excessive sharpening halos or recover detail absent from the source.

Previous features remain available:

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
