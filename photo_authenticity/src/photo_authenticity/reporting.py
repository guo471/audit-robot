from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Sequence

import numpy as np

from .contracts import Decision


FORMAL_MINIMUMS = {"non_real": 14, "real": 20}
Scope = Literal["exploratory_cv", "formal_locked", "challenge"]


class FormalEvaluationPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class PredictionRecord:
    sample_id: str
    label: str
    label_status: str
    score: float
    decision: Decision
    source_group: str
    scope: str
    split: str
    manifest_sha256: str
    model_sha256: str
    threshold_sha256: str


@dataclass(frozen=True)
class EvaluationMetrics:
    confusion_matrix: dict[str, dict[str, int]]
    non_real_recall: float
    real_to_manual_review_rate: float
    balanced_accuracy: float
    bootstrap_intervals: dict[str, tuple[float, float]] | None


@dataclass(frozen=True)
class EvaluationResult:
    scope: Scope
    exploratory: bool
    formal_status: str
    metrics: EvaluationMetrics | None
    group_count: int
    label_status_counts: dict[str, int]
    shortage_counts: dict[str, int]
    missed_confirmed_non_real_ids: tuple[str, ...]
    manifest_sha256: str
    model_sha256: str
    threshold_sha256: str


@dataclass(frozen=True)
class ReportPaths:
    json_path: Path
    markdown_path: Path


def _single_hash(predictions: Sequence[PredictionRecord], field: str) -> str:
    values = {getattr(item, field) for item in predictions}
    if not values:
        return ""
    if len(values) != 1 or len(next(iter(values))) != 64:
        raise ValueError(f"predictions must bind one valid {field}")
    return next(iter(values))


def _metric_values(predictions: Sequence[PredictionRecord]) -> tuple[float, float, float]:
    non_real = [item for item in predictions if item.label == "non_real"]
    real = [item for item in predictions if item.label == "real"]
    non_real_recall = (
        sum(item.decision == "manual_review" for item in non_real) / len(non_real)
        if non_real
        else 0.0
    )
    real_manual_rate = (
        sum(item.decision == "manual_review" for item in real) / len(real) if real else 0.0
    )
    real_low_risk_recall = 1.0 - real_manual_rate if real else 0.0
    return non_real_recall, real_manual_rate, (non_real_recall + real_low_risk_recall) / 2.0


def _bootstrap(
    predictions: Sequence[PredictionRecord], repetitions: int = 500
) -> dict[str, tuple[float, float]] | None:
    if len(predictions) < 4 or {item.label for item in predictions} != {"real", "non_real"}:
        return None
    random = np.random.default_rng(20260713)
    values = np.empty((repetitions, 3), dtype=np.float64)
    for index in range(repetitions):
        sampled = [predictions[item] for item in random.integers(0, len(predictions), len(predictions))]
        values[index] = _metric_values(sampled)
    names = ("non_real_recall", "real_to_manual_review_rate", "balanced_accuracy")
    return {
        name: (
            float(np.percentile(values[:, column], 2.5)),
            float(np.percentile(values[:, column], 97.5)),
        )
        for column, name in enumerate(names)
    }


