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
    EXPECTED_V4_PROMPT_SHA256,
    FrozenFFTRescue,
    AuthenticityImageResult,
    AuthenticityOrderResult,
    PhotoAuthenticityConfig,
    PhotoAuthenticitySchemaError,
    derive_v4_result,
    evaluate_authenticity_images,
    apply_photo_authenticity_gate,
    load_approved_v4_prompt,
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


def test_mode_defaults_enforce_and_rejects_unknown_value():
    assert PhotoAuthenticityConfig.from_env({}).mode == "enforce"
    assert PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "off"}).mode == "off"
    assert PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "shadow"}).mode == "shadow"
    with pytest.raises(ValueError, match="PHOTO_AUTHENTICITY_MODE"):
        PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "maybe"})


def test_fft_enabled_config_defaults_false_and_parses_strict_boolean():
    assert PhotoAuthenticityConfig.from_env({}).fft_enabled is False
    assert PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_FFT_ENABLED": "true"}).fft_enabled is True
    assert PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_FFT_ENABLED": "0"}).fft_enabled is False
    with pytest.raises(ValueError, match="PHOTO_AUTHENTICITY_FFT_ENABLED"):
        PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_FFT_ENABLED": "sometimes"})


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


@pytest.mark.parametrize("code", sorted({"EDGE_CUTOFF", "OUTER_PLANE_OPTICS", "PLANAR_APPEARANCE", "LOCAL_MOIRE", "UI_CANDIDATE"}))
def test_all_structured_weak_evidence_routes_manual_even_when_reason_says_real(code):
    observation = validate_image_observations([
        raw(weak_evidence=[evidence(code, "background")], reason="正常实拍，不是翻拍")
    ], ["i1"])["i1"]
    assert derive_v4_result(observation) == ("manual_review", "R9")


def test_bare_abrupt_cutoff_without_structured_evidence_is_normal_crop():
    edges = {side: "scene_continues" for side in SIDES} | {"right": "abrupt_cutoff"}
    observation = validate_image_observations([raw(edges=edges, reason="标签没有完全拍到")], ["i1"])["i1"]
    assert derive_v4_result(observation) == ("no_evidence", "R9")


def test_product_screen_local_moire_only_is_exempt_when_scene_is_continuous():
    observation = validate_image_observations([
        raw(
            screen_owner="product_screen",
            weak_evidence=[evidence("LOCAL_MOIRE", "product_screen")],
            reason="真实设备屏幕近拍产生的正常摩尔纹",
        )
    ], ["i1"])["i1"]
    assert derive_v4_result(observation) == ("no_evidence", "R10_PRODUCT_SCREEN_LOCAL_MOIRE_EXEMPT")


@pytest.mark.parametrize(
    "overrides",
    [
        {"screen_owner": "external_screen", "weak_evidence": [evidence("LOCAL_MOIRE", "product_screen")]},
        {"screen_owner": "product_screen", "weak_evidence": [evidence("LOCAL_MOIRE", "product_screen", "background")]},
        {"screen_owner": "product_screen", "weak_evidence": [evidence("LOCAL_MOIRE", "product_screen"), evidence("PLANAR_APPEARANCE", "product_body")]},
        {"screen_owner": "product_screen", "weak_evidence": [evidence("LOCAL_MOIRE", "product_screen")],
         "edges": {"top": "scene_continues", "right": "abrupt_cutoff", "bottom": "scene_continues", "left": "scene_continues"}},
    ],
)
def test_product_screen_local_moire_exemption_is_withheld_when_any_guard_fails(overrides):
    observation = validate_image_observations([raw(**overrides)], ["i1"])["i1"]
    assert derive_v4_result(observation)[0] == "manual_review"


@pytest.mark.parametrize(
    "weak_evidence",
    [
        [evidence("LOCAL_MOIRE", "package")],
        [evidence("OUTER_PLANE_OPTICS", "product_body")],
        [evidence("LOCAL_MOIRE", "product_screen"), evidence("OUTER_PLANE_OPTICS", "package")],
    ],
)
def test_r9_benign_local_moire_and_outer_plane_optics_are_exempt_with_guards(weak_evidence):
    observation = validate_image_observations([raw(weak_evidence=weak_evidence)], ["i1"])["i1"]
    assert derive_v4_result(observation) == ("no_evidence", "R9")


@pytest.mark.parametrize(
    "overrides",
    [
        {"screen_owner": "external_screen", "weak_evidence": [evidence("LOCAL_MOIRE", "package")]},
        {"screen_owner": "uncertain", "weak_evidence": [evidence("LOCAL_MOIRE", "package")]},
        {"weak_evidence": [evidence("LOCAL_MOIRE", "background")]},
        {"weak_evidence": [evidence("LOCAL_MOIRE", "package"), evidence("LOCAL_MOIRE", "background")]},
        {"weak_evidence": [evidence("OUTER_PLANE_OPTICS", "hand")]},
        {"weak_evidence": [evidence("LOCAL_MOIRE", "unknown")]},
        {"weak_evidence": [evidence("LOCAL_MOIRE", "package"), evidence("PLANAR_APPEARANCE", "product_body")]},
        {"strong_evidence": [evidence("CROSS_OBJECT_MOIRE", "product_body")],
         "weak_evidence": [evidence("LOCAL_MOIRE", "package")]},
        {"weak_evidence": [evidence("LOCAL_MOIRE", "package")],
         "edges": {"top": "scene_continues", "right": "abrupt_cutoff", "bottom": "scene_continues", "left": "scene_continues"}},
    ],
)
def test_r9_benign_weak_exemption_is_withheld_when_any_guard_fails(overrides):
    observation = validate_image_observations([raw(**overrides)], ["i1"])["i1"]
    assert derive_v4_result(observation) == ("manual_review", "R9")


