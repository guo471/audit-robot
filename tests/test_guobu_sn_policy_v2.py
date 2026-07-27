# -*- coding: utf-8 -*-
import json
import re

import pytest

from tools.guobu_sn_policy_v2 import (
    SnCategory,
    build_model_payload,
    build_sn_prompt,
    classify_sn_category,
    decide_sn as decide_sn_policy,
)
from tools.run_guobu_model_audit_v2 import effective_product_category


def decide_sn(fields, model_evidence, **kwargs):
    kwargs.setdefault("effective_category", effective_product_category(fields))
    return decide_sn_policy(fields, model_evidence, **kwargs)


def candidate(
    source,
    raw_text="",
    *,
    label="S/N",
    field_type="SN",
    readable=True,
    complete=True,
    normalized_text="",
):
    return {
        "image_id": "img_003",
        "source": source,
        "field_type": field_type,
        "label_text": label,
        "raw_text": raw_text,
        "raw_context": f"{label}: {raw_text}",
        "normalized_text": normalized_text or raw_text,
        "label_binding": "EXPLICIT",
        "readable": readable,
        "complete": complete,
        "confidence": 0.99,
        "visual_ambiguity_notes": [],
    }


def identity(field_type, raw_text=None):
    if raw_text is None:
        raw_text = {
            "MEID": "A1234567890BCD",
            "EID": "12345678901234567890123456789012",
        }.get(field_type, "123456789012345")
    return {
        "image_id": "img_003",
        "source": "DEVICE_SCREEN",
        "field_type": field_type,
        "label_text": field_type,
        "label_binding": "EXPLICIT",
        "raw_text": raw_text,
        "readable": True,
        "complete": True,
    }


def evidence(*candidates, identities=None, screen_identity_state=None):
    if screen_identity_state is None:
        screen_candidates = [
            item for item in candidates if item.get("source") in {"DEVICE_SCREEN", "SCREEN"}
        ]
        readable_screen_values = {
            item.get("raw_text")
            for item in screen_candidates
            if item.get("readable") and item.get("complete") and item.get("raw_text")
        }
        if len(readable_screen_values) >= 2:
            screen_identity_state = "SCREEN_SN_CONFLICT"
        elif readable_screen_values:
            screen_identity_state = "SCREEN_SN_READABLE"
        elif screen_candidates:
            screen_identity_state = "SCREEN_SN_UNREADABLE"
        elif identities:
            screen_identity_state = "PHONE_IDENTITY_ONLY"
        else:
            screen_identity_state = "NO_SCREEN_SN"
    return {
        "schema_version": "guobu_sn_evidence_v2",
        "sn_readable": any(
            item.get("readable")
            and item.get("complete")
            and item.get("field_type") in {"SN", "SERIAL"}
            and item.get("label_text")
            in {"SN", "S/N", "SN码", "序列号", "产品序列号", "Serial No.", "Serial Number", "Serial#"}
            and item.get("label_binding") == "EXPLICIT"
            for item in candidates
        ),
        "screen_identity_state": screen_identity_state,
        "sn_candidates": list(candidates),
        "identity_evidence": list(identities or []),
        "confidence": 0.99,
    }


@pytest.mark.parametrize(
    ("effective_category", "fields", "expected"),
    [
        ("ordinary_3c", {"product_type": "[B01] 手机"}, SnCategory.PHONE),
        ("ordinary_3c", {"cate_code_name": "平板电脑"}, SnCategory.TABLET),
        ("ordinary_3c", {"product_type": "儿童智能手表"}, SnCategory.WATCH),
        ("ordinary_3c", {"product_type": "运动智能手环"}, SnCategory.WATCH),
        ("computer", {"goods_name": "轻薄笔记本电脑"}, SnCategory.COMPUTER),
        ("computer", {"product_type": "[A05] 电脑", "cate_code_name": "电脑"}, SnCategory.COMPUTER),
        ("home_appliance", {"cate_code_name": "冰箱"}, SnCategory.HOME_APPLIANCE),
        ("ordinary_3c", {"product_type": "AI智能眼镜"}, SnCategory.HOME_APPLIANCE),
        ("ordinary_3c", {"product_type": "数码相机"}, SnCategory.UNSUPPORTED),
        ("ordinary_3c", {"product_type": "无线耳机"}, SnCategory.UNSUPPORTED),
        ("ordinary_3c", {"product_type": "Bluetooth headphone"}, SnCategory.UNSUPPORTED),
        ("unknown", {"category_name": "digital", "product_type": "phone"}, SnCategory.UNSUPPORTED),
    ],
)
def test_name_based_sn_category_routing(effective_category, fields, expected):
    assert classify_sn_category(fields, effective_category=effective_category) is expected


