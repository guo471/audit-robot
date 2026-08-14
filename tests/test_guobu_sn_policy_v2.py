# -*- coding: utf-8 -*-
import json

import pytest

from tools.guobu_sn_policy_v2 import (
    PROMPT_CHAR_LIMIT,
    SCHEMA_VERSION,
    SnCategory,
    build_model_payload,
    build_sn_prompt,
    canonical_sn,
    canonical_system_sn,
    classify_sn_category,
    decide_sn as decide_sn_policy,
)
from tools.run_guobu_model_audit_v2 import effective_product_category


def decide_sn(fields, model_evidence, **kwargs):
    kwargs.setdefault("effective_category", effective_product_category(fields))
    return decide_sn_policy(fields, model_evidence, **kwargs)


def candidate(
    source,
    raw_text,
    *,
    field_type="SN",
    image_id="img_003",
    readable=True,
    confidence=0.99,
    complete=True,
    ambiguity=None,
):
    item = {
        "image_id": image_id,
        "source": source,
        "field_type": field_type,
        "raw_text": raw_text,
        "normalized_text": canonical_sn(raw_text),
        "readable": readable,
        "confidence": confidence,
    }
    if complete is not None:
        item["complete"] = complete
    if ambiguity is not None:
        item["visual_ambiguity_notes"] = ambiguity
    return item


def identity(field_type="IMEI1", raw_text="123456789012345"):
    return {
        "image_id": "img_003",
        "source": "DEVICE_SCREEN",
        "field_type": field_type,
        "raw_text": raw_text,
        "readable": True,
        "complete": True,
    }


def evidence(*candidates, state="NO_SCREEN_IDENTITY", identities=None, manual_reason_code=""):
    return {
        "schema_version": SCHEMA_VERSION,
        "screen_identity_state": state,
        "observed_sn": "",
        "normalized_observed_sn": "",
        "sn_candidates": list(candidates),
        "identity_evidence": list(identities or []),
        "manual_reason_code": manual_reason_code,
        "manual_reason": "",
        "confidence": 0.99,
    }


@pytest.mark.parametrize(
    ("effective_category", "fields", "expected"),
    [
        ("ordinary_3c", {"product_type": "手机"}, SnCategory.PHONE),
        ("ordinary_3c", {"cate_code_name": "平板电脑"}, SnCategory.TABLET),
        ("ordinary_3c", {"product_type": "智能手表"}, SnCategory.WATCH),
        ("ordinary_3c", {"product_type": "智能手环"}, SnCategory.WATCH),
        ("computer", {"product_type": "电脑"}, SnCategory.COMPUTER),
        ("home_appliance", {"cate_code_name": "冰箱"}, SnCategory.HOME_APPLIANCE),
        ("ordinary_3c", {"product_type": "数码相机"}, SnCategory.UNSUPPORTED),
        ("unknown", {"product_type": "手机"}, SnCategory.UNSUPPORTED),
    ],
)
def test_local_fields_route_sn_category_without_model_classification(effective_category, fields, expected):
    assert classify_sn_category(fields, effective_category=effective_category) is expected


@pytest.mark.parametrize("category", list(SnCategory))
def test_sn_main_prompt_is_under_500_chars_and_contains_no_barcode_plugin(category):
    prompt = build_sn_prompt(category)
    assert len(prompt) <= PROMPT_CHAR_LIMIT
    assert "系统SN" not in prompt
    assert "SN_MISMATCH" not in prompt
    assert "二次确认插件" not in prompt
    assert "screen_identity_state" in prompt
    assert "PACKAGE_LABEL" in prompt


