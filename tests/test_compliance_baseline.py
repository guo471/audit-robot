# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import compliance_baseline as baseline
from tools import run_guobu_model_audit_v2 as v2


EXPECTED_FIELDS = (
    "渠道订单号",
    "订单品类/商品类型",
    "商品照片",
    "拆封/安装照片",
    "激活/SN照片",
    "原始流程状态",
)


def _image(image_id: str, title: str) -> dict:
    return {
        "image_id": image_id,
        "title": title,
        "local_path": f"C:/images/{image_id}.jpg",
        "source_url": f"https://example.invalid/{image_id}.jpg",
    }


def _task(order_id: str, status: str = "已通过", *, activation: bool = True) -> dict:
    groups = {
        "商品照片": [_image("img_001", "商品照片")],
        "拆封照片": [_image("img_002", "拆封照片")],
    }
    if activation:
        groups["SN码采集/激活照片"] = [_image("img_003", "SN码采集/激活照片")]
    return {
        "channel_order_no": order_id,
        "fields": {
            "product_type": "[B01] 手机",
            "source_flow_status": status,
        },
        "image_groups": groups,
    }


def test_build_record_has_exact_six_fields_and_preserves_status():
    record = baseline.build_dataset_record(_task("A-1", "审核中"))

    assert tuple(record) == EXPECTED_FIELDS
    assert record["渠道订单号"] == "A-1"
    assert record["订单品类/商品类型"] == "[B01] 手机"
    assert record["原始流程状态"] == "审核中"
    assert record["激活/SN照片"][0]["image_id"] == "img_003"


