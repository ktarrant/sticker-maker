"""Optional SAM 2.1 image adapter and isolated local benchmarking."""

from __future__ import annotations

import hashlib
import io
import json
import math
import platform
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

import numpy as np
from PIL import Image
from shapely.geometry import MultiLineString, MultiPolygon
from shapely.ops import polygonize

from sticker_maker.recognition import read_rgb
from sticker_maker.regions import RegionError, atomic_json, geojson, image_identity, polygon

SAM_REVISION = "2b90b9f5ceec907a1c18123530e92e794ad901a4"
CONFIGS = {"small": "configs/sam2.1/sam2.1_hiera_s.yaml", "tiny": "configs/sam2.1/sam2.1_hiera_t.yaml"}


def mask_outline(mask, origin=(0, 0)):
    """Trace pixel-cell boundaries, including diagonal contacts, without invalid ring repairs."""
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2 or not mask.any():
        raise RegionError("No region found. Add a foreground click or draw a tighter box.")
    padded = np.pad(mask, 1)
    sides = [(mask & ~padded[:-2, 1:-1], "top"), (mask & ~padded[2:, 1:-1], "bottom"),
             (mask & ~padded[1:-1, :-2], "left"), (mask & ~padded[1:-1, 2:], "right")]
    segments = []
    for boundary, side in sides:
        for y, x in zip(*np.nonzero(boundary)):
            if side == "top":
                segment = [(x, y), (x+1, y)]
            elif side == "bottom":
                segment = [(x, y+1), (x+1, y+1)]
            elif side == "left":
                segment = [(x, y), (x, y+1)]
            else:
                segment = [(x+1, y), (x+1, y+1)]
            segments.append(segment)
    parts = []
    for part in polygonize(MultiLineString(segments)):
        point = part.representative_point()
        if mask[min(mask.shape[0]-1, int(point.y)), min(mask.shape[1]-1, int(point.x))]:
            parts.append(part)
    geometry = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    # Preserve pixel topology first; only then simplify with a bounded subpixel tolerance.
    simplified = geometry.simplify(0.35, preserve_topology=True)
    if geometry.boundary.hausdorff_distance(simplified.boundary) <= 0.35 + 1e-7:
        geometry = simplified
    from shapely.affinity import translate
    geometry = translate(geometry, xoff=origin[0], yoff=origin[1])
    polygon(geojson(geometry))
    return geometry, 0


