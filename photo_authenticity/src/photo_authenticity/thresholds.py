from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .config import ThresholdPolicy
from .contracts import Decision


class ThresholdSelectionPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class FrozenThresholds:
    low_risk: float
    risk: float
    model_sha256: str
    exploratory: bool = False
    selection_scope: str = "oof_validation"

    def __post_init__(self) -> None:
        if not (0.0 <= self.low_risk < self.risk <= 1.0):
            raise ValueError("thresholds must satisfy 0 <= low_risk < risk <= 1")
        if len(self.model_sha256) != 64:
            raise ValueError("model_sha256 must contain 64 hexadecimal characters")


@dataclass(frozen=True)
class ThresholdSelection:
    thresholds: FrozenThresholds
    achieved_non_real_recall: float
    low_risk_coverage: float
    considered_cut_points: tuple[float, ...]


def classify_score(score: float, thresholds: FrozenThresholds | None) -> Decision:
    if thresholds is None or not math.isfinite(score):
        return "manual_review"
    if score < 0.0 or score > 1.0:
        return "manual_review"
    return "low_risk_candidate" if score < thresholds.low_risk else "manual_review"


def select_thresholds(
    validation_predictions: Sequence[object], policy: ThresholdPolicy
) -> ThresholdSelection:
    predictions = tuple(validation_predictions)
    if not predictions:
        raise ThresholdSelectionPolicyError("OOF validation predictions are required")
    if any(
        getattr(item, "scope", None) != "exploratory_cv"
        or getattr(item, "split", None) != "validation"
        for item in predictions
    ):
        raise ThresholdSelectionPolicyError(
            "thresholds may be selected only from OOF validation predictions"
        )
    scores = [float(getattr(item, "score")) for item in predictions]
    if any(not math.isfinite(score) or not 0.0 <= score <= 1.0 for score in scores):
        raise ThresholdSelectionPolicyError("validation scores must be finite probabilities")
    model_hashes = {str(getattr(item, "model_sha256", "")) for item in predictions}
    if len(model_hashes) != 1 or len(next(iter(model_hashes))) != 64:
        raise ThresholdSelectionPolicyError("predictions must bind one valid model hash")
    non_real_scores = [
        score
        for score, item in zip(scores, predictions)
        if getattr(item, "label", None) == "non_real"
    ]
    if not non_real_scores:
        raise ThresholdSelectionPolicyError("confirmed non-real validation examples are required")

    cut_points = tuple(
        sorted({0.0, policy.low_risk_threshold, *scores})
    )
    candidates: list[tuple[float, float, float]] = []
    for cut_point in cut_points:
        if not 0.0 <= cut_point < policy.risk_threshold:
            continue
        recall = sum(score >= cut_point for score in non_real_scores) / len(non_real_scores)
        coverage = sum(score < cut_point for score in scores) / len(scores)
        if recall >= policy.minimum_non_real_recall:
            candidates.append((cut_point, recall, coverage))
    if not candidates:
        raise ThresholdSelectionPolicyError("no threshold satisfies minimum non-real recall")
    selected_low, recall, coverage = min(
        candidates,
        key=lambda item: (-item[1], item[0], item[2]),
    )
    exploratory = any(
        getattr(item, "label_status", None) == "weak_label" for item in predictions
    )
    frozen = FrozenThresholds(
        low_risk=selected_low,
        risk=policy.risk_threshold,
        model_sha256=next(iter(model_hashes)),
        exploratory=exploratory,
        selection_scope="oof_validation",
    )
    return ThresholdSelection(frozen, recall, coverage, cut_points)
