# Changelog

## 1.0.3
- Fixed a critical DNG-layout bug: `DefaultScale` is now written to the IFD that actually contains the RAW/LinearRaw image instead of relying on ExifTool's default SubIFD target.
- Added RAW IFD detection using `PhotometricInterpretation`, CFA tags, image dimensions, and existing DNG scale tags.
- Fixed metadata dimension selection for DNGs that contain thumbnail/preview IFDs alongside the RAW image.
- Verification now checks the scale in the detected RAW IFD.
- ExifTool `Nothing changed` output is treated as a failure instead of a successful copy.
- UI logs the detected RAW IFD layout for diagnosis.
- Clarified viewer compatibility: metadata-only de-squeeze remains RAW-lossless, but generic viewers may ignore pixel-aspect metadata.


## 1.0.2
- Fixed fatal startup-processing error when setting the progress-bar maximum.
- `App.ui()` now safely forwards both positional and keyword arguments to Tk callbacks.
- Added regression coverage for keyword-argument UI dispatch.
- Snapshot Tk-bound settings before worker startup to avoid cross-thread Tcl/Tk access.

## 1.0.1 — 2026-08-27

- Fixed ExifTool automatic installation after the official Windows downloads moved away from the old `exiftool.org/exiftool-<version>_64.zip` path.
- Uses the official SourceForge direct-download endpoint first, with the SourceForge `/download` endpoint as a fallback.
- Added a second current-version endpoint fallback.
- Validates that a downloaded response is a real ZIP before extracting it.
- Requires both `exiftool.exe` and `exiftool_files` before replacing an existing installation.
- Stages the new ExifTool files before swapping them into place, reducing the risk of leaving a half-installed tool.
- Updated the standalone PowerShell installer to use the same resilient download logic.
