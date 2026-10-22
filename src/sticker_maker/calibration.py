"""Camera-independent planar calibration and saved profile contracts.

Points use the decoded, unrotated primary image raster: x right, y down.
Lens correction, if used, happens upstream and has its own frame identifier.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile

import numpy as np


class CalibrationError(ValueError):
    """Invalid calibration data or an incompatible image coordinate frame."""


def _points(values, name: str) -> np.ndarray:
    try:
        points = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise CalibrationError(f"{name} must contain numeric [x, y] pairs.") from exc
    if points.shape in {(0,), (0, 2)}:
        return np.empty((0, 2))
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise CalibrationError(f"{name} must contain finite [x, y] pairs.")
    return points


def _positive_pair(value, name: str, *, integers: bool = False) -> tuple:
    points = _points([value], name)
    if points.shape != (1, 2) or (points <= 0).any():
        raise CalibrationError(f"{name} must contain two positive values.")
    if integers and any(type(number) is not int for number in value):
        raise CalibrationError(f"{name} must contain integer pixel dimensions.")
    return tuple(int(v) if integers else float(v) for v in points[0])


@dataclass(frozen=True)
class ReferencePoint:
    image_px: tuple[float, float]
    mat_mm: tuple[float, float]

    @classmethod
    def from_dict(cls, value: dict) -> ReferencePoint:
        return cls(tuple(_points([value["image_px"]], "image_px")[0]),
                   tuple(_points([value["mat_mm"]], "mat_mm")[0]))


@dataclass(frozen=True)
class ValidationReport:
    status: str
    point_count: int
    rms_error_mm: float | None
    max_error_mm: float | None
    max_allowed_error_mm: float


def _project(matrix: np.ndarray, points) -> list[list[float]]:
    points = _points(points, "points")
    if len(points) == 0:
        return []
    homogeneous = np.column_stack((points, np.ones(len(points))))
    mapped = homogeneous @ matrix.T
    # Test the denominator relative to its terms, not the arbitrary matrix scale.
    scale = np.abs(homogeneous) @ np.abs(matrix[2])
    if (np.abs(mapped[:, 2]) <= 1e-12 * np.maximum(scale, np.finfo(float).tiny)).any():
        raise CalibrationError("A point lies on the transform's projective horizon.")
    result = mapped[:, :2] / mapped[:, 2, None]
    if not np.isfinite(result).all():
        raise CalibrationError("Transform produced non-finite coordinates.")
    return result.tolist()


def _normalize(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    center = points.mean(axis=0)
    centered = points - center
    distance = np.sqrt(np.mean(np.sum(centered ** 2, axis=1)))
    if distance <= np.finfo(float).eps:
        raise CalibrationError("Reference points have no spatial extent.")
    scale = math.sqrt(2) / distance
    transform = np.array([[scale, 0, -scale * center[0]], [0, scale, -scale * center[1]], [0, 0, 1]])
    return np.asarray(_project(transform, points)), transform


def _fit_matrix(references: tuple[ReferencePoint, ...]) -> tuple[tuple[float, ...], ...]:
    if len(references) < 4:
        raise CalibrationError("At least four reference correspondences are required.")
    source, source_frame = _normalize(np.array([p.image_px for p in references]))
    target, target_frame = _normalize(np.array([p.mat_mm for p in references]))
    rows = []
    for (x, y), (u, v) in zip(source, target):
        rows.extend([[-x, -y, -1, 0, 0, 0, u*x, u*y, u],
                     [0, 0, 0, -x, -y, -1, v*x, v*y, v]])
    design = np.asarray(rows)
    if np.linalg.matrix_rank(design) < 8:
        raise CalibrationError("Reference points are degenerate; use distinct, non-collinear points.")
    # Four correspondences produce an 8x9 matrix: the full right nullspace is
    # essential here. Normalization keeps camera-pixel and millimeter scales stable.
    _, _, right = np.linalg.svd(design, full_matrices=len(references) == 4)
    matrix = np.linalg.inv(target_frame) @ right[-1].reshape(3, 3) @ source_frame
    matrix /= np.linalg.norm(matrix)
    pivot = matrix.flat[np.argmax(np.abs(matrix))]
    if pivot < 0:
        matrix = -matrix
    return tuple(tuple(float(v) for v in row) for row in matrix)


@dataclass(frozen=True)
class CalibrationProfile:
    rig_id: str
    image_size_px: tuple[int, int]
    mat_size_mm: tuple[float, float]
    reference_points: tuple[ReferencePoint, ...]
    check_points: tuple[ReferencePoint, ...]
    image_to_mat: tuple[tuple[float, ...], ...]
    reference_kind: str = "synthetic"
    exif_orientation: int = 1
    pixel_space: str = "raw"
    lens_profile_id: str | None = None
    source_image_sha256: str | None = None
    max_error_mm: float = 0.25

    def __post_init__(self):
        if not isinstance(self.rig_id, str) or not self.rig_id.strip():
            raise CalibrationError("rig_id must identify the camera/lens/focus/mount setup.")
        _positive_pair(self.image_size_px, "image_size_px", integers=True)
        _positive_pair(self.mat_size_mm, "mat_size_mm")
        if self.reference_kind not in {"synthetic", "measured"}:
            raise CalibrationError("reference_kind must be synthetic or measured.")
        if type(self.exif_orientation) is not int or not 1 <= self.exif_orientation <= 8:
            raise CalibrationError("exif_orientation must be an integer from 1 to 8.")
        if self.pixel_space not in {"raw", "undistorted"}:
            raise CalibrationError("pixel_space must be raw or undistorted.")
        if self.lens_profile_id is not None and (not isinstance(self.lens_profile_id, str) or not self.lens_profile_id.strip()):
            raise CalibrationError("lens_profile_id must be a nonempty string.")
        if (self.pixel_space == "undistorted") != bool(self.lens_profile_id):
            raise CalibrationError("Only undistorted coordinates require a lens_profile_id.")
        if self.source_image_sha256 is not None and (not isinstance(self.source_image_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.source_image_sha256)):
            raise CalibrationError("source_image_sha256 must be a lowercase SHA-256 digest.")
        if not math.isfinite(self.max_error_mm) or self.max_error_mm <= 0:
            raise CalibrationError("max_error_mm must be finite and positive.")
        if len(self.reference_points) < 4:
            raise CalibrationError("At least four reference correspondences are required.")
        for group in [self.reference_points, self.check_points]:
            image_points = _points([p.image_px for p in group], "image_px")
            mat_points = _points([p.mat_mm for p in group], "mat_mm")
            if len(group) and ((image_points < 0).any() or (image_points > self.image_size_px).any()
                               or (mat_points < 0).any() or (mat_points > self.mat_size_mm).any()):
                raise CalibrationError("Reference/check points must lie inside their image and mat extents.")
            if len(set(p.image_px for p in group)) != len(group) or len(set(p.mat_mm for p in group)) != len(group):
                raise CalibrationError("Reference/check points must be distinct within each group.")
        if ({p.image_px for p in self.reference_points} & {p.image_px for p in self.check_points}
                or {p.mat_mm for p in self.reference_points} & {p.mat_mm for p in self.check_points}):
            raise CalibrationError("Validation check points must be independent of fitting points.")
        matrix = np.asarray(self.image_to_mat, dtype=float)
        if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or np.linalg.matrix_rank(matrix) != 3:
            raise CalibrationError("image_to_mat must be a finite, invertible 3x3 matrix.")
        width, height = self.image_size_px
        corners = np.array([[0, 0, 1], [width, 0, 1], [width, height, 1], [0, height, 1]])
        denominators = corners @ matrix[2]
        if not ((denominators > 0).all() or (denominators < 0).all()):
            raise CalibrationError("The projective horizon crosses the image; check reference correspondences.")

    @property
    def validation(self) -> ValidationReport:
        count = len(self.check_points)
        if not count:
            return ValidationReport("not_checked", 0, None, None, self.max_error_mm)
        measured = np.array([p.mat_mm for p in self.check_points])
        predicted = self.pixels_to_mm([p.image_px for p in self.check_points])
        errors = np.linalg.norm(np.asarray(predicted) - measured, axis=1)
        spread = np.linalg.matrix_rank(measured - measured.mean(axis=0))
        status = "passed" if count >= 3 and spread == 2 and errors.max() <= self.max_error_mm else "failed"
        return ValidationReport(status, count, float(np.sqrt(np.mean(errors**2))), float(errors.max()), self.max_error_mm)

    def pixels_to_mm(self, points) -> list[list[float]]:
        return _project(np.asarray(self.image_to_mat), points)

    def mm_to_pixels(self, points) -> list[list[float]]:
        return _project(np.linalg.inv(np.asarray(self.image_to_mat)), points)

    def assert_compatible(
        self, *, image_size_px: tuple[int, int], rig_id: str, exif_orientation: int = 1,
        pixel_space: str = "raw", lens_profile_id: str | None = None,
        source_image_sha256: str | None = None,
    ) -> None:
        actual = (tuple(image_size_px), rig_id, exif_orientation, pixel_space, lens_profile_id, source_image_sha256)
        expected = (self.image_size_px, self.rig_id, self.exif_orientation, self.pixel_space, self.lens_profile_id, self.source_image_sha256)
        if actual != expected:
            raise CalibrationError("Image size, orientation, rig, lens coordinate frame, or source identity does not match calibration.")

    def require_measured_validation(self) -> None:
        """Gate photo-derived export; this does not certify Cricut blade alignment."""
        if self.reference_kind != "measured" or self.source_image_sha256 is None or self.validation.status != "passed":
            raise CalibrationError("Physical export requires a source-bound measured profile with passing independent check points.")

    def _payload(self) -> dict:
        return {"schema_version": 1, **asdict(self)}

    @property
    def profile_id(self) -> str:
        canonical = json.dumps(self._payload(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {**self._payload(), "profile_id": self.profile_id, "validation": asdict(self.validation)}

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replacement prevents a GUI reader seeing half-written JSON.
        with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(json.dumps(self.to_dict(), indent=2, allow_nan=False) + "\n")
                output.close()
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)


def _profile_data(document: dict) -> dict:
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise CalibrationError("Expected calibration schema_version 1.")
    try:
        return {
            "rig_id": document["rig_id"],
            "image_size_px": _positive_pair(document["image_size_px"], "image_size_px", integers=True),
            "mat_size_mm": _positive_pair(document["mat_size_mm"], "mat_size_mm"),
            "reference_points": tuple(ReferencePoint.from_dict(p) for p in document["reference_points"]),
            "check_points": tuple(ReferencePoint.from_dict(p) for p in document.get("check_points", [])),
            "reference_kind": document.get("reference_kind", "synthetic"),
            "exif_orientation": document.get("exif_orientation", 1),
            "pixel_space": document.get("pixel_space", "raw"),
            "lens_profile_id": document.get("lens_profile_id"),
            "source_image_sha256": document.get("source_image_sha256"),
            "max_error_mm": float(document.get("max_error_mm", 0.25)),
        }
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise CalibrationError(f"Malformed calibration reference data: {exc}") from exc


def fit_calibration(document: dict) -> CalibrationProfile:
    """Fit a normalized planar homography from at least four saved point pairs."""
    data = _profile_data(document)
    try:
        matrix = _fit_matrix(data["reference_points"])
        return CalibrationProfile(**data, image_to_mat=matrix)
    except np.linalg.LinAlgError as exc:
        raise CalibrationError("Reference geometry could not be solved.") from exc


def load_calibration(path: Path) -> CalibrationProfile:
    """Load a profile, recheck its fingerprint, and recompute validation metrics."""
    try:
        document = json.loads(Path(path).read_text())
        data = _profile_data(document)
        matrix = tuple(tuple(float(v) for v in row) for row in document["image_to_mat"])
        profile = CalibrationProfile(**data, image_to_mat=matrix)
        if profile.profile_id != document.get("profile_id"):
            raise CalibrationError("Calibration fingerprint changed; refit the profile after editing its inputs.")
        return profile
    except (KeyError, TypeError, ValueError) as exc:
        raise CalibrationError(f"Invalid calibration profile: {exc}") from exc
