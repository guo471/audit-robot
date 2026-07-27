from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from tools.black_edge_shadow_detector import scan_image
except Exception:  # pragma: no cover
    scan_image = None


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
FEATURE_EXTRACTOR_VERSION = "non-real-local-features-v2"
FEATURE_NAMES = (
    "black_edge_any_candidate",
    "black_edge_any_strong",
    "black_edge_strong_sides",
    "black_edge_uncertain_sides",
    "edge_dark_bottom",
    "edge_dark_left",
    "edge_dark_max",
    "edge_dark_mean",
    "edge_dark_right",
    "edge_dark_top",
    "edge_run_bottom",
    "edge_run_left",
    "edge_run_max",
    "edge_run_right",
    "edge_run_top",
    "fft_angular_max",
    "fft_angular_std",
    "fft_axis_sum",
    "fft_entropy",
    "fft_high_0.18",
    "fft_high_0.25",
    "fft_high_0.35",
    "fft_high_0.50",
    "fft_high_0.70",
    "fft_horizontal",
    "fft_peak_median",
    "fft_vertical",
    "grad_mean",
    "grad_orient_entropy",
    "grad_orient_max",
    "grad_orient_std",
    "grad_p95",
    "luma_mean",
    "luma_std",
    "saturation_mean",
    "tile_grad_cv",
    "tile_grad_max",
    "tile_grad_mean",
    "tile_grad_min",
    "tile_grad_std",
)


def iter_images(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTS)


def load_rgb(path: Path, max_size: int = 512) -> np.ndarray:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        scale = min(1.0, max_size / max(image.size))
        if scale < 1.0:
            image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.BILINEAR)
        return np.asarray(image, dtype=np.float32) / 255.0


def longest_run_fraction(mask: np.ndarray) -> float:
    best = cur = 0
    for value in mask.tolist():
        if value:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best / max(1, mask.size)


def edge_features(gray: np.ndarray) -> dict[str, float]:
    h, w = gray.shape
    depth = max(2, round(min(h, w) * 0.04))
    dark = gray < 0.18
    sides = {
        "top": dark[:depth, :],
        "bottom": dark[h - depth:, :],
        "left": dark[:, :depth],
        "right": dark[:, w - depth:],
    }
    feats: dict[str, float] = {}
    for side, band in sides.items():
        feats[f"edge_dark_{side}"] = float(band.mean())
    feats["edge_dark_max"] = max(feats[f"edge_dark_{side}"] for side in sides)
    feats["edge_dark_mean"] = float(np.mean([feats[f"edge_dark_{side}"] for side in sides]))
    feats["edge_run_top"] = longest_run_fraction(dark[0, :])
    feats["edge_run_bottom"] = longest_run_fraction(dark[-1, :])
    feats["edge_run_left"] = longest_run_fraction(dark[:, 0])
    feats["edge_run_right"] = longest_run_fraction(dark[:, -1])
    feats["edge_run_max"] = max(feats[f"edge_run_{side}"] for side in ("top", "bottom", "left", "right"))
    return feats


def fft_features(gray: np.ndarray) -> dict[str, float]:
    size = 384
    image = Image.fromarray(np.uint8(np.clip(gray * 255, 0, 255)), mode="L").resize((size, size), Image.Resampling.BILINEAR)
    g = np.asarray(image, dtype=np.float32) / 255.0
    centered = g - float(g.mean())
    magnitude = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(centered))))
    yy, xx = np.indices(magnitude.shape, dtype=np.float32)
    cy = cx = (size - 1) / 2
    dx, dy = xx - cx, yy - cy
    radius = np.sqrt(dx * dx + dy * dy)
    radius_n = radius / radius.max()
    theta = (np.arctan2(dy, dx) + np.pi) / (2 * np.pi)
    valid = radius > 2
    vals = magnitude[valid]
    total = float(vals.sum()) + 1e-12
    feats: dict[str, float] = {}
    for cutoff in (0.18, 0.25, 0.35, 0.50, 0.70):
        feats[f"fft_high_{cutoff:.2f}"] = float(magnitude[radius_n >= cutoff].sum() / total)
    probs = vals / total
    feats["fft_entropy"] = float(-(probs * np.log(probs + 1e-12)).sum() / np.log(vals.size))
    feats["fft_peak_median"] = float(vals.max() / (np.median(vals) + 1e-12))
    narrow = size // 96
    feats["fft_horizontal"] = float(magnitude[np.abs(dy) <= narrow].sum() / total)
    feats["fft_vertical"] = float(magnitude[np.abs(dx) <= narrow].sum() / total)
    angular = []
    for low, high in zip(np.linspace(0, 1, 25)[:-1], np.linspace(0, 1, 25)[1:]):
        angular.append(float(magnitude[valid & (theta >= low) & (theta < high)].sum() / total))
    feats["fft_angular_max"] = max(angular)
    feats["fft_angular_std"] = float(np.std(angular))
    feats["fft_axis_sum"] = feats["fft_horizontal"] + feats["fft_vertical"]
    return feats


