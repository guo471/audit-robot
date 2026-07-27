from __future__ import annotations

import json
import math

import pytest

from photo_authenticity.config import ThresholdPolicy
from photo_authenticity.reporting import (
    FormalEvaluationPolicyError,
    PredictionRecord,
    evaluate_predictions,
    write_evaluation_report,
)
from photo_authenticity.thresholds import (
    FrozenThresholds,
    ThresholdSelectionPolicyError,
    classify_score,
    select_thresholds,
)


MODEL_HASH = "a" * 64
MANIFEST_HASH = "b" * 64
THRESHOLD_HASH = "c" * 64


def _prediction(
    sample_id: str = "S1",
    *,
    label: str = "real",
    label_status: str = "confirmed",
    score: float = 0.1,
    decision: str = "low_risk_candidate",
    scope: str = "exploratory_cv",
    split: str = "validation",
) -> PredictionRecord:
    return PredictionRecord(
        sample_id=sample_id,
        label=label,
        label_status=label_status,
        score=score,
        decision=decision,
        source_group=f"group-{sample_id}",
        scope=scope,
        split=split,
        manifest_sha256=MANIFEST_HASH,
        model_sha256=MODEL_HASH,
        threshold_sha256=THRESHOLD_HASH,
    )


def test_gray_zone_and_weak_label_formal_guard() -> None:
    frozen = FrozenThresholds(low_risk=0.20, risk=0.70, model_sha256=MODEL_HASH)

    assert classify_score(0.10, frozen) == "low_risk_candidate"
    assert classify_score(0.50, frozen) == "manual_review"
    assert classify_score(0.90, frozen) == "manual_review"
    assert classify_score(math.nan, frozen) == "manual_review"
    assert classify_score(0.10, None) == "manual_review"
    with pytest.raises(FormalEvaluationPolicyError):
        evaluate_predictions(
            [_prediction(label_status="weak_label", scope="formal_locked", split="locked")],
            scope="formal_locked",
        )


def test_threshold_selection_accepts_only_oof_validation_and_marks_weak_exploratory() -> None:
    predictions = [
        _prediction("N1", label="non_real", label_status="confirmed", score=0.8, decision="manual_review"),
        _prediction("N2", label="non_real", label_status="confirmed", score=0.6, decision="manual_review"),
        _prediction("R1", label="real", label_status="weak_label", score=0.1),
    ]

    selection = select_thresholds(predictions, ThresholdPolicy(0.2, 0.7, 1.0))

    assert selection.thresholds.exploratory is True
    assert selection.thresholds.selection_scope == "oof_validation"
    assert selection.achieved_non_real_recall == 1.0
    assert selection.thresholds.low_risk <= 0.6

    with pytest.raises(ThresholdSelectionPolicyError):
        select_thresholds(
            [_prediction(scope="formal_locked", split="locked")],
            ThresholdPolicy(0.2, 0.7, 0.9),
        )


def test_exploratory_report_names_weak_scope_and_lists_every_confirmed_miss(tmp_path) -> None:
    predictions = [
        _prediction("N-MISS", label="non_real", score=0.05, decision="low_risk_candidate"),
        _prediction("N-CAUGHT", label="non_real", score=0.9, decision="manual_review"),
        _prediction("R-WEAK", label="real", label_status="weak_label", score=0.1),
        _prediction("R-MANUAL", label="real", score=0.5, decision="manual_review"),
    ]
    result = evaluate_predictions(predictions, scope="exploratory_cv")

    paths = write_evaluation_report(
        result, tmp_path / "evaluation.json", tmp_path / "evaluation.md"
    )
    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
    markdown = paths.markdown_path.read_text(encoding="utf-8")

    assert result.exploratory is True
    assert result.missed_confirmed_non_real_ids == ("N-MISS",)
    assert payload["missed_confirmed_non_real_ids"] == ["N-MISS"]
    assert "N-MISS" in markdown
    assert "探索性结果（含 weak_label，不得用于正式效果验收）" in markdown
    assert "仅代表当前小型锁定集，不代表生产零漏放" in markdown
    assert payload["hashes"] == {
        "manifest_sha256": MANIFEST_HASH,
        "model_sha256": MODEL_HASH,
        "threshold_sha256": THRESHOLD_HASH,
    }


def test_insufficient_formal_data_has_no_fabricated_metrics(tmp_path) -> None:
    result = evaluate_predictions(
        [_prediction("N1", label="non_real", scope="formal_locked", split="locked")],
        scope="formal_locked",
    )
    paths = write_evaluation_report(result, tmp_path / "formal.json", tmp_path / "formal.md")
    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))

    assert result.formal_status == "not_runnable_insufficient_confirmed_data"
    assert result.metrics is None
    assert payload["metrics"] is None
    assert payload["shortage_counts"] == {"non_real": 13, "real": 20}
