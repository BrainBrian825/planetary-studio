# Image formats

## SER v3

The 178-byte header is little endian. Color IDs 0, 8–11, and 100–101 represent mono, the four Bayer patterns, RGB, and BGR. Raw pixels use one byte for 1–8-bit data and two bytes for 9–16-bit data. Readers honor the historical inverted endian flag (`0` means little endian), validate dimensions and the full declared pixel payload, and accept optional timestamp trailers. Writers finalize the frame count and write UTC .NET ticks (100 ns since year 1) for each received frame.

Timestamps are host receive times rather than a claim of hardware-trigger precision. During recording the header's recoverable count is updated every 32 frames; a sudden interruption can leave a small uncounted tail. Normal stopping finalizes all frames. The reader rejects empty or truncated declared recordings instead of silently inventing pixels.

The byte order behavior follows the [SER Player reference reader](https://github.com/cgarry/ser-player/blob/master/src/pipp_ser.cpp) and [writer](https://github.com/cgarry/ser-player/blob/master/src/pipp_ser_write.cpp); their source is referenced for format behavior and is not copied into the application.

## Other inputs

Video decoding is provided by the packaged OpenCV/FFmpeg backend. AVI, MOV, MP4, and MKV work when their codecs are available. Processing requires a readable frame count and supports random seeks. Use lossless SER for raw astronomy capture; compressed videos may contain prior color conversion and quantization.

TIFF, PNG, FITS, JPEG, and BMP image sequences are naturally sorted (`frame2` precedes `frame10`). All frames must have the same shape. FITS unsigned integer scaling is handled by Astropy, color planes are converted to RGB, and big-endian FITS data is converted to native endian before image operations. Float FITS processing/calibration inputs are expected to be normalized to 0–1; arbitrary astronomical ADU float images need normalization before import.

## Outputs

Stack and finished exports are 16-bit TIFF, 16-bit PNG, or float32 FITS. Output pixels are clipped to 0–1; monitor previews are 8-bit and do not change stored processing pixels. The JSON companion records processing parameters, frame quality, selected frames, global shifts, and alignment point positions. Prepared SER exports are debayered and calibrated 16-bit RGB/mono; capture SER preserves original raw pixels.
