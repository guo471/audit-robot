# -*- coding: utf-8 -*-
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools import run_guobu_sn_v2 as runner
from tools.guobu_sn_policy_v2 import SCHEMA_VERSION, SN_LOGIC_VERSION
from tools.run_guobu_sn_v2 import audit_task_sn_v2


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def task(product_type="手机", system_sn="SECRET-SN-123", with_image=True):
    image_groups = {}
    if with_image:
        image_groups = {
            "SN码采集 / 激活照片": [
                {
                    "image_id": "img_003",
                    "title": "SN码采集 / 激活照片",
                    "source_url": "https://example.test/sn.jpg",
                }
            ]
        }
    return {
        "task_id": "task-order-1",
        "channel_order_no": "order-1",
        "fields": {
            "product_type": product_type,
            "cate_code_name": product_type,
            "goods_name": product_type,
            "system_sn": system_sn,
        },
        "image_groups": image_groups,
    }


def screen_evidence(value="SECRET-SN-123"):
    return {
        "schema_version": SCHEMA_VERSION,
        "sn_readable": True,
        "screen_identity_state": "SCREEN_SN_CLEAR",
        "sn_candidates": [
            {
                "image_id": "img_003",
                "source": "DEVICE_SCREEN",
                "field_type": "SN",
                "label_text": "SN",
                "raw_text": value,
                "raw_context": f"SN: {value}",
                "normalized_text": value,
                "label_binding": "EXPLICIT",
                "readable": True,
                "complete": True,
                "confidence": 0.99,
                "visual_ambiguity_notes": [],
            }
        ],
        "identity_evidence": [],
        "confidence": 0.99,
    }


def home_evidence(value="SECRET-SN-123"):
    result = screen_evidence(value)
    result["screen_identity_state"] = "NO_SCREEN_IDENTITY"
    result["sn_candidates"][0]["source"] = "PACKAGE_LABEL"
    return result


def barcode_result(text, *, fmt="CODE_128", field_type=""):
    result = {"text": text, "format": fmt}
    if field_type:
        result["field_type"] = field_type
    return result


def test_runner_model_request_contains_no_system_sn_or_derived_hint(tmp_path):
    calls = []

    def fake_model_caller(_base, _key, _model, prompt, payload, images, **kwargs):
        calls.append((prompt, payload, images, kwargs))
        serialized = prompt + json.dumps(payload, ensure_ascii=False)
        assert "SECRET-SN-123" not in serialized
        assert "system_sn" not in serialized.lower()
        assert "comparison" not in serialized.lower()
        return screen_evidence(), json.dumps(screen_evidence()), 0.25, {"total_tokens": 123}, False

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "qwen3.7-plus",
        task(),
        model_caller=fake_model_caller,
        cache_dir=tmp_path,
    )

    assert len(calls) == 1
    assert result["row"]["manual_flag"] == "否"
    assert result["row"]["sn_match"] is True
    assert result["row"]["strategy"] == "sn_v2_sidecar"
    assert result["row"]["sn_version"] == SN_LOGIC_VERSION
    assert result["row"]["barcode_mode"] == "shadow"
    assert result["row"]["model_calls"] == 1
    assert result["row"]["total_tokens"] == 123
    assert result["_raw"]["model_result"]["schema_version"] == SCHEMA_VERSION


def test_runner_does_not_call_model_for_unsupported_category():
    def fail_model_caller(*_args, **_kwargs):
        raise AssertionError("model must not be called")

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "qwen3.7-plus",
        task(product_type="数码相机"),
        model_caller=fail_model_caller,
    )

    assert result["row"]["manual_flag"] == "是"
    assert result["row"]["manual_reason"] == "该商品品类暂未配置SN自动审核规则"
    assert result["row"]["model_calls"] == 0


def test_runner_does_not_call_model_when_activation_group_is_missing():
    def fail_model_caller(*_args, **_kwargs):
        raise AssertionError("model must not be called")

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "qwen3.7-plus",
        task(with_image=False),
        model_caller=fail_model_caller,
    )

    assert result["row"]["manual_flag"] == "是"
    assert result["row"]["manual_reason_code"] == "SN_NOT_FOUND"
    assert result["row"]["model_calls"] == 0


