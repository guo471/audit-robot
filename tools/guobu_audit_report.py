"""Business display helpers for the Guobu audit report."""

from __future__ import annotations


_UNKNOWN_REASON = "图片信息无法确认"

_STANDARD_REASONS = {
    "PRODUCT_TYPE_MISMATCH": "商品类型不一致",
    "PRODUCT_PHOTO_INVALID": "商品照片不符合要求",
    "UNBOXING_PHOTO_INVALID": "拆封/安装照片不符合要求",
    "ACTIVATION_PHOTO_INVALID": "激活照片不符合要求",
    "SN_MISSING_IN_ACTIVATION_PHOTO": "激活照片不符合要求",
    "ADDRESS_TOO_COARSE": "收货地址不符合要求",
    "DUPLICATE_IMAGE_EVIDENCE": "存在重复图片，不符合要求",
    "NON_REAL_PHOTO_REVIEW": "图片疑似非实拍",
    "NON_REAL_PHOTO_STRONG_RISK": "图片疑似非实拍",
    "IMAGE_STRONG_RISK": "图片疑似非实拍",
    "SN_MISMATCH": "SN不一致",
    "INVOICE_ORANGE_WARNING": "发票疑似已红冲",
    "MODEL_UNCERTAIN": _UNKNOWN_REASON,
    "PHOTO_AUTHENTICITY_SERVICE_FAILURE": "审核服务异常",
    "ARTIFACT_LOAD_FAILURE": "审核服务异常",
    "FFT_FAILURE": "审核服务异常",
    "SN_TRUNCATED_OBSCURED": "SN不完整，无法识别",
    "SN_NOT_FOUND": "SN无法识别",
    "SYSTEM_SN_MISSING": "系统SN缺失",
    "IMAGE_MISSING": "图片缺失",
    "FIELD_MISSING": "订单信息缺失",
    "PRODUCT_TYPE_MISSING": "商品类型信息缺失",
    "NON_REAL_PHOTO_FFT_RESCUE": "图片疑似非实拍",
}


_NETWORK_FAILURE_MARKERS = ("timeouterror", "timed out", "modelconnectionerror",
                            "connect failed", "winerror 10060", "http error 500")


def network_failure(item: dict) -> bool:
    row = item.get("row") or {}
    values = [item.get("_error"), *(row.get(key) for key in ("manual_reason", "manual_reason_cn", "strategy"))]
    text = "\n".join(str(value) for value in values if value is not None).lower()
    return any(marker in text for marker in _NETWORK_FAILURE_MARKERS)


def _indexed(items: list[dict], label: str) -> dict[str, dict]:
    result = {}
    for item in items:
        order_id = str(((item.get("row") or {}).get("id")) or "").strip()
        if not order_id:
            raise ValueError(f"empty {label} order ID")
        if order_id in result:
            raise ValueError(f"duplicate {label} order ID: {order_id!r}")
        result[order_id] = item
    return result


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _usage_objects(item: dict):
    roots = []
    root_ids = set()
    if isinstance(item.get("_raw"), dict):
        roots.append(item["_raw"])
        root_ids.add(id(item["_raw"]))
    row_raw = (item.get("row") or {}).get("_raw")
    if isinstance(row_raw, dict) and id(row_raw) not in root_ids:
        roots.append(row_raw)

    def visit(container: dict):
        for key, value in container.items():
            if key.endswith("_usage") and isinstance(value, dict):
                stage = key.split("_usage", 1)[0]
                yield value, container.get(f"{stage}_cached") is True
            elif isinstance(value, dict):
                yield from visit(value)

    for root in roots:
        yield from visit(root)


