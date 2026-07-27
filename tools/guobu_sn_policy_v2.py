# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Iterable


class SnCategory(str, Enum):
    HOME_APPLIANCE = "HOME_APPLIANCE"
    PHONE = "PHONE"
    TABLET = "TABLET"
    WATCH = "WATCH"
    COMPUTER = "COMPUTER"
    UNSUPPORTED = "UNSUPPORTED"


SCHEMA_VERSION = "guobu_sn_evidence_v2"
SCREEN_SOURCES = {"DEVICE_SCREEN", "SCREEN"}
PACKAGE_SOURCES = {"PACKAGE_LABEL"}
HOME_SOURCES = {"DEVICE_BODY", "PACKAGE_LABEL"}
IDENTITY_TYPES = {"IMEI", "IMEI1", "IMEI2", "MEID", "EID"}
SCREEN_IDENTITY_STATES = {
    SnCategory.HOME_APPLIANCE: {"NOT_APPLICABLE"},
    SnCategory.PHONE: {
        "SCREEN_SN_READABLE",
        "SCREEN_SN_UNREADABLE",
        "PHONE_IDENTITY_ONLY",
        "NO_SCREEN_SN",
        "SCREEN_SN_CONFLICT",
    },
    SnCategory.TABLET: {
        "SCREEN_SN_READABLE",
        "SCREEN_SN_UNREADABLE",
        "NO_SCREEN_SN",
        "SCREEN_SN_CONFLICT",
    },
    SnCategory.WATCH: {
        "SCREEN_SN_READABLE",
        "SCREEN_SN_UNREADABLE",
        "NO_SCREEN_SN",
        "SCREEN_SN_CONFLICT",
    },
    SnCategory.COMPUTER: {
        "SCREEN_SN_READABLE",
        "SCREEN_SN_UNREADABLE",
        "NO_SCREEN_SN",
        "SCREEN_SN_CONFLICT",
    },
    SnCategory.UNSUPPORTED: {"NOT_APPLICABLE", "NO_SCREEN_SN"},
}


_PRIMARY_CATEGORY_FIELDS = (
    "category_name",
    "cate_code_name",
    "product_type",
    "type",
)
_UNSUPPORTED_DIGITAL_KEYWORDS = (
    "相机",
    "照相机",
    "耳机",
    "耳麦",
    "camera",
    "headphone",
    "headset",
    "earphone",
    "earbud",
)


COMMON_PROMPT = """你是国补审核的SN高精度证据读取员。只输出严格JSON对象。

商品品类已经由本地程序确定，模型不得自行分类或修改品类。你只能执行当前提示词中唯一出现的品类规则，不得借用其他品类规则。
只能查看“SN码采集 / 激活照片 / 序列号照片”分组，不得使用商品照片或拆封照片中的号码。
模型只负责如实读取证据，不负责与系统SN比较，也不得推断最终是否一致。

只有以下标签可绑定SN：SN、S/N、SN码、序列号、产品序列号、Serial No.、Serial Number、Serial#。
出厂编号、主机编号、机器编号、整机编号、设备编号、型号、Model、产品编号、批次号、EAN、普通条码及无明确SN标签的数字均不得作为SN。

每个SN候选必须分别输出image_id、source、field_type、label_text、raw_text、raw_context、normalized_text、label_binding、readable、complete、confidence、visual_ambiguity_notes。
source只能是DEVICE_SCREEN、DEVICE_BODY或PACKAGE_LABEL。label_binding只能是EXPLICIT、AMBIGUOUS或NONE。
raw_text只填写图片中SN标签绑定的号码原文，不得包含标签文字。normalized_text仅供诊断，不得增加、删除或替换任何字母数字。
必须放大后逐字读取。O/0/Q/D、I/1/L、S/5、E/B/8、Y/V、6/G、J/U/L、W/V/N无法确定时，不得猜测或生成多个替代号码；应保留证据项并设置readable=false或complete=false，在visual_ambiguity_notes中记录不确定位置。
visual_ambiguity_notes必须是JSON字符串数组；没有歧义时必须且只能输出[]，不得填写“无”“none”或其他文字。

sn_readable=true仅当sn_candidates中至少存在一个标签明确、完整且可读的SN证据；否则必须为false。它不表示该SN与系统SN一致。
confidence沿用现有输出方式，本提示词不新增评分规则。
"""


