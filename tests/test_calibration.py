"""Synthetic geometry verifies calibration independently of camera hardware."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from sticker_maker.calibration import CalibrationError, fit_calibration, load_calibration
from sticker_maker.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "calibration-references.json"


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text())
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_affine_mapping_recovers_known_physical_dimensions(self):
        profile = fit_calibration(self.data)
        actual = profile.pixels_to_mm([[3000, 2000], [1500, 1000], [4500, 3000]])
        np.testing.assert_allclose(actual, [[150, 100], [0, 0], [300, 200]], atol=1e-10)
        self.assertEqual(profile.validation.status, "passed")
        self.assertLess(profile.validation.max_error_mm, 1e-10)

    def perspective_data(self):
        data = deepcopy(self.data)
        # Analytic camera projection independent of the fitting implementation.
        def image_point(u, v):
            denominator = 1 + 0.0005*u + 0.0003*v
            return [(10*u + 100)/denominator, (8*v + 200)/denominator]
        for group in [data["reference_points"], data["check_points"]]:
            for point in group:
                point["image_px"] = image_point(*point["mat_mm"])
        return data, image_point

    def test_perspective_mapping_and_inverse_recover_unseen_points(self):
        data, image_point = self.perspective_data()
        profile = fit_calibration(data)
        mat_points = [[20, 30], [145, 125], [270, 170]]
        pixels = [image_point(*point) for point in mat_points]
        np.testing.assert_allclose(profile.pixels_to_mm(pixels), mat_points, atol=1e-9)
        np.testing.assert_allclose(profile.mm_to_pixels(mat_points), pixels, atol=1e-9)

    def test_overdetermined_fit_handles_small_measurement_noise(self):
        data, image_point = self.perspective_data()
        for u, v, dx, dy in [(75, 100, 0.1, -0.1), (225, 100, -0.1, 0.1)]:
            x, y = image_point(u, v)
            data["reference_points"].append({"image_px": [x+dx, y+dy], "mat_mm": [u, v]})
        profile = fit_calibration(data)
        self.assertEqual(profile.validation.status, "passed")
        self.assertGreater(profile.validation.rms_error_mm, 0)
        self.assertLess(profile.validation.max_error_mm, 0.1)

    def test_fitted_corners_alone_do_not_establish_accuracy(self):
        self.data["check_points"] = []
        profile = fit_calibration(self.data)
        self.assertEqual(profile.validation.status, "not_checked")
        self.assertIsNone(profile.validation.max_error_mm)
        with self.assertRaises(CalibrationError):
            profile.require_measured_validation()

    def test_independent_error_budget_failure_is_reported(self):
        self.data["reference_kind"] = "measured"
        self.data["source_image_sha256"] = "a" * 64
        self.data["check_points"][0]["mat_mm"][0] += 1
        profile = fit_calibration(self.data)
        self.assertEqual(profile.validation.status, "failed")
        self.assertAlmostEqual(profile.validation.max_error_mm, 1)
        with self.assertRaises(CalibrationError):
            profile.require_measured_validation()

    def test_check_points_require_two_dimensional_coverage(self):
        self.data["check_points"] = [
            {"image_px": [2000, 1500], "mat_mm": [50, 50]},
            {"image_px": [3000, 1500], "mat_mm": [150, 50]},
            {"image_px": [4000, 1500], "mat_mm": [250, 50]},
        ]
        self.assertEqual(fit_calibration(self.data).validation.status, "failed")

    def test_reusing_fitting_points_as_checks_is_rejected(self):
        self.data["check_points"] = self.data["reference_points"][:3]
        with self.assertRaisesRegex(CalibrationError, "independent"):
            fit_calibration(self.data)

    def test_synthetic_profiles_never_claim_measured_validation(self):
        profile = fit_calibration(self.data)
        self.assertEqual(profile.validation.status, "passed")
        with self.assertRaisesRegex(CalibrationError, "measured"):
            profile.require_measured_validation()

    def test_measured_profile_requires_a_source_image_identity(self):
        self.data["reference_kind"] = "measured"
        with self.assertRaises(CalibrationError):
            fit_calibration(self.data).require_measured_validation()
        self.data["source_image_sha256"] = "a" * 64
        fit_calibration(self.data).require_measured_validation()

    def test_profile_round_trip_preserves_mapping_and_fingerprint(self):
        original = fit_calibration(self.data)
        path = self.root / "profiles" / "calibration.json"
        original.save(path)
        loaded = load_calibration(path)
        self.assertEqual(loaded.profile_id, original.profile_id)
        self.assertEqual(loaded.validation, original.validation)
        self.assertEqual(loaded.pixels_to_mm([[3000, 2000]]), original.pixels_to_mm([[3000, 2000]]))
        self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_changing_a_profile_invalidates_its_fingerprint(self):
        profile = fit_calibration(self.data)
        path = self.root / "profile.json"
        profile.save(path)
        changed = json.loads(path.read_text())
        changed["image_to_mat"][0][2] += 0.01
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(CalibrationError, "fingerprint"):
            load_calibration(path)

    def test_stored_validation_summary_is_recomputed(self):
        self.data["check_points"][0]["mat_mm"][0] += 1
        profile = fit_calibration(self.data)
        path = self.root / "profile.json"
        profile.save(path)
        changed = json.loads(path.read_text())
        changed["validation"]["status"] = "passed"
        changed["validation"]["max_error_mm"] = 0
        path.write_text(json.dumps(changed))
        self.assertEqual(load_calibration(path).validation.status, "failed")

    def test_incompatible_image_context_is_rejected(self):
        self.data["source_image_sha256"] = "a" * 64
        profile = fit_calibration(self.data)
        context = {"image_size_px": (6000, 4000), "rig_id": self.data["rig_id"], "source_image_sha256": "a" * 64}
        profile.assert_compatible(**context)
        for change in [{"image_size_px": (3000, 2000)}, {"rig_id": "moved-rig"},
                       {"exif_orientation": 6}, {"source_image_sha256": "b" * 64},
                       {"pixel_space": "undistorted", "lens_profile_id": "lens-v1"}]:
            with self.subTest(change=change), self.assertRaises(CalibrationError):
                profile.assert_compatible(**(context | change))

    def test_distortion_frame_requires_explicit_lens_profile_identity(self):
        self.data["pixel_space"] = "undistorted"
        with self.assertRaises(CalibrationError):
            fit_calibration(self.data)
        self.data["lens_profile_id"] = "locked-16mm-lens-v1"
        profile = fit_calibration(self.data)
        profile.assert_compatible(image_size_px=(6000, 4000), rig_id=self.data["rig_id"],
                                  pixel_space="undistorted", lens_profile_id="locked-16mm-lens-v1")

    def test_degenerate_reference_geometry_is_rejected(self):
        for coordinates in [[(1, 1), (2, 2), (3, 3), (4, 4)], [(1, 1)] * 4]:
            data = deepcopy(self.data)
            for point, xy in zip(data["reference_points"], coordinates):
                point["image_px"] = xy
            with self.subTest(coordinates=coordinates), self.assertRaises(CalibrationError):
                fit_calibration(data)

    def test_too_few_references_are_rejected(self):
        self.data["reference_points"] = self.data["reference_points"][:3]
        with self.assertRaisesRegex(CalibrationError, "four"):
            fit_calibration(self.data)

    def test_invalid_numeric_and_frame_metadata_is_rejected(self):
        changes = [{"schema_version": 2}, {"schema_version": True}, {"image_size_px": [0, 4000]},
                   {"image_size_px": [6000.0, 4000]}, {"mat_size_mm": [300, -1]},
                   {"max_error_mm": float("nan")}, {"source_image_sha256": "invalid"},
                   {"source_image_sha256": 1}, {"exif_orientation": 9}, {"rig_id": ""},
                   {"lens_profile_id": "unexpected-for-raw"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(CalibrationError):
                fit_calibration(self.data | change)
        self.data["reference_points"][0]["image_px"][0] = float("inf")
        with self.assertRaises(CalibrationError):
            fit_calibration(self.data)

    def test_points_outside_declared_image_or_mat_are_rejected(self):
        for key, coordinates in [("image_px", [-1, 10]), ("mat_mm", [301, 10])]:
            data = deepcopy(self.data)
            data["reference_points"][0][key] = coordinates
            with self.subTest(key=key), self.assertRaises(CalibrationError):
                fit_calibration(data)

    def test_empty_mapping_and_invalid_points(self):
        profile = fit_calibration(self.data)
        self.assertEqual(profile.pixels_to_mm([]), [])
        for points in [[[1, 2, 3]], [[float("nan"), 1]], [[]]]:
            with self.subTest(points=points), self.assertRaises(CalibrationError):
                profile.pixels_to_mm(points)

    def test_mapping_rejects_a_point_on_projective_horizon(self):
        data, _ = self.perspective_data()
        profile = fit_calibration(data)
        with self.assertRaisesRegex(CalibrationError, "horizon"):
            profile.mm_to_pixels([[-2000, 0]])

    def test_calibration_module_does_not_import_camera_library(self):
        code = "import sys; sys.modules['gphoto2'] = None; import sticker_maker.calibration"
        subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, timeout=10)

    def test_cli_fit_map_and_inspect_are_camera_independent(self):
        path = self.root / "profile.json"
        def invoke(*arguments):
            output = io.StringIO()
            with patch.object(sys, "argv", ["sticker-maker", "calibration", *arguments]), redirect_stdout(output):
                main()
            return json.loads(output.getvalue())
        with patch("sticker_maker.cli.capture_image", side_effect=AssertionError("Camera must not be used")):
            fitted = invoke("fit", str(FIXTURE), "--output", str(path))
            self.assertEqual(fitted["reference_kind"], "synthetic")
            mapped = invoke("map", str(path), "--points", "[[3000,2000]]")
            np.testing.assert_allclose(mapped["points"], [[150, 100]], atol=1e-10)
            restored = invoke("map", str(path), "--points", "[[150,100]]", "--inverse")
            np.testing.assert_allclose(restored["points"], [[3000, 2000]], atol=1e-9)
            self.assertEqual(invoke("inspect", str(path))["validation"]["status"], "passed")
        with patch.object(sys, "argv", ["sticker-maker", "calibration", "inspect", str(path), "--for-export"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                main()
        self.assertEqual(result.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
