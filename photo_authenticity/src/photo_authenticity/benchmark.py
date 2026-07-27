from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import cv2
import numpy as np
import onnxruntime as ort


@dataclass(frozen=True)
class BenchmarkResult:
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None
    successful_runs: int
    manual_review_runs: int
    failed_runs: int
    completed_runs: int
    environment: dict[str, object]
    release_sha256: str
    warmup: int
    repetitions: int


@dataclass(frozen=True)
class DiagnosticFeatures:
    fft_high_frequency_ratio: float
    edge_uniformity: float
    laplacian_sharpness: float


def benchmark_orders(
    predictor_factory: Callable[[], object],
    orders: Sequence[Sequence[Path]],
    warmup: int,
    repetitions: int,
) -> BenchmarkResult:
    if warmup < 0 or repetitions <= 0:
        raise ValueError("warmup must be non-negative and repetitions must be positive")
    if not orders or any(len(order) != 3 for order in orders):
        raise ValueError("benchmark orders must each contain exactly three images")
    predictor = predictor_factory()
    for index in range(warmup):
        predictor.predict_order(orders[index % len(orders)])

    durations: list[float] = []
    successful = 0
    manual = 0
    failed = 0
    for index in range(repetitions):
        started = time.perf_counter_ns()
        try:
            result = predictor.predict_order(orders[index % len(orders)])
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            durations.append(elapsed)
            if getattr(result, "decision", None) == "low_risk_candidate":
                successful += 1
            else:
                manual += 1
        except Exception:
            time.perf_counter_ns()
            failed += 1
    values = np.asarray(durations, dtype=np.float64)
    p50 = float(np.percentile(values, 50, method="linear")) if values.size else None
    p95 = float(np.percentile(values, 95, method="linear")) if values.size else None
    maximum = float(np.max(values)) if values.size else None
    threads = int(getattr(predictor, "intra_op_threads", 1))
    environment = {
        "platform": platform.platform(),
        "processor": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
        "python": platform.python_version(),
        "onnxruntime": ort.__version__,
        "intra_op_threads": threads,
    }
    return BenchmarkResult(
        p50,
        p95,
        maximum,
        successful,
        manual,
        failed,
        len(durations),
        environment,
        str(getattr(predictor, "release_sha256", "")),
        warmup,
        repetitions,
    )


def compute_diagnostics(path: Path) -> DiagnosticFeatures:
    image = cv2.imread(str(path.resolve()), cv2.IMREAD_GRAYSCALE)
    if image is None or image.size == 0:
        raise ValueError(f"unable to decode diagnostic image: {path}")
    normalized = image.astype(np.float64) / 255.0
    spectrum = np.abs(np.fft.fftshift(np.fft.fft2(normalized))) ** 2
    height, width = spectrum.shape
    center_y, center_x = height // 2, width // 2
    radius_y = max(1, height // 10)
    radius_x = max(1, width // 10)
    low_frequency = spectrum[
        center_y - radius_y : center_y + radius_y + 1,
        center_x - radius_x : center_x + radius_x + 1,
    ].sum()
    total_energy = spectrum.sum()
    high_frequency_ratio = (
        float(max(0.0, total_energy - low_frequency) / total_energy)
        if total_energy > 0
        else 0.0
    )

    edges = cv2.Canny(image, 50, 150).astype(np.float64) / 255.0
    tile_densities = []
    for y_parts in np.array_split(edges, 4, axis=0):
        for tile in np.array_split(y_parts, 4, axis=1):
            tile_densities.append(float(tile.mean()))
    densities = np.asarray(tile_densities)
    mean_density = float(densities.mean())
    edge_uniformity = (
        float(max(0.0, 1.0 - densities.std() / mean_density))
        if mean_density > 0
        else 1.0
    )
    sharpness = float(cv2.Laplacian(image, cv2.CV_64F).var())
    return DiagnosticFeatures(high_frequency_ratio, edge_uniformity, sharpness)
