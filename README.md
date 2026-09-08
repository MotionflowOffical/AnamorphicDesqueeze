# Anamorphic DNG Batch

A Windows-oriented batch desktop app for de-squeezing **hundreds of DNG files without resampling the RAW image data**.

## Why this preserves quality

Instead of decoding the RAW image, scaling pixels, and attempting to manufacture a new RAW file, this app changes the DNG `DefaultScale` metadata. `DefaultScale` represents the scale needed to convert non-square image pixels to square pixels. The RAW mosaic/linear image samples remain untouched.

This is the appropriate DNG-level method when the receiving RAW application honors `DefaultScale` (Adobe Camera Raw, desktop Lightroom, etc.). Some software may ignore this tag and show the squeezed preview even though the DNG itself contains the scale metadata.

## Optimized batch pipeline

- Scans file names with native filesystem calls; no image decoding.
- Reads metadata for the entire folder in one ExifTool batch.
- Uses persistent ExifTool processes (`-stay_open`) rather than launching a process per photo.
- ExifTool creates each output DNG directly while changing metadata, avoiding an extra whole-file copy pass.
- 1–8 parallel workers; 2 is a sensible SSD default and 1 is often best for spinning disks.
- Never changes the source files.
- Optional recursive scan and mirrored subfolder structure.
- Skip / overwrite / keep-both behavior for existing output files.
- Verification checks the output `DefaultScale` and compares `RawImageDigest` when the DNG contains one.

## Portrait and landscape handling

The program detects the **effective displayed orientation**, not merely the stored pixel dimensions. DNG/EXIF orientations 5–8 swap the stored X/Y axes. The app maps:

- landscape → displayed horizontal de-squeeze
- portrait → displayed vertical de-squeeze

back to the appropriate stored RAW axis before writing `DefaultScale`.

This is important because many portrait RAW photos are physically stored in the camera sensor's landscape orientation and are rotated only via metadata.

## Run on Windows

1. Install Python 3.11+ from python.org if necessary. During installation, enable **Add Python to PATH**.
2. Double-click `run.bat`.
3. If ExifTool is not installed, the app can download the current official 64-bit Windows release from the ExifTool SourceForge distribution, or run `install_exiftool.ps1` manually.
4. Choose a source folder and output folder.
5. Set the squeeze ratio (`1.33`, `2.0`, or a custom value).
6. Click **Process folder**.

There are no third-party Python packages required for normal use; the GUI is built with Tkinter from the Python standard library.

## Build a Windows EXE

Run `build_windows_exe.bat` on Windows. It installs PyInstaller and builds `dist\AnamorphicDNGBatch\AnamorphicDNGBatch.exe`.

ExifTool is deliberately kept as a separate tool next to the app because its Windows package also includes an `exiftool_files` directory. The app can download it automatically from the current official SourceForge distribution, with two mirror URL fallbacks.

## Notes / compatibility

- Source files must be `.dng`.
- Output is also `.dng`.
- The app does **not** rasterize to TIFF/JPEG and does not interpolate pixels.
- Existing `DefaultScale` can optionally be preserved and multiplied by the chosen anamorphic factor. This is enabled by default.
- If a target viewer ignores DNG `DefaultScale`, the RAW file may still *display* squeezed in that viewer. That is a viewer limitation, not loss of RAW information.
- Desktop Adobe RAW software is the main compatibility target for this metadata-based workflow.

## Safety

Sources are read-only. Outputs are first written with `.partial.dng` and atomically renamed after ExifTool reports success. This reduces the chance of leaving a normal-looking but incomplete output if the process is interrupted.


## Important compatibility note (v1.0.3)
DNG `DefaultScale` is image-directory metadata. DNGs may store the RAW image in `IFD0`, `SubIFD`, or another SubIFD while other IFDs contain thumbnails/previews. v1.0.3 detects the RAW image IFD and writes the scale there explicitly. This fixes files where an unqualified ExifTool write targeted a nonexistent or wrong SubIFD.

This mode does **not** resample the RAW sensor data. Lightroom Classic/Desktop/Web RAW rendering is the intended target. Some generic image viewers and Lightroom Mobile ignore `DefaultScale` and may still display their embedded squeezed preview; that is a viewer limitation, not a change to the underlying RAW samples.
