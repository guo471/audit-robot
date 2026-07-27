# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from modules.code_extractor import CodeExtractor


CSV_PATH = Path(r"C:\audit_robot\reports\sn_batch_test_results.csv")


def main() -> int:
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    matched = 0
    newly_matched = []
    still_missed = []
    for row in rows:
        texts = [
            {"text": value, "confidence": 0.99, "box": None}
            for value in (row["recognized_sn"] or "").split(";")
            if value
        ]
        result = CodeExtractor.match_system_sn(texts, row["expected_sn"])
        if result["sn_match"]:
            matched += 1
            if str(row["matched"]).lower() != "true":
                newly_matched.append((row["file_name"], result["match_type"], row["recognized_sn"]))
        else:
            still_missed.append((row["file_name"], row["recognized_sn"]))

    print(f"total={len(rows)}")
    print(f"new_matched={matched}")
    print(f"new_hit_rate={matched / len(rows):.4f}")
    print("newly_matched:")
    for name, match_type, recognized in newly_matched:
        print(f"- {name} | {match_type} | {recognized}")
    print("still_missed:")
    for name, recognized in still_missed:
        print(f"- {name} | {recognized}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