def gradient_features(gray: np.ndarray) -> dict[str, float]:
    gy, gx = np.gradient(gray)
    mag = np.sqrt(gx * gx + gy * gy)
    angle = np.mod(np.arctan2(gy, gx), np.pi)
    threshold = np.quantile(mag, 0.75)
    sel = mag >= threshold
    hist, _ = np.histogram(angle[sel], bins=18, range=(0, np.pi), weights=mag[sel])
    total = float(hist.sum()) + 1e-12
    probs = hist / total
    return {
        "grad_mean": float(mag.mean()),
        "grad_p95": float(np.quantile(mag, 0.95)),
        "grad_orient_entropy": float(-(probs * np.log(probs + 1e-12)).sum() / np.log(len(hist))),
        "grad_orient_max": float(probs.max()),
        "grad_orient_std": float(probs.std()),
    }


def tile_texture_features(gray: np.ndarray) -> dict[str, float]:
    h, w = gray.shape
    values = []
    for y0, y1 in zip(np.linspace(0, h, 5, dtype=int)[:-1], np.linspace(0, h, 5, dtype=int)[1:]):
        for x0, x1 in zip(np.linspace(0, w, 5, dtype=int)[:-1], np.linspace(0, w, 5, dtype=int)[1:]):
            tile = gray[y0:y1, x0:x1]
            gy, gx = np.gradient(tile)
            values.append(float(np.sqrt(gx * gx + gy * gy).mean()))
    arr = np.asarray(values, dtype=np.float32)
    return {
        "tile_grad_mean": float(arr.mean()),
        "tile_grad_std": float(arr.std()),
        "tile_grad_cv": float(arr.std() / (arr.mean() + 1e-12)),
        "tile_grad_min": float(arr.min()),
        "tile_grad_max": float(arr.max()),
    }


def extract_features(path: Path) -> dict[str, Any]:
    rgb = load_rgb(path)
    gray = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    feats: dict[str, Any] = {}
    feats.update(edge_features(gray))
    feats.update(fft_features(gray))
    feats.update(gradient_features(gray))
    feats.update(tile_texture_features(gray))
    feats["luma_mean"] = float(gray.mean())
    feats["luma_std"] = float(gray.std())
    feats["saturation_mean"] = float((rgb.max(axis=2) - rgb.min(axis=2)).mean())
    if scan_image is not None:
        try:
            scan = scan_image(path)
            feats["black_edge_strong_sides"] = sum(1 for item in scan.sides.values() if item.status == "strong_candidate")
            feats["black_edge_uncertain_sides"] = sum(1 for item in scan.sides.values() if item.status == "uncertain_candidate")
            feats["black_edge_any_strong"] = float(scan.status == "strong_candidate")
            feats["black_edge_any_candidate"] = float(scan.status != "none")
        except Exception:
            feats["black_edge_strong_sides"] = 0
            feats["black_edge_uncertain_sides"] = 0
            feats["black_edge_any_strong"] = 0.0
            feats["black_edge_any_candidate"] = 0.0
    return feats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a-dir", type=Path, required=True)
    parser.add_argument("--b-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for label, directory in (("A", args.a_dir), ("B", args.b_dir)):
        for path in iter_images(directory):
            try:
                rows.append({"label": label, "path": str(path), "features": extract_features(path)})
            except Exception as exc:
                rows.append({
                    "label": label,
                    "path": str(path),
                    "features": {},
                    "error": f"{type(exc).__name__}: {exc}",
                })
                print(f"feature_error {path}: {type(exc).__name__}: {exc}", flush=True)
            if len(rows) % 100 == 0:
                print(f"processed {len(rows)}", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({"rows": len(rows), "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
