from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class BinaryMetrics:
    true_real: int
    false_non_real: int
    false_real: int
    true_non_real: int
    non_real_recall: float
    balanced_accuracy: float


def binary_metrics(labels: Sequence[int], predictions: Sequence[int]) -> BinaryMetrics:
    if len(labels) != len(predictions):
        raise ValueError("labels and predictions must have equal length")
    true_real = sum(label == 0 and prediction == 0 for label, prediction in zip(labels, predictions))
    false_non_real = sum(label == 0 and prediction == 1 for label, prediction in zip(labels, predictions))
    false_real = sum(label == 1 and prediction == 0 for label, prediction in zip(labels, predictions))
    true_non_real = sum(label == 1 and prediction == 1 for label, prediction in zip(labels, predictions))
    real_total = true_real + false_non_real
    non_real_total = true_non_real + false_real
    real_recall = true_real / real_total if real_total else 0.0
    non_real_recall = true_non_real / non_real_total if non_real_total else 0.0
    return BinaryMetrics(
        true_real,
        false_non_real,
        false_real,
        true_non_real,
        non_real_recall,
        (real_recall + non_real_recall) / 2.0,
    )
