# AI noise cleanup

Planetary Studio 0.4 includes optional **FFDNet** cleanup after wavelet sharpening and Richardson–Lucy deconvolution. It is disabled when Strength is 0. Color and gamma adjustments follow cleanup. The existing wavelet Noise threshold is separate: it suppresses small coefficients during sharpening.

## Controls

- **Strength, 0–100%:** blends the model's result with the sharpened input. Start at 20–30%. Zero bypasses the model exactly.
- **Noise level, 0–50:** expected noise standard deviation on a 0–255 brightness scale; this is a model parameter, not the image's bit depth. Start at 3 for a lightly noisy stack. Increasing it can remove more noise and more fine detail. Zero also bypasses inference.
- **Show original:** compares with the unsharpened source. To compare cleanup alone, set Strength to 0 and restore it to the previous value.

Presets, project files, batch jobs, and TIFF/PNG/FITS export reports store `ai_denoise_amount` (0–1) and `ai_denoise_noise` (0–50). Old files missing these fields use amount 0 and noise level 3. Reset adjustments turns cleanup off.

## Model choice

| Candidate | Relevant properties | Integration decision |
|---|---|---|
| [FFDNet in KAIR](https://github.com/cszn/KAIR) | MIT-licensed project; author publishes compact RGB and grayscale models with explicit noise-level inputs | Included; approximately 5.3 MB combined, CPU inference through the existing OpenCV dependency |
| [DRUNet in DPIR](https://github.com/cszn/DPIR) | MIT-licensed project; controllable noise level; KAIR's pretrained weights are about 130 MB per model | Possible future comparison; significantly larger than FFDNet |
| [Restormer](https://github.com/swz30/Restormer) | MIT-licensed project; pretrained color/grayscale and real-image denoisers, about 100 MB per model | Possible future comparison; has not been evaluated in Planetary Studio |

The included clipped-image weights come directly from the author's [KAIR v1.0 model release](https://github.com/cszn/KAIR/releases/tag/v1.0): `ffdnet_color_clip.pth` and `ffdnet_gray_clip.pth`. The upstream implementation and license were examined at commit `fc1732f4a4514e42ce15e5b3a1e18c828af47a1e`. Original and converted model SHA-256 checksums, URLs, and convolution counts are in `src/planetary_studio/assets/models/manifest.json`. The upstream MIT notice is distributed alongside the models.

[FFDNet paper](https://arxiv.org/abs/1710.04026): Kai Zhang, Wangmeng Zuo, and Lei Zhang, *FFDNet: Toward a Fast and Flexible Solution for CNN based Image Denoising*, IEEE Transactions on Image Processing, 2018.

## Precision, memory, and portability

The application uses float32 inference with the OpenCV CPU backend. It neither converts input to 8-bit nor stretches its brightness for the model. Odd image dimensions are padded by replicating their final row/column, then restored. Processing uses even 192-pixel tiles with 32 pixels of surrounding context; this context exceeds both networks' receptive radii and preserves pixel-shuffle phase. The image is never resized for cleanup. Calls sharing a model are serialized because OpenCV networks have mutable inference state.

The bundled ONNX graphs contain the original convolution weights without retraining. Pixel packing, the noise map, and unpacking use NumPy. `packaging/export_ffdnet.py` reproducibly builds the graphs from checksum-verified upstream weights. PyTorch and the ONNX Python package are needed only for conversion; neither is needed to run or build the normal app. The models load only when cleanup is requested, and their checksums are checked at first load. A missing/damaged model produces an error rather than silently exporting an image without requested cleanup.

## Validation and limits

Tests exercise color and monochrome inference, reduction of error against a known synthetic planetary reference, zero-strength bypass, floating-point blend precision, odd and tiny images, tiled/whole-image agreement, concurrent calls, and processing order. Every source and packaged platform self-test executes both bundled models and checks known-reference noise reduction. This is broader than merely testing whether a model file can load.

Local manual evaluation also used a user's already-sharpened 480 × 256 Saturn stack. At noise level 3, cleanup took approximately 0.12 seconds on the development Mac. This is an observation for that image and machine, not a timing guarantee. User images remain local and are not included in this repository.

FFDNet was trained on general photographic noise, not on noise amplified by planetary wavelets. Synthetic test improvements do not establish the accuracy of features in a real recording. High strength/noise settings can remove faint bands, fine ring structure, or other real features. Cleanup is not a remedy for excessive sharpening halos or a soft source image; compare results at low strength and keep the original stack. The implementation does not perform super-resolution or train an automatic wavelet tuner.
