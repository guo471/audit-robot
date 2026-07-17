from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo


NETWORK_MARKERS = (
    "timeouterror",
    "timed out",
    "modelconnectionerror",
    "connect failed",
    "winerror 10060",
    "http error 500",
)


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def network_failure(row: dict[str, Any]) -> bool:
    text = " ".join(
        str(row.get(key) or "")
        for key in ("manual_reason", "manual_reason_cn", "strategy")
    ).lower()
    return any(marker in text for marker in NETWORK_MARKERS)


def manual(row: dict[str, Any]) -> bool:
    return bool(str(row.get("manual_reason") or row.get("manual_reason_cn") or "").strip())


def number(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def integer(row: dict[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def reason_text(row: dict[str, Any]) -> str:
    return str(row.get("manual_reason_cn") or row.get("manual_reason") or "")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--first-jsonl", required=True)
    parser.add_argument("--second-jsonl")
    parser.add_argument("--output-xlsx", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    first_items = read_jsonl(Path(args.first_jsonl))
    second_items = read_jsonl(Path(args.second_jsonl)) if args.second_jsonl else []
    if not first_items:
        raise SystemExit("First-run JSONL is empty")

    second_by_id = {str(item["row"]["id"]): item for item in second_items}
    first_timeout_ids = {
        str(item["row"]["id"])
        for item in first_items
        if network_failure(item.get("row") or {})
    }

    rows: list[dict[str, Any]] = []
    for seq, first_item in enumerate(first_items, 1):
        first = first_item.get("row") or {}
        order_id = str(first.get("id") or first_item.get("task", {}).get("channel_order_no") or "")
        second_item = second_by_id.get(order_id) if order_id in first_timeout_ids else None
        final = (second_item or first_item).get("row") or {}
        first_elapsed = number(first, "elapsed_sec")
        second_elapsed = number(final, "elapsed_sec") if second_item else 0.0
        first_tokens = integer(first, "total_tokens")
        second_tokens = integer(final, "total_tokens") if second_item else 0
        rows.append(
            {
                "序号": seq,
                "订单号": order_id,
                "是否转人工": "是" if manual(final) else "否",
                "转人工原因码": str(final.get("manual_reason_code") or ""),
                "转人工原因": reason_text(final),
                "系统SN": str(final.get("system_sn") or ""),
                "模型SN": str(final.get("observed_sn") or ""),
                "原本流程状态": str(final.get("source_flow_status") or ""),
                "源审核状态": final.get("source_examine_status", ""),
                "源结算状态": final.get("source_settle_status", ""),
                "耗时(秒)": round(number(final, "elapsed_sec"), 2),
                "使用token量": integer(final, "total_tokens"),
                "结果来源": "第二轮网络重跑" if second_item else "第一次全量审核",
                "第一次是否网络失败": "是" if order_id in first_timeout_ids else "否",
                "第二次是否仍网络失败": "是" if second_item and network_failure(final) else ("否" if second_item else ""),
                "第一次转人工原因": reason_text(first),
                "第一次耗时(秒)": round(first_elapsed, 2),
                "第二次耗时(秒)": round(second_elapsed, 2) if second_item else "",
                "两轮合计耗时(秒)": round(first_elapsed + second_elapsed, 2),
                "第一次token": first_tokens,
                "第二次token": second_tokens if second_item else "",
                "两轮合计token": first_tokens + second_tokens,
                "策略": str(final.get("strategy") or ""),
                "置信度": final.get("confidence", ""),
            }
        )

    final_manual = sum(row["是否转人工"] == "是" for row in rows)
    second_still_timeout = sum(item["row"]["id"] in first_timeout_ids and network_failure(item["row"]) for item in second_items)
    reason_counts = Counter(
        row["转人工原因码"] if row["是否转人工"] == "是" and row["转人工原因码"] else "自动通过"
        for row in rows
    )
    summary = {
        "total": len(rows),
        "first_timeout": len(first_timeout_ids),
        "second_rerun": len(second_items),
        "second_resolved": len(second_items) - second_still_timeout,
        "second_still_timeout": second_still_timeout,
        "final_manual": final_manual,
        "final_auto": len(rows) - final_manual,
        "final_timeout": second_still_timeout,
        "final_tokens": sum(int(row["使用token量"]) for row in rows),
        "actual_tokens": sum(int(row["两轮合计token"]) for row in rows),
        "final_elapsed_seconds": round(sum(float(row["耗时(秒)"]) for row in rows), 2),
        "actual_elapsed_seconds": round(sum(float(row["两轮合计耗时(秒)"]) for row in rows), 2),
    }

    output_xlsx = Path(args.output_xlsx)
    output_json = Path(args.output_json)
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    detail = workbook.active
    detail.title = "明细表"
    headers = list(rows[0])
    detail.append(headers)
    for row in rows:
        detail.append([row[header] for header in headers])

    summary_sheet = workbook.create_sheet("汇总表")
    summary_sheet.append(["指标", "数值"])
    summary_labels = {
        "total": "样本总数",
        "first_timeout": "第一次网络失败转人工单数",
        "second_rerun": "第二轮重跑单数",
        "second_resolved": "第二轮已解决网络失败单数",
        "second_still_timeout": "第二轮仍网络失败单数",
        "final_manual": "综合后转人工总数",
        "final_auto": "综合后自动通过数",
        "final_timeout": "综合后网络失败转人工数",
        "final_tokens": "综合结果token合计",
        "actual_tokens": "两轮实际token合计",
        "final_elapsed_seconds": "综合结果耗时合计(秒)",
        "actual_elapsed_seconds": "两轮实际耗时合计(秒)",
    }
    for key, value in summary.items():
        summary_sheet.append([summary_labels[key], value])
    summary_sheet.append([])
    summary_sheet.append(["综合后转人工原因分布", "单数"])
    for code, count in reason_counts.most_common():
        summary_sheet.append([code, count])

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(name="Arial", bold=True, color="FFFFFF")
    thin = Side(style="thin", color="D9E2F3")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                cell.font = Font(name="Arial", size=10, bold=cell.font.bold, color=cell.font.color)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                cell.border = border
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

    widths = [8, 24, 12, 20, 55, 28, 28, 16, 12, 12, 12, 14, 18, 16, 18, 55, 16, 16, 18, 14, 14, 16, 24, 10]
    for index, width in enumerate(widths, 1):
        detail.column_dimensions[get_column_letter(index)].width = width
    summary_sheet.column_dimensions["A"].width = 36
    summary_sheet.column_dimensions["B"].width = 18

    table = Table(displayName="GuobuAuditDetail", ref=f"A1:{get_column_letter(detail.max_column)}{detail.max_row}")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True, showColumnStripes=False)
    detail.add_table(table)
    workbook.save(output_xlsx)

    output_json.write_text(
        json.dumps({"summary": summary, "reason_counts": dict(reason_counts)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    check = load_workbook(output_xlsx, read_only=True)
    if check["明细表"].max_row != len(rows) + 1:
        raise SystemExit("Workbook detail row count mismatch")
    if any("??" in value for sheet in check.worksheets for row in sheet.iter_rows(values_only=True) for value in row if isinstance(value, str)):
        raise SystemExit("Workbook contains question-mark encoding damage")

    print(json.dumps({"xlsx": str(output_xlsx), "json": str(output_json), "summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
