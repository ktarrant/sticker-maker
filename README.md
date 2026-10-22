# Sticker Maker

A local Python project for turning overhead photos of printed media into sticker cut paths for a Cricut Maker 4.

The project is at its setup stage. The camera is currently visible to macOS as a Sony **ILCE-6000** over USB. Sony lists the ILCE-6000 as supported by Imaging Edge Desktop, and Cricut Design Space accepts SVG vector uploads. The project includes the `gphoto2` Python binding for direct camera capture; Imaging Edge Desktop remains a fallback capture path:

1. Trigger a capture through libgphoto2 and retrieve the image directly into the Python workflow.
2. Rectify the photo against a Cricut mat reference and let a person review proposed sticker regions.
3. Export reviewed cut paths as a correctly scaled SVG.
4. Import the SVG into Cricut Design Space and send it to the Maker 4.

The first milestone should validate the complete physical workflow and time the capture-to-preview path. Direct cutter control is a separate later investigation; Cricut Design Space is the baseline machine handoff.

## Setup

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run sticker-maker --help
```

The code uses a `src/` layout and is installed as a package into the project environment. `uv sync` also installs the Python gphoto2 binding and its bundled libgphoto2 wheel on supported macOS/Python combinations. Raw photos and generated files belong under the ignored `data/` and `output/` directories.

## Capture probe status

- UV installed `gphoto2` 2.6.4 successfully on this Mac.
- With direct USB access, libgphoto2 detects one `USB PTP Class Camera` at `usb:001,003`; macOS identifies that USB device as `ILCE-6000`.
- Opening the PTP device currently fails with `Could not claim the USB device`, so capture control is not confirmed yet. The next check is the camera's `USB Connection` setting (`PC Remote`) and whether macOS or another app is holding the PTP interface.

## Project plan

### 1. Prove capture and physical setup

- Set the ILCE-6000's USB Connection mode to PC Remote and verify gphoto2 can open it, trigger a capture, and retrieve the image. If USB access remains unavailable, use Sony Imaging Edge Desktop (Remote) to save captures into a watched folder while investigating the PTP access issue.
- Fix the camera directly above the mat, lock focus/exposure/white balance, and use even diffuse lighting to limit glare and shadows.
- Photograph the empty mat and a test sheet with known dimensions. Record camera, lens/zoom, height, orientation, mat type, and image settings.
- Measure capture-to-file and capture-to-preview latency. If Sony Remote's saved-file workflow is too slow, compare USB tethering alternatives only after confirming model and macOS support.

### 2. Define coordinates and calibration

- Establish a canonical mat coordinate system in millimeters, with a documented origin and orientation matching the Cricut mat loaded into Design Space.
- Detect mat corners or fiducials, then rectify perspective and map pixels to millimeters. Include a manual four-corner fallback.
- Measure repeatability across captures and after reloading the mat. Add a calibration artifact and reject jobs when calibration confidence is poor.

### 3. Build a human-reviewed image pipeline

- Normalize lighting and color, then propose candidate sticker regions using a mix of segmentation, edge/contour extraction, and optional interactive foreground selection.
- Present the source image with editable boundaries. Keep uncertain choices reviewable; do not auto-cut based on an opaque model decision.
- Smooth contours, remove tiny artifacts, optionally add a configurable offset for a sticker border, and preserve holes/islands where appropriate.
- Keep processing modular so local ML segmentation can be evaluated later against simpler CV methods on a small, consented example set.

### 4. Export a safe cut job

- Convert accepted regions from image pixels to SVG paths in physical millimeters, with predictable scaling and mat placement.
- Preview the SVG over the original image and enforce mat bounds, minimum feature size, and total cut-area checks before export.
- Version each job with the source image, calibration, chosen regions, and SVG so the result is reproducible.
- Import the SVG into Cricut Design Space and validate alignment, material choice, blade, and pressure with a small test cut before a full sheet.

### 5. Tighten the loop and automate selectively

- Add a capture-folder watcher, job queue, processing status, and a local review screen; optimize for a usable preview within seconds of the file landing.
- Evaluate batch capture and recoverability for camera disconnects, malformed files, and interrupted processing.
- Investigate whether Cricut offers a supported external control path. Until verified, keep Design Space as the machine handoff because its SVG upload path is documented and the final physical cut needs operator supervision.

## Initial architecture

- `capture`: control the camera through the gphoto2 Python binding; provide a watched-folder adapter for Imaging Edge Remote as a fallback.
- `calibration`: mat detection, perspective correction, and pixel-to-mm transform.
- `segmentation`: candidate detection and human edits.
- `geometry`: contour cleanup, offsets, and physical constraints.
- `export`: preview image and Cricut-compatible SVG.
- `jobs`: local job metadata and pipeline orchestration.
- `cli` (then optionally a local UI): user-facing workflow.

Suggested initial dependencies, added when each milestone starts: OpenCV and Pillow for image handling, a small geometry library only if needed for robust offsets, and a lightweight local UI framework only after the review interaction is clear. Keep the first implementation local and deterministic; evaluate optional ML after collecting representative images.

## References

- [Sony ILCE-6000 specifications](https://www.sony.com/electronics/support/e-mount-body-ilce-6000-series/ilce-6000/specifications) list PC Remote capability.
- [Sony Imaging Edge supported devices](https://support.d-imaging.sony.co.jp/app/imagingedge/ja/devices/) lists ILCE-6000 support for Remote shooting.
- [Sony Imaging Edge USB connection instructions](https://support.d-imaging.sony.co.jp/app/imagingedge/en/instruction/4_1_connection.php) describe PC Remote setup for supported cameras.
- [Cricut Design Space image upload instructions](https://help.cricut.com/hc/en-us/articles/360009556313-How-to-upload-images-into-Design-Space) describe SVG uploads as vector cut paths.
- [Cricut Maker 4 quick start](https://help.cricut.com/hc/en-us/articles/28450495457559-Cricut-Maker-4-Quick-Start-Guide) documents Maker 4 setup and mat use.