def _account_attempt(item: dict, source: str) -> tuple[dict, dict]:
    totals = {key: 0 for key in ("logical_input_tokens", "logical_cached_input_tokens",
        "logical_output_tokens", "billed_input_tokens", "billed_cached_input_tokens",
        "billed_output_tokens")}
    for usage, stage_cached in _usage_objects(item):
        prompt = int(_number(usage.get("prompt_tokens")))
        details = usage.get("prompt_tokens_details") or {}
        cached_input = min(prompt, int(_number(details.get("cached_tokens"))))
        output = int(_number(usage.get("completion_tokens")))
        totals["logical_input_tokens"] += prompt
        totals["logical_cached_input_tokens"] += cached_input
        totals["logical_output_tokens"] += output
        if not stage_cached:
            totals["billed_input_tokens"] += prompt - cached_input
            totals["billed_cached_input_tokens"] += cached_input
            totals["billed_output_tokens"] += output
    row = item.get("row") or {}
    trace = {"source": source, "order_id": str(row.get("id") or "").strip(),
             "elapsed_seconds": _number(row.get("elapsed_sec")), "item": item, **totals}
    return trace, totals


def _validate_final(item: dict) -> tuple[bool, str]:
    row = item.get("row") or {}
    manual = parse_manual_flag(row.get("manual_flag"))
    code = str(row.get("manual_reason_code") or "").strip()
    reason = str(row.get("manual_reason") or row.get("manual_reason_cn") or "").strip()
    if not manual and (code or reason):
        raise ValueError("manual_flag false contradicts final reason")
    if manual and not code:
        raise ValueError("manual_flag true requires final primary reason code")
    return manual, code


def merge_attempts(first_items: list[dict], retry_items: list[dict],
                   retry_ids: set[str] | None = None) -> tuple[list[dict], dict]:
    first = _indexed(first_items, "first-run")
    retries = _indexed(retry_items, "retry")
    failures = {order_id for order_id, item in first.items() if network_failure(item)}
    retry_item_ids = set(retries)
    if retry_ids is not None:
        selected = {str(order_id).strip() for order_id in retry_ids}
        if selected != retry_item_ids:
            raise ValueError("retry selection mismatch")
        if selected - failures:
            raise ValueError("retry ID is not a first-run network failure")
    if retry_item_ids - failures:
        raise ValueError(f"unknown retry order ID: {sorted(retry_item_ids - failures)!r}")

    accounting = {"attempts": [], "elapsed_seconds": 0.0,
                  **{key: 0 for key in ("logical_input_tokens", "logical_cached_input_tokens",
                     "logical_output_tokens", "billed_input_tokens", "billed_cached_input_tokens",
                     "billed_output_tokens")}}
    for source, items in (("first", first_items), ("retry", retry_items)):
        for item in items:
            trace, totals = _account_attempt(item, source)
            accounting["attempts"].append(trace)
            accounting["elapsed_seconds"] += trace["elapsed_seconds"]
            for key, value in totals.items():
                accounting[key] += value

    merged = []
    for order_id, first_item in first.items():
        retry_item = retries.get(order_id)
        final_item = retry_item or first_item
        manual, code = _validate_final(final_item)
        merged.append({"row": final_item.get("row") or {}, "reason_code": code,
                       "reason": standard_reason(code), "manual": manual,
                       "final_source": "retry" if retry_item else "first",
                       "first_attempt": first_item, "retry_attempt": retry_item})
    return merged, accounting


def _rate(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None}


