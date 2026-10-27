# Roadmap

## Current state

- Capture is an independent gphoto2 service/CLI with settings inspection, direct shutter/download, validation, manifests, locking, deadlines, and restoration. An initial A6000 test downloaded/decoded 6000 × 4000 in 3.86 s, not a sustained guarantee. Shutter/ISO/white-balance/manual-focus overrides restored correctly. Exposure changes/restoration took 22.92 s; reuse a fixed profile. Keep aperture and the read-only mode dial manual.
- Calibration fits/saves/loads profiles, checks image/rig context and independent errors, and maps pixels ↔ millimeters without a camera. The mount and LED fill are assembled. Measured references, lens correction, and mat/cutter accuracy remain unvalidated; see the [calibration contract](calibration.md).
- Parsing/review implements saved-image OpenCV proposals, SAM 2.1 Small box/click refinement, editable outlines, group/union/backing, split/brush/vertices, borders/inner cuts, undo/redo, autosave, and source/revision checks. See the [parser contract and measurements](parser.md).
- The desktop GUI composes capture → proposals → review → approval/export using standalone APIs. Analysis is cancellable; camera transfers finish before switching jobs/closing. Real-model offscreen selection passed. Target hardware testing is on the other Intel MacBook running Ventura 13.7.5; follow the [hardware handoff](hardware-validation.md).
- SVG export serializes approved millimeter paths plus placement instructions. Measured calibration, content review, geometry validation, and current approval are required. Actual Design Space import/placement and physical cutting remain unvalidated.
- The local 6000 × 4000 development photo is handheld and excluded from Git. Initial OpenCV and Small/Tiny experiments on it show feasibility, not production image quality, physical scale, or representative recognition quality.
- Transfer readiness includes locked architecture-specific dependencies, managed Python 3.12 setup, verified model downloads, camera-free preflight, a generated software example, relative image paths, and complete review-job bundles. Intel runtime/software tests passed through Rosetta; actual Ventura/USB/lighting results require the target Mac.

## Next work

| Priority | Work | Acceptance |
| --- | --- | --- |
| 1 | Validate mounted capture/parser on the Intel MacBook | Follow the handoff: preflight, separate capture and saved-image parsing, then the GUI. Record actual image quality, settings restoration, timings, idle/wake/disconnect recovery, and preserved prior jobs. No automatic second exposures. |
| 2 | Review parser/editor on representative media | Annotate 10–20 varied sheets; compare correction effort, proposal recall, contour quality, and latency against manual tracing. Refine defaults from results. |
| 3 | Prove Cricut handoff with known geometry | Verify SVG size, Attach, absolute Prepare placement, loading orientation, and material settings using pen/scrap. Start before the final camera mount if practical. |
| 4 | Calibrate and validate the production rig/stack | Establish locked lens/JPEG correction and per-image mat mapping with mount/LED fill. Measure independent references and center/edge cut alignment across at least 10 mat reloads, then cut a sticker and a sheet. |

Interaction defaults, automatic irregular-region discovery, and natural-language edits should follow observed needs. All phases remain independently callable/testable. A stable placement offset may be compensable; variation between mat loads cannot be fixed with one correction. Keep the original sheet on the same mat through cutting. Synthetic fixtures never certify physical placement.

## Proposed acceptance targets

These are engineering goals, not measured guarantees:

- Capture feedback under 100 ms; first candidate preview within 5 s at p95 over 30 captures. Measure the full pipeline; the 3.86 s capture and separate parser timings do not establish this target.
- Review feedback/undo under 100 ms, cheap proposals within 1 s of file readiness, and warm refinement p95 within 1 s on the laptop. Initial model timings meet the refinement goal on one prompt; measure GUI latency, cold startup/crops, memory, and correction effort broadly.
- Maximum image-to-mat error of 0.25 mm on independent reference points.
- Maximum cut-to-artwork error of 1 mm over tested reloads, starting with approximately 2 mm surrounding-paper margins. Tight edge-following cuts require stricter demonstrated tolerance.

## Open decisions

- Confirm original adhesive-backed sheet versus reproduced artwork on printable stock.
- Choose mat size, media/adhesive/liner stack, and kiss-cut versus through-cut result.
- Set borders, minimum feature/clearance limits, and alignment tolerance from physical trials.