def test_model_payload_contains_no_system_sn_or_derived_hint():
    task = {
        "channel_order_no": "order-1",
        "fields": {"product_type": "手机", "system_sn": "SECRET-SN-123"},
    }
    payload = build_model_payload(
        task,
        SnCategory.PHONE,
        [{"image_id": "img_003", "title": "SN码采集 / 激活照片", "source_url": "https://example/img.jpg"}],
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "SECRET-SN-123" not in serialized
    assert "system_sn" not in serialized.lower()


def test_system_sn_missing_routes_system_sn_missing():
    result = decide_sn({"product_type": "手机", "system_sn": ""}, evidence(state="NO_SCREEN_IDENTITY"))
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SYSTEM_SN_MISSING"


def test_model_reference_to_non_order_image_is_uncertain():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(candidate("DEVICE_SCREEN", "ABC123", image_id="other"), state="SCREEN_SN_CLEAR"),
        allowed_image_ids={"img_003"},
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"


@pytest.mark.parametrize("raw_text", ["SN:ABC123", "S/N: ABC123", "Serial No: ABC123", "\u5e8f\u5217\u53f7: ABC123"])
def test_label_prefix_is_stripped_before_exact_compare(raw_text):
    result = decide_sn(
        {"product_type": "\u624b\u673a", "system_sn": "ABC123"},
        evidence(candidate("DEVICE_SCREEN", raw_text), state="SCREEN_SN_CLEAR"),
    )
    assert result["manual_required"] is False
    assert result["normalized_observed_sn"] == "ABC123"


def test_label_prefix_stripping_does_not_correct_similar_characters():
    result = decide_sn(
        {"product_type": "\u624b\u673a", "system_sn": "ABCO123"},
        evidence(candidate("DEVICE_SCREEN", "SN:ABC0123"), state="SCREEN_SN_CLEAR"),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"


@pytest.mark.parametrize(
    ("system_sn", "auxiliary_code", "matching_candidate"),
    [
        ("511320Q1170AA101070058", "D71-001Q1170-25A10-170058", "511-320Q1170AA10-1070058"),
        ("511310A2247B7071241628", "D71-004A2247-26707-441628", "511-310A2247-B707-1241628"),
        ("511-310A1849-B517-1170008", "D71-002A1849-26517-270008", "511-310A1849-B517-1170008"),
    ],
)
def test_home_appliance_exact_system_sn_rescues_d71_auxiliary_code_conflict(
    monkeypatch, system_sn, auxiliary_code, matching_candidate
):
    monkeypatch.setenv("SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE", "true")

    result = decide_sn(
        {"product_type": "电冰箱", "cate_code_name": "电冰箱", "system_sn": system_sn},
        evidence(
            candidate("DEVICE_BODY", auxiliary_code),
            candidate("DEVICE_BODY", matching_candidate),
            state="NO_SCREEN_IDENTITY",
        ),
    )

    assert result["manual_required"] is False
    assert result["sn_match"] is True
    assert result["normalized_observed_sn"] == canonical_system_sn(system_sn)
    assert result["sn_conflict_resolution"] == "home_appliance_exact_system_sn_d71_auxiliary_code"


def test_home_appliance_exact_system_sn_conflict_rescue_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE", raising=False)

    result = decide_sn(
        {"product_type": "电冰箱", "cate_code_name": "电冰箱", "system_sn": "511320Q1170AA101070058"},
        evidence(
            candidate("DEVICE_BODY", "D71-001Q1170-25A10-170058"),
            candidate("DEVICE_BODY", "511-320Q1170AA10-1070058"),
            state="NO_SCREEN_IDENTITY",
        ),
    )

    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"


@pytest.mark.parametrize("product_type", ["手机", "平板电脑", "智能手表", "电脑"])
def test_home_appliance_conflict_rescue_does_not_apply_to_digital_categories(monkeypatch, product_type):
    monkeypatch.setenv("SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE", "true")

    result = decide_sn(
        {"product_type": product_type, "system_sn": "511320Q1170AA101070058"},
        evidence(
            candidate("DEVICE_BODY", "D71-001Q1170-25A10-170058"),
            candidate("DEVICE_BODY", "511-320Q1170AA10-1070058"),
            state="NO_SCREEN_IDENTITY",
        ),
    )

    assert result["manual_required"] is True


def test_home_appliance_conflict_rescue_keeps_real_second_sn_blocked(monkeypatch):
    monkeypatch.setenv("SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE", "true")

    result = decide_sn(
        {"product_type": "电冰箱", "cate_code_name": "电冰箱", "system_sn": "511320Q1170AA101070058"},
        evidence(
            candidate("DEVICE_BODY", "611320Q1170AA101070058"),
            candidate("DEVICE_BODY", "511-320Q1170AA10-1070058"),
            state="NO_SCREEN_IDENTITY",
        ),
    )

    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"


def test_home_appliance_conflict_rescue_does_not_treat_o_zero_as_exact(monkeypatch):
    monkeypatch.setenv("SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE", "true")

    result = decide_sn(
        {"product_type": "电冰箱", "cate_code_name": "电冰箱", "system_sn": "3B164B00RNP00000"},
        evidence(
            candidate("DEVICE_BODY", "D71-001Q1170-25A10-170058"),
            candidate("DEVICE_BODY", "3B164BOORNP00000"),
            state="NO_SCREEN_IDENTITY",
        ),
    )

    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"


def test_system_sn_canonicalization_preserves_zero_and_letter_o():
    assert canonical_system_sn("3B164BOORNP00000") == "3B164BOORNP00000"
    assert canonical_system_sn("3B164B00RNP00000") == "3B164B00RNP00000"


def test_system_sn_letter_o_is_not_rewritten_to_match_model_zero():
    result = decide_sn(
        {"product_type": "\u624b\u673a", "system_sn": "3B164BOORNP00000"},
        evidence(candidate("DEVICE_SCREEN", "3B164B00RNP00000"), state="SCREEN_SN_CLEAR"),
    )

    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["normalized_system_sn"] == "3B164BOORNP00000"
    assert result["normalized_observed_sn"] == "3B164B00RNP00000"


def test_model_side_letter_o_does_not_match_system_zero_by_default():
    result = decide_sn(
        {"product_type": "\u624b\u673a", "system_sn": "3B164B00RNP00000"},
        evidence(candidate("DEVICE_SCREEN", "3B164BOORNP00000"), state="SCREEN_SN_CLEAR"),
    )

    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["normalized_system_sn"] == "3B164B00RNP00000"
    assert result["normalized_observed_sn"] == "3B164BOORNP00000"


def test_canonical_sn_preserves_slash_as_real_character():
    assert canonical_sn("69716/F5ZA00395") == "69716/F5ZA00395"


def test_phone_slash_in_screen_sn_is_not_cleaned_to_match_system_sn():
    result = decide_sn_policy(
        {"product_type": "phone", "system_sn": "69716F5ZA00395"},
        evidence(candidate("DEVICE_SCREEN", "69716/F5ZA00395"), state="SCREEN_SN_CLEAR"),
        effective_category="ordinary_3c",
    )

    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["normalized_observed_sn"] == "69716/F5ZA00395"


def test_real_chinese_serial_field_type_can_match():
    result = decide_sn(
        {"product_type": "\u624b\u673a", "system_sn": "ABC123"},
        evidence(candidate("DEVICE_SCREEN", "ABC123", field_type="\u5e8f\u5217\u53f7"), state="SCREEN_SN_CLEAR"),
    )
    assert result["manual_required"] is False


def test_non_sn_identity_shape_does_not_block_complete_screen_sn():
    model_evidence = evidence(candidate("DEVICE_SCREEN", "ABC123"), state="SCREEN_SN_CLEAR")
    model_evidence["identity_evidence"] = None

    result = decide_sn({"product_type": "\u624b\u673a", "system_sn": "ABC123"}, model_evidence)

    assert result["manual_required"] is False


def test_legacy_identity_shape_supports_phone_identity_only_package_sn():
    model_evidence = evidence(candidate("PACKAGE_LABEL", "ABC123"), state="PHONE_IDENTITY_ONLY")
    model_evidence["identity_evidence"] = [{"image_id": "img_003", "type": "IMEI1", "value": "123456789012345"}]

    result = decide_sn({"product_type": "\u624b\u673a", "system_sn": "ABC123"}, model_evidence)

    assert result["manual_required"] is False
    assert result["selected_source"] == "PACKAGE_LABEL"


def test_home_appliance_service_sticker_package_candidate_can_match():
    result = decide_sn(
        {"cate_code_name": "冰箱", "system_sn": "HAIER123456"},
        evidence(candidate("PACKAGE_LABEL", "HAIER123456"), state="NO_SCREEN_IDENTITY"),
    )
    assert result["manual_required"] is False
    assert result["selected_source"] == "PACKAGE_LABEL"


def test_home_appliance_unclear_sn_evidence_is_uncertain():
    result = decide_sn(
        {"cate_code_name": "冰箱", "system_sn": "HAIER123456"},
        evidence(candidate("DEVICE_BODY", "HAIER12", readable=False, complete=False), state="NO_SCREEN_IDENTITY"),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"


def test_phone_screen_sn_clear_takes_priority_over_package():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC123"),
            candidate("PACKAGE_LABEL", "WRONG999"),
            state="SCREEN_SN_CLEAR",
        ),
    )
    assert result["manual_required"] is False
    assert result["selected_source"] == "DEVICE_SCREEN"


def test_phone_identity_only_allows_package_sn_match():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(candidate("PACKAGE_LABEL", "ABC123"), identities=[identity("IMEI2")], state="PHONE_IDENTITY_ONLY"),
    )
    assert result["manual_required"] is False
    assert result["selected_source"] == "PACKAGE_LABEL"


