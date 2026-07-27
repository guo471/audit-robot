"""Evaluate local pixel-only non-real-photo features on labeled folders.

This script is intentionally offline. It uses only decoded pixels from local
images; labels and paths are used only for evaluation output, never as feature
inputs.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Iterable

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from tools.black_edge_shadow_detector import DetectorConfig, scan_array


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DEFAULT_RULES = {
    "full_edge_contrast_min": 75.0,
    "full_edge_dark_run_min": 1.0,
    "fft_diagonal_ratio_min": 0.043506940851064116,
    "tile_fft_similarity_low_max": 0.9884000576663744,
    "edge_texture_orientation_similarity_min": 0.8251639422316148,
}


@dataclass(frozen=True)
class LocalFeatureResult:
    path: str
    metrics: dict[str, float]
    rules: tuple[str, ...]

    @property
    def predicted_non_real(self) -> bool:
        return bool(self.rules)


def _image_files(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]


def _load_rgb(path: Path, max_dimension: int) -> np.ndarray:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        scale = min(1.0, max_dimension / max(image.size))
        if scale < 1.0:
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.BILINEAR,
            )
        return np.asarray(image, dtype=np.float32) / 255.0


def _gray(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def _longest_true_run(mask: np.ndarray) -> int:
    best = 0
    current = 0
    for value in mask.tolist():
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def _edge_metrics(rgb: np.ndarray) -> dict[str, float]:
    scan = scan_array(np.clip(rgb * 255, 0, 255).astype(np.uint8), DetectorConfig(max_dimension=512))
    sides = scan.sides.values()
    metrics = {
        "edge_strong_sides": float(sum(side.status == "strong_candidate" for side in sides)),
        "edge_candidate_sides": float(sum(side.status != "none" for side in scan.sides.values())),
        "edge_max_dark_run": max(side.dark_run_fraction for side in scan.sides.values()),
        "edge_max_contrast": max(side.contrast for side in scan.sides.values()),
        "edge_min_dark_texture_std": min(
            (side.dark_texture_std for side in scan.sides.values() if side.status != "none"),
            default=999.0,
        ),
    }

    gray = _gray(rgb)
    height, width = gray.shape
    bands = (
        gray[0, :],
        gray[-1, :],
        gray[:, 0],
        gray[:, -1],
    )
    lengths = [width, width, height, height]
    dark_runs = [
        _longest_true_run(band <= 0.18) / max(length, 1)
        for band, length in zip(bands, lengths)
    ]
    metrics["edge_very_dark_run_max"] = float(max(dark_runs))
    metrics["edge_very_dark_run_count_35"] = float(sum(run >= 0.35 for run in dark_runs))
    return metrics


def _fft_profile(gray: np.ndarray) -> dict[str, float]:
    resized = _resize_gray(gray, 384)
    window_y = np.hanning(resized.shape[0])[:, None]
    window_x = np.hanning(resized.shape[1])[None, :]
    centered = (resized - float(resized.mean())) * window_y * window_x
    magnitude = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(centered))))
    height, width = magnitude.shape
    yy, xx = np.indices((height, width), dtype=np.float32)
    cy, cx = (height - 1) / 2, (width - 1) / 2
    dy, dx = yy - cy, xx - cx
    radius = np.sqrt(dx * dx + dy * dy)
    valid = radius > max(5.0, min(height, width) * 0.025)
    values = magnitude[valid]
    total = float(values.sum()) + 1e-12
    top = np.sort(values.ravel())[-16:]
    narrow = max(1, min(height, width) // 96)
    axis = ((np.abs(dx) <= narrow) | (np.abs(dy) <= narrow)) & valid
    diagonal = (np.abs(np.abs(dx) - np.abs(dy)) <= narrow) & valid
    high = radius > min(height, width) * 0.22
    return {
        "fft_peak_median": float(values.max() / (float(np.median(values)) + 1e-12)),
        "fft_top16_ratio": float(top.sum() / total),
        "fft_axis_ratio": float(magnitude[axis].sum() / total),
        "fft_diagonal_ratio": float(magnitude[diagonal].sum() / total),
        "fft_high_ratio": float(magnitude[high].sum() / total),
    }


def _resize_gray(gray: np.ndarray, max_dimension: int) -> np.ndarray:
    height, width = gray.shape
    scale = min(1.0, max_dimension / max(height, width))
    if scale >= 1.0:
        return gray.astype(np.float32, copy=False)
    image = Image.fromarray(np.clip(gray * 255, 0, 255).astype(np.uint8), mode="L")
    image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.BILINEAR)
    return np.asarray(image, dtype=np.float32) / 255.0


def _high_pass(gray: np.ndarray) -> np.ndarray:
    image = Image.fromarray(np.clip(gray * 255, 0, 255).astype(np.uint8), mode="L")
    fine = np.asarray(image.filter(ImageFilter.GaussianBlur(radius=0.6)), dtype=np.float32)
    coarse = np.asarray(image.filter(ImageFilter.GaussianBlur(radius=2.4)), dtype=np.float32)
    return (fine - coarse) / 255.0


def _orientation_histogram(tile: np.ndarray) -> tuple[np.ndarray, float, float]:
    hp = _high_pass(tile)
    gy, gx = np.gradient(hp)
    magnitude = np.sqrt(gx * gx + gy * gy)
    if float(magnitude.max()) <= 1e-8:
        return np.zeros(36, dtype=np.float64), 0.0, 0.0
    low, high = np.quantile(magnitude, (0.45, 0.97))
    selected = (magnitude >= low) & (magnitude <= high)
    angles = np.mod(np.arctan2(gy[selected], gx[selected]), math.pi)
    weights = magnitude[selected]
    hist, _ = np.histogram(angles, bins=36, range=(0.0, math.pi), weights=weights)
    total = float(hist.sum())
    if total <= 0:
        return np.zeros(36, dtype=np.float64), 0.0, 0.0
    hist = hist.astype(np.float64) / total
    entropy = float(-(hist * np.log(hist + 1e-12)).sum() / math.log(hist.size))
    energy = float(np.mean(np.abs(hp)))
    return hist, entropy, energy


def _cosine_similarity(vectors: list[np.ndarray]) -> float:
    values = []
    normalized = []
    for vector in vectors:
        norm = float(np.linalg.norm(vector))
        if norm > 1e-12:
            normalized.append(vector / norm)
    for index, first in enumerate(normalized):
        for second in normalized[index + 1 :]:
            values.append(float(np.dot(first, second)))
    return float(np.mean(values)) if values else 0.0


def _tile_slices(size: int, parts: int = 3) -> list[slice]:
    edges = np.linspace(0, size, parts + 1).round().astype(int)
    return [slice(int(edges[i]), int(edges[i + 1])) for i in range(parts)]


def _tile_texture_metrics(gray: np.ndarray) -> dict[str, float]:
    small = _resize_gray(gray, 384)
    hists: list[np.ndarray] = []
    entropies: list[float] = []
    energies: list[float] = []
    fft_hists: list[np.ndarray] = []
    fft_peaks: list[float] = []
    for ys in _tile_slices(small.shape[0]):
        for xs in _tile_slices(small.shape[1]):
            tile = small[ys, xs]
            if min(tile.shape) < 48:
                continue
            hist, entropy, energy = _orientation_histogram(tile)
            hists.append(hist)
            entropies.append(entropy)
            energies.append(energy)
            profile = _tile_fft_vector(tile)
            fft_hists.append(profile[0])
            fft_peaks.append(profile[1])

    entropy_values = np.asarray(entropies, dtype=np.float64)
    energy_values = np.asarray(energies, dtype=np.float64)
    return {
        "orientation_entropy_mean": float(entropy_values.mean()) if entropy_values.size else 0.0,
        "orientation_entropy_std": float(entropy_values.std()) if entropy_values.size else 999.0,
        "orientation_similarity": _cosine_similarity(hists),
        "texture_energy_mean": float(energy_values.mean()) if energy_values.size else 0.0,
        "texture_energy_std": float(energy_values.std()) if energy_values.size else 999.0,
        "tile_fft_similarity": _cosine_similarity(fft_hists),
        "tile_fft_peak_mean": float(np.mean(fft_peaks)) if fft_peaks else 0.0,
        "tile_fft_peak_max": float(np.max(fft_peaks)) if fft_peaks else 0.0,
    }


def _tile_fft_vector(tile: np.ndarray) -> tuple[np.ndarray, float]:
    centered = tile - float(tile.mean())
    magnitude = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(centered))))
    height, width = magnitude.shape
    yy, xx = np.indices((height, width), dtype=np.float32)
    cy, cx = (height - 1) / 2, (width - 1) / 2
    radius = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    valid = radius > max(3.0, min(height, width) * 0.04)
    values = magnitude[valid]
    peak = float(values.max() / (float(np.median(values)) + 1e-12)) if values.size else 0.0
    bins = np.linspace(0, float(radius.max()) + 1e-6, 18)
    hist, _ = np.histogram(radius[valid], bins=bins, weights=values)
    total = float(hist.sum())
    vector = hist.astype(np.float64) / total if total > 0 else np.zeros(hist.shape, dtype=np.float64)
    return vector, peak


def _triggered_rules(metrics: dict[str, float], thresholds: dict[str, float] | None = None) -> tuple[str, ...]:
    t = thresholds or DEFAULT_RULES
    rules: list[str] = []
    if (
        metrics["edge_max_contrast"] >= t["full_edge_contrast_min"]
        and metrics["edge_max_dark_run"] >= t["full_edge_dark_run_min"]
    ):
        rules.append("FULL_EDGE_FRAME_CONTRAST")
    if (
        metrics["fft_diagonal_ratio"] >= t["fft_diagonal_ratio_min"]
        and metrics["tile_fft_similarity"] <= t["tile_fft_similarity_low_max"]
    ):
        rules.append("FFT_DIAGONAL_WITH_CROSS_TILE_DISCORD")
    if (
        metrics["edge_max_dark_run"] >= t["full_edge_dark_run_min"]
        and metrics["orientation_similarity"] >= t["edge_texture_orientation_similarity_min"]
    ):
        rules.append("FULL_EDGE_WITH_TEXTURE_ORIENTATION_SYNC")
    return tuple(rules)


def extract_local_features(path: Path, thresholds: dict[str, float] | None = None) -> LocalFeatureResult:
    rgb = _load_rgb(Path(path), max_dimension=640)
    gray = _gray(rgb)
    metrics: dict[str, float] = {}
    metrics.update(_edge_metrics(rgb))
    metrics.update(_fft_profile(gray))
    metrics.update(_tile_texture_metrics(gray))
    return LocalFeatureResult(str(path), metrics, _triggered_rules(metrics, thresholds))


def _summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for label in ("A", "B"):
        subset = [row for row in rows if row["label"] == label]
        hit = [row for row in subset if row["predicted_non_real"]]
        key = "non_real" if label == "A" else "real"
        summary[key] = {
            "count": len(subset),
            "hit_count": len(hit),
            "hit_rate": len(hit) / len(subset) if subset else None,
            "miss_count" if label == "A" else "false_positive_count": len(subset) - len(hit) if label == "A" else len(hit),
            "miss_rate" if label == "A" else "false_positive_rate": (len(subset) - len(hit)) / len(subset) if label == "A" and subset else (len(hit) / len(subset) if subset else None),
        }
    rule_counts: dict[str, int] = {}
    for row in rows:
        for rule in str(row["rules"]).split("|"):
            if rule:
                rule_counts[rule] = rule_counts.get(rule, 0) + 1
    summary["rule_counts"] = rule_counts
    summary["metric_quantiles"] = _metric_quantiles(rows)
    return summary


def _metric_quantiles(rows: list[dict[str, object]]) -> dict[str, object]:
    keys = sorted(k for k in rows[0] if k.startswith("metric_")) if rows else []
    output: dict[str, object] = {}
    for label in ("A", "B"):
        subset = [row for row in rows if row["label"] == label]
        output[label] = {}
        for key in keys:
            values = sorted(float(row[key]) for row in subset)
            if not values:
                continue
            output[label][key.removeprefix("metric_")] = {
                "min": values[0],
                "p50": values[len(values) // 2],
                "p95": values[min(len(values) - 1, round(len(values) * 0.95))],
                "max": values[-1],
            }
    return output


def _sweep_single_metric(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    if not rows:
        return []
    metric_keys = sorted(k for k in rows[0] if k.startswith("metric_"))
    candidates: list[dict[str, object]] = []
    for metric in metric_keys:
        values = sorted(set(float(row[metric]) for row in rows))
        if len(values) > 80:
            step = max(1, len(values) // 80)
            values = values[::step] + [values[-1]]
        for direction in ("ge", "le"):
            for threshold in values:
                hits = [
                    row
                    for row in rows
                    if (float(row[metric]) >= threshold if direction == "ge" else float(row[metric]) <= threshold)
                ]
                a_total = sum(row["label"] == "A" for row in rows)
                b_total = sum(row["label"] == "B" for row in rows)
                a_hit = sum(row["label"] == "A" for row in hits)
                b_hit = sum(row["label"] == "B" for row in hits)
                candidates.append(
                    {
                        "metric": metric.removeprefix("metric_"),
                        "direction": direction,
                        "threshold": threshold,
                        "a_hit": a_hit,
                        "a_recall": a_hit / a_total if a_total else None,
                        "b_false_positive": b_hit,
                        "b_false_positive_rate": b_hit / b_total if b_total else None,
                    }
                )
    candidates.sort(key=lambda x: (-(x["a_recall"] or 0), x["b_false_positive_rate"] or 1, x["metric"]))
    return candidates[:50]


def _write_outputs(rows: list[dict[str, object]], output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    metric_fields = sorted(k for k in rows[0] if k.startswith("metric_")) if rows else []
    fieldnames = [
        "label",
        "path",
        "predicted_non_real",
        "rules",
        *metric_fields,
        "error",
    ]
    with (output_dir / "results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    with (output_dir / "results.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    summary = _summarize(rows)
    summary["single_metric_sweep_top50"] = _sweep_single_metric(rows)
    summary["default_rules"] = DEFAULT_RULES
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def run_evaluation(non_real_root: Path, real_root: Path, output_dir: Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for label, root in (("A", non_real_root), ("B", real_root)):
        for path in _image_files(root):
            try:
                result = extract_local_features(path)
                rows.append(
                    {
                        "label": label,
                        "path": str(path),
                        "predicted_non_real": result.predicted_non_real,
                        "rules": "|".join(result.rules),
                        **{f"metric_{key}": value for key, value in result.metrics.items()},
                        "error": "",
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "label": label,
                        "path": str(path),
                        "predicted_non_real": True,
                        "rules": "LOCAL_FEATURE_ERROR",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    return _write_outputs(rows, output_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--non-real-root", type=Path, default=Path("\u975e\u5b9e\u62cd\u6837\u672c"))
    parser.add_argument("--real-root", type=Path, default=Path("\u5b9e\u62cd\u56fe\u6837\u672c"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/local_feature_eval/content_features"))
    args = parser.parse_args()
    summary = run_evaluation(args.non_real_root, args.real_root, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
