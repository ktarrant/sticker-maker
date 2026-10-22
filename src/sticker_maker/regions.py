"""Versioned, camera-free region documents and reversible editing commands."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import fcntl
import json
import math
from pathlib import Path
import tempfile
from uuid import uuid4

from PIL import Image
from shapely.errors import GEOSException
from shapely.geometry import LineString, box, mapping, shape
from shapely.ops import split, unary_union

HISTORY_LIMIT = 40
DEFAULT_RECIPE = {"offset": 0.0, "units": "mm", "join": "round", "simplify": 0.0,
                  "cut_holes": False}


class RegionError(ValueError):
    """Invalid region document, edit, or stale result."""


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def atomic_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def image_identity(path: Path) -> dict:
    path = Path(path).resolve()
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    with Image.open(path) as image:
        image.seek(0)  # Sony MPO: retain the original primary raster convention.
        image.load()
        return {"path": str(path), "sha256": checksum, "frame_index": 0,
                "image_size_px": list(image.size), "exif_orientation": image.getexif().get(274, 1),
                "pixel_space": "raw", "lens_profile_id": None}


def polygon(value):
    try:
        geometry = shape(value)
        coordinates = mapping(geometry)
        # JSON forbids NaN/Infinity, including coordinates accepted by GEOS.
        json.dumps(coordinates, allow_nan=False)
        if geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid:
            raise RegionError("Outline must be a nonempty, valid Polygon or MultiPolygon.")
        if not math.isfinite(geometry.area) or geometry.area <= 0:
            raise RegionError("Outline must have positive finite area.")
        return geometry
    except (KeyError, TypeError, ValueError, GEOSException) as exc:
        raise RegionError(f"Invalid outline: {exc}") from exc


def geojson(geometry) -> dict:
    return json.loads(json.dumps(mapping(geometry), allow_nan=False))


def recipe(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - set(DEFAULT_RECIPE):
        raise RegionError("Unknown cut recipe field.")
    result = DEFAULT_RECIPE | value
    if result["units"] not in {"px", "mm"} or result["join"] not in {"round", "mitre", "bevel"}:
        raise RegionError("Recipe requires px/mm units and round/mitre/bevel joins.")
    for key in ("offset", "simplify"):
        if isinstance(result[key], bool) or not isinstance(result[key], (int, float)) or not math.isfinite(result[key]):
            raise RegionError(f"{key} must be a finite number.")
    if result["simplify"] < 0 or type(result["cut_holes"]) is not bool:
        raise RegionError("Simplification must be nonnegative and cut_holes boolean.")
    return result


class RegionDocument:
    """All mutations pass through commands; snapshots and stale-result checks are shared by UI/CLI."""

    def __init__(self, data: dict):
        self._data = deepcopy(data)
        self._saved_hashes = {}
        self._validate()

    @classmethod
    def create(cls, image: Path) -> RegionDocument:
        source = image_identity(image)
        width, height = source["image_size_px"]
        return cls({"schema_version": 1, "job_id": uuid4().hex, "revision": 0,
                    "source": source, "state": {"regions": [], "groups": {},
                    "work_area": geojson(box(0, 0, width, height)), "calibration": None,
                    "minimum_clearance_mm": 0.0, "minimum_feature_mm": 0.0},
                    "undo": [], "redo": [], "approval": None})

    @classmethod
    def load(cls, path: Path, *, image: Path | None = None, verify_image: bool = True) -> RegionDocument:
        try:
            path = Path(path).resolve()
            raw = path.read_bytes()
            result = cls(json.loads(raw))
            result._saved_hashes[path] = hashlib.sha256(raw).hexdigest()
            if verify_image:
                result.verify_image(image)
            return result
        except (KeyError, TypeError, ValueError) as exc:
            raise RegionError(f"Invalid region document: {exc}") from exc

    def _validate(self):
        data = self._data
        if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise RegionError("Expected region schema_version 1.")
        if type(data.get("revision")) is not int or data["revision"] < 0:
            raise RegionError("Invalid document revision.")
        if not isinstance(data.get("job_id"), str) or not data["job_id"]:
            raise RegionError("Invalid job identity.")
        source = data["source"]
        if (not isinstance(source["sha256"], str) or len(source["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in source["sha256"])):
            raise RegionError("Invalid source image fingerprint.")
        if (len(source["image_size_px"]) != 2 or any(type(v) is not int or v <= 0 for v in source["image_size_px"])
                or type(source["frame_index"]) is not int or source["frame_index"] != 0
                or type(source["exif_orientation"]) is not int or source["exif_orientation"] not in range(1, 9)
                or source["pixel_space"] != "raw" or source["lens_profile_id"] is not None):
            raise RegionError("Unsupported source coordinate frame; use the unrotated primary raster.")
        for state in [data["state"], *data["undo"], *data["redo"]]:
            self._validate_state(state)
        json.dumps(data, allow_nan=False)

    def _validate_state(self, state):
        polygon(state["work_area"])
        ids = set()
        for region in state["regions"]:
            if not isinstance(region["id"], str) or not region["id"] or region["id"] in ids:
                raise RegionError("Region IDs must be nonempty and unique.")
            ids.add(region["id"])
            polygon(region["geometry"])
            polygon(region["baseline_geometry"])
            recipe(region["recipe"])
            if region["status"] not in {"proposed", "selected", "rejected"} or type(region["reviewed"]) is not bool:
                raise RegionError("Invalid region review state.")
        for members in state["groups"].values():
            if len(members) < 2 or len(set(members)) != len(members) or set(members) - ids:
                raise RegionError("Invalid region group.")
        for key in ("minimum_clearance_mm", "minimum_feature_mm"):
            value = state[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise RegionError(f"{key} must be finite and nonnegative.")
        if state["calibration"] is not None:
            from sticker_maker.calibration import calibration_from_dict
            calibration_from_dict(state["calibration"])

    @property
    def revision(self):
        return self._data["revision"]

    @property
    def job_id(self):
        return self._data["job_id"]

    @property
    def source(self):
        return deepcopy(self._data["source"])

    @property
    def state(self):
        return deepcopy(self._data["state"])

    def to_dict(self):
        return deepcopy(self._data)

    def verify_image(self, image: Path | None = None):
        actual = image_identity(image or self.source["path"])
        if any(actual[key] != value for key, value in self.source.items() if key != "path"):
            raise RegionError("Source image/frame changed; start a new job for a different image.")
        self._data["source"]["path"] = actual["path"]

    def save(self, path: Path):
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        # A stable sidecar lock plus the last observed bytes prevents a CLI edit
        # and GUI autosave from silently overwriting each other's work.
        with path.with_suffix(path.suffix + ".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RegionError("Review job is being saved by another process; retry shortly.") from exc
            current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            if current != self._saved_hashes.get(path):
                raise RegionError("Review job changed on disk. Reopen it, or save your edits to a new file.")
            atomic_json(path, self._data)
            self._saved_hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()

    def fingerprint(self):
        source = self.source
        source.pop("path")  # Relocating the identical image does not change its geometry.
        return digest({"source": source, "state": self.state, "revision": self.revision,
                       "job_id": self._data["job_id"]})

    def _commit(self, state):
        self._validate_state(state)
        self._data["undo"] = (self._data["undo"] + [self.state])[-HISTORY_LIMIT:]
        self._data["redo"] = []
        self._data["state"] = state
        self._data["revision"] += 1
        self._data["approval"] = None

    def undo(self):
        self._travel("undo", "redo")

    def redo(self):
        self._travel("redo", "undo")

    def _travel(self, source, target):
        if not self._data[source]:
            raise RegionError(f"Nothing to {source}.")
        self._data[target] = (self._data[target] + [self.state])[-HISTORY_LIMIT:]
        self._data["state"] = self._data[source].pop()
        self._data["revision"] += 1  # Never reuse a revision: old inference stays stale after undo.
        self._data["approval"] = None

    def apply(self, command: dict, *, expected_revision: int | None = None) -> list[str]:
        if expected_revision is not None and expected_revision != self.revision:
            raise RegionError("Stale edit/result: document revision changed.")
        if command.get("op") in {"undo", "redo"}:
            getattr(self, command["op"])()
            return []
        state = self.state
        regions = {r["id"]: r for r in state["regions"]}
        op = command.get("op")
        ids = command.get("ids", [])
        if len(ids) != len(set(ids)) or set(ids) - set(regions):
            raise RegionError("Select existing, distinct region IDs.")
        selected = [regions[i] for i in ids]
        result = list(ids)

        def add(geometry, *, label="Region", origin=None, settings=None, status="selected", identifier=None):
            identifier = identifier or uuid4().hex
            if identifier in regions:
                raise RegionError("Region ID already exists.")
            outline = geojson(polygon(geometry))
            region = {"id": identifier, "label": str(label), "geometry": outline,
                      "baseline_geometry": deepcopy(outline), "origin": origin or {"provider": "manual"},
                      "recipe": recipe(settings or {}), "status": status, "reviewed": False}
            state["regions"].append(region)
            regions[identifier] = region
            return identifier

        if op == "add":
            result = [add(command["geometry"], label=command.get("label", "Manual region"),
                          settings=command.get("recipe"), identifier=command.get("id"))]
        elif op == "proposals":
            if command["source_sha256"] != self.source["sha256"]:
                raise RegionError("Stale recognition result: source image changed.")
            result = []
            for item in command["proposals"]:
                if item.get("origin", {}).get("provider") == "opencv" and any(
                        r["origin"].get("provider") == "opencv" and r["baseline_geometry"] == item["geometry"]
                        for r in state["regions"]):
                    continue
                origin = item.get("origin", {})
                parents = origin.get("parents", []) if origin.get("provider") == "sam2" else []
                inherited = regions[parents[0]]["recipe"] if len(parents) == 1 and parents[0] in regions else None
                result.append(add(item["geometry"], label=item.get("label", "Proposal"),
                                  origin=item.get("origin"), settings=inherited, status="proposed"))
        elif op in {"select", "accept", "reject", "review", "reset", "recipe", "replace", "brush"}:
            if not selected:
                raise RegionError("Select at least one region.")
            for region in selected:
                if op in {"select", "accept", "reject", "review"}:
                    if op in {"accept", "review"} and region["origin"].get("provider") == "sam2":
                        ancestry = list(region["origin"].get("parents", []))
                        visited = set()
                        while ancestry:
                            parent_id = ancestry.pop()
                            if parent_id in visited:
                                continue
                            visited.add(parent_id)
                            if parent_id in regions and parent_id not in ids:
                                regions[parent_id].update(status="rejected", reviewed=False)
                                if regions[parent_id]["origin"].get("provider") == "sam2":
                                    ancestry.extend(regions[parent_id]["origin"].get("parents", []))
                    region["status"] = "rejected" if op == "reject" else "selected"
                    region["reviewed"] = op == "review"
                else:
                    region["reviewed"] = False
                    if op == "reset":
                        region["geometry"] = deepcopy(region["baseline_geometry"])
                    elif op == "recipe":
                        region["recipe"] = recipe(region["recipe"] | command["recipe"])
                    elif op == "replace":
                        region["geometry"] = geojson(polygon(command["geometry"]))
                    else:
                        base, brush = polygon(region["geometry"]), polygon(command["geometry"])
                        if command["mode"] not in {"add", "erase"}:
                            raise RegionError("Brush mode must be add or erase.")
                        edited = base.union(brush) if command["mode"] == "add" else base.difference(brush)
                        region["geometry"] = geojson(polygon(geojson(edited)))
        elif op in {"union", "backing"}:
            if not selected or (op == "union" and len(selected) < 2):
                raise RegionError("Select regions to combine.")
            settings = command.get("recipe")
            if settings is None:
                if len({digest(r["recipe"]) for r in selected}) != 1:
                    raise RegionError("Combined regions have different recipes; choose the resulting recipe explicitly.")
                settings = selected[0]["recipe"]
            geometry = unary_union([polygon(r["geometry"]) for r in selected])
            if op == "backing":
                mode = command.get("mode", "rectangle")
                if mode not in {"rectangle", "hull"}:
                    raise RegionError("Backing mode must explicitly choose rectangle or hull.")
                geometry = geometry.envelope if mode == "rectangle" else geometry.convex_hull
            result = [add(geojson(geometry), label=command.get("label", "Combined region"), settings=settings,
                          origin={"provider": op, "parents": ids})]
            for region in selected:
                region.update(status="rejected", reviewed=False)
        elif op == "split":
            if len(selected) != 1:
                raise RegionError("Select one region to split.")
            parent = selected[0]
            geometry = polygon(parent["geometry"])
            if command.get("line"):
                try:
                    line = LineString(command["line"])
                    if not line.is_valid or line.is_empty or not math.isfinite(line.length):
                        raise RegionError("Split line must contain distinct finite image points.")
                    pieces = list(split(geometry, line).geoms)
                except GEOSException as exc:
                    raise RegionError(f"Invalid split line: {exc}") from exc
            else:
                pieces = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
            if len(pieces) < 2:
                raise RegionError("Split line must cross the outline, or select disconnected components.")
            result = [add(geojson(p), label=parent["label"] + f" {i+1}", settings=parent["recipe"],
                          origin={"provider": "split", "parents": ids}) for i, p in enumerate(pieces)]
            parent.update(status="rejected", reviewed=False)
        elif op == "group":
            if len(selected) < 2:
                raise RegionError("Select at least two regions to group.")
            for group in list(state["groups"]):
                if set(state["groups"][group]) & set(ids):
                    del state["groups"][group]
            state["groups"][uuid4().hex] = ids
        elif op == "ungroup":
            state["groups"] = {key: members for key, members in state["groups"].items() if not set(members) & set(ids)}
        elif op == "work_area":
            state["work_area"] = geojson(polygon(command["geometry"]))
        elif op == "calibration":
            from sticker_maker.geometry import check_calibration
            from sticker_maker.calibration import calibration_from_dict
            value = command.get("profile")
            if value is not None:
                check_calibration(self.source, calibration_from_dict(value))
            state["calibration"] = value
        elif op == "constraints":
            for key in ("minimum_clearance_mm", "minimum_feature_mm"):
                if key in command:
                    state[key] = command[key]
        else:
            raise RegionError(f"Unknown edit command: {op}")
        self._commit(state)
        return result

    def approve(self, *, acknowledge_warnings: bool = False):
        from sticker_maker.geometry import build_cut_paths
        self.verify_image()
        result = build_cut_paths(self, physical=True)
        errors = [d["message"] for d in result["diagnostics"] if d["level"] == "error"]
        if errors:
            raise RegionError("Cannot approve: " + "; ".join(errors))
        if any(d["level"] == "warning" for d in result["diagnostics"]) and not acknowledge_warnings:
            raise RegionError("Review geometry warnings before approving (acknowledge_warnings).")
        payload = {"input_fingerprint": self.fingerprint(), "snapshot": result}
        self._data["approval"] = payload | {"fingerprint": digest(payload)}

    def approved_snapshot(self):
        approval = self._data.get("approval")
        if not approval or approval["input_fingerprint"] != self.fingerprint():
            raise RegionError("Geometry has no current physical export approval.")
        payload = {k: approval[k] for k in ("input_fingerprint", "snapshot")}
        if digest(payload) != approval.get("fingerprint"):
            raise RegionError("Approved path fingerprint changed.")
        from sticker_maker.calibration import calibration_from_dict
        from sticker_maker.geometry import check_calibration
        profile = calibration_from_dict(self.state["calibration"])
        check_calibration(self.source, profile)
        profile.require_measured_validation()
        snapshot = approval["snapshot"]
        if snapshot["units"] != "mm" or not snapshot["paths"] or any(d["level"] == "error" for d in snapshot["diagnostics"]):
            raise RegionError("Invalid approved physical path snapshot.")
        for path in snapshot["paths"]:
            polygon(path["geometry"])
        return deepcopy(snapshot)
