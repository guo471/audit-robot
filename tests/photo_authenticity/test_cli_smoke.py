from __future__ import annotations

import contextlib
import inspect
import io
import json
import socket
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from PIL import Image
from torch import nn

import photo_authenticity.cli as cli_module
from photo_authenticity.artifacts import build_release_bundle, preprocess_contract_hash
from photo_authenticity.cli import _build_parser, main
from photo_authenticity.config import PreprocessConfig
from photo_authenticity.onnx_export import export_onnx


PREPROCESS = PreprocessConfig(
    image_size=16,
    fill_rgb=(0, 0, 0),
    mean=(0.5, 0.5, 0.5),
    std=(0.5, 0.5, 0.5),
)


class CliBrightnessModel(nn.Module):
    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        risk = inputs.mean(dim=(1, 2, 3)) * 5.0
        return torch.stack((torch.zeros_like(risk), risk), dim=1)


@pytest.fixture
def valid_release(tmp_path) -> Path:
    checkpoint = tmp_path / "checkpoint.pt"
    torch.save(
        {
            "model_state_dict": CliBrightnessModel().state_dict(),
            "output_order": ["real", "non_real"],
        },
        checkpoint,
    )
    exported = export_onnx(
        checkpoint,
        tmp_path / "model.onnx",
        PREPROCESS,
        model_factory=CliBrightnessModel,
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
                "model_version": "cli-synthetic-v1",
                "output_order": ["real", "non_real"],
                "mode": "offline_shadow",
                "exploratory": True,
                "self_test": {
                    "expected_non_real_risk": 0.5,
                    "absolute_tolerance": 1e-5,
                },
            }
        ),
        encoding="utf-8",
    )
    return build_release_bundle(
        exported.output_path, thresholds, metadata, tmp_path / "release"
    ).root


@pytest.fixture
def three_images(tmp_path) -> list[Path]:
    paths = []
    for index in range(3):
        path = tmp_path / f"image-{index}.png"
        Image.new("RGB", (16, 16), (0, 0, 0)).save(path)
        paths.append(path)
    return paths


@pytest.fixture
def deny_network(monkeypatch):
    calls = []

    def denied(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    return SimpleNamespace(calls=calls)


@pytest.fixture
def cli_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_OPERATION_LOG", str(tmp_path / "operations.jsonl"))

    def run(arguments):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(arguments)
        return SimpleNamespace(exit_code=exit_code, stdout=stdout.getvalue())

    return run


def test_infer_order_cli_is_offline_and_fail_closed(
    cli_runner, valid_release, three_images, deny_network
) -> None:
    result = cli_runner(
        ["infer-order", "--release", str(valid_release), *map(str, three_images)]
    )
    payload = json.loads(result.stdout)

    assert result.exit_code in {0, 3}
    assert payload["mode"] == "offline_shadow"
    assert payload["decision"] in {"low_risk_candidate", "manual_review"}
    assert "approved" not in result.stdout.lower()
    assert deny_network.calls == []


def test_inference_cli_boundary_converts_unexpected_exception(monkeypatch, cli_runner) -> None:
    monkeypatch.setattr(cli_module, "predict_order_isolated", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    result = cli_runner(["infer-order", "--release", "missing", "1", "2", "3"])
    payload = json.loads(result.stdout)

    assert result.exit_code == 3
    assert payload["decision"] == "manual_review"
    assert payload["reason_code"] == "inference_error"


def test_cli_registers_all_planned_subcommands_without_forbidden_imports() -> None:
    parser = _build_parser()
    subparser_action = next(action for action in parser._actions if action.dest == "command")
    expected = {
        "check-env",
        "build-manifest",
        "group-sources",
        "split",
        "train",
        "freeze-thresholds",
        "evaluate",
        "export-onnx",
        "verify-release",
        "infer-image",
        "infer-order",
        "benchmark",
        "append-samples",
        "compare-releases",
    }
    source = inspect.getsource(cli_module)

    assert set(subparser_action.choices) == expected
    for forbidden in ("modules", "audit_service", "run_audit", "requests", "boto3", "openai"):
        assert forbidden not in source


def test_check_env_emits_machine_readable_json(cli_runner) -> None:
    result = cli_runner(
        [
            "check-env",
            "--config",
            str(Path("photo_authenticity/configs/base.toml").resolve()),
        ]
    )
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["ok"] is True
    assert payload["mode"] == "offline_shadow"


def test_operator_documentation_contains_exact_offline_sequence_and_limits() -> None:
    root = Path("photo_authenticity")
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert "$env:PA_PYTHON='C:\\Users\\HUAWEI\\Desktop\\audit_robot\\.venv-photo-auth\\Scripts\\python.exe'" in readme
    assert "-m photo_authenticity.cli build-manifest" in readme
    assert "-m photo_authenticity.cli infer-order" in readme
    assert "not_runnable_insufficient_confirmed_data" in readme
    assert "weak_label" in readme and "exploratory" in readme
    assert "prohibition on production integration" in readme
    for relative in ("data/README.md", "models/README.md", "reports/README.md"):
        assert (root / relative).is_file()
