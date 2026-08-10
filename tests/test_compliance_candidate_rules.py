# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import compliance_candidate_rules as candidate


HUMAN_LABEL_FIXTURE = (
    Path(__file__).parent / "fixtures" / "compliance_candidate_manual_labels_9.json"
)
MANUAL_PASS_FIXTURE = (
    Path(__file__).parent / "fixtures" / "compliance_candidate_manual_pass_10.json"
)
EXPECTED_HUMAN_LABELS = [
    ("SYNTH-MANUAL-001", "UNBOXING_PHOTO_INVALID"),
    ("SYNTH-MANUAL-002", "UNBOXING_PHOTO_INVALID"),
    ("SYNTH-MANUAL-003", "UNBOXING_PHOTO_INVALID"),
    ("SYNTH-MANUAL-004", "UNBOXING_PHOTO_INVALID"),
    ("SYNTH-MANUAL-005", "UNBOXING_PHOTO_INVALID"),
    ("SYNTH-MANUAL-006", "UNBOXING_PHOTO_INVALID"),
    ("SYNTH-MANUAL-007", "UNBOXING_PHOTO_INVALID"),
    ("SYNTH-MANUAL-008", "UNBOXING_PHOTO_INVALID"),
    ("SYNTH-MANUAL-009", "PRODUCT_TYPE_MISMATCH"),
]


def test_manual_label_regression_fixture_is_exact_and_ordered():
    labels = json.loads(HUMAN_LABEL_FIXTURE.read_text(encoding="utf-8"))

    assert [
        (item["order_id"], item["expected_reason_code"]) for item in labels
    ] == EXPECTED_HUMAN_LABELS
    assert {item["expected_decision"] for item in labels} == {"manual_review"}


def test_manual_pass_regression_fixture_is_exact_and_ordered():
    labels = json.loads(MANUAL_PASS_FIXTURE.read_text(encoding="utf-8"))

    assert [item["order_id"] for item in labels] == [
        "SYNTH-PASS-001",
        "SYNTH-PASS-002",
        "SYNTH-PASS-003",
        "SYNTH-PASS-004",
        "SYNTH-PASS-005",
        "SYNTH-PASS-006",
        "SYNTH-PASS-007",
        "SYNTH-PASS-008",
        "SYNTH-PASS-009",
        "SYNTH-PASS-010",
    ]
    assert {item["expected_decision"] for item in labels} == {"pass"}


