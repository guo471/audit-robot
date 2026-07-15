from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tools.photo_authenticity_mainline import (
    EXPECTED_EXTRACTOR_VERSION,
    EXPECTED_FEATURE_DIMENSION,
    EXPECTED_MODEL_SHA256,
    EXPECTED_THRESHOLD,
    FrozenFFTRescue,
    AuthenticityImageResult,
    AuthenticityOrderResult,
    PhotoAuthenticityConfig,
    PhotoAuthenticitySchemaError,
    derive_v4_result,
    evaluate_authenticity_images,
    apply_photo_authenticity_gate,
    validate_image_observations,
)


SIDES = ("top", "right", "bottom", "left")


def evidence(code: str, *regions: str) -> dict:
    return {"code": code, "regions": list(regions)}


def raw(image_id: str = "i1", **overrides) -> dict:
    value = {
        "image_id": image_id,
        "edges": {side: "scene_continues" for side in SIDES},
        "screen_owner": "none",
        "strong_evidence": [],
        "weak_evidence": [],
        "reason": "no visible carrier evidence",
    }
    value.update(overrides)
    return value


def test_mode_defaults_off_and_rejects_unknown_value():
    assert PhotoAuthenticityConfig.from_env({}).mode == "off"
    assert PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "shadow"}).mode == "shadow"
    with pytest.raises(ValueError, match="PHOTO_AUTHENTICITY_MODE"):
        PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "maybe"})


@pytest.mark.parametrize(
    "items, expected, affected",
    [
        ([raw("i1")], ["i1", "i2"], "i2"),
        ([raw("i1"), raw("i1")], ["i1"], "i1"),
        ([raw("i1"), raw("i2")], ["i1"], "i2"),
    ],
)
def test_validator_requires_exact_unique_image_id_coverage(items, expected, affected):
    with pytest.raises(PhotoAuthenticitySchemaError, match=affected):
        validate_image_observations(items, expected)


@pytest.mark.parametrize(
    "bad",
    [
        raw(edges={"top": "bogus", "right": "scene_continues", "bottom": "scene_continues", "left": "scene_continues"}),
        raw(screen_owner="phone"),
        raw(strong_evidence=[evidence("MADE_UP", "background")]),
        raw(weak_evidence=[evidence("LOCAL_MOIRE", "barcode")]),
    ],
)
def test_validator_rejects_unknown_edge_owner_evidence_and_region(bad):
    with pytest.raises(PhotoAuthenticitySchemaError, match="i1"):
        validate_image_observations([bad], ["i1"])


@pytest.mark.parametrize(
    "bad",
    [
        raw(edges={"top": [], "right": "scene_continues", "bottom": "scene_continues", "left": "scene_continues"}),
        raw(screen_owner={"unexpected": "object"}),
        raw(strong_evidence=[evidence([], "background")]),
        raw(weak_evidence=[evidence({}, "background")]),
    ],
)
def test_validator_normalizes_unhashable_json_values_to_schema_error(bad):
    with pytest.raises(PhotoAuthenticitySchemaError, match="i1"):
        validate_image_observations([raw("valid"), bad], ["valid", "i1"])


def test_validator_returns_observations_keyed_by_input_id_not_array_order():
    result = validate_image_observations([raw("i2"), raw("i1")], ["i1", "i2"])
    assert list(result) == ["i1", "i2"]
    assert result["i2"].image_id == "i2"


def test_off_mode_does_not_load_artifact_or_images(monkeypatch, tmp_path):
    config = PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "off"})
    monkeypatch.setattr(FrozenFFTRescue, "load", lambda *_: pytest.fail("artifact loaded"))
    result = evaluate_authenticity_images(
        config=config,
        raw_observations=None,
        expected_image_ids=["i1"],
        image_paths={"i1": tmp_path / "missing.jpg"},
    )
    assert result.mode == "off"
    assert result.would_manual is False
    assert result.image_results == {}


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"strong_evidence": [evidence("EXTERNAL_PHOTO_CARRIER", "background")]}, ("high_risk_non_real", "R1")),
        ({"screen_owner": "external_screen", "strong_evidence": [evidence("PHOTO_VIEWER_UI", "background")]}, ("high_risk_non_real", "R2")),
        ({"screen_owner": "product_screen", "strong_evidence": [evidence("PHOTO_VIEWER_UI", "product_screen")]}, ("no_evidence", "R3")),
        ({"strong_evidence": [evidence("PRINTED_PHOTO_CARRIER", "background")]}, ("high_risk_non_real", "R4")),
        ({"strong_evidence": [evidence("NESTED_IMAGE_BOUNDARY", "background")]}, ("high_risk_non_real", "R4")),
        ({"strong_evidence": [evidence("CROSS_OBJECT_MOIRE", "hand", "background")]}, ("high_risk_non_real", "R5")),
        ({"strong_evidence": [evidence("CROSS_OBJECT_MOIRE", "hand")]}, ("manual_review", "R9")),
        ({"weak_evidence": [evidence("PLANAR_APPEARANCE", "background")]}, ("manual_review", "R9")),
        ({}, ("no_evidence", "R9")),
    ],
)
def test_frozen_v4_rules(overrides, expected):
    observation = validate_image_observations([raw(**overrides)], ["i1"])["i1"]
    assert derive_v4_result(observation) == expected


