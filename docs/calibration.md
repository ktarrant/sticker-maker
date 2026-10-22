# Calibration contract

`sticker_maker.calibration` works on saved reference data without importing gphoto2 or accessing a camera. It fits a planar homography with normalized point coordinates and NumPy SVD. See the [synthetic input fixture](../tests/fixtures/calibration-references.json) and the [README commands](../README.md#calibration-without-a-camera).

## Reference input and profile

Input JSON uses `schema_version: 1` and these fields:

| Field | Meaning |
| --- | --- |
| `rig_id` | Identifies the camera/lens/zoom/focus/mount configuration; change it when that setup changes. |
| `image_size_px`, `exif_orientation` | Decoded primary raster dimensions and original EXIF orientation; orientation defaults to 1. Points use the unrotated raster, X right and Y down, not a resized or auto-rotated preview. |
| `mat_size_mm` | Physical mat coordinate extents, with the origin/orientation defined in [system design](system-design.md#coordinates-and-physical-registration). |
| `reference_points` | At least four distinct, non-degenerate correspondences, each with `image_px: [x,y]` and `mat_mm: [x,y]`. |
| `check_points` | Separate correspondences used only to measure error. At least three non-collinear checks are needed to pass validation. |
| `reference_kind` | `synthetic` (default) or `measured`. Mathematical test results do not become physical validation. |
| `source_image_sha256` | Optional image identity; required for measured export validation. Bind a mat-pose transform to the photograph used to fit it. |
| `max_error_mm` | Maximum allowed independent error; defaults to the project's provisional 0.25 mm target. |
| `pixel_space`, `lens_profile_id` | `raw` (default), or `undistorted` with an explicit lens-profile identifier. Lens correction must happen upstream; this module does not estimate lens parameters or undistort images yet. |

Saved profiles retain the inputs plus the 3×3 `image_to_mat` matrix, a content-derived `profile_id`, and validation metrics. Atomic writes prevent partial profiles. Loading checks the fingerprint and recomputes validation instead of trusting a stored success flag. Keep generated profiles under ignored `output/` or `data/`.

## Shared API

- `fit_calibration(reference_data)` creates a profile from the input dictionary.
- `profile.save(path)` and `load_calibration(path)` persist/reload it. `calibration_from_dict(data)` performs the same fingerprint/validation checks for profiles embedded in region documents.
- `profile.assert_compatible(...)` checks image size, orientation, rig, pixel space, lens profile, and source identity. Parsing/export consumers must call it before applying a profile to an image artifact.
- `profile.pixels_to_mm(points)` and `profile.mm_to_pixels(points)` map point arrays, including polygon vertices. They are geometry operations, not approval or bounds checks for cut jobs.
- `profile.validation` reports `not_checked`, `failed`, or `passed`, with independent RMS/maximum error in millimeters.
- `profile.require_measured_validation()` rejects synthetic, source-unbound, or insufficiently validated profiles before photo-derived export. It does not certify blade placement, material settings, or sticker approval.

The CLI exposes `calibration fit`, `inspect`, and `map`; `map --inverse` maps millimeters to pixels, while `inspect --for-export` enforces measured validation. A profile without checks is useful for development but has no measured accuracy result.

## Final rig work

Fit the mat pose per image using visible references. Supply the capture manifest's image hash/dimensions/orientation and retain the resulting profile ID in reviewed region artifacts. Keep preview-to-original transforms explicit. After installing the rig, measure lens distortion, validate independent references over the working area, and test actual cutter placement. The current fixture is a synthetic 300 × 200 mm plane, not a measured Cricut mat or a calibration of the handheld example.
