# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DATASET_ALLOWED_FIELDS = {
    "channel_order_no",
    "system_sn",
    "source_flow_status",
    "activation_sn_images",
}

REPORT_HEADERS = [
    "channel_order_no",
    "order_type_category",
    "old_sn_result",
    "new_sn_result",
    "diff_reason",
    "final_recommendation",
]


@dataclass
class BuildResult:
    records: list[dict[str, Any]]
    issues: list[dict[str, str]]
    scanned_files: int


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_activation_sn_title(title: str) -> bool:
    normalized = title.lower()
    markers = ("sn", "s/n", "serial", "序列号", "激活", "activate")
    return any(marker in normalized for marker in markers)


def _activation_images(task: dict[str, Any]) -> list[dict[str, str]]:
    projected: list[dict[str, str]] = []
    groups = task.get("image_groups") if isinstance(task.get("image_groups"), dict) else {}
    for title, images in groups.items():
        if not _is_activation_sn_title(_text(title)) or not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict):
                continue
            source_url = _text(image.get("source_url") or image.get("url"))
            local_path = _text(image.get("local_path") or image.get("path"))
            if not source_url and not local_path:
                continue
            item: dict[str, str] = {}
            image_id = _text(image.get("image_id") or image.get("id"))
            if image_id:
                item["image_id"] = image_id
            if source_url:
                item["source_url"] = source_url
            if local_path:
                item["local_path"] = local_path
            projected.append(item)
    return projected


def _issue(path: Path, channel_order_no: str, code: str) -> dict[str, str]:
    return {
        "file": str(path),
        "channel_order_no": channel_order_no,
        "code": code,
    }


def _record_from_task(path: Path, task: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    fields = task.get("fields") if isinstance(task.get("fields"), dict) else {}
    channel_order_no = _text(task.get("channel_order_no") or fields.get("channel_order_no") or path.stem)
    system_sn = _text(task.get("system_sn") or fields.get("system_sn"))
    source_flow_status = _text(
        task.get("source_flow_status")
        or fields.get("source_flow_status")
        or fields.get("flow_status")
        or fields.get("status")
    )
    activation_sn_images = _activation_images(task)

    if not system_sn:
        return None, _issue(path, channel_order_no, "MISSING_SYSTEM_SN")
    if not source_flow_status:
        return None, _issue(path, channel_order_no, "MISSING_SOURCE_FLOW_STATUS")
    if not activation_sn_images:
        return None, _issue(path, channel_order_no, "MISSING_ACTIVATION_SN_IMAGE")

    record = {
        "channel_order_no": channel_order_no,
        "system_sn": system_sn,
        "source_flow_status": source_flow_status,
        "activation_sn_images": activation_sn_images,
    }
    extra = set(record) - DATASET_ALLOWED_FIELDS
    if extra:
        raise AssertionError(f"dataset record has unexpected fields: {sorted(extra)}")
    return record, None


def build_dataset(tasks_dir: Path, *, limit: int | None = None) -> BuildResult:
    paths = sorted(tasks_dir.glob("*.json"))
    if limit is not None and limit > 0:
        paths = paths[:limit]
    records: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for path in paths:
        try:
            task = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            issues.append(_issue(path, path.stem, "INVALID_JSON"))
            continue
        if not isinstance(task, dict):
            issues.append(_issue(path, path.stem, "TASK_JSON_NOT_OBJECT"))
            continue
        record, issue = _record_from_task(path, task)
        if issue:
            issues.append(issue)
        elif record:
            records.append(record)
    return BuildResult(records=records, issues=issues, scanned_files=len(paths))


def write_dataset(records: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report_template(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(REPORT_HEADERS)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an SN acceptance dataset from local task JSON files")
    parser.add_argument("--tasks-dir", required=True, help="Directory containing collected task JSON files")
    parser.add_argument("--out", required=True, help="Output dataset JSON path")
    parser.add_argument("--report-template", help="Optional CSV template for old/new SN comparison reports")
    parser.add_argument("--issues-out", help="Optional JSON path for skipped-record issues")
    parser.add_argument("--limit", type=int, default=0, help="Maximum task JSON files to scan; 0 means all")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_dataset(Path(args.tasks_dir), limit=args.limit or None)
    write_dataset(result.records, Path(args.out))
    if args.report_template:
        write_report_template(Path(args.report_template))
    if args.issues_out:
        write_dataset(result.issues, Path(args.issues_out))
    print(f"SCANNED={result.scanned_files}")
    print(f"RECORDS={len(result.records)}")
    print(f"ISSUES={len(result.issues)}")
    print(f"DATASET={args.out}")
    if args.report_template:
        print(f"REPORT_TEMPLATE={args.report_template}")
    if args.issues_out:
        print(f"ISSUES_OUT={args.issues_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