EXPECTED_PROMPTS = {
    "home_appliance": """你是国补家电图片合规审核员，只输出JSON。

当前品类：家电（冰箱、电视机、空调、热水器、洗衣机）。

规则：
1. 三张及以上完全相同原始文件判DUPLICATE_IMAGE_EVIDENCE；仅两张重复，或角度、位置、裁切、透视、背景、光线、屏幕内容任一不同，不判。
2. 商品照看到商品本体，或看到纸箱类的包装，即为合格；只有外包装也可合格，不要求商品本体同框。
3. 拆封/安装照逐张判断，严禁跨组补证。unboxing_image_evidence按本组每图一项填写，image_id照抄标签，不得缺失、重复或使用其他组ID。有包装时，同一项product_visible和package_visible均为true即通过，不要求家庭场景；同画面内无论旋转、远近、并排或分处两侧均算同图，不得判分拍，不同项不得拼接。仅见纸箱、泡沫、塑料袋、标签、说明书或开箱动作判UNBOXING_PHOTO_INVALID。无包装时，同一项product_visible和home_or_installation_scene_visible均为true才通过；门店、仓库、店铺门口、人物合影或场景不明判该码。
4. 不核验品牌、型号或包装文字一致性，品牌不同不得作为不通过理由。仅按可见形态判断与category_name是否同类；明显不同判PRODUCT_TYPE_MISMATCH，不明判MODEL_UNCERTAIN。热水器含电热水器。
5. evidence_summary只写可见事实，不作额外推测。

输出：
{
  "manual_reason_codes": [],
  "product_type_match": "match | mismatch | unknown",
  "product_photo_ok": boolean,
  "unboxing_photo_ok": boolean,
  "unboxing_image_evidence": [
    {
      "image_id": "",
      "product_visible": boolean,
      "package_visible": boolean,
      "home_or_installation_scene_visible": boolean
    }
  ],
  "duplicate_image_evidence": boolean,
  "evidence_summary": "",
  "confidence": number
}

原因码仅限：
DUPLICATE_IMAGE_EVIDENCE,
PRODUCT_TYPE_MISMATCH,
PRODUCT_PHOTO_INVALID,
UNBOXING_PHOTO_INVALID,
MODEL_UNCERTAIN。""",
    "ordinary_3c": """你是国补普通3C图片合规审核员，只输出JSON。

当前品类：普通3C（手机、平板、智能手表/手环、智能眼镜）。

规则：
1. 三张及以上完全相同原始文件判DUPLICATE_IMAGE_EVIDENCE；仅两张重复，或角度、位置、裁切、透视、背景、光线、屏幕内容任一不同，不判。
2. 商品照需看到设备本体或有效外包装，否则判PRODUCT_PHOTO_INVALID。
3. 拆封照需看到已拆封的设备本体，否则判UNBOXING_PHOTO_INVALID。
4. 手机激活照需亮屏并出现SN、序列号、IMEI1、IMEI2、1码、2码、MEID或EID之一；平板、手表/手环需亮屏显示SN或序列号。
5. 智能眼镜不需亮屏，可见有效包装盒即可。
6. 仅锁屏、桌面、开机、配对或设置引导页不算有效身份页。
7. 按可见形态判断与category_name是否同类；带固定底座的小度智能屏/中控屏/音箱不属于平板，申报平板判PRODUCT_TYPE_MISMATCH；其他明显不同同判，不明判MODEL_UNCERTAIN。
8. evidence_summary只写可见事实，不作额外推测。

输出：
{
  "manual_reason_codes": [],
  "product_type_match": "match | mismatch | unknown",
  "product_photo_ok": boolean,
  "unboxing_photo_ok": boolean,
  "activation_photo_ok": boolean,
  "activation_evidence_type": "SCREEN_SN | PHONE_IDENTITY_ONLY | SMART_GLASSES_PACKAGE | PAIRING_OR_SETUP | SCREEN_ON_NO_IDENTITY | UNCLEAR | NONE",
  "duplicate_image_evidence": boolean,
  "evidence_summary": ""
}

原因码仅限：
DUPLICATE_IMAGE_EVIDENCE,
PRODUCT_TYPE_MISMATCH,
PRODUCT_PHOTO_INVALID,
UNBOXING_PHOTO_INVALID,
ACTIVATION_PHOTO_INVALID,
MODEL_UNCERTAIN。""",
    "computer": """你是国补电脑图片合规审核员，只输出JSON。

当前品类：电脑（笔记本、台式机、一体机）。

规则：
1. 三张及以上完全相同原始文件，判DUPLICATE_IMAGE_EVIDENCE。只有两张重复，或角度、位置、裁切、透视、背景、光线、屏幕内容任一不同，不判重复。
2. 商品照需看到电脑本体或有效外包装，否则判PRODUCT_PHOTO_INVALID。
3. 拆封照需看到已拆封的电脑本体，并能与包装形成同一商品证据链，否则判UNBOXING_PHOTO_INVALID。
4. 笔记本激活照需在同一照片中看到电脑亮屏显示SN或序列号，同时看到包装SN。
5. 台式机激活照需看到主机机身SN或铭牌，并与外包装SN形成证据链。
6. 一体机激活照按笔记本规则判断。
7. 只有锁屏、桌面、开机画面、包装SN或机身外观，不属于有效的笔记本激活证据。
8. 仅根据可见商品形态判断是否与category_name同类；明显不同判PRODUCT_TYPE_MISMATCH，无法判断判MODEL_UNCERTAIN。
9. evidence_summary只写可见事实，不作额外推测。

输出：
{
  "manual_reason_codes": [],
  "product_type_match": "match | mismatch | unknown",
  "product_photo_ok": boolean,
  "unboxing_photo_ok": boolean,
  "activation_photo_ok": boolean,
  "activation_evidence_type": "LAPTOP_SCREEN_SN_WITH_PACKAGE | DESKTOP_BODY_SN_WITH_PACKAGE | SCREEN_SN_ONLY | PACKAGE_SN_ONLY | UNCLEAR | NONE",
  "duplicate_image_evidence": boolean,
  "evidence_summary": ""
}

原因码仅限：
DUPLICATE_IMAGE_EVIDENCE,
PRODUCT_TYPE_MISMATCH,
PRODUCT_PHOTO_INVALID,
UNBOXING_PHOTO_INVALID,
ACTIVATION_PHOTO_INVALID,
MODEL_UNCERTAIN。""",
}

