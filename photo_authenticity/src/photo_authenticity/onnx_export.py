from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import onnx
import onnxruntime as ort
import torch
from torch import nn

from .artifacts import preprocess_contract_hash
from .config import PreprocessConfig
from .hashing import sha256_file
from .modeling import build_model


@dataclass(frozen=True)
class ExportResult:
    output_path: Path
    model_sha256: str
    preprocessing_contract_hash: str
    opset: int
    input_name: str
    output_name: str


@dataclass(frozen=True)
class EquivalenceResult:
    ok: bool
    max_absolute_difference: float
    max_relative_difference: float
    max_risk_difference: float
    error: str | None = None


def export_onnx(
    checkpoint: Path,
    output: Path,
    preprocess: PreprocessConfig,
    opset: int = 17,
    *,
    model_factory: Callable[[], nn.Module] | None = None,
) -> ExportResult:
    payload = torch.load(checkpoint.resolve(), map_location="cpu", weights_only=True)
    if not isinstance(payload, dict) or "model_state_dict" not in payload:
        raise ValueError("checkpoint does not contain model_state_dict")
    if payload.get("output_order") != ["real", "non_real"]:
        raise ValueError("checkpoint output order is not [real, non_real]")
    model = (model_factory or (lambda: build_model(weights="none")))()
    model.load_state_dict(payload["model_state_dict"])
    model.eval()
    output_path = output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sample = torch.zeros(1, 3, preprocess.image_size, preprocess.image_size, dtype=torch.float32)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="You are using the legacy TorchScript-based ONNX export.*",
            category=DeprecationWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message="The feature will be removed. Please remove usage of this function",
            category=DeprecationWarning,
        )
        torch.onnx.export(
            model,
            sample,
            output_path,
            export_params=True,
            opset_version=opset,
            input_names=["image"],
            output_names=["logits"],
            dynamic_axes={"image": {0: "batch"}, "logits": {0: "batch"}},
            dynamo=False,
        )
    onnx_model = onnx.load(output_path)
    onnx.checker.check_model(onnx_model)
    return ExportResult(
        output_path,
        sha256_file(output_path),
        preprocess_contract_hash(preprocess),
        opset,
        "image",
        "logits",
    )


def _softmax_non_real(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=1, keepdims=True)
    exponentials = np.exp(shifted)
    return exponentials[:, 1] / np.sum(exponentials, axis=1)


def verify_onnx_equivalence(
    torch_model: nn.Module,
    onnx_path: Path,
    tensors: Sequence[torch.Tensor],
    atol: float = 1e-5,
    rtol: float = 1e-4,
) -> EquivalenceResult:
    try:
        onnx_model = onnx.load(onnx_path.resolve())
        onnx.checker.check_model(onnx_model)
        session = ort.InferenceSession(
            str(onnx_path.resolve()), providers=["CPUExecutionProvider"]
        )
        input_name = session.get_inputs()[0].name
        torch_model.eval()
        max_absolute = 0.0
        max_relative = 0.0
        max_risk = 0.0
        for tensor in tensors:
            inputs = tensor.detach().cpu().to(torch.float32)
            with torch.no_grad():
                torch_logits = torch_model(inputs).detach().cpu().numpy()
            onnx_logits = session.run(None, {input_name: inputs.numpy()})[0]
            absolute = np.abs(torch_logits - onnx_logits)
            relative = absolute / np.maximum(np.abs(torch_logits), 1e-12)
            torch_risk = _softmax_non_real(torch_logits)
            onnx_risk = _softmax_non_real(onnx_logits)
            max_absolute = max(max_absolute, float(np.max(absolute)))
            max_relative = max(max_relative, float(np.max(relative)))
            max_risk = max(max_risk, float(np.max(np.abs(torch_risk - onnx_risk))))
            if not np.allclose(torch_logits, onnx_logits, atol=atol, rtol=rtol):
                return EquivalenceResult(
                    False,
                    max_absolute,
                    max_relative,
                    max_risk,
                    "PyTorch and ONNX logits differ beyond tolerance",
                )
        return EquivalenceResult(True, max_absolute, max_relative, max_risk)
    except Exception as exc:
        return EquivalenceResult(False, float("inf"), float("inf"), float("inf"), str(exc))
