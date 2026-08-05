# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import run_guobu_model_audit_v2 as v1_transport
from tools.guobu_sn_policy_v2 import (
    SCHEMA_VERSION,
    SN_LOGIC_VERSION,
    SnCategory,
    build_model_payload,
    build_sn_prompt,
    classify_sn_category,
    decide_sn,
)


ModelCaller = Callable[..., tuple[dict[str, Any], str, float, dict[str, Any], bool]]
BarcodeScanner = Callable[[dict[str, Any], list[dict[str, Any]]], list[dict[str, Any]]]


def _empty_evidence(category: SnCategory) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "screen_identity_state": "NO_SCREEN_IDENTITY",
        "observed_sn": "",
        "normalized_observed_sn": "",
        "sn_candidates": [],
        "identity_evidence": [],
        "manual_reason_code": "SN_NOT_FOUND",
        "manual_reason": "",
        "confidence": 0.0,
    }


def _row_from_decision(
    task: dict[str, Any],
    decision: dict[str, Any],
    *,
    elapsed_sec: float,
    sn_elapsed_sec: float,
    model_calls: int,
    total_tokens: int,
    screen_identity_state: str,
    barcode_mode: str,
) -> dict[str, Any]:
    fields = task.get("fields") or {}
    return {
        "id": str(task.get("channel_order_no") or task.get("task_id") or ""),
        "manual_flag": "是" if decision.get("manual_required") else "否",
        "manual_reason_code": str(decision.get("manual_reason_code") or ""),
        "manual_reason": str(decision.get("manual_reason") or ""),
        "audit_category": str(decision.get("audit_category") or ""),
        "product_type": str(fields.get("product_type") or ""),
        "system_sn": str(decision.get("system_sn") or ""),
        "observed_sn": str(decision.get("observed_sn") or ""),
        "sn_match": bool(decision.get("sn_match")),
        "selected_source": str(decision.get("selected_source") or ""),
        "screen_identity_state": screen_identity_state,
        "sn_version": SN_LOGIC_VERSION,
        "barcode_mode": str(barcode_mode or "off"),
        "elapsed_sec": round(elapsed_sec, 4),
        "sn_elapsed_sec": round(sn_elapsed_sec, 4),
        "strategy": "sn_v2_sidecar",
        "model_calls": model_calls,
        "total_tokens": total_tokens,
    }


def _trim_barcode_text(value: Any) -> str:
    return str(value or "").strip()