@pytest.mark.parametrize(
    ("effective_category", "fields", "expected"),
    [
        ("home_appliance", {"product_type": "手机"}, SnCategory.HOME_APPLIANCE),
        ("computer", {"product_type": "手机"}, SnCategory.COMPUTER),
        ("ordinary_3c", {"product_type": "手机"}, SnCategory.PHONE),
        ("ordinary_3c", {"product_type": "平板电脑"}, SnCategory.TABLET),
        ("ordinary_3c", {"product_type": "智能手环"}, SnCategory.WATCH),
        ("ordinary_3c", {"product_type": "智能眼镜"}, SnCategory.HOME_APPLIANCE),
        ("ordinary_3c", {"product_type": "数码相机"}, SnCategory.UNSUPPORTED),
        ("ordinary_3c", {"product_type": "无线耳机"}, SnCategory.UNSUPPORTED),
        ("ordinary_3c", {"product_type": "Bluetooth earphones"}, SnCategory.UNSUPPORTED),
        ("unknown", {"product_type": "手机"}, SnCategory.UNSUPPORTED),
    ],
)
def test_sn_v2_consumes_mainline_effective_category(effective_category, fields, expected):
    assert classify_sn_category(fields, effective_category=effective_category) is expected


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"product_type": "手机", "goods_name": "赠送智能眼镜保护壳"}, SnCategory.PHONE),
        ({"product_type": "平板电脑", "goods_name": "赠送无线耳机"}, SnCategory.TABLET),
        ({"product_type": "手机", "model": "Camera Edition"}, SnCategory.PHONE),
    ],
)
def test_ordinary_3c_subtype_uses_primary_category_field_without_cross_field_pollution(fields, expected):
    assert classify_sn_category(fields, effective_category="ordinary_3c") is expected


def test_sn_v2_fails_closed_when_mainline_category_is_missing():
    assert classify_sn_category({"product_type": "手机"}) is SnCategory.UNSUPPORTED


def test_ordinary_3c_does_not_fallback_to_goods_name_or_model():
    assert (
        classify_sn_category(
            {"goods_name": "赠品手机", "model": "iPhone"},
            effective_category="ordinary_3c",
        )
        is SnCategory.UNSUPPORTED
    )


@pytest.mark.parametrize("category", list(SnCategory))
def test_prompt_contains_exactly_one_category_rule(category):
    prompt = build_sn_prompt(category)
    markers = [f"RULE_{item.value}" for item in SnCategory]
    assert prompt.count(f"RULE_{category.value}") == 1
    assert all(marker not in prompt for marker in markers if marker != f"RULE_{category.value}")
    assert "guobu_sn_evidence_v2" in prompt
    assert "SN_MISMATCH" not in prompt


@pytest.mark.parametrize("category", list(SnCategory))
def test_prompt_output_example_is_valid_json(category):
    prompt = build_sn_prompt(category)
    match = re.search(r'(\{\s*"schema_version".*?\n\})', prompt, re.DOTALL)
    assert match is not None
    example = json.loads(match.group(1))
    assert example["schema_version"] == "guobu_sn_evidence_v2"
    assert isinstance(example["sn_readable"], bool)
    assert example["screen_identity_state"] in {
        "NOT_APPLICABLE",
        "NO_SCREEN_SN",
    }


