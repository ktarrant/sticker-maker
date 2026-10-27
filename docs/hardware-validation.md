# Camera and parser hardware handoff

Target: **Intel MacBook, macOS Ventura 13.7.5**, mounted Sony A6000, and LED fill. Validate capture and parsing separately before the combined GUI. Cricut blade placement is a later physical acceptance check.

The Intel software stack has been exercised through Rosetta on the M4 development Mac, including real SAM box/foreground/background inference. Its wheel deployment tags support Ventura or older. This establishes installation/software evidence, not USB behavior, latency, or image quality on the target MacBook. Record those results below using actual mounted captures.

## 1. Install a clean checkout

Install Git and [uv](https://docs.astral.sh/uv/getting-started/installation/) if absent. Git may prompt for Apple's Command Line Tools. Then, in Terminal:

```sh
git clone https://github.com/ktarrant/sticker-maker.git
cd sticker-maker
./scripts/setup-macos.sh
```

First setup needs internet for managed Python, locked packages, the pinned SAM source, and verified model weights. It creates `.venv`, `output/models/`, a generated test sheet, and `output/setup/doctor.json`. No Homebrew camera library or separate gphoto2 executable is required: the selected Python gphoto2 wheel includes libgphoto2. Do not copy an Apple silicon `.venv` to Intel.

The Intel pins are Python 3.12, gphoto2 2.6.3/libgphoto2 2.5.33, NumPy 1.26.4, OpenCV 4.11, Qt 6.8.3, and Torch 2.2.2/torchvision 0.17.2 on **CPU**. Newer Mac Torch wheels target Apple silicon. The installer deliberately builds the pinned SAM package against the installed Intel runtime; use it instead of a bare `uv sync --extra sam`. Only checksum-verified official Small/Tiny checkpoints are accepted. See the [runtime decisions](system-design.md).

Use `./scripts/sticker-maker` thereafter; it anchors paths to the checkout and uses the prepared environment without syncing or downloading. `setup-macos.sh --without-ai` installs capture, OpenCV, and the manual GUI only. Full parser validation requires the normal setup. Subsequent normal use is offline.

## 2. Software checks before USB testing

```sh
mkdir -p output/validation
git rev-parse HEAD > output/validation/revision.txt
./scripts/sticker-maker doctor --require-ai --output output/validation/doctor.json
uv run --no-sync python -m unittest discover -s tests -v > output/validation/tests.log 2>&1
./scripts/sticker-maker parse output/examples/labels.png --work-area 40 120 1170 620 --output output/validation/software-regions.json
./scripts/sticker-maker review output/validation/software-regions.json
```

Doctor must report `ready`, `x86_64`, Python 3.12, and CPU. It imports native libraries and verifies weights without opening a camera. The tests use fakes unless explicitly running the cached-model test; they never take actual photographs.

Confirm visible suggestions around the generated four label panels; nested icon alternatives are legitimate. Try manual drawing, select/remove, borders in **px**, and undo/reopen. Then verify actual AI selection through the background worker:

```sh
STICKER_MAKER_SAM_CHECKPOINT="$PWD/output/models/sam2.1_hiera_small.pt" QT_QPA_PLATFORM=offscreen uv run --no-sync python -m unittest discover -s tests -p test_review.py -v > output/validation/ai-tests.log 2>&1
```

The model-backed test must run, not be skipped. Keep the normal GUI free of `QT_QPA_PLATFORM=offscreen`; this setting belongs only on the test command. The first model import/encoding may take seconds on Intel; do not compare it to M4 warm timings.

## 3. Camera capture by itself

1. Mount/lock the camera and lens, turn on the LED fill, flatten a representative sheet, and leave references visible for later calibration. Confirm focus on the paper at the actual working distance.
2. Connect a data-capable USB cable, turn the camera on, choose **USB Connection: PC Remote**, JPEG **Fine/Standard**, and Large image size. Use **M**, initially a short shutter such as 1/125 s, and set exposure/white balance for the lighting. Aperture/mode remain manual; reuse fixed exposure settings rather than changing them per capture.
3. Close Photos, Image Capture, Sony apps, and other camera users. If macOS requests accessory access, allow this known camera. Do not run a separate gphoto2 process alongside this app.

```sh
./scripts/sticker-maker cameras > output/validation/cameras.json
./scripts/sticker-maker camera-settings > output/validation/settings-before.json
./scripts/sticker-maker validate-capture --count 3 --output-dir output/validation/capture
```

**The last command fires three shutters.** It prints a report location and original-image/manifest paths. Each attempt has a deadline; failures, restoration warnings, or interruption stop the batch without automatically retrying an exposure. Reports preserve before/after settings and capture p95. Inspect any `failure.json`, check camera state, then reconnect/check settings before manually starting another run.

Capture passes when each requested image has a complete `capture.json`, matching bytes/hash/dimensions, no warnings, and unchanged relevant before/after settings. JPEG/MPO is expected; the original bytes are retained and every embedded frame is decoded. Frame zero is primary. A black frame passes transport checks but does not validate the mounted lighting/focus.

Open the originals and inspect the center and all corners at full resolution: sharp printed edges, complete sheet coverage, flat media, useful contrast, and no glare/clipping/banding that obscures boundaries. Adjust focus/exposure/lighting and repeat deliberately if needed. After the three-shot check, run `validate-capture --count 30` for repeatability if battery/power permits. Record latency as measured; the earlier M4 capture result is not an Intel guarantee.

## 4. Parse an actual mounted capture independently

Copy one validated `image.jpg` path from the report; substitute it below. The camera can now be disconnected. Choose a fresh output name on each new parsing run.

```sh
./scripts/sticker-maker parse /absolute/path/to/image.jpg --output output/validation/mounted-regions.json
./scripts/sticker-maker review output/validation/mounted-regions.json
```

Draw a **Work area** around the sheet and choose **Propose regions** to exclude desk/mat distractions. Review the exact green cut preview, with cyan base outlines and amber suggestions. Exercise:

- Accept a panel, remove a false suggestion, and restore it through undo.
- Draw an **AI box** for a missed region; use **AI include/exclude** clicks. Accept refinement explicitly and confirm the previous selection is replaced, not duplicated. Manual edits must remain available during analysis; cancellation must return control.
- Add a manual rectangle/polygon, move vertices, brush-add/erase, and split a region.
- Group two regions and confirm separate cuts; Union overlapping outlines; use One backing on disconnected artwork and inspect the surrounding paper it adds.
- Grow/shrink in **px**, adjust corners/simplification/inner cuts, and undo. Confirm the original outline is preserved and nearby cuts/bounds produce actionable diagnostics.
- Mark content reviewed, close/reopen the same JSON, and confirm identical outlines/settings. A saved-image job must reopen without a connected camera or another model run.

Measure missed regions, false suggestions, correction clicks/time, and edge agreement at full resolution across several actual sheets. Distinguish panel backing from an icon silhouette or grouped text. Do not judge only the model score. Millimeter borders remain pending until a compatible measured calibration is attached; this is expected. Capture/parser success does not authorize a physical cut or establish millimeter/blade accuracy.

## 5. Combined GUI and recovery

Start `./scripts/sticker-maker review` with the camera connected. Press **Capture once**. It must download through the same gphoto2 service, open the photo, and propose regions while preserving the prior job. Check view responsiveness, editing, and save/reopen. Capture/transfer finishes before switching jobs or closing; Cancel analysis applies to recognition, not an active camera transfer.

After ordinary captures pass, test camera off/disconnected **before** a capture request and confirm a bounded, actionable failure with no second exposure. Reconnect, inspect settings, and initiate a new capture manually. Also check an idle/wake cycle. Do not intentionally interrupt a USB write merely to test recovery; a real interruption/timeout is sufficient evidence when it occurs.

## Return evidence and update context

Keep results under ignored `output/validation/`: revision, doctor, test logs, capture reports/manifests, representative originals, edited jobs/overlays, correction counts, and timing notes. Record Mac model/RAM, camera/lens settings, mount/lighting, sheet/material, pass/fail per phase, and exact reproduction steps for any failure. Capture p95 and time-to-first-preview are separate measurements; the latter needs an actual GUI observation.

To share a review job, including its original image and all verified mask assets/history:

```sh
./scripts/sticker-maker regions bundle output/validation/mounted-regions.json --output-dir output/validation/transfer-job
```

Copy that entire folder by AirDrop/zip and open its `regions.json` on the other machine. Bundling retains content review and requires fresh physical approval. For an older job whose absolute image path broke, use `review JOB.json --image /new/path/image.jpg` or `regions relocate JOB.json --image /new/path/image.jpg`; only identical image bytes are accepted. Git excludes raw images, generated jobs, weights, and virtual environments. The handheld development example/demo is not in a fresh clone; the generated sheet and newly captured originals replace that dependency.

Update [roadmap](roadmap.md) with verified hardware results and [system design](system-design.md) only if decisions change. Keep detailed run evidence in job data, not session-log documents. Do not mark the Intel hardware validated based only on Rosetta/software tests. Lens correction, measured mat calibration, Design Space placement, and blade trials remain the next physical checks.