def test_phone_identity_only_recovers_identity_polluted_sn_candidates():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "123456789012345", field_type="IMEI1"),
            candidate("PACKAGE_LABEL", "ABC123"),
            state="PHONE_IDENTITY_ONLY",
        ),
    )
    assert result["manual_required"] is False
    assert result["selected_source"] == "PACKAGE_LABEL"


def test_phone_identity_pollution_does_not_rescue_package_mismatch():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "123456789012345", field_type="IMEI1"),
            candidate("PACKAGE_LABEL", "XYZ999"),
            state="PHONE_IDENTITY_ONLY",
        ),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"


def test_screen_sn_unclear_allows_package_sn_match():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC", readable=False, complete=False),
            candidate("PACKAGE_LABEL", "ABC123"),
            state="SCREEN_SN_UNCLEAR",
        ),
    )
    assert result["manual_required"] is False
    assert result["selected_source"] == "PACKAGE_LABEL"


@pytest.mark.parametrize("product_type", ["平板电脑", "智能手表", "电脑"])
def test_non_phone_without_screen_sn_trace_cannot_pass_by_package_only(product_type):
    result = decide_sn(
        {"product_type": product_type, "system_sn": "ABC123"},
        evidence(candidate("PACKAGE_LABEL", "ABC123"), state="NO_SCREEN_IDENTITY"),
    )
    assert result["manual_required"] is True
    assert result["sn_match"] is False


