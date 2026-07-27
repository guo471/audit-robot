"""Precision-first local candidate detector for possible external screen edges.

The detector reports geometry only. It never makes a business decision and is
connected to the audit flow only through the reversible edge-mapping plugin.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import tempfile
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageOps


SIDES = ("top", "right", "bottom", "left")
DETECTOR_VERSION = "outer-edge-geometry-v2"
ANNOTATION_VERSION = "full-scene-magenta-v1"


@dataclass(frozen=True)
class DetectorConfig:
    max_dimension: int = 640
    max_depth_fraction: float = 0.14
    dark_threshold: float = 95.0
    min_run_fraction: float = 0.08
    strong_run_fraction: float = 0.15
    min_jump: float = 25.0
    strong_jump: float = 42.0
    min_transition_coverage: float = 0.55
    strong_transition_coverage: float = 0.72
    max_fit_residual_fraction: float = 0.04
    max_dark_texture_std: float = 24.0
    max_boundary_fraction: float = 0.16


@dataclass(frozen=True)
class SideEvidence:
    side: str
    status: str
    dark_run_fraction: float
    band_depth_fraction: float
    contrast: float
    boundary_fit: str
    reason: str
    tangent_start_fraction: float = 0.0
    tangent_end_fraction: float = 0.0
    boundary_depth_fraction: float = 0.0
    dark_texture_std: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImageScan:
    path: str
    width: int
    height: int
    status: str
    sides: dict[str, SideEvidence]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "width": self.width,
            "height": self.height,
            "status": self.status,
            "sides": {side: result.to_dict() for side, result in self.sides.items()},
        }


def _to_gray(image: np.ndarray) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim == 2:
        gray = array.astype(np.float32, copy=False)
    elif array.ndim == 3 and array.shape[2] >= 3:
        rgb = array[..., :3].astype(np.float32, copy=False)
        gray = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    else:
        raise ValueError("image must be a grayscale or RGB/RGBA array")
    return np.clip(gray, 0.0, 255.0)


def _longest_true_run(mask: np.ndarray) -> tuple[int, int]:
    best_start = 0
    best_length = 0
    start = 0
    length = 0
    for index, value in enumerate(mask.tolist()):
        if value:
            if length == 0:
                start = index
            length += 1
            if length > best_length:
                best_start = start
                best_length = length
        else:
            length = 0
    return best_start, best_length


def _side_lines(gray: np.ndarray, side: str, max_depth: int) -> list[np.ndarray]:
    height, width = gray.shape
    if side == "top":
        return [gray[depth, :] for depth in range(max_depth)]
    if side == "bottom":
        return [gray[height - depth - 1, :] for depth in range(max_depth)]
    if side == "left":
        return [gray[:, depth] for depth in range(max_depth)]
    if side == "right":
        return [gray[:, width - depth - 1] for depth in range(max_depth)]
    raise ValueError(f"unsupported side: {side}")


def _first_transition(
    lines: list[np.ndarray],
    tangent_start: int,
    tangent_end: int,
    tangent_length: int,
    short_side: int,
    config: DetectorConfig,
) -> tuple[float, float, float, str, float, float, float]:
    if tangent_end <= tangent_start:
        return 0.0, 0.0, 0.0, "none", 0.0, 0.0, 0.0

    transitions: list[float] = []
    jumps: list[float] = []
    dark_samples: list[float] = []
    for tangent_index in range(tangent_start, tangent_end):
        values = np.asarray([line[tangent_index] for line in lines], dtype=np.float32)
        dark = values <= config.dark_threshold
        if not dark[0]:
            continue
        first_light = np.flatnonzero(~dark)
        if first_light.size == 0:
            continue
        boundary = int(first_light[0])
        if boundary <= 0:
            continue
        positive_jumps = np.diff(values[: boundary + 1])
        jump = float(np.max(positive_jumps)) if positive_jumps.size else 0.0
        transitions.append(boundary / max(short_side, 1))
        jumps.append(jump)
        dark_samples.extend(values[:boundary].tolist())

    coverage = len(transitions) / max(tangent_end - tangent_start, 1)
    if not transitions:
        return 0.0, coverage, 0.0, "none", 0.0, 0.0, 0.0

    depths = np.asarray(transitions, dtype=np.float32)
    positions = np.linspace(tangent_start, tangent_end - 1, num=len(depths), dtype=np.float32)
    if len(depths) >= 2 and np.ptp(positions) > 0:
        coefficients = np.polyfit(positions, depths, 1)
        fitted = np.polyval(coefficients, positions)
        residual = float(np.mean(np.abs(depths - fitted)))
    else:
        residual = 0.0
    depth_median = float(np.median(depths))
    median_spread = float(np.median(np.abs(depths - depth_median)))
    fit = "linear" if residual <= config.max_fit_residual_fraction or median_spread <= 0.02 else "irregular"
    dark_texture_std = float(np.std(np.asarray(dark_samples, dtype=np.float32))) if dark_samples else 0.0
    start_fraction = tangent_start / max(tangent_length, 1)
    end_fraction = tangent_end / max(tangent_length, 1)
    return (
        depth_median,
        coverage,
        float(np.median(jumps)),
        fit,
        start_fraction,
        end_fraction,
        dark_texture_std,
    )


def _empty_side(side: str, reason: str) -> SideEvidence:
    return SideEvidence(side, "none", 0.0, 0.0, 0.0, "none", reason)


def _scan_side(gray: np.ndarray, side: str, config: DetectorConfig) -> SideEvidence:
    height, width = gray.shape
    short_side = min(height, width)
    tangent_length = width if side in {"top", "bottom"} else height
    max_depth = max(6, int(short_side * config.max_depth_fraction) + 2)
    max_depth = min(max_depth, short_side - 1)
    lines = _side_lines(gray, side, max_depth)

    # The candidate must touch the actual uploaded-image edge. Looking only at
    # an inner line would mistake a product bezel or an internal dark object
    # for an image carrier boundary.
    start, length = _longest_true_run(lines[0] <= config.dark_threshold)
    run_fraction = length / max(tangent_length, 1)
    if run_fraction < config.min_run_fraction:
        return _empty_side(side, "no_continuous_outer_dark_run")

    tangent_end = start + length
    (
        depth_fraction,
        coverage,
        contrast,
        fit,
        tangent_start_fraction,
        tangent_end_fraction,
        dark_texture_std,
    ) = _first_transition(lines, start, tangent_end, tangent_length, short_side, config)
    if not depth_fraction:
        return SideEvidence(
            side,
            "uncertain_candidate",
            run_fraction,
            0.0,
            0.0,
            "none",
            "outer_dark_run_without_visible_inner_boundary",
            tangent_start_fraction,
            tangent_end_fraction,
            0.0,
            dark_texture_std,
        )

    has_abrupt_boundary = contrast >= config.min_jump
    strong = (
        run_fraction >= config.strong_run_fraction
        and coverage >= config.strong_transition_coverage
        and contrast >= config.strong_jump
        and fit == "linear"
        and dark_texture_std <= config.max_dark_texture_std
        and depth_fraction <= config.max_boundary_fraction
    )
    if strong:
        status = "strong_candidate"
        reason = "outer_dark_run_with_abrupt_regular_low_texture_boundary"
    elif has_abrupt_boundary or coverage >= config.min_transition_coverage:
        status = "uncertain_candidate"
        reason = "outer_dark_run_needs_scene_or_product_ownership_review"
    else:
        status = "uncertain_candidate"
        reason = "outer_dark_run_without_confirmed_boundary"
    return SideEvidence(
        side,
        status,
        run_fraction,
        depth_fraction,
        contrast,
        fit,
        reason,
        tangent_start_fraction,
        tangent_end_fraction,
        depth_fraction,
        dark_texture_std,
    )


def scan_array(image: np.ndarray, config: DetectorConfig | None = None) -> ImageScan:
    config = config or DetectorConfig()
    gray = _to_gray(image)
    if max(gray.shape) > config.max_dimension:
        height, width = gray.shape
        scale = config.max_dimension / max(height, width)
        resized = Image.fromarray(gray.astype(np.uint8), mode="L").resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.BILINEAR,
        )
        gray = np.asarray(resized, dtype=np.float32)
    height, width = gray.shape
    sides = {side: _scan_side(gray, side, config) for side in SIDES}
    statuses = [result.status for result in sides.values()]
    if any(status == "strong_candidate" for status in statuses):
        status = "strong_candidate"
    elif any(status == "uncertain_candidate" for status in statuses):
        status = "uncertain_candidate"
    else:
        status = "none"
    return ImageScan("", width, height, status, sides)


def scan_image(path: Path, config: DetectorConfig | None = None) -> ImageScan:
    with Image.open(path) as image:
        result = scan_array(np.asarray(ImageOps.exif_transpose(image).convert("RGB")), config)
    return ImageScan(str(path), result.width, result.height, result.status, result.sides)


def annotate_strong_candidates(source: Path, destination: Path, scan: ImageScan) -> Path:
    """Draw thin machine markers on the full scene without cropping the source."""
    if destination.is_file():
        return destination
    with Image.open(source) as image:
        annotated = ImageOps.exif_transpose(image).convert("RGB")
        draw = ImageDraw.Draw(annotated)
        width, height = annotated.size
        marker = (255, 0, 200)
        marker_width = max(2, round(min(width, height) / 220))
        for side, evidence in scan.sides.items():
            if evidence.status != "strong_candidate":
                continue
            tangent_length = width if side in {"top", "bottom"} else height
            tangent_start = round(evidence.tangent_start_fraction * tangent_length)
            tangent_end = round(evidence.tangent_end_fraction * tangent_length)
            depth = max(1, round(evidence.boundary_depth_fraction * min(width, height)))
            if side == "top":
                draw.line((tangent_start, depth, tangent_end, depth), fill=marker, width=marker_width)
            elif side == "bottom":
                y = height - depth
                draw.line((tangent_start, y, tangent_end, y), fill=marker, width=marker_width)
            elif side == "left":
                draw.line((depth, tangent_start, depth, tangent_end), fill=marker, width=marker_width)
            elif side == "right":
                x = width - depth
                draw.line((x, tangent_start, x, tangent_end), fill=marker, width=marker_width)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=destination.stem + "-",
            suffix=".tmp.png",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        try:
            annotated.save(temporary, format="PNG")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination
