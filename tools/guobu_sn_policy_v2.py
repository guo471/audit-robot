# -*- coding: utf-8 -*-
from __future__ import annotations

import os
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
SN_LOGIC_VERSION = "sn_v2_barcode_shadow_20260731"
PROMPT_CHAR_LIMIT = 500

SCREEN_SOURCES = {"DEVICE_SCREEN", "SCREEN"}
PACKAGE_SOURCES = {"PACKAGE_LABEL"}
BODY_SOURCES = {"DEVICE_BODY"}
ALL_SN_SOURCES = SCREEN_SOURCES | PACKAGE_SOURCES | BODY_SOURCES
HOME_SOURCES = PACKAGE_SOURCES | BODY_SOURCES

SCREEN_SN_CLEAR = "SCREEN_SN_CLEAR"
SCREEN_SN_UNCLEAR = "SCREEN_SN_UNCLEAR"
PHONE_IDENTITY_ONLY = "PHONE_IDENTITY_ONLY"
NO_SCREEN_IDENTITY = "NO_SCREEN_IDENTITY"
SCREEN_SN_CONFLICT = "SCREEN_SN_CONFLICT"
LEGACY_STATE_MAP = {
    "SCREEN_SN_READABLE": SCREEN_SN_CLEAR,
    "SCREEN_SN_UNREADABLE": SCREEN_SN_UNCLEAR,
    "NO_SCREEN_SN": NO_SCREEN_IDENTITY,
    "NOT_APPLICABLE": NO_SCREEN_IDENTITY,
}
SCREEN_IDENTITY_STATES = {
    SCREEN_SN_CLEAR,
    SCREEN_SN_UNCLEAR,
    PHONE_IDENTITY_ONLY,
    NO_SCREEN_IDENTITY,
    SCREEN_SN_CONFLICT,
}

SN_FIELD_TYPES = {"SN", "S/N", "SN码", "SERIAL", "SERIALNO", "SERIALNUMBER", "SERIAL_NO", "SERIAL_NUMBER"}
IDENTITY_TYPES = {"IMEI", "IMEI1", "IMEI2", "1码", "2码", "MEID", "EID"}
NON_SN_FIELD_TYPES = {
    "IMEI",
    "IMEI1",
    "IMEI2",
    "MEID",
    "EID",
    "MODEL",
    "型号",
    "产品编号",
    "PRODUCT_CODE",
    "BARCODE",
    "EAN",
}

_PRIMARY_CATEGORY_FIELDS = ("category_name", "cate_code_name", "product_type", "type")
_UNSUPPORTED_DIGITAL_KEYWORDS = ("相机", "照相机", "耳机", "耳麦", "camera", "headphone", "headset", "earphone", "earbud")

SN_RECOGNITION_PROMPT = (
    "你是SN识别员，只看激活/SN照片，只输出JSON，不比对；品类由本地给。"
    "SN标签：SN/SN码/S/N/序列号/产品序列号/Serial/包装标签S/N。"
    "sn_candidates项：image_id,source(DEVICE_SCREEN/DEVICE_BODY/PACKAGE_LABEL),field_type,raw_text,normalized_text,readable,complete,confidence。"
    "不合并猜改，O0/I1L/S5/B8/G6/2Z不清则readable=false。"
    "家电铭牌/服务贴纸型号下行或二维码下方长字母数字可作PACKAGE_LABEL；型号/电话/地址/容量/普通条码不是SN。"
    "IMEI/1码/2码/MEID/EID进identity_evidence。"
    "screen_identity_state:SCREEN_SN_CLEAR/SCREEN_SN_UNCLEAR/PHONE_IDENTITY_ONLY/NO_SCREEN_IDENTITY/SCREEN_SN_CONFLICT。"
    "无可信SN填SN_NOT_FOUND。输出同名JSON字段。"
)


def canonical_sn(value: Any) -> str:
    return re.sub(r"[^0-9A-Z/]", "", str(value or "").upper())


def canonical_system_sn(value: Any) -> str:
    return canonical_sn(value)


_SN_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:LENOVO\s*S\s*/\s*N|LENOVO\s*SN|\u8054\u60f3\s*SN|S\s*/\s*N|SN(?:\s*\u7801)?|SERIAL(?:\s*(?:NO\.?|NUMBER))?|\u5e8f\u5217\u53f7|\u4ea7\u54c1\u5e8f\u5217\u53f7)\s*[:\uff1a#._/\-\s]+\s*",
    re.IGNORECASE,
)


