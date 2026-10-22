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

Capture works independently. Sticker-region parsing, Cricut export, and the combined GUI are planned.

Keep local photos under `data/` and generated jobs under `output/`; both directories are ignored by Git.

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

## Documentation

- [System design](docs/system-design.md): workflow, architecture, and technical decisions.
- [Roadmap](docs/roadmap.md): verified progress, next steps, and open decisions.
