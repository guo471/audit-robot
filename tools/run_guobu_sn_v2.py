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
    SnCategory,
    build_model_payload,
    build_sn_prompt,
    classify_sn_category,
    decide_sn,
)


ModelCaller = Callable[..., tuple[dict[str, Any], str, float, dict[str, Any], bool]]


def _empty_evidence(category: SnCategory) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "sn_readable": False,
        "screen_identity_state": "NOT_APPLICABLE" if category is SnCategory.HOME_APPLIANCE else "NO_SCREEN_SN",
        "sn_candidates": [],
        "identity_evidence": [],
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
        "elapsed_sec": round(elapsed_sec, 4),
        "sn_elapsed_sec": round(sn_elapsed_sec, 4),
        "strategy": "sn_v2_sidecar",
        "model_calls": model_calls,
        "total_tokens": total_tokens,
    }


def audit_task_sn_v2(
    base_url: str,
    api_key: str,
    model: str,
    task: dict[str, Any],
    *,
    model_caller: ModelCaller | None = None,
    cache_dir: Path | None = None,
    timeout_sec: float = 60.0,
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
        row = _row_from_decision(
            task,
            decision,
            elapsed_sec=time.time() - started,
            sn_elapsed_sec=0.0,
            model_calls=0,
            total_tokens=0,
            screen_identity_state=model_result["screen_identity_state"],
        )
        return {"task": task, "row": row, "_raw": {"model_result": model_result, "model_raw": "", "usage": {}, "cached": False}}

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
    total_tokens = int((usage or {}).get("total_tokens") or 0)
    row = _row_from_decision(
        task,
        decision,
        elapsed_sec=time.time() - started,
        sn_elapsed_sec=model_elapsed,
        model_calls=0 if cached else 1,
        total_tokens=total_tokens,
        screen_identity_state=str(model_result.get("screen_identity_state") or ""),
    )
    return {
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
    )
    print(f"SN V2 result: {output_path}", flush=True)


if __name__ == "__main__":
    main()
