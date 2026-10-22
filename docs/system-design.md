# System design

## Goal and decisions

Turn an overhead photo into reviewed cut paths that align with the original printed media on a Cricut Maker 4 mat. The working assumption is that the media receives adhesive backing before capture; reproducing artwork on new sticker stock would require a separate Print Then Cut workflow.

- **Capture:** direct USB control through Python `gphoto2`/libgphoto2, with the A6000 in PC Remote mode. Preserve the original camera file and use captured stills rather than requiring live view.
- **Production rig:** fixed top-down camera mount and diffuse LED array fill. Lock lens/zoom, focus, exposure, and white balance; keep assembled media flat and references visible. The handheld repository sample is a parsing-development example, not the expected production image quality.
- **Interaction:** an eventual all-in-one desktop GUI composes capture, region proposal/editing/approval, and export. Each phase also remains independently callable and testable through its own API and command. Run blocking work outside the GUI event loop.
- **Cutter handoff:** Cricut Design Space imports the SVG. Its [documented cutting workflow](https://help.cricut.com/hc/en-us/articles/26751753338007-Make-screen-Design-Space-Guide) includes material/tool selection, mat loading, and pressing Go. Direct cutter control remains a separate feasibility question.

## Three independent phases

| Phase | Input → output | Responsibility |
| --- | --- | --- |
| Capture (implemented) | Camera request → original `image.jpg` + `capture.json` | Trigger, transfer, decode, and record provenance/timings. No parsing or cutter dependency. |
| Parse (planned) | Any existing image + optional calibration → versioned `regions.json` + review overlay | Propose/edit/approve regions in explicit image coordinates; apply lens/perspective correction and map to millimeters when calibrated. Camera access is unnecessary. |
| Export (planned) | Reviewed geometry in millimeters → `cuts.svg` + placement instructions | Validate physical geometry and emit Cricut-compatible cut paths. Photo-derived regions require a validated physical transform; synthetic fixtures can supply millimeter geometry directly. |

The planned region artifact must include its schema version, source-image identity, coordinate units/transform, calibration state, geometry, offsets, and approval state. Uncalibrated image coordinates support parsing/review but cannot be exported as a physical cut job. Invalidate approval after source/geometry changes. The GUI calls phase APIs instead of duplicating their logic or embedding it in button handlers.

Start parsing with simple label/panel proposals plus click/box selection, editable polygons, merge/split, rejection, and undo. Default to outer boundaries; inner cuts are explicit choices. Evaluate prompted segmentation when representative examples justify it. Smaller processing previews must retain transforms to full-resolution and mat coordinates.

## Capture implementation

`capture.capture_image()` is the reusable entry point; `list_cameras()` and `camera_settings()` provide diagnostics. Native gphoto2 work runs in a subprocess with a whole-operation deadline. A shared process lock serializes this application's camera use across output folders; other camera applications must be closed. Failed or uncertain operations never trigger an automatic second exposure.

Each job preserves the camera's JPEG/MPO bytes. Pillow fully decodes every embedded frame; frame zero is the primary image. A black image is valid. `capture.json` is atomically published only after validation and worker completion; failures have `failure.json` instead. Consumers use completed manifests, not merely the presence of a JPEG. Metadata includes camera/settings, UTC capture-request time, remote filename, primary dimensions/orientation, frame count, byte count, SHA-256, software versions, warnings, and call timings. On Sony, the capture call may already perform transfer, so the file-get timing alone is not USB transfer latency.

Optional shutter/ISO/white-balance overrides and manual focus validate camera writability/choices before mutation. Restore prior settings on a fresh camera connection because the driver can retain stale properties after capture. Exposure changes can take seconds each; production should reuse a fixed profile. A restoration failure preserves a valid image and reports warnings. A forced deadline can prevent restoration; check camera state before retrying. Aperture control is advertised but failed confirmation in hardware testing, so leave aperture and the read-only mode dial under manual control.

## Coordinates and physical registration

The camera-independent `calibration` module implements planar fitting, saved profiles, bidirectional coordinate mapping, image-context matching, and independent error checks. Its [profile/API contract](calibration.md) is shared by parsing and export. Synthetic tests establish the software behavior; production lens correction and physical mat/cutter accuracy await the fixed mount and LED array.

Use millimeters with the origin at a measured point on the usable adhesive grid, X right and Y toward the trailing edge in the selected loading orientation. Distinguish the grid from the outer plastic boundary. Use asymmetric references, estimate mat pose per capture, and measure error on independent points beyond those used to fit the transform.

Planar perspective correction does not remove lens distortion or compensate for curled/raised media. Keep references near the artwork plane and validate the locked lens/JPEG settings using [camera calibration and undistortion](https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html). Known planar references can establish scale even in a handheld image; neither handheld nor mounted capture establishes blade placement by itself.

Physical registration must be demonstrated through the actual SVG import and mat-loading process. [Attach](https://help.cricut.com/hc/en-us/articles/360009380514-How-to-use-Attach-in-Design-Space) preserves relative shape arrangement; verify imported size and absolute group placement separately. Do not assume SVG whitespace or Canvas coordinates preserve a physical origin. [Prepare-screen placement resets on return to Canvas](https://help.cricut.com/hc/en-us/articles/26762475430167-Prepare-screen-Design-Space-Guide). Keep the sheet attached to the same mat from capture through cutting.

Choose one adhesive/media/liner stack and kiss-cut versus through-cut behavior before physical validation. An outward offset includes existing surrounding paper; it cannot create a white border absent from the source. Check offsets against neighboring artwork, other cuts, sheet and mat bounds, narrow features, self-intersections, and duplicate paths.

## Job records

Keep phase artifacts under ignored `data/` and `output/` directories. Retain calibration, reviewed paths, material stack, placement procedure, and tool/cut settings as later phases are implemented. Reference graphics remain separate from production cuts. Preserve artifacts for reproducibility; context documents describe the current system.
