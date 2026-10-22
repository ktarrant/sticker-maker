"""Replaceable image proposal providers. OpenCV and model imports stay optional."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon, box

from sticker_maker.regions import RegionError, geojson, image_identity, polygon


def read_rgb(path: Path, *, max_side: int | None = None):
    with Image.open(path) as image:
        image.seek(0)
        image = image.convert("RGB")
        original_size = image.size
        if max_side:
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        return image, original_size


class OpenCVRecognizer:
    def propose(self, image_path: Path, *, work_area=None, max_candidates=60, max_side=1600):
        try:
            import cv2
        except ImportError as exc:
            raise RegionError("Install parser dependencies with: uv sync --extra parser") from exc
        started = time.perf_counter()
        image, original_size = read_rgb(image_path, max_side=max_side)
        rgb = np.asarray(image)
        sx, sy = original_size[0] / image.width, original_size[1] / image.height
        area = polygon(work_area) if work_area else box(0, 0, *original_size)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        contrast = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        edges = cv2.Canny(contrast, 45, 120)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        light = cv2.inRange(hsv, (0, 0, 75), (179, 95, 255))
        light = cv2.morphologyEx(light, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        candidates = []
        for mask in (edges, light):
            contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                size = cv2.contourArea(contour) * sx * sy
                if size < max(100, area.area * 0.001) or size > area.area * 0.65:
                    continue
                perimeter = cv2.arcLength(contour, True)
                outline = cv2.approxPolyDP(contour, max(1.0, 0.008 * perimeter), True)
                if not 4 <= len(outline) <= 16:
                    continue
                (_, _), (width, height), _ = cv2.minAreaRect(contour)
                if min(width, height) < 8 or max(width, height) / min(width, height) > 12:
                    continue
                fill = cv2.contourArea(contour) / max(width * height, 1)
                if fill < 0.68:
                    continue
                interior = np.zeros(gray.shape, np.uint8)
                cv2.drawContours(interior, [contour], -1, 255, cv2.FILLED)
                interior = cv2.erode(interior, np.ones((7, 7), np.uint8))
                if cv2.countNonZero(interior) < 16:
                    continue
                _, variation = cv2.meanStdDev(gray, mask=interior)
                if float(variation[0, 0]) < 8:
                    continue  # Empty mat-grid cells and plain background are poor sticker proposals.
                geometry = Polygon([(float(p[0][0]) * sx, float(p[0][1]) * sy) for p in outline])
                if not geometry.is_valid or not area.covers(geometry):
                    continue
                score = fill * (1.0 if len(outline) <= 8 else 0.9) * (0.8 + 0.2 * min(float(variation[0, 0]) / 40, 1))
                candidates.append((score, geometry))
        kept = []
        for score, geometry in sorted(candidates, key=lambda item: item[0], reverse=True):
            if any(geometry.intersection(previous).area / geometry.union(previous).area > 0.80 for _, previous in kept):
                continue
            kept.append((score, geometry))
            if len(kept) >= max_candidates:
                break
        kept.sort(key=lambda item: (item[1].centroid.y, item[1].centroid.x))
        proposals = [{"geometry": geojson(g), "label": f"Panel {i+1}",
                      "origin": {"provider": "opencv", "version": cv2.__version__, "score": float(score),
                                 "preview_size_px": list(image.size), "preview_to_source_scale": [sx, sy]}}
                     for i, (score, g) in enumerate(kept)]
        return {"source_sha256": image_identity(image_path)["sha256"], "proposals": proposals,
                "elapsed_seconds": time.perf_counter() - started}


def save_overlay(document, path: Path, *, max_side=1800):
    """Portable review image; coordinates remain bound to the original raster."""
    if Path(path).resolve() == Path(document.source["path"]).resolve():
        raise RegionError("Save the review overlay to a different file; preserve the original image.")
    from sticker_maker.geometry import build_cut_paths, components, map_geometry
    from sticker_maker.calibration import calibration_from_dict
    image, size = read_rgb(document.source["path"], max_side=max_side)
    sx, sy = image.width / size[0], image.height / size[1]
    draw = ImageDraw.Draw(image)
    for index, region in enumerate(document.state["regions"]):
        if region["status"] == "rejected":
            continue
        if region["status"] == "proposed" and not polygon(document.state["work_area"]).covers(polygon(region["geometry"])):
            continue
        color = "#46dce8" if region["status"] == "selected" else "#ffbc42"
        geometry = polygon(region["geometry"])
        for part in components(geometry):
            points = [(x*sx, y*sy) for x, y in part.exterior.coords]
            draw.line(points, fill=color, width=2)
        draw.text((geometry.bounds[0]*sx, geometry.bounds[1]*sy), str(index+1), fill=color,
                  stroke_width=1, stroke_fill="black")
    result = build_cut_paths(document)
    profile = calibration_from_dict(document.state["calibration"]) if document.state["calibration"] else None
    for path_data in result["paths"]:
        geometry = polygon(path_data["geometry"])
        if profile:
            geometry = map_geometry(geometry, profile.mm_to_pixels)
        for part in components(geometry):
            for ring in [part.exterior, *part.interiors]:
                draw.line([(x*sx, y*sy) for x, y in ring.coords], fill="#78ff75", width=2)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def parse_image(image_path: Path, output: Path, *, work_area=None):
    from sticker_maker.regions import RegionDocument
    document = RegionDocument.create(image_path)
    if work_area:
        document.apply({"op": "work_area", "geometry": work_area})
    revision = document.revision
    result = OpenCVRecognizer().propose(image_path, work_area=document.state["work_area"])
    document.apply({"op": "proposals", "source_sha256": result["source_sha256"],
                    "proposals": result["proposals"]}, expected_revision=revision)
    document.save(output)
    overlay = Path(output).with_name(Path(output).stem + "-overlay.png")
    save_overlay(document, overlay)
    return {"document": str(Path(output).resolve()), "overlay": str(overlay.resolve()),
            "proposal_count": len(result["proposals"]), "proposal_seconds": result["elapsed_seconds"]}
