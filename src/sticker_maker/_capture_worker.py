"""Isolated gphoto2 worker so native camera calls cannot hang the calling UI."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import gphoto2 as gp
from PIL import Image

from sticker_maker.capture import CAPTURE_SETTINGS, CaptureError


def detect_cameras() -> list[dict]:
    detected = gp.Camera.autodetect()
    return [{"model": detected[i][0], "port": detected[i][1]} for i in range(len(detected))]


def connect_camera(port: str | None):
    devices = detect_cameras()
    if port:
        devices = [device for device in devices if device["port"] == port]
    else:
        devices = [device for device in devices if "A6000" in device["model"] or "ILCE-6000" in device["model"]]
    if not devices:
        raise CaptureError(
            "No matching A6000 camera found. Turn it on, select USB Connection: PC Remote, "
            "check the USB cable, and close other camera applications."
        )
    if len(devices) != 1:
        raise CaptureError("Multiple cameras found; use --port from the cameras command.")
    device = devices[0]
    camera = gp.Camera()
    abilities = gp.CameraAbilitiesList()
    abilities.load()
    camera.set_abilities(abilities[abilities.lookup_model(device["model"])])
    ports = gp.PortInfoList()
    ports.load()
    camera.set_port_info(ports[ports.lookup_path(device["port"])])
    try:
        camera.init()
    except gp.GPhoto2Error as exc:
        raise CaptureError(f"Cannot open the camera; close other camera applications. {exc}") from exc
    return camera, device


SETTING_NAMES = (
    "expprogram", "shutterspeed", "iso", "f-number", "focusmode",
    "whitebalance", "imagequality", "imagesize",
)


def read_settings(camera) -> dict:
    result = {}
    for name in SETTING_NAMES:
        try:
            widget = camera.get_single_config(name)
            result[name] = {
                "value": widget.get_value(), "writable": not bool(widget.get_readonly()),
                "choices": list(widget.get_choices()) if widget.get_type() in (gp.GP_WIDGET_MENU, gp.GP_WIDGET_RADIO) else [],
            }
        except gp.GPhoto2Error:
            pass
    return result


def set_value(camera, name: str, value: str) -> None:
    widget = camera.get_single_config(name)
    widget.set_value(value)
    camera.set_single_config(name, widget)
    # Sony can acknowledge a write before a property-change event arrives.
    deadline = time.monotonic() + 2
    while camera.get_single_config(name).get_value() != value:
        if time.monotonic() >= deadline:
            raise CaptureError(f"Camera did not confirm {name}={value}.")
        camera.wait_for_event(100)


def inspect_settings(port: str | None) -> dict:
    camera, device = connect_camera(port)
    try:
        camera.wait_for_event(1000)
        return {"camera": device, "settings": read_settings(camera)}
    finally:
        camera.exit()


def restore_and_close(camera, device: dict, restore_values: list[tuple[str, str]]) -> list[str]:
    warnings = []
    try:
        camera.exit()
    except gp.GPhoto2Error as exc:
        warnings.append(f"Camera cleanup failed: {exc}; reconnect before another capture.")
    if not restore_values:
        return warnings
    # Reopen before restoring. The A6000 driver can retain stale property data
    # after capture, causing otherwise valid ISO/shutter restoration to fail.
    restore_camera = None
    try:
        restore_camera, _ = connect_camera(device["port"])
        restore_camera.wait_for_event(1000)
        for name, value in reversed(restore_values):
            try:
                set_value(restore_camera, name, value)
            except (gp.GPhoto2Error, CaptureError) as exc:
                warnings.append(f"Could not restore {name} to {value}: {exc}")
    except (gp.GPhoto2Error, CaptureError) as exc:
        warnings.append(f"Could not reopen camera to restore {dict(restore_values)}: {exc}")
    finally:
        if restore_camera is not None:
            try:
                restore_camera.exit()
            except gp.GPhoto2Error as exc:
                warnings.append(f"Camera cleanup failed after restoration: {exc}")
    return warnings


def capture_once(
    job_dir: Path, port: str | None, manual_focus: bool,
    overrides: dict[str, str] | None = None,
) -> dict:
    started = time.monotonic()
    camera, device = connect_camera(port)
    timings = {}
    warnings = []
    restore_values = []
    partial = job_dir / "image.jpg.part"
    try:
        # Sony properties initially contain placeholders. Let the driver receive
        # the first property events before reading settings or overriding focus.
        camera.wait_for_event(1000)
        available = read_settings(camera)
        settings = {name: control["value"] for name, control in available.items()}
        if settings.get("imagequality") == "RAW":
            raise CaptureError("This capture phase requires JPEG. Set camera image quality to Fine or Standard.")
        requested = dict(overrides or {})
        if requested.keys() - CAPTURE_SETTINGS:
            raise CaptureError("Unsupported capture setting requested.")
        if manual_focus:
            requested["focusmode"] = "Manual"
        # Check the entire request before changing anything or firing a shutter.
        for name, value in requested.items():
            control = available.get(name)
            if not control or control["value"] == "Undefined" or not control["writable"]:
                raise CaptureError(f"{name} is unavailable or read-only; check the physical mode dial and camera settings.")
            if control["choices"] and value not in control["choices"]:
                raise CaptureError(f"Unsupported {name} value {value!r}; use camera-settings to list choices.")
        for name, value in requested.items():
            if settings[name] != value:
                # A failed USB write may still have reached the camera.
                restore_values.append((name, settings[name]))
                set_value(camera, name, value)
            settings[name] = value
        timings["connect"] = time.monotonic() - started
        captured_at = datetime.now(timezone.utc).isoformat()
        before_capture = time.monotonic()
        try:
            remote = camera.capture(gp.GP_CAPTURE_IMAGE)
        except gp.GPhoto2Error as exc:
            raise CaptureError(
                f"Shutter capture failed: {exc}. With a lens cap or low contrast, "
                "use --manual-focus. For short test exposures, set the camera dial to M "
                "and choose a short shutter speed. No automatic retry was made."
            ) from exc
        after_capture = time.monotonic()
        timings["capture"] = after_capture - before_capture
        if Path(remote.name).suffix.lower() not in {".jpg", ".jpeg"}:
            raise CaptureError(f"Camera returned {remote.name}; select JPEG/Fine image quality and retry.")
        camera.file_get(remote.folder, remote.name, gp.GP_FILE_TYPE_NORMAL).save(str(partial))
        downloaded = time.monotonic()
        timings["download"] = downloaded - after_capture
        # Decode every pixel. A valid header alone cannot detect a truncated
        # transfer. Black frames are intentionally valid capture test images.
        try:
            with Image.open(partial) as image:
                image_format = image.format
                if image_format not in {"JPEG", "MPO"}:
                    raise CaptureError(f"Downloaded file is {image_format}, not JPEG/MPO.")
                width, height = image.size
                exif = image.getexif()
                orientation = exif.get(274, 1)
                exposure_exif = exif.get_ifd(34665)
                exposure = {
                    name: str(exposure_exif[tag])
                    for name, tag in [("exposure_time", 33434), ("f_number", 33437), ("iso", 34855)]
                    if tag in exposure_exif
                }
                frames = getattr(image, "n_frames", 1)
                for frame in range(frames):
                    image.seek(frame)
                    image.load()
        except (OSError, ValueError) as exc:
            raise CaptureError(f"Downloaded image could not be fully decoded: {exc}") from exc
        with partial.open("rb") as source:
            sha256 = hashlib.file_digest(source, "sha256").hexdigest()
        image_metadata = {
            "file": "image.jpg", "format": image_format, "width": width, "height": height,
            "frames": frames, "primary_frame": 0,
            "exposure_exif": exposure,
            "exif_orientation": orientation, "bytes": partial.stat().st_size, "sha256": sha256,
        }
        partial.replace(job_dir / "image.jpg")
        timings["validate"] = time.monotonic() - downloaded
    finally:
        before_cleanup = time.monotonic()
        warnings.extend(restore_and_close(camera, device, restore_values))
        timings["cleanup"] = time.monotonic() - before_cleanup
        partial.unlink(missing_ok=True)
        for warning in warnings:
            print(warning, file=sys.stderr)
    return {
        "schema_version": 1, "phase": "capture", "status": "complete",
        "captured_at": captured_at, "camera": device, "camera_settings": settings,
        "manual_focus_override": manual_focus,
        "settings_before_override": dict(restore_values),
        "remote_file": {"folder": remote.folder, "name": remote.name},
        "image": image_metadata,
        "software": {"gphoto2": gp.__version__, "libgphoto2": gp.gp_library_version(gp.GP_VERSION_SHORT)[0]},
        "timings_seconds": {key: round(value, 6) for key, value in timings.items()},
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["cameras", "settings", "capture"])
    parser.add_argument("job_dir", nargs="?", type=Path)
    parser.add_argument("--port")
    parser.add_argument("--manual-focus", action="store_true")
    parser.add_argument("--setting", action="append", default=[])
    args = parser.parse_args()
    try:
        if args.operation == "cameras":
            result = detect_cameras()
        elif args.operation == "settings":
            result = inspect_settings(args.port)
        else:
            overrides = dict(setting.split("=", 1) for setting in args.setting)
            result = capture_once(args.job_dir, args.port, args.manual_focus, overrides)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
