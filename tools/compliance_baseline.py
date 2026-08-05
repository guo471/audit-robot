# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import copy
import csv
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable
import urllib.parse
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import run_guobu_model_audit_v2 as v2
from tools import compliance_candidate_rules as candidate_rules


BASELINE_VERSION = "compliance-old-v1-20260731"
MODEL_NAME = "qwen3.7-plus"
MODEL_STAGE = "compliance_baseline_v1"
RULESETS = ("legacy", "candidate")
DATASET_FIELDS = (
    "渠道订单号",
    "订单品类/商品类型",
    "商品照片",
    "拆封/安装照片",
    "激活/SN照片",
    "原始流程状态",
)
PHOTO_KEYS = ("image_id", "title", "local_path", "source_url")
REQUIRED_MODEL_FIELDS = (
    "effective_category",
    "product_type_match",
    "product_photo_ok",
    "unboxing_photo_ok",
    "activation_photo_ok",
    "manual_required",
    "manual_reason_codes",
    "confidence",
)
COMPARISON_FIELDS = (
    "渠道订单号",
    "订单品类/商品类型",
    "原始流程状态",
    "旧版合规结论",
    "是否转人工",
    "原因码",
    "中文原因",
    "生效品类",
    "商品照片合格",
    "拆封/安装照片合格",
    "激活/SN照片合格",
    "激活证据类型",
    "结构异常",
    "服务失败",
    "模型调用次数",
    "总Tokens",
    "耗时秒",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_text_atomic(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="\n",
            prefix=path.name + ".",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_json(path: Path, value: Any) -> None:
    _write_text_atomic(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def _write_bytes_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=path.name + ".",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _safe_image(image: dict[str, Any]) -> dict[str, str]:
    return {key: str(image.get(key) or "") for key in PHOTO_KEYS}


def _images_for_role(groups: dict[str, Any], role: str) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    for title, images in groups.items():
        normalized_title = str(title or "")
        if role == "product":
            matches = "商品" in normalized_title and "激活" not in normalized_title
        elif role == "unboxing":
            matches = "拆封" in normalized_title or "安装" in normalized_title
        else:
            matches = "激活" in normalized_title or "SN" in normalized_title.upper()
        if not matches or not isinstance(images, list):
            continue
        selected.extend(_safe_image(image) for image in images if isinstance(image, dict))
    return selected


def build_dataset_record(task: dict[str, Any]) -> dict[str, Any]:
    fields = task.get("fields") or {}
    groups = task.get("image_groups") or {}
    status = fields.get(
        "source_flow_status",
        fields.get("flow_status", fields.get("status", "")),
    )
    return {
        "渠道订单号": str(task.get("channel_order_no") or ""),
        "订单品类/商品类型": str(
            fields.get("product_type") or fields.get("cate_code_name") or ""
        ),
        "商品照片": _images_for_role(groups, "product"),
        "拆封/安装照片": _images_for_role(groups, "unboxing"),
        "激活/SN照片": _images_for_role(groups, "activation"),
        "原始流程状态": str(status or ""),
    }


def _validate_dataset_record(record: dict[str, Any]) -> None:
    if tuple(record) != DATASET_FIELDS:
        raise ValueError(f"dataset fields must be exactly {DATASET_FIELDS}")
    if not str(record["渠道订单号"]).strip():
        raise ValueError("channel order is missing")
    if not str(record["订单品类/商品类型"]).strip():
        raise ValueError(f"product type is missing for {record['渠道订单号']}")
    if not str(record["原始流程状态"]).strip():
        raise ValueError(f"source flow status is missing for {record['渠道订单号']}")
    for key in ("商品照片", "拆封/安装照片", "激活/SN照片"):
        if not isinstance(record[key], list):
            raise ValueError(f"{key} must be a list for {record['渠道订单号']}")


def write_dataset(
    source_dir: Path,
    output_path: Path,
    *,
    exclude_order_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    excluded = {str(order_id) for order_id in exclude_order_ids}
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task_path in sorted(Path(source_dir).glob("*.json")):
        task = json.loads(task_path.read_text(encoding="utf-8"))
        record = build_dataset_record(task)
        _validate_dataset_record(record)
        order_id = record["渠道订单号"]
        if order_id in seen:
            raise ValueError(f"duplicate channel order: {order_id}")
        seen.add(order_id)
        if order_id not in excluded:
            records.append(record)
    body = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    _write_text_atomic(Path(output_path), body)
    return records


def load_dataset(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        record = json.loads(raw_line)
        _validate_dataset_record(record)
        order_id = record["渠道订单号"]
        if order_id in seen:
            raise ValueError(f"duplicate channel order at line {line_number}: {order_id}")
        seen.add(order_id)
        records.append(record)
    return records


def _download_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "audit-robot-baseline/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def recover_missing_local_images(
    record: dict[str, Any],
    recovery_dir: Path,
    *,
    fetch_bytes: Any = _download_bytes,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    recovered = copy.deepcopy(record)
    events: list[dict[str, Any]] = []
    safe_order_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(record["渠道订单号"]))
    for field in ("商品照片", "拆封/安装照片", "激活/SN照片"):
        for image in recovered[field]:
            local_path = Path(str(image.get("local_path") or ""))
            if str(local_path) and local_path.is_file():
                continue
            source_url = str(image.get("source_url") or "").strip()
            if not source_url:
                continue
            image_id = re.sub(
                r"[^A-Za-z0-9_-]", "_", str(image.get("image_id") or "image")
            )
            suffix = Path(urllib.parse.urlparse(source_url).path).suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                suffix = ".img"
            target = Path(recovery_dir) / safe_order_id / f"{image_id}{suffix}"
            if target.is_file():
                content = target.read_bytes()
            else:
                content = bytes(fetch_bytes(source_url))
                if not content:
                    raise ValueError(f"empty recovered image for {record['渠道订单号']} {image_id}")
                _write_bytes_atomic(target, content)
            image["local_path"] = str(target.resolve())
            image["source_url"] = ""
            events.append(
                {
                    "field": field,
                    "image_id": str(image.get("image_id") or ""),
                    "source_url_sha256": _sha256_bytes(source_url.encode("utf-8")),
                    "recovered_path": str(target.resolve()),
                    "content_sha256": _sha256_bytes(content),
                }
            )
    return recovered, events


def missing_local_images(record: dict[str, Any]) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    for field in ("商品照片", "拆封/安装照片", "激活/SN照片"):
        for image in record[field]:
            local_path = str(image.get("local_path") or "")
            if local_path and Path(local_path).is_file():
                continue
            source_url = str(image.get("source_url") or "")
            missing.append(
                {
                    "field": field,
                    "image_id": str(image.get("image_id") or ""),
                    "source_scheme": urllib.parse.urlparse(source_url).scheme,
                }
            )
    return missing


def validate_disjoint(
    test_records: Iterable[dict[str, Any]],
    acceptance_records: Iterable[dict[str, Any]],
) -> None:
    test_ids = {str(record["渠道订单号"]) for record in test_records}
    acceptance_ids = {str(record["渠道订单号"]) for record in acceptance_records}
    overlap = sorted(test_ids & acceptance_ids)
    if overlap:
        raise ValueError(f"test/acceptance overlap: {overlap[:10]}")


def _record_to_task(record_or_task: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if "渠道订单号" not in record_or_task:
        record = build_dataset_record(record_or_task)
    else:
        record = dict(record_or_task)
    _validate_dataset_record(record)
    groups = {
        "商品照片": record["商品照片"],
        "拆封照片": record["拆封/安装照片"],
        "SN码采集/激活照片": record["激活/SN照片"],
    }
    task = {
        "channel_order_no": record["渠道订单号"],
        "fields": {"product_type": record["订单品类/商品类型"]},
        "image_groups": groups,
    }
    return record, task


def _candidate_labeled_images(
    groups: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    display_names = {
        "商品照片": "商品照片",
        "拆封照片": "拆封/安装照片",
        "SN码采集/激活照片": "激活/SN照片",
    }
    labeled: list[dict[str, Any]] = []
    for group_name, images in groups.items():
        display_name = display_names.get(group_name, group_name)
        for image in images:
            item = dict(image)
            image_id = str(item.get("image_id") or "").strip() or "unknown"
            item["_prompt_label"] = f"【{display_name}｜{image_id}】"
            labeled.append(item)
    return labeled


def _local_manual_result(
    record: dict[str, Any],
    *,
    code: str,
    reason: str,
    category: str,
    service_failure: bool = False,
    input_recoveries: list[dict[str, Any]] | None = None,
    input_missing_images: list[dict[str, str]] | None = None,
    model_calls: int = 0,
    baseline_version: str = BASELINE_VERSION,
    structure_anomaly: bool = False,
    missing_model_fields: list[str] | None = None,
    invalid_model_fields: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "渠道订单号": record["渠道订单号"],
        "订单品类/商品类型": record["订单品类/商品类型"],
        "原始流程状态": record["原始流程状态"],
        "baseline_version": baseline_version,
        "effective_category": category,
        "decision": "manual_review",
        "manual_required": True,
        "manual_reason_codes": [code],
        "manual_reason": reason,
        "manual_reason_cn": v2.build_chinese_reason([code], reason),
        "product_type_match": "",
        "product_photo_ok": "",
        "unboxing_photo_ok": "",
        "activation_photo_ok": "",
        "activation_evidence_type": "",
        "structure_anomaly": structure_anomaly,
        "missing_model_fields": missing_model_fields or [],
        "invalid_model_fields": invalid_model_fields or [],
        "service_failure": service_failure,
        "model_calls": model_calls,
        "cached": False,
        "elapsed_sec": 0.0,
        "total_tokens": 0,
        "prompt_sha256": "",
        "prompt_character_count": 0,
        "raw_model_result": {},
        "raw_model_text": "",
        "input_recoveries": input_recoveries or [],
        "input_missing_images": input_missing_images or [],
    }


def _candidate_runtime_failure_result(
    record: dict[str, Any],
    *,
    category: str,
    reason: str,
    model_calls: int,
    raw_model_result: Any = None,
    raw_model_text: str = "",
    prompt: str = "",
    elapsed: float = 0.0,
    usage: dict[str, Any] | None = None,
    input_recoveries: list[dict[str, Any]] | None = None,
    input_missing_images: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    result = _local_manual_result(
        record,
        code="MODEL_UNCERTAIN",
        reason=reason,
        category=category,
        service_failure=True,
        model_calls=model_calls,
        baseline_version=candidate_rules.CANDIDATE_VERSION,
        structure_anomaly=True,
        invalid_model_fields=["$runtime"],
        input_recoveries=input_recoveries,
        input_missing_images=input_missing_images,
    )
    try:
        total_tokens = int((usage or {}).get("total_tokens") or 0)
    except (TypeError, ValueError, OverflowError):
        total_tokens = 0
    result.update(
        {
            "raw_model_result": raw_model_result,
            "raw_model_text": raw_model_text,
            "prompt_sha256": (
                _sha256_bytes(prompt.encode("utf-8")) if prompt else ""
            ),
            "prompt_character_count": len(prompt),
            "elapsed_sec": round(float(elapsed), 3),
            "total_tokens": total_tokens,
        }
    )
    return result


def _image_is_usable_by_model(image: dict[str, Any]) -> bool:
    if str(image.get("source_url") or image.get("url") or "").strip():
        return True
    local_path = str(image.get("local_path") or "").strip()
    if not local_path:
        return False
    try:
        return Path(local_path).is_file()
    except OSError:
        return False


def _candidate_model_result(
    record: dict[str, Any],
    *,
    category: str,
    prompt: str,
    raw_model_result: Any,
    raw_model_text: str,
    elapsed: float,
    usage: dict[str, Any],
    cached: bool,
    input_recoveries: list[dict[str, Any]],
    input_missing_images: list[dict[str, str]],
) -> dict[str, Any]:
    validation = candidate_rules.validate_candidate_response(
        category,
        record["订单品类/商品类型"],
        raw_model_result,
        unboxing_image_ids=tuple(
            str(image.get("image_id") or "").strip()
            for image in record["拆封/安装照片"]
        ),
    )
    reason_codes = validation["manual_reason_codes"]
    manual_required = validation["manual_required"]
    raw_decision = raw_model_result if isinstance(raw_model_result, dict) else {}
    if validation["structure_anomaly"]:
        details = []
        if validation["missing_model_fields"]:
            details.append(
                "缺失字段: " + ", ".join(validation["missing_model_fields"])
            )
        if validation["invalid_model_fields"]:
            details.append(
                "无效字段: " + ", ".join(validation["invalid_model_fields"])
            )
        reason = "候选合规模型输出结构异常"
        if details:
            reason += "（" + "；".join(details) + "）"
    elif manual_required:
        reason = str(raw_decision.get("evidence_summary") or "")
    else:
        reason = ""
    return {
        "渠道订单号": record["渠道订单号"],
        "订单品类/商品类型": record["订单品类/商品类型"],
        "原始流程状态": record["原始流程状态"],
        "baseline_version": candidate_rules.CANDIDATE_VERSION,
        "effective_category": category,
        "model_effective_category": "",
        "decision": "manual_review" if manual_required else "pass",
        "manual_required": manual_required,
        "manual_reason_codes": reason_codes if manual_required else [],
        "manual_reason": reason,
        "manual_reason_cn": (
            v2.build_chinese_reason(reason_codes, reason) if manual_required else ""
        ),
        "product_type_match": raw_decision.get("product_type_match", ""),
        "product_photo_ok": raw_decision.get("product_photo_ok", ""),
        "unboxing_photo_ok": validation["effective_unboxing_photo_ok"],
        "unboxing_image_evidence": raw_decision.get("unboxing_image_evidence", ""),
        "package_visible": validation["package_visible"],
        "whole_product_visible": validation["whole_product_visible"],
        "product_and_package_same_image": validation[
            "product_and_package_same_image"
        ],
        "home_or_installation_scene_visible": validation[
            "home_or_installation_scene_visible"
        ],
        "activation_photo_ok": raw_decision.get("activation_photo_ok", ""),
        "activation_evidence_type": raw_decision.get(
            "activation_evidence_type", ""
        ),
        "structure_anomaly": validation["structure_anomaly"],
        "missing_model_fields": validation["missing_model_fields"],
        "invalid_model_fields": validation["invalid_model_fields"],
        "local_corrections": validation["local_corrections"],
        "service_failure": False,
        "model_calls": 0 if cached else 1,
        "cached": bool(cached),
        "elapsed_sec": round(float(elapsed), 3),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
        "prompt_character_count": len(prompt),
        "raw_model_result": raw_model_result,
        "raw_model_text": raw_model_text,
        "input_recoveries": input_recoveries,
        "input_missing_images": input_missing_images,
    }


def audit_record(
    record_or_task: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    model: str = MODEL_NAME,
    cache_dir: Path | None,
    timeout_sec: float = 60.0,
    input_recovery_dir: Path | None = None,
    ruleset: str = "legacy",
) -> dict[str, Any]:
    if ruleset not in RULESETS:
        raise ValueError(f"unsupported ruleset: {ruleset}")
    baseline_version = (
        BASELINE_VERSION
        if ruleset == "legacy"
        else candidate_rules.CANDIDATE_VERSION
    )
    record, task = _record_to_task(record_or_task)
    input_recoveries: list[dict[str, Any]] = []
    input_missing_images = missing_local_images(record)
    category = v2.effective_product_category(task["fields"])
    if input_recovery_dir is not None:
        try:
            record, input_recoveries = recover_missing_local_images(
                record, input_recovery_dir
            )
        except Exception as exc:
            return _local_manual_result(
                record,
                code="MODEL_UNCERTAIN",
                reason=f"输入恢复失败: {type(exc).__name__}: {str(exc)[:240]}",
                category=category,
                service_failure=True,
                input_missing_images=input_missing_images,
                baseline_version=baseline_version,
            )
        record, task = _record_to_task(record)
    fields = task["fields"]
    groups = task["image_groups"]
    category = v2.effective_product_category(fields)
    activation_images = groups["SN码采集/激活照片"]
    if ruleset == "candidate":
        if category not in candidate_rules.PROMPTS:
            return _local_manual_result(
                record,
                code="MODEL_UNCERTAIN",
                reason="候选合规无法识别订单品类",
                category=category,
                input_recoveries=input_recoveries,
                input_missing_images=input_missing_images,
                baseline_version=baseline_version,
            )
        if not candidate_rules.product_subtype_for_category(
            category, record["订单品类/商品类型"]
        ):
            return _local_manual_result(
                record,
                code="MODEL_UNCERTAIN",
                reason="候选合规无法确定具体商品类型",
                category=category,
                input_recoveries=input_recoveries,
                input_missing_images=input_missing_images,
                baseline_version=baseline_version,
            )
        required_groups = {
            "商品照片": groups["商品照片"],
            "拆封/安装照片": groups["拆封照片"],
        }
        if category in {"ordinary_3c", "computer"}:
            required_groups["激活/SN照片"] = activation_images
        missing_groups = [
            name
            for name, images in required_groups.items()
            if not any(_image_is_usable_by_model(image) for image in images)
        ]
        if missing_groups:
            return _local_manual_result(
                record,
                code="MODEL_UNCERTAIN",
                reason="候选合规缺少必需图片组: " + ", ".join(missing_groups),
                category=category,
                input_recoveries=input_recoveries,
                input_missing_images=input_missing_images,
                baseline_version=baseline_version,
            )
    if ruleset == "legacy" and not activation_images:
        return _local_manual_result(
            record,
            code="ACTIVATION_PHOTO_INVALID",
            reason="激活/SN照片缺失",
            category=category,
            input_recoveries=input_recoveries,
            input_missing_images=input_missing_images,
            baseline_version=baseline_version,
        )

    if ruleset == "legacy":
        prompt = v2.compliance_prompt_for_category(
            category,
            product_type=record["订单品类/商品类型"],
            include_photo_authenticity=False,
            digital_activation_evidence_mode="on",
        )
        model_stage = MODEL_STAGE
    else:
        prompt = candidate_rules.prompt_for_category(category)
        model_stage = candidate_rules.CANDIDATE_STAGE
    all_images = (
        _candidate_labeled_images(groups)
        if ruleset == "candidate"
        else v2.flatten_image_groups(groups)
    )
    payload = {
        "id": record["渠道订单号"],
        "product_type": record["订单品类/商品类型"],
        "category_name": record["订单品类/商品类型"],
        "effective_category": category,
        "is_home_appliance": category == "home_appliance",
        "address_ok": None,
        "sn_match": True,
        "observed_sn": "",
        "raw_observed_sn": "",
        "visual_sn_ambiguity": False,
        "image_groups": {
            title: [
                {
                    "image_id": image.get("image_id"),
                    "title": image.get("title"),
                    "url": image.get("source_url"),
                }
                for image in images
            ]
            for title, images in groups.items()
        },
    }
    model_call_options = {
        "stage": model_stage,
        "cache_dir": cache_dir,
        "detail": "auto",
        "timeout_sec": timeout_sec,
        "retry_timeout_sec": timeout_sec,
    }
    if ruleset == "candidate":
        model_call_options["allow_non_object"] = True
    try:
        decision, raw_text, elapsed, usage, cached = v2.call_model_with_retry(
            base_url,
            api_key,
            model,
            prompt,
            payload,
            all_images,
            **model_call_options,
        )
    except Exception as exc:
        if ruleset == "candidate" and isinstance(exc, json.JSONDecodeError):
            return _candidate_model_result(
                record,
                category=category,
                prompt=prompt,
                raw_model_result=None,
                raw_model_text=exc.doc,
                elapsed=0.0,
                usage={},
                cached=False,
                input_recoveries=input_recoveries,
                input_missing_images=input_missing_images,
            )
        return _local_manual_result(
            record,
            code="MODEL_UNCERTAIN",
            reason=f"合规模型服务异常: {type(exc).__name__}: {str(exc)[:240]}",
            category=category,
            service_failure=True,
            input_recoveries=input_recoveries,
            input_missing_images=input_missing_images,
            model_calls=1,
            baseline_version=baseline_version,
        )

    if ruleset == "candidate":
        try:
            return _candidate_model_result(
                record,
                category=category,
                prompt=prompt,
                raw_model_result=decision,
                raw_model_text=raw_text,
                elapsed=elapsed,
                usage=usage,
                cached=cached,
                input_recoveries=input_recoveries,
                input_missing_images=input_missing_images,
            )
        except Exception as exc:
            return _candidate_runtime_failure_result(
                record,
                category=category,
                reason=(
                    "候选合规模型答卷处理异常: "
                    f"{type(exc).__name__}: {str(exc)[:240]}"
                ),
                model_calls=0 if cached else 1,
                raw_model_result=decision,
                raw_model_text=raw_text,
                prompt=prompt,
                elapsed=elapsed,
                usage=usage,
                input_recoveries=input_recoveries,
                input_missing_images=input_missing_images,
            )

    raw_decision = dict(decision) if isinstance(decision, dict) else {}
    missing_fields = [field for field in REQUIRED_MODEL_FIELDS if field not in raw_decision]
    model_effective_category = raw_decision.get("effective_category", "")
    decision["model_effective_category"] = model_effective_category
    decision["product_type"] = record["订单品类/商品类型"]
    decision["category_name"] = record["订单品类/商品类型"]
    decision["effective_category"] = category
    decision["is_home_appliance"] = category == "home_appliance"
    decision["_sn_already_verified_by_system"] = True
    decision["digital_activation_evidence_mode"] = "on"
    decision["_activation_image_ids"] = [
        str(image.get("image_id") or "") for image in activation_images
    ]
    decision["_exact_duplicate_image_groups"] = v2.exact_duplicate_image_groups(groups)
    decision = v2.enforce_photo_noncompliance_manual(decision, address_ok=None)
    reason_codes = v2.as_codes(decision.get("manual_reason_codes"))
    manual_required = v2.as_bool(decision.get("manual_required"))
    reason = str(decision.get("manual_reason") or "") if manual_required else ""
    return {
        "渠道订单号": record["渠道订单号"],
        "订单品类/商品类型": record["订单品类/商品类型"],
        "原始流程状态": record["原始流程状态"],
        "baseline_version": baseline_version,
        "effective_category": category,
        "model_effective_category": model_effective_category,
        "decision": "manual_review" if manual_required else "pass",
        "manual_required": manual_required,
        "manual_reason_codes": reason_codes if manual_required else [],
        "manual_reason": reason,
        "manual_reason_cn": (
            v2.build_chinese_reason(reason_codes, reason) if manual_required else ""
        ),
        "product_type_match": decision.get("product_type_match", ""),
        "product_photo_ok": decision.get("product_photo_ok", ""),
        "unboxing_photo_ok": decision.get("unboxing_photo_ok", ""),
        "activation_photo_ok": decision.get("activation_photo_ok", ""),
        "activation_evidence_type": decision.get("activation_evidence_type", ""),
        "structure_anomaly": bool(missing_fields),
        "missing_model_fields": missing_fields,
        "invalid_model_fields": [],
        "service_failure": False,
        "model_calls": 0 if cached else 1,
        "cached": bool(cached),
        "elapsed_sec": round(float(elapsed), 3),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
        "prompt_character_count": len(prompt),
        "raw_model_result": raw_decision,
        "raw_model_text": raw_text,
        "input_recoveries": input_recoveries,
        "input_missing_images": input_missing_images,
    }


def validate_result_coverage(
    records: Iterable[dict[str, Any]], results: Iterable[dict[str, Any]]
) -> None:
    expected = [str(record["渠道订单号"]) for record in records]
    result_ids = [str(result["渠道订单号"]) for result in results]
    duplicates = sorted(
        order_id for order_id, count in Counter(result_ids).items() if count > 1
    )
    if duplicates:
        raise ValueError(f"duplicate result: {duplicates[:10]}")
    missing = sorted(set(expected) - set(result_ids))
    extra = sorted(set(result_ids) - set(expected))
    if missing:
        raise ValueError(f"missing result: {missing[:10]}")
    if extra:
        raise ValueError(f"unexpected result: {extra[:10]}")


def pending_records(
    records: Iterable[dict[str, Any]],
    existing: dict[str, dict[str, Any]],
    *,
    retry_service_failures: bool,
) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if record["渠道订单号"] not in existing
        or (
            retry_service_failures
            and bool(existing[record["渠道订单号"]].get("service_failure"))
        )
    ]


def _dataset_manifest(
    *,
    name: str,
    role: str,
    source_dir: Path,
    source_count: int,
    records: list[dict[str, Any]],
    output_path: Path,
    excluded_overlap_ids: list[str],
) -> dict[str, Any]:
    statuses = Counter(record["原始流程状态"] for record in records)
    categories = Counter(record["订单品类/商品类型"] for record in records)
    missing_groups = Counter()
    missing_local_images = 0
    for record in records:
        for field in ("商品照片", "拆封/安装照片", "激活/SN照片"):
            if not record[field]:
                missing_groups[field] += 1
            for image in record[field]:
                local_path = str(image.get("local_path") or "")
                if not local_path or not Path(local_path).is_file():
                    missing_local_images += 1
    unstable_statuses = {"审核中", "待我领取", "待审核", "处理中"}
    return {
        "schema_version": 1,
        "dataset_name": name,
        "role": role,
        "created_at_utc": _utc_now(),
        "source_dir": str(Path(source_dir).resolve()),
        "source_file_count": source_count,
        "output_order_count": len(records),
        "schema_fields": list(DATASET_FIELDS),
        "orders_jsonl": str(output_path.resolve()),
        "orders_jsonl_sha256": _sha256_file(output_path),
        "excluded_overlap_ids": excluded_overlap_ids,
        "status_distribution": dict(sorted(statuses.items())),
        "category_distribution": dict(sorted(categories.items())),
        "issues": {
            "unstable_original_status_count": sum(
                count for status, count in statuses.items() if status in unstable_statuses
            ),
            "missing_group_order_counts": dict(missing_groups),
            "missing_local_image_count": missing_local_images,
            "all_orders_single_original_status": len(statuses) == 1,
        },
    }


def _git_value(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def build_freeze_manifest(latest_run_manifest: Path | None = None) -> dict[str, Any]:
    prompt_entries: dict[str, Any] = {}
    for category in ("home_appliance", "ordinary_3c", "computer", "unknown"):
        product_type = "[B01] 手机" if category == "ordinary_3c" else ""
        prompt = v2.compliance_prompt_for_category(
            category,
            product_type=product_type,
            include_photo_authenticity=False,
            digital_activation_evidence_mode="on",
        )
        prompt_entries[category] = {
            "sha256": _sha256_bytes(prompt.encode("utf-8")),
            "character_count": len(prompt),
            "digital_activation_evidence_mode": "on",
            "digital_activation_plugin_included": (
                "普通3C激活证据统一口径插件" in prompt
            ),
            "photo_authenticity_addendum_included": False,
        }
    latest: dict[str, Any] = {}
    if latest_run_manifest and Path(latest_run_manifest).is_file():
        latest = json.loads(Path(latest_run_manifest).read_text(encoding="utf-8"))
    status_text = _git_value(["status", "--porcelain", "--untracked-files=no"])
    runtime_paths = (
        PROJECT_ROOT / "tools" / "run_guobu_model_audit_v2.py",
        PROJECT_ROOT / "tools" / "run_guobu_audit_batch.ps1",
        PROJECT_ROOT / "prompts" / "digital_activation_evidence_review.txt",
    )
    return {
        "baseline_version": BASELINE_VERSION,
        "frozen_at_utc": _utc_now(),
        "scope": "compliance_detection_only",
        "repository": {
            "path": str(PROJECT_ROOT),
            "branch": _git_value(["branch", "--show-current"]),
            "commit": _git_value(["rev-parse", "HEAD"]),
            "tracked_worktree_dirty": bool(status_text),
        },
        "runtime_sha256": {
            str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): _sha256_file(path)
            for path in runtime_paths
        },
        "source_defaults": {
            "production_entry": "tools/run_guobu_audit_batch.ps1",
            "audit_entry": "tools/run_guobu_model_audit_v2.py",
            "model": MODEL_NAME,
            "mode": "hybrid",
            "enable_thinking": False,
            "sn_policy_version": "v1",
            "digital_activation_evidence_mode": "on",
            "photo_authenticity_mode": "enforce",
            "photo_authenticity_new_rule_enabled": "true",
            "photo_authenticity_local_tree_enabled": "true",
            "photo_authenticity_local_tree_confirmation_enabled": "false",
            "sn_label_auth_review_mode": "off",
            "photo_auth_edge_mapping_mode": "off",
        },
        "latest_auditable_run": latest,
        "isolated_old_compliance_baseline": {
            "model": MODEL_NAME,
            "stage": MODEL_STAGE,
            "mode": "compliance_only_old",
            "model_calls_per_order": "0_if_local_missing_activation_else_1",
            "enable_thinking": False,
            "upstream_sn_assumption": "passed; no SN model call and no SN comparison",
            "address_gate": "excluded because address is outside the fixed dataset contract",
            "digital_activation_evidence_mode": "on",
            "photo_authenticity": "excluded; no addendum, local tree, edge mapping, or confirmation",
            "report_writeback": "local artifacts only; no backend status writeback",
        },
        "prompts": prompt_entries,
        "known_baseline_issues": [
            "All category prompts exceed the compliance constitution limit of 400 characters.",
            "Core compliance prompts still contain image-authenticity and invoice rules.",
            "Ordinary 3C/computer missing-field behavior depends partly on model structure.",
            "Computer same-photo SN/package evidence is not mechanically verifiable.",
            "The latest auditable runtime switches differ from current source defaults.",
        ],
    }


def _freeze_checklist(manifest: dict[str, Any]) -> str:
    prompts = manifest["prompts"]
    latest = manifest.get("latest_auditable_run") or {}
    return f"""# 合规检测冻结基线

- 基线版本：`{manifest['baseline_version']}`
- 仓库分支：`{manifest['repository']['branch']}`
- 仓库 commit：`{manifest['repository']['commit']}`
- 工作树：`{'dirty' if manifest['repository']['tracked_worktree_dirty'] else 'clean'}`
- 生产默认模式：`hybrid`
- 模型：`{MODEL_NAME}`，`enable_thinking=false`
- 合规隔离基线：仅一次合规模型调用；不调用 SN；不调用图片真实性；不回写后台
- 普通 3C 数字激活证据插件：`on`
- 最近可审计真实运行：`{latest.get('run_name', 'missing')}`
- 最近可审计真实性新版总开关：`{latest.get('photo_authenticity_new_rule_enabled', 'missing')}`

## 提示词冻结

| 品类 | SHA-256 | 字符数 |
|---|---|---:|
| 家电 | `{prompts['home_appliance']['sha256']}` | {prompts['home_appliance']['character_count']} |
| 普通3C | `{prompts['ordinary_3c']['sha256']}` | {prompts['ordinary_3c']['character_count']} |
| 电脑 | `{prompts['computer']['sha256']}` | {prompts['computer']['character_count']} |
| unknown | `{prompts['unknown']['sha256']}` | {prompts['unknown']['character_count']} |

## 已知问题

- 单品类提示词均超过 400 字宪法限制。
- 合规主提示词仍混有图片真实性与发票规则，本轮只冻结记录，不修改。
- 代码默认真实性开关与最近可审计回滚运行不一致，不能混称为同一运行版本。
- 478 单中存在未终态原始状态；759 单原始状态全为未通过，验收分布不均衡。
"""


def prepare_artifacts(
    *,
    test_source: Path,
    acceptance_source: Path,
    output_root: Path,
    latest_run_manifest: Path | None = None,
) -> dict[str, Any]:
    output_root = Path(output_root)
    freeze_dir = output_root / "freeze"
    test_dir = output_root / "test_set"
    acceptance_dir = output_root / "acceptance_set"
    manifest = build_freeze_manifest(latest_run_manifest)
    _write_json(freeze_dir / "baseline_manifest.json", manifest)
    _write_text_atomic(freeze_dir / "baseline_checklist.md", _freeze_checklist(manifest))

    test_output = test_dir / "orders.jsonl"
    acceptance_output = acceptance_dir / "orders.jsonl"
    test_records = write_dataset(test_source, test_output)
    test_ids = {record["渠道订单号"] for record in test_records}
    acceptance_source_tasks = sorted(Path(acceptance_source).glob("*.json"))
    acceptance_source_ids = {
        str(json.loads(path.read_text(encoding="utf-8")).get("channel_order_no") or "")
        for path in acceptance_source_tasks
    }
    overlap = sorted(test_ids & acceptance_source_ids)
    acceptance_records = write_dataset(
        acceptance_source,
        acceptance_output,
        exclude_order_ids=overlap,
    )
    validate_disjoint(test_records, acceptance_records)
    test_manifest = _dataset_manifest(
        name="compliance_test_478",
        role="test",
        source_dir=test_source,
        source_count=len(list(Path(test_source).glob("*.json"))),
        records=test_records,
        output_path=test_output,
        excluded_overlap_ids=[],
    )
    acceptance_manifest = _dataset_manifest(
        name="compliance_acceptance_757",
        role="acceptance",
        source_dir=acceptance_source,
        source_count=len(acceptance_source_tasks),
        records=acceptance_records,
        output_path=acceptance_output,
        excluded_overlap_ids=overlap,
    )
    _write_json(test_dir / "manifest.json", test_manifest)
    _write_json(acceptance_dir / "manifest.json", acceptance_manifest)
    return {
        "freeze_manifest": str((freeze_dir / "baseline_manifest.json").resolve()),
        "test_set": test_manifest,
        "acceptance_set": acceptance_manifest,
    }


def _load_partial_results(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    results: dict[str, dict[str, Any]] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        result = json.loads(raw_line)
        order_id = str(result.get("渠道订单号") or "")
        if not order_id:
            raise ValueError(f"result line {line_number} has no channel order")
        if order_id in results:
            raise ValueError(f"duplicate result: {order_id}")
        results[order_id] = result
    return results


def _comparison_row(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "渠道订单号": result["渠道订单号"],
        "订单品类/商品类型": result["订单品类/商品类型"],
        "原始流程状态": result["原始流程状态"],
        "旧版合规结论": result["decision"],
        "是否转人工": "是" if result["manual_required"] else "否",
        "原因码": ";".join(result.get("manual_reason_codes") or []),
        "中文原因": result.get("manual_reason_cn") or "",
        "生效品类": result.get("effective_category") or "",
        "商品照片合格": result.get("product_photo_ok", ""),
        "拆封/安装照片合格": result.get("unboxing_photo_ok", ""),
        "激活/SN照片合格": result.get("activation_photo_ok", ""),
        "激活证据类型": result.get("activation_evidence_type", ""),
        "结构异常": result.get("structure_anomaly", False),
        "服务失败": result.get("service_failure", False),
        "模型调用次数": result.get("model_calls", 0),
        "总Tokens": result.get("total_tokens", 0),
        "耗时秒": result.get("elapsed_sec", 0),
    }


def _baseline_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    versions = {str(result.get("baseline_version") or "") for result in results}
    if len(versions) != 1:
        raise ValueError(f"mixed baseline versions: {sorted(versions)}")
    baseline_version = next(iter(versions))
    decisions = Counter(result["decision"] for result in results)
    reasons = Counter(
        code
        for result in results
        for code in (result.get("manual_reason_codes") or [])
    )
    statuses = Counter(result["原始流程状态"] for result in results)
    service_failure_orders = [
        {
            "渠道订单号": result["渠道订单号"],
            "订单品类/商品类型": result["订单品类/商品类型"],
            "原因": result.get("manual_reason") or "",
            "缺失图片": result.get("input_missing_images") or [],
        }
        for result in results
        if result.get("service_failure")
    ]
    structure_anomaly_orders = [
        {
            "渠道订单号": result["渠道订单号"],
            "订单品类/商品类型": result["订单品类/商品类型"],
            "旧版合规结论": result["decision"],
            "缺失模型字段": result.get("missing_model_fields") or [],
        }
        for result in results
        if result.get("structure_anomaly")
    ]
    return {
        "baseline_version": baseline_version,
        "completed_at_utc": _utc_now(),
        "order_count": len(results),
        "decision_counts": dict(sorted(decisions.items())),
        "manual_reason_code_counts": dict(reasons.most_common()),
        "original_status_distribution": dict(sorted(statuses.items())),
        "structure_anomaly_count": sum(
            bool(result.get("structure_anomaly")) for result in results
        ),
        "service_failure_count": sum(
            bool(result.get("service_failure")) for result in results
        ),
        "service_failure_orders": service_failure_orders,
        "structure_anomaly_orders": structure_anomaly_orders,
        "model_call_count": sum(int(result.get("model_calls") or 0) for result in results),
        "cache_hit_count": sum(bool(result.get("cached")) for result in results),
        "total_tokens": sum(int(result.get("total_tokens") or 0) for result in results),
        "total_model_elapsed_sec": round(
            sum(float(result.get("elapsed_sec") or 0) for result in results), 3
        ),
        "interpretation_limit": (
            "原始流程状态可能包含其他阶段结论；本报告只固化本次合规输出，"
            "不得把两者差异直接解释为合规准确率。"
        ),
    }


def _baseline_report(summary: dict[str, Any]) -> str:
    decisions = summary["decision_counts"]
    service_lines = "\n".join(
        f"- `{item['渠道订单号']}`：{item['原因']}"
        for item in summary["service_failure_orders"]
    ) or "- 无"
    structure_lines = "\n".join(
        f"- `{item['渠道订单号']}`：旧版结论 `{item['旧版合规结论']}`，缺失字段 "
        + ", ".join(item["缺失模型字段"])
        for item in summary["structure_anomaly_orders"]
    ) or "- 无"
    report_name = (
        "旧版合规基线报告"
        if summary["baseline_version"] == BASELINE_VERSION
        else "候选合规基线报告"
    )
    result_name = "旧版合规" if summary["baseline_version"] == BASELINE_VERSION else "候选合规"
    return f"""# {report_name}

- 基线版本：`{summary['baseline_version']}`
- 验收订单：{summary['order_count']}
- {result_name}通过：{decisions.get('pass', 0)}
- {result_name}转人工：{decisions.get('manual_review', 0)}
- 结构异常：{summary['structure_anomaly_count']}
- 服务失败：{summary['service_failure_count']}
- 逻辑模型调用（重试请求不单列）：{summary['model_call_count']}
- 缓存命中：{summary['cache_hit_count']}
- 总 Tokens：{summary['total_tokens']}

## 口径

- 每单最多一次 `qwen3.7-plus` 合规模型调用，`enable_thinking=false`。
- SN 视为上游已通过，本轮不调用 SN 模型、不比较 SN。
- 图片真实性插件、边缘映射、本地树和确认调用全部排除。
- 规则集版本：`{summary['baseline_version']}`。
- 输出只写本地文件，不回写后台订单状态。

## 解释限制

{summary['interpretation_limit']}

## 服务失败订单

{service_lines}

## 结构异常订单

{structure_lines}
"""


def _candidate_prompt_hashes() -> dict[str, str]:
    return {
        category: _sha256_bytes(
            candidate_rules.prompt_for_category(category).encode("utf-8")
        )
        for category in candidate_rules.PROMPTS
    }


def _candidate_runtime_hashes() -> dict[str, str]:
    runtime_paths = {
        "tools/compliance_baseline.py": Path(__file__),
        "tools/compliance_candidate_rules.py": Path(candidate_rules.__file__),
        "tools/run_guobu_model_audit_v2.py": Path(v2.__file__),
    }
    return {
        name: _sha256_file(path)
        for name, path in runtime_paths.items()
    }


def _validate_candidate_resume_contract(
    manifest_path: Path,
    current_manifest: dict[str, Any],
) -> None:
    if not manifest_path.is_file():
        raise ValueError(
            "candidate resume contract mismatch: run_manifest.json is missing"
        )
    try:
        previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "candidate resume contract mismatch: run_manifest.json is unreadable"
        ) from exc

    contract_fields = (
        "baseline_version",
        "ruleset",
        "dataset_sha256",
        "dataset_order_count",
        "model",
        "stage",
        "runtime_sha256",
        "candidate_runtime_sha256",
        "candidate_prompt_sha256",
    )
    mismatches = [
        field
        for field in contract_fields
        if previous_manifest.get(field) != current_manifest.get(field)
    ]
    if mismatches:
        raise ValueError(
            "candidate resume contract mismatch: " + ", ".join(mismatches)
        )


def run_baseline(
    *,
    dataset_path: Path,
    output_dir: Path,
    cache_dir: Path,
    base_url: str,
    api_key: str,
    model: str = MODEL_NAME,
    workers: int = 4,
    timeout_sec: float = 60.0,
    retry_service_failures: bool = False,
    input_recovery_dir: Path | None = None,
    ruleset: str = "legacy",
) -> dict[str, Any]:
    if ruleset not in RULESETS:
        raise ValueError(f"unsupported ruleset: {ruleset}")
    baseline_version = (
        BASELINE_VERSION
        if ruleset == "legacy"
        else candidate_rules.CANDIDATE_VERSION
    )
    model_stage = (
        MODEL_STAGE
        if ruleset == "legacy"
        else candidate_rules.CANDIDATE_STAGE
    )
    records = load_dataset(dataset_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.jsonl"
    existing = _load_partial_results(results_path)
    expected_ids = {record["渠道订单号"] for record in records}
    unexpected = sorted(set(existing) - expected_ids)
    if unexpected:
        raise ValueError(f"unexpected result: {unexpected[:10]}")
    mixed_versions = sorted(
        {
            str(result.get("baseline_version") or "")
            for result in existing.values()
            if str(result.get("baseline_version") or "") != baseline_version
        }
    )
    if mixed_versions:
        raise ValueError(
            f"output directory contains a different ruleset: {mixed_versions}"
        )
    run_manifest = {
        "baseline_version": baseline_version,
        "ruleset": ruleset,
        "started_at_utc": _utc_now(),
        "dataset_path": str(Path(dataset_path).resolve()),
        "dataset_sha256": _sha256_file(Path(dataset_path)),
        "dataset_order_count": len(records),
        "model": model,
        "stage": model_stage,
        "workers": workers,
        "timeout_sec": timeout_sec,
        "enable_thinking": False,
        "digital_activation_evidence_mode": "on" if ruleset == "legacy" else "off",
        "sn_model_calls": 0,
        "photo_authenticity_model_calls": 0,
        "backend_writeback": False,
        "retry_service_failures": retry_service_failures,
        "input_recovery_dir": (
            str(Path(input_recovery_dir).resolve()) if input_recovery_dir else ""
        ),
        "runtime_sha256": _sha256_file(Path(__file__)),
    }
    if ruleset == "candidate":
        run_manifest["candidate_runtime_sha256"] = _candidate_runtime_hashes()
        run_manifest["candidate_prompt_sha256"] = _candidate_prompt_hashes()
        if existing:
            _validate_candidate_resume_contract(
                output_dir / "run_manifest.json", run_manifest
            )
    retry_history = [
        result for result in existing.values() if bool(result.get("service_failure"))
    ] if retry_service_failures else []
    if retry_history:
        history_path = output_dir / "service_failure_retry_history.jsonl"
        with history_path.open("a", encoding="utf-8", newline="\n") as handle:
            for result in retry_history:
                handle.write(
                    json.dumps(
                        {"retried_at_utc": _utc_now(), "previous_result": result},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        retry_ids = {result["渠道订单号"] for result in retry_history}
        existing = {
            order_id: result
            for order_id, result in existing.items()
            if order_id not in retry_ids
        }
        _write_text_atomic(
            results_path,
            "".join(
                json.dumps(existing[record["渠道订单号"]], ensure_ascii=False) + "\n"
                for record in records
                if record["渠道订单号"] in existing
            ),
        )

    _write_json(output_dir / "run_manifest.json", run_manifest)

    pending = pending_records(
        records,
        existing,
        retry_service_failures=False,
    )
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(
                    audit_record,
                    record,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    cache_dir=cache_dir,
                    timeout_sec=timeout_sec,
                    input_recovery_dir=input_recovery_dir,
                    ruleset=ruleset,
                ): record
                for record in pending
            }
            for completed_count, future in enumerate(as_completed(futures), 1):
                source_record = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    if ruleset != "candidate":
                        raise
                    category = v2.effective_product_category(
                        {"product_type": source_record["订单品类/商品类型"]}
                    )
                    result = _candidate_runtime_failure_result(
                        source_record,
                        reason=(
                            "候选合规单单审核异常: "
                            f"{type(exc).__name__}: {str(exc)[:240]}"
                        ),
                        category=category,
                        model_calls=1,
                    )
                order_id = result["渠道订单号"]
                existing[order_id] = result
                with results_path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                    handle.flush()
                print(
                    f"[{completed_count}/{len(pending)}] {order_id} -> {result['decision']}",
                    flush=True,
                )

    ordered_results = [existing[record["渠道订单号"]] for record in records]
    validate_result_coverage(records, ordered_results)
    _write_text_atomic(
        results_path,
        "".join(
            json.dumps(result, ensure_ascii=False) + "\n" for result in ordered_results
        ),
    )
    comparison_path = output_dir / "comparison_baseline.csv"
    with comparison_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COMPARISON_FIELDS)
        writer.writeheader()
        writer.writerows(_comparison_row(result) for result in ordered_results)
    summary = _baseline_summary(ordered_results)
    _write_json(output_dir / "summary.json", summary)
    _write_text_atomic(output_dir / "baseline_report.md", _baseline_report(summary))
    run_manifest["completed_at_utc"] = _utc_now()
    run_manifest["result_order_count"] = len(ordered_results)
    run_manifest["results_sha256"] = _sha256_file(results_path)
    run_manifest["comparison_sha256"] = _sha256_file(comparison_path)
    _write_json(output_dir / "run_manifest.json", run_manifest)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--test-source", required=True, type=Path)
    prepare.add_argument("--acceptance-source", required=True, type=Path)
    prepare.add_argument("--output-root", required=True, type=Path)
    prepare.add_argument("--latest-run-manifest", type=Path)

    run = subparsers.add_parser("run")
    run.add_argument("--dataset", required=True, type=Path)
    run.add_argument("--output-dir", required=True, type=Path)
    run.add_argument("--cache-dir", required=True, type=Path)
    run.add_argument("--model", default=MODEL_NAME, choices=[MODEL_NAME])
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--timeout-sec", type=float, default=60.0)
    run.add_argument("--retry-service-failures", action="store_true")
    run.add_argument("--recover-missing-local-images", type=Path)
    run.add_argument("--ruleset", choices=RULESETS, default="legacy")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "prepare":
        result = prepare_artifacts(
            test_source=args.test_source,
            acceptance_source=args.acceptance_source,
            output_root=args.output_root,
            latest_run_manifest=args.latest_run_manifest,
        )
    else:
        base_url = os.environ.get("VISION_API_BASE_URL", "").strip()
        api_key = os.environ.get("VISION_API_KEY", "").strip()
        if not base_url or not api_key:
            raise SystemExit("VISION_API_BASE_URL and VISION_API_KEY are required")
        result = run_baseline(
            dataset_path=args.dataset,
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
            base_url=base_url,
            api_key=api_key,
            model=args.model,
            workers=args.workers,
            timeout_sec=args.timeout_sec,
            retry_service_failures=args.retry_service_failures,
            input_recovery_dir=args.recover_missing_local_images,
            ruleset=args.ruleset,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