@pytest.mark.parametrize("product_type", ["平板电脑", "智能手表", "电脑"])
def test_non_phone_unclear_screen_sn_allows_package_auxiliary_match(product_type):
    result = decide_sn(
        {"product_type": product_type, "system_sn": "ABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC", readable=False, complete=False),
            candidate("PACKAGE_LABEL", "ABC123"),
            state="SCREEN_SN_UNCLEAR",
        ),
    )
    assert result["manual_required"] is False
    assert result["selected_source"] == "PACKAGE_LABEL"


@pytest.mark.parametrize("product_type", ["平板电脑", "智能手表", "电脑"])
def test_non_phone_screen_prefix_can_be_rescued_by_exact_package_sn(product_type):
    result = decide_sn(
        {"product_type": product_type, "system_sn": "3ULYD25508403192"},
        evidence(
            candidate("DEVICE_SCREEN", "3ULYD255084031"),
            candidate("PACKAGE_LABEL", "3ULYD25508403192"),
            state="SCREEN_SN_CLEAR",
        ),
    )
    assert result["manual_required"] is False
    assert result["selected_source"] == "PACKAGE_LABEL"


@pytest.mark.parametrize("product_type", ["平板电脑", "智能手表", "电脑"])
def test_non_phone_screen_prefix_rescue_requires_exact_package_sn(product_type):
    result = decide_sn(
        {"product_type": product_type, "system_sn": "3ULYD25508403192"},
        evidence(
            candidate("DEVICE_SCREEN", "3ULYD255084031"),
            candidate("PACKAGE_LABEL", "3ULYD25508403193"),
            state="SCREEN_SN_CLEAR",
        ),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"


@pytest.mark.parametrize("product_type", ["平板电脑", "智能手表", "电脑"])
def test_non_phone_screen_prefix_rescue_rejects_third_conflicting_sn(product_type):
    result = decide_sn(
        {"product_type": product_type, "system_sn": "3ULYD25508403192"},
        evidence(
            candidate("DEVICE_SCREEN", "3ULYD255084031"),
            candidate("PACKAGE_LABEL", "3ULYD25508403192"),
            candidate("DEVICE_BODY", "ZZZ999"),
            state="SCREEN_SN_CLEAR",
        ),
    )
    assert result["manual_required"] is True
    assert result["sn_match"] is False


def test_screen_sn_conflict_is_uncertain():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC123"),
            candidate("DEVICE_SCREEN", "XYZ999"),
            state="SCREEN_SN_CONFLICT",
        ),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"


def test_leading_s_missing_match_is_limited_to_screen_sn():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "SABC123"},
        evidence(candidate("DEVICE_SCREEN", "ABC123"), state="SCREEN_SN_CLEAR"),
    )
    assert result["manual_required"] is False
    assert result["selected_source"] == "DEVICE_SCREEN"


def test_leading_s_missing_does_not_apply_to_package():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "SABC123"},
        evidence(candidate("PACKAGE_LABEL", "ABC123"), identities=[identity()], state="PHONE_IDENTITY_ONLY"),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"


