from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TreeNode:
    leaf: bool
    pos: int
    neg: int
    metric: str | None = None
    threshold: float | None = None
    left: "TreeNode | None" = None
    right: "TreeNode | None" = None


def _gini(labels: list[int]) -> float:
    total = len(labels)
    if total == 0:
        return 0.0
    positive = sum(labels) / total
    return 1.0 - positive * positive - (1.0 - positive) * (1.0 - positive)


def _candidate_thresholds(values: list[float], buckets: int = 40) -> list[float]:
    if not values:
        return []
    ordered = sorted(values)
    thresholds: list[float] = []
    seen: set[float] = set()
    for index in range(1, buckets):
        position = round((len(ordered) - 1) * index / buckets)
        threshold = ordered[min(len(ordered) - 1, max(0, position))]
        if threshold not in seen:
            seen.add(threshold)
            thresholds.append(threshold)
    return thresholds


def _best_split(
    rows: list[dict[str, Any]],
    labels: list[int],
    metrics: list[str],
    thresholds: dict[str, list[float]],
) -> tuple[float, str, float, list[int], list[int]] | None:
    base = _gini(labels)
    best: tuple[float, str, float, list[int], list[int]] | None = None
    indices = list(range(len(rows)))
    for metric in metrics:
        for threshold in thresholds.get(metric, []):
            left = [idx for idx in indices if float(rows[idx]["features"][metric]) <= threshold]
            right = [idx for idx in indices if float(rows[idx]["features"][metric]) > threshold]
            if not left or not right:
                continue
            score = (
                (len(left) / len(indices)) * _gini([labels[idx] for idx in left])
                + (len(right) / len(indices)) * _gini([labels[idx] for idx in right])
            )
            gain = base - score
            if best is None or gain > best[0]:
                best = (gain, metric, threshold, left, right)
    return best


def _build_tree(
    rows: list[dict[str, Any]],
    labels: list[int],
    metrics: list[str],
    thresholds: dict[str, list[float]],
    indices: list[int],
    *,
    depth: int,
    max_depth: int,
    min_size: int,
) -> TreeNode:
    pos = sum(labels[idx] for idx in indices)
    neg = len(indices) - pos
    if depth >= max_depth or len(indices) <= min_size or pos == 0 or neg == 0:
        return TreeNode(leaf=True, pos=pos, neg=neg)

    subset_rows = [rows[idx] for idx in indices]
    subset_labels = [labels[idx] for idx in indices]
    split = _best_split(subset_rows, subset_labels, metrics, thresholds)
    if split is None or split[0] <= 1e-9:
        return TreeNode(leaf=True, pos=pos, neg=neg)

    _, metric, threshold, local_left, local_right = split
    left_indices = [indices[idx] for idx in local_left]
    right_indices = [indices[idx] for idx in local_right]
    return TreeNode(
        leaf=False,
        pos=pos,
        neg=neg,
        metric=metric,
        threshold=threshold,
        left=_build_tree(
            rows,
            labels,
            metrics,
            thresholds,
            left_indices,
            depth=depth + 1,
            max_depth=max_depth,
            min_size=min_size,
        ),
        right=_build_tree(
            rows,
            labels,
            metrics,
            thresholds,
            right_indices,
            depth=depth + 1,
            max_depth=max_depth,
            min_size=min_size,
        ),
    )


def _predict_tree(tree: TreeNode, features: dict[str, float], *, leaf_positive_ratio: float) -> bool:
    node = tree
    while not node.leaf:
        assert node.metric is not None and node.threshold is not None
        value = float(features[node.metric])
        node = node.left if value <= node.threshold else node.right
        assert node is not None
    total = node.pos + node.neg
    ratio = (node.pos / total) if total else 0.0
    return ratio >= leaf_positive_ratio


def _load_rows(feature_jsonl: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in feature_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows.append(row)
    return rows


def run_evaluation(
    feature_jsonl: Path,
    *,
    max_depth: int = 12,
    min_size: int = 3,
    leaf_positive_ratio: float = 0.3,
) -> dict[str, Any]:
    rows = _load_rows(feature_jsonl)
    if not rows:
        raise ValueError("feature_jsonl is empty")
    metrics = sorted(rows[0]["features"].keys())
    labels = [1 if row["label"] == "A" else 0 for row in rows]
    thresholds = {metric: _candidate_thresholds([float(row["features"][metric]) for row in rows]) for metric in metrics}
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

    predictions = [
        _predict_tree(tree, row["features"], leaf_positive_ratio=leaf_positive_ratio)
        for row in rows
    ]
    summary: dict[str, Any] = {}
    for label in ("A", "B"):
        subset = [row for row in rows if row["label"] == label]
        predicted = [pred for row, pred in zip(rows, predictions) if row["label"] == label and pred]
        hit_count = len(predicted)
        total = len(subset)
        summary["non_real" if label == "A" else "real"] = {
            "count": total,
            "hit_count": hit_count,
            "hit_rate": hit_count / total if total else None,
            "miss_count" if label == "A" else "false_positive_count": total - hit_count if label == "A" else hit_count,
            "miss_rate" if label == "A" else "false_positive_rate": (total - hit_count) / total if label == "A" and total else (hit_count / total if total else None),
        }
    summary["tree"] = tree
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, default=Path("reports/non_real_prompt_eval/local_features_v2.jsonl"))
    args = parser.parse_args()
    summary = run_evaluation(args.features)
    print(json.dumps(summary, ensure_ascii=False, default=lambda obj: obj.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