def strip_sn_label_prefix(value: Any) -> str:
    text = str(value or "").strip()
    previous = None
    while previous != text:
        previous = text
        text = _SN_LABEL_PREFIX_RE.sub("", text, count=1).strip()
    return text


def canonical_candidate_sn(candidate: dict[str, Any]) -> str:
    return canonical_sn(strip_sn_label_prefix(candidate.get("raw_text")))


SN_RECOGNITION_PROMPT = (
    "\u4f60\u662fSN\u8bc6\u522b\u5458\uff0c\u53ea\u770b\u6fc0\u6d3b/SN\u7167\u7247\uff0c\u53ea\u8f93\u51faJSON\uff0c\u4e0d\u6bd4\u5bf9\uff1b\u54c1\u7c7b\u672c\u5730\u7ed9\u3002"
    "\u6807\u7b7e\u53ea\u5b9a\u4f4d\uff0craw/normalized\u53ea\u5199\u7eafSN\uff0c\u7981\u5e26SN:/S/N:/Serial No:/\u5e8f\u5217\u53f7:\u3002"
    "sn_candidates\u9879\uff1aimage_id,source(DEVICE_SCREEN/DEVICE_BODY/PACKAGE_LABEL),field_type,raw_text,normalized_text,readable,complete,confidence\u3002"
    "\u5b8c\u6574\u8fde\u7eedSN\u5fc5\u586b\u5019\u9009\uff1b\u4e0d\u5408\u5e76\u4e0d\u731c\uff0cO0/I1L/S5/B8/G6/2Z\u4e0d\u6e05\u5219readable=false\u3002"
    "\u5bb6\u7535\u94ed\u724c/\u670d\u52a1\u8d34\u7eb8\u578b\u53f7\u4e0b\u884c\u6216\u7801\u4e0b\u957f\u5b57\u6bcd\u6570\u5b57\u53ef\u4f5cPACKAGE_LABEL\uff1b\u578b\u53f7/\u7535\u8bdd/\u5730\u5740/\u5bb9\u91cf\u975eSN\u3002"
    "IMEI/1\u7801/2\u7801/MEID/EID\u8fdbidentity_evidence\u3002screen_identity_state:SCREEN_SN_CLEAR/SCREEN_SN_UNCLEAR/PHONE_IDENTITY_ONLY/NO_SCREEN_IDENTITY/SCREEN_SN_CONFLICT\u3002"
    "\u65e0SN\u586bSN_NOT_FOUND\u3002"
)


def _normalize_token(value: Any) -> str:
    return re.sub(r"[\s./:#_\-]+", "", str(value or "").strip().upper())


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
    if any(keyword in text for keyword in ("智能手表手环", "智能手表", "智能手环", "手表", "手环", "smartwatch", "watch", "wristband")):
        return SnCategory.WATCH
    if any(keyword in text for keyword in ("手机", "智能手机", "smartphone", "phone")):
        return SnCategory.PHONE
    return SnCategory.UNSUPPORTED


def classify_sn_category(fields: dict[str, Any] | None, *, effective_category: str | None = None) -> SnCategory:
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
    _coerce_category(category)
    return SN_RECOGNITION_PROMPT


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


