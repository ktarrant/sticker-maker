"""Reproducible official SAM checkpoint downloads; no Torch import required."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import urllib.request

MODELS = {
    "small": {"filename": "sam2.1_hiera_small.pt", "size": 184416285,
              "sha256": "6d1aa6f30de5c92224f8172114de081d104bbd23dd9dc5c58996f0cad5dc4d38"},
    "tiny": {"filename": "sam2.1_hiera_tiny.pt", "size": 156008466,
             "sha256": "7402e0d864fa82708a20fbd15bc84245c2f26dff0eb43a4b5b93452deb34be69"},
}
BASE_URL = "https://dl.fbaipublicfiles.com/segment_anything_2/092824/"


def verify_model(path: Path, model: str) -> dict:
    spec = MODELS[model]
    path = Path(path)
    if path.stat().st_size != spec["size"]:
        raise ValueError(f"{path}: not the expected official SAM 2.1 {model} checkpoint (size mismatch).")
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if checksum != spec["sha256"]:
        raise ValueError(f"{path}: checkpoint SHA-256 mismatch; obtain verified official weights with models fetch.")
    return {"path": str(path.resolve()), "model": model, "sha256": checksum, "bytes": spec["size"]}


def fetch_model(directory: Path, model="small") -> dict:
    spec = MODELS[model]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / spec["filename"]
    if destination.exists():
        return verify_model(destination, model)
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".download", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            count = 0
            with urllib.request.urlopen(BASE_URL + spec["filename"], timeout=60) as response:
                while block := response.read(1024 * 1024):
                    count += len(block)
                    if count > spec["size"]:
                        raise ValueError("Checkpoint download exceeds the expected file size.")
                    stream.write(block)
            stream.close()
            verify_model(temporary, model)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return verify_model(destination, model)