def test_leading_s_missing_does_not_apply_when_other_candidate_conflicts():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "SABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC123"),
            candidate("PACKAGE_LABEL", "ZZZ999"),
            state="SCREEN_SN_CLEAR",
        ),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"


def test_similar_characters_are_not_equivalent():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABCO123"},
        evidence(candidate("DEVICE_SCREEN", "ABC0123"), state="SCREEN_SN_CLEAR"),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"


def test_ambiguous_candidate_cannot_auto_pass():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(candidate("DEVICE_SCREEN", "ABC123", ambiguity=["O/0 unclear"]), state="SCREEN_SN_UNCLEAR"),
    )
    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"


def test_sn_misplaced_in_identity_evidence_can_be_rescued_as_screen_candidate():
    model_evidence = evidence(
        candidate("PACKAGE_LABEL", "WRONG999"),
        state="SCREEN_SN_CLEAR",
        identities=[identity("SN", "ABC123")],
    )

    result = decide_sn({"product_type": "手机", "system_sn": "ABC123"}, model_evidence)

    assert result["manual_required"] is False
    assert result["selected_source"] == "DEVICE_SCREEN"
    assert result["normalized_observed_sn"] == "ABC123"


@pytest.mark.parametrize("field_type", ["IMEI", "IMEI1", "IMEI2", "1码", "2码", "MEID", "EID"])
def test_identity_fields_are_never_rescued_as_sn(field_type):
    result = decide_sn(
        {"product_type": "手机", "system_sn": "123456789012345"},
        evidence(state="PHONE_IDENTITY_ONLY", identities=[identity(field_type, "123456789012345")]),
    )

    assert result["manual_required"] is True
    assert result["sn_match"] is False


def test_explicit_imei1_mismatch_blocks_after_sn_match():
    result = decide_sn(
        {"product_type": "phone", "system_sn": "ABC123", "imei1": "111111111111111"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC123"),
            state="SCREEN_SN_CLEAR",
            identities=[identity("IMEI1", "999999999999999")],
        ),
        effective_category="ordinary_3c",
    )

    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["identity_code_mismatch"] is True
    assert result["sn_match"] is False


def test_explicit_imei1_mismatch_blocks_even_when_sn_already_mismatched():
    result = decide_sn(
        {"product_type": "phone", "system_sn": "ABC123", "imei1": "111111111111111"},
        evidence(
            candidate("DEVICE_SCREEN", "XYZ999"),
            state="SCREEN_SN_CLEAR",
            identities=[identity("IMEI1", "999999999999999")],
        ),
        effective_category="ordinary_3c",
    )

    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["identity_code_mismatch"] is True
    assert result["sn_match"] is False


def test_generic_imei_does_not_guess_imei1_or_imei2_slot():
    result = decide_sn(
        {
            "product_type": "phone",
            "system_sn": "ABC123",
            "imei1": "111111111111111",
            "imei2": "222222222222222",
        },
        evidence(
            candidate("DEVICE_SCREEN", "ABC123"),
            state="SCREEN_SN_CLEAR",
            identities=[identity("IMEI", "999999999999999")],
        ),
        effective_category="ordinary_3c",
    )

    assert result["manual_required"] is False
    assert result["sn_match"] is True


@pytest.mark.parametrize("field_type,label_text", [("IMEI-1", ""), ("imei_1", ""), ("IMEI1", "I M E I 1")])
def test_explicit_imei1_label_variants_are_checked(field_type, label_text):
    identity_item = identity(field_type, "999999999999999")
    if label_text:
        identity_item["label_text"] = label_text
    result = decide_sn(
        {"product_type": "phone", "system_sn": "ABC123", "imei1": "111111111111111"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC123"),
            state="SCREEN_SN_CLEAR",
            identities=[identity_item],
        ),
        effective_category="ordinary_3c",
    )

    assert result["manual_required"] is True
    assert result["identity_code_mismatch"] is True


@pytest.mark.parametrize(
    "raw_text,complete",
    [("99999999999999", True), ("999999999999999", False)],
)
def test_incomplete_or_non_15_digit_imei1_is_not_used_for_mismatch_gate(raw_text, complete):
    result = decide_sn(
        {"product_type": "phone", "system_sn": "ABC123", "imei1": "111111111111111"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC123"),
            state="SCREEN_SN_CLEAR",
            identities=[identity("IMEI1", raw_text) | {"complete": complete}],
        ),
        effective_category="ordinary_3c",
    )

    assert result["manual_required"] is False
    assert result["sn_match"] is True


