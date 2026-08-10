# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import unicodedata


BarcodeScanner = Callable[[dict[str, Any], list[dict[str, Any]]], list[dict[str, Any]]]


def trim_barcode_text(value: Any) -> str:
    text = str(value or "")
    text = text.strip()
    text = text.removeprefix("<NUL>").removeprefix("<NULL>")
    text = text.removesuffix("<NUL>").removesuffix("<NULL>")
    start = 0
    end = len(text)
    while start < end and unicodedata.category(text[start]) in {"Cc", "Cf"}:
        start += 1
    while end > start and unicodedata.category(text[end - 1]) in {"Cc", "Cf"}:
        end -= 1
    return text[start:end].strip()


def _barcode_token(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def comparable_barcode_sn(value: Any) -> str:
    return trim_barcode_text(value).upper().replace("O", "0")


def barcode_reject_reason(decoded: dict[str, Any], fields: dict[str, Any]) -> str:
    kind = _barcode_token(decoded.get("field_type") or decoded.get("type") or decoded.get("label") or "")
    fmt = _barcode_token(decoded.get("format") or decoded.get("barcode_format") or "")
    if kind.startswith("IMEI") or kind in {"MEID", "EID"}:
        return "identity_barcode"
    if kind in {"EAN", "EAN8", "EAN13", "UPC", "UPCA", "UPCE"}:
        return "retail_barcode"
    if fmt in {"EAN8", "EAN13", "UPCA", "UPCE", "EAN", "UPC"}:
        return "retail_barcode"

    text = trim_barcode_text(decoded.get("text") or decoded.get("raw_text") or decoded.get("value") or "")
    for key in ("imei", "imei1", "imei2", "meid", "eid"):
        value = trim_barcode_text(fields.get(key))
        if value and text == value:
            return "identity_or_retail_field"
    return ""


def barcode_second_check(fields: dict[str, Any], decoded_items: list[dict[str, Any]]) -> dict[str, Any]:
    system_sn = trim_barcode_text(fields.get("system_sn"))
    comparable_system_sn = comparable_barcode_sn(system_sn)
    result = {
        "matched": False,
        "match_type": "",
        "matched_text": "",
        "format": "",
        "decoded": decoded_items,
        "reject_reasons": [],
    }
    if not comparable_system_sn:
        result["reject_reasons"].append("system_sn_missing")
        return result

    for decoded in decoded_items:
        if not isinstance(decoded, dict):
            result["reject_reasons"].append("invalid_decode_item")
            continue
        text = trim_barcode_text(decoded.get("text") or decoded.get("raw_text") or decoded.get("value") or "")
        fmt = str(decoded.get("format") or decoded.get("barcode_format") or "")
        reject_reason = barcode_reject_reason(decoded, fields)
        if reject_reason:
            result["reject_reasons"].append(reject_reason)
            continue
        comparable_text = comparable_barcode_sn(text)
        if comparable_text == comparable_system_sn:
            result.update({"matched": True, "match_type": "exact", "matched_text": text, "format": fmt})
            return result
        if comparable_text == "S" + comparable_system_sn:
            result.update({"matched": True, "match_type": "leading_s_prefix", "matched_text": text, "format": fmt})
            return result
    if not result["reject_reasons"]:
        result["reject_reasons"].append("no_barcode_match")
    return result


def barcode_rescue_decision(
    fields: dict[str, Any],
    decision: dict[str, Any],
    barcode_result: dict[str, Any],
) -> dict[str, Any]:
    rescued = dict(decision)
    rescued.update(
        {
            "manual_required": False,
            "manual_reason_code": "",
            "manual_reason": "",
            "manual_reason_codes": [],
            "observed_sn": barcode_result.get("matched_text") or "",
            "normalized_observed_sn": barcode_result.get("matched_text") or "",
            "selected_source": "BARCODE",
            "sn_match": True,
            "system_sn": str(fields.get("system_sn") or ""),
        }
    )
    return rescued


def apply_barcode_second_check(
    task: dict[str, Any],
    decision: dict[str, Any],
    activation_images: list[dict[str, Any]],
    *,
    barcode_scanner: BarcodeScanner | None,
    barcode_mode: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    mode = str(barcode_mode or "off").strip().lower()
    if mode not in {"shadow", "enforce"}:
        return decision, None
    if decision.get("manual_reason_code") != "SN_MISMATCH":
        return decision, None

    scanner = barcode_scanner or scan_activation_barcodes
    try:
        decoded_items = scanner(task, activation_images)
    except Exception as exc:
        return decision, {
            "matched": False,
            "match_type": "",
            "matched_text": "",
            "format": "",
            "decoded": [],
            "reject_reasons": ["scanner_error"],
            "error": type(exc).__name__,
        }
    fields = task.get("fields") or {}
    barcode_result = barcode_second_check(fields, list(decoded_items or []))
    if mode == "enforce" and barcode_result.get("matched"):
        return barcode_rescue_decision(fields, decision, barcode_result), barcode_result
    return decision, barcode_result


def fixed_barcode_regions(width: int, height: int) -> list[tuple[str, tuple[int, int, int, int]]]:
    return [
        ("full", (0, 0, width, height)),
        ("lower_half", (0, height // 2, width, height)),
        ("lower_third", (0, (height * 2) // 3, width, height)),
        ("middle_band", (0, height // 3, width, (height * 2) // 3)),
        ("right_half", (width // 2, 0, width, height)),
        ("left_half", (0, 0, width // 2, height)),
        ("pack_right_lower_wide", (width // 3, height // 2, width, height)),
    ]


def barcode_region_scales(region_name: str) -> tuple[int, ...]:
    if region_name in {"full", "left_half"}:
        return (1,)
    return (1, 2)


def scan_image_barcodes(image_path: Path, image_id: str) -> list[dict[str, Any]]:
    try:
        from PIL import Image
        import zxingcpp
    except Exception:
        return []

    decoded: list[dict[str, Any]] = []
    try:
        with Image.open(image_path) as image:
            source = image.convert("RGB")
            for region_name, box in fixed_barcode_regions(*source.size):
                left, top, right, bottom = box
                if right <= left or bottom <= top:
                    continue
                crop = source if region_name == "full" else source.crop(box)
                for scale in barcode_region_scales(region_name):
                    scan_image = crop if scale == 1 else crop.resize((crop.width * scale, crop.height * scale))
                    scan_region = region_name if scale == 1 else f"{region_name}:s{scale}"
                    for barcode in zxingcpp.read_barcodes(scan_image):
                        text = trim_barcode_text(getattr(barcode, "text", ""))
                        if not text:
                            continue
                        decoded.append(
                            {
                                "image_id": image_id,
                                "text": text,
                                "format": str(getattr(barcode, "format", "")),
                                "region": scan_region,
                            }
                        )
    except Exception:
        return decoded
    return decoded


def scan_activation_barcodes(_task: dict[str, Any], activation_images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    decoded: list[dict[str, Any]] = []
    for image in activation_images:
        image_path = Path(str(image.get("local_path") or image.get("path") or ""))
        if not image_path.is_file():
            continue
        image_id = str(image.get("image_id") or "")
        try:
            image_items = scan_image_barcodes(image_path, image_id)
        except Exception:
            image_items = []
        for item in image_items:
            key = (str(item.get("text") or ""), str(item.get("format") or ""), image_id)
            if key in seen:
                continue
            seen.add(key)
            decoded.append(item)
    return decoded


__all__ = [
    "BarcodeScanner",
    "apply_barcode_second_check",
    "barcode_region_scales",
    "barcode_rescue_decision",
    "barcode_reject_reason",
    "barcode_second_check",
    "fixed_barcode_regions",
    "scan_activation_barcodes",
    "scan_image_barcodes",
    "trim_barcode_text",
]