def test_prompt_defines_empty_visual_ambiguity_notes_and_state_semantics():
    prompt = build_sn_prompt(SnCategory.PHONE)
    assert "没有歧义时必须且只能输出[]" in prompt
    assert "SCREEN_SN_READABLE表示" in prompt
    assert "PHONE_IDENTITY_ONLY表示" in prompt
    assert "sn_readable=true仅当" in prompt


def test_phone_prompt_has_phone_identity_rules_without_tablet_or_computer_rules():
    prompt = build_sn_prompt(SnCategory.PHONE)
    assert all(value in prompt for value in ("IMEI", "MEID", "EID"))
    assert "RULE_TABLET" not in prompt
    assert "RULE_COMPUTER" not in prompt
    assert "IMEI、IMEI1、IMEI2必须是15位数字" in prompt


def test_home_prompt_contains_no_phone_identity_terms_or_states():
    prompt = build_sn_prompt(SnCategory.HOME_APPLIANCE)
    assert "IMEI" not in prompt
    assert "MEID" not in prompt
    assert "EID" not in prompt
    assert "PHONE_IDENTITY_ONLY" not in prompt


def test_model_payload_contains_no_system_sn_or_derived_length():
    task = {
        "channel_order_no": "order-1",
        "fields": {
            "product_type": "手机",
            "cate_code_name": "手机",
            "system_sn": "SECRET-SN-123",
        },
    }
    payload = build_model_payload(
        task,
        SnCategory.PHONE,
        [{"image_id": "img_003", "title": "SN码采集 / 激活照片", "source_url": "https://example/img.jpg"}],
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "SECRET-SN-123" not in serialized
    assert "system_sn" not in serialized.lower()
    assert "system_sn_len" not in serialized.lower()


def test_home_appliance_passes_when_any_explicit_candidate_matches():
    result = decide_sn(
        {"product_type": "冰箱", "system_sn": "ABC123"},
        evidence(
            candidate("PACKAGE_LABEL", "WRONG999"),
            candidate("DEVICE_BODY", "ABC123"),
            screen_identity_state="NOT_APPLICABLE",
        ),
    )
    assert result["manual_required"] is False
    assert result["sn_match"] is True
    assert result["observed_sn"] == "ABC123"


def test_screen_sn_match_cannot_be_overturned_by_package_candidate():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC123"),
            candidate("PACKAGE_LABEL", "WRONG999"),
        ),
    )
    assert result["manual_required"] is False
    assert result["observed_sn"] == "ABC123"
    assert result["selected_source"] == "DEVICE_SCREEN"


def test_two_distinct_readable_screen_sns_are_uncertain():
    result = decide_sn(
        {"product_type": "平板", "system_sn": "ABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC123"),
            candidate("DEVICE_SCREEN", "XYZ999"),
        ),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"


@pytest.mark.parametrize("field_type", ["IMEI", "IMEI1", "IMEI2", "MEID", "EID"])
def test_phone_identity_only_can_enable_package_fallback(field_type):
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(candidate("PACKAGE_LABEL", "ABC123"), identities=[identity(field_type)]),
    )
    assert result["manual_required"] is False
    assert result["selected_source"] == "PACKAGE_LABEL"


