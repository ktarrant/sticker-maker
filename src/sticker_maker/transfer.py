"""Copy a review job and its verified original/mask assets into a portable folder."""

import hashlib
from pathlib import Path
import shutil
import tempfile

from sticker_maker.regions import RegionDocument, RegionError, atomic_json


def bundle_job(document_path: Path, destination: Path, *, image: Path | None = None) -> dict:
    document_path, destination = Path(document_path).resolve(), Path(destination).resolve()
    if destination.exists():
        raise RegionError("Choose a new bundle directory; existing files are preserved.")
    document = RegionDocument.load(document_path, image=image)
    data = document.to_dict()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".sticker-bundle-", dir=destination.parent))
    try:
        original = Path(document.source["path"])
        image_name = "image" + original.suffix.lower()
        shutil.copyfile(original, temporary / image_name)
        data["source"]["path"] = str(temporary / image_name)
        masks = set()
        for state in [data["state"], *data["undo"], *data["redo"]]:
            for region in state["regions"]:
                origin = region["origin"]
                if not origin.get("mask_asset"):
                    continue
                source = Path(origin["mask_asset"])
                if not source.is_absolute():
                    source = document_path.parent / source
                checksum = origin.get("mask_sha256")
                with source.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if checksum != actual:
                    raise RegionError(f"Mask asset checksum changed: {source}")
                relative = f"masks/{checksum}.png"
                (temporary / "masks").mkdir(exist_ok=True)
                if relative not in masks:
                    shutil.copyfile(source, temporary / relative)
                    masks.add(relative)
                origin["mask_asset"] = relative
        # Asset locations changed: retain content review, require a fresh physical
        # approval of the copied job, and leave the source job untouched.
        data["revision"] += 1
        data["approval"] = None
        copied = RegionDocument(data)
        copied.verify_image()
        copied.save(temporary / "regions.json")
        capture = original.parent / "capture.json"
        if capture.exists():
            import json
            metadata = json.loads(capture.read_text())
            if metadata.get("image", {}).get("sha256") == document.source["sha256"]:
                shutil.copyfile(capture, temporary / "capture.json")
        atomic_json(temporary / "bundle.json", {"schema_version": 1, "source_sha256": document.source["sha256"],
                    "mask_assets": sorted(masks), "physical_approval": "requires_review"})
        temporary.replace(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {"document": str(destination / "regions.json"), "image": str(destination / image_name), "mask_count": len(masks)}
