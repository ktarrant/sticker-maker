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
    args = parser.parse_args()
    try:
        if args.command == "cameras":
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
    except (CaptureError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("Capture interrupted. Check the camera before retrying.", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
