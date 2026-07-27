# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable


HEADERS = [
    "订单号",
    "V1数据状态",
    "V1是否转人工",
    "V1原因码",
    "V1模型SN",
    "V1_SN是否一致",
    "V2数据状态",
    "V2是否转人工",
    "V2原因码",
    "V2模型SN",
    "V2_SN是否一致",
    "V2品类",
    "结论是否变化",
]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            entries.append(value)
    return entries


def _row(entry: dict[str, Any]) -> dict[str, Any]:
    value = entry.get("row") or entry.get("result") or entry
    return value if isinstance(value, dict) else {}


def _order_id(entry: dict[str, Any]) -> str:
    row = _row(entry)
    task = entry.get("task") if isinstance(entry.get("task"), dict) else {}
    row_id = str(row.get("id") or "")
    task_order_id = str(task.get("channel_order_no") or "")
    task_id = str(task.get("task_id") or "")
    entry_order_id = str(entry.get("channel_order_no") or "")
    if row_id and task_order_id and row_id != task_order_id:
        raise ValueError(f"conflicting order IDs: row={row_id}, task={task_order_id}")
    if row_id and not task_order_id and task_id:
        equivalent_task_id = task_id == row_id or task_id.endswith(f"-{row_id}")
        if not equivalent_task_id:
            raise ValueError(f"conflicting order IDs: row={row_id}, task={task_id}")
    return row_id or task_order_id or task_id or entry_order_id


def _manual_flag(row: dict[str, Any]) -> str:
    if row.get("manual_flag") in {"是", "否"}:
        return str(row.get("manual_flag"))
    return "是" if row.get("manual_required") else "否"


def _sn_match_text(row: dict[str, Any]) -> str:
    value = row.get("sn_match")
    if value is True:
        return "是"
    if value is False:
        return "否"
    return ""


def _decision_signature(row: dict[str, Any]) -> tuple[str, str, str]:
    sn_match = _sn_match_text(row)
    reason_code = str(row.get("manual_reason_code") or "")
    if sn_match == "是" and reason_code == "SN_ONLY_MATCH_NOT_FULL_AUDIT":
        reason_code = ""
    observed_sn = re.sub(r"[^0-9A-Z]", "", str(row.get("observed_sn") or "").upper())
    return (
        sn_match,
        reason_code,
        observed_sn,
    )


def _index(entries: Iterable[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        order_id = _order_id(entry)
        if not order_id:
            raise ValueError(f"{label} contains a row without order ID")
        if order_id in indexed:
            raise ValueError(f"{label} contains duplicate order ID: {order_id}")
        indexed[order_id] = entry
    return indexed


def compare_result_sets(
    v1_entries: Iterable[dict[str, Any]],
    v2_entries: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    v1 = _index(v1_entries, "V1")
    v2 = _index(v2_entries, "V2")
    rows: list[dict[str, Any]] = []
    for order_id in sorted(set(v1) | set(v2)):
        v1_row = _row(v1[order_id]) if order_id in v1 else {}
        v2_row = _row(v2[order_id]) if order_id in v2 else {}
        both = bool(v1_row and v2_row)
        changed = "无法比较" if not both else ("是" if _decision_signature(v1_row) != _decision_signature(v2_row) else "否")
        rows.append(
            {
                "订单号": order_id,
                "V1数据状态": "存在" if v1_row else "缺失",
                "V1是否转人工": _manual_flag(v1_row) if v1_row else "",
                "V1原因码": str(v1_row.get("manual_reason_code") or ""),
                "V1模型SN": str(v1_row.get("observed_sn") or ""),
                "V1_SN是否一致": _sn_match_text(v1_row),
                "V2数据状态": "存在" if v2_row else "缺失",
                "V2是否转人工": _manual_flag(v2_row) if v2_row else "",
                "V2原因码": str(v2_row.get("manual_reason_code") or ""),
                "V2模型SN": str(v2_row.get("observed_sn") or ""),
                "V2_SN是否一致": _sn_match_text(v2_row),
                "V2品类": str(v2_row.get("audit_category") or ""),
                "结论是否变化": changed,
            }
        )
    return rows


def write_outputs(rows: list[dict[str, Any]], output_stem: Path) -> tuple[Path, Path]:
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output_stem.with_suffix(".csv")
    json_path = output_stem.with_suffix(".json")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, json_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Guobu SN V1 and V2 results by order ID")
    parser.add_argument("--v1-jsonl", required=True)
    parser.add_argument("--v2-jsonl", required=True)
    parser.add_argument("--out", required=True, help="Output path without extension")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    rows = compare_result_sets(load_jsonl(Path(args.v1_jsonl)), load_jsonl(Path(args.v2_jsonl)))
    csv_path, json_path = write_outputs(rows, Path(args.out))
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    main()
