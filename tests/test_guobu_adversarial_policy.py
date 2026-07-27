# -*- coding: utf-8 -*-
import pytest

from tools import run_guobu_model_audit_v2 as v2


@pytest.fixture(autouse=True)
def _keep_legacy_sn_tests_on_v1(monkeypatch):
    monkeypatch.setenv("SN_POLICY_VERSION", "v1")


def test_authenticity_prompt_forbids_cross_image_evidence_and_model_final_verdict():
    prompt = v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM

    assert "不得跨图拼接证据" in prompt
    assert "不得输出最终真实性裁决" in prompt
    assert "每个证据只能属于当前 image_id" in prompt


def test_authenticity_prompt_defines_evidence_thresholds_and_exemptions():
    prompt = v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM

    for code in (
        "EXTERNAL_PHOTO_CARRIER",
        "PHOTO_VIEWER_UI",
        "PRINTED_PHOTO_CARRIER",
        "NESTED_IMAGE_BOUNDARY",
        "CROSS_OBJECT_MOIRE",
        "EDGE_CUTOFF",
        "OUTER_PLANE_OPTICS",
        "PLANAR_APPEARANCE",
        "LOCAL_MOIRE",
        "UI_CANDIDATE",
    ):
        assert f"{code}=" in prompt
    assert "商品自身屏幕内UI不记PHOTO_VIEWER_UI" in prompt
    assert "局部摩尔纹只记LOCAL_MOIRE" in prompt
    assert "至少2个不同的非商品屏物理区域" in prompt
    assert "不限于笔直黑边" in prompt
    assert "普通反射、模糊、滤镜、常规裁切、局部纹理或单一弱证据不得记为strong" in prompt


def test_authenticity_prompt_v5_keeps_product_screen_and_external_carrier_boundaries_separate():
    prompt = v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM

    assert "水印、定位、时间、文件/路径/尺寸/EXIF、品牌记忆" in prompt
    assert "水印正常" in prompt
    assert "carrier_boundary只用于外部显示屏/照片/纸张等二次载体边界" in prompt
    assert "不用于商品自身电脑/笔记本/显示器的屏幕边框、机身边框或品牌Logo底边" in prompt
    assert "商品自身屏幕内的系统UI、任务栏、鼠标光标正常" in prompt
    assert "压在包装/背景/照片载体/未知外部画面上的系统UI" in prompt
    assert "跨product_screen与任一非屏物理区" in prompt
    assert "或跨全图" in prompt


def test_authenticity_prompt_replaces_legacy_top_level_authenticity_adjudication():
    prompt = v2.compliance_prompt_for_category(
        "ordinary_3c",
        include_photo_authenticity=True,
        replace_legacy_authenticity_adjudication=True,
    )
    shadow_prompt = v2.compliance_prompt_for_category(
        "ordinary_3c",
        include_photo_authenticity=True,
        replace_legacy_authenticity_adjudication=False,
    )

    assert "本段替代通用强风险中与二次翻拍/拍屏/拍纸照真实性相关的旧裁决规则" in prompt
    assert "不得因真实性观察单独设置顶层 IMAGE_STRONG_RISK、image_risk=true 或 manual_required=true" in prompt
    assert "SN/IMEI篡改、多台设备或多个包装混拍、证据链明显不一致" in prompt
    assert "仍按原业务强风险规则输出 IMAGE_STRONG_RISK" in prompt
    assert "不得因真实性观察单独设置顶层 IMAGE_STRONG_RISK" not in shadow_prompt


def test_authenticity_normalizer_rejects_evidence_that_names_another_image():
    observation = {
        "image_id": "a",
        "edges": {"top": "scene_continues", "right": "scene_continues", "bottom": "scene_continues", "left": "scene_continues"},
        "screen_owner": "none",
        "strong_evidence": [{"code": "CROSS_OBJECT_MOIRE", "regions": ["background"], "image_id": "b"}],
        "weak_evidence": [],
        "reason": "异常",
    }

    try:
        v2._normalize_photo_authenticity_observations({"photo_authenticity_by_image": [observation]}, ("a",))
    except v2.PhotoAuthenticitySchemaError as exc:
        assert "entries require exactly code and regions" in str(exc)
    else:
        raise AssertionError("cross-image evidence must be rejected")


