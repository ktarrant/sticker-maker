"""Serialize approved millimeter paths without camera or recognition dependencies."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from sticker_maker.geometry import components
from sticker_maker.regions import RegionDocument, RegionError, atomic_json, polygon

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def svg_bytes(snapshot: dict) -> bytes:
    width, height = snapshot["mat_size_mm"]
    svg = ET.Element(f"{{{SVG_NS}}}svg", {"width": f"{width}mm", "height": f"{height}mm",
                                          "viewBox": f"0 0 {width} {height}", "version": "1.1"})
    group = ET.SubElement(svg, f"{{{SVG_NS}}}g", {"id": "sticker-cuts", "fill": "#000000", "fill-rule": "evenodd"})
    for path in snapshot["paths"]:
        commands = []
        for part in components(polygon(path["geometry"])):
            for ring in [part.exterior, *part.interiors]:
                points = list(ring.coords)[:-1]
                commands.append(f"M {points[0][0]!r} {points[0][1]!r}")
                commands.extend(f"L {x!r} {y!r}" for x, y in points[1:])
                commands.append("Z")
        element = ET.SubElement(group, f"{{{SVG_NS}}}path", {"id": "region-" + path["region_id"], "d": " ".join(commands)})
        ET.SubElement(element, f"{{{SVG_NS}}}title").text = path["label"]
    return ET.tostring(svg, encoding="utf-8", xml_declaration=True) + b"\n"


def export_svg(document_path: Path, destination: Path) -> dict:
    document = RegionDocument.load(document_path, verify_image=False)
    destination = Path(destination).resolve()
    protected = {Path(document_path).resolve(), Path(document.source["path"]).resolve()}
    if destination.suffix.lower() != ".svg" or {destination, destination.with_suffix(".placement.json")} & protected:
        raise RegionError("Choose a .svg output path that preserves the review job and original image.")
    if Path(document.source["path"]).exists():
        document.verify_image()
    snapshot = document.approved_snapshot()
    data = svg_bytes(snapshot)  # No geometry rebuilding, scaling, or segmentation.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".svg.tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    bounds = [polygon(p["geometry"]).bounds for p in snapshot["paths"]]
    report = {"schema_version": 1, "svg": str(destination.resolve()), "svg_sha256": hashlib.sha256(data).hexdigest(),
              "approval_fingerprint": document.to_dict()["approval"]["fingerprint"], "units": "mm",
              "mat_size_mm": snapshot["mat_size_mm"],
              "cut_bounds_mm": [min(b[0] for b in bounds), min(b[1] for b in bounds),
                                max(b[2] for b in bounds), max(b[3] for b in bounds)],
              "placement_instructions": ["Import as Basic Cut in Cricut Design Space; verify imported dimensions.",
                 "Attach all cut shapes to preserve their relative arrangement.",
                 "Set and verify absolute group placement on the Prepare mat; SVG whitespace is not a registration guarantee.",
                 "Keep the original sheet on the same mat; use the validated loading orientation and material settings.",
                 "Validate placement with a pen or scrap before relying on blade alignment."]}
    report_path = destination.with_suffix(".placement.json")
    atomic_json(report_path, report)
    return {"svg": str(destination.resolve()), "placement": str(report_path.resolve()), "region_count": len(snapshot["paths"])}
