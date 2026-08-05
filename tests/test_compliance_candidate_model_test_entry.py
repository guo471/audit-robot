# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = PROJECT_ROOT / "tools" / "run_compliance_candidate_model_test.ps1"


def _entry_module():
    try:
        return importlib.import_module("tools.compliance_candidate_model_test")
    except ModuleNotFoundError:
        pytest.fail("fixed compliance model-test entry module is missing")


def _write_dataset(tmp_path: Path) -> Path:
    image_root = tmp_path / "图片 证据"
    image_root.mkdir(parents=True)
    images = []
    for index, title in enumerate(("商品照片", "拆封照片", "SN码采集/激活照片"), 1):
        path = image_root / f"img_{index:03d}.jpg"
        path.write_bytes(b"jpeg")
        images.append(
            {
                "image_id": f"img_{index:03d}",
                "title": title,
                "local_path": str(path),
                "source_url": "",
            }
        )
    record = {
        "渠道订单号": "SYNTHETIC-ORDER-001",
        "订单品类/商品类型": "[B01] 手机",
        "商品照片": [images[0]],
        "拆封/安装照片": [images[1]],
        "激活/SN照片": [images[2]],
        "原始流程状态": "未通过",
    }
    dataset = tmp_path / "固定 输入" / "orders.jsonl"
    dataset.parent.mkdir(parents=True)
    dataset.write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return dataset


def test_preflight_creates_missing_chinese_space_directories_without_model_call(tmp_path):
    entry = _entry_module()
    dataset = _write_dataset(tmp_path)
    output_dir = tmp_path / "结果 空间" / "候选 输出"
    cache_dir = tmp_path / "短 缓存"

    result = entry.run_preflight(
        dataset_path=dataset,
        output_dir=output_dir,
        cache_dir=cache_dir,
        expected_order_count=1,
    )

    assert result["status"] == "ready"
    assert result["model_calls"] == 0
    assert result["dataset_order_count"] == 1
    assert result["candidate_version"] == "compliance-candidate-v6-20260804"
    assert result["candidate_stage"] == "compliance_candidate_v6"
    assert output_dir.is_dir()
    assert cache_dir.is_dir()
    assert list(cache_dir.iterdir()) == []
    assert not (output_dir / "results.jsonl").exists()


def test_preflight_rejects_long_cache_path_before_creating_or_calling_model(tmp_path):
    entry = _entry_module()
    dataset = _write_dataset(tmp_path)
    output_dir = tmp_path / "output"
    long_cache_dir = tmp_path / ("x" * 110) / ("y" * 90)

    with pytest.raises(RuntimeError, match="cache temporary path is too long"):
        entry.run_preflight(
            dataset_path=dataset,
            output_dir=output_dir,
            cache_dir=long_cache_dir,
            expected_order_count=1,
        )

    assert not long_cache_dir.exists()
    assert not (output_dir / "results.jsonl").exists()


def test_dependency_gate_reports_missing_dependency_before_run(monkeypatch):
    entry = _entry_module()
    real_import = entry.importlib.import_module

    def fake_import(name: str):
        if name == "joblib":
            raise ModuleNotFoundError("joblib is absent")
        return real_import(name)

    monkeypatch.setattr(entry.importlib, "import_module", fake_import)

    with pytest.raises(RuntimeError, match="missing runtime dependency: joblib"):
        entry.check_runtime_dependencies()


def test_powershell_entry_preserves_chinese_space_arguments_and_uses_secret_loader(tmp_path):
    assert WRAPPER.is_file(), "fixed PowerShell entry is missing"
    dataset = _write_dataset(tmp_path)
    output_dir = tmp_path / "输出 目录" / "候选"
    cache_dir = tmp_path / "缓存 目录"
    secrets = tmp_path / "vision.env"
    secrets.write_text(
        "VISION_API_BASE_URL=https://offline.invalid/v1\n"
        "VISION_API_KEY=dummy-key\n"
        "VISION_MODEL_NAME=qwen3.7-plus\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WRAPPER),
            "-Action",
            "self-check",
            "-Dataset",
            str(dataset),
            "-OutputDir",
            str(output_dir),
            "-CacheDir",
            str(cache_dir),
            "-ExpectedOrderCount",
            "1",
            "-PythonExe",
            sys.executable,
            "-SecretsPath",
            str(secrets),
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ready"
    assert payload["model_calls"] == 0
    assert Path(payload["dataset_path"]) == dataset.resolve()
    assert Path(payload["output_dir"]) == output_dir.resolve()
    assert Path(payload["cache_dir"]) == cache_dir.resolve()


def test_powershell_entry_rejects_old_huawei_interpreter_path_before_execution(tmp_path):
    assert WRAPPER.is_file(), "fixed PowerShell entry is missing"
    dataset = _write_dataset(tmp_path)
    secrets = tmp_path / "vision.env"
    secrets.write_text(
        "VISION_API_BASE_URL=https://offline.invalid/v1\n"
        "VISION_API_KEY=dummy-key\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WRAPPER),
            "-Action",
            "self-check",
            "-Dataset",
            str(dataset),
            "-OutputDir",
            str(tmp_path / "out"),
            "-CacheDir",
            str(tmp_path / "cache"),
            "-ExpectedOrderCount",
            "1",
            "-PythonExe",
            r"C:\Users\HUAWEI\Python311\python.exe",
            "-SecretsPath",
            str(secrets),
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode != 0
    assert "HUAWEI" in completed.stderr
