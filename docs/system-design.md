# System Design

## Goal and working assumption

Turn an overhead image of printed media placed on a Cricut mat into reviewed cut paths that line up with the physical media when the mat is loaded into the Cricut Maker 4.

Working assumption: the Maker cuts the original media on the mat. If the intended result is instead to print new sticker artwork and use Cricut Print Then Cut, that needs a different output and alignment workflow.

## Pipeline

1. **Acquire** — Capture with the Sony ILCE-6000 through the `gphoto2` Python binding. Keep a watched-folder import path available as a fallback. Preserve the original image.
2. **Calibrate** — Find the mat plane and its coordinate frame. Correct perspective, then map pixels to millimeters. Record mat orientation and the origin used by the cut file.
3. **Propose regions** — Suggest candidate sticker shapes from image boundaries, color/texture changes, and object segmentation.
4. **Review** — Show the image with editable cut boundaries. The person confirms, removes, or adjusts each region before cutting.
5. **Prepare paths** — Smooth and validate geometry, apply an optional border/offset, check size and mat limits, and retain an overlay preview.
6. **Export and cut** — Export correctly scaled SVG paths. Import them into Cricut Design Space, confirm placement/material settings, then cut a small calibration sample before a complete sheet.

## Coordinate and job data

Use one canonical coordinate frame in millimeters, tied to the mat's orientation in the camera image and the mat-loading orientation in Design Space. Each job should retain:

- Original image and capture metadata (camera, timestamp, image size, and capture settings when available).
- Mat model/size and detected boundary or fiducials.
- Pixel-to-millimeter transform and its confidence/error estimate.
- Reviewed region geometry, optional border offset, and resulting SVG.
- A preview overlay and processing parameters so a cut can be reproduced.

Generated files and images remain local under the ignored `data/` and `output/` directories; don't commit personal capture data by default.

## What the handheld sample can establish

A sample can help us develop and compare segmentation approaches, decide how region review should work, choose useful image preprocessing, and validate SVG generation at the geometry level. Perspective correction can help with a tilted image if the mat boundary/grid is visible.

It cannot establish final camera-to-cutter alignment or repeatable scale because the camera and mat position will move between handheld shots. The mounted setup will still need a fixed empty-mat calibration and a physical test cut.

## Capture guidance before the mounts arrive

- Use the original full-resolution JPEG (RAW is welcome too, but not required). Avoid screenshots or messaging-app resized copies.
- Include the complete mat, visible edges, and as much of its printed grid as possible; keep the media flat and inside the mat area.
- Use even room light, avoid flash glare, and keep hands and shadows out of the frame.
- A little tilt is fine for early pipeline work. Don't worry about making it match the final mount's orientation.
- If convenient, also take one image of the empty mat with a ruler in the mat plane. It helps check scale and perspective handling.

## Decisions still needed

- Is the Maker cutting the original magazine/media page, or is the page only a reference for reproducing artwork on sticker material?
- Should a cut follow the visible artwork edge, add a white/colored sticker border, or allow both per region?
- For the first version, should the workflow assume manual selection and approval for every candidate?
- Which mat size and media thickness will be used first?

## Before and after the camera mount arrives

**Before:** obtain representative images, build the import/review/contour/SVG path, and verify output dimensions and bounds with synthetic geometry.

**After:** fix camera/lens/zoom/focus/exposure, calibrate against the mat, measure repeatability after mat reload, test capture-to-preview latency, and validate camera-to-cut placement with a small physical test cut.
