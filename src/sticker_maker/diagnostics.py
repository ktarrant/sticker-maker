"""Read-only software preflight. Never open a USB camera or trigger capture."""

from __future__ import annotations

import importlib
from importlib import metadata
from pathlib import Path
import platform
import sys


def doctor(*, require_ai=False, models_dir=Path("output/models")) -> dict:
    checks = []
    system, machine, macos = platform.system(), platform.machine(), platform.mac_ver()[0]

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": str(detail)})

    intel = system == "Darwin" and machine == "x86_64"
    if system == "Darwin":
        minimum = 13 if intel else 14
        major = int(macos.split(".")[0]) if macos else 0
        add("macos", "pass" if major >= minimum else "fail", f"{machine} macOS {macos}; requires {minimum}+")
    else:
        add("platform", "warning", f"Hardware handoff targets macOS; this process runs on {system} {machine}.")
    add("python", "pass" if sys.version_info[:2] == (3, 12) else "fail",
        f"{platform.python_version()}; run scripts/setup-macos.sh for the supported Python 3.12 environment")
    packages = {}
    for distribution, module in [("gphoto2", "gphoto2"), ("numpy", "numpy"), ("pillow", "PIL.Image"),
                                 ("shapely", "shapely"), ("opencv-python-headless", "cv2"),
                                 ("PySide6-Essentials", "PySide6.QtWidgets")]:
        try:
            imported = importlib.import_module(module)
            packages[distribution] = metadata.version(distribution)
            add(distribution, "pass", packages[distribution])
            if distribution == "gphoto2":
                add("libgphoto2", "pass", imported.gp_library_version(imported.GP_VERSION_SHORT)[0])
        except (ImportError, OSError, RuntimeError, metadata.PackageNotFoundError) as exc:
            add(distribution, "fail", exc)
    device = None
    try:
        torch = importlib.import_module("torch")
        importlib.import_module("sam2.sam2_image_predictor")
        packages["torch"] = torch.__version__
        packages["torchvision"] = metadata.version("torchvision")
        device = "mps" if torch.backends.mps.is_available() and not intel else "cpu"
        add("sam_runtime", "pass", f"Torch {torch.__version__}; {device}")
        from sticker_maker.models import MODELS, verify_model
        weights = verify_model(Path(models_dir) / MODELS["small"]["filename"], "small")
        add("sam_weights", "pass", weights["sha256"])
    except (ImportError, OSError, RuntimeError, ValueError, metadata.PackageNotFoundError) as exc:
        add("sam", "fail" if require_ai else "warning", exc)
    return {"schema_version": 1, "status": "error" if any(c["status"] == "fail" for c in checks) else "ready",
            "system": {"os": system, "macos": macos, "architecture": machine, "python": platform.python_version(),
                       "executable": sys.executable}, "packages": packages, "recommended_device": device,
            "checks": checks, "camera_accessed": False}
