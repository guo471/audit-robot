"""Run the isolated black-edge detector against labeled image libraries."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from tools.black_edge_shadow_detector import scan_image


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _image_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            yield path


def build_manifest(
    real_manifest: Path,
    non_real_root: Path,
    extra_positive_paths: Iterable[Path] = (),
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    with real_manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle):
            filename = (record.get("文件名") or record.get("filename") or "").strip()
            if not filename:
                continue
            path = real_manifest.parent / filename
            if not path.is_file():
                continue
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "path": str(path),
                    "label": "real",
                    "split": "library",
                    "group_id": (record.get("订单号") or record.get("order_id") or filename).strip(),
                }
            )

    for path in _image_files(non_real_root):
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        group_id = path.name.split("_", 1)[0]
        rows.append({"path": str(path), "label": "non_real", "split": "library", "group_id": group_id})

    for path in extra_positive_paths:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "path": str(path),
                "label": "non_real",
                "split": "development_extra",
                "group_id": path.stem,
            }
        )
    return rows


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def compute_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for label, name in (("non_real", "non_real"), ("real", "real")):
        for split, split_name in (("library", "library"), ("development_extra", "development_extra")):
            subset = [row for row in rows if row.get("label") == label and row.get("split") == split]
            candidate_count = sum(row.get("status") != "none" for row in subset)
            strong_count = sum(row.get("status") == "strong_candidate" for row in subset)
            metrics[f"{name}_{split_name}"] = {
                "count": len(subset),
                "candidate_count": candidate_count,
                "strong_candidate_count": strong_count,
                "candidate_recall" if label == "non_real" else "candidate_rate": _rate(candidate_count, len(subset)),
                "strong_recall" if label == "non_real" else "strong_candidate_rate": _rate(strong_count, len(subset)),
            }
    metrics["status_counts"] = dict(Counter(row.get("status", "service_error") for row in rows))
    metrics["service_error_count"] = sum(row.get("status") == "service_error" for row in rows)
    return metrics


def run_evaluation(manifest: list[dict[str, str]], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for item in manifest:
        try:
            scan = scan_image(Path(item["path"]))
            result = {
                **item,
                "status": scan.status,
                "width": scan.width,
                "height": scan.height,
                "sides": {side: evidence.to_dict() for side, evidence in scan.sides.items()},
            }
        except Exception as exc:  # pragma: no cover - exercised by real file failures
            result = {**item, "status": "service_error", "error": f"{type(exc).__name__}: {exc}"}
        results.append(result)

    summary = {
        "image_count": len(results),
        "automatic_rejection_count": 0,
        "metrics": compute_metrics(results),
    }
    with (output_dir / "results.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")

    with (output_dir / "results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "label", "split", "group_id", "status", "width", "height", "sides", "error"])
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    **result,
                    "sides": json.dumps(result.get("sides", {}), ensure_ascii=False, sort_keys=True),
                }
            )

    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-manifest", type=Path, required=True)
    parser.add_argument("--non-real-root", type=Path, required=True)
    parser.add_argument("--extra-positive", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(args.real_manifest, args.non_real_root, args.extra_positive)
    summary = run_evaluation(manifest, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