def test_authenticity_normalizer_allows_legal_region_even_when_another_image_uses_that_id():
    first = {
        "image_id": "a",
        "edges": {"top": "scene_continues", "right": "scene_continues", "bottom": "scene_continues", "left": "scene_continues"},
        "screen_owner": "none",
        "strong_evidence": [{"code": "CROSS_OBJECT_MOIRE", "regions": ["background"]}],
        "weak_evidence": [],
        "reason": "异常",
    }
    second = {**first, "image_id": "background", "strong_evidence": []}

    normalized = v2._normalize_photo_authenticity_observations(
        {"photo_authenticity_by_image": [first, second]},
        ("a", "background"),
    )

    assert normalized["a"].strong_evidence[0].regions == ("background",)


def test_prompts_allow_one_two_code_when_package_sn_matches():
    policy_text = v2.SN_PROMPT + "\n" + v2.ORDINARY_3C_COMPLIANCE_PROMPT

    assert "1码" in policy_text
    assert "2码" in policy_text
    assert "包装" in policy_text
    assert "SN 一致性已由系统完成，本阶段不重新识别或比对 SN" in policy_text
    assert "不强制要求屏幕" in policy_text


def test_sn_candidates_are_not_duplicated_in_compliance_stage():
    assert "sn_candidates" in v2.SN_PROMPT
    assert "sn_candidates 中不要列出 IMEI" in v2.SN_PROMPT
    assert "sn_candidates" not in v2.COMPLIANCE_OUTPUT_SCHEMA
    assert "activation_screen" in v2.COMPLIANCE_OUTPUT_SCHEMA
    assert "package_visible" in v2.COMPLIANCE_OUTPUT_SCHEMA


def test_compliance_prompt_treats_clear_web_or_screenshot_risk_as_manual():
    policy_text = "\n".join(
        [
            v2.HOME_APPLIANCE_COMPLIANCE_PROMPT,
            v2.ORDINARY_3C_COMPLIANCE_PROMPT,
            v2.COMPUTER_COMPLIANCE_PROMPT,
        ]
    )

    assert "截图、相册图、电子屏二次翻拍、拼图、P图、SN/IMEI区域篡改" in policy_text
    assert "IMAGE_STRONG_RISK" in policy_text
    assert "每张照片都要检查是否为真实拍摄" in policy_text


def test_activation_evidence_has_priority_over_general_pass():
    policy_text = v2.SN_PROMPT + "\n" + v2.ORDINARY_3C_COMPLIANCE_PROMPT + "\n" + v2.COMPUTER_COMPLIANCE_PROMPT

    assert "亮屏不等于合格" in policy_text
    assert "只有亮屏、锁屏、桌面、开机画面" in policy_text
    assert "拼图" in policy_text
    assert "笔记本激活照片必须同时出现亮屏设备 SN 和包装 SN 合照" in policy_text


def test_final_row_contains_chinese_reason_for_review():
    row = v2._final_row(
        {"channel_order_no": "1", "fields": {"product_type": "[B01] 手机", "system_sn": "ABC123"}},
        {
            "manual_required": True,
            "manual_reason_codes": ["SN_MISMATCH"],
            "manual_reason": "observed SN differs",
        },
        {"sn_match": False, "observed_sn": "ABD123"},
        {},
        1.0,
        0.0,
        1.0,
        0.0,
    )

    assert row["manual_reason_code"] == "SN_MISMATCH"
    assert row["manual_reason_cn"].startswith("系统SN与照片中SN不一致")
    assert "observed SN differs" in row["manual_reason"]


def test_address_reason_chinese_mapping_is_readable():
    assert v2.reason_code_to_chinese("ADDRESS_TOO_COARSE") == "家电收货地址不够精确"


