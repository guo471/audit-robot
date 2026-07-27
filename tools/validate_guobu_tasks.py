# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from run_guobu_model_audit_v2 import group_images_by_title, is_activation_title


def validate_task(path: Path) -> dict[str, Any]:
    task = json.loads(path.read_text(encoding="utf-8-sig"))
    groups = group_images_by_title(task)
    activation_titles = [title for title in groups if is_activation_title(title)]
    images_count = sum(len(images) for images in groups.values())
    issues: list[str] = []

    if not task.get("image_groups"):
        issues.append("MISSING_IMAGE_GROUPS: 仍是旧images结构，容易因图片顺序导致角色错配")
    if not activation_titles:
        issues.append("MISSING_ACTIVATION_GROUP: 缺少SN码采集/激活照片分组")
    if images_count == 0:
        issues.append("NO_IMAGES: 没有采集到图片")
    if task.get("images") and len(task.get("images") or []) == 3 and not task.get("image_groups"):
        issues.append("LEGACY_THREE_IMAGE_SHAPE: 疑似旧版固定三图采集")

    return {
        "id": task.get("channel_order_no") or path.stem,
        "file": str(path),
        "ok": not issues,
        "issues": issues,
        "group_counts": {title: len(images) for title, images in groups.items()},
        "activation_image_count": sum(len(groups[title]) for title in activation_titles),
        "total_images": images_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks-dir", required=True)
    args = parser.parse_args()

    results = [validate_task(path) for path in sorted(Path(args.tasks_dir).glob("*.json"))]
    issue_counter: Counter[str] = Counter()
    for result in results:
        for issue in result["issues"]:
            issue_counter[issue.split(":", 1)[0]] += 1

    print("COUNT=" + str(len(results)))
    print("OK=" + str(sum(1 for result in results if result["ok"])))
    print("ISSUES=" + json.dumps(dict(issue_counter), ensure_ascii=False))
    for result in results:
        status = "OK" if result["ok"] else "NG"
        print(f"{status} {result['id']} total={result['total_images']} activation={result['activation_image_count']} groups={result['group_counts']}")
        for issue in result["issues"]:
            print("  - " + issue)


if __name__ == "__main__":
    main()
