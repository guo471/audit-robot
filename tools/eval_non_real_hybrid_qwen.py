from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import eval_non_real_prompt_qwen as qwen_eval
from tools.non_real_local_features import IMAGE_EXTS, extract_features, iter_images
from tools.non_real_tree_classifier import (
    _build_tree,
    _candidate_thresholds,
    _load_rows,
    _predict_tree,
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _image_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _select(paths: list[Path], offset: int, limit: int | None) -> list[Path]:
    sliced = paths[offset:]
    return sliced if limit is None else sliced[:limit]


def _all_images(directories: list[Path], files: list[Path], exclude_names: set[str]) -> list[Path]:
    paths: list[Path] = []
    for directory in directories:
        paths.extend(iter_images(directory))
    paths.extend(path for path in files if path.is_file() and path.suffix.lower() in IMAGE_EXTS)
    unique: dict[str, Path] = {}
    for path in paths:
        if path.name in exclude_names:
            continue
        unique[str(path.resolve()).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item))


def _train_tree(feature_jsonl: Path, *, max_depth: int, min_size: int, leaf_positive_ratio: float) -> Any:
    rows = _load_rows(feature_jsonl)
    if not rows:
        raise ValueError("feature_jsonl is empty")
    metrics = sorted(rows[0]["features"].keys())
    labels = [1 if row["label"] == "A" else 0 for row in rows]
    thresholds = {
        metric: _candidate_thresholds([float(row["features"][metric]) for row in rows])
        for metric in metrics
    }
    tree = _build_tree(
        rows,
        labels,
        metrics,
        thresholds,
        list(range(len(rows))),
        depth=0,
        max_depth=max_depth,
        min_size=min_size,
    )
    return tree


def _predict_local_tree(tree: Any, image_path: Path, leaf_positive_ratio: float) -> tuple[str, str, dict[str, Any]]:
    features = extract_features(image_path)
    predicted = _predict_tree(tree, features, leaf_positive_ratio=leaf_positive_ratio)
    return ("non_real" if predicted else "real"), "LOCAL_TREE", features


