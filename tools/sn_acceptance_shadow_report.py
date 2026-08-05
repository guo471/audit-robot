# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_dataset(dataset_path: Path) -> list[dict[str, Any]]:
    data = json.loads(dataset_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("SN acceptance dataset must be a JSON array")
    for index, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise ValueError(f"dataset row {index} is not a JSON object")
    return data


def build_blocked_report(dataset_path: Path, *, reason: str) -> dict[str, Any]:
    records = _load_dataset(dataset_path)
    return {
        "status": "blocked",
        "old_new_comparison_ran": False,
        "blocked_reason": reason,
        "dataset_path": str(dataset_path),
        "dataset_records": len(records),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pass_to_manual_changes": [],
        "manual_to_pass_changes": [],
        "changed_orders": [],
        "note": "No old/new model outputs were available, so this report only proves the acceptance dataset was loaded.",
    }


def write_report(report: dict[str, Any], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write an SN acceptance shadow report when model comparison is blocked")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--reason", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_blocked_report(Path(args.dataset), reason=args.reason)
    out = write_report(report, Path(args.out))
    print(f"STATUS={report['status']}")
    print(f"DATASET_RECORDS={report['dataset_records']}")
    print(f"REPORT={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