def test_runner_can_be_executed_directly_from_project_root():
    completed = subprocess.run(
        [sys.executable, "tools/run_guobu_sn_v2.py", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--tasks-dir" in completed.stdout


def test_runner_is_compatible_with_clean_v1_caller_signature():
    def clean_v1_caller(
        _base,
        _key,
        _model,
        _prompt,
        _payload,
        _images,
        *,
        stage,
        cache_dir=None,
        detail="auto",
        timeout_sec=60,
        retry_timeout_sec=0,
    ):
        assert stage == "sn_v2_evidence"
        assert detail == "high"
        return screen_evidence(), "{}", 0.1, {}, False

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "model",
        task(),
        model_caller=clean_v1_caller,
    )
    assert result["row"]["manual_flag"] == "否"


def test_runner_rejects_evidence_from_unknown_input_image():
    model_result = screen_evidence()
    model_result["sn_candidates"][0]["image_id"] = "not-an-input-image"

    def fake_model_caller(*_args, **_kwargs):
        return model_result, "{}", 0.1, {}, False

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "model",
        task(),
        model_caller=fake_model_caller,
    )
    assert result["row"]["manual_flag"] == "是"
    assert result["row"]["manual_reason_code"] == "MODEL_UNCERTAIN"


def test_runner_passes_mainline_effective_category_into_sn_v2(monkeypatch):
    monkeypatch.setattr(
        runner.v1_transport,
        "effective_product_category",
        lambda _fields: "home_appliance",
    )

    def fake_model_caller(_base, _key, _model, prompt, payload, _images, **_kwargs):
        assert len(prompt) <= 500
        assert "screen_identity_state" in prompt
        assert payload["audit_category"] == "HOME_APPLIANCE"
        return home_evidence(), "{}", 0.1, {}, False

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "model",
        task(product_type="手机"),
        model_caller=fake_model_caller,
    )

    assert result["row"]["audit_category"] == "HOME_APPLIANCE"
    assert result["row"]["manual_flag"] == "否"


def test_barcode_enforce_rescues_numeric_system_sn_after_sn_mismatch():
    calls = []

    def fake_model_caller(*_args, **_kwargs):
        return screen_evidence("999999999999999"), "{}", 0.1, {}, False

    def fake_barcode_scanner(_task, images):
        calls.append(images)
        return [barcode_result("123456789012345")]

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "model",
        task(system_sn="123456789012345"),
        model_caller=fake_model_caller,
        barcode_scanner=fake_barcode_scanner,
        barcode_mode="enforce",
    )

    assert len(calls) == 1
    assert result["row"]["manual_flag"] == "否"
    assert result["row"]["sn_match"] is True
    assert result["row"]["selected_source"] == "BARCODE"
    assert result["_raw"]["barcode_result"]["match_type"] == "exact"


def test_barcode_enforce_accepts_single_leading_s_package_prefix():
    def fake_model_caller(*_args, **_kwargs):
        return screen_evidence("WRONG123"), "{}", 0.1, {}, False

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "model",
        task(system_sn="H0QW1CDVWD"),
        model_caller=fake_model_caller,
        barcode_scanner=lambda _task, _images: [barcode_result("SH0QW1CDVWD")],
        barcode_mode="enforce",
    )

    assert result["row"]["manual_flag"] == "否"
    assert result["row"]["observed_sn"] == "SH0QW1CDVWD"
    assert result["_raw"]["barcode_result"]["match_type"] == "leading_s_prefix"


def test_barcode_does_not_clean_slash_to_match_system_sn():
    def fake_model_caller(*_args, **_kwargs):
        return screen_evidence("WRONG123"), "{}", 0.1, {}, False

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "model",
        task(system_sn="69716F5ZA00395"),
        model_caller=fake_model_caller,
        barcode_scanner=lambda _task, _images: [barcode_result("69716/F5ZA00395")],
        barcode_mode="enforce",
    )

    assert result["row"]["manual_flag"] == "是"
    assert result["row"]["manual_reason_code"] == "SN_MISMATCH"
    assert result["_raw"]["barcode_result"]["matched"] is False


@pytest.mark.parametrize(
    "decoded",
    [
        barcode_result("123456789012345", field_type="IMEI"),
        barcode_result("6901234567890", fmt="EAN_13"),
        barcode_result("123456789012", fmt="UPC_A"),
    ],
)
def test_barcode_rejects_imei_ean_and_upc(decoded):
    def fake_model_caller(*_args, **_kwargs):
        return screen_evidence("WRONG123"), "{}", 0.1, {}, False

    system_sn = decoded["text"]
    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "model",
        task(system_sn=system_sn),
        model_caller=fake_model_caller,
        barcode_scanner=lambda _task, _images: [decoded],
        barcode_mode="enforce",
    )

    assert result["row"]["manual_flag"] == "是"
    assert result["row"]["manual_reason_code"] == "SN_MISMATCH"
    assert result["_raw"]["barcode_result"]["matched"] is False


