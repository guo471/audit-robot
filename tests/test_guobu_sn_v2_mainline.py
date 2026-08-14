# -*- coding: utf-8 -*-
import csv
import json
from pathlib import Path

import pytest

from tools import run_guobu_model_audit_v2 as mainline


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _keep_legacy_compliance_contract(monkeypatch):
    monkeypatch.setenv("COMPLIANCE_RULESET", "legacy")


def task():
    def image(image_id, title, url):
        return {"image_id": image_id, "title": title, "source_url": url}

    return {
        "channel_order_no": "order-1",
        "fields": {
            "product_type": "手机",
            "cate_code_name": "手机",
            "goods_name": "测试手机",
            "system_sn": "ABC123",
            "address": "",
        },
        "image_groups": {
            "商品照片": [image("img_001", "商品照片", "https://example.test/product.jpg")],
            "拆封照片": [image("img_002", "拆封照片", "https://example.test/unboxing.jpg")],
            "SN码采集 / 激活照片": [
                image("img_003", "SN码采集 / 激活照片", "https://example.test/sn.jpg")
            ],
        },
    }


def sn_evidence(value="ABC123"):
    return {
        "schema_version": "guobu_sn_evidence_v2",
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


def compliance_pass():
    return {
        "manual_required": False,
        "manual_reason_codes": [],
        "manual_reason": "",
        "product_type_match": True,
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "activation_photo_ok": True,
        "activation_evidence_type": "SCREEN_SN",
        "confidence": 0.99,
    }


def test_cli_defaults_to_sn_v2(monkeypatch):
    monkeypatch.delenv("SN_POLICY_VERSION", raising=False)
    args = mainline.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert args.sn_policy_version == "v2"

    explicit_v2 = mainline.parse_cli_args(
        ["--tasks-dir", "tasks", "--out-dir", "out", "--sn-policy-version", "v2"]
    )
    assert explicit_v2.sn_policy_version == "v2"


def test_hybrid_v2_sn_match_continues_existing_compliance_chain(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    calls = []

    def fake_call(_base, _key, _model, prompt, payload, _images, *, stage, **_kwargs):
        calls.append(stage)
        if stage == "hybrid_sn_v2":
            serialized = prompt + json.dumps(payload, ensure_ascii=False)
            assert "ABC123" not in serialized
            assert "system_sn" not in serialized.lower()
            assert len(prompt) <= 500
            assert "screen_identity_state" in prompt
            return sn_evidence(), "sn-v2", 0.1, {"total_tokens": 10}, False
        if stage == "hybrid_compliance":
            return compliance_pass(), "compliance", 0.2, {"total_tokens": 20}, False
        raise AssertionError(f"unexpected model stage: {stage}")

    monkeypatch.setattr(mainline, "call_model_with_retry", fake_call)
    monkeypatch.setattr(mainline, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)

    result = mainline.audit_task_hybrid(
        "https://unused", "key", "model", task(), sn_policy_version="v2",
    )

    assert calls == ["hybrid_sn_v2", "hybrid_compliance"]
    assert result["manual_flag"] == "否"
    assert result["sn_match"] is True
    assert result["strategy"] == "hybrid_sn_v2_then_compliance"
    assert result["model_calls"] == 2
    assert result["total_tokens"] == 30


def test_hybrid_v2_sn_mismatch_stops_before_compliance(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    calls = []

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        calls.append(stage)
        if stage != "hybrid_sn_v2":
            raise AssertionError("compliance must not run after V2 SN mismatch")
        return sn_evidence("XYZ999"), "sn-v2", 0.1, {"total_tokens": 10}, False

    monkeypatch.setattr(mainline, "call_model_with_retry", fake_call)

    result = mainline.audit_task_hybrid(
        "https://unused", "key", "model", task(), sn_policy_version="v2",
    )

    assert calls == ["hybrid_sn_v2"]
    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["strategy"] == "hybrid_sn_v2_manual"


def test_hybrid_v2_system_sn_letter_o_is_not_rewritten_to_match_model_zero(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    calls = []
    order = task()
    order["fields"]["system_sn"] = "3B164BOORNP00000"

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        calls.append(stage)
        if stage != "hybrid_sn_v2":
            raise AssertionError("compliance must not run after V2 SN mismatch")
        return sn_evidence("3B164B00RNP00000"), "sn-v2", 0.1, {"total_tokens": 10}, False

    monkeypatch.setattr(mainline, "call_model_with_retry", fake_call)

    result = mainline.audit_task_hybrid(
        "https://unused", "key", "model", order, sn_policy_version="v2",
    )

    assert calls == ["hybrid_sn_v2"]
    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["sn_match"] is False
    assert result["strategy"] == "hybrid_sn_v2_manual"


def test_hybrid_v2_barcode_enforce_rescues_and_continues_compliance(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    calls = []

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        calls.append(stage)
        if stage == "hybrid_sn_v2":
            return sn_evidence("XYZ999"), "sn-v2", 0.1, {"total_tokens": 10}, False
        if stage == "hybrid_compliance":
            return compliance_pass(), "compliance", 0.2, {"total_tokens": 20}, False
        raise AssertionError(f"unexpected model stage: {stage}")

    monkeypatch.setattr(mainline, "call_model_with_retry", fake_call)
    monkeypatch.setattr(mainline, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)

    result = mainline.audit_task_hybrid(
        "https://unused",
        "key",
        "model",
        task(),
        sn_policy_version="v2",
        sn_barcode_mode="enforce",
        barcode_scanner=lambda _task, _images: [{"text": "ABC123", "format": "CODE_128"}],
    )

    assert calls == ["hybrid_sn_v2", "hybrid_compliance"]
    assert result["sn_match"] is True
    assert result["observed_sn"] == "ABC123"
    assert result["model_sn"] == "XYZ999"
    assert result["new_final_result"] == "通过"
    assert result["sn_barcode_mode"] == "enforce"
    assert result["barcode_attempted"] is True
    assert result["barcode_matched"] is True
    assert result["barcode_values"] == ["ABC123"]
    assert result["barcode_rescued"] is True
    assert result["strategy"] == "hybrid_sn_v2_then_compliance"
    assert result["_raw"]["sn_barcode_result"]["match_type"] == "exact"


def test_hybrid_v2_barcode_enforce_prepares_source_url_images_for_scanner(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    scanner_images = []

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        if stage == "hybrid_sn_v2":
            return sn_evidence("XYZ999"), "sn-v2", 0.1, {"total_tokens": 10}, False
        if stage == "hybrid_compliance":
            return compliance_pass(), "compliance", 0.2, {"total_tokens": 20}, False
        raise AssertionError(f"unexpected model stage: {stage}")

    def fake_prepare(images, *, cache_dir):
        assert cache_dir == tmp_path
        return [dict(image, local_path=str(tmp_path / f"{image['image_id']}.jpg")) for image in images]

    def fake_scanner(_task, images):
        scanner_images.extend(images)
        return [{"text": "ABC123", "format": "CODE_128"}]

    monkeypatch.setattr(mainline, "call_model_with_retry", fake_call)
    monkeypatch.setattr(mainline, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)
    monkeypatch.setattr(mainline, "_prepare_barcode_activation_images", fake_prepare)

    result = mainline.audit_task_hybrid(
        "https://unused",
        "key",
        "model",
        task(),
        cache_dir=tmp_path,
        sn_policy_version="v2",
        sn_barcode_mode="enforce",
        barcode_scanner=fake_scanner,
    )

    assert result["barcode_attempted"] is True
    assert result["barcode_rescued"] is True
    assert scanner_images
    assert all(image.get("local_path") for image in scanner_images)


def test_hybrid_v2_barcode_fields_are_safe_for_csv_output(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        if stage == "hybrid_sn_v2":
            return sn_evidence("XYZ999"), "sn-v2", 0.1, {"total_tokens": 10}, False
        if stage == "hybrid_compliance":
            return compliance_pass(), "compliance", 0.2, {"total_tokens": 20}, False
        raise AssertionError(f"unexpected model stage: {stage}")

    monkeypatch.setattr(mainline, "call_model_with_retry", fake_call)
    monkeypatch.setattr(mainline, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)

    result = mainline.audit_task_hybrid(
        "https://unused",
        "key",
        "model",
        task(),
        sn_policy_version="v2",
        sn_barcode_mode="enforce",
        barcode_scanner=lambda _task, _images: [{"text": "ABC123", "format": "CODE_128"}],
    )
    row = {key: value for key, value in result.items() if not key.startswith("_")}
    fieldnames = [key for key, _label in mainline.CSV_COLUMNS]

    assert set(row) <= set(fieldnames)
    csv_path = tmp_path / "report.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writerow({key: label for key, label in mainline.CSV_COLUMNS})
        writer.writerow(row)

    assert "ABC123" in csv_path.read_text(encoding="utf-8-sig")


def test_hybrid_v2_identity_code_mismatch_does_not_prepare_or_scan_barcode(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")

    evidence = sn_evidence("ABC123")
    evidence["identity_evidence"] = [
        {
            "image_id": "img_003",
            "field_type": "IMEI1",
            "label_text": "IMEI1",
            "raw_text": "999999999999999",
            "readable": True,
            "complete": True,
        }
    ]
    identity_task = task()
    identity_task["fields"]["imei1"] = "111111111111111"

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        if stage != "hybrid_sn_v2":
            raise AssertionError("identity mismatch must stop before compliance")
        return evidence, "sn-v2", 0.1, {"total_tokens": 10}, False

    def fail_prepare(*_args, **_kwargs):
        raise AssertionError("identity mismatch must not prepare barcode images")

    def fail_scanner(*_args, **_kwargs):
        raise AssertionError("identity mismatch must not scan barcodes")

    monkeypatch.setattr(mainline, "call_model_with_retry", fake_call)
    monkeypatch.setattr(mainline, "_prepare_barcode_activation_images", fail_prepare)

    result = mainline.audit_task_hybrid(
        "https://unused",
        "key",
        "model",
        identity_task,
        cache_dir=tmp_path,
        sn_policy_version="v2",
        sn_barcode_mode="enforce",
        barcode_scanner=fail_scanner,
    )

    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["barcode_attempted"] is False
    assert "sn_barcode_result" not in result["_raw"]


def test_hybrid_v2_sn_and_identity_mismatch_cannot_be_rescued_by_barcode(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")

    evidence = sn_evidence("XYZ999")
    evidence["identity_evidence"] = [
        {
            "image_id": "img_003",
            "source": "DEVICE_SCREEN",
            "field_type": "IMEI1",
            "label_text": "IMEI1",
            "raw_text": "999999999999999",
            "readable": True,
            "complete": True,
        }
    ]
    identity_task = task()
    identity_task["fields"]["imei1"] = "111111111111111"

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        if stage != "hybrid_sn_v2":
            raise AssertionError("identity mismatch must stop before compliance")
        return evidence, "sn-v2", 0.1, {"total_tokens": 10}, False

    def fail_scanner(*_args, **_kwargs):
        raise AssertionError("barcode must not rescue when explicit identity code mismatches")

    monkeypatch.setattr(mainline, "call_model_with_retry", fake_call)

    result = mainline.audit_task_hybrid(
        "https://unused",
        "key",
        "model",
        identity_task,
        sn_policy_version="v2",
        sn_barcode_mode="enforce",
        barcode_scanner=fail_scanner,
    )

    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["sn_match"] is False
    assert result["barcode_attempted"] is False
    assert "sn_barcode_result" not in result["_raw"]


def test_hybrid_v2_computer_identity_code_mismatch_blocks(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")

    evidence = sn_evidence("ABC123")
    evidence["identity_evidence"] = [
        {
            "image_id": "img_003",
            "source": "DEVICE_SCREEN",
            "field_type": "IMEI2",
            "label_text": "IMEI-2",
            "raw_text": "999999999999999",
            "readable": True,
            "complete": True,
        }
    ]
    computer_task = task()
    computer_task["fields"]["product_type"] = "computer"
    computer_task["fields"]["cate_code_name"] = "computer"
    computer_task["fields"]["imei2"] = "222222222222222"

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        if stage != "hybrid_sn_v2":
            raise AssertionError("computer identity mismatch must stop before compliance")
        return evidence, "sn-v2", 0.1, {"total_tokens": 10}, False

    monkeypatch.setattr(mainline, "call_model_with_retry", fake_call)

    result = mainline.audit_task_hybrid(
        "https://unused",
        "key",
        "model",
        computer_task,
        sn_policy_version="v2",
        sn_barcode_mode="enforce",
        barcode_scanner=lambda *_args, **_kwargs: [{"text": "ABC123", "format": "CODE_128"}],
    )

    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["sn_match"] is False
    assert result["barcode_attempted"] is False
    assert "sn_barcode_result" not in result["_raw"]


def test_download_barcode_image_to_local_writes_file_without_network(monkeypatch, tmp_path):
    class FakeResponse:
        headers = {"Content-Type": "image/png"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return b"fake-image-bytes"

    monkeypatch.setattr(mainline, "_barcode_source_url_is_allowed", lambda _url: True)
    monkeypatch.setattr(mainline, "_open_barcode_image_request", lambda _request, timeout: FakeResponse())

    prepared, status = mainline._download_barcode_image_to_local(
        {"image_id": "img:003", "source_url": "https://static.jhddsz.com/test/sn.png"},
        tmp_path,
        index=1,
    )

    local_path = Path(prepared["local_path"])
    assert status["status"] == "downloaded"
    assert local_path.is_file()
    assert local_path.suffix == ".png"
    assert local_path.read_bytes() == b"fake-image-bytes"


def test_hybrid_v2_barcode_scanner_failure_is_visible_and_stays_manual(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        if stage != "hybrid_sn_v2":
            raise AssertionError("compliance must not run after scanner failure leaves mismatch")
        return sn_evidence("XYZ999"), "sn-v2", 0.1, {"total_tokens": 10}, False

    def fail_scanner(_task, _images):
        raise RuntimeError("zxing failed")

    monkeypatch.setattr(mainline, "call_model_with_retry", fake_call)

    result = mainline.audit_task_hybrid(
        "https://unused",
        "key",
        "model",
        task(),
        sn_policy_version="v2",
        sn_barcode_mode="enforce",
        barcode_scanner=fail_scanner,
    )

    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["barcode_attempted"] is True
    assert result["barcode_matched"] is False
    assert result["barcode_error"] == "RuntimeError"
    assert result["barcode_rescued"] is False
    assert result["_raw"]["sn_barcode_result"]["reject_reasons"] == ["scanner_error"]


def test_batch_wrapper_defaults_to_sn_v2_and_forwards_the_switch():
    source = (PROJECT_ROOT / "tools" / "run_guobu_audit_batch.ps1").read_text(encoding="utf-8-sig")
    assert '[ValidateSet("v1", "v2")][string]$SnPolicyVersion = "v2"' in source
    assert '"--sn-policy-version", $SnPolicyVersion' in source
    assert "sn_policy_version = $SnPolicyVersion" in source
    assert "snPolicyVersion = $SnPolicyVersion" in source
    assert '"tools/guobu_sn_policy_v2.py"' in source