CATEGORY_PROMPTS = {
    SnCategory.HOME_APPLIANCE: """RULE_HOME_APPLIANCE
当前SN品类为HOME_APPLIANCE。只执行本段规则。
家电不要求亮屏。读取机身铭牌和包装标签上明确绑定SN标签的全部候选。屏幕号码不作为家电SN通过证据。
多个明确SN必须全部如实输出，不得自行选择最终匹配项。
identity_evidence必须输出空数组，screen_identity_state必须输出NOT_APPLICABLE。
""",
    SnCategory.PHONE: """RULE_PHONE
当前SN品类为PHONE。只执行本段规则。
读取手机屏幕、包装和机身中的全部明确SN证据，并单独记录屏幕中的IMEI、IMEI1、IMEI2、MEID、EID身份字段。
identity_evidence只记录上述明确标注且完整可读的身份字段；它们永远不是SN候选。
每个identity_evidence项必须输出image_id、source、field_type、label_text、raw_text、label_binding、readable、complete；source必须是DEVICE_SCREEN，label_binding必须是EXPLICIT，label_text必须与field_type明确对应。
IMEI、IMEI1、IMEI2必须是15位数字；MEID必须是14位十六进制字符或18位数字；EID必须是32位数字。
screen_identity_state含义：SCREEN_SN_READABLE表示存在一个唯一的清晰完整屏幕SN；SCREEN_SN_UNREADABLE表示屏幕SN标签存在但号码模糊、遮挡或不完整，且没有可读屏幕SN；PHONE_IDENTITY_ONLY表示没有屏幕SN证据，但存在明确标注且完整可读的手机身份字段；NO_SCREEN_SN表示屏幕既无SN证据也无有效手机身份字段；SCREEN_SN_CONFLICT表示存在两个或以上不同的清晰完整屏幕SN。
清晰完整屏幕SN是最高优先证据。屏幕没有SN、屏幕SN不可读或屏幕只有身份字段时，仍须如实读取包装SN，最终是否允许使用包装由本地程序决定。
""",
    SnCategory.TABLET: """RULE_TABLET
当前SN品类为TABLET。只执行本段规则。
读取平板屏幕、包装和机身中的全部明确SN证据。屏幕中的IMEI、MEID、EID只能记录为诊断身份字段，不能伪装为SN。
identity_evidence只记录上述身份字段。
每个identity_evidence项必须输出image_id、source、field_type、label_text、raw_text、label_binding、readable、complete；source必须是DEVICE_SCREEN，label_binding必须是EXPLICIT，label_text必须与field_type明确对应。
清晰完整屏幕SN是最高优先证据。屏幕SN字段存在但不可读时，仍须如实读取包装SN，最终是否允许使用包装由本地程序决定。
""",
    SnCategory.WATCH: """RULE_WATCH
当前SN品类为WATCH，智能手表和智能手环均执行本段规则。
读取设备屏幕、包装和机身中的全部明确SN证据。清晰完整屏幕SN是最高优先证据。
屏幕SN字段存在但不可读时，仍须如实读取包装SN，最终是否允许使用包装由本地程序决定。
identity_evidence必须输出空数组。
""",
    SnCategory.COMPUTER: """RULE_COMPUTER
当前SN品类为COMPUTER。只执行本段规则。
读取电脑BIOS、系统信息页或设备信息页中的屏幕SN，并如实记录包装和机身候选。
只有清晰完整的屏幕或系统页面SN可以成为权威SN；包装和机身候选只作诊断，最终裁决由本地程序完成。
identity_evidence必须输出空数组。
""",
    SnCategory.UNSUPPORTED: """RULE_UNSUPPORTED
当前商品不属于已配置的SN自动审核品类。不得套用任何其他品类规则，只输出当前照片中的原始证据。
""",
}