def _barcode_token(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _barcode_reject_reason(decoded: dict[str, Any], fields: dict[str, Any]) -> str:
    kind = _barcode_token(decoded.get("field_type") or decoded.get("type") or decoded.get("label") or "")
    fmt = _barcode_token(decoded.get("format") or decoded.get("barcode_format") or "")
    if kind.startswith("IMEI") or kind in {"MEID", "EID"}:
        return "identity_barcode"
    if kind in {"EAN", "EAN8", "EAN13", "UPC", "UPCA", "UPCE"}:
        return "retail_barcode"
    if fmt in {"EAN8", "EAN13", "UPCA", "UPCE", "EAN", "UPC"}:
        return "retail_barcode"

    text = _trim_barcode_text(decoded.get("text") or decoded.get("raw_text") or decoded.get("value") or "")
    for key in ("imei", "imei1", "imei2", "meid", "eid"):
        value = _trim_barcode_text(fields.get(key))
        if value and text == value:
            return "identity_or_retail_field"
    return ""


def _barcode_second_check(fields: dict[str, Any], decoded_items: list[dict[str, Any]]) -> dict[str, Any]:
    system_sn = _trim_barcode_text(fields.get("system_sn"))
    result = {
        "matched": False,
        "match_type": "",
        "matched_text": "",
        "format": "",
        "decoded": decoded_items,
        "reject_reasons": [],
    }
    if not system_sn:
        result["reject_reasons"].append("system_sn_missing")
        return result

    for decoded in decoded_items:
        if not isinstance(decoded, dict):
            result["reject_reasons"].append("invalid_decode_item")
            continue
        text = _trim_barcode_text(decoded.get("text") or decoded.get("raw_text") or decoded.get("value") or "")
        fmt = str(decoded.get("format") or decoded.get("barcode_format") or "")
        reject_reason = _barcode_reject_reason(decoded, fields)
        if reject_reason:
            result["reject_reasons"].append(reject_reason)
            continue
        if text == system_sn:
            result.update({"matched": True, "match_type": "exact", "matched_text": text, "format": fmt})
            return result
        if text == "S" + system_sn:
            result.update({"matched": True, "match_type": "leading_s_prefix", "matched_text": text, "format": fmt})
            return result
    if not result["reject_reasons"]:
        result["reject_reasons"].append("no_barcode_match")
    return result


def _barcode_rescue_decision(task: dict[str, Any], decision: dict[str, Any], barcode_result: dict[str, Any]) -> dict[str, Any]:
    fields = task.get("fields") or {}
    rescued = dict(decision)
    rescued.update(
        {
            "manual_required": False,
            "manual_reason_code": "",
            "manual_reason": "",
            "observed_sn": barcode_result.get("matched_text") or "",
            "normalized_observed_sn": barcode_result.get("matched_text") or "",
            "selected_source": "BARCODE",
            "sn_match": True,
            "system_sn": str(fields.get("system_sn") or ""),
        }
    )
    return rescued


def _apply_barcode_second_check(
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

    scanner = barcode_scanner or _scan_activation_barcodes
    decoded_items = scanner(task, activation_images)
    barcode_result = _barcode_second_check(task.get("fields") or {}, list(decoded_items or []))
    if mode == "enforce" and barcode_result.get("matched"):
        return _barcode_rescue_decision(task, decision, barcode_result), barcode_result
    return decision, barcode_result


def _fixed_barcode_regions(width: int, height: int) -> list[tuple[str, tuple[int, int, int, int]]]:
    return [
        ("full", (0, 0, width, height)),
        ("lower_half", (0, height // 2, width, height)),
        ("lower_third", (0, (height * 2) // 3, width, height)),
        ("middle_band", (0, height // 3, width, (height * 2) // 3)),
        ("right_half", (width // 2, 0, width, height)),
        ("left_half", (0, 0, width // 2, height)),
        ("pack_right_lower_wide", (width // 3, height // 2, width, height)),
    ]


def _barcode_region_scales(region_name: str) -> tuple[int, ...]:
    if region_name in {"full", "left_half"}:
        return (1,)
    return (1, 2)


def _scan_image_barcodes(image_path: Path, image_id: str) -> list[dict[str, Any]]:
    try:
        from PIL import Image
        import zxingcpp
    except Exception:
        return []

    decoded: list[dict[str, Any]] = []
    try:
        with Image.open(image_path) as image:
            source = image.convert("RGB")
            for region_name, box in _fixed_barcode_regions(*source.size):
                left, top, right, bottom = box
                if right <= left or bottom <= top:
                    continue
                crop = source if region_name == "full" else source.crop(box)
                for scale in _barcode_region_scales(region_name):
                    scan_image = crop if scale == 1 else crop.resize((crop.width * scale, crop.height * scale))
                    scan_region = region_name if scale == 1 else f"{region_name}:s{scale}"
                    for barcode in zxingcpp.read_barcodes(scan_image):
                        text = _trim_barcode_text(getattr(barcode, "text", ""))
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


def _scan_activation_barcodes(_task: dict[str, Any], activation_images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    decoded: list[dict[str, Any]] = []
    for image in activation_images:
        image_path = Path(str(image.get("local_path") or image.get("path") or ""))
        if not image_path.is_file():
            continue
        image_id = str(image.get("image_id") or "")
        for item in _scan_image_barcodes(image_path, image_id):
            key = (str(item.get("text") or ""), str(item.get("format") or ""), image_id)
            if key in seen:
                continue
            seen.add(key)
            decoded.append(item)
    return decoded


def audit_task_sn_v2(
    base_url: str,
    api_key: str,
    model: str,
    task: dict[str, Any],
    *,
    model_caller: ModelCaller | None = None,
    cache_dir: Path | None = None,
    timeout_sec: float = 60.0,
    barcode_scanner: BarcodeScanner | None = None,
    barcode_mode: str = "shadow",
) -> dict[str, Any]:
    started = time.time()
    fields = task.get("fields") or {}
    effective_category = v1_transport.effective_product_category(fields)
    category = classify_sn_category(fields, effective_category=effective_category)
    caller = model_caller or v1_transport.call_model_with_retry
    activation_images = v1_transport._sn_only_activation_images(task)

    if category is SnCategory.UNSUPPORTED or not activation_images:
        model_result = _empty_evidence(category)
        decision = decide_sn(fields, model_result, effective_category=effective_category)
        decision, barcode_result = _apply_barcode_second_check(
            task,
            decision,
            activation_images,
            barcode_scanner=barcode_scanner,
            barcode_mode=barcode_mode,
        )
        row = _row_from_decision(
            task,
            decision,
            elapsed_sec=time.time() - started,
            sn_elapsed_sec=0.0,
            model_calls=0,
            total_tokens=0,
            screen_identity_state=model_result["screen_identity_state"],
            barcode_mode=barcode_mode,
        )
        raw = {"model_result": model_result, "model_raw": "", "usage": {}, "cached": False}
        if barcode_result is not None:
            raw["barcode_result"] = barcode_result
        return {"task": task, "row": row, "_raw": raw}

    prompt = build_sn_prompt(category)
    payload = build_model_payload(task, category, activation_images)
    prepared_images = v1_transport._with_detail(activation_images, "high")
    model_result, model_raw, model_elapsed, usage, cached = caller(
        base_url,
        api_key,
        model,
        prompt,
        payload,
        prepared_images,
        stage="sn_v2_evidence",
        cache_dir=cache_dir,
        detail="high",
        timeout_sec=max(0.1, float(timeout_sec)),
        retry_timeout_sec=0,
    )
    decision = decide_sn(
        fields,
        model_result,
        allowed_image_ids={str(image.get("image_id") or "") for image in activation_images},
        effective_category=effective_category,
    )
    decision, barcode_result = _apply_barcode_second_check(
        task,
        decision,
        activation_images,
        barcode_scanner=barcode_scanner,
        barcode_mode=barcode_mode,
    )
    total_tokens = int((usage or {}).get("total_tokens") or 0)
    row = _row_from_decision(
        task,
        decision,
        elapsed_sec=time.time() - started,
        sn_elapsed_sec=model_elapsed,
        model_calls=0 if cached else 1,
        total_tokens=total_tokens,
        screen_identity_state=str(model_result.get("screen_identity_state") or ""),
        barcode_mode=barcode_mode,
    )
    result = {
        "task": task,
        "row": row,
        "_raw": {
            "model_result": model_result,
            "model_raw": model_raw,
            "usage": usage,
            "cached": cached,
            "prompt_category": category.value,
        },
    }
    if barcode_result is not None:
        result["_raw"]["barcode_result"] = barcode_result
    return result


def _error_result(task: dict[str, Any], exc: Exception) -> dict[str, Any]:
    fields = task.get("fields") or {}
    effective_category = v1_transport.effective_product_category(fields)
    category = classify_sn_category(fields, effective_category=effective_category)
    decision = {
        "audit_category": category.value,
        "manual_required": True,
        "manual_reason_code": "MODEL_UNCERTAIN",
        "manual_reason": f"SN V2模型调用失败：{type(exc).__name__}",
        "system_sn": str(fields.get("system_sn") or ""),
        "observed_sn": "",
        "selected_source": "",
        "sn_match": False,
    }
    row = _row_from_decision(
        task,
        decision,
        elapsed_sec=0.0,
        sn_elapsed_sec=0.0,
        model_calls=0,
        total_tokens=0,
        screen_identity_state="",
        barcode_mode="off",
    )
    return {"task": task, "row": row, "_raw": {"error": traceback.format_exc()}}


def _audit_path(
    index: int,
    task_path: Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    cache_dir: Path | None,
    timeout_sec: float,
    barcode_mode: str,
) -> tuple[int, dict[str, Any]]:
    try:
        task = json.loads(task_path.read_text(encoding="utf-8-sig"))
        if not isinstance(task, dict):
            raise ValueError("task JSON must be an object")
    except Exception as exc:
        task = {
            "task_id": task_path.stem,
            "channel_order_no": task_path.stem,
            "fields": {},
        }
        return index, _error_result(task, exc)
    try:
        result = audit_task_sn_v2(
            base_url,
            api_key,
            model,
            task,
            cache_dir=cache_dir,
            timeout_sec=timeout_sec,
            barcode_mode=barcode_mode,
        )
    except Exception as exc:
        result = _error_result(task, exc)
    return index, result


def run_batch(
    tasks_dir: Path,
    output_path: Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    cache_dir: Path | None = None,
    timeout_sec: float = 60.0,
    workers: int = 1,
    barcode_mode: str = "shadow",
) -> Path:
    task_paths = sorted(tasks_dir.glob("*.json"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        pending_results: dict[int, dict[str, Any]] = {}
        next_index = 1
        completed_count = 0
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(
                    _audit_path,
                    index,
                    task_path,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    cache_dir=cache_dir,
                    timeout_sec=timeout_sec,
                    barcode_mode=barcode_mode,
                ): (index, task_path)
                for index, task_path in enumerate(task_paths, 1)
            }
            for future in as_completed(futures):
                expected_index, task_path = futures[future]
                try:
                    index, result = future.result()
                except Exception as exc:
                    index = expected_index
                    task = {
                        "task_id": task_path.stem,
                        "channel_order_no": task_path.stem,
                        "fields": {},
                    }
                    result = _error_result(task, exc)
                completed_count += 1
                pending_results[index] = result
                while next_index in pending_results:
                    handle.write(json.dumps(pending_results.pop(next_index), ensure_ascii=False) + "\n")
                    next_index += 1
                handle.flush()
                row = result["row"]
                print(
                    f"[{completed_count}/{len(task_paths)}] {row['id']} {row['manual_flag']} {row['manual_reason_code']}",
                    flush=True,
                )
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run isolated Guobu SN V2 evidence audit")
    parser.add_argument("--tasks-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--run-name", default="guobu_sn_v2")
    parser.add_argument("--model", default=os.environ.get("VISION_MODEL_NAME", "qwen3.7-plus"))
    parser.add_argument("--cache-dir", default="reports/model_audit/sn_v2_cache")
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--barcode-mode", choices=["off", "shadow", "enforce"], default="shadow")
    return parser.parse_args(argv)


def main() -> None:
    v1_transport.configure_utf8_stdio()
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(args.out_dir) / f"{args.run_name}_{stamp}.jsonl"
    run_batch(
        Path(args.tasks_dir),
        output_path,
        base_url=os.environ["VISION_API_BASE_URL"],
        api_key=os.environ["VISION_API_KEY"],
        model=args.model,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        timeout_sec=args.timeout_sec,
        workers=args.workers,
        barcode_mode=args.barcode_mode,
    )
    print(f"SN V2 result: {output_path}", flush=True)


if __name__ == "__main__":
    main()