def evaluate_predictions(
    predictions: Sequence[PredictionRecord], scope: Scope
) -> EvaluationResult:
    records = tuple(predictions)
    if scope not in {"exploratory_cv", "formal_locked", "challenge"}:
        raise ValueError("invalid evaluation scope")
    if any(item.scope != scope for item in records):
        raise ValueError("prediction scope mismatch")
    if scope in {"formal_locked", "challenge"} and any(
        item.label_status != "confirmed" for item in records
    ):
        raise FormalEvaluationPolicyError("formal and challenge evaluation require confirmed labels")
    if any(item.label not in {"real", "non_real"} for item in records):
        raise ValueError("evaluation labels must be real or non_real")
    if any(item.decision not in {"low_risk_candidate", "manual_review"} for item in records):
        raise ValueError("invalid public decision")

    counts = Counter(item.label for item in records if item.label_status == "confirmed")
    shortages = {
        label: max(0, minimum - counts[label]) for label, minimum in FORMAL_MINIMUMS.items()
    }
    formal_unavailable = scope == "formal_locked" and any(shortages.values())
    metrics: EvaluationMetrics | None = None
    if not formal_unavailable:
        non_real_recall, real_manual_rate, balanced_accuracy = _metric_values(records)
        confusion = {
            "real": {
                "low_risk_candidate": sum(
                    item.label == "real" and item.decision == "low_risk_candidate" for item in records
                ),
                "manual_review": sum(
                    item.label == "real" and item.decision == "manual_review" for item in records
                ),
            },
            "non_real": {
                "low_risk_candidate": sum(
                    item.label == "non_real" and item.decision == "low_risk_candidate" for item in records
                ),
                "manual_review": sum(
                    item.label == "non_real" and item.decision == "manual_review" for item in records
                ),
            },
        }
        metrics = EvaluationMetrics(
            confusion,
            non_real_recall,
            real_manual_rate,
            balanced_accuracy,
            _bootstrap(records),
        )
    missed = tuple(
        sorted(
            item.sample_id
            for item in records
            if item.label == "non_real"
            and item.label_status == "confirmed"
            and item.decision == "low_risk_candidate"
        )
    )
    exploratory = scope == "exploratory_cv" or any(
        item.label_status == "weak_label" for item in records
    )
    return EvaluationResult(
        scope=scope,
        exploratory=exploratory,
        formal_status=(
            "not_runnable_insufficient_confirmed_data"
            if formal_unavailable
            else "runnable"
        ),
        metrics=metrics,
        group_count=len({item.source_group for item in records}),
        label_status_counts=dict(sorted(Counter(item.label_status for item in records).items())),
        shortage_counts=shortages,
        missed_confirmed_non_real_ids=missed,
        manifest_sha256=_single_hash(records, "manifest_sha256"),
        model_sha256=_single_hash(records, "model_sha256"),
        threshold_sha256=_single_hash(records, "threshold_sha256"),
    )


def write_evaluation_report(
    result: EvaluationResult, output_json: Path, output_md: Path
) -> ReportPaths:
    json_path = output_json.resolve()
    markdown_path = output_md.resolve()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(result)
    payload["hashes"] = {
        "manifest_sha256": payload.pop("manifest_sha256"),
        "model_sha256": payload.pop("model_sha256"),
        "threshold_sha256": payload.pop("threshold_sha256"),
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    title = (
        "探索性结果（含 weak_label，不得用于正式效果验收）"
        if result.exploratory and result.label_status_counts.get("weak_label", 0)
        else "Photo Authenticity Evaluation"
    )
    lines = [
        f"# {title}",
        "",
        f"- Scope: `{result.scope}`",
        f"- Formal status: `{result.formal_status}`",
        f"- Group count: {result.group_count}",
        "- 仅代表当前小型锁定集，不代表生产零漏放",
        "",
        "## Missed confirmed non-real samples",
        "",
    ]
    if result.missed_confirmed_non_real_ids:
        lines.extend(f"- `{sample_id}`" for sample_id in result.missed_confirmed_non_real_ids)
    else:
        lines.append("- None observed in this evaluation population")
    lines.extend(["", "## Metrics", ""])
    if result.metrics is None:
        lines.append("Not runnable because confirmed formal data is insufficient; no metrics emitted.")
    else:
        lines.extend(
            [
                f"- Non-real recall: {result.metrics.non_real_recall:.6f}",
                f"- Real-to-manual-review rate: {result.metrics.real_to_manual_review_rate:.6f}",
                f"- Balanced accuracy: {result.metrics.balanced_accuracy:.6f}",
            ]
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ReportPaths(json_path, markdown_path)
