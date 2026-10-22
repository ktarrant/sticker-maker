"""Capture service shared by the CLI and a future GUI; no parsing or cutter imports."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Iterator
from uuid import uuid4

CAPTURE_SETTINGS = frozenset({"shutterspeed", "iso", "whitebalance"})


class CaptureError(RuntimeError):
    """A capture failed; its output must not be passed to the next phase."""


@dataclass(frozen=True)
class CaptureResult:
    image_path: Path
    manifest_path: Path
    metadata: dict


@contextmanager
def _camera_lock() -> Iterator[int]:
    """Serialize our camera users across CLI processes, threads, and output folders."""
    lock_path = Path(tempfile.gettempdir()) / f"sticker-maker-camera-{os.getuid()}.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CaptureError("Another Sticker Maker process is using the camera.") from exc
        try:
            yield lock.fileno()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def _run_worker(arguments: list[str], timeout: float, lock_fd: int) -> dict | list:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite number greater than zero")
    command = [sys.executable, "-m", "sticker_maker._capture_worker", *arguments]
    try:
        # A process deadline also covers blocking native USB calls. The child
        # inherits the lock so it remains held if the calling GUI/CLI exits.
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            pass_fds=(lock_fd,), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(
            f"Camera operation timed out after {timeout:g}s. No automatic retry was made. "
            "An exposure may have occurred; check/reconnect the camera before retrying. "
            "Any temporary camera settings may still be active."
        ) from exc
    if completed.returncode:
        raise CaptureError(completed.stderr.strip() or "Camera worker exited without a result.")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CaptureError("Camera worker returned an invalid result.") from exc


def list_cameras(*, timeout: float = 10) -> list[dict]:
    """List connected gphoto2 cameras without opening one or firing its shutter."""
    with _camera_lock() as lock_fd:
        return _run_worker(["cameras"], timeout, lock_fd)


def camera_settings(*, port: str | None = None, timeout: float = 10) -> dict:
    """Read current values, writability, and choices without taking a photograph."""
    arguments = ["settings"]
    if port:
        arguments.extend(["--port", port])
    with _camera_lock() as lock_fd:
        return _run_worker(arguments, timeout, lock_fd)


def capture_image(
    output_dir: Path = Path("data/captures"), *, port: str | None = None,
    timeout: float = 60, manual_focus: bool = False,
    settings: dict[str, str] | None = None,
) -> CaptureResult:
    """Capture one JPEG into a unique job and publish a manifest only on success.

    Call from a GUI background worker. Timeout includes connection, exposure,
    transfer, decoding, and camera cleanup; camera settings are otherwise retained.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite number greater than zero")
    settings = dict(settings or {})
    if settings.keys() - CAPTURE_SETTINGS:
        raise ValueError(f"Supported capture settings: {', '.join(sorted(CAPTURE_SETTINGS))}")
    with _camera_lock() as lock_fd:
        started = time.monotonic()
        job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex[:8]
        job_dir = Path(output_dir).resolve() / job_id
        job_dir.mkdir(parents=True)
        arguments = ["capture", str(job_dir)]
        if port:
            arguments.extend(["--port", port])
        if manual_focus:
            arguments.append("--manual-focus")
        for name, value in settings.items():
            arguments.extend(["--setting", f"{name}={value}"])
        try:
            metadata = _run_worker(arguments, timeout, lock_fd)
            metadata["job_id"] = job_id
            metadata["timings_seconds"]["total"] = round(time.monotonic() - started, 6)
            manifest_path = job_dir / "capture.json"
            _write_json(manifest_path, metadata)
        except (CaptureError, OSError, KeyboardInterrupt) as exc:
            _write_json(job_dir / "failure.json", {
                "schema_version": 1, "phase": "capture", "status": "failed",
                "job_id": job_id, "error": str(exc) or "Interrupted by user",
                "elapsed_seconds": round(time.monotonic() - started, 6),
            })
            raise
        return CaptureResult(job_dir / metadata["image"]["file"], manifest_path, metadata)