def test_incomplete_system_imei1_is_not_used_for_mismatch_gate():
    result = decide_sn(
        {"product_type": "phone", "system_sn": "ABC123", "imei1": "11111111111111"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC123"),
            state="SCREEN_SN_CLEAR",
            identities=[identity("IMEI1", "999999999999999")],
        ),
        effective_category="ordinary_3c",
    )

    assert result["manual_required"] is False
    assert result["sn_match"] is True


def test_explicit_2_code_mismatch_blocks_after_sn_match():
    result = decide_sn(
        {"product_type": "phone", "system_sn": "ABC123", "2_code": "222222222222222"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC123"),
            state="SCREEN_SN_CLEAR",
            identities=[identity("2码", "999999999999999")],
        ),
        effective_category="ordinary_3c",
    )

    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["identity_code_mismatch"] is True
    assert result["sn_match"] is False


def test_phone_identity_only_package_match_does_not_require_identity_evidence():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "J699HC5726"},
        evidence(candidate("PACKAGE_LABEL", "J699HC5726"), state="PHONE_IDENTITY_ONLY", identities=[]),
    )

    assert result["manual_required"] is False
    assert result["selected_source"] == "PACKAGE_LABEL"


def test_phone_package_match_can_continue_when_model_screen_state_has_no_screen_candidate():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "6596CP26008897QS"},
        evidence(candidate("PACKAGE_LABEL", "6596CP26008897QS"), state="SCREEN_SN_CLEAR", identities=[]),
    )

    assert result["manual_required"] is False
    assert result["selected_source"] == "PACKAGE_LABEL"


def test_package_sn_does_not_override_clear_conflicting_screen_sn():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "ABC123"},
        evidence(
            candidate("DEVICE_SCREEN", "ABC123"),
            candidate("DEVICE_SCREEN", "XYZ999"),
            candidate("PACKAGE_LABEL", "ABC123"),
            state="PHONE_IDENTITY_ONLY",
        ),
    )

    assert result["manual_required"] is True
    assert result["sn_match"] is False


@pytest.mark.parametrize("field_type", ["Lenovo SN", "Lenovo S/N", "联想 SN"])
def test_lenovo_sn_label_is_treated_as_sn_and_cleaned(field_type):
    result = decide_sn(
        {"product_type": "电脑", "system_sn": "PF6CKE8X"},
        evidence(candidate("DEVICE_SCREEN", "Lenovo SN PF6CKE8X", field_type=field_type), state="SCREEN_SN_CLEAR"),
    )

    assert result["manual_required"] is False
    assert result["normalized_observed_sn"] == "PF6CKE8X"


def test_tv_screen_serial_number_can_match_system_sn():
    result = decide_sn(
        {"product_type": "[A01] 电视机", "cate_code_name": "电视机", "system_sn": "6QEUN26514000334"},
        evidence(candidate("DEVICE_SCREEN", "6QEUN26514000334", field_type="序列号"), state="SCREEN_SN_CLEAR"),
    )

    assert result["manual_required"] is False
    assert result["selected_source"] == "DEVICE_SCREEN"


def test_non_tv_home_appliance_screen_sn_does_not_auto_pass():
    result = decide_sn(
        {"product_type": "冰箱", "cate_code_name": "冰箱", "system_sn": "HAIER123456"},
        evidence(candidate("DEVICE_SCREEN", "HAIER123456", field_type="序列号"), state="SCREEN_SN_CLEAR"),
    )

    assert result["manual_required"] is True
    assert result["sn_match"] is False


def test_phone_package_similar_character_error_still_does_not_auto_pass():
    result = decide_sn(
        {"product_type": "手机", "system_sn": "HYTX33RCFW"},
        evidence(candidate("PACKAGE_LABEL", "HYTX3RCFW"), state="PHONE_IDENTITY_ONLY", identities=[]),
    )

    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"


def test_tv_wrong_body_identifier_still_does_not_auto_pass():
    result = decide_sn(
        {"product_type": "[A01] 电视机", "cate_code_name": "电视机", "system_sn": "11510101117908E9264J000F6Q"},
        evidence(candidate("DEVICE_BODY", "55F295C/NZZ2610114"), state="NO_SCREEN_IDENTITY"),
    )

    assert result["manual_required"] is True
    assert result["manual_reason_code"] == "SN_MISMATCH"