def test_edge_rules_cover_two_one_and_abrupt_plus_optics():
    two = {side: "scene_continues" for side in SIDES} | {"top": "carrier_boundary", "bottom": "carrier_boundary"}
    one = {side: "scene_continues" for side in SIDES} | {"left": "carrier_boundary"}
    abrupt = {side: "scene_continues" for side in SIDES} | {"right": "abrupt_cutoff"}
    observations = validate_image_observations([
        raw("two", edges=two), raw("one", edges=one),
        raw("abrupt", edges=abrupt, weak_evidence=[evidence("OUTER_PLANE_OPTICS", "background")]),
    ], ["two", "one", "abrupt"])
    assert derive_v4_result(observations["two"]) == ("high_risk_non_real", "R6")
    assert derive_v4_result(observations["one"]) == ("manual_review", "R7")
    assert derive_v4_result(observations["abrupt"]) == ("high_risk_non_real", "R8")


def _release_dir() -> Path:
    return Path("photo_authenticity/models/releases/non-real-photo-v2")


def test_frozen_artifact_contract_and_model_hash():
    rescue = FrozenFFTRescue.load(_release_dir())
    assert rescue.metadata["extractor_version"] == EXPECTED_EXTRACTOR_VERSION
    assert rescue.feature_dimension == EXPECTED_FEATURE_DIMENSION == 795
    assert rescue.threshold == EXPECTED_THRESHOLD == 0.995
    assert hashlib.sha256((_release_dir() / "model.joblib").read_bytes()).hexdigest() == EXPECTED_MODEL_SHA256


def test_frozen_artifact_rejects_tampering_and_threshold_drift(tmp_path):
    metadata = json.loads((_release_dir() / "metadata.json").read_text(encoding="utf-8"))
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "model.joblib").write_bytes((_release_dir() / "model.joblib").read_bytes() + b"x")
    (target / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        FrozenFFTRescue.load(target)
    (target / "model.joblib").write_bytes((_release_dir() / "model.joblib").read_bytes())
    metadata["threshold"] = 0.9
    (target / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="metadata hash"):
        FrozenFFTRescue.load(target)


def test_frozen_artifact_rejects_any_metadata_contract_tampering(tmp_path):
    metadata = json.loads((_release_dir() / "metadata.json").read_text(encoding="utf-8"))
    metadata["failure_policy"]["after_retries"] = "allow"
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "model.joblib").write_bytes((_release_dir() / "model.joblib").read_bytes())
    (target / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="metadata hash"):
        FrozenFFTRescue.load(target)


def test_fft_score_is_deterministic_795_dim_and_metadata_independent(tmp_path):
    pixels = np.arange(48 * 64 * 3, dtype=np.uint8).reshape(48, 64, 3)
    first = tmp_path / "order-SECRET-a.png"
    second = tmp_path / "renamed-unrelated.jpg"
    Image.fromarray(pixels).save(first)
    Image.fromarray(pixels).save(second, format="PNG")
    rescue = FrozenFFTRescue.load(_release_dir())
    score1, evidence1 = rescue.score(first)
    score2, evidence2 = rescue.score(second)
    assert score1 == pytest.approx(score2, abs=1e-12)
    assert evidence1["feature_dimension"] == evidence2["feature_dimension"] == 795
    assert evidence1["extractor_version"] == EXPECTED_EXTRACTOR_VERSION