def build_summary(rows: list[dict], accounting: dict, prices: dict | None = None) -> dict:
    failed_denominator = failed_numerator = passed_denominator = passed_numerator = 0
    manual_total = 0
    for item in rows:
        row = item.get("row") or {}
        manual = bool(item.get("manual"))
        manual_total += int(manual)
        status = str(row.get("source_flow_status") or "").strip()
        if status == "\u672a\u901a\u8fc7":
            failed_denominator += 1
            failed_numerator += int(manual)
        elif status == "\u5df2\u901a\u8fc7":
            passed_denominator += 1
            passed_numerator += int(manual)

    effective_hours = _number(accounting.get("elapsed_seconds")) / 3600
    sample_count = len(rows)
    human_rate = 550 / 7.5
    human_hours = sample_count / human_rate
    model_rate = sample_count / effective_hours if effective_hours else None
    multiple = model_rate / human_rate if model_rate is not None else None
    required = ("input_per_million", "cached_input_per_million", "output_per_million")
    configured = prices is not None and all(isinstance(prices.get(key), (int, float))
        and not isinstance(prices.get(key), bool) for key in required)
    cost = "\u5f85\u914d\u7f6e"
    if configured:
        cost = (accounting.get("billed_input_tokens", 0) * prices["input_per_million"]
                + accounting.get("billed_cached_input_tokens", 0) * prices["cached_input_per_million"]
                + accounting.get("billed_output_tokens", 0) * prices["output_per_million"]) / 1_000_000
    return {"sample_count": sample_count, "manual_total": manual_total,
            "automatic_pass_total": sample_count - manual_total,
            "failed_interception": _rate(failed_numerator, failed_denominator),
            "passed_false_positive": _rate(passed_numerator, passed_denominator),
            "billed_input_tokens": accounting.get("billed_input_tokens", 0),
            "billed_cached_input_tokens": accounting.get("billed_cached_input_tokens", 0),
            "billed_output_tokens": accounting.get("billed_output_tokens", 0),
            "estimated_cost": cost, "effective_hours": effective_hours,
            "human_orders_per_hour": human_rate, "human_estimated_hours": human_hours,
            "model_orders_per_hour": model_rate, "efficiency_multiple": multiple,
            "efficiency_improvement": multiple - 1 if multiple is not None else None,
            "saved_human_hours": human_hours - effective_hours}


def standard_reason(code: str) -> str:
    """Return the approved business wording for a primary reason code."""
    if not code:
        return ""
    return _STANDARD_REASONS.get(code, _UNKNOWN_REASON)


def parse_manual_flag(value: object) -> bool:
    """Parse the only explicit values permitted for a final manual flag."""
    if type(value) is bool:
        return value
    if value == "是":
        return True
    if value == "否":
        return False
    raise ValueError(f"invalid manual_flag: {value!r}")


def _sn_value(value: object) -> str:
    return "" if value is None else str(value)


def _adjacent_transposition(system: str, observed: str) -> int | None:
    differences = [i for i, pair in enumerate(zip(system, observed)) if pair[0] != pair[1]]
    if len(differences) != 2:
        return None
    first, second = differences
    if second == first + 1 and system[first] == observed[second] and system[second] == observed[first]:
        return first
    return None


def _single_edit(longer: str, shorter: str) -> tuple[int, str] | None:
    for index in range(len(longer)):
        if longer[:index] + longer[index + 1 :] == shorter:
            return index, longer[index]
    return None


def _sn_difference(system: str, observed: str) -> str:
    if not observed:
        return "模型未读取到SN"
    if system == observed:
        return ""

    transposition = _adjacent_transposition(system, observed)
    if transposition is not None:
        end = transposition + 2
        return f"字符顺序不同：系统{system[transposition:end]}，模型{observed[transposition:end]}"

    if len(system) == len(observed):
        differences = [i for i, pair in enumerate(zip(system, observed)) if pair[0] != pair[1]]
        if len(differences) == 1:
            index = differences[0]
            return f"第{index + 1}位不同：系统{system[index]}，模型{observed[index]}"
        return "SN存在多处差异"

    if system.startswith(observed):
        return f"模型末尾少读{system[len(observed):]}"
    if system.endswith(observed):
        return f"模型开头少读{system[:-len(observed)]}"

    if len(observed) == len(system) + 1:
        edit = _single_edit(observed, system)
        if edit is not None:
            index, character = edit
            return f"模型第{index + 1}位多读{character}"
    if len(system) == len(observed) + 1:
        edit = _single_edit(system, observed)
        if edit is not None:
            index, character = edit
            return f"模型第{index + 1}位少读{character}"
    return "SN存在多处差异"


def sn_display(row: dict) -> tuple[str, str]:
    """Return the fixed status and raw-character difference for an audit row."""
    system = _sn_value(row.get("system_sn"))
    observed = _sn_value(row.get("observed_sn"))
    if row.get("sn_match") is True:
        return "是", ""
    if not system:
        return "无系统SN", ""
    if not observed:
        return "未读取", _sn_difference(system, observed)
    return "否", _sn_difference(system, observed)
