# -*- coding: utf-8 -*-
import json
from pathlib import Path

from tools import run_guobu_model_audit_v2 as mainline


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def test_cli_defaults_to_sn_v1_and_keeps_v2_as_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("SN_POLICY_VERSION", raising=False)
    args = mainline.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert args.sn_policy_version == "v1"

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


def test_batch_wrapper_defaults_to_sn_v2_and_forwards_the_switch():
    source = (PROJECT_ROOT / "tools" / "run_guobu_audit_batch.ps1").read_text(encoding="utf-8-sig")
    assert '[ValidateSet("v1", "v2")][string]$SnPolicyVersion = "v2"' in source
    assert '"--sn-policy-version", $SnPolicyVersion' in source
    assert "sn_policy_version = $SnPolicyVersion" in source
    assert "snPolicyVersion = $SnPolicyVersion" in source
    assert '"tools/guobu_sn_policy_v2.py"' in source
