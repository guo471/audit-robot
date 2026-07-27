from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import photo_authenticity.benchmark as benchmark_module
from photo_authenticity.benchmark import benchmark_orders, compute_diagnostics
from photo_authenticity.contracts import ReasonCode
from photo_authenticity.inference import OrderDecision


THREE_IMAGE_ORDERS = [
    (Path("a.png"), Path("b.png"), Path("c.png")),
]


class FakePredictor:
    release_sha256 = "d" * 64
    intra_op_threads = 1

    def predict_order(self, order):
        return OrderDecision("low_risk_candidate", (), 0.0, ReasonCode.NONE)


@pytest.fixture
def fake_clock_predictor(monkeypatch):
    timestamps = iter(
        value * 1_000_000
        for value in (0, 10, 10, 30, 30, 60, 60, 100, 100, 150)
    )
    monkeypatch.setattr(benchmark_module.time, "perf_counter_ns", lambda: next(timestamps))
    return FakePredictor


def test_benchmark_reports_three_image_p50_p95_and_max(fake_clock_predictor) -> None:
    result = benchmark_orders(fake_clock_predictor, THREE_IMAGE_ORDERS, warmup=1, repetitions=5)

    assert result.p50_ms == 30.0
    assert result.p95_ms == 48.0
    assert result.max_ms == 50.0
    assert result.successful_runs == 5
    assert result.manual_review_runs == 0
    assert result.failed_runs == 0
    assert result.release_sha256 == "d" * 64


def test_benchmark_counts_manual_review_and_exceptions(monkeypatch) -> None:
    class MixedPredictor(FakePredictor):
        def __init__(self) -> None:
            self.calls = 0

        def predict_order(self, order):
            self.calls += 1
            if self.calls == 2:
                return OrderDecision("manual_review", (), 0.0, ReasonCode.INFERENCE_ERROR)
            if self.calls == 3:
                raise RuntimeError("synthetic failure")
            return super().predict_order(order)

    timestamps = iter(value * 1_000_000 for value in (0, 10, 10, 30, 30, 60))
    monkeypatch.setattr(benchmark_module.time, "perf_counter_ns", lambda: next(timestamps))

    result = benchmark_orders(MixedPredictor, THREE_IMAGE_ORDERS, warmup=0, repetitions=3)

    assert result.successful_runs == 1
    assert result.manual_review_runs == 1
    assert result.failed_runs == 1
    assert result.completed_runs == 2
    assert result.p50_ms == 15.0


def test_diagnostics_change_without_affecting_predictor_decision(tmp_path) -> None:
    smooth = tmp_path / "smooth.png"
    textured = tmp_path / "textured.png"
    Image.new("RGB", (64, 64), (127, 127, 127)).save(smooth)
    random = np.random.default_rng(20260713)
    Image.fromarray(random.integers(0, 256, (64, 64, 3), dtype=np.uint8)).save(textured)
    predictor = FakePredictor()

    smooth_features = compute_diagnostics(smooth)
    textured_features = compute_diagnostics(textured)
    before = predictor.predict_order(THREE_IMAGE_ORDERS[0]).decision
    after = predictor.predict_order(THREE_IMAGE_ORDERS[0]).decision

    assert smooth_features != textured_features
    assert textured_features.laplacian_sharpness > smooth_features.laplacian_sharpness
    assert before == after == "low_risk_candidate"


def test_benchmark_rejects_non_three_image_orders() -> None:
    with pytest.raises(ValueError):
        benchmark_orders(FakePredictor, [(Path("a"), Path("b"))], warmup=0, repetitions=1)