def test_fft_only_rescues_no_evidence_and_retries_twice(monkeypatch, tmp_path):
    observations = validate_image_observations([
        raw("high", strong_evidence=[evidence("EXTERNAL_PHOTO_CARRIER", "background")]),
        raw("manual", weak_evidence=[evidence("LOCAL_MOIRE", "background")]),
        raw("n0"), raw("failure"),
    ], ["high", "manual", "n0", "failure"])
    attempts = {"n0": 0, "failure": 0}
    for key in observations:
        (tmp_path / f"{key}.png").write_bytes(b"present")

    class FakeRescue:
        threshold = 0.995

        def score(self, path):
            key = path.stem
            attempts[key] += 1
            if key == "failure":
                raise OSError("decode failed")
            return 0.999, {"feature_dimension": 795}

    result = evaluate_authenticity_images(
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "enforce"}),
        raw_observations=[raw(item.image_id, edges=item.edges, screen_owner=item.screen_owner,
                              strong_evidence=[evidence(x.code, *x.regions) for x in item.strong_evidence],
                              weak_evidence=[evidence(x.code, *x.regions) for x in item.weak_evidence])
                          for item in observations.values()],
        expected_image_ids=list(observations),
        image_paths={key: tmp_path / f"{key}.png" for key in observations},
        rescue=FakeRescue(),
    )
    assert result.image_results["high"].result == "high_risk_non_real"
    assert result.image_results["manual"].result == "manual_review"
    assert result.image_results["n0"].result == "manual_review"
    assert result.image_results["n0"].rescued_by_fft is True
    assert result.image_results["failure"].result == "manual_review"
    assert result.image_results["failure"].status == "failed_after_retries"
    assert attempts == {"n0": 1, "failure": 2}


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_artifact_load_failure_is_service_failure_and_never_silently_passes(monkeypatch, tmp_path, mode):
    calls = []

    def fail_load(_artifact_dir):
        calls.append(1)
        raise ValueError("frozen model hash mismatch")

    monkeypatch.setattr(FrozenFFTRescue, "load", fail_load)
    (tmp_path / "unused.png").write_bytes(b"present")
    result = evaluate_authenticity_images(
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": mode}),
        raw_observations=[raw("n0")],
        expected_image_ids=["n0"],
        image_paths={"n0": tmp_path / "unused.png"},
    )
    assert calls == [1]
    assert result.service_failure is True
    assert result.would_manual is True
    assert result.image_results["n0"].result == "manual_review"
    assert result.image_results["n0"].status == "artifact_load_failure"
    assert "hash mismatch" in result.image_results["n0"].evidence_summary["error"]


def test_gate_off_is_exact_noop_and_does_not_touch_dependencies():
    legacy = {"manual_flag": "否", "manual_reason_code": "", "sentinel": [1, 2]}
    result = apply_photo_authenticity_gate(
        legacy_row=legacy, compliance={}, images=[], config=PhotoAuthenticityConfig.from_env({}),
        fallback=lambda: pytest.fail("fallback called"), evaluator=lambda **_: pytest.fail("evaluator called"),
    )
    assert result is legacy


@pytest.mark.parametrize("mode, expected_flag", [("shadow", "否"), ("enforce", "是")])
def test_gate_shadow_records_candidate_but_only_enforce_changes_legacy_pass(mode, expected_flag):
    legacy = {"manual_flag": "否", "manual_reason_code": "", "manual_reason": ""}
    evaluated = AuthenticityOrderResult(mode, True, {"i1": AuthenticityImageResult("i1", "high_risk_non_real", "R1")})
    result = apply_photo_authenticity_gate(
        legacy_row=legacy, compliance={"photo_authenticity_by_image": [raw("i1")]},
        images=[{"image_id": "i1", "local_path": "unused.jpg"}],
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": mode}), evaluator=lambda **_: evaluated,
    )
    assert result["manual_flag"] == expected_flag
    assert result["photo_authenticity_would_manual"] is True
    if mode == "enforce":
        assert result["manual_reason_code"] == "NON_REAL_PHOTO_STRONG_RISK"


def test_gate_never_overwrites_or_runs_for_legacy_manual():
    legacy = {"manual_flag": "是", "manual_reason_code": "SN_MISMATCH", "manual_reason": "old"}
    result = apply_photo_authenticity_gate(
        legacy_row=legacy, compliance={}, images=[],
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "enforce"}),
        fallback=lambda: pytest.fail("fallback called"), evaluator=lambda **_: pytest.fail("evaluator called"),
    )
    assert result is legacy
    assert result["manual_reason_code"] == "SN_MISMATCH"


def test_gate_does_not_fallback_when_more_than_one_image_is_invalid():
    calls = []
    legacy = {"manual_flag": "否", "manual_reason_code": "", "manual_reason": ""}
    result = apply_photo_authenticity_gate(
        legacy_row=legacy, compliance={},
        images=[{"image_id": "i1", "local_path": "a.jpg"}, {"image_id": "i2", "local_path": "b.jpg"}],
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "enforce"}),
        fallback=lambda _image: calls.append(1) or None,
    )
    assert calls == []
    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "PHOTO_AUTHENTICITY_SERVICE_FAILURE"


