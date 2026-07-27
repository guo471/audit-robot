import csv
import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

RUN = "guobu_20260723_204824_all1199_bg"
PROJECT = Path(r"C:\Users\HUAWEI\Desktop\audit_robot")
TASKS_DIR = PROJECT / "data" / "guobu_api_all_20260723_204824" / "tasks"
BASE = PROJECT / "reports" / "model_audit"
FIRST_DIR = BASE / f"{RUN}_first"
COMBINED_XLSX = BASE / f"{RUN}_combined.xlsx"
COMBINED_JSON = BASE / f"{RUN}_combined.json"
LIVE_CSV = BASE / f"{RUN}_live_orders_status.csv"
TOTAL_DEFAULT = 1199
SHOW_LAST = 45

REASON_MAP = {
    "SN_MISMATCH": "\u0053\u004e\u4e0d\u4e00\u81f4",
    "SN_NOT_FOUND": "\u672a\u8bc6\u522b\u5230\u0053\u004e",
    "MODEL_UNCERTAIN": "\u6a21\u578b\u65e0\u6cd5\u786e\u8ba4",
    "PRODUCT_TYPE_MISMATCH": "\u5546\u54c1\u7c7b\u578b\u4e0d\u4e00\u81f4",
    "PRODUCT_PHOTO_INVALID": "\u5546\u54c1\u7167\u7247\u4e0d\u7b26\u5408\u8981\u6c42",
    "UNBOXING_PHOTO_INVALID": "\u62c6\u5c01\u7167\u7247\u4e0d\u7b26\u5408\u8981\u6c42",
    "ACTIVATION_PHOTO_INVALID": "\u6fc0\u6d3b/\u0053\u004e\u7167\u7247\u4e0d\u7b26\u5408\u8981\u6c42",
    "IMAGE_STRONG_RISK": "\u56fe\u7247\u5b58\u5728\u5f3a\u98ce\u9669",
    "NON_REAL_PHOTO_REVIEW": "\u56fe\u7247\u7591\u4f3c\u975e\u5b9e\u62cd",
    "DUPLICATE_IMAGE_EVIDENCE": "\u5b58\u5728\u91cd\u590d\u56fe\u7247\uff0c\u4e0d\u7b26\u5408\u8981\u6c42",
    "INVOICE_ORANGE_WARNING": "\u53d1\u7968\u7591\u4f3c\u5df2\u7ea2\u51b2",
}


def short(text, width):
    text = "" if text is None else str(text).strip()
    return text if len(text) <= width else text[: max(0, width - 1)] + "\u2026"


def load_task_ids():
    ids = []
    if not TASKS_DIR.exists():
        return ids
    for path in sorted(TASKS_DIR.glob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            order_id = (
                obj.get("channel_order_no")
                or obj.get("task_id")
                or obj.get("id")
                or path.stem
            )
        except Exception:
            order_id = path.stem
        order_id = str(order_id)
        if order_id.startswith("guobu-api-"):
            order_id = order_id[len("guobu-api-") :]
        ids.append(order_id)
    return ids


def latest_jsonl():
    files = list(FIRST_DIR.glob("*.jsonl")) if FIRST_DIR.exists() else []
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def read_rows(path):
    rows = []
    if not path or not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                row = obj.get("row") or {}
                rows.append(row)
            except Exception:
                continue
    return rows


def reason_text(code):
    code = (code or "").strip()
    return REASON_MAP.get(code, code)


def export_live_csv(rows):
    tmp = LIVE_CSV.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "\u5e8f\u53f7",
                "\u8ba2\u5355\u53f7",
                "\u7ed3\u679c",
                "\u8f6c\u4eba\u5de5\u539f\u56e0",
                "\u539f\u56e0\u7801",
                "\u539f\u59cb\u6d41\u7a0b\u72b6\u6001",
                "\u7cfb\u7edfSN",
                "\u6a21\u578bSN",
                "SN\u662f\u5426\u4e00\u81f4",
                "\u5355\u5355\u8017\u65f6\u79d2",
            ]
        )
        for idx, row in enumerate(rows, start=1):
            code = str(row.get("manual_reason_code") or "").strip()
            result = "\u901a\u8fc7" if not code else "\u8f6c\u4eba\u5de5"
            writer.writerow(
                [
                    idx,
                    row.get("id", ""),
                    result,
                    reason_text(code),
                    code,
                    row.get("source_flow_status", ""),
                    row.get("system_sn", ""),
                    row.get("observed_sn", ""),
                    row.get("sn_match", ""),
                    row.get("elapsed_sec", ""),
                ]
            )
    tmp.replace(LIVE_CSV)