def _classify_ordinary_3c_subtype(fields: dict[str, Any]) -> SnCategory:
    text = ""
    for key in _PRIMARY_CATEGORY_FIELDS:
        value = str(fields.get(key) or "").strip()
        if value:
            text = value.lower()
            break
    if not text:
        return SnCategory.UNSUPPORTED
    if any(keyword in text for keyword in ("智能眼镜", "smart glasses", "smart glass")):
        return SnCategory.HOME_APPLIANCE
    if any(keyword in text for keyword in _UNSUPPORTED_DIGITAL_KEYWORDS):
        return SnCategory.UNSUPPORTED
    if any(keyword in text for keyword in ("平板电脑", "平板", "tablet")):
        return SnCategory.TABLET
    if any(
        keyword in text
        for keyword in (
            "智能手表手环",
            "智能手表",
            "智能手环",
            "手表",
            "手环",
            "smartwatch",
            "watch",
            "wristband",
        )
    ):
        return SnCategory.WATCH
    if any(keyword in text for keyword in ("手机", "智能手机", "smartphone", "phone")):
        return SnCategory.PHONE
    return SnCategory.UNSUPPORTED


def classify_sn_category(
    fields: dict[str, Any] | None,
    *,
    effective_category: str | None = None,
) -> SnCategory:
    values = fields or {}
    mainline_category = str(effective_category or "").strip().lower()
    if mainline_category == "home_appliance":
        return SnCategory.HOME_APPLIANCE
    if mainline_category == "computer":
        return SnCategory.COMPUTER
    if mainline_category == "ordinary_3c":
        return _classify_ordinary_3c_subtype(values)
    return SnCategory.UNSUPPORTED


def _coerce_category(value: SnCategory | str) -> SnCategory:
    if isinstance(value, SnCategory):
        return value
    return SnCategory(str(value).strip().upper())


def build_sn_prompt(category: SnCategory | str) -> str:
    selected = _coerce_category(category)
    allowed_states = "、".join(sorted(SCREEN_IDENTITY_STATES[selected]))
    example_state = "NOT_APPLICABLE" if selected in {SnCategory.HOME_APPLIANCE, SnCategory.UNSUPPORTED} else "NO_SCREEN_SN"
    schema_prompt = f"""screen_identity_state只能从以下值中选择：{allowed_states}。
以下是合法JSON结构示例；字段值必须根据图片证据填写，不得机械照抄示例：
{{
  "schema_version": "{SCHEMA_VERSION}",
  "sn_readable": false,
  "screen_identity_state": "{example_state}",
  "sn_candidates": [],
  "identity_evidence": [],
  "confidence": 0.0
}}"""
    return "\n\n".join((COMMON_PROMPT.strip(), CATEGORY_PROMPTS[selected].strip(), schema_prompt.strip()))


