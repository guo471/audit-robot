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
    PhotoAuthenticityConfig,
    PhotoAuthenticitySchemaError,
    derive_v4_result,
    evaluate_authenticity_images,
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