def test_r9_benign_weak_exemption_does_not_override_r8():
    observation = validate_image_observations([
        raw(
            weak_evidence=[evidence("OUTER_PLANE_OPTICS", "package")],
            edges={"top": "scene_continues", "right": "abrupt_cutoff", "bottom": "scene_continues", "left": "scene_continues"},
        )
    ], ["i1"])["i1"]
    assert derive_v4_result(observation) == ("high_risk_non_real", "R8")


def test_r9_benign_weak_exemption_does_not_override_r7():
    observation = validate_image_observations([
        raw(
            weak_evidence=[evidence("LOCAL_MOIRE", "package")],
            edges={"top": "scene_continues", "right": "carrier_boundary", "bottom": "scene_continues", "left": "scene_continues"},
        )
    ], ["i1"])["i1"]
    assert derive_v4_result(observation) == ("manual_review", "R7")


def test_r9_benign_weak_exemption_allows_product_screen_viewer_ui_exempt_strong():
    observation = validate_image_observations([
        raw(
            screen_owner="product_screen",
            strong_evidence=[evidence("PHOTO_VIEWER_UI", "product_screen")],
            weak_evidence=[evidence("LOCAL_MOIRE", "product_screen")],
        )
    ], ["i1"])["i1"]
    assert derive_v4_result(observation) == ("no_evidence", "R10_PRODUCT_SCREEN_LOCAL_MOIRE_EXEMPT")


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
        config=PhotoAuthenticityConfig.from_env({
            "PHOTO_AUTHENTICITY_MODE": "enforce", "PHOTO_AUTHENTICITY_FFT_ENABLED": "true",
        }),
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


def test_fft_disabled_by_default_keeps_no_evidence_and_performs_zero_io(monkeypatch, tmp_path):
    monkeypatch.setattr(FrozenFFTRescue, "load", lambda *_: pytest.fail("FFT artifact must not load"))
    result = evaluate_authenticity_images(
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "enforce"}),
        raw_observations=[raw("i1")],
        expected_image_ids=["i1"],
        image_paths={"i1": tmp_path / "missing.jpg"},
    )
    assert result.would_manual is False
    assert result.service_failure is False
    assert result.image_results["i1"].result == "no_evidence"
    assert result.image_results["i1"].score is None


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_artifact_load_failure_is_service_failure_and_never_silently_passes(monkeypatch, tmp_path, mode):
    calls = []

    def fail_load(_artifact_dir):
        calls.append(1)
        raise ValueError("frozen model hash mismatch")

    monkeypatch.setattr(FrozenFFTRescue, "load", fail_load)
    (tmp_path / "unused.png").write_bytes(b"present")
    result = evaluate_authenticity_images(
        config=PhotoAuthenticityConfig.from_env({
            "PHOTO_AUTHENTICITY_MODE": mode, "PHOTO_AUTHENTICITY_FFT_ENABLED": "true",
        }),
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
        legacy_row=legacy, compliance={}, images=[],
        config=PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "off"}),
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
        config=PhotoAuthenticityConfig.from_env({
            "PHOTO_AUTHENTICITY_MODE": "enforce", "PHOTO_AUTHENTICITY_FFT_ENABLED": "true",
        }),
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
        config=PhotoAuthenticityConfig.from_env({
            "PHOTO_AUTHENTICITY_MODE": "enforce", "PHOTO_AUTHENTICITY_FFT_ENABLED": "true",
        }),
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
        config=PhotoAuthenticityConfig.from_env({
            "PHOTO_AUTHENTICITY_MODE": "enforce", "PHOTO_AUTHENTICITY_FFT_ENABLED": "true",
        }),
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
        config=PhotoAuthenticityConfig.from_env({
            "PHOTO_AUTHENTICITY_MODE": "enforce", "PHOTO_AUTHENTICITY_FFT_ENABLED": "true",
        }),
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


def test_approved_v4_prompt_has_frozen_sha_and_loads_current_file():
    expected = "7d4e7224b38c5fc5cbb9293f6d72b091df39d4dc9efb2e983b0daa829a43ca77"
    path = Path("photo_authenticity/prompts/non_real_photo_auditor_v4.txt")
    assert EXPECTED_V4_PROMPT_SHA256 == expected
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    assert load_approved_v4_prompt(path) == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["tampered", "missing"])
def test_approved_v4_prompt_loader_rejects_tampering_and_missing(tmp_path, kind):
    path = tmp_path / "v4.txt"
    if kind == "tampered":
        path.write_text("changed", encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError), match="prompt|No such file"):
        load_approved_v4_prompt(path)