class SAMRecognizer:
    def __init__(self, checkpoint, model="small", device="auto"):
        if model not in CONFIGS or device not in {"auto", "mps", "cpu"}:
            raise RegionError("Use SAM 2.1 small/tiny on auto, mps, or cpu.")
        started = time.perf_counter()
        from sticker_maker.models import verify_model
        verified = verify_model(Path(checkpoint), model)
        try:
            import torch
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor
        except ImportError as exc:
            raise RegionError("Install the compatible AI environment with: ./scripts/setup-macos.sh") from exc
        checkpoint = Path(checkpoint).resolve()
        checksum = verified["sha256"]
        intel_mac = sys.platform == "darwin" and platform.machine() == "x86_64"
        if device == "auto":
            device = "mps" if torch.backends.mps.is_available() and not intel_mac else "cpu"
        if device == "mps" and intel_mac:
            raise RegionError("The Intel compatibility runtime uses CPU image inference; choose auto or cpu.")
        if device == "mps" and not torch.backends.mps.is_available():
            raise RegionError("MPS is unavailable in this process; choose CPU or run with Metal access.")
        self.torch, self.device = torch, device
        self.predictor = SAM2ImagePredictor(build_sam2(CONFIGS[model], str(checkpoint), device=device,
                                                      apply_postprocessing=False), max_hole_area=0, max_sprinkle_area=0)
        self.metadata = {"provider": "sam2", "revision": SAM_REVISION, "model": model,
                         "checkpoint_sha256": checksum, "torch_version": torch.__version__, "device": device}
        self._image_key = self._crop_key = self._previous = self._logits = None
        self._rgb = self._identity = None
        self.load_seconds = time.perf_counter() - started

    def _sync(self):
        if self.device == "mps":
            self.torch.mps.synchronize()

    def refine(self, image_path, *, box_px=None, points=(), labels=(), asset_dir):
        started = time.perf_counter()
        image_path = Path(image_path).resolve()
        stat = image_path.stat()
        key = (str(image_path), stat.st_size, stat.st_mtime_ns)
        if key != self._image_key:
            self._identity = image_identity(image_path)
            image, _ = read_rgb(image_path)
            self._rgb = np.asarray(image).copy()
            self._image_key = key
            self._crop_key = None
        width, height = self._identity["image_size_px"]
        coords = np.asarray(points, dtype=float).reshape(-1, 2)
        labels = np.asarray(labels)
        if (labels.ndim != 1 or len(coords) != len(labels) or not np.isfinite(coords).all() or not np.isin(labels, [0, 1]).all()
                or (coords < 0).any() or (coords > [width, height]).any()):
            raise RegionError("Prompts must be finite image points with foreground/background labels.")
        labels = labels.astype(np.int32)
        if box_px is None and not (labels == 1).any():
            raise RegionError("Provide a selection box or foreground click.")
        if box_px is not None:
            bounds = np.asarray(box_px, dtype=float)
            if (bounds.shape != (4,) or not np.isfinite(bounds).all() or bounds[2] <= bounds[0]
                    or bounds[3] <= bounds[1] or (bounds < 0).any() or (bounds > [width, height, width, height]).any()):
                raise RegionError("Selection box must lie inside the image and have positive area.")
            x1, y1, x2, y2 = bounds
            margin = max(32, 0.15 * max(x2-x1, y2-y1))
            if len(coords):
                x1, y1 = np.minimum([x1, y1], coords.min(axis=0))
                x2, y2 = np.maximum([x2, y2], coords.max(axis=0))
            crop = (max(0, math.floor(x1-margin)), max(0, math.floor(y1-margin)),
                    min(width, math.ceil(x2+margin)), min(height, math.ceil(y2+margin)))
        else:
            crop = (0, 0, width, height)
        decode_done = time.perf_counter()
        with self.torch.inference_mode():
            encoded = self._crop_key != crop
            if encoded:
                self.predictor.set_image(self._rgb[crop[1]:crop[3], crop[0]:crop[2]])
                self._crop_key, self._previous, self._logits = crop, None, None
            self._sync()
            encode_done = time.perf_counter()
            prompt = {"points": coords.tolist(), "labels": labels.tolist(), "box": list(box_px) if box_px is not None else None}
            previous = self._previous
            prior = (self._logits if previous and previous["box"] == prompt["box"] and len(previous["points"]) < len(prompt["points"])
                     and prompt["points"][:len(previous["points"])] == previous["points"]
                     and prompt["labels"][:len(previous["labels"])] == previous["labels"] else None)
            masks, scores, logits = self.predictor.predict(
                point_coords=coords - [crop[0], crop[1]] if len(coords) else None,
                point_labels=labels if len(coords) else None,
                box=np.asarray(box_px) - [crop[0], crop[1], crop[0], crop[1]] if box_px is not None else None,
                mask_input=prior, multimask_output=box_px is None and len(coords) == 1)
            self._sync()
            predict_done = time.perf_counter()
            best = int(np.argmax(scores))
            self._previous, self._logits = prompt, logits[best:best+1]
        mask = masks[best] > 0
        geometry, discarded = mask_outline(mask, crop[:2])
        bounds = Image.fromarray(mask.astype(np.uint8) * 255).getbbox()
        if bounds is None:
            raise RegionError("Empty model selection; add a foreground click.")
        encoded_mask = io.BytesIO()
        Image.fromarray(mask.astype(np.uint8) * 255).crop(bounds).save(encoded_mask, format="PNG")
        data = encoded_mask.getvalue()
        asset_hash = hashlib.sha256(data).hexdigest()
        asset_dir = Path(asset_dir).resolve()
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_path = asset_dir / f"{asset_hash}.png"
        with tempfile.NamedTemporaryFile(dir=asset_dir, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(data)
                stream.close()
                temporary.replace(asset_path)
            finally:
                temporary.unlink(missing_ok=True)
        timings = {"image_decode_seconds": decode_done-started, "image_encode_seconds": encode_done-decode_done,
                   "predict_seconds": predict_done-encode_done, "total_seconds": time.perf_counter()-started}
        origin = self.metadata | {"prompts": prompt, "score": float(scores[best]), "crop_px": list(crop),
                                  "mask_asset": str(asset_path), "mask_sha256": asset_hash,
                                  "mask_origin_px": [crop[0]+bounds[0], crop[1]+bounds[1]],
                                  "discarded_degenerate_components": discarded, "timings": timings}
        origin["trace_tolerance_px"] = 0.35
        return {"source_sha256": self._identity["sha256"], "encoded_image": encoded, "timings": timings,
                "proposal": {"label": "AI selection", "geometry": geojson(geometry), "origin": origin}}


def benchmark_one(image, checkpoint, model, device, box_px, trials, output):
    adapter = SAMRecognizer(checkpoint, model, device)
    results = [adapter.refine(image, box_px=box_px, asset_dir=Path(output).parent / "masks") for _ in range(trials+1)]
    warm = [r["timings"]["total_seconds"] for r in results[1:]]
    import torch
    result = {"model": model, "device": adapter.device, "load_seconds": adapter.load_seconds,
              "first_refinement": results[0]["timings"], "warm_trials": trials,
              "warm_refine_median_seconds": float(np.median(warm)), "warm_refine_p95_seconds": float(np.percentile(warm, 95)),
              "peak_process_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
              "mps_driver_bytes_after_trials": torch.mps.driver_allocated_memory() if adapter.device == "mps" else None,
              "sample_result": results[-1], "note": "Repeated box prompts measure latency, not segmentation accuracy or correction effort."}
    atomic_json(output, result)
    return result


def benchmark_models(image, checkpoints, output, *, device="auto", box_px, trials=10):
    if trials < 2:
        raise RegionError("Benchmark at least two warm trials.")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for model in ("small", "tiny"):
        target = output.with_name(output.stem + f"-{model}.json")
        command = [sys.executable, "-m", "sticker_maker.sam", str(image),
                   str(Path(checkpoints) / f"sam2.1_hiera_{model}.pt"), model, device, str(target),
                   "--box", *map(str, box_px), "--trials", str(trials)]
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired as exc:
            raise RegionError(f"{model} benchmark exceeded five minutes.") from exc
        target.with_suffix(".log").write_text(process.stderr)
        if process.returncode:
            raise RegionError(f"{model} benchmark failed; see {target.with_suffix('.log')}")
        results.append(json.loads(target.read_text()))
    summary = {"schema_version": 1, "image": image_identity(image), "box_px": box_px, "results": results}
    atomic_json(output, summary)
    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    for name in ("image", "checkpoint", "model", "device", "output"):
        parser.add_argument(name)
    parser.add_argument("--box", type=float, nargs=4, required=True)
    parser.add_argument("--trials", type=int, default=10)
    arguments = parser.parse_args()
    benchmark_one(arguments.image, arguments.checkpoint, arguments.model, arguments.device,
                  arguments.box, arguments.trials, Path(arguments.output))