def test_gate_shadow_schema_failure_preserves_legacy_result():
    legacy = {"manual_flag": "否", "manual_reason_code": "", "manual_reason": ""}
    result = apply_photo_authenticity_gate(
        legacy_row=legacy, compliance={}, images=[{"image_id": "i1", "local_path": "a.jpg"}],
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "shadow"}), fallback=lambda _image: None,
    )
    assert result["manual_flag"] == "否"
    assert result["photo_authenticity_would_manual"] is True
    assert result["photo_authenticity_service_failure"] is True


def test_fft_does_not_start_when_order_budget_is_exhausted(tmp_path):
    calls = []
    class Rescue:
        threshold = 0.995
        def score(self, _path):
            calls.append(1)
            return 0.1, {}
    result = evaluate_authenticity_images(
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "enforce"}),
        raw_observations=[raw("i1")], expected_image_ids=["i1"],
        image_paths={"i1": tmp_path / "x.jpg"}, rescue=Rescue(), budget_available=lambda: False,
    )
    assert calls == []
    assert result.service_failure is True
    assert result.image_results["i1"].status == "order_budget_exhausted"


def test_missing_local_file_fails_once_without_fft_decode_retry(tmp_path):
    calls = []
    class Rescue:
        threshold = 0.995
        def score(self, _path):
            calls.append(1)
            raise AssertionError("score must not run")
    result = evaluate_authenticity_images(
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "enforce"}),
        raw_observations=[raw("i1")], expected_image_ids=["i1"],
        image_paths={"i1": tmp_path / "missing.jpg"}, rescue=Rescue(),
    )
    assert calls == []
    assert result.image_results["i1"].status == "image_file_missing"


def test_gate_does_not_start_fallback_when_budget_is_exhausted():
    calls = []
    legacy = {"manual_flag": "否", "manual_reason_code": "", "manual_reason": ""}
    result = apply_photo_authenticity_gate(
        legacy_row=legacy, compliance={}, images=[{"image_id": "i1", "local_path": "a.jpg"}],
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "enforce"}),
        fallback=lambda _image: calls.append(1), budget_available=lambda: False,
    )
    assert calls == []
    assert result["manual_reason_code"] == "PHOTO_AUTHENTICITY_SERVICE_FAILURE"


def test_gate_single_invalid_image_uses_one_single_image_fallback():
    calls = []
    legacy = {"manual_flag": "否", "manual_reason_code": "", "manual_reason": ""}
    fallback_observation = raw("i2", strong_evidence=[evidence("EXTERNAL_PHOTO_CARRIER", "background")])
    evaluated = AuthenticityOrderResult(
        "enforce", True, {"i1": AuthenticityImageResult("i1", "no_evidence", "R9"),
                           "i2": AuthenticityImageResult("i2", "high_risk_non_real", "R1")},
    )
    def fallback(image):
        calls.append(image["image_id"])
        return fallback_observation
    evaluations = []
    def evaluator(**_kwargs):
        evaluations.append(1)
        if len(evaluations) == 1:
            raise PhotoAuthenticitySchemaError("missing i2")
        return evaluated
    result = apply_photo_authenticity_gate(
        legacy_row=legacy,
        compliance={"photo_authenticity_by_image": [raw("i1")]},
        images=[{"image_id": "i1", "local_path": "a.jpg"}, {"image_id": "i2", "local_path": "b.jpg"}],
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "enforce"}),
        fallback=fallback, evaluator=evaluator,
    )
    assert calls == ["i2"]
    assert result["manual_reason_code"] == "NON_REAL_PHOTO_STRONG_RISK"


def test_strong_reason_wins_over_service_failure():
    result = AuthenticityOrderResult(
        "enforce", True,
        {"strong": AuthenticityImageResult("strong", "high_risk_non_real", "R1"),
         "failed": AuthenticityImageResult("failed", "manual_review", "FFT_FAILURE", status="failed_after_retries")},
        service_failure=True,
    )
    legacy = {"manual_flag": "否", "manual_reason_code": "", "manual_reason": ""}
    row = apply_photo_authenticity_gate(
        legacy_row=legacy, compliance={"photo_authenticity_by_image": [raw("strong"), raw("failed")]},
        images=[{"image_id": "strong"}, {"image_id": "failed"}],
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "enforce"}), evaluator=lambda **_: result,
    )
    assert row["manual_reason_code"] == "NON_REAL_PHOTO_STRONG_RISK"
