from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest
import torch
from PIL import Image
from torch import nn

from photo_authenticity.artifacts import build_release_bundle, preprocess_contract_hash
from photo_authenticity.config import PreprocessConfig
from photo_authenticity.contracts import ReasonCode
from photo_authenticity.inference import ShadowPredictor, predict_order_isolated
from photo_authenticity.onnx_export import export_onnx


PREPROCESS = PreprocessConfig(
    image_size=16,
    fill_rgb=(0, 0, 0),
    mean=(0.5, 0.5, 0.5),
    std=(0.5, 0.5, 0.5),
)


class BrightnessRiskModel(nn.Module):
    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        risk_logit = inputs.mean(dim=(1, 2, 3)) * 5.0
        return torch.stack((torch.zeros_like(risk_logit), risk_logit), dim=1)


def _crash_worker(queue, release_dir, image_paths, intra_op_threads) -> None:
    raise RuntimeError("synthetic child crash")


def _malformed_worker(queue, release_dir, image_paths, intra_op_threads) -> None:
    queue.put({"bad": "payload"})


def _release(tmp_path: Path, self_test_score: float = 0.5) -> Path:
    checkpoint = tmp_path / "checkpoint.pt"
    torch.save(
        {
            "model_state_dict": BrightnessRiskModel().state_dict(),
            "model_architecture": "brightness_test",
            "output_order": ["real", "non_real"],
        },
        checkpoint,
    )
    exported = export_onnx(
        checkpoint,
        tmp_path / "model.onnx",
        PREPROCESS,
        model_factory=BrightnessRiskModel,
    )
    thresholds = tmp_path / "thresholds.json"
    thresholds.write_text(
        json.dumps(
            {
                "low_risk": 0.6,
                "risk": 0.8,
                "model_sha256": exported.model_sha256,
                "exploratory": True,
                "selection_scope": "oof_validation",
            }
        ),
        encoding="utf-8",
    )
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "manifest_sha256": "b" * 64,
                "preprocessing_contract_hash": preprocess_contract_hash(PREPROCESS),
                "preprocess": asdict(PREPROCESS),
                "model_version": "synthetic-v1",
                "output_order": ["real", "non_real"],
                "mode": "offline_shadow",
                "exploratory": True,
                "self_test": {
                    "expected_non_real_risk": self_test_score,
                    "absolute_tolerance": 1e-5,
                },
            }
        ),
        encoding="utf-8",
    )
    return build_release_bundle(
        exported.output_path, thresholds, metadata, tmp_path / "release"
    ).root


def _images(tmp_path: Path) -> tuple[list[Path], Path]:
    dark = []
    for index in range(3):
        path = tmp_path / f"dark-{index}.png"
        Image.new("RGB", (16, 16), (0, 0, 0)).save(path)
        dark.append(path)
    bright = tmp_path / "bright.png"
    Image.new("RGB", (16, 16), (255, 255, 255)).save(bright)
    return dark, bright


@pytest.fixture
def inference_fault_case(tmp_path):
    def run(fault: str):
        case = tmp_path / fault
        case.mkdir()
        release = _release(case)
        images, _ = _images(case)
        if fault == "missing_model":
            (release / "model.onnx").unlink()
            return ShadowPredictor.start(release, 1)
        if fault == "corrupt_image":
            broken = case / "broken.png"
            broken.write_bytes(b"bad image")
            startup = ShadowPredictor.start(release, 1)
            assert startup.predictor is not None
            return startup.predictor.predict_image(broken)
        if fault == "threshold_mismatch":
            (release / "thresholds.json").write_text("{}", encoding="utf-8")
            return ShadowPredictor.start(release, 1)
        if fault == "runtime_error":
            startup = ShadowPredictor.start(release, 1)
            assert startup.predictor is not None
            startup.predictor._session = None
            return startup.predictor.predict_image(images[0])
        if fault == "timeout":
            return predict_order_isolated(
                release, images, 0.001, case / "timeout.jsonl"
            )
        raise AssertionError(f"unknown fault: {fault}")

    return run


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("missing_model", ReasonCode.MODEL_MISSING),
        ("corrupt_image", ReasonCode.IMAGE_CORRUPT),
        ("threshold_mismatch", ReasonCode.THRESHOLD_MISMATCH),
        ("runtime_error", ReasonCode.INFERENCE_ERROR),
        ("timeout", ReasonCode.TIMEOUT),
    ],
)
def test_every_fault_returns_manual_review(fault, reason, inference_fault_case) -> None:
    result = inference_fault_case(fault)

    assert result.decision == "manual_review"
    assert result.reason_code == reason


def test_startup_self_test_failure_is_fail_closed(tmp_path) -> None:
    startup = ShadowPredictor.start(_release(tmp_path, self_test_score=0.9), 1)

    assert startup.ok is False
    assert startup.decision == "manual_review"
    assert startup.reason_code == ReasonCode.SELF_TEST_FAILED


def test_three_low_risk_images_pass_shadow_but_one_risky_image_reviews(tmp_path) -> None:
    release = _release(tmp_path)
    dark, bright = _images(tmp_path)

    low = predict_order_isolated(release, dark, 10.0, tmp_path / "low.jsonl")
    risky = predict_order_isolated(
        release, [dark[0], bright, dark[2]], 10.0, tmp_path / "risky.jsonl"
    )

    assert low.decision == "low_risk_candidate"
    assert risky.decision == "manual_review"
    assert len(low.images) == 3
    log = json.loads((tmp_path / "low.jsonl").read_text(encoding="utf-8"))
    assert log["schema_version"] == 1
    assert log["mode"] == "offline_shadow"
    assert len(log["image_scores"]) == 3
    assert len(log["model_sha256"]) == 64
    assert len(log["thresholds_sha256"]) == 64


def test_child_crash_malformed_payload_and_bad_count_fail_closed(tmp_path) -> None:
    release = _release(tmp_path)
    images, _ = _images(tmp_path)

    crashed = predict_order_isolated(
        release,
        images,
        10.0,
        tmp_path / "crash.jsonl",
        worker_target=_crash_worker,
    )
    malformed = predict_order_isolated(
        release,
        images,
        10.0,
        tmp_path / "malformed.jsonl",
        worker_target=_malformed_worker,
    )
    wrong_count = predict_order_isolated(
        release, images[:2], 10.0, tmp_path / "count.jsonl"
    )

    assert crashed.reason_code == ReasonCode.INFERENCE_ERROR
    assert malformed.reason_code == ReasonCode.INFERENCE_ERROR
    assert wrong_count.reason_code == ReasonCode.INPUT_COUNT_INVALID
    assert all(item.decision == "manual_review" for item in (crashed, malformed, wrong_count))


def test_log_failure_overrides_low_risk_result(tmp_path) -> None:
    release = _release(tmp_path)
    images, _ = _images(tmp_path)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block", encoding="utf-8")

    result = predict_order_isolated(
        release, images, 10.0, blocker / "audit.jsonl"
    )

    assert result.decision == "manual_review"
    assert result.reason_code == ReasonCode.LOG_WRITE_FAILED