def test_pass_candidate_does_not_add_extra_model_review(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    calls = []

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append((stage, detail))
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "manual_reason_code": "SN_MATCH", "confidence": 0.95},
                "{}",
                1.0,
                {"total_tokens": 10},
                False,
            )
        if stage == "hybrid_compliance":
            return (
                {
                    "manual_required": False,
                    "manual_reason_codes": [],
                    "manual_reason": "",
                    "product_type_match": True,
                    "product_photo_ok": True,
                    "unboxing_photo_ok": True,
                    "activation_photo_ok": True,
                    "activation_evidence_type": "SCREEN_SN",
                    "sn_candidates": [
                        {
                            "image_id": "img_002",
                            "source": "SCREEN",
                            "raw_text": "ABC123",
                            "normalized_text": "ABC123",
                            "readable": True,
                            "matches_system_sn": True,
                        }
                    ],
                    "activation_screen": {
                        "screen_on": True,
                        "screen_content_type": "ABOUT_DEVICE_SN",
                        "screen_sn_visible": True,
                        "screen_sn_text": "ABC123",
                    },
                    "activation_identity_by_image": [
                        {
                            "image_id": "img_002",
                            "screen_on": True,
                            "screen_source": "PRODUCT_DEVICE_SCREEN",
                            "page_type": "DEVICE_INFO",
                            "identity_fields": [
                                {
                                    "field_type": "SN",
                                    "raw_value": "ABC123",
                                    "readable": True,
                                    "complete": True,
                                }
                            ],
                        }
                    ],
                    "photo_integrity": {
                        "collage_or_edit_risk": False,
                        "evidence_chain_trustworthy": True,
                    },
                    "image_risk": False,
                    "duplicate_image_evidence": False,
                    "confidence": 0.95,
                },
                "{}",
                1.0,
                {"total_tokens": 10},
                False,
            )
        raise AssertionError(stage)

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    task = {
        "channel_order_no": "1",
        "fields": {
            "product_type": "[B01] 手机",
            "system_sn": "ABC123",
            "is_home_appliance": False,
            "address": "",
        },
        "image_groups": {
            "拆封照片": [{"image_id": "img_001", "title": "拆封照片", "source_url": "a"}],
            "SN码采集 / 激活照片": [{"image_id": "img_002", "title": "SN码采集 / 激活照片", "source_url": "b"}],
        },
    }

    result = v2.audit_task_hybrid("https://example.invalid/v1", "key", "model", task)

    assert calls == [("hybrid_sn", "high"), ("hybrid_compliance", "low")]
    assert result["manual_flag"] == "否"
    assert result["strategy"] == "hybrid_sn_then_compliance"


def test_screen_sn_label_without_structured_screen_text_is_manual():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["ACTIVATION_PHOTO_INVALID"]


def test_screen_active_with_sn_label_without_linked_evidence_is_manual():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_ACTIVE_WITH_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "HOME_OR_LOCK_SCREEN",
                "screen_sn_visible": False,
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "PACKAGE_LABEL",
                    "raw_text": "ABC123",
                    "normalized_text": "ABC123",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "same_photo_or_same_group_chain": True,
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["ACTIVATION_PHOTO_INVALID"]


def test_screen_active_with_sn_passes_with_structured_package_link():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type": "[B01] phone",
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_ACTIVE_WITH_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "DEVICE_INFO_WITH_ID",
                "screen_sn_visible": False,
                "screen_identity_text": "device info page",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "PACKAGE_LABEL",
                    "raw_text": "ABC123",
                    "normalized_text": "ABC123",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "same_photo_or_same_group_chain": True,
            "photo_integrity": {
                "collage_or_edit_risk": False,
                "evidence_chain_trustworthy": True,
            },
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_tablet_screen_sn_candidate_can_pass():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type": "[B02] tablet",
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "ABC123",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "ABC123",
                    "normalized_text": "ABC123",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_watch_screen_sn_candidate_can_pass():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type": "[B03] watch",
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "ABC123",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "ABC123",
                    "normalized_text": "ABC123",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_clean_computer_screen_sn_candidate_can_pass_when_tamper_checks_pass():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "PF5K2115",
            "product_type": "[A05] PC",
            "sn_confidence": 0.99,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "BIOS_INFO",
                "screen_sn_visible": True,
                "screen_sn_text": "PF5K2115",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "PF5K2115",
                    "normalized_text": "PF5K2115",
                    "readable": True,
                    "matches_system_sn": True,
                },
                {
                    "image_id": "img_002",
                    "source": "PACKAGE_LABEL",
                    "raw_text": "PF5K2115",
                    "normalized_text": "PF5K2115",
                    "readable": True,
                    "matches_system_sn": True,
                },
            ],
            "tamper_checks": {
                "font_consistency_ok": True,
                "perspective_consistency_ok": True,
                "noise_compression_consistency_ok": True,
                "edge_blending_ok": True,
                "screen_reflection_consistency_ok": True,
            },
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.99,
        }
    )

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_screen_sn_text_mismatch_is_manual_even_when_model_claims_pass():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "ABD123",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "ABD123",
                    "normalized_text": "ABD123",
                    "readable": True,
                    "matches_system_sn": False,
                }
            ],
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["SN_MISMATCH"]


