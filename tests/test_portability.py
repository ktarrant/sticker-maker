"""Transfer, verified models, and fail-stop hardware validation without a camera."""

import hashlib
import io
import json
from contextlib import ExitStack
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from shapely.geometry import box

from sticker_maker.capture import CaptureError
from sticker_maker.models import fetch_model, verify_model
from sticker_maker.regions import RegionDocument, RegionError, geojson
from sticker_maker.transfer import bundle_job
from sticker_maker.validation import validate_capture


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.image = self.root / "original.png"
        Image.new("RGB", (400, 300), "white").save(self.image)
        self.document = RegionDocument.create(self.image)
        self.path = self.root / "jobs" / "regions.json"

    def test_relative_image_paths_survive_moving_a_complete_tree(self):
        self.document.apply({"op": "add", "geometry": geojson(box(10, 10, 50, 50))})
        self.document.save(self.path)
        self.assertFalse(Path(json.loads(self.path.read_text())["source"]["path"]).is_absolute())
        moved = self.root.parent / (self.root.name + "-moved")
        shutil.copytree(self.root, moved)
        self.addCleanup(shutil.rmtree, moved)
        self.image.unlink()
        loaded = RegionDocument.load(moved / "jobs" / "regions.json")
        self.assertEqual(loaded.source["sha256"], self.document.source["sha256"])
        self.assertEqual(loaded.fingerprint(), self.document.fingerprint())
        self.assertEqual(loaded.source["path"], str(moved / "original.png"))

    def test_bundle_copies_verified_masks_and_history_into_a_relocatable_job(self):
        mask = self.root / "mask.png"
        Image.new("L", (40, 40), 255).save(mask)
        sha = hashlib.sha256(mask.read_bytes()).hexdigest()
        ids = self.document.apply({"op": "proposals", "source_sha256": self.document.source["sha256"],
                                   "proposals": [{"geometry": geojson(box(10, 10, 50, 50)),
                                                  "origin": {"provider": "sam2", "mask_asset": str(mask), "mask_sha256": sha}}]})
        self.document.apply({"op": "review", "ids": ids})
        self.document.save(self.path)
        old_state = self.path.read_bytes()
        bundle = self.root / "bundle"
        result = bundle_job(self.path, bundle)
        self.assertEqual(result["mask_count"], 1)
        self.assertEqual(self.path.read_bytes(), old_state)
        self.assertIsNone(RegionDocument.load(bundle / "regions.json").to_dict()["approval"])
        moved = self.root / "another laptop"
        bundle.rename(moved)
        self.image.unlink()
        mask.unlink()
        loaded = RegionDocument.load(moved / "regions.json")
        self.assertEqual(loaded.source["sha256"], self.document.source["sha256"])
        self.assertTrue(loaded.state["regions"][0]["reviewed"])
        self.assertEqual(loaded.state["regions"][0]["origin"]["mask_asset"], f"masks/{sha}.png")
        loaded.undo()
        loaded.redo()
        loaded.save(moved / "regions.json")
        bundle_job(moved / "regions.json", self.root / "copied-again")

    def test_missing_or_corrupt_bundle_assets_leave_no_partial_destination(self):
        self.document.apply({"op": "proposals", "source_sha256": self.document.source["sha256"],
                             "proposals": [{"geometry": geojson(box(10, 10, 50, 50)),
                                            "origin": {"provider": "sam2", "mask_asset": str(self.image), "mask_sha256": "0"*64}}]})
        self.document.save(self.path)
        with self.assertRaisesRegex(RegionError, "checksum"):
            bundle_job(self.path, self.root / "bundle")
        self.assertFalse((self.root / "bundle").exists())
        self.assertFalse(list(self.root.glob(".sticker-bundle-*")))

    def test_image_relocation_requires_identical_bytes(self):
        self.document.save(self.path)
        copied = self.root / "moved.png"
        shutil.copyfile(self.image, copied)
        self.image.unlink()
        loaded = RegionDocument.load(self.path, image=copied)
        loaded.save(self.path)
        self.assertEqual(RegionDocument.load(self.path).source["path"], str(copied))
        Image.new("RGB", (400, 300), "black").save(copied)
        with self.assertRaises(RegionError):
            RegionDocument.load(self.path, image=copied)

    def test_model_fetch_is_atomic_verified_and_reuses_correct_offline_file(self):
        data = b"test checkpoint bytes"
        registry = {"small": {"filename": "small.pt", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
        with patch("sticker_maker.models.MODELS", registry), patch("sticker_maker.models.urllib.request.urlopen", return_value=io.BytesIO(data)) as request:
            result = fetch_model(self.root / "models")
            self.assertEqual(Path(result["path"]).read_bytes(), data)
            fetch_model(self.root / "models")
            request.assert_called_once()
            Path(result["path"]).write_bytes(b"x" * len(data))
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                verify_model(Path(result["path"]), "small")

    def test_corrupt_model_download_is_not_published(self):
        registry = {"small": {"filename": "small.pt", "size": 3, "sha256": "0"*64}}
        with patch("sticker_maker.models.MODELS", registry), patch("sticker_maker.models.urllib.request.urlopen", return_value=io.BytesIO(b"bad")):
            with self.assertRaises(ValueError):
                fetch_model(self.root / "models")
        self.assertFalse((self.root / "models" / "small.pt").exists())
        self.assertFalse(list((self.root / "models").glob("*.download")))

    def test_capture_validation_stops_after_one_uncertain_exposure(self):
        settings = {"settings": {"focusmode": {"value": "Manual"}, "iso": {"value": "400"}}}
        with patch("sticker_maker.validation.camera_settings", return_value=settings), patch("sticker_maker.validation.capture_image", side_effect=CaptureError("uncertain shutter")) as capture:
            report = validate_capture(self.root / "validation", count=30)
        capture.assert_called_once()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["completed"], 0)
        self.assertIn("uncertain shutter", json.loads(Path(report["report"]).read_text())["error"])

    def test_capture_validation_compares_settings_and_stops_on_restoration_warning(self):
        before = {"settings": {"focusmode": {"value": "AF-A"}}}
        after = {"settings": {"focusmode": {"value": "Manual"}}}
        captured = SimpleNamespace(image_path=self.image, manifest_path=self.root/"capture.json",
            metadata={"timings_seconds": {"total": 4.0}, "warnings": ["focus restoration uncertain"]})
        with patch("sticker_maker.validation.camera_settings", side_effect=[before, after]), patch("sticker_maker.validation.capture_image", return_value=captured) as capture:
            report = validate_capture(self.root / "validation", count=30)
        capture.assert_called_once()
        self.assertEqual(report["status"], "needs_review")
        self.assertIn("focusmode", report["settings_changed"])

    def test_capture_validation_rejects_invalid_requests_before_camera_access(self):
        with patch("sticker_maker.validation.camera_settings") as inspect:
            for arguments in [{"count": 0}, {"timeout": float("nan")}]:
                with self.assertRaises(ValueError):
                    validate_capture(self.root, **arguments)
        inspect.assert_not_called()

    def test_doctor_never_opens_camera_and_requires_the_requested_ai_runtime(self):
        from sticker_maker import diagnostics, models
        import gphoto2
        fake = SimpleNamespace(__version__="2.2.2", GP_VERSION_SHORT=0,
            gp_library_version=lambda _: ["2.5.33"],
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)))
        with ExitStack() as stack:
            stack.enter_context(patch.object(gphoto2, "Camera", side_effect=AssertionError("No USB access")))
            stack.enter_context(patch.object(diagnostics.metadata, "version", return_value="test"))
            stack.enter_context(patch.object(diagnostics.platform, "system", return_value="Darwin"))
            stack.enter_context(patch.object(diagnostics.platform, "machine", return_value="x86_64"))
            stack.enter_context(patch.object(diagnostics.platform, "mac_ver", return_value=("13.7.5", (), "")))
            stack.enter_context(patch.object(diagnostics.sys, "version_info", (3, 12, 0)))
            stack.enter_context(patch.object(models, "verify_model", return_value={"sha256": "test"}))
            stack.enter_context(patch.object(diagnostics.importlib, "import_module", return_value=fake))
            result = diagnostics.doctor(require_ai=True)
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["camera_accessed"])
        self.assertEqual(result["recommended_device"], "cpu")


if __name__ == "__main__":
    unittest.main()