def test_write_dataset_rejects_duplicate_order_ids(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("a.json", "b.json"):
        (source / name).write_text(
            json.dumps(_task("DUPLICATE"), ensure_ascii=False), encoding="utf-8"
        )

    with pytest.raises(ValueError, match="duplicate channel order"):
        baseline.write_dataset(source, tmp_path / "orders.jsonl")


def test_validate_disjoint_reports_overlap():
    with pytest.raises(ValueError, match="overlap"):
        baseline.validate_disjoint(
            [{"渠道订单号": "A"}], [{"渠道订单号": "A"}]
        )


def test_missing_activation_is_fail_closed_without_model_call(monkeypatch):
    called = []

    def unexpected_call(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("missing activation must not call the model")

    monkeypatch.setattr(v2, "call_model_with_retry", unexpected_call)

    result = baseline.audit_record(
        _task("MISSING", activation=False),
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model="qwen3.7-plus",
        cache_dir=None,
    )

    assert called == []
    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["ACTIVATION_PHOTO_INVALID"]


def test_baseline_uses_only_compliance_prompt_and_single_stage(monkeypatch):
    calls = []

    def fake_call(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return (
            {
                "effective_category": "ordinary_3c",
                "product_type_match": True,
                "product_photo_ok": True,
                "unboxing_photo_ok": True,
                "activation_photo_ok": True,
                "activation_evidence_type": "SCREEN_ACTIVE_WITH_SN",
                "activation_identity_by_image": [
                    {
                        "image_id": "img_003",
                        "identity_fields": [
                            {
                                "field_type": "IMEI1",
                                "raw_value": "867530900000001",
                                "readable": True,
                                "complete": True,
                            }
                        ],
                    }
                ],
                "confidence": 0.99,
                "manual_required": False,
                "manual_reason_codes": [],
            },
            "{}",
            0.01,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = baseline.audit_record(
        _task("PASS"),
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model="qwen3.7-plus",
        cache_dir=None,
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["kwargs"]["stage"] == "compliance_baseline_v1"
    assert "allow_non_object" not in call["kwargs"]
    prompt = call["args"][3]
    assert v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM not in prompt
    assert "普通3C激活证据统一口径插件" in prompt
    assert result["manual_required"] is False


def test_result_coverage_rejects_missing_and_duplicate_ids():
    records = [{"渠道订单号": "A"}, {"渠道订单号": "B"}]

    with pytest.raises(ValueError, match="missing result"):
        baseline.validate_result_coverage(records, [{"渠道订单号": "A"}])
    with pytest.raises(ValueError, match="duplicate result"):
        baseline.validate_result_coverage(
            records,
            [
                {"渠道订单号": "A"},
                {"渠道订单号": "A"},
            ],
        )


def test_freeze_manifest_hashes_the_actual_supported_ordinary_3c_prompt():
    manifest = baseline.build_freeze_manifest()
    entry = manifest["prompts"]["ordinary_3c"]
    actual_prompt = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="[B01] 手机",
        include_photo_authenticity=False,
        digital_activation_evidence_mode="on",
    )

    assert entry["digital_activation_plugin_included"] is True
    assert entry["character_count"] == len(actual_prompt)
    assert entry["sha256"] == baseline._sha256_bytes(actual_prompt.encode("utf-8"))


def test_pending_records_retries_only_service_failures_when_requested():
    records = [{"渠道订单号": "A"}, {"渠道订单号": "B"}, {"渠道订单号": "C"}]
    existing = {
        "A": {"渠道订单号": "A", "service_failure": False},
        "B": {"渠道订单号": "B", "service_failure": True},
    }

    assert [item["渠道订单号"] for item in baseline.pending_records(
        records, existing, retry_service_failures=False
    )] == ["C"]
    assert [item["渠道订单号"] for item in baseline.pending_records(
        records, existing, retry_service_failures=True
    )] == ["B", "C"]


def test_recover_missing_local_images_uses_isolated_copy(tmp_path):
    record = baseline.build_dataset_record(_task("RECOVER"))
    for index, field in enumerate(("商品照片", "激活/SN照片"), 1):
        existing_path = tmp_path / f"existing_{index}.jpg"
        existing_path.write_bytes(b"existing")
        record[field][0]["local_path"] = str(existing_path)
    record["拆封/安装照片"][0]["local_path"] = str(tmp_path / "missing.jpg")
    original_url = record["拆封/安装照片"][0]["source_url"]

    recovered, events = baseline.recover_missing_local_images(
        record,
        tmp_path / "recovery",
        fetch_bytes=lambda url: b"jpeg-bytes" if url == original_url else b"",
    )

    recovered_image = recovered["拆封/安装照片"][0]
    assert recovered_image["source_url"] == ""
    assert Path(recovered_image["local_path"]).read_bytes() == b"jpeg-bytes"
    assert record["拆封/安装照片"][0]["source_url"] == original_url
    assert events[0]["image_id"] == "img_002"
    assert events[0]["content_sha256"] == baseline._sha256_bytes(b"jpeg-bytes")


def test_input_recovery_failure_is_fail_closed_per_order(monkeypatch, tmp_path):
    def fail_recovery(*args, **kwargs):
        raise ValueError("blob URL cannot be recovered")

    monkeypatch.setattr(baseline, "recover_missing_local_images", fail_recovery)

    result = baseline.audit_record(
        _task("RECOVERY-FAIL"),
        base_url="https://example.invalid/v1",
        api_key="test-key",
        cache_dir=None,
        input_recovery_dir=tmp_path,
    )

    assert result["decision"] == "manual_review"
    assert result["service_failure"] is True
    assert result["model_calls"] == 0
    assert result["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert "输入恢复失败" in result["manual_reason"]


def test_model_failure_counts_one_logical_model_call(monkeypatch):
    def fail_model(*args, **kwargs):
        raise RuntimeError("service unavailable")

    monkeypatch.setattr(v2, "call_model_with_retry", fail_model)

    result = baseline.audit_record(
        _task("MODEL-FAIL"),
        base_url="https://example.invalid/v1",
        api_key="test-key",
        cache_dir=None,
    )

    assert result["service_failure"] is True
    assert result["model_calls"] == 1


def test_ruleset_defaults_to_legacy():
    args = baseline.parse_args(
        [
            "run",
            "--dataset",
            "orders.jsonl",
            "--output-dir",
            "out",
            "--cache-dir",
            "cache",
        ]
    )

    assert args.ruleset == "legacy"


_CANDIDATE_REQUIRED_FIELDS = {
    "home_appliance": (
        "manual_reason_codes",
        "product_type_match",
        "product_photo_ok",
        "unboxing_photo_ok",
        "unboxing_image_evidence",
        "duplicate_image_evidence",
        "evidence_summary",
        "confidence",
    ),
    "ordinary_3c": (
        "manual_reason_codes",
        "product_type_match",
        "product_photo_ok",
        "unboxing_photo_ok",
        "activation_photo_ok",
        "activation_evidence_type",
        "duplicate_image_evidence",
        "evidence_summary",
    ),
    "computer": (
        "manual_reason_codes",
        "product_type_match",
        "product_photo_ok",
        "unboxing_photo_ok",
        "activation_photo_ok",
        "activation_evidence_type",
        "duplicate_image_evidence",
        "evidence_summary",
    ),
}

_CANDIDATE_PRODUCT_TYPES = {
    "home_appliance": "[A01] 冰箱",
    "ordinary_3c": "[B01] 手机",
    "computer": "[C01] 笔记本电脑",
}


def _valid_candidate_decision(category: str) -> dict:
    common = {
        "manual_reason_codes": [],
        "product_type_match": "match",
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "duplicate_image_evidence": False,
        "evidence_summary": "visible evidence",
    }
    if category == "home_appliance":
        return {
            **common,
            "unboxing_image_evidence": [
                {
                    "image_id": "img_002",
                    "product_visible": True,
                    "package_visible": True,
                    "home_or_installation_scene_visible": False,
                }
            ],
            "confidence": 0.99,
        }
    if category == "ordinary_3c":
        return {
            **common,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
        }
    return {
        **common,
        "activation_photo_ok": True,
        "activation_evidence_type": "LAPTOP_SCREEN_SN_WITH_PACKAGE",
    }


def _candidate_task(category: str, order_id: str = "CANDIDATE") -> dict:
    task = _task(order_id)
    task["fields"]["product_type"] = _CANDIDATE_PRODUCT_TYPES[category]
    return task


def _audit_candidate(monkeypatch, category: str, decision, raw_text: str = "raw") -> dict:
    def fake_call(*args, **kwargs):
        return decision, raw_text, 0.01, {"total_tokens": 10}, False

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    return baseline.audit_record(
        _candidate_task(category),
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model="qwen3.7-plus",
        cache_dir=None,
        ruleset="candidate",
    )


def _assert_candidate_structure_anomaly(result: dict) -> None:
    assert result["decision"] == "manual_review"
    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert result["structure_anomaly"] is True
    assert result["service_failure"] is False


def test_candidate_home_without_activation_still_calls_model(monkeypatch):
    calls = []

    def fake_call(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return (
            {
                "manual_reason_codes": [],
                "product_type_match": "match",
                "product_photo_ok": True,
                "unboxing_photo_ok": True,
                "unboxing_image_evidence": [
                    {
                        "image_id": "img_002",
                        "product_visible": True,
                        "package_visible": False,
                        "home_or_installation_scene_visible": True,
                    }
                ],
                "duplicate_image_evidence": False,
                "evidence_summary": "可见已安装家电本体",
                "confidence": 0.99,
            },
            "{}",
            0.01,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    task = _task("HOME-NO-ACTIVATION", activation=False)
    task["fields"]["product_type"] = "[A01] 冰箱"

    result = baseline.audit_record(
        task,
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model="qwen3.7-plus",
        cache_dir=None,
        ruleset="candidate",
    )

    assert len(calls) == 1
    assert calls[0]["kwargs"]["stage"] == "compliance_candidate_v6"
    assert calls[0]["kwargs"]["allow_non_object"] is True
    assert result["baseline_version"] == "compliance-candidate-v6-20260804"
    assert result["decision"] == "pass"


def test_candidate_request_labels_each_image_immediately_before_image(monkeypatch):
    captured = {}

    def fake_post(base_url, api_key, body, *, read_timeout_sec):
        captured["body"] = body
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            _valid_candidate_decision("home_appliance"),
                            ensure_ascii=False,
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 10},
        }

    monkeypatch.setattr(v2, "_post_chat_completion_json", fake_post)
    task = _candidate_task("home_appliance", "ROLE-LABELS")

    result = baseline.audit_record(
        task,
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model="qwen3.7-plus",
        cache_dir=None,
        ruleset="candidate",
    )

    content = captured["body"]["messages"][1]["content"]
    assert [(part["type"], part.get("text")) for part in content] == [
        ("text", content[0]["text"]),
        ("text", "【商品照片｜img_001】"),
        ("image_url", None),
        ("text", "【拆封/安装照片｜img_002】"),
        ("image_url", None),
        ("text", "【激活/SN照片｜img_003】"),
        ("image_url", None),
    ]
    assert result["decision"] == "pass"


def test_candidate_report_uses_mechanically_corrected_home_unboxing(monkeypatch):
    decision = _valid_candidate_decision("home_appliance")
    decision.update(
        {
            "unboxing_photo_ok": False,
            "manual_reason_codes": ["UNBOXING_PHOTO_INVALID"],
        }
    )

    result = _audit_candidate(monkeypatch, "home_appliance", decision)

    assert result["decision"] == "pass"
    assert result["manual_reason_codes"] == []
    assert result["unboxing_photo_ok"] is True
    assert result["package_visible"] is True
    assert result["whole_product_visible"] is True
    assert result["product_and_package_same_image"] is True
    assert result["home_or_installation_scene_visible"] is False
    assert result["local_corrections"] == [
        "REMOVE_UNBOXING_PHOTO_INVALID_PER_IMAGE_PACKAGED_EVIDENCE"
    ]
    assert result["raw_model_result"]["unboxing_photo_ok"] is False
    assert result["raw_model_result"]["manual_reason_codes"] == [
        "UNBOXING_PHOTO_INVALID"
    ]


def test_candidate_home_missing_per_image_evidence_is_fail_closed(monkeypatch):
    decision = _valid_candidate_decision("home_appliance")
    decision.pop("unboxing_image_evidence")

    result = _audit_candidate(monkeypatch, "home_appliance", decision)

    _assert_candidate_structure_anomaly(result)
    assert result["missing_model_fields"] == ["unboxing_image_evidence"]


def test_candidate_multiple_unboxing_images_still_require_explicit_same_image(
    monkeypatch,
):
    decision = _valid_candidate_decision("home_appliance")
    decision.update(
        {
            "unboxing_image_evidence": [
                {
                    "image_id": "img_002",
                    "product_visible": True,
                    "package_visible": False,
                    "home_or_installation_scene_visible": False,
                },
                {
                    "image_id": "img_004",
                    "product_visible": False,
                    "package_visible": True,
                    "home_or_installation_scene_visible": False,
                },
            ],
            "unboxing_photo_ok": False,
            "manual_reason_codes": ["UNBOXING_PHOTO_INVALID"],
        }
    )

    def fake_call(*args, **kwargs):
        return decision, "raw", 0.01, {"total_tokens": 10}, False

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    task = _candidate_task("home_appliance", "MULTI-UNBOXING")
    task["image_groups"]["拆封照片"].append(_image("img_004", "拆封照片"))

    result = baseline.audit_record(
        task,
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model="qwen3.7-plus",
        cache_dir=None,
        ruleset="candidate",
    )

    assert result["decision"] == "manual_review"
    assert result["manual_reason_codes"] == ["UNBOXING_PHOTO_INVALID"]
    assert result["local_corrections"] == []


@pytest.mark.parametrize(
    ("category", "missing_field"),
    [
        (category, field)
        for category, fields in _CANDIDATE_REQUIRED_FIELDS.items()
        for field in fields
    ],
)
def test_candidate_missing_any_required_field_is_fail_closed(
    monkeypatch, category, missing_field
):
    decision = _valid_candidate_decision(category)
    decision.pop(missing_field)

    result = _audit_candidate(monkeypatch, category, decision, "missing field raw")

    _assert_candidate_structure_anomaly(result)
    assert result["missing_model_fields"] == [missing_field]
    assert result["raw_model_result"] == decision
    assert result["raw_model_text"] == "missing field raw"


@pytest.mark.parametrize(
    ("category", "field", "invalid_value"),
    [
        ("home_appliance", "manual_reason_codes", "MODEL_UNCERTAIN"),
        ("home_appliance", "product_type_match", True),
        ("home_appliance", "product_photo_ok", 1),
        ("home_appliance", "unboxing_photo_ok", "true"),
        ("home_appliance", "unboxing_image_evidence", {}),
        ("home_appliance", "duplicate_image_evidence", 0),
        ("home_appliance", "evidence_summary", None),
        ("home_appliance", "confidence", True),
        ("ordinary_3c", "activation_photo_ok", 1),
        ("ordinary_3c", "activation_evidence_type", 1),
        ("computer", "activation_photo_ok", "true"),
        ("computer", "activation_evidence_type", None),
    ],
)
def test_candidate_wrong_field_type_is_fail_closed(
    monkeypatch, category, field, invalid_value
):
    decision = _valid_candidate_decision(category)
    decision[field] = invalid_value

    result = _audit_candidate(monkeypatch, category, decision)

    _assert_candidate_structure_anomaly(result)
    assert field in result["invalid_model_fields"]


@pytest.mark.parametrize(
    ("category", "field", "invalid_value"),
    [
        ("home_appliance", "product_type_match", "MATCH"),
        ("ordinary_3c", "product_type_match", "uncertain"),
        ("ordinary_3c", "activation_evidence_type", "SCREEN_WITH_SN"),
        ("computer", "activation_evidence_type", "PHONE_IDENTITY_ONLY"),
    ],
)
def test_candidate_invalid_enum_is_fail_closed(
    monkeypatch, category, field, invalid_value
):
    decision = _valid_candidate_decision(category)
    decision[field] = invalid_value

    result = _audit_candidate(monkeypatch, category, decision)

    _assert_candidate_structure_anomaly(result)
    assert field in result["invalid_model_fields"]


@pytest.mark.parametrize("category", tuple(_CANDIDATE_REQUIRED_FIELDS))
def test_candidate_unknown_reason_code_is_fail_closed(monkeypatch, category):
    decision = _valid_candidate_decision(category)
    decision["manual_reason_codes"] = ["NOT_A_REAL_REASON"]

    result = _audit_candidate(monkeypatch, category, decision)

    _assert_candidate_structure_anomaly(result)
    assert "manual_reason_codes" in result["invalid_model_fields"]


@pytest.mark.parametrize(
    ("category", "field"),
    [
        ("home_appliance", "product_photo_ok"),
        ("ordinary_3c", "product_photo_ok"),
        ("ordinary_3c", "unboxing_photo_ok"),
        ("ordinary_3c", "activation_photo_ok"),
        ("computer", "product_photo_ok"),
        ("computer", "unboxing_photo_ok"),
        ("computer", "activation_photo_ok"),
    ],
)
def test_candidate_false_business_field_without_reason_is_fail_closed(
    monkeypatch, category, field
):
    decision = _valid_candidate_decision(category)
    decision[field] = False

    result = _audit_candidate(monkeypatch, category, decision)

    _assert_candidate_structure_anomaly(result)
    assert field in result["invalid_model_fields"]


@pytest.mark.parametrize("match_value", ["mismatch", "unknown"])
def test_candidate_non_match_without_reason_is_fail_closed(monkeypatch, match_value):
    decision = _valid_candidate_decision("ordinary_3c")
    decision["product_type_match"] = match_value

    result = _audit_candidate(monkeypatch, "ordinary_3c", decision)

    _assert_candidate_structure_anomaly(result)
    assert "product_type_match" in result["invalid_model_fields"]


def test_candidate_duplicate_without_reason_is_fail_closed(monkeypatch):
    decision = _valid_candidate_decision("computer")
    decision["duplicate_image_evidence"] = True

    result = _audit_candidate(monkeypatch, "computer", decision)

    _assert_candidate_structure_anomaly(result)
    assert "duplicate_image_evidence" in result["invalid_model_fields"]


def test_candidate_home_invalid_mechanical_evidence_gets_specific_reason(monkeypatch):
    decision = _valid_candidate_decision("home_appliance")
    decision["unboxing_image_evidence"][0]["product_visible"] = False

    result = _audit_candidate(monkeypatch, "home_appliance", decision)

    assert result["decision"] == "manual_review"
    assert result["manual_reason_codes"] == ["UNBOXING_PHOTO_INVALID"]
    assert result["unboxing_photo_ok"] is False
    assert result["structure_anomaly"] is False
    assert result["local_corrections"] == [
        "ADD_UNBOXING_PHOTO_INVALID_PER_IMAGE_EVIDENCE"
    ]


def test_candidate_non_finite_or_unrepresentable_confidence_is_fail_closed(monkeypatch):
    decision = _valid_candidate_decision("home_appliance")
    decision["confidence"] = 10**1000

    result = _audit_candidate(monkeypatch, "home_appliance", decision)

    _assert_candidate_structure_anomaly(result)
    assert "confidence" in result["invalid_model_fields"]


@pytest.mark.parametrize("category", tuple(_CANDIDATE_REQUIRED_FIELDS))
def test_candidate_complete_answer_can_pass(monkeypatch, category):
    result = _audit_candidate(
        monkeypatch, category, _valid_candidate_decision(category)
    )

    assert result["decision"] == "pass"
    assert result["manual_reason_codes"] == []
    assert result["structure_anomaly"] is False


def test_candidate_non_object_answer_is_fail_closed_and_preserved(monkeypatch):
    result = _audit_candidate(monkeypatch, "ordinary_3c", [], "[]")

    _assert_candidate_structure_anomaly(result)
    assert result["raw_model_result"] == []
    assert result["raw_model_text"] == "[]"


@pytest.mark.parametrize("content", ["not json", "[]"])
def test_candidate_actual_parser_anomaly_is_fail_closed_and_preserved(
    monkeypatch, content
):
    monkeypatch.setattr(
        v2,
        "_post_chat_completion_json",
        lambda *args, **kwargs: {
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 10},
        },
    )

    result = baseline.audit_record(
        _candidate_task("ordinary_3c", "PARSER-ANOMALY"),
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model="qwen3.7-plus",
        cache_dir=None,
        ruleset="candidate",
    )

    _assert_candidate_structure_anomaly(result)
    assert result["raw_model_text"] == content
    assert result["model_calls"] == 1


def test_candidate_reason_codes_are_passed_through_without_conflict_rules(monkeypatch):
    def fake_call(*args, **kwargs):
        return (
            {
                "manual_reason_codes": ["ACTIVATION_PHOTO_INVALID"],
                "product_type_match": "match",
                "product_photo_ok": True,
                "unboxing_photo_ok": True,
                "activation_photo_ok": True,
                "activation_evidence_type": "SCREEN_SN",
                "duplicate_image_evidence": False,
                "evidence_summary": "模型按定稿提示词返回原因码",
            },
            "{}",
            0.01,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = baseline.audit_record(
        _task("CANDIDATE-PASSTHROUGH"),
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model="qwen3.7-plus",
        cache_dir=None,
        ruleset="candidate",
    )

    assert result["decision"] == "manual_review"
    assert result["manual_reason_codes"] == ["ACTIVATION_PHOTO_INVALID"]
    assert result["manual_reason"] == "模型按定稿提示词返回原因码"
    assert result["structure_anomaly"] is False


@pytest.mark.parametrize(
    "product_type",
    [
        "其他商品",
        "[B99] 手机壳",
        "headphone",
        "[B02] 手机",
        "[C99] 笔记本电脑包",
        "notebook stand",
        "烘干机",
    ],
)
def test_candidate_unknown_category_or_subtype_is_manual_without_model_call(
    monkeypatch, product_type
):
    calls = []

    def unexpected_call(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("candidate preflight must not call the model")

    monkeypatch.setattr(v2, "call_model_with_retry", unexpected_call)
    task = _task("UNKNOWN-CANDIDATE")
    task["fields"]["product_type"] = product_type

    result = baseline.audit_record(
        task,
        base_url="https://example.invalid/v1",
        api_key="test-key",
        cache_dir=None,
        ruleset="candidate",
    )

    assert calls == []
    assert result["decision"] == "manual_review"
    assert result["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert result["model_calls"] == 0


@pytest.mark.parametrize("product_type", ["[A05] 电脑", "电脑"])
@pytest.mark.parametrize(
    "activation_evidence_type",
    [
        "LAPTOP_SCREEN_SN_WITH_PACKAGE",
        "DESKTOP_BODY_SN_WITH_PACKAGE",
    ],
)
def test_candidate_generic_computer_calls_model_and_accepts_strong_evidence(
    monkeypatch, product_type, activation_evidence_type
):
    calls = []
    decision = _valid_candidate_decision("computer")
    decision["activation_evidence_type"] = activation_evidence_type

    def fake_call(*args, **kwargs):
        calls.append((args, kwargs))
        return decision, "raw", 0.01, {"total_tokens": 10}, False

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    task = _candidate_task("computer", f"GENERIC-{product_type}-{activation_evidence_type}")
    task["fields"]["product_type"] = product_type

    result = baseline.audit_record(
        task,
        base_url="https://example.invalid/v1",
        api_key="test-key",
        cache_dir=None,
        ruleset="candidate",
    )

    assert len(calls) == 1
    assert calls[0][1]["stage"] == "compliance_candidate_v6"
    assert result["decision"] == "pass"
    assert result["manual_reason_codes"] == []
    assert result["structure_anomaly"] is False


@pytest.mark.parametrize(
    ("category", "missing_group"),
    [
        ("home_appliance", "商品照片"),
        ("home_appliance", "拆封照片"),
        ("ordinary_3c", "商品照片"),
        ("ordinary_3c", "拆封照片"),
        ("ordinary_3c", "SN码采集/激活照片"),
        ("computer", "商品照片"),
        ("computer", "拆封照片"),
        ("computer", "SN码采集/激活照片"),
    ],
)
def test_candidate_missing_required_image_group_is_manual_without_model_call(
    monkeypatch, category, missing_group
):
    calls = []

    def unexpected_call(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("missing image group must not call the model")

    monkeypatch.setattr(v2, "call_model_with_retry", unexpected_call)
    task = _candidate_task(category, f"MISSING-{category}-{missing_group}")
    task["image_groups"][missing_group] = []

    result = baseline.audit_record(
        task,
        base_url="https://example.invalid/v1",
        api_key="test-key",
        cache_dir=None,
        ruleset="candidate",
    )

    assert calls == []
    assert result["decision"] == "manual_review"
    assert result["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert result["model_calls"] == 0


@pytest.mark.parametrize(
    ("category", "unusable_group"),
    [
        ("home_appliance", "商品照片"),
        ("home_appliance", "拆封照片"),
        ("ordinary_3c", "商品照片"),
        ("ordinary_3c", "拆封照片"),
        ("ordinary_3c", "SN码采集/激活照片"),
        ("computer", "商品照片"),
        ("computer", "拆封照片"),
        ("computer", "SN码采集/激活照片"),
    ],
)
def test_candidate_unusable_required_image_group_is_manual_without_model_call(
    monkeypatch, category, unusable_group
):
    calls = []

    def unexpected_call(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("unusable image group must not call the model")

    monkeypatch.setattr(v2, "call_model_with_retry", unexpected_call)
    task = _candidate_task(category, f"UNUSABLE-{category}-{unusable_group}")
    task["image_groups"][unusable_group] = [
        {
            "image_id": "blank",
            "title": unusable_group,
            "local_path": "",
            "source_url": "",
        }
    ]

    result = baseline.audit_record(
        task,
        base_url="https://example.invalid/v1",
        api_key="test-key",
        cache_dir=None,
        ruleset="candidate",
    )

    assert calls == []
    assert result["decision"] == "manual_review"
    assert result["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert result["model_calls"] == 0


def test_candidate_batch_contains_one_order_exception_and_processes_next(
    monkeypatch, tmp_path
):
    records = [
        baseline.build_dataset_record(_task("BROKEN")),
        baseline.build_dataset_record(_task("NEXT")),
    ]
    dataset_path = tmp_path / "orders.jsonl"
    dataset_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    def fake_audit(record, **kwargs):
        if record["渠道订单号"] == "BROKEN":
            raise RuntimeError("isolated candidate failure")
        return baseline._local_manual_result(
            record,
            code="MODEL_UNCERTAIN",
            reason="next order completed",
            category="ordinary_3c",
            baseline_version="compliance-candidate-v6-20260804",
        )

    monkeypatch.setattr(baseline, "audit_record", fake_audit)

    summary = baseline.run_baseline(
        dataset_path=dataset_path,
        output_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache",
        base_url="https://example.invalid/v1",
        api_key="test-key",
        workers=1,
        ruleset="candidate",
    )

    results = [
        json.loads(line)
        for line in (tmp_path / "out" / "results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert summary["order_count"] == 2
    assert [result["渠道订单号"] for result in results] == ["BROKEN", "NEXT"]
    assert all(result["decision"] == "manual_review" for result in results)
    assert results[0]["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert "isolated candidate failure" in results[0]["manual_reason"]
    assert results[0]["model_calls"] == 1
    assert results[0]["structure_anomaly"] is True
    assert results[0]["invalid_model_fields"] == ["$runtime"]
    assert results[0]["raw_model_text"] == ""


def test_candidate_post_model_exception_is_auditable_and_preserves_raw(monkeypatch):
    decision = _valid_candidate_decision("ordinary_3c")

    def fake_call(*args, **kwargs):
        return decision, "raw answer", 0.01, {"total_tokens": "not-an-int"}, False

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = baseline.audit_record(
        _candidate_task("ordinary_3c", "POST-MODEL-FAILURE"),
        base_url="https://example.invalid/v1",
        api_key="test-key",
        cache_dir=None,
        ruleset="candidate",
    )

    assert result["decision"] == "manual_review"
    assert result["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert result["service_failure"] is True
    assert result["structure_anomaly"] is True
    assert result["invalid_model_fields"] == ["$runtime"]
    assert result["model_calls"] == 1
    assert result["raw_model_result"] == decision
    assert result["raw_model_text"] == "raw answer"


def _run_candidate_without_model(monkeypatch, dataset_path, output_dir, cache_dir):
    calls = []

    def fake_audit(record, **kwargs):
        calls.append(record["渠道订单号"])
        return baseline._local_manual_result(
            record,
            code="MODEL_UNCERTAIN",
            reason="isolated test result",
            category="ordinary_3c",
            baseline_version="compliance-candidate-v6-20260804",
        )

    monkeypatch.setattr(baseline, "audit_record", fake_audit)
    summary = baseline.run_baseline(
        dataset_path=dataset_path,
        output_dir=output_dir,
        cache_dir=cache_dir,
        base_url="https://example.invalid/v1",
        api_key="test-key",
        workers=1,
        ruleset="candidate",
    )
    return summary, calls


def test_candidate_resume_rejects_changed_dataset_before_reusing_result(
    monkeypatch, tmp_path
):
    dataset_path = tmp_path / "orders.jsonl"
    output_dir = tmp_path / "out"
    record = baseline.build_dataset_record(_task("SAME-ID"))
    dataset_path.write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _summary, first_calls = _run_candidate_without_model(
        monkeypatch, dataset_path, output_dir, tmp_path / "cache"
    )
    assert first_calls == ["SAME-ID"]

    record["订单品类/商品类型"] = "[B02] 平板"
    dataset_path.write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="candidate resume contract mismatch"):
        _run_candidate_without_model(
            monkeypatch, dataset_path, output_dir, tmp_path / "cache"
        )


@pytest.mark.parametrize(
    "manifest_field",
    ["runtime_sha256", "candidate_runtime_sha256", "candidate_prompt_sha256"],
)
def test_candidate_resume_rejects_stale_runtime_or_prompt_contract(
    monkeypatch, tmp_path, manifest_field
):
    dataset_path = tmp_path / "orders.jsonl"
    output_dir = tmp_path / "out"
    record = baseline.build_dataset_record(_task("STALE-CONTRACT"))
    dataset_path.write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _run_candidate_without_model(
        monkeypatch, dataset_path, output_dir, tmp_path / "cache"
    )
    manifest_path = output_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[manifest_field] = "stale"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="candidate resume contract mismatch"):
        _run_candidate_without_model(
            monkeypatch, dataset_path, output_dir, tmp_path / "cache"
        )


def test_candidate_report_labels_model_calls_as_logical_calls():
    summary = baseline._baseline_summary(
        [
            baseline._local_manual_result(
                baseline.build_dataset_record(_task("REPORT")),
                code="MODEL_UNCERTAIN",
                reason="test",
                category="ordinary_3c",
                baseline_version="compliance-candidate-v6-20260804",
                model_calls=1,
            )
        ]
    )

    report = baseline._baseline_report(summary)

    assert "逻辑模型调用（重试请求不单列）：1" in report