def test_collage_or_edit_suspected_is_image_risk_manual():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "ABC123",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "ABC123",
                    "normalized_text": "ABC123",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "photo_integrity": {
                "collage_or_edit_risk": True,
                "evidence_chain_trustworthy": False,
            },
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["IMAGE_STRONG_RISK"]


def test_pass_candidate_with_low_sn_confidence_is_manual_uncertain():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type": "[B01] phone",
            "sn_confidence": 0.74,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "ABC123",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "ABC123",
                    "normalized_text": "ABC123",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["MODEL_UNCERTAIN"]


def test_screen_sn_with_extra_character_is_sn_mismatch():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "10CFAQX7B000ZM",
            "product_type": "[B01] phone",
            "sn_confidence": 0.96,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "10CFAQX17B000ZM",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "SN: 10CFAQX17B000ZM",
                    "normalized_text": "10CFAQX17B000ZM",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["SN_MISMATCH"]


def test_screen_showing_photo_or_screenshot_is_image_risk_manual():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type": "[B01] phone",
            "sn_confidence": 0.96,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "PHOTO_VIEWER_OR_SCREENSHOT",
                "screen_sn_visible": True,
                "screen_sn_text": "ABC123",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "ABC123",
                    "normalized_text": "ABC123",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "photo_integrity": {
                "collage_or_edit_risk": False,
                "screen_shows_photo_or_screenshot": True,
                "evidence_chain_trustworthy": False,
            },
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["IMAGE_STRONG_RISK"]


def test_photo_viewer_screen_content_type_forces_image_risk_manual():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type": "[B01] phone",
            "sn_confidence": 0.96,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "PHOTO_VIEWER_OR_SCREENSHOT",
                "screen_sn_visible": True,
                "screen_sn_text": "ABC123",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "SN: ABC123",
                    "normalized_text": "ABC123",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "photo_integrity": {
                "collage_or_edit_risk": False,
                "evidence_chain_trustworthy": True,
            },
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["IMAGE_STRONG_RISK"]


def test_confusable_sn_characters_can_auto_pass_when_position_exactly_matches():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "AQULO25A22003424",
            "product_type": "[B01] phone",
            "sn_confidence": 0.98,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "AQULO25A22003424",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "SN:AQULO25A22003424",
                    "normalized_text": "AQULO25A22003424",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.98,
        }
    )

    assert result["manual_required"] is False


def test_confusable_sn_position_conflict_forces_sn_mismatch_manual():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "AQULO25A22003424",
            "observed_sn": "AQUL025A22003424",
            "product_type": "[B01] phone",
            "sn_confidence": 0.98,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "AQUL025A22003424",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "SN:AQUL025A22003424",
                    "normalized_text": "AQUL025A22003424",
                    "readable": True,
                    "matches_system_sn": False,
                }
            ],
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.98,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["SN_MISMATCH"]


def test_unlabeled_compliance_candidate_does_not_override_sn_specialist_match():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "observed_sn": "ABC123",
            "product_type": "[B01] phone",
            "sn_confidence": 0.96,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "ABC123",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "BARCODE_TEXT",
                    "raw_text": "IMEI 867530900000000",
                    "normalized_text": "867530900000000",
                    "readable": True,
                    "matches_system_sn": False,
                }
            ],
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.96,
        }
    )

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_compliance_sn_mismatch_code_without_explicit_sn_conflict_is_ignored():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": True,
            "manual_reason_codes": ["SN_MISMATCH"],
            "manual_reason": "IMEI differs from system SN",
            "system_sn": "ABC123",
            "observed_sn": "ABC123",
            "product_type": "[B01] phone",
            "sn_confidence": 0.96,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "ABC123",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "IMEI1: 867530900000000",
                    "normalized_text": "867530900000000",
                    "readable": True,
                    "matches_system_sn": False,
                }
            ],
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.96,
        }
    )

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_home_appliance_package_serial_mismatch_overrides_activation_requirement():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": True,
            "manual_reason_codes": ["ACTIVATION_PHOTO_INVALID"],
            "manual_reason": "激活/SN证据链不足",
            "system_sn": "BC12P200BDDBAQ5GLRD8",
            "product_type": "[A02] 电冰箱",
            "is_home_appliance": True,
            "sn_confidence": 0.86,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "package_visible": True,
            "activation_photo_ok": False,
            "activation_evidence_type": "PACKAGE_SN_ONLY",
            "sn_candidates": [
                {
                    "image_id": "img_003",
                    "source": "PACKAGE_LABEL",
                    "raw_text": "Serial Number: BC12P 20000 0BAQ5 GLRDB",
                    "normalized_text": "BC12P200000BAQ5GLRDB",
                    "readable": True,
                    "matches_system_sn": False,
                },
                {
                    "image_id": "img_003",
                    "source": "PACKAGE_LABEL",
                    "raw_text": "BCD-472WGHTDB9SJU1",
                    "normalized_text": "BCD472WGHTDB9SJU1",
                    "readable": True,
                    "matches_system_sn": False,
                },
            ],
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.86,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["SN_MISMATCH"]