def clear_screen():
    os.system("cls")


def main():
    os.system("chcp 65001 > nul")
    task_ids = load_task_ids()
    total = len(task_ids) or TOTAL_DEFAULT
    generated_text = "\u5df2\u751f\u6210"
    not_generated_text = "\u5c1a\u672a\u751f\u6210"
    h_index = "\u5e8f\u53f7"
    h_order = "\u8ba2\u5355\u53f7"
    h_result = "\u7ed3\u679c"
    h_reason = "\u539f\u56e0"
    h_system_sn = "\u7cfb\u7edfSN"
    h_model_sn = "\u6a21\u578bSN"
    h_elapsed = "\u8017\u65f6"
    while True:
        path = latest_jsonl()
        rows = read_rows(path)
        export_live_csv(rows)
        done = len(rows)
        manual = sum(1 for r in rows if str(r.get("manual_reason_code") or "").strip())
        passed = done - manual
        pct = round(done * 100 / total, 1) if total else 0
        next_order = task_ids[done] if done < len(task_ids) else ""
        clear_screen()
        print("\u56fd\u8865\u5ba1\u6838\u9010\u5355\u5b9e\u65f6\u76d1\u63a7")
        print(f"\u6279\u6b21: {RUN}")
        print(f"\u65f6\u95f4: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(
            f"\u603b\u8fdb\u5ea6: {done}/{total} ({pct}%)    "
            f"\u81ea\u52a8\u901a\u8fc7: {passed}    \u8f6c\u4eba\u5de5: {manual}"
        )
        if next_order:
            print(f"\u9884\u8ba1\u6b63\u5728\u5904\u7406/\u4e0b\u4e00\u5355: {next_order}")
        if path:
            stat = path.stat()
            print(f"\u7ed3\u679c\u6587\u4ef6: {path}")
            print(f"\u6700\u540e\u66f4\u65b0: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))}")
        else:
            print("\u7ed3\u679c\u6587\u4ef6: \u5c1a\u672a\u51fa\u73b0")
        print(f"\u9010\u5355CSV: {LIVE_CSV}")
        excel_status = generated_text if COMBINED_XLSX.exists() else not_generated_text
        print(f"\u6700\u7ec8Excel: {excel_status}")
        print()
        print(f"\u6700\u8fd1\u5b8c\u6210\u7684 {SHOW_LAST} \u5355:")
        print(f"{h_index:<5} {h_order:<24} {h_result:<8} {h_reason:<18} {h_system_sn:<24} {h_model_sn:<24} {h_elapsed:>8}")
        print("-" * 120)
        start = max(0, len(rows) - SHOW_LAST)
        for idx, row in enumerate(rows[start:], start=start + 1):
            code = str(row.get("manual_reason_code") or "").strip()
            result = "\u901a\u8fc7" if not code else "\u8f6c\u4eba\u5de5"
            elapsed = row.get("elapsed_sec", "")
            try:
                elapsed = f"{float(elapsed):.1f}s"
            except Exception:
                elapsed = ""
            print(
                f"{idx:<5} "
                f"{short(row.get('id', ''), 24):<24} "
                f"{result:<8} "
                f"{short(reason_text(code), 18):<18} "
                f"{short(row.get('system_sn', ''), 24):<24} "
                f"{short(row.get('observed_sn', ''), 24):<24} "
                f"{elapsed:>8}"
            )
        print()
        print(
            "\u8bf4\u660e\uff1a\u6a21\u578b\u662f\u4e00\u5355\u5b8c\u6210\u540e\u624d\u5199\u5165\u7ed3\u679c\uff1b"
            "\u7a97\u53e3\u6bcf 5 \u79d2\u5237\u65b0\u3002Ctrl+C \u53ea\u5173\u95ed\u76d1\u63a7\uff0c\u4e0d\u4f1a\u505c\u6b62\u540e\u53f0\u5ba1\u6838\u3002"
        )
        time.sleep(5)


if __name__ == "__main__":
    main()
