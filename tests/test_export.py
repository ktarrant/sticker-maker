"""SVG serializes exactly the approved snapshot and rejects unapproved jobs."""

import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from PIL import Image
from shapely.geometry import Polygon, box

from sticker_maker.calibration import fit_calibration
from sticker_maker.export import SVG_NS, export_svg
from sticker_maker.geometry import components
from sticker_maker.regions import RegionDocument, RegionError, geojson, polygon


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.image = self.root / "photo.png"
        Image.new("RGB", (400, 400), "white").save(self.image)
        self.doc = RegionDocument.create(self.image)
        self.path = self.root / "regions.json"

    def approved(self):
        identifier = self.doc.apply({"op": "add", "label": "Sticker <&> test",
                                     "geometry": geojson(Polygon([(50, 50), (250, 50), (250, 250), (50, 250)],
                                                                 [[(100, 100), (200, 100), (200, 200), (100, 200)]])),
                                     "recipe": {"cut_holes": True}})[0]
        # Generated reference data exercises the measured-profile contract; it
        # does not claim that this test image or a physical cutter was calibrated.
        references = {"schema_version": 1, "rig_id": "unit-test", "image_size_px": [400, 400], "mat_size_mm": [200, 200],
                      "reference_kind": "measured", "source_image_sha256": self.doc.source["sha256"],
                      "reference_points": [{"image_px": [x*2, y*2], "mat_mm": [x, y]} for x, y in [(10, 10), (190, 10), (190, 190), (10, 190)]],
                      "check_points": [{"image_px": [x*2, y*2], "mat_mm": [x, y]} for x, y in [(50, 50), (150, 50), (150, 150)]]}
        self.doc.apply({"op": "calibration", "profile": fit_calibration(references).to_dict()})
        self.doc.apply({"op": "review", "ids": [identifier]})
        self.doc.approve()
        self.doc.save(self.path)

    def test_svg_coordinates_match_approved_vertices_without_rebuilding_or_source_image(self):
        self.approved()
        snapshot = self.doc.approved_snapshot()
        self.image.unlink()
        with patch("sticker_maker.geometry.build_cut_paths", side_effect=AssertionError("Do not rebuild reviewed paths")):
            report = export_svg(self.path, self.root / "cuts.svg")
        svg = ET.parse(report["svg"]).getroot()
        self.assertEqual(svg.attrib["width"], "200.0mm")
        self.assertEqual(svg.attrib["height"], "200.0mm")
        nodes = svg.findall(f".//{{{SVG_NS}}}path")
        self.assertEqual(len(nodes), len(snapshot["paths"]))
        for node, path in zip(nodes, snapshot["paths"]):
            numbers = list(map(float, re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", node.attrib["d"])))
            expected = [v for part in components(polygon(path["geometry"]))
                        for ring in [part.exterior, *part.interiors] for point in list(ring.coords)[:-1] for v in point]
            self.assertEqual(numbers, expected)
            self.assertEqual(node.find(f"{{{SVG_NS}}}title").text, "Sticker <&> test")
        placement = json.loads(Path(report["placement"]).read_text())
        self.assertEqual(placement["units"], "mm")
        self.assertEqual(len(placement["placement_instructions"]), 5)

    def test_unapproved_or_changed_jobs_do_not_write_svg(self):
        self.doc.save(self.path)
        target = self.root / "cuts.svg"
        with self.assertRaises(RegionError):
            export_svg(self.path, target)
        self.assertFalse(target.exists())
        self.approved()
        identifier = self.doc.state["regions"][0]["id"]
        self.doc.apply({"op": "recipe", "ids": [identifier], "recipe": {"offset": 1}})
        self.doc.save(self.path)
        with self.assertRaises(RegionError):
            export_svg(self.path, target)
        self.assertFalse(target.exists())

    def test_export_cannot_overwrite_document_or_use_changed_source(self):
        self.approved()
        before = self.path.read_bytes()
        with self.assertRaises(RegionError):
            export_svg(self.path, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        Image.new("RGB", (400, 400), "black").save(self.image)
        with self.assertRaisesRegex(RegionError, "Source image"):
            export_svg(self.path, self.root / "cuts.svg")


if __name__ == "__main__":
    unittest.main()
