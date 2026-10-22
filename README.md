# Sticker Maker

A local Python project for turning overhead photos of printed media into sticker cut paths for a Cricut Maker 4.

The intended workflow starts with a laptop Capture button: control a Sony Alpha 6000 over USB through Python `gphoto2`/libgphoto2, download the image, propose sticker regions for review, and export SVG cut paths for Cricut Design Space.

## Setup and usage

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run sticker-maker cameras
uv run sticker-maker camera-settings
uv run sticker-maker capture --manual-focus
```

Connect the A6000 over USB, turn it on, and select **USB Connection: PC Remote** and JPEG **Fine** or **Standard** image quality. For predictable test exposures, set the physical mode dial to **M** and choose a short shutter speed. Close other applications using the camera. `--manual-focus` temporarily disables autofocus and restores the previous mode afterward; it uses the lens's existing focus distance.

Each capture saves the original `image.jpg` and a `capture.json` manifest in a unique directory under `data/captures/`. The command prints JSON with the saved paths and timings. Sony JPEG/MPO images are supported without conversion. Use `capture --help` for output-directory, camera-port, and timeout options.

Optional temporary exposure settings use the values listed by `camera-settings`:

```sh
uv run sticker-maker capture --manual-focus --set shutterspeed=1/125 --set iso=400
```

Supported overrides are shutter speed, ISO, and white balance. Aperture and the physical mode dial currently require manual setup. Overrides are restored during cleanup; check settings after a forced timeout or restoration warning.

Capture, parsing/review, and SVG export work independently. An initial desktop GUI combines them; physical cut placement still needs validation with the mounted rig.

Keep local photos under `data/` and generated jobs under `output/`; both directories are ignored by Git.

## Review sticker regions

Install the desktop extra and open an existing image or saved review job; no camera is needed:

```sh
uv sync --extra gui
uv run --extra gui sticker-maker review data/examples/DSC06487.JPG
```

Draw a **Work area** around the paper, then click **Propose regions**. Amber outlines are suggestions; select one and click **Use selected** to include it. Cyan shows included outlines and green shows the resulting cut paths. Scroll to zoom, choose **Pan** to move the view, and use Shift/Cmd for multiple selections.

The tool selector offers rectangles, polygons (Enter/double-click to finish), freehand drawing, vertex editing, brushes, and splitting. **Group** retains separate stickers; **Union** combines outlines; **One backing** creates an enclosing rectangle around disconnected artwork. Adjust borders, units, corners, simplification, and inner cuts in the inspector. Undo/redo works across edits, and jobs autosave under `output/jobs/`. Reopen their `regions.json` to resume. **Save as** preserves a separate version.

Without calibration, use `px` to preview border changes. Millimeter values remain pending until a compatible calibration is loaded. **Mark reviewed** records content review; **Approve paths for export** additionally requires a source-bound measured calibration with passing checks and valid geometry.

The GUI's **Capture** button uses the same gphoto2 service and temporary manual-focus behavior as `capture --manual-focus`, then proposes regions. Keep the camera connected until transfer finishes. The camera mode/aperture setup above still applies.

For standalone parsing and previews:

```sh
uv run --extra parser sticker-maker parse data/examples/DSC06487.JPG --work-area 1900 1400 3400 3100 --output output/example/regions.json
uv run sticker-maker regions inspect output/example/regions.json
uv run sticker-maker regions preview output/example/regions.json --output output/example/review.png
```

`regions edit --help` describes scripted JSON edits. Existing jobs are preserved; choose a new `parse --output` or open the saved job instead.

## Optional AI selection

SAM 2.1 Small adds box selection and foreground/background correction clicks. Install its optional dependencies and download Meta's weights once:

```sh
SAM2_BUILD_CUDA=0 uv sync --extra gui --extra sam
mkdir -p output/models
curl -fL https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt -o output/models/sam2.1_hiera_small.pt
uv run --extra gui --extra sam sticker-maker review data/examples/DSC06487.JPG
```

The default weights path is detected automatically; **AI model** chooses another Small/Tiny checkpoint. **AI box** proposes a new region. **AI include/exclude** refines the selected region with clicks. Results are alternatives: **Use selected / accept refinement** explicitly replaces the prior selection, preserving its border settings. Ordinary use works offline after setup. Analysis can be cancelled while manual editing remains available.

The standalone `sam refine` command saves a proposal to an existing job. `sam benchmark --help` compares Small and Tiny using separately downloaded checkpoints; see the [parser contract and measurements](docs/parser.md).

## Export reviewed cuts

Load the measured profile using the GUI or `regions calibrate`, mark selected regions reviewed, then approve and export:

```sh
uv run sticker-maker regions calibrate output/example/regions.json output/calibration/measured.json
uv run sticker-maker regions approve output/example/regions.json
uv run sticker-maker export output/example/regions.json --output output/example/cuts.svg
```

`measured.json` must come from actual reference measurements for that image; the synthetic fixture below cannot authorize physical cuts. Review any geometry warnings before acknowledging them. Source, outline, recipe, or calibration changes invalidate export approval.

Export creates the SVG and `cuts.placement.json`. Import into Cricut Design Space as **Basic Cut**, verify dimensions, **Attach** the shapes, and establish the absolute mat placement using the placement report and your validated loading procedure. Keep the original sheet on the same mat. The software does not directly operate the cutter or establish blade alignment; validate with a pen/scrap first.

## Calibration without a camera

Fit and exercise a saved profile using the synthetic reference fixture:

```sh
uv run sticker-maker calibration fit tests/fixtures/calibration-references.json --output output/calibration/synthetic.json
uv run sticker-maker calibration map output/calibration/synthetic.json --points '[[3000,2000]]'
uv run sticker-maker calibration inspect output/calibration/synthetic.json
```

The example maps the image point to approximately `[150, 100]` millimeters. It tests the software; real rig calibration still needs measured references after the mounts are installed. See the [calibration contract](docs/calibration.md) for profile fields and API usage.

Run automated tests without a camera:

```sh
uv run python -m unittest discover -s tests -v
```

Install the parser/GUI extras to include their optional tests. A cached-model GUI integration test can also run without a camera:

```sh
STICKER_MAKER_SAM_CHECKPOINT="$PWD/output/models/sam2.1_hiera_small.pt" uv run --extra gui --extra sam python -m unittest discover -s tests -p test_review.py -v
```

## Documentation

- [System design](docs/system-design.md): workflow, architecture, and technical decisions.
- [Roadmap](docs/roadmap.md): verified progress, next steps, and open decisions.
