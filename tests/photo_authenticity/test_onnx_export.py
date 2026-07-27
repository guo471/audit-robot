from __future__ import annotations

import json

import onnx
import pytest
import torch
from torch import nn

from photo_authenticity.artifacts import (
    build_release_bundle,
    preprocess_contract_hash,
    verify_release_bundle,
)
from photo_authenticity.config import PreprocessConfig
from photo_authenticity.contracts import ReasonCode
from photo_authenticity.onnx_export import export_onnx, verify_onnx_equivalence


PREPROCESS = PreprocessConfig(
    image_size=16,
    fill_rgb=(0, 0, 0),
    mean=(0.485, 0.456, 0.406),
    std=(0.229, 0.224, 0.225),
)


class TinyOnnxModel(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.features = nn.Sequential(nn.Conv2d(3, 4, 3, padding=1), nn.ReLU())
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.classifier = nn.Linear(4, 2)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.pool(self.features(inputs)).flatten(1))


def _export_tiny(tmp_path):
    torch.manual_seed(20260713)
    model = TinyOnnxModel().eval()
    checkpoint = tmp_path / "checkpoint.pt"
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "model_architecture": "tiny_test",
            "output_order": ["real", "non_real"],
        },
        checkpoint,
    )
    output = tmp_path / "model.onnx"
    exported = export_onnx(checkpoint, output, PREPROCESS, model_factory=TinyOnnxModel)
    return model, exported


@pytest.fixture
def valid_bundle(tmp_path):
    _, exported = _export_tiny(tmp_path)
    thresholds = tmp_path / "thresholds.json"
    thresholds.write_text(
        json.dumps(
            {
                "low_risk": 0.2,
                "risk": 0.7,
                "model_sha256": exported.model_sha256,
                "exploratory": True,
                "selection_scope": "oof_validation",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "manifest_sha256": "b" * 64,
                "preprocessing_contract_hash": preprocess_contract_hash(PREPROCESS),
                "model_version": "test-v1",
                "output_order": ["real", "non_real"],
                "mode": "offline_shadow",
                "exploratory": True,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return build_release_bundle(exported.output_path, thresholds, metadata, tmp_path / "release")


def test_release_bundle_rejects_model_threshold_hash_mismatch(valid_bundle) -> None:
    valid_bundle.model_path.write_bytes(valid_bundle.model_path.read_bytes() + b"tamper")

    result = verify_release_bundle(valid_bundle.root)

    assert result.ok is False
    assert result.reason_code == ReasonCode.MODEL_HASH_MISMATCH


def test_exported_onnx_is_dynamic_and_numerically_equivalent(tmp_path) -> None:
    model, exported = _export_tiny(tmp_path)
    graph = onnx.load(exported.output_path)
    batch_dimension = graph.graph.input[0].type.tensor_type.shape.dim[0]
    tensors = [
        torch.zeros(1, 3, 16, 16),
        torch.randn(2, 3, 16, 16, generator=torch.Generator().manual_seed(7)),
    ]

    result = verify_onnx_equivalence(model, exported.output_path, tensors)

    assert batch_dimension.dim_param == "batch"
    assert result.ok is True
    assert result.max_absolute_difference <= 1e-5
    assert result.max_risk_difference <= 1e-5


def test_corrupt_onnx_and_swapped_thresholds_fail_verification(valid_bundle) -> None:
    valid_bundle.thresholds_path.write_text("{}", encoding="utf-8")
    threshold_result = verify_release_bundle(valid_bundle.root)
    valid_bundle.model_path.write_bytes(b"not an onnx model")
    model_result = verify_release_bundle(valid_bundle.root)

    assert threshold_result.ok is False
    assert threshold_result.reason_code == ReasonCode.THRESHOLD_MISMATCH
    assert model_result.ok is False
    assert model_result.reason_code == ReasonCode.MODEL_HASH_MISMATCH


def test_release_rejects_preprocessing_contract_metadata_mismatch(valid_bundle) -> None:
    release_payload = json.loads(valid_bundle.release_path.read_text(encoding="utf-8"))
    release_payload["preprocessing_contract_hash"] = "f" * 64
    valid_bundle.release_path.write_text(
        json.dumps(release_payload, sort_keys=True), encoding="utf-8"
    )

    result = verify_release_bundle(valid_bundle.root)

    assert result.ok is False
    assert result.reason_code == ReasonCode.THRESHOLD_MISMATCH


def test_equivalence_returns_failure_for_corrupt_onnx(tmp_path) -> None:
    path = tmp_path / "corrupt.onnx"
    path.write_bytes(b"broken")

    result = verify_onnx_equivalence(
        TinyOnnxModel().eval(), path, [torch.zeros(1, 3, 16, 16)]
    )

    assert result.ok is False
    assert result.error
