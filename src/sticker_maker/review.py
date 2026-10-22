"""Thin desktop editor. All persistent edits use the camera-free region service."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from uuid import uuid4

try:
    from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
    from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                                 QFormLayout, QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem,
                                 QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QListWidget,
                                 QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSplitter,
                                 QScrollArea, QToolBar, QVBoxLayout, QWidget)
except ImportError as exc:
    from sticker_maker.regions import RegionError
    raise RegionError("Install desktop dependencies with: uv sync --extra gui") from exc

from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box

from sticker_maker.calibration import calibration_from_dict, load_calibration
from sticker_maker.geometry import build_cut_paths, components, map_geometry
from sticker_maker.recognition import read_rgb
from sticker_maker.regions import RegionDocument, RegionError, geojson, polygon
from sticker_maker.tasks import BackgroundTasks


def painter_path(geometry):
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    for part in components(geometry):
        for ring in [part.exterior, *part.interiors]:
            points = list(ring.coords)
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            path.closeSubpath()
    return path


def cosmetic_pen(color, width=2):
    pen = QPen(QColor(color), width)
    pen.setCosmetic(True)
    return pen


class VertexHandle(QGraphicsEllipseItem):
    def __init__(self, point, callback):
        super().__init__(-4, -4, 8, 8)
        self.setPos(*point)
        self.callback = callback
        self.setBrush(QColor("white"))
        self.setPen(cosmetic_pen("#168b9b", 1))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setZValue(20)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        position = self.pos()
        # Defer scene rebuilding until this handle's event handler has returned.
        QTimer.singleShot(0, lambda: self.callback([position.x(), position.y()]))


class ImageView(QGraphicsView):
    drawn = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.mode = "Select"
        self.points = []
        self.temporary = None
        self.brush_radius = 12.0
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#20252b"))

    def set_mode(self, mode):
        self.clear_drawing()
        self.mode = mode
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if mode == "Pan" else
                         QGraphicsView.DragMode.RubberBandDrag if mode == "Select" else QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor if mode in {"Select", "Edit vertices"} else Qt.CursorShape.CrossCursor)

    def clear_drawing(self):
        if self.temporary is not None:
            self.scene().removeItem(self.temporary)
        self.temporary, self.points = None, []

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        scale = self.transform().m11() * factor
        if 0.02 <= scale <= 15:
            self.scale(factor, factor)

    def mousePressEvent(self, event):
        if self.mode in {"Select", "Pan", "Edit vertices"} or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        position = self.mapToScene(event.position().toPoint())
        point = [position.x(), position.y()]
        if self.mode in {"AI include", "AI exclude"}:
            self.drawn.emit(self.mode, point)
            return
        if self.mode == "Polygon":
            self.points.append(point)
            self._ghost(point)
        else:
            self.points = [point]

    def mouseMoveEvent(self, event):
        if not self.points:
            return super().mouseMoveEvent(event)
        p = self.mapToScene(event.position().toPoint())
        point = [p.x(), p.y()]
        if self.mode in {"Freehand", "Brush add", "Brush erase"} and event.buttons() & Qt.MouseButton.LeftButton:
            if Point(point).distance(Point(self.points[-1])) > 1:
                self.points.append(point)
        self._ghost(point)

    def _ghost(self, point):
        if self.temporary:
            self.scene().removeItem(self.temporary)
        path = QPainterPath()
        if self.mode in {"Rectangle", "Rounded rectangle", "Work area", "AI box"}:
            rect = QRectF(QPointF(*self.points[0]), QPointF(*point)).normalized()
            path.addRect(rect)
        else:
            path.moveTo(*self.points[0])
            for vertex in [*self.points[1:], point]:
                path.lineTo(*vertex)
        self.temporary = self.scene().addPath(path, cosmetic_pen("#ffffff"))
        self.temporary.setZValue(100)

    def mouseReleaseEvent(self, event):
        if not self.points or self.mode == "Polygon":
            return super().mouseReleaseEvent(event)
        p = self.mapToScene(event.position().toPoint())
        self.points.append([p.x(), p.y()])
        points = list(self.points)
        self.clear_drawing()
        self.drawn.emit(self.mode, points)

    def mouseDoubleClickEvent(self, event):
        if self.mode == "Polygon" and len(self.points) >= 3:
            points = list(self.points)
            self.clear_drawing()
            self.drawn.emit(self.mode, points)
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.clear_drawing()
        elif event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and self.mode == "Polygon" and len(self.points) >= 3:
            points = list(self.points)
            self.clear_drawing()
            self.drawn.emit(self.mode, points)
        else:
            super().keyPressEvent(event)


class ReviewWindow(QMainWindow):
    def __init__(self, input_path=None, *, output=None):
        super().__init__()
        self.document = None
        self.document_path = Path(output) if output else None
        self.tasks = BackgroundTasks()
        self._syncing = False
        self.outlines = {}
        self.overlay_items = []
        self.handles = []
        self.prompts = {"box": None, "points": [], "labels": [], "region_id": None}
        self.setWindowTitle("Sticker Maker")
        self.resize(1440, 940)
        self.view = ImageView()
        self.view.drawn.connect(self._drawn)
        self.view.scene().selectionChanged.connect(self._scene_selection)
        self.region_list = QListWidget()
        self.region_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.region_list.itemSelectionChanged.connect(self._list_selection)
        self.show_rejected = QCheckBox("Show removed regions")
        self.show_rejected.toggled.connect(lambda: self.refresh())
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Regions · Shift/Cmd to select several"))
        left_layout.addWidget(self.region_list)
        left_layout.addWidget(self.show_rejected)
        controls = QWidget()
        panel = QVBoxLayout(controls)
        self.selection_label = QLabel("Open an image to begin")
        panel.addWidget(self.selection_label)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-1000, 1000)
        self.offset.setDecimals(2)
        self.units = QComboBox()
        self.units.addItems(["mm", "px"])
        self.join = QComboBox()
        self.join.addItems(["round", "mitre", "bevel"])
        self.simplify = QDoubleSpinBox()
        self.simplify.setRange(0, 100)
        self.simplify.setDecimals(3)
        self.holes = QCheckBox("Cut inner holes")
        self.brush = QDoubleSpinBox()
        self.brush.setRange(1, 300)
        self.brush.setValue(12)
        self.brush.valueChanged.connect(lambda value: setattr(self.view, "brush_radius", value))
        form = QFormLayout()
        for title, widget in [("Border (+grow / −shrink)", self.offset), ("Units", self.units),
                              ("Corners", self.join), ("Simplify tolerance", self.simplify), ("Brush radius (px)", self.brush)]:
            form.addRow(title, widget)
        panel.addLayout(form)
        panel.addWidget(self.holes)
        self._button(panel, "Apply border / shape", self._apply_recipe)
        for title, operation in [("Use selected / accept refinement", "accept"), ("Mark reviewed", "review"),
                                 ("Remove selected", "reject"), ("Reset outline", "reset"),
                                 ("Group", "group"), ("Ungroup", "ungroup"), ("Union outlines", "union")]:
            self._button(panel, title, lambda checked=False, op=operation: self.edit({"op": op, "ids": self.selected_ids()}))
        self._button(panel, "One backing (rectangle)", lambda: self.edit({"op": "backing", "mode": "rectangle", "ids": self.selected_ids()}))
        self._button(panel, "Split disconnected parts", lambda: self.edit({"op": "split", "ids": self.selected_ids()}))
        self._button(panel, "Load calibration…", self._load_calibration)
        self.warnings_reviewed = QCheckBox("I reviewed the geometry warnings")
        panel.addWidget(self.warnings_reviewed)
        self._button(panel, "Approve paths for export", self._approve)
        self._button(panel, "Export SVG…", self._export)
        self.diagnostics = QLabel("Uncalibrated: review in pixels; millimeter borders remain pending.")
        self.diagnostics.setWordWrap(True)
        self.diagnostics.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        panel.addWidget(self.diagnostics)
        panel.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(controls)
        scroll.setWidgetResizable(True)
        splitter = QSplitter()
        for widget in (left, self.view, scroll):
            splitter.addWidget(widget)
        splitter.setSizes([230, 890, 320])
        self.setCentralWidget(splitter)
        toolbar = QToolBar("Review")
        self.addToolBar(toolbar)
        self._action(toolbar, "Open…", self._open_dialog, QKeySequence.StandardKey.Open)
        self._action(toolbar, "Capture", self._capture)
        self._action(toolbar, "Save", self.save, QKeySequence.StandardKey.Save)
        self._action(toolbar, "Save as…", self._save_as)
        self._action(toolbar, "Undo", lambda: self.edit({"op": "undo"}), QKeySequence.StandardKey.Undo)
        self._action(toolbar, "Redo", lambda: self.edit({"op": "redo"}), QKeySequence.StandardKey.Redo)
        self._action(toolbar, "Fit", self._fit_view)
        self._action(toolbar, "Rotate view", lambda: self.view.rotate(90))
        self._action(toolbar, "Propose regions", self._propose)
        self._action(toolbar, "Cancel analysis", self._cancel)
        self.mode = QComboBox()
        self.mode.addItems(["Select", "Pan", "Rectangle", "Rounded rectangle", "Polygon", "Freehand", "Work area",
                            "Split", "Brush add", "Brush erase", "Edit vertices", "AI box", "AI include", "AI exclude"])
        self.mode.currentTextChanged.connect(self._mode_changed)
        toolbar.addWidget(self.mode)
        self._action(toolbar, "AI model…", self._model_dialog)
        default_weights = Path("output/models/sam2.1_hiera_small.pt").resolve()
        self.model_configuration = ({"checkpoint": str(default_weights), "model": "small", "device": "auto"}
                                    if default_weights.exists() else None)
        self.autosave = QTimer(self)
        self.autosave.setSingleShot(True)
        self.autosave.timeout.connect(self.save)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start(50)
        if input_path:
            self.open_path(Path(input_path), output=output)
        QTimer.singleShot(0, self._fit_view)

    def _button(self, layout, title, callback):
        button = QPushButton(title)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _action(self, toolbar, title, callback, shortcut=None):
        action = QAction(title, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(shortcut)
        toolbar.addAction(action)

    def _error(self, error):
        self.statusBar().showMessage(str(error))
        QMessageBox.warning(self, "Sticker Maker", str(error))

    def _open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open image or review job", "", "Images and jobs (*.jpg *.jpeg *.png *.tif *.tiff *.mpo *.json)")
        if path:
            self.open_path(Path(path))

    def _fit_view(self):
        if self.document:
            x1, y1, x2, y2 = polygon(self.document.state["work_area"]).bounds
            self.view.fitInView(QRectF(x1, y1, x2-x1, y2-y1), Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.view.fitInView(self.view.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def open_path(self, path, *, output=None):
        try:
            if self.tasks.pending and self.tasks.pending["op"] == "capture":
                raise RegionError("Wait for the camera transfer to finish before opening another image.")
            if self.document:
                if not self.save():
                    return
            if self.tasks.pending:
                self._cancel()
            if path.suffix.lower() == ".json":
                document = RegionDocument.load(path)
                target = path
            else:
                document = RegionDocument.create(path)
                target = Path(output) if output else Path("output/jobs") / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6]) / "regions.json"
                if target.exists():
                    raise RegionError("Choose a new output path; an existing review job must be opened directly.")
            image, _ = read_rgb(Path(document.source["path"]))
            raster = QImage(image.tobytes(), image.width, image.height, image.width * 3, QImage.Format.Format_RGB888).copy()
            pixmap = QPixmap.fromImage(raster)
            self._syncing = True
            self.view.clear_drawing()
            self.view.scene().clear()
            self.outlines, self.overlay_items, self.handles = {}, [], []
            self.view.scene().addPixmap(pixmap)
            self.view.setSceneRect(QRectF(0, 0, image.width, image.height))
            self.document, self.document_path = document, target
            self._syncing = False
            self.prompts = {"box": None, "points": [], "labels": [], "region_id": None}
            self.refresh()
            self._fit_view()
            self.save()
            self.setWindowTitle(f"Sticker Maker — {path.name}")
        except (OSError, ValueError, KeyError) as exc:
            self._syncing = False
            self._error(exc)

    def selected_ids(self):
        ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.region_list.selectedItems()}
        if self.document:
            for members in self.document.state["groups"].values():
                if ids.intersection(members):
                    ids.update(members)
            return [r["id"] for r in self.document.state["regions"] if r["id"] in ids]
        return []

    def _scene_selection(self):
        if self._syncing:
            return
        ids = {item.data(0) for item in self.view.scene().selectedItems()}
        self._select(ids)

    def _list_selection(self):
        if not self._syncing:
            self._select(self.selected_ids())

    def _select(self, ids):
        self._syncing = True
        for index in range(self.region_list.count()):
            item = self.region_list.item(index)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) in ids)
        for identifier, item in self.outlines.items():
            item.setSelected(identifier in ids)
        self._syncing = False
        self._inspector()

    def _inspector(self):
        ids = self.selected_ids()
        self.selection_label.setText(f"{len(ids)} region(s) selected")
        if ids and self.document:
            value = next(r for r in self.document.state["regions"] if r["id"] == ids[0])["recipe"]
            self.offset.setValue(value["offset"])
            self.units.setCurrentText(value["units"])
            self.join.setCurrentText(value["join"])
            self.simplify.setValue(value["simplify"])
            self.holes.setChecked(value["cut_holes"])
        self._vertices()

    def refresh(self, selected=None):
        if not self.document:
            return
        ids = set(selected if selected is not None else self.selected_ids())
        self._syncing = True
        self.view.clear_drawing()
        for item in [*self.outlines.values(), *self.overlay_items, *self.handles]:
            self.view.scene().removeItem(item)
        self.outlines, self.overlay_items, self.handles = {}, [], []
        self.region_list.clear()
        state = self.document.state
        for index, region in enumerate(state["regions"]):
            if region["status"] == "rejected" and not self.show_rejected.isChecked():
                continue
            if region["status"] == "proposed" and not polygon(state["work_area"]).covers(polygon(region["geometry"])):
                continue
            status = "reviewed" if region["reviewed"] else region["status"]
            text = f"{index+1}. {region['label']} · {status}"
            entry = QListWidgetItem(text)
            entry.setData(Qt.ItemDataRole.UserRole, region["id"])
            self.region_list.addItem(entry)
            color = "#78ff75" if region["reviewed"] else "#46dce8" if region["status"] == "selected" else "#ffbc42" if region["status"] == "proposed" else "#9b9b9b"
            item = QGraphicsPathItem(painter_path(polygon(region["geometry"])))
            item.setPen(cosmetic_pen(color))
            item.setBrush(Qt.BrushStyle.NoBrush)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            item.setData(0, region["id"])
            item.setZValue(2)
            self.view.scene().addItem(item)
            self.outlines[region["id"]] = item
        work = self.view.scene().addPath(painter_path(polygon(state["work_area"])), cosmetic_pen("#dddddd", 1))
        work.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.overlay_items.append(work)
        result = build_cut_paths(self.document)
        profile = calibration_from_dict(state["calibration"]) if state["calibration"] else None
        for path in result["paths"]:
            geometry = polygon(path["geometry"])
            if profile:
                geometry = map_geometry(geometry, profile.mm_to_pixels)
            item = self.view.scene().addPath(painter_path(geometry), cosmetic_pen("#78ff75", 1.5))
            item.setZValue(3)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.overlay_items.append(item)
        messages = [d["message"] for d in result["diagnostics"]]
        prefix = "Uncalibrated · pixel preview. Millimeter borders are pending.\n" if not profile else f"{profile.reference_kind.title()} calibration · {profile.validation.status}.\n"
        self.diagnostics.setText(prefix + ("\n".join(messages[:12]) or "No geometry issues detected."))
        self._syncing = False
        self._select(ids)

    def edit(self, command, *, expected_revision=None):
        if not self.document:
            return
        try:
            ids = self.document.apply(command, expected_revision=expected_revision)
            self.warnings_reviewed.setChecked(False)
            self.refresh(ids if ids else None)
            self.autosave.start(250)
            self.statusBar().showMessage(f"Edit applied · revision {self.document.revision}")
        except (ValueError, KeyError, TypeError) as exc:
            self._error(exc)

    def save(self):
        if self.document and self.document_path:
            try:
                self.document.save(self.document_path)
                self.statusBar().showMessage(f"Saved {self.document_path}")
                return True
            except (OSError, ValueError) as exc:
                self._error(exc)
                return False
        return True

    def _save_as(self):
        if self.document:
            path, _ = QFileDialog.getSaveFileName(self, "Save review job to a new file", str(self.document_path), "Review job (*.json)")
            if path:
                previous = self.document_path
                self.document_path = Path(path)
                if not self.save():
                    self.document_path = previous

    def _apply_recipe(self):
        self.edit({"op": "recipe", "ids": self.selected_ids(), "recipe": {
            "offset": self.offset.value(), "units": self.units.currentText(), "join": self.join.currentText(),
            "simplify": self.simplify.value(), "cut_holes": self.holes.isChecked()}})

    def _mode_changed(self, mode):
        self.view.set_mode(mode)
        self._vertices()
        if mode == "Polygon":
            self.statusBar().showMessage("Click corners; double-click or Enter to finish. Escape cancels.")

    def _vertices(self):
        for handle in self.handles:
            self.view.scene().removeItem(handle)
        self.handles = []
        ids = self.selected_ids()
        if self.view.mode != "Edit vertices" or len(ids) != 1 or not self.document:
            return
        region = next(r for r in self.document.state["regions"] if r["id"] == ids[0])
        geometry = polygon(region["geometry"])
        parts = components(geometry)
        revision = self.document.revision
        # Vertex handles are deliberately limited for dense masks; simplify first.
        count = sum(len(r.coords)-1 for p in parts for r in [p.exterior, *p.interiors])
        if count > 1200:
            self.statusBar().showMessage("Outline has many vertices; simplify it before editing vertices.")
            return
        for pi, part in enumerate(parts):
            for ri, ring in enumerate([part.exterior, *part.interiors]):
                for vi, point in enumerate(list(ring.coords)[:-1]):
                    def moved(value, part_index=pi, ring_index=ri, vertex_index=vi):
                        edited = []
                        for i, p in enumerate(parts):
                            rings = [list(r.coords)[:-1] for r in [p.exterior, *p.interiors]]
                            if i == part_index:
                                rings[ring_index][vertex_index] = value
                            edited.append(Polygon(rings[0], rings[1:]))
                        outline = edited[0] if len(edited) == 1 else MultiPolygon(edited)
                        self.edit({"op": "replace", "ids": ids, "geometry": geojson(outline)}, expected_revision=revision)
                        self.refresh(ids)
                    handle = VertexHandle(point, moved)
                    self.view.scene().addItem(handle)
                    self.handles.append(handle)

    def _drawn(self, mode, points):
        if not self.document:
            return
        try:
            if mode in {"AI include", "AI exclude"}:
                self._refine(point=points, label=1 if mode == "AI include" else 0)
                return
            if mode in {"Rectangle", "Rounded rectangle", "Work area", "AI box"}:
                x1, x2 = sorted([points[0][0], points[-1][0]])
                y1, y2 = sorted([points[0][1], points[-1][1]])
                if min(x2-x1, y2-y1) < 2:
                    return
                if mode == "AI box":
                    self._refine(box_px=[x1, y1, x2, y2])
                    return
                geometry = box(x1, y1, x2, y2)
                if mode == "Rounded rectangle":
                    radius = min(x2-x1, y2-y1) * 0.1
                    geometry = box(x1+radius, y1+radius, x2-radius, y2-radius).buffer(radius, quad_segs=16)
                self.edit({"op": "work_area" if mode == "Work area" else "add", "geometry": geojson(geometry)})
            elif mode == "Split":
                self.edit({"op": "split", "ids": self.selected_ids(), "line": [points[0], points[-1]]})
            elif mode in {"Brush add", "Brush erase"}:
                stroke = LineString(points) if len(set(map(tuple, points))) > 1 else Point(points[0])
                self.edit({"op": "brush", "ids": self.selected_ids(), "mode": "add" if mode == "Brush add" else "erase",
                           "geometry": geojson(stroke.buffer(self.view.brush_radius, quad_segs=16))})
            else:
                self.edit({"op": "add", "geometry": geojson(Polygon(points))})
        except (ValueError, KeyError) as exc:
            self._error(exc)

    def _load_calibration(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load calibration", "", "Calibration (*.json)")
        if path:
            try:
                self.edit({"op": "calibration", "profile": load_calibration(Path(path)).to_dict()})
            except (OSError, ValueError) as exc:
                self._error(exc)

    def _approve(self):
        if self.document:
            try:
                self.document.approve(acknowledge_warnings=self.warnings_reviewed.isChecked())
                self.save()
                self.statusBar().showMessage("Exact physical paths approved for export.")
            except ValueError as exc:
                self._error(exc)

    def _export(self):
        if not self.document:
            return
        try:
            self.document.approved_snapshot()
            path, _ = QFileDialog.getSaveFileName(self, "Export approved cuts", str(self.document_path.parent / "cuts.svg"), "SVG (*.svg)")
            if path and self.save():
                from sticker_maker.export import export_svg
                result = export_svg(self.document_path, Path(path))
                self.statusBar().showMessage(f"Exported {result['svg']}; check the placement instructions beside it.")
        except (OSError, ValueError) as exc:
            self._error(exc)

    def _capture(self):
        try:
            if self.document and not self.save():
                return
            self.tasks.submit({"op": "capture", "output_dir": str(Path("data/captures").resolve())})
            self.statusBar().showMessage("Capturing and downloading… Keep the camera connected.")
        except ValueError as exc:
            self._error(exc)

    def _propose(self):
        if self.document:
            self._submit({"op": "propose", "image": self.document.source["path"], "work_area": self.document.state["work_area"]})

    def _model_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose SAM 2.1 Small or Tiny weights", "output/models", "Model weights (*.pt)")
        if path:
            model = "tiny" if "tiny" in Path(path).name else "small"
            self.model_configuration = {"checkpoint": path, "model": model, "device": "auto"}
            self.prompts = {"box": None, "points": [], "labels": [], "region_id": None}
            self.statusBar().showMessage(f"AI selection configured: {model}")

    def _refine(self, *, box_px=None, point=None, label=1):
        if not self.model_configuration:
            raise RegionError("Choose AI model weights first. The README explains optional model setup.")
        if self.tasks.pending:
            raise RegionError("Wait for analysis or cancel it before adding another prompt.")
        ids = self.selected_ids()
        if len(ids) > 1 and box_px is None:
            raise RegionError("Select one region to refine, or clear selection to add a new one.")
        target = ids[0] if len(ids) == 1 and box_px is None else None
        if box_px is not None or target != self.prompts["region_id"]:
            self.prompts = {"box": box_px, "points": [], "labels": [], "region_id": target}
            if box_px is None and target:
                region = next(r for r in self.document.state["regions"] if r["id"] == target)
                self.prompts["box"] = list(polygon(region["geometry"]).bounds)
        if point is not None:
            self.prompts["points"].append(point)
            self.prompts["labels"].append(label)
        self._submit({"op": "refine", "image": self.document.source["path"], "configuration": self.model_configuration,
                      "asset_dir": str(self.document_path.parent / "masks"), **self.prompts})

    def _submit(self, task):
        try:
            task |= {"job_id": self.document.job_id, "revision": self.document.revision}
            self.tasks.submit(task)
            self.statusBar().showMessage("Analyzing image… You can keep editing; changed jobs discard old results.")
        except ValueError as exc:
            self._error(exc)

    def _poll(self):
        reply = self.tasks.poll()
        if reply is None:
            return
        if "error" in reply:
            self._error(reply["error"])
            return
        task, result = reply["task"], reply["result"]
        if task["op"] == "capture":
            self.open_path(Path(result["image"]))
            warnings = result["metadata"].get("warnings", [])
            if warnings:
                self._error("Capture completed: " + "; ".join(warnings))
            self._propose()
            return
        if not self.document or task["job_id"] != self.document.job_id or task["revision"] != self.document.revision:
            self.statusBar().showMessage("Analysis discarded because the review job changed. Run it again if needed.")
            return
        if task["op"] == "propose":
            self.edit({"op": "proposals", "source_sha256": result["source_sha256"], "proposals": result["proposals"]},
                      expected_revision=task["revision"])
        else:
            # Refinement arrives as a replacement proposal; accepted edits remain intact.
            if task.get("region_id"):
                result["proposal"]["origin"]["parents"] = [task["region_id"]]
            self.edit({"op": "proposals", "source_sha256": result["source_sha256"], "proposals": [result["proposal"]]},
                      expected_revision=task["revision"])
            self.prompts["region_id"] = self.selected_ids()[0] if self.selected_ids() else None

    def _cancel(self):
        if self.tasks.pending and self.tasks.pending["op"] == "capture":
            self.statusBar().showMessage("Capture is in progress. Keep the camera connected until transfer finishes.")
            return
        self.tasks.close()
        self.statusBar().showMessage("Analysis cancelled.")

    def closeEvent(self, event):
        if self.tasks.pending and self.tasks.pending["op"] == "capture":
            self.statusBar().showMessage("Wait for camera transfer/cleanup before closing.")
            event.ignore()
            return
        if not self.save():
            event.ignore()
            return
        self.tasks.close()
        super().closeEvent(event)


def run_review(input_path=None, *, output=None):
    application = QApplication.instance() or QApplication(sys.argv[:1])
    window = ReviewWindow(input_path, output=output)
    window.show()
    application.exec()
