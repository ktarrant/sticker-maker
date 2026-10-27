# Sticker Maker

A local desktop application for capturing Sony Alpha 6000 photographs over USB with Python gphoto2/libgphoto2, reviewing sticker outlines, and exporting approved SVG cut paths for Cricut Design Space. Capture, parsing/editing, and export are also independently callable.

## Install on a Mac

Supported setup: **Intel macOS 13+** (including Ventura 13.7.5) or **Apple silicon macOS 14+**, Git, and [uv](https://docs.astral.sh/uv/getting-started/installation/). The installer manages Python 3.12 and the architecture-specific locked dependencies; no separate Homebrew camera SDK is required.

```sh
git clone https://github.com/ktarrant/sticker-maker.git
cd sticker-maker
./scripts/setup-macos.sh
./scripts/sticker-maker review output/examples/labels.png
```

Initial setup needs internet for packages, pinned SAM source, and checksum-verified official SAM 2.1 Small weights. Normal use is then offline. Intel uses a tested legacy Torch **CPU** configuration; Apple silicon uses Metal when available. The installer handles their different build requirements. Use `setup-macos.sh --without-ai` for capture, OpenCV, and manual editing without Torch/weights.

The generated sheet is a software example, not a calibration target. Raw photos, model weights, jobs, and virtual environments stay in ignored `data/`, `output/`, and `.venv/`; these are not downloaded by Git. Generate the example again with `uv run --no-sync python scripts/create-example.py` if needed. Run the setup script after cloning or moving a checkout rather than copying its virtual environment.

The launcher uses the installed environment without syncing extras and anchors data/model paths to the checkout. Check readiness without accessing the camera:

```sh
./scripts/sticker-maker doctor --require-ai --output output/setup/doctor.json
```

For the mounted camera and another MacBook, follow the [hardware-validation handoff](docs/hardware-validation.md).

## Capture

```sh
./scripts/sticker-maker cameras
./scripts/sticker-maker camera-settings
./scripts/sticker-maker capture --manual-focus
```

On the A6000 choose **USB Connection: PC Remote**, JPEG **Fine/Standard**, and a suitable image size. Set the physical mode dial to **M**, choose a short shutter speed, and focus at the actual working distance. Close other camera applications. `--manual-focus` temporarily disables autofocus and restores its prior mode; it retains the existing focus distance. Aperture and the mode dial remain manual.

Capture saves original JPEG/MPO bytes plus a completed `capture.json` in a unique directory under `data/captures/`. JSON output reports paths, timings, and warnings. Optional temporary shutter/ISO/white-balance overrides use choices from `camera-settings`:

```sh
./scripts/sticker-maker capture --manual-focus --set shutterspeed=1/125 --set iso=400
```

Overrides restore afterward; check settings after a timeout/restoration warning. Fixed exposure settings avoid override overhead. For an explicit validation batch:

```sh
./scripts/sticker-maker validate-capture --count 3
```

This fires three shutters, records before/after settings and capture latencies, and stops on failures or restoration warnings without retrying an exposure.

## Review sticker regions

```sh
./scripts/sticker-maker parse output/examples/labels.png --work-area 40 120 1170 620 --output output/example/regions.json
./scripts/sticker-maker review output/example/regions.json
```

Or open any original image directly in `review`. Draw a **Work area**, then **Propose regions**. Amber outlines are suggestions; **Use selected** includes them. Cyan shows base outlines and green shows derived cut paths. Scroll to zoom; choose Pan or Fit; Shift/Cmd selects multiple regions.

Manual tools include rectangles, rounded rectangles, polygons (Enter/double-click finishes), freehand, vertex editing, brushes, and splitting. **Group** retains separate stickers; **Union** combines overlaps; **One backing** encloses disconnected artwork in a rectangle. Set borders, corners, simplification, and inner cuts in the inspector. Undo/redo and autosave retain edits under `output/jobs/`; reopen `regions.json` to resume.

**AI box** proposes a new selection; **AI include/exclude** adds corrective clicks. Results remain alternatives until **Use selected / accept refinement** replaces the prior selection, retaining its recipe. Manual editing remains available during analysis; Cancel analysis stops recognition. The default Small weights are detected automatically. **AI model** selects verified official Small/Tiny weights; custom/unverified checkpoints are rejected. Fetch Tiny if wanted with `models fetch --model tiny`.

Without calibration use **px** to preview borders. Millimeter values remain pending until a compatible profile is loaded. **Mark reviewed** records content review; physical export approval additionally needs a source-bound measured calibration with passing independent checks. The GUI Capture button uses the same gphoto2 service, downloads, then proposes regions; keep the camera connected until transfer finishes.

Standalone commands include `regions inspect`, `regions preview --output review.png`, `regions edit --command JSON`, and `sam refine/benchmark`; use their `--help` for arguments. See the [parser contract](docs/parser.md) for APIs and measurements.

## Move or share a review job

Create a folder containing its original image, verified mask assets, edited geometry, and undo history:

```sh
./scripts/sticker-maker regions bundle output/example/regions.json --output-dir output/transfer-job
```

Copy the entire folder, then open its `regions.json` on the other Mac. Content review is retained; physical approval is cleared for review on the copy. New jobs store image paths relative to their JSON file. For a legacy absolute path, use `review JOB.json --image /new/path/image.jpg` or `regions relocate JOB.json --image /new/path/image.jpg`; the image bytes must match exactly. The GUI also asks for a missing original. Saved geometry reopens without rerunning recognition.

## Calibration and SVG export

Exercise the camera-independent calibration API with the synthetic fixture:

```sh
./scripts/sticker-maker calibration fit tests/fixtures/calibration-references.json --output output/calibration/synthetic.json
./scripts/sticker-maker calibration map output/calibration/synthetic.json --points '[[3000,2000]]'
```

The result is approximately `[150,100]` mm. This tests software, not the physical rig. See the [calibration contract](docs/calibration.md) for measured references. For a real image, load its measured profile, mark selected regions reviewed, then approve/export:

```sh
./scripts/sticker-maker regions calibrate output/example/regions.json output/calibration/measured.json
./scripts/sticker-maker regions approve output/example/regions.json
./scripts/sticker-maker export output/example/regions.json --output output/example/cuts.svg
```

The synthetic fixture cannot authorize physical cuts. Errors block approval; review warnings before acknowledging them. Edits invalidate approval. Export serializes the approved vertices and writes `cuts.placement.json`. Import as **Basic Cut**, verify dimensions, **Attach** shapes, and establish absolute mat placement using a validated loading procedure. Keep the original sheet on the same mat. Direct cutter operation and physical alignment are not automated; validate with pen/scrap before blade use.

## Tests and documentation

```sh
uv run --no-sync python -m unittest discover -s tests -v
STICKER_MAKER_SAM_CHECKPOINT="$PWD/output/models/sam2.1_hiera_small.pt" uv run --no-sync python -m unittest discover -s tests -p test_review.py -v
```

Tests are camera-free; the second command enables cached-model GUI integration. [Hardware validation](docs/hardware-validation.md) covers actual captures, parser checks, recovery, and evidence. [System design](docs/system-design.md) holds technical decisions; [roadmap](docs/roadmap.md) holds progress and open work. Project license: [GPL v3](LICENSE).