def _is_true(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def _candidate_source(candidate: dict[str, Any]) -> str:
    source = str(candidate.get("source") or "").strip().upper()
    return "DEVICE_SCREEN" if source == "SCREEN" else source


def _field_type(candidate: dict[str, Any]) -> str:
    raw = str(candidate.get("field_type") or "").strip()
    compact = _normalize_token(raw)
    if compact in {"LENOVOSN", "LENOVOSN", "联想SN"}:
        return "SN"
    if compact in {"SERIALNO", "SERIALNUMBER", "SERIAL"}:
        return compact
    if raw in {"\u5e8f\u5217\u53f7", "\u4ea7\u54c1\u5e8f\u5217\u53f7", "\u0053\u004e\u7801"}:
        return "SN"
    if raw in {"SN码", "序列号", "产品序列号"}:
        return "SN"
    return compact


def _ambiguity_present(candidate: dict[str, Any]) -> bool:
    notes = candidate.get("visual_ambiguity_notes")
    if isinstance(notes, list):
        return any(str(item or "").strip() for item in notes)
    return bool(str(notes or "").strip())


def _explicit_candidate(candidate: Any) -> bool:
    if not isinstance(candidate, dict):
        return False
    field_type = _field_type(candidate)
    if field_type in NON_SN_FIELD_TYPES or field_type not in SN_FIELD_TYPES:
        return False
    if _candidate_source(candidate) not in ALL_SN_SOURCES:
        return False
    return bool(canonical_candidate_sn(candidate))


def _usable_candidate(candidate: Any) -> bool:
    if not _explicit_candidate(candidate):
        return False
    if not _is_true(candidate.get("readable")):
        return False
    if "complete" in candidate and not _is_true(candidate.get("complete")):
        return False
    confidence = candidate.get("confidence", 1)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or confidence <= 0:
        return False
    return not _ambiguity_present(candidate)


def _candidates(evidence: dict[str, Any], sources: set[str], *, usable: bool) -> list[dict[str, Any]]:
    predicate = _usable_candidate if usable else _explicit_candidate
    return [
        candidate
        for candidate in evidence.get("sn_candidates", [])
        if predicate(candidate) and _candidate_source(candidate) in sources
    ]


def _identity_type(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw in {"1码", "1CODE", "CODE1"}:
        return "IMEI1"
    if raw in {"2码", "2CODE", "CODE2"}:
        return "IMEI2"
    return re.sub(r"[\s_\-]+", "", raw)


def _identity_entries(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = evidence.get("identity_evidence")
    if raw_items is None or raw_items == "":
        return []
    if isinstance(raw_items, dict):
        raw_items = [
            {"source": "DEVICE_SCREEN", "field_type": key, "raw_text": value, "readable": True, "complete": True}
            for key, value in raw_items.items()
        ]
    if not isinstance(raw_items, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        mapped = dict(item)
        if "field_type" not in mapped and "type" in mapped:
            mapped["field_type"] = mapped.get("type")
        if "raw_text" not in mapped and "value" in mapped:
            mapped["raw_text"] = mapped.get("value")
        mapped.setdefault("source", "DEVICE_SCREEN")
        mapped.setdefault("readable", True)
        mapped.setdefault("complete", True)
        entries.append(mapped)
    for item in evidence.get("sn_candidates", []):
        if not isinstance(item, dict):
            continue
        if _identity_type(item.get("field_type")) in IDENTITY_TYPES:
            entries.append(dict(item))
    return entries


def _sn_candidates_from_identity_evidence(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    rescued: list[dict[str, Any]] = []
    for item in _identity_entries(evidence):
        if not isinstance(item, dict):
            continue
        if _identity_type(item.get("field_type")) in IDENTITY_TYPES:
            continue
        candidate = dict(item)
        candidate.setdefault("source", "DEVICE_SCREEN")
        source = _candidate_source(candidate)
        if source not in SCREEN_SOURCES | PACKAGE_SOURCES:
            continue
        if _field_type(candidate) not in SN_FIELD_TYPES:
            continue
        candidate.setdefault("readable", True)
        candidate.setdefault("complete", True)
        candidate.setdefault("confidence", 1.0)
        rescued.append(candidate)
    return rescued


def _clean_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(evidence)
    raw_candidates = evidence.get("sn_candidates") or []
    cleaned["sn_candidates"] = list(raw_candidates) + _sn_candidates_from_identity_evidence(evidence)
    return cleaned


def _valid_identities(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for item in _identity_entries(evidence):
        field_type = _identity_type(item.get("field_type"))
        canonical_value = canonical_sn(item.get("raw_text"))
        if field_type in {"IMEI", "IMEI1", "IMEI2"}:
            value_is_complete = bool(re.fullmatch(r"[0-9]{15}", canonical_value))
        elif field_type == "MEID":
            value_is_complete = bool(re.fullmatch(r"(?:[0-9A-F]{14}|[0-9]{18})", canonical_value))
        elif field_type == "EID":
            value_is_complete = bool(re.fullmatch(r"[0-9]{32}", canonical_value))
        else:
            value_is_complete = False
        if (
            field_type in {"IMEI", "IMEI1", "IMEI2", "MEID", "EID"}
            and _candidate_source(item) == "DEVICE_SCREEN"
            and _is_true(item.get("readable"))
            and ("complete" not in item or _is_true(item.get("complete")))
            and value_is_complete
        ):
            valid.append(item)
    return valid


def _system_identity_value(fields: dict[str, Any], key: str) -> str:
    aliases = {
        "IMEI1": ("imei1", "imei_1", "1_code", "code1", "one_code", "\u0031\u7801"),
        "IMEI2": ("imei2", "imei_2", "2_code", "code2", "two_code", "\u0032\u7801"),
    }
    for alias in aliases.get(key, ()):
        value = canonical_sn(fields.get(alias))
        if value:
            return value
    return ""


_IDENTITY_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:IMEI\s*[/_-]\s*MEID|IMEI\s*(?:1|2)?|MEID|EID|[12]\s*\u7801)\s*[:\uff1a._/\-\s]*",
    re.IGNORECASE,
)


def _canonical_identity_value(value: Any) -> str:
    text = str(value or "").strip()
    previous = None
    while previous != text:
        previous = text
        text = _IDENTITY_LABEL_PREFIX_RE.sub("", text, count=1).strip()
    return canonical_sn(text)


def _identity_slot_is_explicit(item: dict[str, Any], field_type: str) -> bool:
    evidence_text = " ".join(
        str(item.get(key) or "").strip()
        for key in ("field_type", "raw_text", "label_text", "raw_context", "context")
        if str(item.get(key) or "").strip()
    )
    if not evidence_text:
        return False
    compact_text = re.sub(r"[\s/_-]+", "", evidence_text.upper())
    labels = {
        re.sub(r"[\s/_-]+", "", match.group(1).upper())
        for match in re.finditer(r"(I\s*M\s*E\s*I\s*(?:1|2)|[12]\s*\u7801)", evidence_text, re.IGNORECASE)
    }
    if "IMEI1" in compact_text:
        labels.add("IMEI1")
    if "IMEI2" in compact_text:
        labels.add("IMEI2")
    if field_type == "IMEI1":
        return bool(labels & {"IMEI1", "\u0031\u7801"})
    if field_type == "IMEI2":
        return bool(labels & {"IMEI2", "\u0032\u7801"})
    return False


def _identity_gate_mismatches(fields: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    observed_by_type: dict[str, list[tuple[str, str]]] = {"IMEI1": [], "IMEI2": []}
    for item in _identity_entries(evidence):
        field_type = _identity_type(item.get("field_type"))
        if field_type not in observed_by_type:
            continue
        if _candidate_source(item) != "DEVICE_SCREEN" or not _is_true(item.get("readable")):
            continue
        if "complete" in item and not _is_true(item.get("complete")):
            continue
        if not _identity_slot_is_explicit(item, field_type):
            continue
        observed = _canonical_identity_value(item.get("raw_text"))
        if not re.fullmatch(r"[0-9]{15}", observed):
            continue
        if observed:
            observed_by_type[field_type].append((observed, str(item.get("raw_text") or "")))

    mismatches: list[str] = []
    for field_type, label in (("IMEI1", "\u0031\u7801"), ("IMEI2", "\u0032\u7801")):
        system = _system_identity_value(fields, field_type)
        observed_items = observed_by_type[field_type]
        if not system or not observed_items:
            continue
        if not re.fullmatch(r"[0-9]{15}", system):
            continue
        observed_values = {observed for observed, _raw in observed_items}
        if observed_values == {system}:
            continue
        raw_values = " / ".join(raw for _observed, raw in observed_items if raw)
        mismatches.append(f"\u7cfb\u7edf{label}\u4e0e\u7167\u7247{label}\u4e0d\u4e00\u81f4\uff08\u7cfb\u7edf\uff1a{system}\uff0c\u7167\u7247\uff1a{raw_values}\uff09")
    return mismatches


def _apply_identity_gate(
    fields: dict[str, Any],
    category: SnCategory,
    evidence: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    if category not in {SnCategory.PHONE, SnCategory.TABLET, SnCategory.WATCH, SnCategory.COMPUTER}:
        return decision
    mismatches = _identity_gate_mismatches(fields, evidence)
    if not mismatches:
        return decision
    blocked = dict(decision)
    blocked["manual_required"] = True
    blocked["manual_reason_code"] = "SN_MISMATCH"
    existing_reason = str(blocked.get("manual_reason") or "").strip()
    identity_reason = "\uff1b".join(mismatches)
    blocked["manual_reason"] = "\uff1b".join(part for part in (existing_reason, identity_reason) if part)
    blocked["manual_reason_codes"] = ["SN_MISMATCH"]
    blocked["identity_code_mismatch"] = True
    blocked["sn_match"] = False
    return blocked


def _screen_state(value: Any) -> str:
    state = str(value or "").strip().upper()
    return LEGACY_STATE_MAP.get(state, state)


def _derived_screen_state(evidence: dict[str, Any], category: SnCategory) -> str:
    screen_usable = _candidates(evidence, {"DEVICE_SCREEN"}, usable=True)
    screen_values = {canonical_candidate_sn(item) for item in screen_usable}
    if len(screen_values) >= 2:
        return SCREEN_SN_CONFLICT
    if screen_values:
        return SCREEN_SN_CLEAR
    if _candidates(evidence, {"DEVICE_SCREEN"}, usable=False):
        return SCREEN_SN_UNCLEAR
    if category is SnCategory.PHONE and _valid_identities(evidence):
        return PHONE_IDENTITY_ONLY
    return NO_SCREEN_IDENTITY


def _base_decision(fields: dict[str, Any], category: SnCategory) -> dict[str, Any]:
    return {
        "audit_category": category.value,
        "manual_required": True,
        "manual_reason_code": "MODEL_UNCERTAIN",
        "manual_reason": "SN证据无法确认",
        "system_sn": str(fields.get("system_sn") or ""),
        "normalized_system_sn": canonical_system_sn(fields.get("system_sn")),
        "observed_sn": "",
        "normalized_observed_sn": "",
        "selected_source": "",
        "sn_match": False,
    }


def _is_tv_order(fields: dict[str, Any]) -> bool:
    text = " ".join(str(fields.get(key) or "") for key in ("product_type", "cate_code_name", "category_name", "goods_name"))
    return "电视" in text or "TV" in text.upper()


def _manual(fields: dict[str, Any], category: SnCategory, code: str, reason: str, candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    decision = _base_decision(fields, category)
    decision["manual_reason_code"] = code
    decision["manual_reason"] = reason
    if candidate:
        decision["observed_sn"] = strip_sn_label_prefix(candidate.get("raw_text"))
        decision["normalized_observed_sn"] = canonical_candidate_sn(candidate)
        decision["selected_source"] = _candidate_source(candidate)
    return decision


def _pass(fields: dict[str, Any], category: SnCategory, candidate: dict[str, Any]) -> dict[str, Any]:
    decision = _base_decision(fields, category)
    decision.update(
        {
            "manual_required": False,
            "manual_reason_code": "",
            "manual_reason": "",
            "observed_sn": strip_sn_label_prefix(candidate.get("raw_text")),
            "normalized_observed_sn": canonical_candidate_sn(candidate),
            "selected_source": _candidate_source(candidate),
            "sn_match": True,
        }
    )
    return decision


def _has_same_priority_conflict(candidates: list[dict[str, Any]]) -> bool:
    return len({canonical_candidate_sn(candidate) for candidate in candidates}) >= 2


def _env_enabled(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_d71_home_appliance_auxiliary_code(candidate: dict[str, Any], *, system: str) -> bool:
    value = canonical_candidate_sn(candidate)
    if not value or value == system:
        return False
    if _candidate_source(candidate) not in HOME_SOURCES:
        return False
    raw = str(candidate.get("raw_text") or "")
    return (
        value.startswith("D71")
        and raw.count("-") >= 3
        and 18 <= len(value) <= 26
    )


def _home_appliance_exact_match_conflict_rescue_candidate(
    fields: dict[str, Any],
    category: SnCategory,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if category is not SnCategory.HOME_APPLIANCE:
        return None
    if not _env_enabled("SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE"):
        return None
    system = canonical_system_sn(fields.get("system_sn"))
    if not system:
        return None
    exact_matches = [candidate for candidate in candidates if canonical_candidate_sn(candidate) == system]
    if len(exact_matches) != 1:
        return None
    for candidate in candidates:
        if candidate is exact_matches[0]:
            continue
        if not _is_d71_home_appliance_auxiliary_code(candidate, system=system):
            return None
    return exact_matches[0]


def _leading_s_exception_applies(fields: dict[str, Any], candidate: dict[str, Any], all_usable: list[dict[str, Any]]) -> bool:
    system = canonical_system_sn(fields.get("system_sn"))
    observed = canonical_candidate_sn(candidate)
    if _candidate_source(candidate) != "DEVICE_SCREEN":
        return False
    if not system.startswith("S") or observed != system[1:] or not observed:
        return False
    for other in all_usable:
        if other is candidate:
            continue
        value = canonical_candidate_sn(other)
        if value not in {system, observed}:
            return False
    return True


def _package_prefix_rescue_candidate(
    fields: dict[str, Any],
    category: SnCategory,
    candidate: dict[str, Any],
    all_usable: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if category not in {SnCategory.TABLET, SnCategory.WATCH, SnCategory.COMPUTER}:
        return None
    if _candidate_source(candidate) != "DEVICE_SCREEN":
        return None
    system = canonical_system_sn(fields.get("system_sn"))
    observed = canonical_candidate_sn(candidate)
    missing = len(system) - len(observed)
    if not observed or observed == system or not system.startswith(observed):
        return None
    if missing <= 0 or missing > 4 or len(observed) < 8:
        return None
    rescue = [
        item
        for item in all_usable
        if _candidate_source(item) in PACKAGE_SOURCES and canonical_candidate_sn(item) == system
    ]
    if len(rescue) != 1:
        return None
    for other in all_usable:
        if other is candidate or other is rescue[0]:
            continue
        value = canonical_candidate_sn(other)
        if value not in {observed, system}:
            return None
    return rescue[0]


def _compare_candidates(
    fields: dict[str, Any],
    category: SnCategory,
    candidates: list[dict[str, Any]],
    *,
    all_usable: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    system = canonical_system_sn(fields.get("system_sn"))
    if not candidates:
        return _manual(fields, category, "SN_NOT_FOUND", "未读取到有效SN")
    if _has_same_priority_conflict(candidates):
        rescue = _home_appliance_exact_match_conflict_rescue_candidate(fields, category, candidates)
        if rescue is not None:
            decision = _pass(fields, category, rescue)
            decision["sn_conflict_resolution"] = "home_appliance_exact_system_sn_d71_auxiliary_code"
            return decision
        return _manual(fields, category, "MODEL_UNCERTAIN", "同优先级SN候选冲突", candidates[0])
    candidate = candidates[0]
    observed = canonical_candidate_sn(candidate)
    if observed == system:
        return _pass(fields, category, candidate)
    if _leading_s_exception_applies(fields, candidate, all_usable or candidates):
        return _pass(fields, category, candidate)
    rescue = _package_prefix_rescue_candidate(fields, category, candidate, all_usable or candidates)
    if rescue is not None:
        return _pass(fields, category, rescue)
    return _manual(
        fields,
        category,
        "SN_MISMATCH",
        f"系统SN与照片SN不一致（系统：{fields.get('system_sn') or ''}，照片：{candidate.get('raw_text') or ''}）",
        candidate,
    )


def _schema_error(evidence: Any, category: SnCategory, allowed_image_ids: Iterable[str] | None = None) -> str:
    if not isinstance(evidence, dict):
        return "模型SN证据不是JSON对象"
    if evidence.get("schema_version") not in {None, "", SCHEMA_VERSION}:
        return "模型SN证据版本不正确"
    if _screen_state(evidence.get("screen_identity_state")) not in SCREEN_IDENTITY_STATES:
        return "模型SN证据的屏幕状态无效"
    if not isinstance(evidence.get("sn_candidates"), list):
        return "模型SN候选结构无效"
    confidence = evidence.get("confidence", 1)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return "模型SN证据置信度无效"

    allowed = {str(image_id) for image_id in allowed_image_ids or [] if str(image_id)}
    for key in ("sn_candidates",):
        for item in evidence.get(key, []):
            if not isinstance(item, dict):
                return "模型SN证据字段结构无效"
            if "source" not in item or "field_type" not in item or "raw_text" not in item or "readable" not in item:
                return "模型SN证据字段结构无效"
            if not isinstance(item.get("source"), str) or not isinstance(item.get("field_type"), str) or not isinstance(item.get("raw_text"), str):
                return "模型SN证据字段结构无效"
            if not isinstance(item.get("readable"), bool):
                return "模型SN证据字段结构无效"
            if "complete" in item and not isinstance(item.get("complete"), bool):
                return "模型SN证据字段结构无效"
            if "confidence" in item and (isinstance(item.get("confidence"), bool) or not isinstance(item.get("confidence"), (int, float))):
                return "模型SN证据字段结构无效"
            notes = item.get("visual_ambiguity_notes", [])
            if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
                return "模型SN证据字段结构无效"
            if allowed:
                image_id = str(item.get("image_id") or "")
                if not image_id or image_id not in allowed:
                    return "模型SN证据引用了非本单激活照片"

    for item in _identity_entries(evidence):
        if allowed:
            image_id = str(item.get("image_id") or "")
            if image_id and image_id not in allowed:
                return "模型SN证据引用了非本单激活照片"

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
    if not canonical_system_sn(values.get("system_sn")):
        return _manual(values, category, "SYSTEM_SN_MISSING", "系统SN缺失")

    schema_error = _schema_error(evidence, category, allowed_image_ids)
    if schema_error:
        return _manual(values, category, "MODEL_UNCERTAIN", schema_error)

    evidence = _clean_evidence(evidence)
    model_state = _screen_state(evidence.get("screen_identity_state"))
    state = _derived_screen_state(evidence, category)
    all_usable = _candidates(evidence, ALL_SN_SOURCES, usable=True)

    def finalize(decision: dict[str, Any]) -> dict[str, Any]:
        return _apply_identity_gate(values, category, evidence, decision)

    if state == SCREEN_SN_CONFLICT:
        return _manual(values, category, "MODEL_UNCERTAIN", "屏幕出现多个不同SN")

    if category is SnCategory.HOME_APPLIANCE:
        home_sources = set(HOME_SOURCES)
        if _is_tv_order(values):
            home_sources |= {"DEVICE_SCREEN"}
        usable = _candidates(evidence, home_sources, usable=True)
        if usable:
            return finalize(_compare_candidates(values, category, usable, all_usable=all_usable))
        if _candidates(evidence, home_sources, usable=False):
            return _manual(values, category, "MODEL_UNCERTAIN", "SN证据存在但不完整或不清楚")
        return _manual(values, category, "SN_NOT_FOUND", "未读取到有效SN")

    screen = _candidates(evidence, {"DEVICE_SCREEN"}, usable=True)
    if state == SCREEN_SN_CLEAR:
        if not screen:
            return _manual(values, category, "MODEL_UNCERTAIN", "屏幕SN状态与候选不一致")
        return finalize(_compare_candidates(values, category, screen, all_usable=all_usable))

    package = _candidates(evidence, PACKAGE_SOURCES, usable=True)
    explicit_screen = _candidates(evidence, {"DEVICE_SCREEN"}, usable=False)

    if (
        category is SnCategory.PHONE
        and package
        and not screen
        and model_state in {SCREEN_SN_CLEAR, SCREEN_SN_UNCLEAR, PHONE_IDENTITY_ONLY}
    ):
        return finalize(_compare_candidates(values, category, package, all_usable=all_usable))

    if category is SnCategory.PHONE and (state == PHONE_IDENTITY_ONLY or model_state == PHONE_IDENTITY_ONLY):
        if package:
            return finalize(_compare_candidates(values, category, package, all_usable=all_usable))
        return _manual(values, category, "SN_NOT_FOUND", "手机屏幕只有身份字段，未读取到包装SN")

    if state == SCREEN_SN_UNCLEAR:
        if package:
            return finalize(_compare_candidates(values, category, package, all_usable=all_usable))
        if explicit_screen:
            return _manual(values, category, "MODEL_UNCERTAIN", "屏幕SN证据存在但不完整或不清楚", explicit_screen[0])
        return _manual(values, category, "MODEL_UNCERTAIN", "屏幕SN状态与候选不一致")

    return _manual(values, category, "SN_NOT_FOUND", "未读取到允许自动比对的有效SN")


__all__ = [
    "PROMPT_CHAR_LIMIT",
    "SCHEMA_VERSION",
    "SN_LOGIC_VERSION",
    "SnCategory",
    "build_model_payload",
    "build_sn_prompt",
    "canonical_sn",
    "canonical_system_sn",
    "classify_sn_category",
    "decide_sn",
]
