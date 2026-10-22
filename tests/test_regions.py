"""Editor and physical geometry contracts, without a camera or recognition model."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from PIL import Image
from shapely.geometry import Polygon, box

from sticker_maker.calibration import fit_calibration
from sticker_maker.geometry import build_cut_paths, map_geometry
from sticker_maker.regions import RegionDocument, RegionError, geojson, polygon


class RegionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.image = self.root / "photo.png"
        Image.new("RGB", (600, 400), "white").save(self.image)
        self.doc = RegionDocument.create(self.image)

    def add(self, geometry=None, **kwargs):
        return self.doc.apply({"op": "add", "geometry": geojson(geometry if geometry is not None else box(100, 100, 180, 180)), **kwargs})[0]

    def profile(self, measured=True, projection=None):
        def image_point(u, v):
            return projection(u, v) if projection else [u * 2, v * 2]
        data = {"schema_version": 1, "rig_id": "test-rig", "image_size_px": [600, 400],
                "mat_size_mm": [300, 200], "source_image_sha256": self.doc.source["sha256"] if measured else None,
                "reference_kind": "measured" if measured else "synthetic",
                "reference_points": [{"mat_mm": [u, v], "image_px": image_point(u, v)}
                                     for u, v in [(10, 10), (290, 10), (290, 190), (10, 190)]],
                "check_points": [{"mat_mm": [u, v], "image_px": image_point(u, v)}
                                 for u, v in [(50, 50), (200, 50), (200, 150)]]}
        return fit_calibration(data)

    def calibrate(self, profile=None):
        self.doc.apply({"op": "calibration", "profile": (profile or self.profile()).to_dict()})

    def test_roundtrip_and_undo_redo_keep_stable_ids_and_increasing_revisions(self):
        identifier = self.add()
        self.doc.apply({"op": "recipe", "ids": [identifier], "recipe": {"offset": 4, "units": "px"}})
        path = self.root / "job.json"
        self.doc.save(path)
        loaded = RegionDocument.load(path)
        self.assertEqual(loaded.to_dict(), self.doc.to_dict())
        revision = loaded.revision
        loaded.undo()
        self.assertEqual(loaded.state["regions"][0]["recipe"]["offset"], 0)
        loaded.redo()
        self.assertEqual(loaded.state["regions"][0]["id"], identifier)
        self.assertEqual(loaded.revision, revision + 2)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_outlines_and_recipes_are_non_destructive(self):
        identifier = self.add()
        base = self.doc.state["regions"][0]["geometry"]
        for value in [10, 20, 0]:
            self.doc.apply({"op": "recipe", "ids": [identifier], "recipe": {"offset": value, "units": "px"}})
        self.assertEqual(self.doc.state["regions"][0]["geometry"], base)
        self.assertTrue(polygon(build_cut_paths(self.doc)["paths"][0]["geometry"]).equals(polygon(base)))

    def test_concurrent_saves_preserve_external_edits(self):
        path = self.root / "regions.json"
        self.doc.save(path)
        other = RegionDocument.load(path)
        other.apply({"op": "add", "geometry": geojson(box(100, 100, 120, 120))})
        other.save(path)
        self.add(box(200, 100, 220, 120))
        with self.assertRaisesRegex(RegionError, "changed on disk"):
            self.doc.save(path)
        self.assertEqual(RegionDocument.load(path).state, other.state)
        self.doc.save(self.root / "recovered.json")

    def test_union_retains_disconnected_components_and_backing_is_explicit(self):
        ids = [self.add(box(100, 100, 120, 120)), self.add(box(150, 100, 170, 120))]
        self.doc.apply({"op": "group", "ids": ids})
        self.assertEqual(len(build_cut_paths(self.doc)["paths"]), 2)
        self.doc.apply({"op": "union", "ids": ids})
        combined = polygon(build_cut_paths(self.doc)["paths"][0]["geometry"])
        self.assertEqual(combined.geom_type, "MultiPolygon")
        self.doc.undo()
        self.doc.apply({"op": "backing", "ids": ids, "mode": "rectangle"})
        backed = polygon(build_cut_paths(self.doc)["paths"][0]["geometry"])
        self.assertEqual(backed.geom_type, "Polygon")
        self.assertEqual(backed.area, 70 * 20)

    def test_combining_different_styles_requires_result_recipe(self):
        ids = [self.add(), self.add(box(180, 100, 200, 180), recipe={"offset": 2})]
        before = self.doc.to_dict()
        with self.assertRaisesRegex(RegionError, "different recipes"):
            self.doc.apply({"op": "union", "ids": ids})
        self.assertEqual(self.doc.to_dict(), before)
        self.doc.apply({"op": "union", "ids": ids, "recipe": {"offset": 1}})

    def test_split_brush_reset_and_rejection_are_reversible(self):
        identifier = self.add()
        self.doc.apply({"op": "brush", "ids": [identifier], "mode": "erase", "geometry": geojson(box(90, 90, 140, 200))})
        self.assertEqual(polygon(self.doc.state["regions"][0]["geometry"]).bounds[0], 140)
        self.doc.apply({"op": "reset", "ids": [identifier]})
        children = self.doc.apply({"op": "split", "ids": [identifier], "line": [[140, 90], [140, 190]]})
        self.assertEqual(len(children), 2)
        self.assertEqual(sum(polygon(p["geometry"]).area for p in build_cut_paths(self.doc)["paths"]), 6400)
        self.doc.apply({"op": "reject", "ids": children})
        self.assertFalse(build_cut_paths(self.doc)["paths"])
        self.doc.undo()
        self.assertEqual(len(build_cut_paths(self.doc)["paths"]), 2)

    def test_proposals_do_not_overwrite_edits_and_stale_results_fail(self):
        identifier = self.add()
        revision = self.doc.revision
        command = {"op": "proposals", "source_sha256": self.doc.source["sha256"],
                   "proposals": [{"geometry": geojson(box(200, 100, 250, 150))}]}
        self.doc.apply(command, expected_revision=revision)
        self.assertEqual(self.doc.state["regions"][0]["id"], identifier)
        self.assertEqual(len(build_cut_paths(self.doc)["paths"]), 1)
        with self.assertRaisesRegex(RegionError, "Stale"):
            self.doc.apply(command, expected_revision=revision)
        self.doc.undo()
        with self.assertRaisesRegex(RegionError, "Stale"):
            self.doc.apply(command, expected_revision=revision)
        with self.assertRaises(RegionError):
            self.doc.apply(command | {"source_sha256": "a" * 64})

    def test_offsets_are_in_mat_mm_after_perspective_mapping(self):
        def projection(u, v):
            d = 1 + 0.0008*u + 0.0004*v
            return [(1.5*u+10)/d, (1.5*v+10)/d]
        profile = self.profile(projection=projection)
        image_shape = Polygon([projection(u, v) for u, v in [(50, 50), (100, 50), (100, 100), (50, 100)]])
        identifier = self.add(image_shape)
        self.calibrate(profile)
        self.doc.apply({"op": "recipe", "ids": [identifier], "recipe": {"offset": 2, "join": "mitre"}})
        geometry = polygon(build_cut_paths(self.doc)["paths"][0]["geometry"])
        np.testing.assert_allclose(geometry.bounds, [48, 48, 102, 102], atol=1e-8)
        self.assertAlmostEqual(geometry.area, 54**2, places=7)

    def test_uncalibrated_mm_values_are_pending_not_guessed(self):
        identifier = self.add()
        self.doc.apply({"op": "recipe", "ids": [identifier], "recipe": {"offset": 2}})
        preview = build_cut_paths(self.doc)
        self.assertEqual(preview["units"], "px")
        self.assertIn("pending_units", [d["code"] for d in preview["diagnostics"]])
        self.assertEqual(polygon(preview["paths"][0]["geometry"]).bounds, (100, 100, 180, 180))
        with self.assertRaisesRegex(RegionError, "calibration"):
            self.doc.approve()

    def test_default_holes_are_filled_and_inner_cuts_are_explicit(self):
        identifier = self.add(Polygon([(100, 100), (200, 100), (200, 200), (100, 200)],
                                      holes=[[(120, 120), (180, 120), (180, 180), (120, 180)]]))
        self.assertEqual(len(polygon(build_cut_paths(self.doc)["paths"][0]["geometry"]).interiors), 0)
        self.doc.apply({"op": "recipe", "ids": [identifier], "recipe": {"cut_holes": True}})
        self.assertEqual(len(polygon(build_cut_paths(self.doc)["paths"][0]["geometry"]).interiors), 1)

    def test_empty_negative_offset_and_bounds_are_reported(self):
        identifier = self.add(box(0, 0, 10, 10))
        self.doc.apply({"op": "recipe", "ids": [identifier], "recipe": {"offset": -6, "units": "px"}})
        self.assertIn("empty_offset", [d["code"] for d in build_cut_paths(self.doc)["diagnostics"]])
        self.doc.apply({"op": "recipe", "ids": [identifier], "recipe": {"offset": 2}})
        self.assertIn("sheet_bounds", [d["code"] for d in build_cut_paths(self.doc)["diagnostics"]])

    def test_overlap_duplicate_and_clearance_checks(self):
        ids = [self.add(), self.add()]
        self.assertIn("overlap", [d["code"] for d in build_cut_paths(self.doc)["diagnostics"]])
        self.doc.apply({"op": "replace", "ids": [ids[1]], "geometry": geojson(box(181, 100, 220, 180))})
        self.calibrate()
        self.doc.apply({"op": "constraints", "minimum_clearance_mm": 1})
        self.assertIn("clearance", [d["code"] for d in build_cut_paths(self.doc)["diagnostics"]])

    def test_physical_approval_binds_snapshot_to_all_edits_and_calibration(self):
        identifier = self.add()
        self.calibrate()
        with self.assertRaisesRegex(RegionError, "Review"):
            self.doc.approve()
        self.doc.apply({"op": "review", "ids": [identifier]})
        expected = build_cut_paths(self.doc, physical=True)
        self.doc.approve()
        self.assertEqual(self.doc.approved_snapshot(), expected)
        self.doc.save(self.root / "approved.json")
        loaded = RegionDocument.load(self.root / "approved.json")
        self.assertEqual(loaded.approved_snapshot(), expected)
        loaded.apply({"op": "recipe", "ids": [identifier], "recipe": {"offset": 1}})
        with self.assertRaises(RegionError):
            loaded.approved_snapshot()
        loaded.undo()
        with self.assertRaises(RegionError):
            loaded.approved_snapshot()

    def test_tampered_source_or_approved_paths_fail(self):
        identifier = self.add()
        self.calibrate()
        self.doc.apply({"op": "review", "ids": [identifier]})
        self.doc.approve()
        data = self.doc.to_dict()
        data["approval"]["snapshot"]["paths"][0]["geometry"] = geojson(box(1, 1, 2, 2))
        with self.assertRaisesRegex(RegionError, "fingerprint"):
            RegionDocument(data).approved_snapshot()
        self.doc.save(self.root / "job.json")
        Image.new("RGB", (600, 400), "black").save(self.image)
        with self.assertRaisesRegex(RegionError, "Source image"):
            RegionDocument.load(self.root / "job.json")

    def test_synthetic_and_mismatched_calibration_cannot_authorize_physical_cuts(self):
        self.add()
        self.calibrate(self.profile(measured=False))
        with self.assertRaisesRegex(RegionError, "measured"):
            self.doc.approve()
        bad = deepcopy(self.profile().to_dict())
        bad["source_image_sha256"] = "a" * 64
        with self.assertRaises(ValueError):
            self.doc.apply({"op": "calibration", "profile": bad})

    def test_invalid_geometry_and_numeric_inputs_do_not_mutate_document(self):
        identifier = self.add()
        before = self.doc.to_dict()
        for command in [
            {"op": "replace", "ids": [identifier], "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]]}},
            {"op": "recipe", "ids": [identifier], "recipe": {"offset": float("nan")}},
            {"op": "brush", "ids": [identifier], "mode": "erase", "geometry": geojson(box(0, 0, 600, 400))},
        ]:
            with self.subTest(command=command), self.assertRaises(RegionError):
                self.doc.apply(command)
            self.assertEqual(self.doc.to_dict(), before)

    def test_simplification_respects_deviation_and_narrow_bridge_warning(self):
        geometry = box(100, 100, 130, 130).union(box(150, 100, 180, 130)).union(box(125, 114, 155, 116))
        identifier = self.add(geometry)
        self.calibrate()
        self.doc.apply({"op": "constraints", "minimum_feature_mm": 2})
        self.doc.apply({"op": "recipe", "ids": [identifier], "recipe": {"simplify": 0.2}})
        result = build_cut_paths(self.doc)
        self.assertIn("narrow_feature", [d["code"] for d in result["diagnostics"]])
        original = map_geometry(geometry, self.profile().pixels_to_mm)
        self.assertLessEqual(original.boundary.hausdorff_distance(polygon(result["paths"][0]["geometry"]).boundary), 0.2 + 1e-7)


if __name__ == "__main__":
    unittest.main()