def test_barcode_is_not_called_when_local_sn_passes():
    def fail_barcode_scanner(*_args, **_kwargs):
        raise AssertionError("barcode scanner must not run after local PASS")

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "model",
        task(system_sn="SECRET-SN-123"),
        model_caller=lambda *_args, **_kwargs: (screen_evidence(), "{}", 0.1, {}, False),
        barcode_scanner=fail_barcode_scanner,
        barcode_mode="enforce",
    )

    assert result["row"]["manual_flag"] == "否"
    assert "barcode_result" not in result["_raw"]


def test_barcode_mismatch_keeps_sn_mismatch():
    def fake_model_caller(*_args, **_kwargs):
        return screen_evidence("WRONG123"), "{}", 0.1, {}, False

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "model",
        task(system_sn="ABC123"),
        model_caller=fake_model_caller,
        barcode_scanner=lambda _task, _images: [barcode_result("XYZ999")],
        barcode_mode="enforce",
    )

    assert result["row"]["manual_flag"] == "是"
    assert result["row"]["manual_reason_code"] == "SN_MISMATCH"
    assert result["_raw"]["barcode_result"]["matched"] is False


def test_barcode_shadow_records_match_without_rescuing_final_decision():
    def fake_model_caller(*_args, **_kwargs):
        return screen_evidence("WRONG123"), "{}", 0.1, {}, False

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "model",
        task(system_sn="ABC123"),
        model_caller=fake_model_caller,
        barcode_scanner=lambda _task, _images: [barcode_result("ABC123")],
        barcode_mode="shadow",
    )

    assert result["row"]["manual_flag"] == "是"
    assert result["row"]["manual_reason_code"] == "SN_MISMATCH"
    assert result["_raw"]["barcode_result"]["matched"] is True
    assert result["_raw"]["barcode_result"]["match_type"] == "exact"


def test_barcode_mode_uses_default_scanner_when_no_scanner_is_injected(monkeypatch):
    def fake_model_caller(*_args, **_kwargs):
        return screen_evidence("WRONG123"), "{}", 0.1, {}, False

    monkeypatch.setattr(runner, "_scan_activation_barcodes", lambda _task, _images: [barcode_result("ABC123")])

    result = audit_task_sn_v2(
        "https://unused",
        "key",
        "model",
        task(system_sn="ABC123"),
        model_caller=fake_model_caller,
        barcode_mode="enforce",
    )

    assert result["row"]["manual_flag"] == "否"
    assert result["row"]["selected_source"] == "BARCODE"


def test_batch_preserves_input_order_and_records_worker_failure(tmp_path, monkeypatch):
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "001.json").write_text("{}", encoding="utf-8")
    (tasks_dir / "002.json").write_text("{}", encoding="utf-8")
    output_path = tmp_path / "result.jsonl"

    def fake_audit_path(index, _task_path, **_kwargs):
        if index == 1:
            time.sleep(0.2)
            raise RuntimeError("first task failure")
        return index, {
            "task": {"channel_order_no": "order-2"},
            "row": {
                "id": "order-2",
                "manual_flag": "否",
                "manual_reason_code": "",
            },
        }

    monkeypatch.setattr(runner, "_audit_path", fake_audit_path)
    runner.run_batch(
        tasks_dir,
        output_path,
        base_url="https://unused",
        api_key="key",
        model="model",
        workers=2,
    )

    saved = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert [item["row"]["id"] for item in saved] == ["001", "order-2"]
    assert saved[0]["row"]["manual_reason_code"] == "MODEL_UNCERTAIN"


def test_batch_records_invalid_task_json_instead_of_stopping(tmp_path):
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "broken-order.json").write_text("not-json", encoding="utf-8")
    output_path = tmp_path / "result.jsonl"

    runner.run_batch(
        tasks_dir,
        output_path,
        base_url="https://unused",
        api_key="key",
        model="model",
    )

    saved = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert saved["row"]["id"] == "broken-order"
    assert saved["row"]["manual_reason_code"] == "MODEL_UNCERTAIN"
