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
LIVE_CSV = BASE / f"{RUN}_live_orders_status.csv"
TOTAL_DEFAULT = 1199

REASON_MAP = {
    "SN_MISMATCH": "SN不一致",
    "SN_NOT_FOUND": "未识别到SN",
    "MODEL_UNCERTAIN": "模型无法确认",
    "PRODUCT_TYPE_MISMATCH": "商品类型不一致",
    "PRODUCT_PHOTO_INVALID": "商品照片不符合要求",
    "UNBOXING_PHOTO_INVALID": "拆封照片不符合要求",
    "ACTIVATION_PHOTO_INVALID": "激活/SN照片不符合要求",
    "IMAGE_STRONG_RISK": "图片存在强风险",
    "NON_REAL_PHOTO_REVIEW": "图片疑似非实拍",
    "DUPLICATE_IMAGE_EVIDENCE": "存在重复图片，不符合要求",
    "INVOICE_ORANGE_WARNING": "发票疑似已红冲",
}


def reason_text(code):
    code = str(code or "").strip()
    return REASON_MAP.get(code, code)


def short(text, width):
    text = "" if text is None else str(text).strip()
    return text if len(text) <= width else text[: width - 1] + "…"


def latest_jsonl():
    if not FIRST_DIR.exists():
        return None
    files = list(FIRST_DIR.glob("*.jsonl"))
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def task_total():
    if not TASKS_DIR.exists():
        return TOTAL_DEFAULT
    count = len(list(TASKS_DIR.glob("*.json")))
    return count or TOTAL_DEFAULT


def row_from_line(line):
    try:
        obj = json.loads(line)
        return obj.get("row") or {}
    except Exception:
        return None


def format_row(idx, row):
    code = str(row.get("manual_reason_code") or "").strip()
    result = "通过" if not code else "转人工"
    elapsed = row.get("elapsed_sec", "")
    try:
        elapsed = f"{float(elapsed):.1f}s"
    except Exception:
        elapsed = ""
    return (
        f"{idx:<5} "
        f"{short(row.get('id', ''), 24):<24} "
        f"{result:<8} "
        f"{short(reason_text(code), 18):<18} "
        f"{short(row.get('system_sn', ''), 24):<24} "
        f"{short(row.get('observed_sn', ''), 24):<24} "
        f"{elapsed:>8}"
    )


def rebuild_live_csv(rows):
    tmp = LIVE_CSV.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["序号", "订单号", "结果", "转人工原因", "原因码", "原始流程状态", "系统SN", "模型SN", "SN是否一致", "单单耗时秒"])
        for idx, row in enumerate(rows, start=1):
            code = str(row.get("manual_reason_code") or "").strip()
            writer.writerow(
                [
                    idx,
                    row.get("id", ""),
                    "通过" if not code else "转人工",
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


def main():
    os.system("chcp 65001 > nul")
    total = task_total()
    print("国补审核逐单日志监控")
    print(f"批次: {RUN}")
    print("显示方式: 完成一单追加一行，不清屏、不整屏刷新。")
    print("Ctrl+C 只关闭这个监控窗口，不会停止后台审核。")
    print("")
    print(f"{'序号':<5} {'订单号':<24} {'结果':<8} {'原因':<18} {'系统SN':<24} {'模型SN':<24} {'耗时':>8}")
    print("-" * 120)
    sys.stdout.flush()

    path = None
    while path is None:
        path = latest_jsonl()
        if path is None:
            print(f"[{time.strftime('%H:%M:%S')}] 等待结果文件出现……")
            sys.stdout.flush()
            time.sleep(3)

    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = row_from_line(line)
            if row is not None:
                rows.append(row)
        rebuild_live_csv(rows)
        done = len(rows)
        manual = sum(1 for r in rows if str(r.get("manual_reason_code") or "").strip())
        print(f"[{time.strftime('%H:%M:%S')}] 已接入结果文件：{path}")
        print(f"[{time.strftime('%H:%M:%S')}] 当前已完成 {done}/{total} 单，自动通过 {done - manual} 单，转人工 {manual} 单；下面开始只追加新增订单。")
        sys.stdout.flush()

        last_heartbeat = time.time()
        finished_notice = False
        while True:
            where = f.tell()
            line = f.readline()
            if not line:
                f.seek(where)
                now = time.time()
                if now - last_heartbeat >= 60:
                    print(f"[{time.strftime('%H:%M:%S')}] 等待下一单完成……当前 {len(rows)}/{total} 单")
                    sys.stdout.flush()
                    last_heartbeat = now
                if COMBINED_XLSX.exists() and not finished_notice:
                    print(f"[{time.strftime('%H:%M:%S')}] 最终Excel已生成：{COMBINED_XLSX}")
                    sys.stdout.flush()
                    finished_notice = True
                time.sleep(2)
                continue
            row = row_from_line(line.strip())
            if row is None:
                continue
            rows.append(row)
            idx = len(rows)
            print(format_row(idx, row))
            sys.stdout.flush()
            if idx % 10 == 0:
                rebuild_live_csv(rows)


if __name__ == "__main__":
    main()
