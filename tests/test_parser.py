"""Saved-image parsing, scripted edits, and cancellable worker contracts."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from shapely.geometry import box

from sticker_maker.cli import main
from sticker_maker.regions import RegionDocument, RegionError, geojson, polygon
from sticker_maker.tasks import BackgroundTasks

HAS_CV = importlib.util.find_spec("cv2") is not None


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.image = self.root / "panels.png"
        image = Image.new("RGB", (800, 600), "#557744")
        draw = ImageDraw.Draw(image)
        for x in range(0, 800, 40):
            draw.line([(x, 0), (x, 600)], fill="#809966")
        for y in range(0, 600, 40):
            draw.line([(0, y), (800, y)], fill="#809966")
        draw.rectangle((100, 100, 310, 400), fill="white")
        draw.rectangle((380, 100, 590, 400), fill="white")
        draw.ellipse((130, 140, 280, 320), fill="red")
        draw.polygon([(400, 320), (490, 140), (570, 320)], fill="blue")
        image.save(self.image)

    def invoke(self, *arguments):
        output = io.StringIO()
        with patch.object(sys, "argv", ["sticker-maker", *arguments]), redirect_stdout(output):
            main()
        return json.loads(output.getvalue())

    @unittest.skipUnless(HAS_CV, "optional OpenCV extra")
    def test_proposals_find_panels_in_original_coordinates_inside_work_area(self):
        from sticker_maker.recognition import OpenCVRecognizer
        area = box(80, 80, 620, 420)
        result = OpenCVRecognizer().propose(self.image, work_area=geojson(area), max_side=400)
        proposals = [polygon(r["geometry"]) for r in result["proposals"]]
        self.assertTrue(all(area.covers(p) for p in proposals))
        for expected in [box(100, 100, 310, 400), box(380, 100, 590, 400)]:
            self.assertTrue(any(p.intersection(expected).area / p.union(expected).area > 0.9 for p in proposals))
        self.assertLess(len(proposals), 10, "Empty grid cells should not flood the result.")

    @unittest.skipUnless(HAS_CV, "optional OpenCV extra")
    def test_cli_parse_edit_preview_and_preserve_existing_job(self):
        path = self.root / "regions.json"
        result = self.invoke("parse", str(self.image), "--output", str(path))
        self.assertGreater(result["proposal_count"], 0)
        self.assertTrue(Path(result["overlay"]).is_file())
        document = RegionDocument.load(path)
        identifier = document.state["regions"][0]["id"]
        edited = self.invoke("regions", "edit", str(path), "--command", json.dumps({"op": "select", "ids": [identifier]}))
        self.assertEqual(edited["revision"], document.revision + 1)
        result = self.invoke("regions", "preview", str(path), "--output", str(self.root / "review.png"))
        self.assertEqual(result["paths"][0]["region_id"], identifier)
        before = path.read_bytes()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke("parse", str(self.image), "--output", str(path))
        self.assertEqual(path.read_bytes(), before)

    def test_batch_edit_failure_is_not_partially_saved(self):
        path = self.root / "regions.json"
        document = RegionDocument.create(self.image)
        document.save(path)
        before = path.read_bytes()
        commands = [{"op": "add", "geometry": geojson(box(100, 100, 200, 200))}, {"op": "unknown"}]
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke("regions", "edit", str(path), "--command", json.dumps(commands))
        self.assertEqual(path.read_bytes(), before)

    def test_overlay_cannot_overwrite_original_image(self):
        from sticker_maker.recognition import save_overlay
        document = RegionDocument.create(self.image)
        before = self.image.read_bytes()
        with self.assertRaisesRegex(RegionError, "original image"):
            save_overlay(document, self.image)
        self.assertEqual(self.image.read_bytes(), before)

    def test_recognition_acceptance_preserves_edits_until_explicit_replacement(self):
        document = RegionDocument.create(self.image)
        parent = document.apply({"op": "add", "geometry": geojson(box(100, 100, 200, 200)),
                                 "recipe": {"offset": 3}})[0]
        replacement = document.apply({"op": "proposals", "source_sha256": document.source["sha256"],
                                      "proposals": [{"geometry": geojson(box(102, 102, 198, 198)),
                                                     "origin": {"provider": "sam2", "parents": [parent]}}]})[0]
        self.assertEqual(document.state["regions"][0]["status"], "selected")
        document.apply({"op": "accept", "ids": [replacement]})
        self.assertEqual(document.state["regions"][0]["status"], "rejected")
        self.assertEqual(document.state["regions"][1]["recipe"]["offset"], 3)
        document.undo()
        self.assertEqual(document.state["regions"][0]["status"], "selected")

    def test_core_imports_and_document_loading_need_no_camera_model_or_gui(self):
        code = "import sys; sys.modules.update({k:None for k in ['gphoto2','torch','sam2','PySide6','cv2']}); import sticker_maker.regions, sticker_maker.geometry, sticker_maker.tasks"
        subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, timeout=10)

    @unittest.skipUnless(HAS_CV, "optional OpenCV extra")
    def test_background_proposals_return_identity_and_cancellation_can_restart(self):
        worker = BackgroundTasks()
        self.addCleanup(worker.close)
        task = {"op": "propose", "image": str(self.image), "work_area": geojson(box(0, 0, 800, 600)),
                "job_id": "test", "revision": 7}
        worker.submit(task)
        worker.close()
        self.assertIsNone(worker.poll())
        worker.submit(task)
        deadline = time.monotonic() + 15
        result = None
        while result is None and time.monotonic() < deadline:
            time.sleep(0.02)
            result = worker.poll()
        self.assertIsNotNone(result)
        self.assertNotIn("error", result)
        self.assertEqual(result["task"]["revision"], 7)
        self.assertGreater(len(result["result"]["proposals"]), 0)

    @unittest.skipUnless(HAS_CV, "optional OpenCV extra")
    def test_mask_tracing_retains_holes_and_original_crop_position(self):
        import numpy as np
        from sticker_maker.sam import mask_outline
        mask = np.zeros((100, 100), dtype=bool)
        mask[10:90, 10:90] = True
        mask[35:65, 35:65] = False
        geometry, discarded = mask_outline(mask, (200, 300))
        self.assertEqual(discarded, 0)
        self.assertEqual(len(geometry.interiors), 1)
        self.assertEqual(geometry.bounds, (210, 310, 290, 390))

    def test_mask_tracing_handles_diagonal_contacts_and_single_pixel_islands(self):
        import numpy as np
        from sticker_maker.sam import mask_outline
        mask = np.zeros((20, 20), dtype=bool)
        mask[1:6, 1:6] = True
        mask[6:10, 6:10] = True
        mask[15, 15] = True
        geometry, discarded = mask_outline(mask)
        self.assertTrue(geometry.is_valid)
        self.assertEqual(len(geometry.geoms), 3)
        self.assertEqual(geometry.area, int(mask.sum()))
        self.assertEqual(discarded, 0)


if __name__ == "__main__":
    unittest.main()