def _qwen_for_image(
    *,
    image_path: Path,
    sample_id: str,
    prompt: str,
    cache_dir: Path,
    base_url: str,
    api_key: str,
    timeout: int,
) -> tuple[dict[str, Any], str, str, dict[str, Any], bool, float, dict[str, Any]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = qwen_eval._json_cache_path(cache_dir, prompt, image_path)
    cached = cache_path.exists()
    elapsed = 0.0
    usage: dict[str, Any] = {}
    if cached:
        record = json.loads(cache_path.read_text(encoding="utf-8"))
        parsed = record["parsed"]
        usage = record.get("usage") or {}
    else:
        parsed, text, usage, elapsed = qwen_eval._call_qwen(
            base_url,
            api_key,
            prompt,
            sample_id,
            image_path,
            timeout,
        )
        try:
            derived_result, derived_rule, normalized_observation = qwen_eval._derive_from_model_observations(
                parsed,
                sample_id,
            )
        except Exception as exc:
            derived_result, derived_rule, normalized_observation = qwen_eval._failure_row(sample_id, exc)
        cache_path.write_text(
            json.dumps(
                {
                    "model": qwen_eval.MODEL,
                    "image_sha256": _image_digest(image_path),
                    "sample_id": sample_id,
                    "parsed": parsed,
                    "normalized_observation": normalized_observation,
                    "derived_result": derived_result,
                    "derived_rule": derived_rule,
                    "content_text": text,
                    "usage": usage,
                    "elapsed_sec": elapsed,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    try:
        derived_result, derived_rule, normalized_observation = qwen_eval._derive_from_model_observations(
            parsed,
            sample_id,
        )
    except Exception as exc:
        derived_result, derived_rule, normalized_observation = qwen_eval._failure_row(sample_id, exc)
    return parsed, derived_result, derived_rule, normalized_observation, cached, elapsed, usage


def _final_result(tree_result: str, qwen_result: str) -> tuple[str, str]:
    if tree_result == "non_real" and qwen_result == "high_risk_non_real":
        return "non_real", "tree+qwen_high_risk"
    if tree_result == "non_real":
        return "non_real", "tree"
    if qwen_result == "high_risk_non_real":
        return "non_real", "qwen_high_risk"
    return "real", "none"


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for group, expected in (("A", "non_real"), ("B", "real")):
        subset = [row for row in rows if row["group"] == group]
        final_errors = [row for row in subset if row["final_result_code"] != expected]
        qwen_manual = [row for row in subset if row["qwen_result"] == "manual_review"]
        qwen_high = [row for row in subset if row["qwen_result"] == "high_risk_non_real"]
        tree_non_real = [row for row in subset if row["tree_result_code"] == "non_real"]
        summary[group] = {
            "total": len(subset),
            "expected_final": expected,
            "final_errors": len(final_errors),
            "final_error_rate": (len(final_errors) / len(subset)) if subset else 0.0,
            "final_non_real": sum(row["final_result_code"] == "non_real" for row in subset),
            "tree_non_real": len(tree_non_real),
            "qwen_high_risk": len(qwen_high),
            "qwen_manual_review": len(qwen_manual),
            "qwen_no_evidence": sum(row["qwen_result"] == "no_evidence" for row in subset),
        }
    summary["fusion_rule"] = "final = non_real if local tree is non_real OR derived Qwen result is high_risk_non_real"
    return summary


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "group",
        "group_index",
        "path",
        "final_result_code",
        "final_source",
        "tree_result_code",
        "tree_rule",
        "qwen_result",
        "qwen_rule",
        "qwen_cached",
        "qwen_reason",
        "qwen_strong_evidence",
        "qwen_weak_evidence",
        "image_sha256",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(row.get(key), ensure_ascii=False)
                if isinstance(row.get(key), (dict, list))
                else row.get(key)
                for key in fieldnames
            })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--feature-jsonl", type=Path, default=PROJECT_ROOT / "reports/non_real_prompt_eval/local_features_v2.jsonl")
    parser.add_argument("--a-dir", type=Path, action="append", default=[])
    parser.add_argument("--a-file", type=Path, action="append", default=[])
    parser.add_argument("--b-dir", type=Path, action="append", default=[])
    parser.add_argument("--b-file", type=Path, action="append", default=[])
    parser.add_argument("--exclude-a-name", action="append", default=[])
    parser.add_argument("--exclude-b-name", action="append", default=[])
    parser.add_argument("--offset-a", type=int, default=0)
    parser.add_argument("--offset-b", type=int, default=0)
    parser.add_argument("--limit-a", type=int)
    parser.add_argument("--limit-b", type=int)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--max-depth", type=int, default=12)
    parser.add_argument("--min-size", type=int, default=3)
    parser.add_argument("--leaf-positive-ratio", type=float, default=0.3)
    args = parser.parse_args()

    base_url = os.environ.get("VISION_API_BASE_URL", "").strip()
    api_key = os.environ.get("VISION_API_KEY", "").strip()
    if not base_url or not api_key:
        raise SystemExit("VISION_API_BASE_URL and VISION_API_KEY are required")

    prompt = args.prompt_file.read_text(encoding="utf-8").strip()
    tree = _train_tree(
        args.feature_jsonl,
        max_depth=args.max_depth,
        min_size=args.min_size,
        leaf_positive_ratio=args.leaf_positive_ratio,
    )
    a_images = _select(
        _all_images(args.a_dir, args.a_file, set(args.exclude_a_name)),
        args.offset_a,
        args.limit_a,
    )
    b_images = _select(
        _all_images(args.b_dir, args.b_file, set(args.exclude_b_name)),
        args.offset_b,
        args.limit_b,
    )

    rows: list[dict[str, Any]] = []
    for group, images in (("A", a_images), ("B", b_images)):
        for index, image_path in enumerate(images, start=1):
            sample_id = f"{group.lower()}_{index + (args.offset_a if group == 'A' else args.offset_b):06d}"
            tree_result, tree_rule, local_features = _predict_local_tree(
                tree,
                image_path,
                args.leaf_positive_ratio,
            )
            parsed, qwen_result, qwen_rule, observation, cached, elapsed, usage = _qwen_for_image(
                image_path=image_path,
                sample_id=sample_id,
                prompt=prompt,
                cache_dir=args.cache_dir,
                base_url=base_url,
                api_key=api_key,
                timeout=args.timeout,
            )
            final_code, final_source = _final_result(tree_result, qwen_result)
            row = {
                "group": group,
                "group_index": index + (args.offset_a if group == "A" else args.offset_b),
                "path": str(image_path),
                "image_sha256": _image_digest(image_path),
                "final_result_code": final_code,
                "final_source": final_source,
                "tree_result_code": tree_result,
                "tree_rule": tree_rule,
                "local_features": local_features,
                "qwen_model_result": parsed.get("result"),
                "qwen_result": qwen_result,
                "qwen_rule": qwen_rule,
                "qwen_cached": cached,
                "qwen_elapsed_sec": elapsed,
                "qwen_usage": usage,
                "qwen_reason": parsed.get("reason"),
                "qwen_observation": observation,
                "qwen_strong_evidence": observation.get("strong_evidence"),
                "qwen_weak_evidence": observation.get("weak_evidence"),
            }
            rows.append(row)
            print(
                f"{group} {index}/{len(images)} final={final_code} source={final_source} "
                f"tree={tree_result} qwen={qwen_result}/{qwen_rule} cached={cached}",
                flush=True,
            )

    result = {
        "model": qwen_eval.MODEL,
        "prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
        "feature_jsonl_sha256": _sha256_bytes(args.feature_jsonl.read_bytes()),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "offset_a": args.offset_a,
        "offset_b": args.offset_b,
        "limit_a": args.limit_a,
        "limit_b": args.limit_b,
        "summary": _summarize(rows),
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(args.out.with_suffix(".csv"), rows)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
