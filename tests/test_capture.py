"""Capture contracts and recovery behavior without requiring a physical camera."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gphoto2 as gp
from PIL import Image

from sticker_maker import _capture_worker as worker
from sticker_maker.capture import CaptureError, _camera_lock, capture_image, list_cameras
from sticker_maker.cli import main


class Widget:
    def __init__(self, value, choices=(), readonly=False):
        self.value = value
        self.choices = choices
        self.readonly = readonly

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value

    def get_readonly(self):
        return self.readonly

    def get_type(self):
        return gp.GP_WIDGET_MENU

    def get_choices(self):
        return self.choices


class Camera:
    def __init__(self, content):
        self.content = content
        self.focus = "AF-A"
        self.capture_error = None
        self.restore_error = False
        self.captures = 0
        self.closed = False
        self.stale_after_capture = False
        self.reject_stale_writes = False
        self.readonly = set()
        self.settings = {
            "focusmode": "AF-A", "imagequality": "Fine", "imagesize": "Large",
            "shutterspeed": "1/1000", "iso": "2000", "f-number": "f/4",
        }
        self.choices = {
            "focusmode": ["AF-A", "Manual"], "shutterspeed": ["1/1000", "1/125"],
            "iso": ["2000", "400"], "f-number": ["f/4", "f/8"],
        }

    def wait_for_event(self, timeout):
        pass

    def get_single_config(self, name):
        if name == "focusmode":
            return Widget(self.focus, self.choices[name], name in self.readonly)
        if name not in self.settings:
            raise gp.GPhoto2Error(gp.GP_ERROR_NOT_SUPPORTED)
        return Widget(self.settings[name], self.choices.get(name, ()), name in self.readonly)

    def set_single_config(self, name, widget):
        if self.reject_stale_writes and self.stale_after_capture:
            raise gp.GPhoto2Error(gp.GP_ERROR_BAD_PARAMETERS)
        if self.restore_error and name == "focusmode" and widget.value == "AF-A":
            raise gp.GPhoto2Error(gp.GP_ERROR_IO)
        if name == "focusmode":
            self.focus = widget.value
        else:
            self.settings[name] = widget.value

    def capture(self, kind):
        self.captures += 1
        self.stale_after_capture = True
        if self.capture_error:
            raise self.capture_error
        return SimpleNamespace(folder="/capt0000", name="capt0000.jpg")

    def file_get(self, folder, name, kind):
        return SimpleNamespace(save=lambda path: Path(path).write_bytes(self.content))

    def exit(self):
        self.closed = True


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        buffer = io.BytesIO()
        Image.new("RGB", (64, 48), "black").save(buffer, "JPEG")
        self.jpeg = buffer.getvalue()
        self.camera = Camera(self.jpeg)
        self.addCleanup(patch.stopall)
        patch("sticker_maker.capture.tempfile.gettempdir", return_value=str(self.root)).start()
        def connect(port):
            self.camera.stale_after_capture = False
            return self.camera, {"model": "Sony Alpha-A6000 (Control)", "port": "usb:001,003"}
        patch.object(worker, "connect_camera", side_effect=connect).start()

    def run_service(self, *, manual_focus=False, settings=None):
        def run_backend(arguments, timeout, lock_fd):
            overrides = dict(arguments[i + 1].split("=", 1) for i, arg in enumerate(arguments) if arg == "--setting")
            return worker.capture_once(Path(arguments[1]), None, "--manual-focus" in arguments, overrides)
        with patch("sticker_maker.capture._run_worker", side_effect=run_backend):
            return capture_image(self.root / "captures", manual_focus=manual_focus, settings=settings)

    def test_black_jpeg_is_preserved_and_manifest_commits_a_complete_capture(self):
        result = self.run_service(manual_focus=True)
        self.assertEqual(result.image_path.read_bytes(), self.jpeg)
        manifest = json.loads(result.manifest_path.read_text())
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["phase"], "capture")
        self.assertEqual((manifest["image"]["width"], manifest["image"]["height"]), (64, 48))
        self.assertEqual(manifest["image"]["sha256"], hashlib.sha256(self.jpeg).hexdigest())
        self.assertEqual(manifest["camera_settings"]["focusmode"], "Manual")
        self.assertEqual(self.camera.focus, "AF-A")
        self.assertTrue(self.camera.closed)
        self.assertEqual(self.camera.captures, 1)
        self.assertFalse(list(result.image_path.parent.glob("*.part")))

    def test_repeated_captures_do_not_overwrite_previous_jobs(self):
        first = self.run_service()
        second = self.run_service()
        self.assertNotEqual(first.image_path, second.image_path)
        self.assertEqual(first.image_path.read_bytes(), self.jpeg)
        self.assertEqual(second.image_path.read_bytes(), self.jpeg)

    def test_sony_mpo_jpeg_preserves_original_and_identifies_primary_frame(self):
        buffer = io.BytesIO()
        Image.new("RGB", (64, 48), "black").save(
            buffer, "MPO", save_all=True, append_images=[Image.new("RGB", (8, 6), "white")],
        )
        self.camera.content = buffer.getvalue()
        result = self.run_service()
        self.assertEqual(result.image_path.read_bytes(), self.camera.content)
        metadata = result.metadata["image"]
        self.assertEqual(metadata["format"], "MPO")
        self.assertEqual((metadata["width"], metadata["height"]), (64, 48))
        self.assertEqual(metadata["frames"], 2)
        self.assertEqual(metadata["primary_frame"], 0)

    def test_truncated_download_is_not_published_and_focus_is_restored(self):
        self.camera.content = self.jpeg[:len(self.jpeg) // 2]
        with self.assertRaisesRegex(CaptureError, "fully decoded"):
            self.run_service(manual_focus=True)
        self.assertFalse(list(self.root.rglob("capture.json")))
        self.assertFalse(list(self.root.rglob("image.jpg")))
        self.assertFalse(list(self.root.rglob("*.part")))
        self.assertEqual(json.loads(next(self.root.rglob("failure.json")).read_text())["status"], "failed")
        self.assertEqual(self.camera.focus, "AF-A")
        self.assertTrue(self.camera.closed)

    def test_shutter_failure_is_not_retried_and_closes_camera(self):
        self.camera.capture_error = gp.GPhoto2Error(gp.GP_ERROR)
        with self.assertRaisesRegex(CaptureError, "Shutter capture failed"):
            self.run_service(manual_focus=True)
        self.assertEqual(self.camera.captures, 1)
        self.assertEqual(self.camera.focus, "AF-A")
        self.assertTrue(self.camera.closed)
        self.assertFalse(list(self.root.rglob("capture.json")))

    def test_focus_cleanup_warning_preserves_a_valid_download(self):
        self.camera.restore_error = True
        with redirect_stderr(io.StringIO()):
            result = self.run_service(manual_focus=True)
        self.assertTrue(result.image_path.exists())
        self.assertIn("Could not restore focusmode", result.metadata["warnings"][0])
        self.assertTrue(self.camera.closed)

    def test_raw_only_setting_fails_before_firing_shutter(self):
        self.camera.settings["imagequality"] = "RAW"
        with self.assertRaisesRegex(CaptureError, "requires JPEG"):
            self.run_service()
        self.assertEqual(self.camera.captures, 0)
        self.assertTrue(self.camera.closed)

    def test_remote_settings_apply_to_exposure_and_restore_afterward(self):
        overrides = {"shutterspeed": "1/125", "iso": "400"}
        before = dict(self.camera.settings)
        result = self.run_service(manual_focus=True, settings=overrides)
        for name, value in overrides.items():
            self.assertEqual(result.metadata["camera_settings"][name], value)
        self.assertEqual(self.camera.settings, before)
        self.assertEqual(self.camera.focus, "AF-A")

    def test_readonly_setting_rejects_whole_request_before_mutating_camera(self):
        self.camera.readonly.add("iso")
        with self.assertRaisesRegex(CaptureError, "read-only"):
            self.run_service(manual_focus=True, settings={"shutterspeed": "1/125", "iso": "400"})
        self.assertEqual(self.camera.settings["shutterspeed"], "1/1000")
        self.assertEqual(self.camera.focus, "AF-A")
        self.assertEqual(self.camera.captures, 0)

    def test_unavailable_value_rejects_request_without_an_exposure(self):
        with self.assertRaisesRegex(CaptureError, "Unsupported iso value"):
            self.run_service(settings={"iso": "123456"})
        self.assertEqual(self.camera.settings["iso"], "2000")
        self.assertEqual(self.camera.captures, 0)

    def test_failed_shutter_restores_all_temporary_settings(self):
        before = dict(self.camera.settings)
        self.camera.capture_error = gp.GPhoto2Error(gp.GP_ERROR)
        with self.assertRaises(CaptureError):
            self.run_service(manual_focus=True, settings={"iso": "400", "shutterspeed": "1/125"})
        self.assertEqual(self.camera.settings, before)
        self.assertEqual(self.camera.focus, "AF-A")
        self.assertTrue(self.camera.closed)

    def test_restoration_recovers_from_stale_properties_after_capture(self):
        self.camera.reject_stale_writes = True
        before = dict(self.camera.settings)
        result = self.run_service(manual_focus=True, settings={"iso": "400", "shutterspeed": "1/125"})
        self.assertEqual(self.camera.settings, before)
        self.assertEqual(self.camera.focus, "AF-A")
        self.assertEqual(result.metadata["warnings"], [])

    def test_busy_camera_lock_rejects_a_second_job(self):
        with _camera_lock():
            with self.assertRaisesRegex(CaptureError, "Another Sticker Maker"):
                capture_image(self.root / "other")
        self.assertFalse((self.root / "other").exists())

    def test_timeout_kills_worker_and_does_not_publish_success(self):
        real_run = subprocess.run
        def slow_worker(command, **kwargs):
            return real_run([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        start = time.monotonic()
        with patch("sticker_maker.capture.subprocess.run", side_effect=slow_worker):
            with self.assertRaisesRegex(CaptureError, "timed out"):
                capture_image(self.root / "captures", timeout=0.1)
        self.assertLess(time.monotonic() - start, 3)
        self.assertFalse(list(self.root.rglob("capture.json")))
        self.assertTrue(list(self.root.rglob("failure.json")))
        with _camera_lock():
            pass  # The timed-out worker did not keep ownership of the camera.

    def test_nonfinite_and_nonpositive_deadlines_are_rejected(self):
        for deadline in [0, -1, float("nan"), float("inf")]:
            with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                capture_image(self.root / "captures", timeout=deadline)
        self.assertFalse((self.root / "captures").exists())

    def test_no_camera_is_an_actionable_error_without_opening_usb(self):
        # Exercise real selection logic rather than the backend stub from setUp.
        patch.stopall()
        with patch.object(worker, "detect_cameras", return_value=[]):
            with self.assertRaisesRegex(CaptureError, "PC Remote"):
                worker.connect_camera(None)

    def test_camera_listing_only_runs_detection(self):
        with patch("sticker_maker.capture._run_worker", return_value=[]) as run:
            self.assertEqual(list_cameras(), [])
            self.assertEqual(run.call_args.args[0], ["cameras"])
        self.assertEqual(self.camera.captures, 0)

    def test_cli_returns_failure_without_printing_a_success_result(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["sticker-maker", "capture"]):
            with patch("sticker_maker.cli.capture_image", side_effect=CaptureError("camera disconnected")):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as exit_status:
                        main()
        self.assertEqual(exit_status.exception.code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("camera disconnected", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