EXPECTED_BODY_LENGTHS = {
    "home_appliance": 654,
    "ordinary_3c": 499,
    "computer": 493,
}
EXPECTED_FULL_LENGTHS = {
    "home_appliance": 1208,
    "ordinary_3c": 1048,
    "computer": 1039,
}
EXPECTED_SHA256 = {
    "home_appliance": "46e99b3bf04b0ce410c9d62f26eb753d9e846468e2e0c6081bad539b4eb8a190",
    "ordinary_3c": "47c2150b1678e6945130f3aeb13158c537e2e88120b839dc13c981ba292e5291",
    "computer": "a9f7cc695387882e98a9221916aa8ab033d4219ef6bb47b46a82dfd2d527ed9b",
}

OLD_HOME_PRODUCT_PHOTO_RULE = (
    "2. 商品照需看到商品本体或有效外包装，否则判PRODUCT_PHOTO_INVALID。"
)
NEW_HOME_PRODUCT_PHOTO_RULE = (
    "2. 商品照看到商品本体，或看到纸箱类的包装，即为合格；只有外包装也可合格，不要求商品本体同框。"
)
V5_PROMPT_SHA256 = {
    "home_appliance": "b30f23e8536f32adccb360d85a97b5e6bf5635843d1cb8493b8771026bb05f74",
    "ordinary_3c": "47c2150b1678e6945130f3aeb13158c537e2e88120b839dc13c981ba292e5291",
    "computer": "a9f7cc695387882e98a9221916aa8ab033d4219ef6bb47b46a82dfd2d527ed9b",
}


def test_v6_changes_only_home_product_photo_prompt_contract():
    home_prompt = EXPECTED_PROMPTS["home_appliance"]

    assert home_prompt.count(NEW_HOME_PRODUCT_PHOTO_RULE) == 1
    assert OLD_HOME_PRODUCT_PHOTO_RULE not in home_prompt
    assert EXPECTED_SHA256["home_appliance"] != V5_PROMPT_SHA256["home_appliance"]
    assert EXPECTED_SHA256["ordinary_3c"] == V5_PROMPT_SHA256["ordinary_3c"]
    assert EXPECTED_SHA256["computer"] == V5_PROMPT_SHA256["computer"]
    assert candidate.CANDIDATE_VERSION == "compliance-candidate-v6-20260804"
    assert candidate.CANDIDATE_STAGE == "compliance_candidate_v6"


@pytest.mark.parametrize("category", tuple(EXPECTED_PROMPTS))
def test_prompt_is_byte_exact_and_hash_locked(category):
    prompt = candidate.prompt_for_category(category)
    expected = EXPECTED_PROMPTS[category]

    assert prompt == expected
    assert "\r" not in prompt
    assert not prompt.startswith("\ufeff")
    assert not prompt.endswith("\n")
    assert len(prompt.split("\n\n输出：\n", 1)[0]) == EXPECTED_BODY_LENGTHS[category]
    assert len(prompt) == EXPECTED_FULL_LENGTHS[category]
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == EXPECTED_SHA256[category]
    assert candidate.PROMPT_SHA256[category] == EXPECTED_SHA256[category]


def test_prompt_loader_rejects_any_character_drift(monkeypatch):
    monkeypatch.setitem(
        candidate.PROMPTS,
        "home_appliance",
        EXPECTED_PROMPTS["home_appliance"] + "\n",
    )

    with pytest.raises(RuntimeError, match="prompt SHA-256 mismatch"):
        candidate.prompt_for_category("home_appliance")


def test_home_prompt_excludes_brand_checks_and_separates_packaged_scene_rule():
    prompt = candidate.prompt_for_category("home_appliance")

    assert "不核验品牌、型号或包装文字一致性" in prompt
    assert "品牌不同不得作为不通过理由" in prompt
    assert "有包装时" in prompt
    assert "不要求家庭场景" in prompt
    assert "无包装时" in prompt
    assert "无论旋转、远近、并排或分处两侧均算同图" in prompt
    assert "不得判分拍" in prompt


