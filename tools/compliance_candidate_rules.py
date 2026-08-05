# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import math
import re
from typing import Any


CANDIDATE_VERSION = "compliance-candidate-v6-20260804"
CANDIDATE_STAGE = "compliance_candidate_v6"

PROMPTS = {
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

PROMPT_SHA256 = {
    "home_appliance": "46e99b3bf04b0ce410c9d62f26eb753d9e846468e2e0c6081bad539b4eb8a190",
    "ordinary_3c": "47c2150b1678e6945130f3aeb13158c537e2e88120b839dc13c981ba292e5291",
    "computer": "a9f7cc695387882e98a9221916aa8ab033d4219ef6bb47b46a82dfd2d527ed9b",
}

_COMMON_REQUIRED_FIELDS = {
    "manual_reason_codes": list,
    "product_type_match": str,
    "product_photo_ok": bool,
    "unboxing_photo_ok": bool,
    "duplicate_image_evidence": bool,
    "evidence_summary": str,
}

_CATEGORY_REQUIRED_FIELDS = {
    "home_appliance": {
        **_COMMON_REQUIRED_FIELDS,
        "unboxing_image_evidence": list,
        "confidence": (int, float),
    },
    "ordinary_3c": {
        **_COMMON_REQUIRED_FIELDS,
        "activation_photo_ok": bool,
        "activation_evidence_type": str,
    },
    "computer": {
        **_COMMON_REQUIRED_FIELDS,
        "activation_photo_ok": bool,
        "activation_evidence_type": str,
    },
}

_PRODUCT_TYPE_MATCH_VALUES = {"match", "mismatch", "unknown"}

_ACTIVATION_EVIDENCE_TYPES = {
    "ordinary_3c": {
        "SCREEN_SN",
        "PHONE_IDENTITY_ONLY",
        "SMART_GLASSES_PACKAGE",
        "PAIRING_OR_SETUP",
        "SCREEN_ON_NO_IDENTITY",
        "UNCLEAR",
        "NONE",
    },
    "computer": {
        "LAPTOP_SCREEN_SN_WITH_PACKAGE",
        "DESKTOP_BODY_SN_WITH_PACKAGE",
        "SCREEN_SN_ONLY",
        "PACKAGE_SN_ONLY",
        "UNCLEAR",
        "NONE",
    },
}

_ALLOWED_REASON_CODES = {
    "home_appliance": {
        "DUPLICATE_IMAGE_EVIDENCE",
        "PRODUCT_TYPE_MISMATCH",
        "PRODUCT_PHOTO_INVALID",
        "UNBOXING_PHOTO_INVALID",
        "MODEL_UNCERTAIN",
    },
    "ordinary_3c": {
        "DUPLICATE_IMAGE_EVIDENCE",
        "PRODUCT_TYPE_MISMATCH",
        "PRODUCT_PHOTO_INVALID",
        "UNBOXING_PHOTO_INVALID",
        "ACTIVATION_PHOTO_INVALID",
        "MODEL_UNCERTAIN",
    },
    "computer": {
        "DUPLICATE_IMAGE_EVIDENCE",
        "PRODUCT_TYPE_MISMATCH",
        "PRODUCT_PHOTO_INVALID",
        "UNBOXING_PHOTO_INVALID",
        "ACTIVATION_PHOTO_INVALID",
        "MODEL_UNCERTAIN",
    },
}

_EVIDENCE_BY_PRODUCT_SUBTYPE = {
    "phone": {"SCREEN_SN", "PHONE_IDENTITY_ONLY"},
    "tablet": {"SCREEN_SN"},
    "watch_or_band": {"SCREEN_SN"},
    "smart_glasses": {"SMART_GLASSES_PACKAGE"},
    "laptop": {"LAPTOP_SCREEN_SN_WITH_PACKAGE"},
    "all_in_one": {"LAPTOP_SCREEN_SN_WITH_PACKAGE"},
    "desktop": {"DESKTOP_BODY_SN_WITH_PACKAGE"},
    "generic_computer": {
        "LAPTOP_SCREEN_SN_WITH_PACKAGE",
        "DESKTOP_BODY_SN_WITH_PACKAGE",
    },
}

_PRODUCT_NAMES_BY_SUBTYPE = {
    "home_appliance": {
        "refrigerator": {"冰箱", "电冰箱"},
        "television": {"电视", "电视机"},
        "air_conditioner": {"空调"},
        "water_heater": {"热水器", "电热水器"},
        "washing_machine": {"洗衣机"},
    },
    "ordinary_3c": {
        "phone": {"手机", "智能手机", "phone", "smartphone", "mobile phone"},
        "tablet": {"平板", "tablet"},
        "watch_or_band": {
            "智能手表手环",
            "智能手表",
            "智能手环",
            "手表",
            "手环",
            "smart watch",
            "smartwatch",
            "smart band",
        },
        "smart_glasses": {"智能眼镜", "smart glasses", "smartglasses"},
    },
    "computer": {
        "generic_computer": {"电脑", "computer"},
        "laptop": {"笔记本", "笔记本电脑", "laptop", "notebook"},
        "all_in_one": {"一体机", "一体电脑", "all-in-one", "all in one"},
        "desktop": {
            "台式机",
            "台式电脑",
            "desktop",
            "desktop computer",
            "desktop pc",
        },
    },
}

_ORDINARY_3C_CODE_SUBTYPES = {
    "B01": "phone",
    "B02": "tablet",
    "B03": "watch_or_band",
    "B04": "smart_glasses",
}

_PRODUCT_CODE_PREFIX = re.compile(r"^\[([^\]]+)\]\s*(.*)$")


def _normalized_category(category: str) -> str:
    normalized = str(category or "").strip().lower()
    if normalized not in PROMPTS:
        raise ValueError(f"unsupported candidate category: {category}")
    return normalized


def _append_once(values: list[str], field: str) -> None:
    if field not in values:
        values.append(field)


_HOME_UNBOXING_EVIDENCE_FIELDS = {
    "image_id": str,
    "product_visible": bool,
    "package_visible": bool,
    "home_or_installation_scene_visible": bool,
}


def _validate_home_unboxing_evidence(
    value: Any,
    expected_image_ids: tuple[Any, ...],
    missing_fields: list[str],
    invalid_fields: list[str],
    local_corrections: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    expected_input_valid = bool(expected_image_ids) and all(
        isinstance(image_id, str) and bool(image_id.strip())
        for image_id in expected_image_ids
    )
    normalized_expected_image_ids = (
        tuple(image_id.strip() for image_id in expected_image_ids)
        if expected_input_valid
        else ()
    )
    if (
        not expected_input_valid
        or len(set(normalized_expected_image_ids))
        != len(normalized_expected_image_ids)
    ):
        _append_once(invalid_fields, "$input.unboxing_image_ids")

    expected_image_id_set = set(normalized_expected_image_ids)
    validated: list[dict[str, Any]] = []
    seen_ids: list[str] = []
    dropped_extra_ids = False
    missing_count_before = len(missing_fields)
    invalid_count_before = len(invalid_fields)
    for index, item in enumerate(value):
        item_path = f"unboxing_image_evidence[{index}]"
        if not isinstance(item, dict):
            _append_once(invalid_fields, item_path)
            continue
        raw_image_id = item.get("image_id")
        image_id = raw_image_id.strip() if isinstance(raw_image_id, str) else ""
        if image_id and expected_image_id_set and image_id not in expected_image_id_set:
            dropped_extra_ids = True
            continue
        item_valid = True
        for field, expected_type in _HOME_UNBOXING_EVIDENCE_FIELDS.items():
            field_path = f"{item_path}.{field}"
            if field not in item:
                _append_once(missing_fields, field_path)
                item_valid = False
                continue
            field_value = item[field]
            if not isinstance(field_value, expected_type):
                _append_once(invalid_fields, field_path)
                item_valid = False
        if image_id:
            seen_ids.append(image_id)
        if item_valid:
            validated.append(
                {
                    "image_id": image_id,
                    "product_visible": item["product_visible"],
                    "package_visible": item["package_visible"],
                    "home_or_installation_scene_visible": item[
                        "home_or_installation_scene_visible"
                    ],
                }
            )

    coverage_valid = (
        expected_input_valid
        and len(seen_ids) == len(set(seen_ids))
        and len(seen_ids) == len(normalized_expected_image_ids)
        and set(seen_ids) == expected_image_id_set
    )
    if not coverage_valid:
        _append_once(invalid_fields, "unboxing_image_evidence.image_id")
    if (
        dropped_extra_ids
        and coverage_valid
        and len(missing_fields) == missing_count_before
        and len(invalid_fields) == invalid_count_before
    ):
        local_corrections.append("DROP_NON_UNBOXING_IMAGE_EVIDENCE_IDS")
    return validated


def product_subtype_for_category(category: str, product_type: str) -> str:
    normalized = _normalized_category(category)
    text = " ".join(str(product_type or "").strip().lower().split())
    code = ""
    match = _PRODUCT_CODE_PREFIX.fullmatch(text)
    if match:
        code = match.group(1).strip().upper()
        text = " ".join(match.group(2).strip().split())

    matches = {
        subtype
        for subtype, names in _PRODUCT_NAMES_BY_SUBTYPE[normalized].items()
        if text in names
    }
    if len(matches) != 1:
        return ""
    subtype = next(iter(matches))
    expected_subtype = (
        _ORDINARY_3C_CODE_SUBTYPES.get(code)
        if normalized == "ordinary_3c"
        else None
    )
    if expected_subtype and expected_subtype != subtype:
        return ""
    return subtype


def validate_candidate_response(
    category: str,
    product_type: str,
    response: Any,
    *,
    unboxing_image_ids: tuple[Any, ...] | list[Any] | None = None,
) -> dict[str, Any]:
    normalized = _normalized_category(category)
    if not isinstance(response, dict):
        return {
            "manual_required": True,
            "manual_reason_codes": ["MODEL_UNCERTAIN"],
            "effective_unboxing_photo_ok": "",
            "package_visible": "",
            "whole_product_visible": "",
            "product_and_package_same_image": "",
            "home_or_installation_scene_visible": "",
            "local_corrections": [],
            "structure_anomaly": True,
            "missing_model_fields": [],
            "invalid_model_fields": ["$"],
        }

    required_fields = _CATEGORY_REQUIRED_FIELDS[normalized]
    missing_fields = [field for field in required_fields if field not in response]
    invalid_fields: list[str] = []

    for field, expected_type in required_fields.items():
        if field not in response:
            continue
        value = response[field]
        if field == "confidence":
            try:
                valid_type = (
                    isinstance(value, expected_type)
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                )
            except (OverflowError, ValueError):
                valid_type = False
        else:
            valid_type = isinstance(value, expected_type)
        if not valid_type:
            _append_once(invalid_fields, field)

    local_corrections: list[str] = []
    home_unboxing_evidence: list[dict[str, Any]] = []
    if normalized == "home_appliance" and "unboxing_image_evidence" in response:
        home_unboxing_evidence = _validate_home_unboxing_evidence(
            response["unboxing_image_evidence"],
            tuple(unboxing_image_ids or ()),
            missing_fields,
            invalid_fields,
            local_corrections,
        )

    reason_codes = response.get("manual_reason_codes")
    if isinstance(reason_codes, list):
        if any(
            not isinstance(code, str)
            or code not in _ALLOWED_REASON_CODES[normalized]
            for code in reason_codes
        ):
            _append_once(invalid_fields, "manual_reason_codes")
    else:
        reason_codes = []

    product_type_match = response.get("product_type_match")
    if isinstance(product_type_match, str) and product_type_match not in _PRODUCT_TYPE_MATCH_VALUES:
        _append_once(invalid_fields, "product_type_match")

    if normalized in _ACTIVATION_EVIDENCE_TYPES:
        activation_type = response.get("activation_evidence_type")
        if (
            isinstance(activation_type, str)
            and activation_type not in _ACTIVATION_EVIDENCE_TYPES[normalized]
        ):
            _append_once(invalid_fields, "activation_evidence_type")

    structure_anomaly = bool(missing_fields or invalid_fields)
    effective_codes = list(reason_codes)
    effective_unboxing_photo_ok = response.get("unboxing_photo_ok")
    package_visible: bool | str = ""
    whole_product_visible: bool | str = ""
    product_and_package_same_image: bool | str = ""
    home_or_installation_scene_visible: bool | str = ""

    if not structure_anomaly and normalized == "home_appliance":
        package_visible = any(item["package_visible"] for item in home_unboxing_evidence)
        whole_product_visible = any(
            item["product_visible"] for item in home_unboxing_evidence
        )
        product_and_package_same_image = any(
            item["product_visible"] and item["package_visible"]
            for item in home_unboxing_evidence
        )
        home_or_installation_scene_visible = any(
            item["home_or_installation_scene_visible"]
            for item in home_unboxing_evidence
        )
        packaged_evidence_ok = bool(product_and_package_same_image)
        unpackaged_evidence_ok = bool(
            not package_visible
            and any(
                item["product_visible"]
                and item["home_or_installation_scene_visible"]
                for item in home_unboxing_evidence
            )
        )
        effective_unboxing_photo_ok = packaged_evidence_ok or unpackaged_evidence_ok
        if effective_unboxing_photo_ok:
            if "UNBOXING_PHOTO_INVALID" in effective_codes:
                effective_codes = [
                    code
                    for code in effective_codes
                    if code != "UNBOXING_PHOTO_INVALID"
                ]
                local_corrections.append(
                    "REMOVE_UNBOXING_PHOTO_INVALID_PER_IMAGE_PACKAGED_EVIDENCE"
                    if packaged_evidence_ok
                    else "REMOVE_UNBOXING_PHOTO_INVALID_PER_IMAGE_UNPACKAGED_HOME_EVIDENCE"
                )
            elif response["unboxing_photo_ok"] is not True:
                local_corrections.append("SET_UNBOXING_PHOTO_OK_PER_IMAGE_EVIDENCE")
        elif "UNBOXING_PHOTO_INVALID" not in effective_codes:
            effective_codes.append("UNBOXING_PHOTO_INVALID")
            local_corrections.append("ADD_UNBOXING_PHOTO_INVALID_PER_IMAGE_EVIDENCE")

    if not missing_fields and not invalid_fields and not effective_codes:
        subtype = product_subtype_for_category(normalized, product_type)
        if not subtype:
            _append_once(invalid_fields, "product_type")
        if response["product_type_match"] != "match":
            _append_once(invalid_fields, "product_type_match")
        if response["product_photo_ok"] is not True:
            _append_once(invalid_fields, "product_photo_ok")
        if effective_unboxing_photo_ok is not True:
            _append_once(invalid_fields, "unboxing_photo_ok")
        if response["duplicate_image_evidence"] is not False:
            _append_once(invalid_fields, "duplicate_image_evidence")
        if normalized in _ACTIVATION_EVIDENCE_TYPES:
            if response["activation_photo_ok"] is not True:
                _append_once(invalid_fields, "activation_photo_ok")
            allowed_evidence = _EVIDENCE_BY_PRODUCT_SUBTYPE.get(subtype, set())
            if response["activation_evidence_type"] not in allowed_evidence:
                _append_once(invalid_fields, "activation_evidence_type")

    structure_anomaly = bool(missing_fields or invalid_fields)
    if structure_anomaly:
        effective_codes = ["MODEL_UNCERTAIN"]
    return {
        "manual_required": bool(effective_codes),
        "manual_reason_codes": effective_codes,
        "effective_unboxing_photo_ok": effective_unboxing_photo_ok,
        "package_visible": package_visible,
        "whole_product_visible": whole_product_visible,
        "product_and_package_same_image": product_and_package_same_image,
        "home_or_installation_scene_visible": home_or_installation_scene_visible,
        "local_corrections": local_corrections,
        "structure_anomaly": structure_anomaly,
        "missing_model_fields": missing_fields,
        "invalid_model_fields": invalid_fields,
    }


def prompt_for_category(category: str) -> str:
    normalized = _normalized_category(category)
    prompt = PROMPTS[normalized]
    if "\r" in prompt or prompt.startswith("\ufeff"):
        raise RuntimeError(f"candidate prompt is not UTF-8/LF normalized: {normalized}")
    actual = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    expected = PROMPT_SHA256[normalized]
    if actual != expected:
        raise RuntimeError(
            f"candidate prompt SHA-256 mismatch: {normalized}; "
            f"expected={expected}; actual={actual}"
        )
    return prompt
