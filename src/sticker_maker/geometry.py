"""Deterministic cut paths shared by review and export, independent of recognition."""

from __future__ import annotations

import math

from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

from sticker_maker.calibration import calibration_from_dict
from sticker_maker.regions import geojson, polygon


def components(geometry):
    return list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]


def map_geometry(geometry, mapper):
    parts = [Polygon(mapper(list(p.exterior.coords)), [mapper(list(r.coords)) for r in p.interiors])
             for p in components(geometry)]
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def check_calibration(source, profile):
    profile.assert_compatible(image_size_px=tuple(source["image_size_px"]), rig_id=profile.rig_id,
                              exif_orientation=source["exif_orientation"], pixel_space=source["pixel_space"],
                              lens_profile_id=source["lens_profile_id"],
                              source_image_sha256=(None if profile.reference_kind == "synthetic"
                                                   and profile.source_image_sha256 is None else source["sha256"]))


def _topology(geometry):
    return len(components(geometry)), sum(len(p.interiors) for p in components(geometry))


def build_cut_paths(document, *, physical=False) -> dict:
    state, source = document.state, document.source
    diagnostics, paths = [], []
    profile = calibration_from_dict(state["calibration"]) if state["calibration"] else None
    units = "mm" if profile else "px"

    def issue(code, message, ids=(), level="error"):
        diagnostics.append({"code": code, "level": level, "region_ids": list(ids), "message": message})

    if profile:
        check_calibration(source, profile)
    if physical:
        if profile is None:
            issue("calibration_required", "Attach measured calibration before physical approval.")
        else:
            try:
                profile.require_measured_validation()
            except ValueError as exc:
                issue("calibration_unvalidated", str(exc))
    width, height = source["image_size_px"]
    sheet = polygon(state["work_area"])
    if not box(0, 0, width, height).buffer(1e-7).covers(sheet):
        issue("work_area_bounds", "Work area extends beyond the original image.")
    if profile:
        sheet = map_geometry(sheet, profile.pixels_to_mm)
    active = [r for r in state["regions"] if r["status"] == "selected"]
    if not active:
        issue("empty_job", "Select at least one sticker.")
    for region in active:
        identifier = region["id"]
        if region["origin"].get("discarded_degenerate_components", 0):
            issue("untraceable_mask_components", f"{region['label']}: model contained tiny components that could not form cut outlines.",
                  [identifier], "warning")
        if physical and not region["reviewed"]:
            issue("unreviewed", f"Review {region['label']} before physical approval.", [identifier])
        geometry = polygon(region["geometry"])
        settings = region["recipe"]
        if profile:
            geometry = map_geometry(geometry, profile.pixels_to_mm)
        if not settings["cut_holes"]:
            geometry = unary_union([Polygon(p.exterior) for p in components(geometry)])
        before = _topology(geometry)
        mismatch = settings["units"] != units and (settings["offset"] != 0 or settings["simplify"] != 0)
        if mismatch:
            issue("pending_units", f"{region['label']}: {settings['units']} border/tolerance is pending a matching coordinate space.",
                  [identifier], "error" if physical else "warning")
        else:
            distance = settings["offset"]
            if distance:
                tolerance = 0.02 if units == "mm" else 0.2
                angle = math.acos(max(-1.0, 1 - min(tolerance / abs(distance), 1)))
                segments = max(8, min(4096, math.ceil(math.pi / (4 * angle))))
                geometry = geometry.buffer(distance, quad_segs=segments, join_style=settings["join"])
            if geometry.is_empty:
                issue("empty_offset", f"{region['label']}: shrinking removed the whole sticker.", [identifier])
                continue
            if settings["simplify"]:
                simplified = geometry.simplify(settings["simplify"], preserve_topology=True)
                if geometry.boundary.hausdorff_distance(simplified.boundary) > settings["simplify"] + 1e-7:
                    issue("simplification_error", "Simplification exceeded its deviation limit.", [identifier])
                geometry = simplified
        if not geometry.is_valid or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            issue("invalid_geometry", f"{region['label']}: resulting outline is invalid.", [identifier])
            continue
        if _topology(geometry) != before:
            issue("topology_change", f"{region['label']}: border/shape changes altered components or holes.", [identifier], "warning")
        if not sheet.buffer(1e-7).covers(geometry):
            issue("sheet_bounds", f"{region['label']}: cut extends beyond the work area.", [identifier])
        if profile and not box(0, 0, *profile.mat_size_mm).buffer(1e-7).covers(geometry):
            issue("mat_bounds", f"{region['label']}: cut extends beyond the calibrated mat.", [identifier])
        feature = state["minimum_feature_mm"] if profile else 0
        if feature:
            eroded = geometry.buffer(-feature / 2)
            if eroded.is_empty or _topology(eroded)[0] != _topology(geometry)[0]:
                issue("narrow_feature", f"{region['label']}: feature/bridge may be narrower than {feature:g} mm.", [identifier], "warning")
        paths.append({"region_id": identifier, "label": region["label"], "geometry": geojson(geometry)})
    for index, left in enumerate(paths):
        a = polygon(left["geometry"])
        for right in paths[index+1:]:
            b = polygon(right["geometry"])
            ids = [left["region_id"], right["region_id"]]
            if a.intersection(b).area > 1e-8 or a.boundary.intersection(b.boundary).length > 1e-7:
                issue("overlap", f"{left['label']} and {right['label']} overlap or share a cut edge.", ids)
            elif profile and a.distance(b) < state["minimum_clearance_mm"] - 1e-7:
                issue("clearance", f"{left['label']} and {right['label']} are too close.", ids)
    return {"units": units, "profile_id": profile.profile_id if profile else None,
            "mat_size_mm": list(profile.mat_size_mm) if profile else None,
            "paths": paths, "diagnostics": diagnostics}