def test_candidate_module_has_no_unapproved_business_adjudication():
    forbidden = (
        "adjudicate",
        "exact_local_duplicate_groups",
        "REQUIRED_FIELDS",
        "ALLOWED_REASON_CODES",
        "ACTIVATION_EVIDENCE_TYPES",
    )

    assert [name for name in forbidden if hasattr(candidate, name)] == []


def test_unknown_candidate_category_is_not_inferred():
    with pytest.raises(ValueError, match="unsupported candidate category"):
        candidate.prompt_for_category("unknown")


def _complete_candidate_response(
    category: str,
    *,
    activation_evidence_type: str = "",
) -> dict:
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
    return {
        **common,
        "activation_photo_ok": True,
        "activation_evidence_type": activation_evidence_type,
    }


_ORDINARY_3C_EVIDENCE_TYPES = {
    "SCREEN_SN",
    "PHONE_IDENTITY_ONLY",
    "SMART_GLASSES_PACKAGE",
    "PAIRING_OR_SETUP",
    "SCREEN_ON_NO_IDENTITY",
    "UNCLEAR",
    "NONE",
}
_ORDINARY_3C_ALLOWED_EVIDENCE = {
    "[B01] 手机": {"SCREEN_SN", "PHONE_IDENTITY_ONLY"},
    "[B02] 平板": {"SCREEN_SN"},
    "[B03] 智能手表手环": {"SCREEN_SN"},
    "[B04] 智能眼镜": {"SMART_GLASSES_PACKAGE"},
}


@pytest.mark.parametrize(
    ("product_type", "activation_evidence_type"),
    [
        (product_type, evidence_type)
        for product_type, evidence_types in _ORDINARY_3C_ALLOWED_EVIDENCE.items()
        for evidence_type in sorted(evidence_types)
    ],
)
def test_ordinary_3c_accepts_only_subtype_appropriate_evidence(
    product_type, activation_evidence_type
):
    validation = candidate.validate_candidate_response(
        "ordinary_3c",
        product_type,
        _complete_candidate_response(
            "ordinary_3c",
            activation_evidence_type=activation_evidence_type,
        ),
    )

    assert validation["manual_required"] is False
    assert validation["structure_anomaly"] is False


