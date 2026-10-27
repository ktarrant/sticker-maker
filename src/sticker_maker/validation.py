"""Explicit hardware validation runs: bounded captures, no retries, saved evidence."""

from datetime import datetime, timezone
import math
from pathlib import Path
from uuid import uuid4

import numpy as np

from sticker_maker.capture import CaptureError, camera_settings, capture_image
from sticker_maker.regions import atomic_json


def validate_capture(output_dir: Path, *, count=3, timeout=60, port=None):
    if type(count) is not int or count < 1:
        raise ValueError("Capture count must be a positive integer.")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be finite and positive.")
    run_dir = Path(output_dir).resolve() / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8])
    run_dir.mkdir(parents=True)
    report = {"schema_version": 1, "status": "failed", "requested": count, "captures": [],
              "error": None, "settings_changed": {}, "p95_capture_seconds": None}
    interrupted = False
    before = None
    try:
        before = camera_settings(port=port)
        report["settings_before"] = before
        for _ in range(count):
            result = capture_image(run_dir / "captures", port=port, timeout=timeout, manual_focus=True)
            report["captures"].append({"image": str(result.image_path), "manifest": str(result.manifest_path),
                                       "seconds": result.metadata["timings_seconds"]["total"],
                                       "warnings": result.metadata["warnings"]})
            if result.metadata["warnings"]:
                report["status"] = "needs_review"
                break  # Do not continue with uncertain restoration/settings.
        else:
            report["status"] = "passed"
    except (CaptureError, OSError, ValueError) as exc:
        report["error"] = str(exc)
    except KeyboardInterrupt:
        report["error"] = "Interrupted; check the camera before another exposure."
        interrupted = True
    finally:
        if before is not None:
            try:
                after = camera_settings(port=port)
                report["settings_after"] = after
                before_values = {k: v["value"] for k, v in before["settings"].items()}
                after_values = {k: v["value"] for k, v in after["settings"].items()}
                report["settings_changed"] = {k: {"before": before_values.get(k), "after": after_values.get(k)}
                    for k in before_values.keys() | after_values.keys() if before_values.get(k) != after_values.get(k)}
                if report["settings_changed"] and report["status"] == "passed":
                    report["status"] = "needs_review"
            except (CaptureError, OSError, ValueError) as exc:
                report["settings_after_error"] = str(exc)
                report["status"] = "failed"
        if report["captures"]:
            report["p95_capture_seconds"] = float(np.percentile([c["seconds"] for c in report["captures"]], 95))
        report["completed"] = len(report["captures"])
        atomic_json(run_dir / "report.json", report)
    if interrupted:
        raise KeyboardInterrupt
    return {"report": str(run_dir / "report.json"), **report}
