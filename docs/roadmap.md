# Roadmap

## Current state

- Capture is implemented as an independent Python service and CLI, with camera detection, settings inspection, direct shutter/download, image validation, saved manifests, locking, and deadlines. Automated tests cover success and failure contracts without hardware.
- The final A6000 capture downloaded and decoded at 6000 × 4000 in 3.86 seconds, including connection and settings restoration. This is an initial measurement, not a sustained latency guarantee. Validated stack: macOS 15.6.1, Python gphoto2 2.6.4, libgphoto2 2.5.34, Pillow 13.0.0.
- A hardware test used 1/125 second, ISO 400, Daylight white balance, and manual focus, then independently confirmed restoration of all original settings. Changing/restoring exposure settings took 22.92 seconds; keep a fixed exposure profile in production. Aperture writes failed confirmation despite advertised writability; the physical mode dial is read-only. Keep aperture/mode setup manual.
- `data/examples/DSC06487.JPG` is the user's handheld 6000 × 4000 example for independent parsing development. Production images will use a static top-down mount and LED array fill. Use the label/icon panels as initial proposals and the headline to test grouping; this sample does not establish final rig quality.
- Parsing, Cricut export, and the combined GUI are not implemented. Segmentation quality, calibrated scale, and physical cut alignment remain unmeasured.

Capture is ready to support parsing development. Parsing is the next milestone and can use saved images without the camera or final rig calibration. See [system design](system-design.md) for phase contracts. Export can use synthetic reviewed geometry; absolute blade placement remains a physical feasibility check.

## Next milestones

| Order | Deliverable | Exit check |
| --- | --- | --- |
| 1 | Standalone parsing/review | Load the handheld example directly; propose/edit/approve regions and save geometry in explicit image coordinates without a connected camera. Accept calibration when available. |
| 2 | Standalone Cricut export | Export synthetic and calibrated reviewed region artifacts; verify SVG size, bounds, Attach, absolute placement, and material settings independently. Start physical pen/blade tests early using known geometry. |
| 3 | Combined desktop GUI | Compose the same phase APIs in one capture → review → export flow, retaining standalone commands and tests. |
| 4 | Calibrate and validate the production rig and sticker stack | Establish the final lens profile and mat mapping with the mounted camera/LED fill; validate center/edge alignment over at least 10 mat reloads, then cut one sticker and a sheet. |

Longer capture runs and real idle/wake, disconnect, and timeout recovery checks remain reliability follow-up work; they do not block parsing development. Calibration interfaces and geometry can be tested with fixtures now, while final rig accuracy waits for the mounts.

The simple registration experiment can start before the final camera mount arrives. Repeat full-system measurements after installing the rig. A stable systematic offset may be compensable; variation between loads cannot be fixed with one constant correction.

## Proposed acceptance targets

These are initial engineering goals, not measured results or manufacturer guarantees:

- Capture-button feedback under 100 ms; candidate preview within 5 seconds at the 95th percentile over 30 captures. Show the acquired photo while slower analysis continues.
- Maximum image-to-mat error of 0.25 mm on independent reference points.
- Maximum cut-to-artwork error of 1 mm across the tested reloads, starting with approximately 2 mm surrounding-paper margins. Tight edge-following cuts require a stricter demonstrated tolerance.

## Open decisions

- Confirm cutting the original adhesive-backed sheet versus reproducing artwork on printable sticker stock.
- Select the first mat size, media/adhesive/liner stack, and kiss-cut versus through-cut result.
- Agree on acceptable border widths and alignment error based on the registration experiment.
