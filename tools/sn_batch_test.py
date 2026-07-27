# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from modules.code_extractor import CodeExtractor
from modules.ocr_engine import OCREngine


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def normalize(value: str) -> str:
    return CodeExtractor._normalize_sn(value)


def classify_reason(expected: str, found_values: list[str], matched: bool) -> str:
    if matched:
        return "已命中"

    expected_norm = normalize(expected)
    found_norms = [normalize(value) for value in found_values if value]
    if not found_norms:
        return "无SN候选"
    if any(value in expected_norm or expected_norm in value for value in found_norms):
        return "局部片段但未达放行条件"
    if any(value.startswith(("BCD", "XQB", "GB")) or "GB21455" in value for value in found_norms):
        return "型号/标准号干扰"
    if any(value.isdigit() for value in found_norms):
        return "纯数字条码/日期干扰"
    return "OCR未读到系统SN"


def make_row(
    image_path: Path,
    expected: str,
    found_values: list[str],
    matched: bool,
    path_used: str,
    match_type: str,
    fast_elapsed,
    slow_elapsed,
    sn_region_elapsed,
    started: float,
    error: str = "",
) -> dict:
    return {
        "file_name": image_path.name,
        "expected_sn": expected,
        "recognized_sn": ";".join(found_values),
        "matched": matched,
        "path_used": path_used,
        "match_type": match_type,
        "failure_reason": classify_reason(expected, found_values, matched),
        "fast_elapsed_sec": round(fast_elapsed, 3) if fast_elapsed is not None else "",
        "slow_elapsed_sec": round(slow_elapsed, 3) if slow_elapsed is not None else "",
        "sn_region_elapsed_sec": round(sn_region_elapsed, 3) if sn_region_elapsed is not None else "",
        "total_elapsed_sec": round(time.perf_counter() - started, 3),
        "error": error,
    }


def unique(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def run_one(ocr: OCREngine, image_path: Path) -> dict:
    expected = normalize(image_path.stem)
    started = time.perf_counter()
    fast_elapsed = None
    slow_elapsed = None
    sn_region_elapsed = None
    path_used = "none"
    match_type = ""
    error = ""
    fast_found: list[str] = []
    slow_found: list[str] = []
    sn_region_found: list[str] = []

    try:
        fast_started = time.perf_counter()
        fast_texts = ocr.extract_text_enhanced(image_path)
        fast_elapsed = time.perf_counter() - fast_started
        fast_match = CodeExtractor.match_system_sn(fast_texts, expected)
        fast_found = fast_match["found_sns"]
        if fast_match["sn_match"]:
            path_used = "fast"
            match_type = fast_match.get("match_type", "")
            return make_row(
                image_path, expected, fast_found, True, path_used, match_type,
                fast_elapsed, slow_elapsed, sn_region_elapsed, started,
            )

        slow_started = time.perf_counter()
        slow_texts = ocr.extract_text_tiled(image_path)
        slow_elapsed = time.perf_counter() - slow_started
        slow_match = CodeExtractor.match_system_sn(slow_texts, expected)
        slow_found = slow_match["found_sns"]
        if slow_match["sn_match"]:
            path_used = "slow"
            match_type = slow_match.get("match_type", "")
        elif hasattr(ocr, "extract_text_sn_regions"):
            region_started = time.perf_counter()
            region_texts = ocr.extract_text_sn_regions(image_path)
            sn_region_elapsed = time.perf_counter() - region_started
            region_match = CodeExtractor.match_system_sn(region_texts, expected)
            sn_region_found = region_match["found_sns"]
            if region_match["sn_match"]:
                path_used = "sn_region"
                match_type = region_match.get("match_type", "")
    except Exception as exc:
        error = repr(exc)

    found_values = unique(fast_found + slow_found + sn_region_found)
    return make_row(
        image_path,
        expected,
        found_values,
        path_used in {"fast", "slow", "sn_region"},
        path_used,
        match_type,
        fast_elapsed,
        slow_elapsed,
        sn_region_elapsed,
        started,
        error,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch test SN OCR images.")
    parser.add_argument("--image-dir", default=r"C:\Users\85169\Desktop\sn码测试")
    parser.add_argument("--out-dir", default=r"C:\audit_robot\reports")
    args = parser.parse_args()

    image_dir = Path(args.image_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "sn_batch_test_results.csv"
    json_path = out_dir / "sn_batch_test_summary.json"

    images = sorted(p for p in image_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    ocr = OCREngine()
    results = []
    batch_started = time.perf_counter()
    for idx, image_path in enumerate(images, 1):
        print(f"[{idx}/{len(images)}] {image_path.name}", flush=True)
        result = run_one(ocr, image_path)
        results.append(result)
        print(
            f"  matched={result['matched']} path={result['path_used']} "
            f"type={result['match_type']} elapsed={result['total_elapsed_sec']}s "
            f"reason={result['failure_reason']} found={result['recognized_sn']}",
            flush=True,
        )

    total_elapsed = time.perf_counter() - batch_started
    fieldnames = [
        "file_name",
        "expected_sn",
        "recognized_sn",
        "matched",
        "path_used",
        "match_type",
        "failure_reason",
        "fast_elapsed_sec",
        "slow_elapsed_sec",
        "sn_region_elapsed_sec",
        "total_elapsed_sec",
        "error",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    elapsed_values = [float(r["total_elapsed_sec"]) for r in results]
    matched_count = sum(1 for r in results if r["matched"])
    summary = {
        "image_dir": str(image_dir),
        "csv_path": str(csv_path),
        "total_images": len(results),
        "matched_count": matched_count,
        "miss_count": len(results) - matched_count,
        "hit_rate": round(matched_count / len(results), 4) if results else 0,
        "fast_match_count": sum(1 for r in results if r["path_used"] == "fast"),
        "slow_match_count": sum(1 for r in results if r["path_used"] == "slow"),
        "sn_region_match_count": sum(1 for r in results if r["path_used"] == "sn_region"),
        "total_elapsed_sec": round(total_elapsed, 3),
        "avg_elapsed_sec": round(statistics.mean(elapsed_values), 3) if elapsed_values else 0,
        "median_elapsed_sec": round(statistics.median(elapsed_values), 3) if elapsed_values else 0,
        "max_elapsed_sec": round(max(elapsed_values), 3) if elapsed_values else 0,
        "min_elapsed_sec": round(min(elapsed_values), 3) if elapsed_values else 0,
        "miss_files": [r["file_name"] for r in results if not r["matched"]],
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