def test_home_appliance_package_sn_pass_does_not_require_screen_on():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": True,
            "manual_reason_codes": ["ACTIVATION_PHOTO_INVALID"],
            "manual_reason": "activation evidence lacks screen-on photo",
            "system_sn": "ABC123",
            "observed_sn": "ABC123",
            "product_type": "[A02] refrigerator",
            "is_home_appliance": True,
            "sn_confidence": 0.96,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "package_visible": True,
            "activation_photo_ok": False,
            "activation_evidence_type": "PACKAGE_SN_ONLY",
            "sn_candidates": [
                {
                    "image_id": "img_003",
                    "source": "PACKAGE_LABEL",
                    "raw_text": "S/N: ABC123",
                    "normalized_text": "ABC123",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.96,
        }
    )

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_final_row_uses_compliance_sn_conflict_for_display():
    row = v2._final_row(
        {
            "channel_order_no": "471160581556415922176074",
            "fields": {
                "product_type": "[A02] 电冰箱",
                "system_sn": "BC12P200BDDBAQ5GLRD8",
            },
        },
        {
            "manual_required": True,
            "manual_reason_codes": ["SN_MISMATCH"],
            "manual_reason": "系统SN与照片中SN不一致",
        },
        {"sn_match": True, "observed_sn": "BC12P200BDDBAQ5GLRD8", "confidence": 0.88},
        {
            "activation_evidence_type": "PACKAGE_SN_ONLY",
            "sn_candidates": [
                {
                    "source": "PACKAGE_LABEL",
                    "raw_text": "Serial Number: BC12P 20000 0BAQ5 GLRDB",
                    "normalized_text": "BC12P200000BAQ5GLRDB",
                    "readable": True,
                    "matches_system_sn": False,
                }
            ],
            "confidence": 0.86,
        },
        1.0,
        0.0,
        0.5,
        0.5,
    )

    assert row["observed_sn"] == "BC12P200000BAQ5GLRDB"
    assert row["sn_match"] is False


def test_font_difference_alone_does_not_force_image_risk_manual():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type": "[A05] PC",
            "sn_confidence": 0.98,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "BIOS_INFO",
                "screen_sn_visible": True,
                "screen_sn_text": "ABC123",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "ABC123",
                    "normalized_text": "ABC123",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "tamper_checks": {
                "font_consistency_ok": False,
                "perspective_consistency_ok": True,
                "noise_compression_consistency_ok": True,
                "edge_blending_ok": True,
                "screen_reflection_consistency_ok": True,
            },
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.98,
        }
    )

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_non_font_tamper_check_failure_forces_image_risk_manual():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type": "[A05] PC",
            "sn_confidence": 0.98,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "BIOS_INFO",
                "screen_sn_visible": True,
                "screen_sn_text": "ABC123",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "ABC123",
                    "normalized_text": "ABC123",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "tamper_checks": {
                "font_consistency_ok": True,
                "perspective_consistency_ok": True,
                "noise_compression_consistency_ok": False,
                "edge_blending_ok": False,
                "screen_reflection_consistency_ok": True,
            },
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.98,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["IMAGE_STRONG_RISK"]


def test_sn_local_erasure_or_overwrite_risk_forces_manual_for_any_product():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "ABC123",
            "product_type": "[B01] phone",
            "sn_confidence": 0.98,
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "ABC123",
            },
            "sn_candidates": [
                {
                    "image_id": "img_002",
                    "source": "SCREEN",
                    "raw_text": "ABC123",
                    "normalized_text": "ABC123",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "tamper_checks": {
                "erasure_or_overwrite_risk": True,
                "local_background_break_risk": True,
            },
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.98,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["IMAGE_STRONG_RISK"]
