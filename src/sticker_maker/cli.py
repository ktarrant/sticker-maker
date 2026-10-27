"""Command line entry point for Sticker Maker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sticker_maker import __version__
from sticker_maker.capture import CaptureError, camera_settings, capture_image, list_cameras


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sticker-maker",
        description="Turn overhead photos into Cricut-ready sticker cut paths.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    preflight = commands.add_parser("doctor", help="Check local runtime/dependencies without accessing a camera.")
    preflight.add_argument("--require-ai", action="store_true")
    preflight.add_argument("--output", type=Path)
    preflight.add_argument("--models-dir", type=Path, default=Path("output/models"))
    models = commands.add_parser("models", help="Fetch verified official SAM weights once for offline use.")
    model_actions = models.add_subparsers(dest="models_command", required=True)
    fetch = model_actions.add_parser("fetch")
    fetch.add_argument("--model", choices=("small", "tiny"), default="small")
    fetch.add_argument("--directory", type=Path, default=Path("output/models"))
    validation = commands.add_parser("validate-capture", help="Take an explicit validation batch; stop on failures/warnings, never retry.")
    validation.add_argument("--count", type=int, default=3)
    validation.add_argument("--timeout", type=float, default=60)
    validation.add_argument("--port")
    validation.add_argument("--output-dir", type=Path, default=Path("output/validation/capture"))
    commands.add_parser("cameras", help="List connected gphoto2 cameras without taking a photo.")
    settings = commands.add_parser("camera-settings", help="Read available camera controls and their choices.")
    settings.add_argument("--port")
    capture = commands.add_parser("capture", help="Capture and directly download one JPEG over USB.")
    capture.add_argument("--output-dir", type=Path, default=Path("data/captures"))
    capture.add_argument("--port", help="Camera USB port reported by the cameras command.")
    capture.add_argument("--timeout", type=float, default=60, help="Whole capture deadline in seconds (default: 60).")
    capture.add_argument("--manual-focus", action="store_true", help="Temporarily use manual focus, then restore the previous mode.")
    capture.add_argument("--set", dest="settings", action="append", default=[], metavar="NAME=VALUE", help="Temporary setting (repeatable): shutterspeed, iso, whitebalance.")
    calibration = commands.add_parser("calibration", help="Fit, inspect, or apply saved calibration without a camera.")
    calibration_commands = calibration.add_subparsers(dest="calibration_command", required=True)
    fit = calibration_commands.add_parser("fit", help="Fit pixel-to-mm mapping from reference-point JSON.")
    fit.add_argument("references", type=Path)
    fit.add_argument("--output", type=Path, required=True)
    inspect = calibration_commands.add_parser("inspect", help="Inspect profile and independent validation errors.")
    inspect.add_argument("profile", type=Path)
    inspect.add_argument("--for-export", action="store_true", help="Require a source-bound measured profile with passing checks.")
    mapping = calibration_commands.add_parser("map", help="Map JSON point pairs through a saved profile.")
    mapping.add_argument("profile", type=Path)
    mapping.add_argument("--points", required=True, help="JSON array, for example '[[3000,2000]]'.")
    mapping.add_argument("--inverse", action="store_true", help="Map millimeters back to image pixels.")
    parsing = commands.add_parser("parse", help="Propose sticker panels from a saved image; no camera required.")
    parsing.add_argument("image", type=Path)
    parsing.add_argument("--output", type=Path, required=True, help="New region document JSON path.")
    parsing.add_argument("--work-area", type=float, nargs=4, metavar=("X1", "Y1", "X2", "Y2"), help="Restrict proposals to this rectangle in original image pixels.")
    regions = commands.add_parser("regions", help="Inspect, edit, preview, or approve a saved review document.")
    edits = regions.add_subparsers(dest="regions_command", required=True)
    for name in ("inspect", "edit", "preview", "calibrate", "approve", "relocate", "bundle"):
        child = edits.add_parser(name)
        child.add_argument("document", type=Path)
        child.add_argument("--image", type=Path, help="Locate the identical original image after moving a job.")
        if name == "edit":
            source = child.add_mutually_exclusive_group(required=True)
            source.add_argument("--command", dest="edit_json", help="One JSON edit command or an array of commands.")
            source.add_argument("--commands-file", type=Path, help="JSON command/array file.")
            child.add_argument("--expected-revision", type=int)
        elif name == "preview":
            child.add_argument("--output", type=Path, required=True)
        elif name == "calibrate":
            child.add_argument("profile", type=Path)
        elif name == "approve":
            child.add_argument("--acknowledge-warnings", action="store_true")
        elif name == "bundle":
            child.add_argument("--output-dir", type=Path, required=True)
    review = commands.add_parser("review", help="Open the desktop region editor for a saved image or regions JSON.")
    review.add_argument("input", type=Path, nargs="?")
    review.add_argument("--output", type=Path, help="Autosave path for a new image review job.")
    review.add_argument("--image", type=Path, help="Locate the identical original for an existing job.")
    sam = commands.add_parser("sam", help="Optional interactive segmentation and local model benchmarking.")
    model_commands = sam.add_subparsers(dest="sam_command", required=True)
    refine = model_commands.add_parser("refine", help="Save an AI proposal without overwriting reviewed outlines.")
    refine.add_argument("document", type=Path)
    refine.add_argument("--checkpoint", type=Path, required=True)
    refine.add_argument("--model", choices=("small", "tiny"), default="small")
    refine.add_argument("--device", choices=("auto", "mps", "cpu"), default="auto")
    refine.add_argument("--box", type=float, nargs=4)
    refine.add_argument("--points", default="[]", help="JSON [[x,y], ...] in original image pixels.")
    refine.add_argument("--labels", default="[]", help="JSON [1,0,...], foreground=1/background=0.")
    benchmark = model_commands.add_parser("benchmark", help="Benchmark Small/Tiny in separate processes.")
    benchmark.add_argument("image", type=Path)
    benchmark.add_argument("--checkpoints", type=Path, default=Path("output/models"))
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--box", type=float, nargs=4, required=True)
    benchmark.add_argument("--device", choices=("auto", "mps", "cpu"), default="auto")
    benchmark.add_argument("--trials", type=int, default=10)
    exporting = commands.add_parser("export", help="Export an approved millimeter path snapshot to SVG.")
    exporting.add_argument("document", type=Path)
    exporting.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            from sticker_maker.diagnostics import doctor
            from sticker_maker.regions import atomic_json
            result = doctor(require_ai=args.require_ai, models_dir=args.models_dir)
            if args.output:
                atomic_json(args.output, result)
            print(json.dumps(result, indent=2))
            if result["status"] == "error":
                raise SystemExit(1)
        elif args.command == "models":
            from sticker_maker.models import fetch_model
            print(json.dumps(fetch_model(args.directory, args.model), indent=2))
        elif args.command == "validate-capture":
            from sticker_maker.validation import validate_capture
            result = validate_capture(args.output_dir, count=args.count, timeout=args.timeout, port=args.port)
            print(json.dumps(result, indent=2))
            if result["status"] != "passed":
                raise SystemExit(1)
        elif args.command == "cameras":
            print(json.dumps(list_cameras(), indent=2))
        elif args.command == "camera-settings":
            print(json.dumps(camera_settings(port=args.port), indent=2))
        elif args.command == "capture":
            if any("=" not in setting for setting in args.settings):
                raise ValueError("Use --set NAME=VALUE; camera-settings lists accepted values.")
            result = capture_image(
                args.output_dir, port=args.port, timeout=args.timeout, manual_focus=args.manual_focus,
                settings=dict(setting.split("=", 1) for setting in args.settings),
            )
            print(json.dumps({
                "image": str(result.image_path), "manifest": str(result.manifest_path),
                "timings_seconds": result.metadata["timings_seconds"],
                "warnings": result.metadata["warnings"],
            }, indent=2))
        elif args.command == "parse":
            from shapely.geometry import box
            from sticker_maker.recognition import parse_image
            if args.output.exists():
                raise ValueError("Region document already exists; review it or choose a new --output path.")
            area = None
            if args.work_area:
                x1, y1, x2, y2 = args.work_area
                if x2 <= x1 or y2 <= y1:
                    raise ValueError("Work area requires X2 > X1 and Y2 > Y1.")
                from sticker_maker.regions import geojson
                area = geojson(box(x1, y1, x2, y2))
            print(json.dumps(parse_image(args.image, args.output, work_area=area), indent=2))
        elif args.command == "regions":
            from sticker_maker.regions import RegionDocument
            from sticker_maker.geometry import build_cut_paths
            document = RegionDocument.load(args.document, image=args.image)
            if args.regions_command == "edit":
                value = json.loads(args.commands_file.read_text() if args.commands_file else args.edit_json)
                batch = value if isinstance(value, list) else [value]
                result_ids = []
                for index, command in enumerate(batch):
                    result_ids = document.apply(command, expected_revision=args.expected_revision if index == 0 else None)
                document.save(args.document)
                result = {"revision": document.revision, "region_ids": result_ids}
            elif args.regions_command == "bundle":
                from sticker_maker.transfer import bundle_job
                result = bundle_job(args.document, args.output_dir, image=args.image)
            elif args.regions_command == "relocate":
                if args.image is None:
                    raise ValueError("Use --image to locate the identical original image.")
                document.save(args.document)
                result = {"image": document.source["path"], "revision": document.revision}
            elif args.regions_command == "calibrate":
                from sticker_maker.calibration import load_calibration
                document.apply({"op": "calibration", "profile": load_calibration(args.profile).to_dict()})
                document.save(args.document)
                result = {"revision": document.revision}
            elif args.regions_command == "approve":
                document.approve(acknowledge_warnings=args.acknowledge_warnings)
                document.save(args.document)
                result = {"approval": document.to_dict()["approval"]["fingerprint"]}
            elif args.regions_command == "preview":
                from sticker_maker.recognition import save_overlay
                save_overlay(document, args.output)
                result = {"overlay": str(args.output.resolve()), **build_cut_paths(document)}
            else:
                result = document.to_dict()
            print(json.dumps(result, indent=2))
        elif args.command == "review":
            from sticker_maker.review import run_review
            run_review(args.input, output=args.output, image=args.image)
        elif args.command == "sam":
            from sticker_maker.sam import SAMRecognizer, benchmark_models
            if args.sam_command == "refine":
                from sticker_maker.regions import RegionDocument
                document = RegionDocument.load(args.document)
                revision = document.revision
                result = SAMRecognizer(args.checkpoint, args.model, args.device).refine(
                    Path(document.source["path"]), box_px=args.box, points=json.loads(args.points),
                    labels=json.loads(args.labels), asset_dir=args.document.parent / "masks")
                latest = RegionDocument.load(args.document)
                if latest.job_id != document.job_id:
                    raise ValueError("Review job changed during recognition; rerun refinement.")
                document = latest
                document.apply({"op": "proposals", "source_sha256": result["source_sha256"],
                                "proposals": [result["proposal"]]}, expected_revision=revision)
                document.save(args.document)
                result = {"revision": document.revision, "timings": result["timings"]}
            else:
                summary = benchmark_models(args.image, args.checkpoints, args.output, device=args.device,
                                           box_px=args.box, trials=args.trials)
                result = {"benchmark": str(args.output.resolve()), "models": [
                    {key: r[key] for key in ("model", "device", "load_seconds", "warm_refine_p95_seconds", "peak_process_rss_bytes")}
                    for r in summary["results"]]}
            print(json.dumps(result, indent=2))
        elif args.command == "export":
            from sticker_maker.export import export_svg
            print(json.dumps(export_svg(args.document, args.output), indent=2))
        elif args.command == "calibration":
            # Calibration has no camera dependency; the GUI can use these same
            # pure APIs against saved images while the camera is disconnected.
            from sticker_maker.calibration import fit_calibration, load_calibration

            if args.calibration_command == "fit":
                profile = fit_calibration(json.loads(args.references.read_text()))
                profile.save(args.output)
                result = {"profile": str(args.output.resolve()), "profile_id": profile.profile_id,
                          "reference_kind": profile.reference_kind, "validation": profile.to_dict()["validation"]}
            else:
                profile = load_calibration(args.profile)
                if args.calibration_command == "inspect":
                    if args.for_export:
                        profile.require_measured_validation()
                    result = profile.to_dict()
                else:
                    points = json.loads(args.points)
                    mapped = profile.mm_to_pixels(points) if args.inverse else profile.pixels_to_mm(points)
                    result = {"profile_id": profile.profile_id, "units": "px" if args.inverse else "mm", "points": mapped}
            print(json.dumps(result, indent=2))
        else:
            parser.print_help()
    except (CaptureError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("Capture interrupted. Check the camera before retrying.", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
