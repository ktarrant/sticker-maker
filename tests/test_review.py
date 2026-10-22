"""Optional offscreen interaction tests for the desktop review editor."""

import importlib.util
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
from shapely.geometry import box

from sticker_maker.geometry import build_cut_paths
from sticker_maker.regions import RegionDocument, geojson, polygon

HAS_GUI = importlib.util.find_spec("PySide6") is not None
if HAS_GUI:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QAction
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton
    from sticker_maker.review import ReviewWindow


@unittest.skipUnless(HAS_GUI, "optional desktop GUI extra")
class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.image = self.root / "page.png"
        Image.new("RGB", (600, 400), "white").save(self.image)
        self.window = ReviewWindow(self.image, output=self.root / "regions.json")
        self.errors = []
        self.window._error = self.errors.append
        self.window.show()
        self.application.processEvents()
        self.addCleanup(self.window.close)

    def press(self, text):
        next(button for button in self.window.findChildren(QPushButton) if button.text() == text).click()
        self.application.processEvents()

    def draw(self, mode, start, end):
        view = self.window.view
        self.window.mode.setCurrentText(mode)
        first = view.mapFromScene(*start)
        last = view.mapFromScene(*end)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
        QTest.mouseMove(view.viewport(), last)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=last)
        self.application.processEvents()

    def test_draw_adjust_border_undo_and_reopen_identical_outlines(self):
        self.draw("Rectangle", (100, 100), (220, 200))
        self.assertFalse(self.errors)
        self.assertEqual(len(self.window.document.state["regions"]), 1)
        self.assertEqual(len(self.window.selected_ids()), 1)
        original = polygon(self.window.document.state["regions"][0]["geometry"])
        self.window.offset.setValue(5)
        self.window.units.setCurrentText("px")
        self.press("Apply border / shape")
        grown = polygon(build_cut_paths(self.window.document)["paths"][0]["geometry"])
        self.assertAlmostEqual(grown.bounds[0], original.bounds[0]-5)
        next(action for action in self.window.findChildren(QAction) if action.text() == "Undo").trigger()
        self.application.processEvents()
        self.assertTrue(polygon(build_cut_paths(self.window.document)["paths"][0]["geometry"]).equals(original))
        self.window.save()
        loaded = RegionDocument.load(self.root / "regions.json")
        self.assertEqual(loaded.state, self.window.document.state)

    def test_scene_selection_removal_restoration_and_polygon_tool(self):
        self.draw("Rectangle", (100, 100), (220, 200))
        identifier = self.window.selected_ids()[0]
        self.window._select([])
        self.window.mode.setCurrentText("Select")
        point = self.window.view.mapFromScene(160, 150)
        QTest.mouseClick(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.application.processEvents()
        self.assertEqual(self.window.selected_ids(), [identifier])
        self.press("Remove selected")
        self.assertEqual(self.window.region_list.count(), 0)
        self.window.edit({"op": "undo"})
        self.assertEqual(self.window.region_list.count(), 1)
        self.window.mode.setCurrentText("Polygon")
        for point in [(300, 100), (400, 100), (350, 200)]:
            QTest.mouseClick(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=self.window.view.mapFromScene(*point))
        QTest.keyClick(self.window.view, Qt.Key.Key_Return)
        self.application.processEvents()
        self.assertEqual(len(self.window.document.state["regions"]), 2)
        self.assertFalse(self.errors)

    def test_vertex_drag_updates_shared_document_and_can_undo(self):
        self.draw("Rectangle", (100, 100), (220, 200))
        original = self.window.document.state["regions"][0]["geometry"]
        self.window.mode.setCurrentText("Edit vertices")
        self.assertEqual(len(self.window.handles), 4)
        handle = self.window.handles[0]
        start = self.window.view.mapFromScene(handle.pos())
        end = start + QPoint(15, 15)
        QTest.mousePress(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(self.window.view.viewport(), end)
        QTest.mouseRelease(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.application.processEvents()
        self.assertNotEqual(self.window.document.state["regions"][0]["geometry"], original)
        self.window.edit({"op": "undo"})
        self.assertEqual(self.window.document.state["regions"][0]["geometry"], original)
        self.assertFalse(self.errors)

    def test_stale_analysis_is_dropped_without_overwriting_edits(self):
        self.draw("Rectangle", (100, 100), (220, 200))
        document = self.window.document
        reply = {"task": {"op": "propose", "job_id": document.job_id, "revision": document.revision-1},
                 "result": {"source_sha256": document.source["sha256"], "proposals": [{"geometry": geojson(box(300, 100, 400, 200))}]}}
        original = document.to_dict()
        from unittest.mock import patch
        with patch.object(self.window.tasks, "poll", return_value=reply):
            self.window._poll()
        self.assertEqual(document.to_dict(), original)
        self.assertIn("discarded", self.window.statusBar().currentMessage())

    def test_capture_waits_for_completion_before_opening_and_proposing(self):
        from unittest.mock import patch
        self.window.tasks.pending = {"op": "capture"}
        self.window._cancel()
        self.assertIsNotNone(self.window.tasks.pending)
        self.assertFalse(self.window.close())
        self.window.tasks.pending = None
        reply = {"task": {"op": "capture"}, "result": {"image": str(self.image), "metadata": {"warnings": []}}}
        with patch.object(self.window.tasks, "poll", return_value=reply), patch.object(self.window, "open_path") as opened, patch.object(self.window, "_propose") as propose:
            self.window._poll()
        opened.assert_called_once_with(self.image)
        propose.assert_called_once()

    @unittest.skipUnless(os.environ.get("STICKER_MAKER_SAM_CHECKPOINT"), "optional cached-model integration")
    def test_ai_box_and_corrective_click_through_real_background_worker(self):
        from PIL import ImageDraw
        image = Image.open(self.image)
        draw = ImageDraw.Draw(image)
        draw.rectangle((120, 120, 220, 220), fill="red")
        image.save(self.image)
        # Reopen so the new test raster has its own correct image identity.
        self.window.open_path(self.image, output=self.root / "ai-regions.json")
        self.window.model_configuration = {"checkpoint": os.environ["STICKER_MAKER_SAM_CHECKPOINT"],
                                           "model": "small", "device": "auto"}
        def wait():
            deadline = time.monotonic() + 30
            while self.window.tasks.pending is not None and time.monotonic() < deadline:
                self.application.processEvents()
                QTest.qWait(20)
            self.application.processEvents()
            self.assertIsNone(self.window.tasks.pending)
            self.assertFalse(self.errors)
        self.draw("AI box", (110, 110), (230, 230))
        wait()
        first = self.window.document.state["regions"][-1]
        self.assertEqual(first["origin"]["provider"], "sam2")
        self.assertEqual(first["status"], "proposed")
        self.window._refine(point=[170, 170], label=1)
        wait()
        second = self.window.document.state["regions"][-1]
        self.assertEqual(second["origin"]["parents"], [first["id"]])
        self.assertEqual(second["origin"]["prompts"]["labels"], [1])
        self.window._refine(point=[115, 115], label=0)
        wait()
        third = self.window.document.state["regions"][-1]
        self.assertEqual(third["origin"]["prompts"]["labels"], [1, 0])
        self.press("Use selected / accept refinement")
        self.assertEqual(self.window.document.state["regions"][0]["status"], "rejected")
        self.assertEqual(self.window.document.state["regions"][1]["status"], "rejected")
        self.assertEqual(self.window.document.state["regions"][2]["status"], "selected")


if __name__ == "__main__":
    unittest.main()