def build_model_payload(
    task: dict[str, Any],
    category: SnCategory | str,
    activation_images: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    fields = task.get("fields") or {}
    selected = _coerce_category(category)
    return {
        "id": str(task.get("channel_order_no") or task.get("task_id") or ""),
        "audit_category": selected.value,
        "product_type": str(fields.get("product_type") or ""),
        "category_name": str(fields.get("category_name") or fields.get("cate_code_name") or ""),
        "goods_name": str(fields.get("goods_name") or fields.get("product_name") or ""),
        "activation_images": [
            {
                "image_id": str(image.get("image_id") or ""),
                "title": str(image.get("title") or ""),
                "url": str(image.get("source_url") or ""),
            }
            for image in activation_images
        ],
    }


_LABEL_PATTERNS = (
    re.compile(r"^\s*S\s*/\s*N\s*[:：]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*SN\s*(?:码)?\s*[:：]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:产品)?序列号\s*[:：]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*SERIAL\s*(?:NO\.?|NUMBER|#)\s*[:：]?\s*$", re.IGNORECASE),
)


def canonical_sn(value: Any) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


def _approved_label(value: Any) -> bool:
    return any(pattern.fullmatch(str(value or "")) for pattern in _LABEL_PATTERNS)


def _is_true(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def _ambiguity_present(candidate: dict[str, Any]) -> bool:
    notes = candidate.get("visual_ambiguity_notes")
    if isinstance(notes, list):
        return any(str(item or "").strip() for item in notes)
    return bool(str(notes or "").strip())


def _explicit_candidate(candidate: Any) -> bool:
    return (
        isinstance(candidate, dict)
        and str(candidate.get("field_type") or "").strip().upper() in {"SN", "SERIAL"}
        and str(candidate.get("label_binding") or "").strip().upper() == "EXPLICIT"
        and _approved_label(candidate.get("label_text"))
        and str(candidate.get("source") or "").strip().upper()
        in {"DEVICE_SCREEN", "SCREEN", "DEVICE_BODY", "PACKAGE_LABEL"}
    )


def _usable_candidate(candidate: Any) -> bool:
    return (
        _explicit_candidate(candidate)
        and _is_true(candidate.get("readable"))
        and _is_true(candidate.get("complete"))
        and not _ambiguity_present(candidate)
        and bool(canonical_sn(candidate.get("raw_text")))
    )


def _candidate_source(candidate: dict[str, Any]) -> str:
    source = str(candidate.get("source") or "").strip().upper()
    return "DEVICE_SCREEN" if source == "SCREEN" else source


def _candidates(evidence: dict[str, Any], sources: set[str], *, usable: bool) -> list[dict[str, Any]]:
    predicate = _usable_candidate if usable else _explicit_candidate
    return [
        candidate
        for candidate in evidence.get("sn_candidates", [])
        if predicate(candidate) and _candidate_source(candidate) in sources
    ]


def _valid_identities(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for item in evidence.get("identity_evidence", []):
        if not isinstance(item, dict):
            continue
        field_type = re.sub(r"[ _]", "", str(item.get("field_type") or "").upper())
        label_text = re.sub(r"[^0-9A-Z]", "", str(item.get("label_text") or "").upper())
        raw_text = item.get("raw_text")
        canonical_value = canonical_sn(raw_text) if isinstance(raw_text, str) else ""
        value_is_complete = (
            bool(re.fullmatch(r"[0-9]{15}", canonical_value))
            if field_type in {"IMEI", "IMEI1", "IMEI2"}
            else bool(re.fullmatch(r"(?:[0-9A-F]{14}|[0-9]{18})", canonical_value))
            if field_type == "MEID"
            else bool(re.fullmatch(r"[0-9]{32}", canonical_value))
            if field_type == "EID"
            else False
        )
        if (
            field_type in IDENTITY_TYPES
            and label_text == field_type
            and str(item.get("source") or "").strip().upper() in SCREEN_SOURCES
            and str(item.get("label_binding") or "").strip().upper() == "EXPLICIT"
            and _is_true(item.get("readable"))
            and _is_true(item.get("complete"))
            and value_is_complete
        ):
            valid.append(item)
    return valid


def _base_decision(fields: dict[str, Any], category: SnCategory) -> dict[str, Any]:
    return {
        "audit_category": category.value,
        "manual_required": True,
        "manual_reason_code": "MODEL_UNCERTAIN",
        "manual_reason": "SN证据无法确认",
        "system_sn": str(fields.get("system_sn") or ""),
        "normalized_system_sn": canonical_sn(fields.get("system_sn")),
        "observed_sn": "",
        "normalized_observed_sn": "",
        "selected_source": "",
        "sn_match": False,
    }


def _manual(
    fields: dict[str, Any],
    category: SnCategory,
    code: str,
    reason: str,
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = _base_decision(fields, category)
    decision["manual_reason_code"] = code
    decision["manual_reason"] = reason
    if candidate:
        decision["observed_sn"] = str(candidate.get("raw_text") or "")
        decision["normalized_observed_sn"] = canonical_sn(candidate.get("raw_text"))
        decision["selected_source"] = _candidate_source(candidate)
    return decision


def _pass(fields: dict[str, Any], category: SnCategory, candidate: dict[str, Any]) -> dict[str, Any]:
    decision = _base_decision(fields, category)
    decision.update(
        {
            "manual_required": False,
            "manual_reason_code": "",
            "manual_reason": "",
            "observed_sn": str(candidate.get("raw_text") or ""),
            "normalized_observed_sn": canonical_sn(candidate.get("raw_text")),
            "selected_source": _candidate_source(candidate),
            "sn_match": True,
        }
    )
    return decision


def _compare_candidates(
    fields: dict[str, Any],
    category: SnCategory,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    system = canonical_sn(fields.get("system_sn"))
    matched = next(
        (candidate for candidate in candidates if canonical_sn(candidate.get("raw_text")) == system),
        None,
    )
    if matched is not None:
        return _pass(fields, category, matched)
    first = candidates[0]
    observed = str(first.get("raw_text") or "")
    return _manual(
        fields,
        category,
        "SN_MISMATCH",
        f"系统SN与照片SN不一致（系统：{fields.get('system_sn') or ''}，照片：{observed}）",
        first,
    )


def _schema_error(
    evidence: Any,
    category: SnCategory,
    allowed_image_ids: Iterable[str] | None = None,
) -> str:
    if not isinstance(evidence, dict):
        return "模型SN证据不是JSON对象"
    if evidence.get("schema_version") != SCHEMA_VERSION:
        return "模型SN证据版本不正确"
    if not isinstance(evidence.get("sn_readable"), bool):
        return "模型SN证据缺少sn_readable"
    if evidence.get("screen_identity_state") not in SCREEN_IDENTITY_STATES[category]:
        return "模型SN证据的屏幕状态无效"
    if not isinstance(evidence.get("sn_candidates"), list):
        return "模型SN候选结构无效"
    if not isinstance(evidence.get("identity_evidence"), list):
        return "模型身份字段结构无效"
    candidate_string_fields = {
        "image_id",
        "source",
        "field_type",
        "label_text",
        "raw_text",
        "raw_context",
        "normalized_text",
        "label_binding",
    }
    for candidate in evidence.get("sn_candidates", []):
        if not isinstance(candidate, dict):
            return "模型SN候选字段结构无效"
        if any(not isinstance(candidate.get(field), str) for field in candidate_string_fields):
            return "模型SN候选字段结构无效"
        if not isinstance(candidate.get("readable"), bool) or not isinstance(candidate.get("complete"), bool):
            return "模型SN候选字段结构无效"
        notes = candidate.get("visual_ambiguity_notes")
        if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
            return "模型SN候选字段结构无效"
        confidence = candidate.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return "模型SN候选字段结构无效"
    identity_string_fields = {
        "image_id",
        "source",
        "field_type",
        "label_text",
        "raw_text",
        "label_binding",
    }
    for identity in evidence.get("identity_evidence", []):
        if not isinstance(identity, dict):
            return "模型身份字段结构无效"
        if any(not isinstance(identity.get(field), str) for field in identity_string_fields):
            return "模型身份字段结构无效"
        if not isinstance(identity.get("readable"), bool) or not isinstance(identity.get("complete"), bool):
            return "模型身份字段结构无效"
    if allowed_image_ids is not None:
        allowed = {str(image_id) for image_id in allowed_image_ids if str(image_id)}
        for key in ("sn_candidates", "identity_evidence"):
            for item in evidence.get(key, []):
                if not isinstance(item, dict):
                    continue
                image_id = str(item.get("image_id") or "")
                if not image_id or image_id not in allowed:
                    return "模型SN证据引用了非本单激活照片"
    expected_sn_readable = any(_usable_candidate(item) for item in evidence.get("sn_candidates", []))
    if evidence.get("sn_readable") is not expected_sn_readable:
        return "模型SN可读状态与候选证据不一致"
    if category is SnCategory.HOME_APPLIANCE:
        expected_screen_state = "NOT_APPLICABLE"
    else:
        readable_screen = _candidates(evidence, {"DEVICE_SCREEN"}, usable=True)
        readable_values = {canonical_sn(item.get("raw_text")) for item in readable_screen}
        if len(readable_values) >= 2:
            expected_screen_state = "SCREEN_SN_CONFLICT"
        elif readable_values:
            expected_screen_state = "SCREEN_SN_READABLE"
        elif _candidates(evidence, {"DEVICE_SCREEN"}, usable=False):
            expected_screen_state = "SCREEN_SN_UNREADABLE"
        elif category is SnCategory.PHONE and _valid_identities(evidence):
            expected_screen_state = "PHONE_IDENTITY_ONLY"
        else:
            expected_screen_state = "NO_SCREEN_SN"
    if evidence.get("screen_identity_state") != expected_screen_state:
        return "模型屏幕状态与候选证据不一致"
    return ""


def decide_sn(
    fields: dict[str, Any] | None,
    evidence: dict[str, Any],
    *,
    allowed_image_ids: Iterable[str] | None = None,
    effective_category: str | None = None,
) -> dict[str, Any]:
    values = fields or {}
    category = classify_sn_category(values, effective_category=effective_category)
    if category is SnCategory.UNSUPPORTED:
        return _manual(values, category, "MODEL_UNCERTAIN", "该商品品类暂未配置SN自动审核规则")
    if not canonical_sn(values.get("system_sn")):
        return _manual(values, category, "SYSTEM_SN_MISSING", "系统SN缺失")

    schema_error = _schema_error(evidence, category, allowed_image_ids)
    if schema_error:
        return _manual(values, category, "MODEL_UNCERTAIN", schema_error)

    if category is SnCategory.HOME_APPLIANCE:
        usable = _candidates(evidence, HOME_SOURCES, usable=True)
        if usable:
            return _compare_candidates(values, category, usable)
        if _candidates(evidence, HOME_SOURCES, usable=False):
            return _manual(values, category, "MODEL_UNCERTAIN", "SN证据存在但无法完整读取")
        return _manual(values, category, "SN_NOT_FOUND", "未读取到明确标注的有效SN")

    screen = _candidates(evidence, {"DEVICE_SCREEN"}, usable=True)
    screen_values = {canonical_sn(item.get("raw_text")) for item in screen}
    if len(screen_values) >= 2:
        return _manual(values, category, "MODEL_UNCERTAIN", "检测到多个不同的清晰屏幕SN")
    if screen:
        return _compare_candidates(values, category, [screen[0]])

    screen_evidence = bool(_candidates(evidence, {"DEVICE_SCREEN"}, usable=False))
    package = _candidates(evidence, PACKAGE_SOURCES, usable=True)

    if category is SnCategory.PHONE:
        fallback_allowed = screen_evidence or bool(_valid_identities(evidence))
        if fallback_allowed and package:
            return _compare_candidates(values, category, package)
        if screen_evidence:
            return _manual(values, category, "MODEL_UNCERTAIN", "屏幕SN证据存在但无法完整读取")
        return _manual(values, category, "SN_NOT_FOUND", "未读取到有效SN")

    if category in {SnCategory.TABLET, SnCategory.WATCH}:
        if screen_evidence and package:
            return _compare_candidates(values, category, package)
        if screen_evidence:
            return _manual(values, category, "MODEL_UNCERTAIN", "屏幕SN证据存在但无法完整读取")
        return _manual(values, category, "SN_NOT_FOUND", "未找到该品类要求的有效屏幕SN")

    if screen_evidence:
        return _manual(values, category, "MODEL_UNCERTAIN", "屏幕SN证据存在但无法完整读取")
    return _manual(values, category, "SN_NOT_FOUND", "未找到该品类要求的有效屏幕SN")


__all__ = [
    "SCHEMA_VERSION",
    "SnCategory",
    "build_model_payload",
    "build_sn_prompt",
    "canonical_sn",
    "classify_sn_category",
    "decide_sn",
]