@pytest.mark.parametrize(
    ("product_type", "activation_evidence_type"),
    [
        (product_type, evidence_type)
        for product_type, allowed_types in _ORDINARY_3C_ALLOWED_EVIDENCE.items()
        for evidence_type in sorted(_ORDINARY_3C_EVIDENCE_TYPES - allowed_types)
    ],
)
def test_ordinary_3c_rejects_invalid_or_cross_subtype_evidence(
    product_type, activation_evidence_type
):
    validation = candidate.validate_candidate_response(
        "ordinary_3c",
        product_type,
        _complete_candidate_response(
            "ordinary_3c",
            activation_evidence_type=activation_evidence_type,
        ),
    )

    assert validation["manual_required"] is True
    assert validation["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert validation["structure_anomaly"] is True
    assert "activation_evidence_type" in validation["invalid_model_fields"]


_COMPUTER_EVIDENCE_TYPES = {
    "LAPTOP_SCREEN_SN_WITH_PACKAGE",
    "DESKTOP_BODY_SN_WITH_PACKAGE",
    "SCREEN_SN_ONLY",
    "PACKAGE_SN_ONLY",
    "UNCLEAR",
    "NONE",
}
_COMPUTER_ALLOWED_EVIDENCE = {
    "[A05] 电脑": {
        "LAPTOP_SCREEN_SN_WITH_PACKAGE",
        "DESKTOP_BODY_SN_WITH_PACKAGE",
    },
    "电脑": {
        "LAPTOP_SCREEN_SN_WITH_PACKAGE",
        "DESKTOP_BODY_SN_WITH_PACKAGE",
    },
    "笔记本电脑": {"LAPTOP_SCREEN_SN_WITH_PACKAGE"},
    "一体机": {"LAPTOP_SCREEN_SN_WITH_PACKAGE"},
    "台式机": {"DESKTOP_BODY_SN_WITH_PACKAGE"},
}


@pytest.mark.parametrize(
    ("product_type", "activation_evidence_type"),
    [
        (product_type, evidence_type)
        for product_type, evidence_types in _COMPUTER_ALLOWED_EVIDENCE.items()
        for evidence_type in sorted(evidence_types)
    ],
)
def test_computer_accepts_only_subtype_appropriate_evidence(
    product_type, activation_evidence_type
):
    validation = candidate.validate_candidate_response(
        "computer",
        product_type,
        _complete_candidate_response(
            "computer",
            activation_evidence_type=activation_evidence_type,
        ),
    )

    assert validation["manual_required"] is False
    assert validation["structure_anomaly"] is False


@pytest.mark.parametrize(
    ("product_type", "activation_evidence_type"),
    [
        (product_type, evidence_type)
        for product_type, allowed_types in _COMPUTER_ALLOWED_EVIDENCE.items()
        for evidence_type in sorted(_COMPUTER_EVIDENCE_TYPES - allowed_types)
    ],
)
def test_computer_rejects_invalid_or_cross_subtype_evidence(
    product_type, activation_evidence_type
):
    validation = candidate.validate_candidate_response(
        "computer",
        product_type,
        _complete_candidate_response(
            "computer",
            activation_evidence_type=activation_evidence_type,
        ),
    )

    assert validation["manual_required"] is True
    assert validation["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert validation["structure_anomaly"] is True
    assert "activation_evidence_type" in validation["invalid_model_fields"]


def _validate_home(response: dict, *image_ids: str) -> dict:
    return candidate.validate_candidate_response(
        "home_appliance",
        "[A02] 电冰箱",
        response,
        unboxing_image_ids=image_ids or ("img_002",),
    )


@pytest.mark.parametrize(
    ("product_visible", "scene_visible", "manual_required"),
    [
        (True, True, False),
        (True, False, True),
        (False, True, True),
        (False, False, True),
    ],
)
def test_home_without_package_requires_product_and_home_scene_in_same_image(
    product_visible, scene_visible, manual_required
):
    response = _complete_candidate_response("home_appliance")
    response["unboxing_image_evidence"] = [
        {
            "image_id": "img_002",
            "product_visible": product_visible,
            "package_visible": False,
            "home_or_installation_scene_visible": scene_visible,
        }
    ]

    validation = _validate_home(response)

    assert validation["manual_required"] is manual_required
    assert validation["structure_anomaly"] is False
    assert validation["manual_reason_codes"] == (
        ["UNBOXING_PHOTO_INVALID"] if manual_required else []
    )
    assert validation["effective_unboxing_photo_ok"] is (not manual_required)


def test_home_packaged_evidence_cannot_join_product_and_package_across_photos():
    response = _complete_candidate_response("home_appliance")
    response["unboxing_image_evidence"] = [
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
    ]

    validation = _validate_home(response, "img_002", "img_004")

    assert validation["manual_required"] is True
    assert validation["manual_reason_codes"] == ["UNBOXING_PHOTO_INVALID"]
    assert validation["structure_anomaly"] is False
    assert validation["effective_unboxing_photo_ok"] is False


def test_home_explicit_unboxing_reason_remains_authoritative_for_cross_photo_evidence():
    response = _complete_candidate_response("home_appliance")
    response["unboxing_image_evidence"] = [
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
    ]
    response["unboxing_photo_ok"] = False
    response["manual_reason_codes"] = ["UNBOXING_PHOTO_INVALID"]

    validation = _validate_home(response, "img_002", "img_004")

    assert validation["manual_required"] is True
    assert validation["manual_reason_codes"] == ["UNBOXING_PHOTO_INVALID"]
    assert validation["structure_anomaly"] is False


def test_home_per_image_packaged_evidence_removes_model_unboxing_overreach():
    response = _complete_candidate_response("home_appliance")
    response["unboxing_photo_ok"] = False
    response["manual_reason_codes"] = ["UNBOXING_PHOTO_INVALID"]

    validation = _validate_home(response)

    assert validation["manual_required"] is False
    assert validation["manual_reason_codes"] == []
    assert validation["effective_unboxing_photo_ok"] is True
    assert validation["structure_anomaly"] is False
    assert validation["local_corrections"] == [
        "REMOVE_UNBOXING_PHOTO_INVALID_PER_IMAGE_PACKAGED_EVIDENCE"
    ]


def test_home_packaged_evidence_drops_extra_non_unboxing_image_id():
    response = _complete_candidate_response("home_appliance")
    response["unboxing_photo_ok"] = False
    response["manual_reason_codes"] = ["UNBOXING_PHOTO_INVALID"]
    response["unboxing_image_evidence"].append(
        {
            "image_id": "img_003",
            "product_visible": False,
            "package_visible": True,
            "home_or_installation_scene_visible": False,
        }
    )

    validation = _validate_home(response)

    assert validation["manual_required"] is False
    assert validation["manual_reason_codes"] == []
    assert validation["structure_anomaly"] is False
    assert validation["effective_unboxing_photo_ok"] is True
    assert validation["local_corrections"] == [
        "DROP_NON_UNBOXING_IMAGE_EVIDENCE_IDS",
        "REMOVE_UNBOXING_PHOTO_INVALID_PER_IMAGE_PACKAGED_EVIDENCE",
    ]


def test_home_unpacked_installation_drops_extra_non_unboxing_image_id():
    response = _complete_candidate_response("home_appliance")
    response["unboxing_photo_ok"] = False
    response["manual_reason_codes"] = ["UNBOXING_PHOTO_INVALID"]
    response["unboxing_image_evidence"] = [
        {
            "image_id": "img_002",
            "product_visible": True,
            "package_visible": False,
            "home_or_installation_scene_visible": True,
        },
        {
            "image_id": "img_003",
            "product_visible": False,
            "package_visible": False,
            "home_or_installation_scene_visible": False,
        },
    ]

    validation = _validate_home(response)

    assert validation["manual_required"] is False
    assert validation["structure_anomaly"] is False
    assert validation["effective_unboxing_photo_ok"] is True
    assert validation["local_corrections"] == [
        "DROP_NON_UNBOXING_IMAGE_EVIDENCE_IDS",
        "REMOVE_UNBOXING_PHOTO_INVALID_PER_IMAGE_UNPACKAGED_HOME_EVIDENCE",
    ]


def test_home_extra_image_id_does_not_hide_missing_expected_unboxing_image():
    response = _complete_candidate_response("home_appliance")
    response["unboxing_image_evidence"] = [
        {
            "image_id": "img_002",
            "product_visible": True,
            "package_visible": True,
            "home_or_installation_scene_visible": False,
        },
        {
            "image_id": "img_003",
            "product_visible": False,
            "package_visible": True,
            "home_or_installation_scene_visible": False,
        },
    ]

    validation = _validate_home(response, "img_002", "img_004")

    assert validation["manual_required"] is True
    assert validation["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert validation["structure_anomaly"] is True
    assert "unboxing_image_evidence.image_id" in validation["invalid_model_fields"]


def test_home_per_image_packaged_evidence_removes_only_unboxing_reason():
    response = _complete_candidate_response("home_appliance")
    response.update(
        {
            "product_type_match": "mismatch",
            "unboxing_photo_ok": False,
            "manual_reason_codes": [
                "PRODUCT_TYPE_MISMATCH",
                "UNBOXING_PHOTO_INVALID",
            ],
        }
    )

    validation = _validate_home(response)

    assert validation["manual_required"] is True
    assert validation["manual_reason_codes"] == ["PRODUCT_TYPE_MISMATCH"]
    assert validation["effective_unboxing_photo_ok"] is True
    assert validation["structure_anomaly"] is False


def test_home_invalid_mechanical_evidence_overrides_model_pass():
    response = _complete_candidate_response("home_appliance")
    response["unboxing_image_evidence"] = [
        {
            "image_id": "img_002",
            "product_visible": False,
            "package_visible": True,
            "home_or_installation_scene_visible": False,
        }
    ]

    validation = _validate_home(response)

    assert validation["manual_required"] is True
    assert validation["manual_reason_codes"] == ["UNBOXING_PHOTO_INVALID"]
    assert validation["effective_unboxing_photo_ok"] is False
    assert validation["structure_anomaly"] is False
    assert validation["local_corrections"] == [
        "ADD_UNBOXING_PHOTO_INVALID_PER_IMAGE_EVIDENCE"
    ]


@pytest.mark.parametrize(
    ("evidence", "expected_invalid_field"),
    [
        ([], "unboxing_image_evidence.image_id"),
        (
            [
                {
                    "image_id": "img_002",
                    "product_visible": True,
                    "package_visible": True,
                    "home_or_installation_scene_visible": False,
                },
                {
                    "image_id": "img_002",
                    "product_visible": True,
                    "package_visible": True,
                    "home_or_installation_scene_visible": False,
                },
            ],
            "unboxing_image_evidence.image_id",
        ),
        (
            [
                {
                    "image_id": "img_003",
                    "product_visible": True,
                    "package_visible": True,
                    "home_or_installation_scene_visible": False,
                }
            ],
            "unboxing_image_evidence.image_id",
        ),
        (
            [
                {
                    "image_id": "img_002",
                    "product_visible": 1,
                    "package_visible": True,
                    "home_or_installation_scene_visible": False,
                }
            ],
            "unboxing_image_evidence[0].product_visible",
        ),
    ],
)
def test_home_per_image_evidence_id_and_type_anomalies_fail_closed(
    evidence, expected_invalid_field
):
    response = _complete_candidate_response("home_appliance")
    response["unboxing_image_evidence"] = evidence

    validation = _validate_home(response)

    assert validation["manual_required"] is True
    assert validation["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert validation["structure_anomaly"] is True
    assert expected_invalid_field in validation["invalid_model_fields"]


def test_home_per_image_evidence_requires_every_unboxing_image_exactly_once():
    response = _complete_candidate_response("home_appliance")

    validation = _validate_home(response, "img_002", "img_004")

    assert validation["manual_required"] is True
    assert validation["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert validation["structure_anomaly"] is True
    assert "unboxing_image_evidence.image_id" in validation["invalid_model_fields"]


def test_home_expected_unboxing_image_id_type_error_fails_closed():
    response = _complete_candidate_response("home_appliance")
    response["unboxing_image_evidence"][0]["image_id"] = "2"

    validation = candidate.validate_candidate_response(
        "home_appliance",
        "[A02] 电冰箱",
        response,
        unboxing_image_ids=(2,),
    )

    assert validation["manual_required"] is True
    assert validation["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert validation["structure_anomaly"] is True
    assert "$input.unboxing_image_ids" in validation["invalid_model_fields"]


def test_xiaodu_fixed_base_device_declared_as_tablet_keeps_product_mismatch():
    response = _complete_candidate_response(
        "ordinary_3c", activation_evidence_type="NONE"
    )
    response.update(
        {
            "manual_reason_codes": ["PRODUCT_TYPE_MISMATCH"],
            "product_type_match": "mismatch",
            "activation_photo_ok": False,
            "evidence_summary": "可见带固定底座的小度智能中控屏",
        }
    )

    validation = candidate.validate_candidate_response(
        "ordinary_3c", "[B02] 平板", response
    )

    assert validation["manual_required"] is True
    assert validation["manual_reason_codes"] == ["PRODUCT_TYPE_MISMATCH"]
    assert validation["structure_anomaly"] is False


def test_existing_reason_code_remains_manual_without_extra_subtype_adjudication():
    response = _complete_candidate_response(
        "ordinary_3c", activation_evidence_type="SMART_GLASSES_PACKAGE"
    )
    response["manual_reason_codes"] = ["ACTIVATION_PHOTO_INVALID"]

    validation = candidate.validate_candidate_response(
        "ordinary_3c", "[B01] 手机", response
    )

    assert validation["manual_required"] is True
    assert validation["manual_reason_codes"] == ["ACTIVATION_PHOTO_INVALID"]
    assert validation["structure_anomaly"] is False


@pytest.mark.parametrize(
    ("category", "product_type"),
    [
        ("ordinary_3c", "[B99] 手机壳"),
        ("ordinary_3c", "headphone"),
        ("ordinary_3c", "[B02] 手机"),
        ("computer", "[C99] 笔记本电脑包"),
        ("computer", "notebook stand"),
        ("home_appliance", "烘干机"),
    ],
)
def test_accessory_near_match_or_conflicting_type_is_not_a_supported_subtype(
    category, product_type
):
    assert candidate.product_subtype_for_category(category, product_type) == ""
