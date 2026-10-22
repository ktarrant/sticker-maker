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