def test_tablet_identity_only_cannot_enable_package_fallback():
    result = decide_sn(
        {"product_type": "平板电脑", "system_sn": "ABC123"},
        evidence(
            candidate("PACKAGE_LABEL", "ABC123"),
            identities=[identity("IMEI")],
            screen_identity_state="NO_SCREEN_SN",
        ),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_NOT_FOUND"
    assert result["manual_reason"] == "未找到该品类要求的有效屏幕SN"


@pytest.mark.parametrize("product_type", ["平板", "智能手表", "智能手环"])
def test_unreadable_screen_sn_enables_package_fallback_for_tablet_and_watch(product_type):
    result = decide_sn(
        {"product_type": product_type, "system_sn": "ABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "", readable=False, complete=False),
            candidate("PACKAGE_LABEL", "ABC123"),
        ),
    )
    assert result["manual_required"] is False
    assert result["selected_source"] == "PACKAGE_LABEL"


def test_computer_never_uses_package_fallback():
    result = decide_sn(
        {"product_type": "笔记本电脑", "system_sn": "ABC123"},
        evidence(candidate("PACKAGE_LABEL", "ABC123")),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_NOT_FOUND"
    assert result["manual_reason"] == "未找到该品类要求的有效屏幕SN"


def test_unreadable_computer_screen_sn_is_uncertain_and_package_cannot_rescue():
    result = decide_sn(
        {"product_type": "笔记本电脑", "system_sn": "ABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "", readable=False, complete=False),
            candidate("PACKAGE_LABEL", "ABC123"),
        ),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"


def test_phone_unreadable_screen_uses_package_and_reports_package_mismatch():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "", readable=False, complete=False),
            candidate("PACKAGE_LABEL", "XYZ999"),
        ),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["observed_sn"] == "XYZ999"


def test_canonical_value_uses_raw_text_not_model_normalized_text():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "abc-123"},
        evidence(candidate("DEVICE_SCREEN", "ABC 123", normalized_text="ABC12K")),
    )
    assert result["manual_required"] is False
    assert result["observed_sn"] == "ABC 123"


def test_field_type_without_approved_explicit_label_is_not_trusted():
    result = decide_sn(
        {"product_type": "冰箱", "system_sn": "ABC123"},
        evidence(
            candidate("PACKAGE_LABEL", "ABC123", label="产品编号"),
            screen_identity_state="NOT_APPLICABLE",
        ),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_NOT_FOUND"


def test_approved_sn_label_cannot_rescue_non_sn_field_type():
    result = decide_sn(
        {"product_type": "冰箱", "system_sn": "ABC123"},
        evidence(
            candidate("PACKAGE_LABEL", "ABC123", field_type="MODEL"),
            screen_identity_state="NOT_APPLICABLE",
        ),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_NOT_FOUND"


def test_phone_identity_field_without_explicit_matching_screen_label_cannot_enable_package_fallback():
    invalid_identity = identity("IMEI")
    invalid_identity.update(
        {
            "source": "PACKAGE_LABEL",
            "label_text": "设备编号",
            "label_binding": "NONE",
        }
    )
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(
            candidate("PACKAGE_LABEL", "ABC123"),
            identities=[invalid_identity],
            screen_identity_state="NO_SCREEN_SN",
        ),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_NOT_FOUND"


def test_device_body_is_diagnostic_only_for_3c():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(candidate("DEVICE_BODY", "ABC123")),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_NOT_FOUND"


def test_unsupported_digital_product_routes_manual():
    result = decide_sn(
        {"product_type": "数码相机", "system_sn": "ABC123"},
        evidence(candidate("PACKAGE_LABEL", "ABC123")),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert result["manual_reason"] == "该商品品类暂未配置SN自动审核规则"


def test_candidate_from_unknown_input_image_routes_uncertain():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(candidate("DEVICE_SCREEN", "ABC123")),
        allowed_image_ids={"another-image"},
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert result["manual_reason"] == "模型SN证据引用了非本单激活照片"


def test_identity_from_unknown_input_image_cannot_enable_phone_package_fallback():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(candidate("PACKAGE_LABEL", "ABC123"), identities=[identity("IMEI")]),
        allowed_image_ids={"another-image"},
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"


def test_contradictory_readability_and_screen_state_cannot_auto_pass():
    model_evidence = evidence(
        candidate("DEVICE_SCREEN", "ABC123"),
        screen_identity_state="SCREEN_SN_CONFLICT",
    )
    model_evidence["sn_readable"] = False
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        model_evidence,
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"


def test_non_string_candidate_raw_text_cannot_auto_pass():
    malformed = candidate("DEVICE_SCREEN", "ABC123")
    malformed["raw_text"] = ["ABC123"]
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(malformed, screen_identity_state="SCREEN_SN_READABLE"),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"


def test_one_digit_phone_identity_cannot_enable_package_fallback():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(
            candidate("PACKAGE_LABEL", "ABC123"),
            identities=[identity("IMEI", "1")],
            screen_identity_state="NO_SCREEN_SN",
        ),
    )
    assert result["manual_required"] is True
