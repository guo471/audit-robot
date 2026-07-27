# -*- coding: utf-8 -*-
import hashlib
import http.server
import json
from pathlib import Path
import threading
import time

import pytest

from tools import run_guobu_model_audit_v2 as v2
from tools.run_guobu_model_audit_v2 import (
    audit_task_hybrid,
    audit_task_fast,
    audit_task_v2,
    build_sn_payload,
    group_images_by_title,
    has_duplicate_cross_group_images,
    is_address_precise_enough,
    normalize_sn,
    precheck_task,
)


@pytest.fixture(autouse=True)
def _keep_legacy_sn_tests_on_v1(monkeypatch):
    monkeypatch.setenv("SN_POLICY_VERSION", "v1")


def _auth_observation(image_id):
    return {
        "image_id": image_id,
        "edges": {"top": "scene_continues", "right": "scene_continues", "bottom": "scene_continues", "left": "scene_continues"},
        "screen_owner": "none",
        "strong_evidence": [],
        "weak_evidence": [],
        "reason": "未发现异常",
    }


def test_compliance_prompt_authenticity_addendum_is_opt_in_and_identical_for_all_categories():
    categories = ("home_appliance", "computer", "ordinary_3c", "unknown")
    original = {
        category: v2.compliance_prompt_for_category(
            category, digital_activation_evidence_mode="off",
        )
        for category in categories
    }

    for category in categories:
        assert v2.compliance_prompt_for_category(
            category,
            include_photo_authenticity=False,
            digital_activation_evidence_mode="off",
        ) == original[category]
        merged = v2.compliance_prompt_for_category(
            category,
            include_photo_authenticity=True,
            sn_label_auth_review_mode="off",
            digital_activation_evidence_mode="off",
        )
        assert merged == original[category] + v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM
        assert merged.count(v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM) == 1
        assert merged.count('"photo_authenticity_by_image"') == 1


def test_plugin_off_compliance_prompts_match_frozen_exact_duplicate_policy_baselines():
    # These hashes include the approved global exact-duplicate wording while
    # keeping the digital activation plugin disabled.
    expected = {
        "home_appliance": "9b0127f2ee8d45168c6c37d4b106a20db256a7ec3884eef85db902639409c96c",
        "computer": "a8df6e1d25ad95bc42376158666179097b9ec95c839b9ebe2c12f69e4fd00c79",
        "ordinary_3c": "df4323fc3d3564d2c64f02b6d6129fd334b41e1172687a66d2ddc6c19f82bb81",
        "unknown": "837429578a41c19c006e95ad8b482773e6e333c931078688442984f087db5f1f",
    }

    actual = {
        category: hashlib.sha256(
            v2.compliance_prompt_for_category(
                category,
                include_photo_authenticity=False,
                digital_activation_evidence_mode="off",
            ).encode("utf-8")
        ).hexdigest()
        for category in expected
    }

    assert actual == expected


@pytest.mark.parametrize("image_ids", [("a", "b", "c"), ("a", "b", "c", "d", "e", "f")])
def test_normalize_photo_authenticity_requires_exact_order_independent_image_coverage(image_ids):
    compliance = {"photo_authenticity_by_image": [_auth_observation(image_id) for image_id in reversed(image_ids)]}

    normalized = v2._normalize_photo_authenticity_observations(compliance, image_ids)

    assert list(normalized) == list(image_ids)
    assert [item["image_id"] for item in compliance["photo_authenticity_by_image"]] == list(image_ids)
    json.dumps(compliance["photo_authenticity_by_image"], ensure_ascii=False)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [_auth_observation("a"), _auth_observation("a")],
        [_auth_observation("a")],
        [_auth_observation("a"), _auth_observation("b"), _auth_observation("extra")],
        [{**_auth_observation("a"), "screen_owner": "television"}, _auth_observation("b")],
    ],
)
def test_normalize_photo_authenticity_rejects_invalid_or_inexact_observations(raw):
    compliance = {"photo_authenticity_by_image": raw}

    with pytest.raises(v2.PhotoAuthenticitySchemaError):
        v2._normalize_photo_authenticity_observations(compliance, ("a", "b"))


def test_hybrid_enforce_routes_incomplete_authenticity_structure_manual_without_extra_model_call(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    calls = []

    def fake_call(_base, _key, _model, prompt, payload, images, *, stage, **kwargs):
        calls.append((stage, prompt, kwargs))
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        if stage == "hybrid_compliance":
            decision = _screen_sn_compliance_pass()
            decision["photo_authenticity_by_image"] = [_auth_observation("i1"), _auth_observation("i2")]
            return (decision, "compliance", 0.1, {}, False)
        raise AssertionError(f"unexpected model call: {stage}")

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)
    task = _base_task()
    for image_id, image in zip(("i1", "i2", "i3"), task["images"]):
        image["image_id"] = image_id
    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", task)
    assert [stage for stage, _, _ in calls] == ["hybrid_sn", "hybrid_compliance"]
    assert v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM in calls[1][1]
    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "PHOTO_AUTHENTICITY_SERVICE_FAILURE"
    assert result["photo_authenticity_fallback_calls"] == 0


def test_hybrid_enforce_routes_package_local_moire_manual_under_legacy_r9(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    monkeypatch.setenv("SN_LABEL_AUTH_REVIEW_MODE", "off")

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        decision = _screen_sn_compliance_pass()
        observations = [_auth_observation(image_id) for image_id in ("i1", "i2", "i3")]
        observations[2]["weak_evidence"] = [{"code": "LOCAL_MOIRE", "regions": ["package"]}]
        observations[2]["reason"] = "正常微距拍摄，属于实拍"
        decision["photo_authenticity_by_image"] = observations
        return (decision, "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)
    task = _base_task()
    for image_id, image in zip(("i1", "i2", "i3"), task["images"]):
        image["image_id"] = image_id

    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", task)

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "NON_REAL_PHOTO_REVIEW"
    assert result["photo_authenticity_would_manual"] is True
    assert result["photo_authenticity_manual_count"] == 1
    assert result["photo_authenticity_fft_count"] == 0


def test_hybrid_enforce_routes_background_local_moire_manual_even_when_reason_says_real(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    monkeypatch.setenv("SN_LABEL_AUTH_REVIEW_MODE", "off")

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        decision = _screen_sn_compliance_pass()
        observations = [_auth_observation(image_id) for image_id in ("i1", "i2", "i3")]
        observations[2]["weak_evidence"] = [{"code": "LOCAL_MOIRE", "regions": ["background"]}]
        observations[2]["reason"] = "正常微距拍摄，属于实拍"
        decision["photo_authenticity_by_image"] = observations
        return (decision, "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)
    task = _base_task()
    for image_id, image in zip(("i1", "i2", "i3"), task["images"]):
        image["image_id"] = image_id

    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", task)

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "NON_REAL_PHOTO_REVIEW"
    assert result["photo_authenticity_manual_count"] == 1


def test_hybrid_enforce_routes_package_outer_plane_optics_manual_under_legacy_r9(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    monkeypatch.setenv("SN_LABEL_AUTH_REVIEW_MODE", "off")

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        decision = _screen_sn_compliance_pass()
        observations = [_auth_observation(image_id) for image_id in ("i1", "i2", "i3")]
        observations[2]["weak_evidence"] = [{"code": "OUTER_PLANE_OPTICS", "regions": ["package"]}]
        decision["photo_authenticity_by_image"] = observations
        return (decision, "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)
    task = _base_task()
    for image_id, image in zip(("i1", "i2", "i3"), task["images"]):
        image["image_id"] = image_id

    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", task)

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "NON_REAL_PHOTO_REVIEW"
    assert result["photo_authenticity_would_manual"] is True
    assert result["photo_authenticity_manual_count"] == 1


def test_hybrid_enforce_keeps_carrier_boundary_with_benign_weak_manual(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    monkeypatch.setenv("SN_LABEL_AUTH_REVIEW_MODE", "off")

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        decision = _screen_sn_compliance_pass()
        observations = [_auth_observation(image_id) for image_id in ("i1", "i2", "i3")]
        observations[2]["edges"]["right"] = "carrier_boundary"
        observations[2]["weak_evidence"] = [{"code": "LOCAL_MOIRE", "regions": ["package"]}]
        decision["photo_authenticity_by_image"] = observations
        return (decision, "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)
    task = _base_task()
    for image_id, image in zip(("i1", "i2", "i3"), task["images"]):
        image["image_id"] = image_id

    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", task)

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "NON_REAL_PHOTO_REVIEW"
    assert result["photo_authenticity_manual_count"] == 1


def test_hybrid_enforce_exempts_only_product_screen_local_moire(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    monkeypatch.setenv("SN_LABEL_AUTH_REVIEW_MODE", "off")

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        decision = _screen_sn_compliance_pass()
        observations = [_auth_observation(image_id) for image_id in ("i1", "i2", "i3")]
        observations[2]["screen_owner"] = "product_screen"
        observations[2]["weak_evidence"] = [{"code": "LOCAL_MOIRE", "regions": ["product_screen"]}]
        observations[2]["reason"] = "真实设备屏幕拍摄产生的正常局部摩尔纹"
        decision["photo_authenticity_by_image"] = observations
        return (decision, "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)
    task = _base_task()
    for image_id, image in zip(("i1", "i2", "i3"), task["images"]):
        image["image_id"] = image_id

    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", task)

    assert result["manual_flag"] == "否"
    assert result["photo_authenticity_would_manual"] is False
    assert result["photo_authenticity_manual_count"] == 0


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_hybrid_prompt_asset_failure_never_starts_fallback_model_or_touches_cache(monkeypatch, tmp_path, mode):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", mode)
    stages = []
    def fake_call(_base, _key, _model, prompt, payload, images, *, stage, **kwargs):
        stages.append(stage)
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        decision = _screen_sn_compliance_pass()
        decision["photo_authenticity_by_image"] = [_auth_observation("i1"), _auth_observation("i2")]
        return (decision, "compliance", 0.1, {}, False)
    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)
    monkeypatch.setattr(v2, "load_approved_v4_prompt", lambda _path: (_ for _ in ()).throw(ValueError("prompt hash mismatch")), raising=False)
    monkeypatch.setattr(v2, "_cache_key", lambda *_args, **_kwargs: "invalid")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    sentinel = cache_dir / "invalid.json"
    sentinel.write_text("must remain byte-for-byte", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in cache_dir.iterdir()}
    task = _base_task()
    for image_id, image in zip(("i1", "i2", "i3"), task["images"]):
        image["image_id"] = image_id
    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", task, cache_dir=cache_dir)
    assert stages == ["hybrid_sn", "hybrid_compliance"]
    assert {path.name: path.read_bytes() for path in cache_dir.iterdir()} == before
    assert result["photo_authenticity_service_failure"] is True
    assert result["manual_flag"] == ("是" if mode == "enforce" else "否")


def test_hybrid_off_keeps_original_prompt_stage_and_never_calls_gate(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    calls = []

    def fake_call(_base, _key, _model, prompt, payload, images, *, stage, **kwargs):
        calls.append((stage, prompt))
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        return (_screen_sn_compliance_pass(), "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "apply_photo_authenticity_gate", lambda **_: pytest.fail("gate called in off mode"), raising=False)
    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", _base_task())
    assert calls[1] == (
        "hybrid_compliance",
        v2.compliance_prompt_for_category("ordinary_3c", product_type="手机 [B01]"),
    )
    assert result["manual_flag"] == "否"


def test_merged_authenticity_unknown_schema_is_not_cacheable():
    images = [{"image_id": "a"}, {"image_id": "b"}]
    assert v2._is_cacheable_model_result(
        "hybrid_compliance", v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM,
        {"photo_authenticity_by_image": [_auth_observation("a"), _auth_observation("b")]}, images,
    ) is True
    assert v2._is_cacheable_model_result(
        "hybrid_compliance", v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM, {}, images,
    ) is False
    assert v2._is_cacheable_model_result(
        "hybrid_photo_authenticity_fallback", v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM, {}, images,
    ) is False
    single = _auth_observation("a")
    single.pop("image_id")
    single["result"] = "no_evidence"
    assert v2._is_cacheable_model_result(
        "hybrid_photo_authenticity_fallback", "approved v4", single, [{"image_id": "a"}],
    ) is True
    assert v2._is_cacheable_model_result(
        "hybrid_photo_authenticity_fallback", "approved v4", {}, [{"image_id": "a"}],
    ) is False
    assert v2._is_cacheable_model_result("hybrid_compliance", "legacy", {}, images) is True


def _base_task():
    return {
        "channel_order_no": "1",
        "fields": {
            "product_type": "手机 [B01]",
            "system_sn": "ABC123",
            "is_home_appliance": False,
            "address": "",
        },
        "images": [
            {"image_id": "img_001", "title": "鍟嗗搧鐓х墖", "source_url": "a"},
            {"image_id": "img_002", "title": "鎷嗗皝鐓х墖", "source_url": "b"},
            {"image_id": "img_003", "title": "SN photo", "source_url": "c"},
        ],
    }


def _screen_sn_compliance_pass():
    return {
        "manual_required": False,
        "manual_reason_codes": [],
        "manual_reason": "",
        "product_type_match": True,
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "package_visible": True,
        "activation_photo_ok": True,
        "activation_evidence_type": "SCREEN_SN",
        "sn_candidates": [
            {
                "image_id": "img_003",
                "source": "SCREEN",
                "raw_text": "ABC123",
                "normalized_text": "ABC123",
                "readable": True,
                "matches_system_sn": True,
            }
        ],
        "activation_screen": {
            "screen_on": True,
            "screen_content_type": "ABOUT_DEVICE_SN",
            "screen_sn_visible": True,
            "screen_sn_text": "ABC123",
        },
        "activation_identity_by_image": [
            {
                "image_id": "img_003",
                "screen_on": True,
                "screen_source": "PRODUCT_DEVICE_SCREEN",
                "page_type": "DEVICE_INFO",
                "identity_fields": [
                    {
                        "field_type": "SN",
                        "raw_value": "ABC123",
                        "readable": True,
                        "complete": True,
                    }
                ],
            }
        ],
        "photo_integrity": {
            "collage_or_edit_risk": False,
            "evidence_chain_trustworthy": True,
        },
        "image_risk": False,
        "duplicate_image_evidence": False,
        "confidence": 0.9,
    }


def test_normalize_sn_ignores_case_and_punctuation():
    assert normalize_sn("S/N: 7urbb-26409200425") == "7URBB26409200425"
    assert normalize_sn("511-320Q1063-A815-1040160") == "511320Q1063A8151040160"
    assert normalize_sn("ABC_123/45") == "ABC12345"


def test_call_model_disables_qwen_thinking_for_sn_stage(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "{\"ok\": true}"}}], "usage": {}}

    monkeypatch.setattr(v2, "_wait_before_model_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        v2,
        "_post_chat_completion_json",
        lambda base_url, api_key, body, read_timeout_sec=60: (
            captured.update({"body": body})
            or {"choices": [{"message": {"content": "{\"ok\": true}"}}], "usage": {}}
        ),
    )

    parsed, *_ = v2.call_model(
        "https://example.test",
        "key",
        "qwen3.7-plus",
        "prompt",
        {"id": "1"},
        [],
        stage="hybrid_sn",
    )

    assert parsed == {"ok": True}
    assert captured["body"]["enable_thinking"] is False


def test_call_model_disables_qwen_thinking_for_compliance_stage(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "{\"ok\": true}"}}], "usage": {}}

    monkeypatch.setattr(v2, "_wait_before_model_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        v2,
        "_post_chat_completion_json",
        lambda base_url, api_key, body, read_timeout_sec=60: (
            captured.update({"body": body})
            or {"choices": [{"message": {"content": "{\"ok\": true}"}}], "usage": {}}
        ),
    )

    v2.call_model(
        "https://example.test",
        "key",
        "qwen3.7-plus",
        "prompt",
        {"id": "1"},
        [],
        stage="hybrid_compliance",
    )

    assert captured["body"]["enable_thinking"] is False


def test_direct_sn_ocr_uses_plain_text_without_system_sn_and_disables_qwen_thinking(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "SN: ABC123"}}], "usage": {"total_tokens": 7}}

    monkeypatch.setattr(v2, "_wait_before_model_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        v2,
        "_post_chat_completion_json",
        lambda base_url, api_key, body, read_timeout_sec=60: (
            captured.update({"body": body})
            or {"choices": [{"message": {"content": "SN: ABC123"}}], "usage": {"total_tokens": 7}}
        ),
    )

    observed, _elapsed, usage, cached = v2.call_direct_sn_ocr(
        "https://example.test",
        "key",
        "qwen3.7-plus",
        [{"source_url": "https://example.test/sn.jpg", "_detail": "high"}],
    )

    assert observed == "ABC123"
    assert usage["total_tokens"] == 7
    assert cached is False
    assert captured["body"]["enable_thinking"] is False
    assert "response_format" not in captured["body"]
    serialized = json.dumps(captured["body"], ensure_ascii=False)
    assert "system_sn" not in serialized
    assert "ABC123" not in serialized


def test_direct_sn_ocr_cache_changes_when_local_image_content_changes(monkeypatch, tmp_path):
    image_path = tmp_path / "sn.jpg"
    image_path.write_bytes(b"first")
    responses = iter(["FIRST123", "SECOND456"])
    calls = []

    def fake_post(*_args, **_kwargs):
        calls.append(True)
        return {
            "choices": [{"message": {"content": next(responses)}}],
            "usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(v2, "_post_chat_completion_json", fake_post)
    images = [{"image_id": "i1", "local_path": str(image_path), "_detail": "high"}]

    first, *_rest, first_cached = v2.call_direct_sn_ocr(
        "https://example.test", "key", "qwen3.7-plus", images, cache_dir=tmp_path / "cache",
    )
    image_path.write_bytes(b"second")
    second, *_rest, second_cached = v2.call_direct_sn_ocr(
        "https://example.test", "key", "qwen3.7-plus", images, cache_dir=tmp_path / "cache",
    )

    assert first == "FIRST123"
    assert second == "SECOND456"
    assert first_cached is False
    assert second_cached is False
    assert len(calls) == 2


def test_model_request_buffer_waits_until_three_seconds_after_previous_request(monkeypatch):
    sleeps = []
    moments = iter([101.0, 103.0])

    monkeypatch.setattr(v2.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(v2.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(v2, "_last_model_request_at", 100.0)

    v2._wait_before_model_request()

    assert sleeps == [2.0]
    assert v2._last_model_request_at == 103.0


def test_chat_completion_throttle_respects_stage_deadline(monkeypatch):
    monkeypatch.setattr(v2, "MODEL_REQUEST_BUFFER_SEC", 0.20)
    monkeypatch.setattr(v2, "_last_model_request_at", time.monotonic())
    monkeypatch.setattr(
        v2,
        "_http_connection_for_url",
        lambda *_args, **_kwargs: pytest.fail("connection should not start after throttle deadline"),
    )

    started = time.perf_counter()
    with pytest.raises(v2.OrderBudgetExceeded):
        v2._post_chat_completion_json(
            "http://127.0.0.1:1/v1",
            "key",
            {"model": "qwen3.7-plus"},
            read_timeout_sec=0.05,
        )

    assert time.perf_counter() - started < 0.12


def test_model_request_buffer_lock_wait_respects_stage_deadline(monkeypatch):
    monkeypatch.setattr(v2, "MODEL_REQUEST_BUFFER_SEC", 0.20)
    monkeypatch.setattr(v2, "_last_model_request_at", None)
    lock_acquired = threading.Event()

    def hold_model_request_lock():
        with v2._model_request_lock:
            lock_acquired.set()
            time.sleep(0.20)

    holder = threading.Thread(target=hold_model_request_lock)
    holder.start()
    assert lock_acquired.wait(timeout=1)

    started = time.perf_counter()
    try:
        with pytest.raises(v2.OrderBudgetExceeded):
            v2._wait_before_model_request(time.time() + 0.05)
        elapsed = time.perf_counter() - started
    finally:
        holder.join(timeout=1)

    assert elapsed < 0.12


def test_chat_completion_retries_connect_failure_with_one_absolute_stage_deadline(monkeypatch):
    calls = []
    fake_now = {"value": 1000.0}

    class FakeHTTPResponse:
        status = 200
        reason = "OK"

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}], "usage": {}}).encode()

    class FakeSocket:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

    class FakeConnection:
        def __init__(self, host, timeout):
            self.host = host
            self.timeout = timeout
            self.sock = FakeSocket()
            calls.append(self)

        def request(self, method, path, body=None, headers=None):
            if len(calls) == 1:
                fake_now["value"] += 5.0
                raise TimeoutError("connect timed out")
            fake_now["value"] += 2.0

        def getresponse(self):
            return FakeHTTPResponse()

        def close(self):
            return None

    monkeypatch.setattr(v2, "_wait_before_model_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(v2.time, "time", lambda: fake_now["value"])
    monkeypatch.setattr(v2, "_http_connection_for_url", lambda parsed_url, timeout: FakeConnection(parsed_url.netloc, timeout))

    response = v2._post_chat_completion_json(
        "https://example.test/v1",
        "key",
        {"model": "qwen3.7-plus"},
        read_timeout_sec=60,
    )

    assert response["choices"][0]["message"]["content"] == "{\"ok\": true}"
    assert len(calls) == 2
    assert all(call.timeout == 5 for call in calls)
    assert calls[1].sock.timeouts == [53.0]


def test_chat_completion_enforces_stage_deadline_during_progressive_body(monkeypatch):
    response_body = json.dumps(
        {"choices": [{"message": {"content": "{\"ok\": true}"}}], "usage": {}}
    ).encode("utf-8")

    class SlowBodyHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            for byte in response_body:
                try:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                except OSError:
                    break
                time.sleep(0.01)

        def log_message(self, format, *args):
            return None

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowBodyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(v2, "_wait_before_model_request", lambda *_args, **_kwargs: None)

    try:
        with pytest.raises(v2.OrderBudgetExceeded):
            v2._post_chat_completion_json(
                f"http://127.0.0.1:{server.server_address[1]}/v1",
                "key",
                {"model": "qwen3.7-plus"},
                read_timeout_sec=0.05,
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sn_only_audit_reads_direct_sn_without_compliance_or_auto_pass(monkeypatch):
    task = _base_task()
    calls = []

    def fake_call_direct_sn(base_url, api_key, model, images, *, cache_dir=None, timeout_sec=40):
        calls.append([image.get("source_url") for image in images])
        return "ABC123", 1.25, {"total_tokens": 10}, False

    monkeypatch.setattr(v2, "call_direct_sn_ocr", fake_call_direct_sn)

    result = v2.audit_task_sn_only(
        "https://example.invalid/v1",
        "key",
        "qwen3.7-plus",
        task,
        cache_dir=None,
    )

    assert calls == [["c"]]
    assert result["strategy"] == "sn_only_direct_ocr"
    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "SN_ONLY_MATCH_NOT_FULL_AUDIT"
    assert result["business_pass"] is False
    assert result["sn_only"] is True
    assert result["system_sn"] == "ABC123"
    assert result["observed_sn"] == "ABC123"
    assert result["sn_match"] is True
    assert result["model_calls"] == 1
    assert result["compliance_elapsed_sec"] == 0.0
    assert result["product_photo_ok"] == ""
    assert result["activation_photo_ok"] == ""


def test_sn_only_audit_reports_mismatch_with_diff_positions(monkeypatch):
    task = _base_task()

    def fake_call_direct_sn(base_url, api_key, model, images, *, cache_dir=None, timeout_sec=40):
        return "AB8123", 2.0, {"total_tokens": 10}, False

    monkeypatch.setattr(v2, "call_direct_sn_ocr", fake_call_direct_sn)

    result = v2.audit_task_sn_only(
        "https://example.invalid/v1",
        "key",
        "qwen3.7-plus",
        task,
        cache_dir=None,
    )

    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["sn_match"] is False
    assert "系统SN=ABC123" in result["manual_reason"]
    assert "模型识别SN=AB8123" in result["manual_reason"]
    assert "第3位" in result["manual_reason"]


def test_audit_task_path_supports_sn_only_mode(monkeypatch, tmp_path):
    captured = {}
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(_base_task()), encoding="utf-8")

    def fake_audit_task_sn_only(base_url, api_key, model, task, *, cache_dir=None):
        captured["called"] = True
        return {"id": task["channel_order_no"], "strategy": "sn_only_direct_ocr"}

    monkeypatch.setattr(v2, "audit_task_sn_only", fake_audit_task_sn_only)

    _index, payload = v2.audit_task_path(
        1,
        1,
        task_path,
        base_url="https://example.invalid/v1",
        api_key="key",
        model="model",
        mode="sn_only",
        cache_dir=tmp_path / "cache",
        allow_review=True,
        allow_targeted_review=True,
    )

    assert captured["called"] is True
    assert payload["result"]["strategy"] == "sn_only_direct_ocr"


def test_sn_result_rejects_visual_insertion_even_when_model_claims_match():
    normalized = v2._normalize_sn_result(
        {"system_sn": "ADUNUTC18006392"},
        {
            "sn_match": True,
            "observed_sn": "ADUNUT5C18006392",
            "normalized_observed_sn": "ADUNUT5C18006392",
            "confidence": 1.0,
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "ADUNUT5C18006392"
    assert normalized["normalized_observed_sn"] == "ADUNUT5C18006392"
    assert "raw_observed_sn" not in normalized
    assert "visual_sn_ambiguity" not in normalized
    assert normalized["manual_reason_code"] == "SN_MISMATCH"
    assert normalized["manual_reason_codes"] == ["SN_MISMATCH"]
    assert normalized["manual_reason"] == "照片中SN与系统SN不一致"


def test_system_sn_led_verification_rejects_common_visual_ocr_confusion():
    normalized = v2._normalize_sn_result(
        {"system_sn": "511-320Q1063-AB25-1042201"},
        {
            "sn_match": True,
            "observed_sn": "511 - 32OQ1063 - AB25- 1042201",
            "normalized_observed_sn": "51132OQ1063AB251042201",
            "manual_reason_code": "OK",
            "manual_reason": "image supports the system SN; one glyph is visually ambiguous",
            "confidence": 0.96,
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "511 - 32OQ1063 - AB25- 1042201"
    assert normalized["normalized_observed_sn"] == "51132OQ1063AB251042201"
    assert "raw_observed_sn" not in normalized
    assert "visual_sn_ambiguity" not in normalized
    assert normalized["manual_reason_code"] == "SN_MISMATCH"
    assert normalized["manual_reason"] == "照片中SN与系统SN不一致"


def test_system_sn_led_verification_rejects_small_alignment_error_when_model_says_match():
    normalized = v2._normalize_sn_result(
        {"system_sn": "6AN0225A21005274"},
        {
            "sn_match": True,
            "observed_sn": "6AN0225A211005274",
            "normalized_observed_sn": "6AN0225A211005274",
            "manual_reason_code": "PASS",
            "manual_reason": "screen and package both show the same SN",
            "confidence": 0.95,
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "6AN0225A211005274"
    assert normalized["normalized_observed_sn"] == "6AN0225A211005274"
    assert "raw_observed_sn" not in normalized
    assert "visual_sn_ambiguity" not in normalized
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


def test_system_sn_led_verification_keeps_clear_different_object_as_mismatch():
    normalized = v2._normalize_sn_result(
        {"system_sn": "511310A1111B4301050610"},
        {
            "sn_match": True,
            "observed_sn": "BC93MF",
            "normalized_observed_sn": "BC93MF",
            "manual_reason_code": "OK",
            "manual_reason": "read a code from image",
            "confidence": 0.95,
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


def test_sn_result_prioritizes_clear_screen_conflict_over_matching_package():
    normalized = v2._normalize_sn_result(
        {"system_sn": "A22C025C17003281"},
        {
            "sn_match": True,
            "observed_sn": "A22C025C17003281",
            "normalized_observed_sn": "A22C025C17003281",
            "confidence": 0.99,
            "sn_candidates": [
                {
                    "source": "DEVICE_SCREEN",
                    "field_type": "SN",
                    "raw_text": "SN:A22C025C03007897",
                    "normalized_text": "A22C025C03007897",
                    "readable": True,
                },
                {
                    "source": "PACKAGE_LABEL",
                    "field_type": "SN",
                    "raw_text": "S/N:A22C025C17003281",
                    "normalized_text": "A22C025C17003281",
                    "readable": True,
                },
            ],
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "A22C025C03007897"
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


def _sn_candidate(
    value,
    *,
    source="PACKAGE_LABEL",
    field_type="SN",
    raw_text=None,
    readable=True,
    matches_system_sn=None,
):
    candidate = {
        "source": source,
        "field_type": field_type,
        "raw_text": raw_text if raw_text is not None else f"SN: {value}",
        "normalized_text": value,
        "readable": readable,
    }
    if matches_system_sn is not None:
        candidate["matches_system_sn"] = matches_system_sn
    return candidate


def test_formal_order_imei_screen_reading_is_discarded_for_package_sn_mismatch():
    fields = {
        "system_sn": "HXL7NVMWGM",
        "imei1": "355516408057368",
        "imei2": "355516407888847",
    }
    normalized = v2._normalize_sn_result(
        fields,
        {
            "sn_match": True,
            "observed_sn": "IMEI1355516408057368IMEI2355516407888847",
            "normalized_observed_sn": "IMEI1355516408057368IMEI2355516407888847",
            "sn_candidates": [
                _sn_candidate(
                    "IMEI1355516408057368IMEI2355516407888847",
                    source="DEVICE_SCREEN",
                    raw_text="IMEI1: 355516408057368 IMEI2: 355516407888847",
                    matches_system_sn=True,
                ),
                _sn_candidate(
                    "HX27MVN9M",
                    source="PACKAGE_LABEL",
                    field_type="SN",
                    raw_text="HX27MVN9M",
                    matches_system_sn=False,
                ),
            ],
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "HX27MVN9M"
    assert normalized["normalized_observed_sn"] == "HX27MVN9M"
    assert normalized["manual_reason_code"] == "SN_MISMATCH"
    assert "355516408057368" not in normalized["observed_sn"]


@pytest.mark.parametrize(
    "compact_identity",
    [
        "IMEI1355516408057368IMEI2355516407888847",
        "EID89049032000000000000000000000001",
    ],
)
@pytest.mark.parametrize("with_package_fallback", [True, False])
def test_compact_top_level_identity_group_is_discarded_without_order_imei_context(
    compact_identity, with_package_fallback
):
    candidates = [_sn_candidate("ABC123", source="PACKAGE_LABEL")] if with_package_fallback else []
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123"},
        {
            "sn_match": True,
            "observed_sn": compact_identity,
            "normalized_observed_sn": compact_identity,
            "sn_candidates": candidates,
        },
    )

    if with_package_fallback:
        assert normalized["sn_match"] is True
        assert normalized["observed_sn"] == "ABC123"
        assert normalized["normalized_observed_sn"] == "ABC123"
        assert normalized["manual_reason_code"] == ""
    else:
        assert normalized["sn_match"] is False
        assert normalized["observed_sn"] == ""
        assert normalized["normalized_observed_sn"] == ""
        assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


def test_equivalent_top_level_observed_fields_normalize_to_one_reading():
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123"},
        {
            "observed_sn": "ABC-123",
            "normalized_observed_sn": "ABC123",
            "sn_candidates": [],
        },
    )

    assert normalized["sn_match"] is True
    assert normalized["normalized_observed_sn"] == "ABC123"


def test_conflicting_top_level_observed_fields_cannot_auto_pass():
    normalized = v2._normalize_sn_result(
        {"system_sn": "SYSTEM123"},
        {
            "observed_sn": "SYSTEM123",
            "normalized_observed_sn": "REAL999",
            "sn_candidates": [],
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "REAL999"
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


def test_conflicting_top_level_group_is_not_hidden_by_matching_package():
    normalized = v2._normalize_sn_result(
        {"system_sn": "SYSTEM123"},
        {
            "observed_sn": "SYSTEM123",
            "normalized_observed_sn": "REAL999",
            "sn_candidates": [_sn_candidate("SYSTEM123")],
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "REAL999"
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


@pytest.mark.parametrize(
    "identity_value",
    [
        "IMEI1355516408057368IMEI2355516407888847",
        "EID89049032000000000000000000000001",
        "355516408057368",
    ],
)
@pytest.mark.parametrize("with_matching_package", [False, True])
def test_mixed_top_level_sn_and_identity_value_cannot_fall_back_to_match(
    identity_value, with_matching_package
):
    candidates = [_sn_candidate("SYSTEM123")] if with_matching_package else []
    decision = {
        "system_sn": "SYSTEM123",
        "imei1": "355516408057368",
        "imei2": "355516407888847",
        "observed_sn": "REAL999",
        "normalized_observed_sn": identity_value,
        "sn_candidates": candidates,
    }

    normalized = v2._normalize_sn_result(
        {
            "system_sn": "SYSTEM123",
            "imei1": "355516408057368",
            "imei2": "355516407888847",
        },
        decision,
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "REAL999"
    assert normalized["manual_reason_code"] == "SN_MISMATCH"
    assert v2._conflicting_observed_sn(decision) == "REAL999"


@pytest.mark.parametrize(
    "identity_value",
    [
        "IMEI1355516408057368IMEI2355516407888847",
        "EID89049032000000000000000000000001",
        "355516408057368",
    ],
)
@pytest.mark.parametrize("identity_first", [False, True])
@pytest.mark.parametrize("with_matching_candidate", [False, True])
def test_matching_top_level_sn_ignores_identity_values_without_conflicting_sn(
    identity_value, identity_first, with_matching_candidate
):
    observed_sn, normalized_observed_sn = (
        (identity_value, "SYSTEM123")
        if identity_first
        else ("SYSTEM123", identity_value)
    )
    candidates = (
        [_sn_candidate("SYSTEM123", source="PACKAGE_LABEL")]
        if with_matching_candidate
        else []
    )
    decision = {
        "system_sn": "SYSTEM123",
        "imei1": "355516408057368",
        "imei2": "355516407888847",
        "observed_sn": observed_sn,
        "normalized_observed_sn": normalized_observed_sn,
        "sn_candidates": candidates,
    }

    normalized = v2._normalize_sn_result(
        {
            "system_sn": "SYSTEM123",
            "imei1": "355516408057368",
            "imei2": "355516407888847",
        },
        decision,
    )

    assert normalized["sn_match"] is True
    assert normalized["observed_sn"] == "SYSTEM123"
    assert normalized.get("manual_reason_code", "") == ""
    assert v2._conflicting_observed_sn(decision) == ""


@pytest.mark.parametrize(
    ("observed_sn", "normalized_observed_sn"),
    [
        ("SYSTEM123", "WRONG1"),
        ("WRONG1", "SYSTEM123"),
    ],
)
def test_mixed_identity_group_displays_non_system_conflict_independent_of_field_order(
    observed_sn, normalized_observed_sn
):
    fields = {
        "system_sn": "SYSTEM123",
        "imei1": "355516408057368",
        "imei2": "355516407888847",
    }
    compliance = {
        "observed_sn": observed_sn,
        "normalized_observed_sn": normalized_observed_sn,
        "read_sn": "WRONG2",
        "normalized_read_sn": "IMEI1355516408057368IMEI2355516407888847",
        "sn_candidates": [_sn_candidate("SYSTEM123")],
    }

    normalized = v2._normalize_sn_result(fields, compliance)
    row = v2._final_row(
        {"channel_order_no": "mixed-identity-conflict", "fields": fields},
        {
            "manual_required": True,
            "manual_reason_codes": ["SN_MISMATCH"],
            "manual_reason": "top-level SN readings conflict",
        },
        normalized,
        compliance,
        0.0,
        0.0,
        0.0,
        0.0,
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "WRONG1"
    assert v2._conflicting_observed_sn({**compliance, **fields}) == "WRONG1"
    assert row["observed_sn"] == "WRONG1"
    assert row["sn_match"] is False


@pytest.mark.parametrize(
    ("observed_sn", "normalized_observed_sn"),
    [
        ("SYSTEM123", "IMEI1355516408057368IMEI2355516407888847"),
        ("IMEI1355516408057368IMEI2355516407888847", "SYSTEM123"),
    ],
)
def test_mixed_identity_group_displays_non_system_candidate_when_top_level_matches(
    observed_sn, normalized_observed_sn
):
    fields = {
        "system_sn": "SYSTEM123",
        "imei1": "355516408057368",
        "imei2": "355516407888847",
    }
    compliance = {
        "observed_sn": observed_sn,
        "normalized_observed_sn": normalized_observed_sn,
        "sn_candidates": [_sn_candidate("REAL999")],
    }

    normalized = v2._normalize_sn_result(fields, compliance)
    row = v2._final_row(
        {"channel_order_no": "mixed-identity-candidate-conflict", "fields": fields},
        {
            "manual_required": True,
            "manual_reason_codes": ["SN_MISMATCH"],
            "manual_reason": "top-level identity and package SN conflict",
        },
        normalized,
        compliance,
        0.0,
        0.0,
        0.0,
        0.0,
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "REAL999"
    assert v2._conflicting_observed_sn({**compliance, **fields}) == "REAL999"
    assert row["observed_sn"] == "REAL999"
    assert row["sn_match"] is False


def test_identity_only_top_level_displays_non_system_candidate_conflict():
    fields = {
        "system_sn": "SYSTEM123",
        "imei1": "355516408057368",
        "imei2": "355516407888847",
    }
    compliance = {
        "observed_sn": "IMEI1355516408057368IMEI2355516407888847",
        "normalized_observed_sn": "IMEI1355516408057368IMEI2355516407888847",
        "sn_candidates": [
            _sn_candidate("SYSTEM123", source="DEVICE_SCREEN"),
            _sn_candidate("REAL999", source="PACKAGE_LABEL"),
        ],
    }

    normalized = v2._normalize_sn_result(fields, compliance)
    row = v2._final_row(
        {"channel_order_no": "identity-candidate-conflict", "fields": fields},
        {
            "manual_required": True,
            "manual_reason_codes": ["SN_MISMATCH"],
            "manual_reason": "structured SN candidates conflict",
        },
        normalized,
        compliance,
        0.0,
        0.0,
        0.0,
        0.0,
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "REAL999"
    assert v2._conflicting_observed_sn({**compliance, **fields}) == "REAL999"
    assert row["observed_sn"] == "REAL999"
    assert row["sn_match"] is False


def test_imei_screen_reading_falls_back_to_matching_package_sn():
    fields = {
        "system_sn": "HXL7NVMWGM",
        "imei1": "867530900000001",
        "imei2": "867530900000002",
    }
    normalized = v2._normalize_sn_result(
        fields,
        {
            "sn_match": False,
            "observed_sn": "IMEI1: 867530900000001",
            "normalized_observed_sn": "867530900000001",
            "sn_candidates": [
                _sn_candidate(
                    "867530900000001",
                    source="DEVICE_SCREEN",
                    raw_text="IMEI1: 867530900000001 IMEI2: 867530900000002",
                    matches_system_sn=True,
                ),
                _sn_candidate("HXL7NVMWGM", matches_system_sn=False),
            ],
        },
    )

    assert normalized["sn_match"] is True
    assert normalized["observed_sn"] == "HXL7NVMWGM"
    assert normalized["normalized_observed_sn"] == "HXL7NVMWGM"
    assert normalized.get("manual_reason_code") not in {"SN_MISMATCH", "SN_NOT_FOUND"}


@pytest.mark.parametrize("field_type", ["IMEI", "imei_1", "I M E I 2", "E_I_D"])
def test_imei_and_eid_field_types_are_excluded_from_sn_candidates(field_type):
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123"},
        {"sn_candidates": [_sn_candidate("ABC123", field_type=field_type)]},
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == ""
    assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


@pytest.mark.parametrize(
    "raw_text",
    [
        "IMEI: 867530900000001",
        "IMEI1: 867530900000001",
        "IMEI 2: 867530900000001",
        "EID: 867530900000001",
    ],
)
def test_sn_field_with_bounded_imei_or_eid_label_is_excluded(raw_text):
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123"},
        {
            "sn_candidates": [
                _sn_candidate("867530900000001", field_type="SN", raw_text=raw_text),
            ]
        },
    )

    assert normalized["observed_sn"] == ""
    assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


def test_package_sn_survives_neighboring_imei_label_and_exposes_top_level_conflict():
    decision = {
        "system_sn": "SYSTEM123",
        "observed_sn": "SYSTEM123",
        "normalized_observed_sn": "SYSTEM123",
        "sn_candidates": [
            _sn_candidate(
                "REAL999",
                source="PACKAGE_LABEL",
                field_type="SN",
                raw_text="SN: REAL999 IMEI: 355516408057368",
            )
        ],
    }

    normalized = v2._normalize_sn_result(
        {"system_sn": "SYSTEM123"},
        decision,
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "REAL999"
    assert normalized["manual_reason_code"] == "SN_MISMATCH"
    assert v2._conflicting_observed_sn(decision) == "REAL999"


def test_fifteen_digit_sn_survives_neighboring_imei_label():
    normalized = v2._normalize_sn_result(
        {"system_sn": "123456789012345"},
        {
            "sn_candidates": [
                _sn_candidate(
                    "123456789012345",
                    field_type="SN",
                    raw_text="SN: 123456789012345 IMEI: 355516408057368",
                )
            ]
        },
    )

    assert normalized["sn_match"] is True
    assert normalized["observed_sn"] == "123456789012345"


def test_identity_labelled_combined_normalized_value_is_excluded_even_for_sn_field():
    normalized = v2._normalize_sn_result(
        {"system_sn": "SYSTEM123"},
        {
            "sn_candidates": [
                _sn_candidate(
                    "IMEI1355516408057368IMEI2355516408057376",
                    field_type="SN",
                    raw_text="SN identifiers captured",
                )
            ]
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == ""
    assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


def test_sn_label_without_value_binding_does_not_claim_imei_value():
    normalized = v2._normalize_sn_result(
        {"system_sn": "355516408057368", "imei1": "999999999999999"},
        {
            "sn_candidates": [
                _sn_candidate(
                    "355516408057368",
                    field_type="SN",
                    raw_text="SN identifiers captured; IMEI: 355516408057368",
                )
            ]
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == ""
    assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


def test_mixed_raw_block_binds_each_candidate_to_its_own_label():
    raw_text = "SN: ABC123 IMEI: 355516408057368"
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123"},
        {
            "sn_candidates": [
                _sn_candidate("ABC123", field_type="SN", raw_text=raw_text),
                _sn_candidate("355516408057368", field_type="SN", raw_text=raw_text),
            ]
        },
    )

    assert normalized["sn_match"] is True
    assert normalized["observed_sn"] == "ABC123"


def test_sn_bound_fifteen_digit_value_remains_a_valid_sn():
    normalized = v2._normalize_sn_result(
        {"system_sn": "123456789012345"},
        {
            "sn_candidates": [
                _sn_candidate(
                    "123456789012345",
                    field_type="SN",
                    raw_text="SN: 123456789012345",
                )
            ]
        },
    )

    assert normalized["sn_match"] is True
    assert normalized["observed_sn"] == "123456789012345"


@pytest.mark.parametrize(
    ("normalized_text", "raw_text"),
    [
        ("ABC-123", "SN: ABC-123 IMEI: 355516408057368"),
        ("ABC 123", "SN: ABC 123 IMEI: 355516408057368"),
        ("ABC123", "(S) Serial No. ABC123 IMEI: 355516408057368"),
    ],
)
def test_sn_binding_allows_separators_between_candidate_characters(
    normalized_text, raw_text
):
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123"},
        {
            "sn_candidates": [
                _sn_candidate(
                    normalized_text,
                    field_type="SN",
                    raw_text=raw_text,
                )
            ]
        },
    )

    assert normalized["sn_match"] is True
    assert normalized["observed_sn"] == "ABC123"


def test_real_formal_order_keeps_package_mismatch_through_final_row():
    fields = {
        "system_sn": "HXL7NVMWGM",
        "imei1": "355516408057368",
        "imei2": "355516407888847",
    }
    model_result = {
        "sn_match": True,
        "observed_sn": "IMEI1355516408057368IMEI2355516407888847",
        "normalized_observed_sn": "IMEI1355516408057368IMEI2355516407888847",
        "sn_candidates": [
            _sn_candidate(
                "IMEI1355516408057368IMEI2355516407888847",
                source="DEVICE_SCREEN",
                field_type="SN",
                raw_text="IMEI1: 355516408057368 IMEI2: 355516407888847",
            ),
            _sn_candidate(
                "HX27MVN9M",
                source="PACKAGE_LABEL",
                field_type="SN",
                raw_text="HX27MVN9M",
                matches_system_sn=False,
            ),
        ],
    }

    normalized = v2._normalize_sn_result(fields, model_result)
    row = v2._final_row(
        {
            "channel_order_no": "481173067012915222937618",
            "fields": fields,
        },
        {
            "manual_required": True,
            "manual_reason_codes": ["SN_MISMATCH"],
            "manual_reason": "package SN differs from system SN",
        },
        normalized,
        {},
        0.0,
        0.0,
        0.0,
        0.0,
    )

    assert normalized["observed_sn"] == "HX27MVN9M"
    assert row["id"] == "481173067012915222937618"
    assert row["observed_sn"] == "HX27MVN9M"
    assert row["sn_match"] is False
    assert row["manual_reason_code"] == "SN_MISMATCH"


def test_candidate_equal_to_order_imei_after_punctuation_normalization_is_excluded():
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123", "imei1": "867 530 900 000 001"},
        {
            "sn_candidates": [
                _sn_candidate(
                    "867-5309-00000-001",
                    field_type="SN",
                    raw_text="SN: 867-5309-00000-001",
                )
            ]
        },
    )

    assert normalized["observed_sn"] == ""
    assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


@pytest.mark.parametrize(
    ("system_sn", "candidate_sn", "expected_match", "expected_code"),
    [
        ("123456789012345", "123456789012345", True, ""),
        ("123456789012345", "999999999999999", False, "SN_MISMATCH"),
    ],
)
def test_valid_fifteen_digit_numeric_sn_is_not_filtered_by_length(
    system_sn, candidate_sn, expected_match, expected_code
):
    normalized = v2._normalize_sn_result(
        {"system_sn": system_sn},
        {"sn_candidates": [_sn_candidate(candidate_sn)]},
    )

    assert normalized["sn_match"] is expected_match
    assert normalized["observed_sn"] == candidate_sn
    assert normalized.get("manual_reason_code", "") == expected_code


def test_ximeiy_substring_is_not_treated_as_an_imei_label():
    normalized = v2._normalize_sn_result(
        {"system_sn": "XIMEIY123"},
        {
            "sn_candidates": [
                _sn_candidate("XIMEIY123", raw_text="SN: XIMEIY123"),
            ]
        },
    )

    assert normalized["sn_match"] is True
    assert normalized["observed_sn"] == "XIMEIY123"


@pytest.mark.parametrize(
    ("candidates", "expected_observed"),
    [
        (
            [
                _sn_candidate("WRONG123", source="DEVICE_SCREEN"),
                _sn_candidate("ABC123", source="PACKAGE_LABEL"),
            ],
            "WRONG123",
        ),
        (
            [
                _sn_candidate("ABC123", source="DEVICE_SCREEN"),
                _sn_candidate("WRONG123", source="PACKAGE_LABEL"),
            ],
            "WRONG123",
        ),
        (
            [
                _sn_candidate("ZZZ999", source="PACKAGE_LABEL"),
                _sn_candidate("AAA111", source="PACKAGE_LABEL"),
            ],
            "AAA111",
        ),
    ],
)
def test_distinct_sn_candidates_always_conflict_and_are_order_stable(
    candidates, expected_observed
):
    outcomes = []
    for ordered_candidates in (candidates, list(reversed(candidates))):
        normalized = v2._normalize_sn_result(
            {"system_sn": "ABC123"},
            {"sn_match": True, "sn_candidates": ordered_candidates},
        )
        outcomes.append(
            (
                normalized["sn_match"],
                normalized["observed_sn"],
                normalized["manual_reason_code"],
            )
        )

    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] is False
    assert outcomes[0][1] == expected_observed
    assert outcomes[0][2] == "SN_MISMATCH"


def test_same_sn_from_multiple_sources_is_a_match():
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123"},
        {
            "sn_match": False,
            "sn_candidates": [
                _sn_candidate("ABC123", source="DEVICE_SCREEN", matches_system_sn=False),
                _sn_candidate("ABC123", source="DEVICE_BODY", matches_system_sn=False),
                _sn_candidate("ABC123", source="PACKAGE_LABEL", matches_system_sn=False),
            ],
        },
    )

    assert normalized["sn_match"] is True
    assert normalized["observed_sn"] == "ABC123"


def test_unreadable_sn_candidate_is_ignored():
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123"},
        {
            "sn_candidates": [
                _sn_candidate("WRONG123", source="DEVICE_SCREEN", readable=False),
                _sn_candidate("ABC123", source="PACKAGE_LABEL"),
            ]
        },
    )

    assert normalized["sn_match"] is True
    assert normalized["observed_sn"] == "ABC123"


def test_top_level_system_observed_is_cross_checked_against_conflicting_candidate():
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123"},
        {
            "sn_match": True,
            "observed_sn": "ABC123",
            "normalized_observed_sn": "ABC123",
            "sn_candidates": [
                _sn_candidate("WRONG123", source="DEVICE_SCREEN"),
            ],
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "WRONG123"
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


@pytest.mark.parametrize(
    (
        "order_id",
        "product_type",
        "system_sn",
        "observed_sn",
        "candidates",
    ),
    [
        (
            "481172622339271118028880",
            "[A03] 洗衣机",
            "CEACF800001PHS21XAK1",
            "G10090BD12S",
            [
                _sn_candidate("G10090BD12S", field_type="SN", raw_text="G10090BD12S"),
                _sn_candidate(
                    "CEACF800001PHS21XAK1",
                    field_type="SERIAL",
                    raw_text="CEACF800001PHS21XAK1",
                ),
            ],
        ),
        (
            "481173066847794737971215",
            "[A03] 洗衣机",
            "51138000MN4AB141A00192",
            "51138000MN4AB141A00192",
            [
                _sn_candidate(
                    "51138000MN4AB141A00192",
                    field_type="SN",
                    raw_text="51138000MN4AB141A00192",
                ),
                _sn_candidate("MB80V37T", field_type="SERIAL", raw_text="MB80V37T"),
            ],
        ),
        (
            "481173197804678689587227",
            "[A04] 空调",
            "AAC81100000N4RCNW2GY",
            "SN_NOT_FOUND",
            [
                _sn_candidate(
                    "KFR72GMEA81U1",
                    source="DEVICE_BODY",
                    field_type="MODEL_NUMBER",
                    raw_text="KFR-72G/MEA81U1",
                ),
                _sn_candidate(
                    "AAC81100000N4RCNW2GY",
                    source="DEVICE_BODY",
                    field_type="OTHER_CODE",
                    raw_text="AAC81 10000 0N4RC NW2GY",
                ),
            ],
        ),
        (
            "481173197823400401305629",
            "[A01] 电视机",
            "1TE650CTCNTA017BQ280040",
            "1TE650CTCNTA017BQ280040",
            [
                _sn_candidate(
                    "1TE650CTCNTA017BQ280040",
                    field_type="SN",
                    raw_text="1TE650CTCNTA017BQ280040",
                ),
                _sn_candidate("65Z570QF", field_type="SERIAL", raw_text="65Z570QF"),
            ],
        ),
        (
            "481173198775177737011289",
            "[A06] 热水器",
            "GA0T0900800GMS4NRQHR",
            "GA0T0900800GMS4NRQHR",
            [
                _sn_candidate(
                    "GA0T0900800GMS4NRQHR",
                    source="DEVICE_BODY",
                    field_type="SERIAL",
                    raw_text="GA0T0900800GMS4NRQHR",
                ),
                _sn_candidate(
                    "GHS4N1C59A",
                    source="DEVICE_BODY",
                    field_type="SN",
                    raw_text="GHS4N1C59A",
                ),
            ],
        ),
        (
            "481173199299432149811214",
            "[A03] 洗衣机",
            "CAACE300000PAS6BZKLC",
            "CAACE300000PAS6BZKLC",
            [
                _sn_candidate(
                    "CAACE300000PAS6BZKLC",
                    source="DEVICE_BODY",
                    field_type="SERIAL",
                    raw_text="CAACE300000PAS6BZKLC",
                ),
                _sn_candidate(
                    "CAACE3000",
                    source="DEVICE_BODY",
                    field_type="SN",
                    raw_text="CAACE3000",
                ),
            ],
        ),
        (
            "481173201334998345318449",
            "[A04] 空调",
            "AB96B400001N7QBKBT01",
            "AAC6G100001N8QBJFXMJ",
            [
                _sn_candidate(
                    "AAC6G100001N8QBJFXMJ",
                    source="DEVICE_BODY",
                    field_type="SN",
                    raw_text="AAC6G 10000 1N8QB JFXMJ",
                ),
                _sn_candidate(
                    "AB96B400001N7QBKBT01",
                    source="DEVICE_BODY",
                    field_type="SERIAL",
                    raw_text="AB96B 40000 1N7QB KBT01",
                ),
            ],
        ),
        (
            "481173202061507166208054",
            "[A06] 热水器",
            "GA0T750020032RCAT9Z6",
            "GA0T750020032RCAT9Z6",
            [
                _sn_candidate(
                    "GA0T750020032RCAT9Z6",
                    field_type="SERIAL",
                    raw_text="GA0T750020032RCAT9Z6",
                ),
                _sn_candidate("32RCAB5CE3", field_type="SN", raw_text="32RCAB5CE3"),
            ],
        ),
    ],
)
def test_home_appliance_exact_system_candidate_locks_authoritative_sn(
    order_id, product_type, system_sn, observed_sn, candidates
):
    normalized = v2._normalize_sn_result(
        {"system_sn": system_sn, "product_type": product_type},
        {
            "sn_match": observed_sn != "SN_NOT_FOUND",
            "observed_sn": observed_sn,
            "normalized_observed_sn": None if observed_sn == "SN_NOT_FOUND" else observed_sn,
            "sn_candidates": candidates,
            "manual_reason_code": "SN_NOT_FOUND" if observed_sn == "SN_NOT_FOUND" else None,
        },
    )

    assert normalized["sn_match"] is True, order_id
    assert normalized["observed_sn"] == system_sn, order_id
    assert normalized["manual_reason_code"] == "", order_id


def test_home_appliance_without_exact_system_candidate_preserves_mismatch():
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123", "product_type": "[A03] 洗衣机"},
        {
            "observed_sn": "WRONG123",
            "sn_candidates": [_sn_candidate("WRONG123", source="DEVICE_BODY")],
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "WRONG123"
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


def test_home_appliance_model_bound_exact_value_cannot_rescue_sn():
    normalized = v2._normalize_sn_result(
        {"system_sn": "KFR72GMEA81U1", "product_type": "[A04] 空调"},
        {
            "observed_sn": "SN_NOT_FOUND",
            "sn_candidates": [
                _sn_candidate(
                    "KFR72GMEA81U1",
                    source="DEVICE_BODY",
                    field_type="MODEL_NUMBER",
                    raw_text="型号: KFR-72G/MEA81U1",
                )
            ],
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


def test_ordinary_3c_screen_first_conflict_is_not_rescued_by_matching_package_sn():
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123", "product_type": "手机"},
        {
            "observed_sn": "WRONG123",
            "sn_candidates": [
                _sn_candidate("WRONG123", source="DEVICE_SCREEN"),
                _sn_candidate("ABC123", source="PACKAGE_LABEL"),
            ],
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "WRONG123"
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


def test_ordinary_3c_cannot_be_rescued_by_model_supplied_home_flag():
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123", "product_type": "手机"},
        {
            "is_home_appliance": True,
            "observed_sn": "WRONG123",
            "sn_candidates": [
                _sn_candidate("WRONG123", source="DEVICE_SCREEN"),
                _sn_candidate("ABC123", source="PACKAGE_LABEL"),
            ],
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "WRONG123"
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


def test_home_appliance_model_context_does_not_hide_separately_bound_exact_sn():
    candidate = _sn_candidate(
        "ABC123",
        source="DEVICE_BODY",
        field_type="OTHER_CODE",
        raw_text="ABC123",
    )
    candidate["raw_context"] = "Model: KFR72; SN: ABC123"

    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123", "product_type": "[A04] 空调"},
        {"observed_sn": "SN_NOT_FOUND", "sn_candidates": [candidate]},
    )

    assert normalized["sn_match"] is True
    assert normalized["observed_sn"] == "ABC123"


@pytest.mark.parametrize(
    "raw_context",
    [
        "Model: ABC123",
        "MODEL-NO: ABC123",
        "MODEL_NO: ABC123",
        "MODEL/NO: ABC123",
    ],
)
def test_home_appliance_context_bound_model_value_cannot_rescue_sn(raw_context):
    candidate = _sn_candidate(
        "ABC123",
        source="DEVICE_BODY",
        field_type="OTHER_CODE",
        raw_text="ABC123",
    )
    candidate["raw_context"] = raw_context

    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123", "product_type": "[A04] 空调"},
        {"observed_sn": "SN_NOT_FOUND", "sn_candidates": [candidate]},
    )

    assert normalized["sn_match"] is False
    assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


@pytest.mark.parametrize(
    "label_text",
    ["MODEL-NO", "MODEL_NO", "MODEL/NO", "MODEL-NUMBER"],
)
def test_home_appliance_punctuated_model_label_cannot_rescue_sn(label_text):
    candidate = _sn_candidate(
        "ABC123",
        source="DEVICE_BODY",
        field_type="OTHER_CODE",
        raw_text="ABC123",
    )
    candidate["label_text"] = label_text

    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123", "product_type": "[A04] 空调"},
        {"observed_sn": "SN_NOT_FOUND", "sn_candidates": [candidate]},
    )

    assert normalized["sn_match"] is False
    assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


@pytest.mark.parametrize(
    "field_type",
    ["IMEI", "IMEI1", "IMEI2", "EID", "IMEI-1", "E-I-D"],
)
def test_home_appliance_identity_candidate_cannot_rescue_sn(field_type):
    normalized = v2._normalize_sn_result(
        {"system_sn": "867530900000001", "product_type": "[A03] 洗衣机"},
        {
            "observed_sn": "SN_NOT_FOUND",
            "sn_candidates": [
                _sn_candidate(
                    "867530900000001",
                    source="DEVICE_BODY",
                    field_type=field_type,
                    raw_text=f"{field_type}: 867530900000001",
                )
            ],
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


def test_home_appliance_hyphenated_model_field_cannot_rescue_sn():
    normalized = v2._normalize_sn_result(
        {"system_sn": "KFR72GMEA81U1", "product_type": "[A04] 空调"},
        {
            "observed_sn": "SN_NOT_FOUND",
            "sn_candidates": [
                _sn_candidate(
                    "KFR72GMEA81U1",
                    source="DEVICE_BODY",
                    field_type="MODEL-NUMBER",
                    raw_text="KFR-72G/MEA81U1",
                )
            ],
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("label_text", "IMEI1"),
        ("raw_context", "IMEI1: 867530900000001"),
        ("label_text", "E-I-D"),
        ("raw_context", "E-I-D: 867530900000001"),
        ("label_text", "I-M-E-I"),
        ("raw_context", "I-M-E-I: 867530900000001"),
    ],
)
def test_home_appliance_identity_bound_exact_other_code_cannot_rescue_sn(
    field_name, field_value
):
    candidate = _sn_candidate(
        "867530900000001",
        source="DEVICE_BODY",
        field_type="OTHER_CODE",
        raw_text="867530900000001",
    )
    candidate[field_name] = field_value

    normalized = v2._normalize_sn_result(
        {"system_sn": "867530900000001", "product_type": "[A03] 洗衣机"},
        {"observed_sn": "SN_NOT_FOUND", "sn_candidates": [candidate]},
    )

    assert normalized["sn_match"] is False
    assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


def test_home_appliance_separate_identity_context_does_not_hide_exact_sn():
    candidate = _sn_candidate(
        "ABC123",
        source="DEVICE_BODY",
        field_type="OTHER_CODE",
        raw_text="ABC123",
    )
    candidate["raw_context"] = "IMEI1: 867530900000001; SN: ABC123"

    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123", "product_type": "[A03] 洗衣机"},
        {"observed_sn": "SN_NOT_FOUND", "sn_candidates": [candidate]},
    )

    assert normalized["sn_match"] is True
    assert normalized["observed_sn"] == "ABC123"


def test_final_row_keeps_locked_home_appliance_sn_instead_of_secondary_conflict():
    fields = {
        "system_sn": "ABC123",
        "product_type": "[A03] 洗衣机",
    }
    sn_result = {
        "sn_match": True,
        "observed_sn": "ABC123",
        "sn_candidates": [
            _sn_candidate("ABC123", source="DEVICE_BODY", field_type="SERIAL"),
            _sn_candidate("SHORT1", source="DEVICE_BODY", field_type="SN"),
        ],
    }
    compliance = {
        "effective_category": "ordinary_3c",
        "system_sn": "ABC123",
        "sn_candidates": [_sn_candidate("SHORT1", source="DEVICE_BODY")],
    }

    row = v2._final_row(
        {"channel_order_no": "home-lock-report", "fields": fields},
        {
            "manual_required": True,
            "manual_reason_codes": ["SN_MISMATCH"],
            "manual_reason": "secondary candidate conflict",
        },
        sn_result,
        compliance,
        1.0,
        0.1,
        0.2,
        0.3,
    )

    assert row["observed_sn"] == "ABC123"
    assert row["sn_match"] is True
    assert row["manual_flag"] == "否"
    assert row["manual_reason_code"] == ""


def test_final_row_keeps_other_manual_reason_after_locked_sn_removes_mismatch():
    fields = {"system_sn": "ABC123", "product_type": "[A03] 洗衣机"}
    sn_result = {
        "sn_match": True,
        "observed_sn": "ABC123",
        "sn_candidates": [_sn_candidate("ABC123", source="DEVICE_BODY")],
    }

    row = v2._final_row(
        {"channel_order_no": "home-lock-other-reason", "fields": fields},
        {
            "manual_required": True,
            "manual_reason_codes": ["SN_MISMATCH", "IMAGE_STRONG_RISK"],
            "manual_reason": "照片中SN与系统SN不一致",
        },
        sn_result,
        {"effective_category": "ordinary_3c"},
        1.0,
        0.1,
        0.2,
        0.3,
    )

    assert row["observed_sn"] == "ABC123"
    assert row["sn_match"] is True
    assert row["manual_flag"] == "是"
    assert row["manual_reason_code"] == "IMAGE_STRONG_RISK"
    assert "SN不一致" not in row["manual_reason_cn"]
    assert "SN不一致" not in row["manual_reason"]


@pytest.mark.parametrize(
    ("observed_sn", "expected_match", "expected_code"),
    [
        ("ABC123", True, ""),
        ("WRONG123", False, "SN_MISMATCH"),
    ],
)
def test_top_level_observed_without_candidates_preserves_historical_behavior(
    observed_sn, expected_match, expected_code
):
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC123"},
        {
            "sn_match": not expected_match,
            "observed_sn": observed_sn,
            "normalized_observed_sn": observed_sn,
        },
    )

    assert normalized["sn_match"] is expected_match
    assert normalized["observed_sn"] == observed_sn
    assert normalized.get("manual_reason_code", "") == expected_code


@pytest.mark.parametrize(
    "candidate",
    [
        _sn_candidate("867530900000001", source="DEVICE_SCREEN", field_type="IMEI"),
        _sn_candidate(
            "867530900000001",
            source="DEVICE_SCREEN",
            field_type="SN",
            raw_text="IMEI1: 867530900000001",
        ),
        _sn_candidate("867-5309-00000-001", source="DEVICE_SCREEN", field_type="SN"),
    ],
)
def test_conflict_helpers_ignore_imei_candidates(candidate):
    decision = {
        "system_sn": "ABC123",
        "imei1": "867530900000001",
        "sn_candidates": [candidate],
    }

    assert v2._conflicting_screen_sn(decision) == ""
    assert v2._conflicting_observed_sn(decision) == ""


def test_conflicting_observed_helper_discards_imei_top_level_group():
    decision = {
        "system_sn": "ABC123",
        "imei1": "867530900000001",
        "observed_sn": "IMEI1: 867530900000001",
        "normalized_observed_sn": "867530900000001",
    }

    assert v2._conflicting_observed_sn(decision) == ""


def test_group_images_by_title_keeps_multiple_images_under_same_title():
    task = {
        "images": [
            {"image_id": "img_001", "title": "product", "source_url": "a"},
            {"image_id": "img_002", "title": "unboxing", "source_url": "b"},
            {"image_id": "img_003", "title": "unboxing", "source_url": "c"},
            {"image_id": "img_004", "title": "SN photo", "source_url": "d"},
        ]
    }

    groups = group_images_by_title(task)

    assert len(groups["product"]) == 1
    assert len(groups["unboxing"]) == 2
    assert len(groups["SN photo"]) == 1


def test_address_precision_rules():
    assert is_address_precise_enough("market B1-14")
    assert is_address_precise_enough("building 1-1-1103")
    assert is_address_precise_enough("幸福村二组2号")
    assert is_address_precise_enough("结沙拉康一组")
    assert is_address_precise_enough("龙仁乡1组")
    assert not is_address_precise_enough("coarse town")
    assert not is_address_precise_enough("main road")


def test_address_precision_rejects_long_opaque_alphanumeric_suffix():
    assert not is_address_precise_enough("513385L0757B609M100145")
    assert not is_address_precise_enough("西藏自治区拉萨市城关区513385L0757B609M100145")


def test_address_precision_accepts_village_or_terminal_number_from_july16_samples():
    for address in (
        "丁青寺僧人宿舍302",
        "柳梧新区圣地财富广场一期5B609",
        "麦冬村",
        "退休村",
        "朗镇巴热村",
        "柳梧宏御商业广场A座507",
    ):
        assert is_address_precise_enough(address)


def test_address_precision_accepts_business_keywords_anywhere():
    for address in ("某某商贸", "北京市某路京东家电配送点", "幸福楼三层"):
        assert is_address_precise_enough(address)


def test_home_appliance_precheck_accepts_address_keyword_and_continues():
    task = {
        "channel_order_no": "1",
        "fields": {
            "product_type": "电冰箱",
            "system_sn": "ABC123",
            "is_home_appliance": True,
            "address": "某某商贸",
        },
        "images": [
            {"title": "product", "source_url": "a"},
            {"title": "unboxing", "source_url": "b"},
            {"title": "SN photo", "source_url": "c"},
        ],
    }

    result = precheck_task(task)

    assert result["manual_required"] is False
    assert result["address_ok"] is True
    assert result["activation_images"]


def test_duplicate_cross_group_helper_requires_three_exact_images():
    groups = {
        "product": [{"source_url": "https://example.com/a.jpg", "local_path": "a.jpg"}],
        "unboxing": [{"source_url": "https://example.com/a.jpg", "local_path": "b.jpg"}],
        "SN photo": [{"source_url": "https://example.com/c.jpg", "local_path": "c.jpg"}],
    }

    assert has_duplicate_cross_group_images(groups) is False
    groups["SN photo"] = [{"source_url": "https://example.com/a.jpg", "local_path": "c.jpg"}]
    assert has_duplicate_cross_group_images(groups) is True


def test_precheck_allows_two_repeated_groups_but_rejects_all_three():
    task = {
        "channel_order_no": "1",
        "fields": {"product_type": "[B01] phone", "system_sn": "ABC123"},
        "image_groups": {
            "product": [{"source_url": "product.jpg"}],
            "unboxing": [{"source_url": "same.jpg"}],
            "SN photo": [{"source_url": "same.jpg"}],
        },
    }

    assert precheck_task(task)["manual_required"] is False

    task["image_groups"]["product"] = [{"source_url": "same.jpg"}]
    decision = precheck_task(task)

    assert decision["manual_required"] is True
    assert decision["manual_reason_codes"] == ["DUPLICATE_IMAGE_EVIDENCE"]


def test_compliance_allows_two_repeated_photos_but_rejects_all_three():
    decision = {
        "_sn_already_verified_by_system": True,
        "manual_required": True,
        "manual_reason_codes": ["DUPLICATE_IMAGE_EVIDENCE"],
        "manual_reason": "unboxing and activation photos are identical",
        "product_type_match": True,
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "activation_photo_ok": True,
        "package_visible": True,
        "duplicate_image_evidence": True,
        "confidence": 1,
    }

    allowed = v2.enforce_photo_noncompliance_manual(decision)
    assert allowed["manual_required"] is False

    decision["_exact_duplicate_image_groups"] = [["product", "unboxing", "activation"]]
    rejected = v2.enforce_photo_noncompliance_manual(decision)
    assert rejected["manual_required"] is True
    assert rejected["manual_reason_codes"] == ["DUPLICATE_IMAGE_EVIDENCE"]


def test_precheck_rejects_home_appliance_coarse_address():
    task = {
        "channel_order_no": "1",
        "fields": {
            "product_type": "[A02] refrigerator",
            "system_sn": "ABC123",
            "is_home_appliance": True,
            "address": "coarse town",
        },
        "images": [
            {"title": "product", "source_url": "a"},
            {"title": "unboxing", "source_url": "b"},
            {"title": "SN photo", "source_url": "c"},
        ],
    }

    result = precheck_task(task)

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["ADDRESS_TOO_COARSE"]


def test_precheck_does_not_reject_computer_as_home_appliance_address():
    task = {
        "channel_order_no": "1",
        "fields": {
            "product_type": "[A05] PC",
            "cate_code": "A05",
            "cate_code_name": "PC",
            "system_sn": "ABC123",
            "is_home_appliance": True,
            "address": "coarse campus",
        },
        "images": [
            {"title": "product", "source_url": "a"},
            {"title": "unboxing", "source_url": "b"},
            {"title": "SN photo", "source_url": "c"},
        ],
    }

    result = precheck_task(task)

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []
    assert result["effective_category"] == "computer"
    assert result["address_ok"] is None


def test_precheck_uses_name_category_not_collector_home_appliance_flag():
    task = {
        "channel_order_no": "1",
        "fields": {
            "product_type": "[A05]",
            "cate_code": "A05",
            "cate_code_name": "PC",
            "goods_name": "\u7b14\u8bb0\u672c\u7535\u8111",
            "system_sn": "ABC123",
            "is_home_appliance": True,
            "address": "coarse campus",
        },
        "images": [
            {"title": "product", "source_url": "a"},
            {"title": "unboxing", "source_url": "b"},
            {"title": "SN\u7801\u91c7\u96c6 / \u6fc0\u6d3b\u7167\u7247", "source_url": "c"},
        ],
    }

    result = precheck_task(task)

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []
    assert result["effective_category"] == "computer"
    assert result["address_ok"] is None


def test_guobu_effective_category_maps_supported_types():
    cases = [
        ({"product_type": "电冰箱"}, "home_appliance"),
        ({"product_type": "电视机"}, "home_appliance"),
        ({"product_type": "空调"}, "home_appliance"),
        ({"product_type": "热水器"}, "home_appliance"),
        ({"product_type": "洗衣机"}, "home_appliance"),
        ({"product_type": "手机"}, "ordinary_3c"),
        ({"product_type": "平板"}, "ordinary_3c"),
        ({"product_type": "智能手表手环"}, "ordinary_3c"),
        ({"product_type": "智能眼镜"}, "ordinary_3c"),
        ({"product_type": "电脑"}, "computer"),
        ({"goods_name": "ThinkPad 笔记本电脑"}, "computer"),
    ]

    for fields, expected in cases:
        assert v2.effective_product_category(fields) == expected


def test_effective_category_prefers_chinese_name_over_model_substrings():
    assert v2.effective_product_category(
        {
            "category_name": "电冰箱",
            "product_type": "电冰箱 [A02]",
            "goods_name": "容声冰箱BCD-466WVS1FPC-JN51",
            "model": "BCD-466WVS1FPC",
        }
    ) == "home_appliance"
    assert v2.effective_product_category(
        {"category_name": "空调", "product_type": "空调 [A04]"}
    ) == "home_appliance"


def test_compliance_prompt_selection_uses_short_category_prompts():
    assert v2.compliance_prompt_for_category("home_appliance") == v2.HOME_APPLIANCE_COMPLIANCE_PROMPT
    assert v2.compliance_prompt_for_category(
        "ordinary_3c", digital_activation_evidence_mode="off",
    ) == v2.ORDINARY_3C_COMPLIANCE_PROMPT
    assert v2.compliance_prompt_for_category("computer") == v2.COMPUTER_COMPLIANCE_PROMPT
    assert v2.compliance_prompt_for_category("unknown") == v2.UNKNOWN_COMPLIANCE_PROMPT


def test_precheck_accepts_valid_grouped_images_for_model_steps():
    task = _base_task()

    result = precheck_task(task)

    assert result["manual_required"] is False
    assert result["activation_images"][0]["source_url"] == "c"


def test_precheck_preserves_explicit_image_groups_without_order_relabeling():
    task = {
        "channel_order_no": "481172411687994723532856",
        "fields": {
            "product_type": "[A02] refrigerator",
            "system_sn": "511310A2066B5041170249",
            "is_home_appliance": True,
            "address": "西藏自治区拉萨市城关区顺通建材市场B-14",
        },
        "image_groups": {
            "鎷嗗皝鐓х墖": [
                {"image_id": "img_001", "title": "鎷嗗皝鐓х墖", "source_url": "unboxing.jpg"}
            ],
            "SN photo": [
                {"image_id": "img_002", "title": "SN photo", "source_url": "sn.jpg"}
            ],
        },
    }

    result = precheck_task(task)

    assert result["manual_required"] is False
    assert result["groups"]["鎷嗗皝鐓х墖"][0]["image_id"] == "img_001"
    assert "product" not in result["groups"]
    assert [image["image_id"] for image in result["activation_images"]] == ["img_002"]


def test_precheck_uses_order_fallback_for_legacy_images_without_groups():
    task = {
        "channel_order_no": "1",
        "fields": {
            "product_type": "[B01] phone",
            "system_sn": "ABC123",
            "is_home_appliance": False,
            "address": "",
        },
        "images": [
            {"image_id": "img_001", "title": "", "source_url": "product.jpg"},
            {"image_id": "img_002", "title": "", "source_url": "unboxing.jpg"},
            {"image_id": "img_003", "title": "", "source_url": "sn.jpg"},
        ],
    }

    result = precheck_task(task)

    assert result["manual_required"] is False
    assert result["groups"]["鍟嗗搧鐓х墖"][0]["image_id"] == "img_001"
    assert result["groups"]["鎷嗗皝鐓х墖"][0]["image_id"] == "img_002"
    assert [image["image_id"] for image in result["activation_images"]] == ["img_003"]


def test_audit_treats_normalized_observed_sn_as_match(monkeypatch):
    calls = []

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append(stage)
        if stage == "sn":
            return (
                {
                    "sn_match": False,
                    "observed_sn": "abc-123",
                    "manual_reason_code": "SN_MISMATCH",
                    "manual_reason": "model originally judged mismatch",
                    "confidence": 0.95,
                },
                "{}",
                1.2,
                {},
                False,
            )
        return (
            _screen_sn_compliance_pass(),
            "{}",
            2.3,
            {},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)
    task = {
        "channel_order_no": "1",
        "fields": {
            "product_type": "手机 [B01]",
            "system_sn": "ABC123",
            "is_home_appliance": False,
            "address": "",
            },
            "images": [
                {"image_id": "img_001", "title": "鍟嗗搧鐓х墖", "source_url": "a"},
                {"image_id": "img_002", "title": "鎷嗗皝鐓х墖", "source_url": "b"},
                {"image_id": "img_003", "title": "SN photo", "source_url": "c"},
            ],
    }

    result = audit_task_v2("https://example.invalid/v1", "key", "model", task)

    assert calls == ["sn", "compliance"]
    assert result["manual_flag"] == "否"
    assert result["sn_match"] is True
    assert result["observed_sn"] == "abc-123"


def test_fast_audit_does_not_auto_pass_without_photo_compliance(monkeypatch):
    calls = []

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append(stage)
        return (
            {
                "sn_match": True,
                "observed_sn": "ABC123",
                "normalized_observed_sn": "ABC123",
                "confidence": 0.95,
            },
            "{}",
            3.4,
            {"total_tokens": 1000},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_fast("https://example.invalid/v1", "key", "model", _base_task())

    assert calls == ["fast_sn"]
    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert result["strategy"] == "fast_sn_only_manual"
    assert result["model_calls"] == 1
    assert result["total_tokens"] == 1000


def test_model_timeout_is_60_seconds_for_hybrid_calls(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    timeouts = []

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        timeouts.append((stage, timeout_sec))
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
                "{}",
                1.0,
                {"total_tokens": 10},
                False,
            )
        return (
            _screen_sn_compliance_pass(),
            "{}",
            1.0,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", _base_task())

    assert result["manual_flag"] == "否"
    assert timeouts[0][0] == "hybrid_sn"
    assert timeouts[0][1] <= 60
    assert timeouts[0][1] == pytest.approx(60, abs=0.01)


def test_hybrid_edge_plugin_adds_no_model_stage_and_keeps_gate_on_original_images(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    monkeypatch.setenv("PHOTO_AUTH_EDGE_MAPPING_MODE", "on")
    stages = []
    gate_image_ids = []

    def fake_prepare(images, payload, **_kwargs):
        diagnostic = {"image_id": "edge_candidate__img_003", "local_path": str(tmp_path / "diag.png")}
        prepared_payload = dict(payload)
        prepared_payload["photo_auth_edge_candidates"] = [{
            "candidate_id": "img_003:bottom", "image_id": "img_003",
            "diagnostic_image_id": "edge_candidate__img_003", "side": "bottom",
        }]
        return [*images, diagnostic], prepared_payload

    def fake_call(_base, _key, _model, prompt, payload, images, *, stage, **_kwargs):
        stages.append((stage, [image["image_id"] for image in images]))
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        decision = _screen_sn_compliance_pass()
        decision["photo_authenticity_by_image"] = [
            _auth_observation("img_001"), _auth_observation("img_002"), _auth_observation("img_003"),
        ]
        decision["photo_auth_edge_candidate_reviews"] = [{
            "candidate_id": "img_003:bottom", "image_id": "img_003",
            "diagnostic_image_id": "edge_candidate__img_003", "side": "bottom",
            "classification": "clothing_or_scene", "confirmed_external_screen": False,
            "supporting_features": [], "reason": "not an external display",
        }]
        return (decision, "compliance", 0.1, {}, False)

    def fake_gate(*, legacy_row, images, **_kwargs):
        gate_image_ids.extend(image["image_id"] for image in images)
        return legacy_row

    monkeypatch.setattr(v2, "prepare_photo_auth_edge_mapping_inputs", fake_prepare)
    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)
    monkeypatch.setattr(v2, "apply_photo_authenticity_gate", fake_gate)

    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", _base_task(), cache_dir=tmp_path)

    assert [stage for stage, _ in stages] == ["hybrid_sn", "hybrid_compliance"]
    assert stages[1][1][-1] == "edge_candidate__img_003"
    assert gate_image_ids == ["img_001", "img_002", "img_003"]
    assert result["model_calls"] == 2


def test_hybrid_enforce_defers_legacy_image_strong_risk_to_authenticity_observations(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    monkeypatch.setenv("SN_LABEL_AUTH_REVIEW_MODE", "off")
    stages = []

    def fake_call(_base, _key, _model, _prompt, _payload, _images, *, stage, **_kwargs):
        stages.append(stage)
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        decision = _screen_sn_compliance_pass()
        decision.update({
            "manual_required": True,
            "manual_reason_codes": ["IMAGE_STRONG_RISK"],
            "manual_reason": "legacy model-only authenticity claim",
            "image_risk": True,
            "photo_authenticity_by_image": [
                _auth_observation("img_001"), _auth_observation("img_002"), _auth_observation("img_003"),
            ],
        })
        return (decision, "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", _base_task(), cache_dir=tmp_path)

    assert stages == ["hybrid_sn", "hybrid_compliance"]
    assert result["model_calls"] == 2
    assert result["manual_flag"] == "否"
    assert result["manual_reason_code"] == ""
    assert result["image_risk"] is False
    assert result["photo_authenticity_would_manual"] is False


def test_hybrid_shadow_keeps_legacy_image_strong_risk_as_rollback_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "shadow")
    monkeypatch.setenv("SN_LABEL_AUTH_REVIEW_MODE", "off")
    prompts = {}

    def fake_call(_base, _key, _model, prompt, _payload, _images, *, stage, **_kwargs):
        prompts[stage] = prompt
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        decision = _screen_sn_compliance_pass()
        decision.update({
            "manual_required": True,
            "manual_reason_codes": ["IMAGE_STRONG_RISK"],
            "manual_reason": "legacy model-only authenticity claim",
            "image_risk": True,
            "photo_authenticity_by_image": [
                _auth_observation("img_001"), _auth_observation("img_002"), _auth_observation("img_003"),
            ],
        })
        return (decision, "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", _base_task(), cache_dir=tmp_path)

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "IMAGE_STRONG_RISK"
    assert result["photo_authenticity_mode"] == "shadow"
    assert v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM in prompts["hybrid_compliance"]
    assert v2.PHOTO_AUTHENTICITY_REPLACEMENT_DIRECTIVE not in prompts["hybrid_compliance"]


def test_hybrid_enforce_preserves_non_authenticity_image_strong_risk(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    monkeypatch.setenv("SN_LABEL_AUTH_REVIEW_MODE", "off")
    prompts = {}

    def fake_call(_base, _key, _model, prompt, _payload, _images, *, stage, **_kwargs):
        prompts[stage] = prompt
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        decision = _screen_sn_compliance_pass()
        decision.update({
            "manual_required": True,
            "manual_reason_codes": ["IMAGE_STRONG_RISK"],
            "manual_reason": "possible strong risk",
            "image_risk": True,
            "tamper_checks": {"erasure_or_overwrite_risk": True},
            "photo_authenticity_by_image": [
                _auth_observation("img_001"), _auth_observation("img_002"), _auth_observation("img_003"),
            ],
        })
        return (decision, "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", _base_task(), cache_dir=tmp_path)

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "IMAGE_STRONG_RISK"
    assert result["image_risk"] is True
    assert v2.PHOTO_AUTHENTICITY_REPLACEMENT_DIRECTIVE in prompts["hybrid_compliance"]


def test_hybrid_edge_plugin_with_no_successful_candidate_uses_baseline_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    monkeypatch.setenv("PHOTO_AUTH_EDGE_MAPPING_MODE", "on")
    compliance_prompts = []

    monkeypatch.setattr(
        v2, "prepare_photo_auth_edge_mapping_inputs",
        lambda images, payload, **_kwargs: (images, payload),
    )

    def fake_call(_base, _key, _model, prompt, _payload, _images, *, stage, **_kwargs):
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        compliance_prompts.append(prompt)
        decision = _screen_sn_compliance_pass()
        decision["photo_authenticity_by_image"] = [
            _auth_observation("img_001"), _auth_observation("img_002"), _auth_observation("img_003"),
        ]
        return (decision, "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)
    monkeypatch.setattr(v2, "apply_photo_authenticity_gate", lambda *, legacy_row, **_kwargs: legacy_row)

    audit_task_hybrid("https://unused", "key", "qwen3.7-plus", _base_task(), cache_dir=tmp_path)

    assert len(compliance_prompts) == 1
    assert v2.read_photo_auth_edge_mapping_prompt() not in compliance_prompts[0]


def test_cli_default_mode_is_hybrid():
    source = v2.Path(v2.__file__).read_text(encoding="utf-8")

    assert 'parser.add_argument("--mode", choices=["fast", "hybrid", "v2", "sn_only"], default="hybrid")' in source


def test_hybrid_does_not_retry_after_model_timeout_under_order_budget(monkeypatch):
    calls = []

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append((stage, timeout_sec))
        raise TimeoutError("first call timed out")

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    try:
        audit_task_hybrid("https://example.invalid/v1", "key", "model", _base_task())
    except TimeoutError:
        pass

    assert len(calls) == 1
    assert calls[0][0] == "hybrid_sn"
    assert calls[0][1] <= 60
    assert calls[0][1] == pytest.approx(60, abs=0.01)


def test_v2_compliance_call_uses_remaining_order_budget(monkeypatch):
    calls = []
    now = [100.0]

    def fake_time():
        return now[0]

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append((stage, timeout_sec))
        if stage == "sn":
            now[0] += 59.0
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
                "{}",
                59.0,
                {"total_tokens": 10},
                False,
            )
        return (
            _screen_sn_compliance_pass(),
            "{}",
            0.5,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2.time, "time", fake_time)
    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_v2("https://example.invalid/v1", "key", "model", _base_task())

    assert result["manual_flag"] == "否"
    assert calls == [("sn", 60), ("compliance", 1.0)]


def test_fast_retry_uses_only_remaining_order_budget(monkeypatch):
    calls = []
    now = [200.0]

    def fake_time():
        return now[0]

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append((stage, timeout_sec))
        if len(calls) == 1:
            now[0] += 59.0
            raise TimeoutError("first call timed out")
        return (
            {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
            "{}",
            0.5,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2.time, "time", fake_time)
    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_fast("https://example.invalid/v1", "key", "model", _base_task())

    assert result["manual_flag"] == "是"
    assert calls == [("fast_sn", 60), ("fast_sn", 1.0)]


def test_hybrid_order_budget_stops_before_compliance_when_time_is_used(monkeypatch):
    calls = []
    now = [100.0]

    def fake_time():
        return now[0]

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append((stage, timeout_sec))
        now[0] += 59.5
        return (
            {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
            "{}",
            59.5,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2.time, "time", fake_time)
    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", _base_task())

    assert calls == [("hybrid_sn", 60)]
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert "60秒" in result["manual_reason"]
    assert "转人工" in result["manual_reason"]
    assert result["strategy"] == "hybrid_order_timeout_manual"


def test_fast_audit_reviews_sn_not_found_but_still_requires_photo_compliance(monkeypatch):
    calls = []

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append(stage)
        if stage == "fast_sn":
            return (
                {
                    "sn_match": False,
                    "observed_sn": "",
                    "normalized_observed_sn": "",
                    "manual_reason_code": "SN_NOT_FOUND",
                    "manual_reason": "棣栬疆鏈瘑鍒埌SN",
                    "product_type_match": True,
                    "product_photo_ok": True,
                    "unboxing_photo_ok": True,
                    "activation_photo_ok": True,
                    "duplicate_image_evidence": False,
                    "confidence": 0.9,
                },
                "{}",
                3.4,
                {"total_tokens": 1000},
                False,
            )
        return (
            {
                "sn_match": True,
                "observed_sn": "abc-123",
                "normalized_observed_sn": "ABC123",
                "manual_reason_code": "",
                "manual_reason": "",
                "confidence": 0.95,
            },
            "{}",
            4.5,
            {"total_tokens": 500},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_fast("https://example.invalid/v1", "key", "model", _base_task())

    assert calls == ["fast_sn", "fast_sn_review"]
    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert result["strategy"] == "fast_sn_only_manual"
    assert result["sn_match"] is True
    assert result["observed_sn"] == "abc-123"
    assert result["model_calls"] == 2
    assert result["total_tokens"] == 1500


def test_hybrid_stops_before_compliance_when_sn_mismatches(monkeypatch):
    calls = []

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append(stage)
        return (
            {
                "sn_match": False,
                "observed_sn": "ZZZ999",
                "normalized_observed_sn": "ZZZ999",
                "manual_reason_code": "SN_MISMATCH",
                "manual_reason": "SN mismatch",
                "confidence": 0.95,
            },
            "{}",
            2.0,
            {"total_tokens": 900},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", _base_task())

    assert calls == ["hybrid_sn"]
    assert result["manual_flag"] == "是"
    assert "SN_MISMATCH" in result["manual_reason"]
    assert result["strategy"] == "hybrid_sn_manual"


def test_sn_normalization_does_not_trust_true_flag_when_observed_sn_differs():
    normalized = v2._normalize_sn_result(
        {"system_sn": "AFWFUN4523H00278"},
        {
            "sn_match": True,
            "observed_sn": "AEWFUN4523H00278",
            "normalized_observed_sn": "AEWFUN4523H00278",
            "manual_reason_code": "SN_MATCH",
            "manual_reason": "",
            "confidence": 0.9,
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["manual_reason_code"] == "SN_MISMATCH"
    assert normalized["manual_reason_codes"] == ["SN_MISMATCH"]


def test_sn_payload_keeps_first_pass_independent_without_full_system_sn():
    task = {
        "channel_order_no": "481172483578762919936056",
        "fields": {
            "product_type": "[A06] hot water heater",
            "system_sn": "GAOUATO0301GBRARL4AC",
        },
    }

    payload = build_sn_payload(
        task,
        task["fields"],
        [{"image_id": "img_002", "title": "SN photo", "source_url": "x"}],
    )

    dumped = str(payload)
    assert "GAOUATO0301GBRARL4AC" not in dumped
    assert payload["system_sn_len"] == 20
    assert payload["sn_stage"] == "independent_read"
    assert payload["system_sn_available_to_model"] is False
    assert "matches_given_system_sn" not in payload["comparison_policy"]


def test_sn_not_found_sentinel_is_not_treated_as_mismatch():
    normalized = v2._normalize_sn_result(
        {"system_sn": "ADUNUT5C22000330"},
        {
            "sn_match": False,
            "observed_sn": "SN_NOT_FOUND",
            "normalized_observed_sn": "SN_NOT_FOUND",
            "manual_reason_code": "SN_NOT_FOUND",
            "manual_reason": "no complete SN is readable",
            "confidence": 0.95,
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == ""
    assert normalized["normalized_observed_sn"] == ""
    assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


def test_unknown_or_na_sn_sentinels_become_sn_not_found():
    for sentinel in ("UNKNOWN", "N/A", "NA", "NONE", "null"):
        normalized = v2._normalize_sn_result(
            {"system_sn": "ADUNUT5C22000330"},
            {
                "sn_match": False,
                "observed_sn": sentinel,
                "normalized_observed_sn": sentinel,
                "manual_reason_code": "MODEL_UNCERTAIN",
                "manual_reason": "SN is not readable",
                "confidence": 0.8,
            },
        )

        assert normalized["sn_match"] is False
        assert normalized["observed_sn"] == ""
        assert normalized["normalized_observed_sn"] == ""
        assert normalized["manual_reason_code"] == "SN_NOT_FOUND"


def test_o_zero_conflict_from_image_sn_is_rejected_without_visual_ambiguity_pass():
    normalized = v2._normalize_sn_result(
        {"system_sn": "GAOUATO0301GBRARL4AC"},
        {
            "sn_match": True,
            "observed_sn": "GA0UAT00301GBRARL4AC",
            "normalized_observed_sn": "GA0UAT00301GBRARL4AC",
            "confidence": 0.95,
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["observed_sn"] == "GA0UAT00301GBRARL4AC"
    assert normalized["normalized_observed_sn"] == "GA0UAT00301GBRARL4AC"
    assert "raw_observed_sn" not in normalized
    assert "visual_sn_ambiguity" not in normalized
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


def test_visual_ambiguity_requires_model_support_and_confidence():
    normalized = v2._normalize_sn_result(
        {"system_sn": "GA0SZY00J01GHS5CT6UV"},
        {
            "sn_match": False,
            "observed_sn": "GAOSZY00J01GHS5CT6UV",
            "normalized_observed_sn": "GAOSZY00J01GHS5CT6UV",
            "manual_reason_code": "SN_MISMATCH",
            "confidence": 0.98,
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


def test_visual_ambiguity_requires_high_confidence():
    normalized = v2._normalize_sn_result(
        {"system_sn": "GA0SZY00J01GHS5CT6UV"},
        {
            "sn_match": True,
            "observed_sn": "GAOSZY00J01GHS5CT6UV",
            "normalized_observed_sn": "GAOSZY00J01GHS5CT6UV",
            "confidence": 0.5,
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


def test_short_visual_ambiguity_is_not_auto_accepted():
    normalized = v2._normalize_sn_result(
        {"system_sn": "ABC5"},
        {
            "sn_match": True,
            "observed_sn": "ABCS",
            "normalized_observed_sn": "ABCS",
            "confidence": 0.99,
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


def test_non_whitelisted_visual_like_characters_are_not_auto_accepted():
    normalized = v2._normalize_sn_result(
        {"system_sn": "GA0SZY00J01GHS5C1UV"},
        {
            "sn_match": True,
            "observed_sn": "GADSZY00J01GHS5CTUV",
            "normalized_observed_sn": "GADSZY00J01GHS5CTUV",
            "confidence": 0.99,
        },
    )

    assert normalized["sn_match"] is False
    assert normalized["manual_reason_code"] == "SN_MISMATCH"


def test_compliance_gate_rejects_visual_ambiguous_package_sn_candidate():
    decision = _screen_sn_compliance_pass()
    decision.update(
        {
            "system_sn": "511-320Q1063-AB25-1042201",
            "normalized_system_sn": "511320Q1063AB251042201",
            "observed_sn": "511-320Q1063-AB25-1042201",
            "normalized_observed_sn": "511320Q1063AB251042201",
            "activation_evidence_type": "SCREEN_ACTIVE_WITH_SN",
            "same_photo_or_same_group_chain": True,
            "sn_candidates": [
                {
                    "image_id": "img_003",
                    "source": "PACKAGE_LABEL",
                    "raw_text": "S/N 511 - 32OQ1063 - AB25- 1042201",
                    "normalized_text": "51132OQ1063AB251042201",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "DEVICE_INFO_WITH_ID",
                "screen_sn_visible": False,
                "screen_identity_text": "device information page",
            },
        }
    )

    assert v2._activation_pass_gate_reason(decision) == "SN_MISMATCH"
    normalized = v2.enforce_photo_noncompliance_manual(decision)
    assert normalized["manual_required"] is True
    assert normalized["manual_reason_codes"] == ["SN_MISMATCH"]


def test_compliance_gate_keeps_clear_candidate_sn_mismatch():
    decision = _screen_sn_compliance_pass()
    decision.update(
        {
            "system_sn": "511310A1111B4301050610",
            "normalized_system_sn": "511310A1111B4301050610",
            "activation_evidence_type": "SCREEN_ACTIVE_WITH_SN",
            "same_photo_or_same_group_chain": True,
            "sn_candidates": [
                {
                    "image_id": "img_003",
                    "source": "PACKAGE_LABEL",
                    "raw_text": "S/N BC93MF",
                    "normalized_text": "BC93MF",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "DEVICE_INFO_WITH_ID",
                "screen_sn_visible": False,
                "screen_identity_text": "device information page",
            },
        }
    )

    assert v2._activation_pass_gate_reason(decision) == "SN_MISMATCH"


def test_compliance_gate_keeps_explicit_false_visual_candidate_mismatch():
    decision = _screen_sn_compliance_pass()
    decision.update(
        {
            "system_sn": "GA0SZY00J01GHS5CT6UV",
            "normalized_system_sn": "GA0SZY00J01GHS5CT6UV",
            "activation_evidence_type": "SCREEN_SN",
            "sn_candidates": [
                {
                    "image_id": "img_003",
                    "source": "SCREEN",
                    "raw_text": "SN GAOSZY00J01GHS5CT6UV",
                    "normalized_text": "GAOSZY00J01GHS5CT6UV",
                    "readable": True,
                    "matches_system_sn": False,
                }
            ],
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "GAOSZY00J01GHS5CT6UV",
            },
            "confidence": 0.98,
        }
    )

    assert v2._activation_pass_gate_reason(decision) == "SN_MISMATCH"


def test_hybrid_runs_compliance_after_sn_passes(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    calls = []

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append((stage, prompt, payload))
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
                "{}",
                2.0,
                {"total_tokens": 900},
                False,
            )
        return (
            _screen_sn_compliance_pass(),
            "{}",
            3.0,
            {"total_tokens": 1100},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", _base_task())

    assert [stage for stage, _prompt, _payload in calls] == ["hybrid_sn", "hybrid_compliance"]
    assert calls[1][1] == v2.compliance_prompt_for_category(
        "ordinary_3c", product_type="手机 [B01]",
    )
    assert result["manual_flag"] == "否"
    assert result["strategy"] == "hybrid_sn_then_compliance"
    assert result["model_calls"] == 2
    assert result["total_tokens"] == 2000


def test_hybrid_forces_local_category_over_model_category(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    seen = {}
    task = _base_task()
    task["fields"].update({"product_type": "电脑", "goods_name": "ThinkPad 笔记本电脑"})

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        seen[stage] = {"prompt": prompt, "payload": payload}
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
                "{}",
                1.0,
                {"total_tokens": 10},
                False,
            )
        return (
            {
                "manual_required": False,
                "manual_reason_codes": [],
                "manual_reason": "",
                "effective_category": "ordinary_3c",
                "product_type_match": "match",
                "product_photo_ok": True,
                "unboxing_photo_ok": True,
                "activation_photo_ok": True,
                "activation_evidence_type": "identity_info",
                "image_risk": False,
                "duplicate_image_evidence": False,
                "invoice_orange_warning": False,
                "confidence": 0.95,
            },
            "{}",
            1.0,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", task)

    assert seen["hybrid_compliance"]["prompt"] == v2.COMPUTER_COMPLIANCE_PROMPT
    assert seen["hybrid_compliance"]["payload"]["effective_category"] == "computer"
    assert result["manual_flag"] == "否"


def test_verified_sn_compliance_does_not_rejudge_sn_candidates():
    decision = {
        "_sn_already_verified_by_system": True,
        "manual_required": False,
        "manual_reason_codes": [],
        "manual_reason": "",
        "system_sn": "ABC123",
        "normalized_system_sn": "ABC123",
        "observed_sn": "ABD123",
        "effective_category": "ordinary_3c",
        "product_type_match": "match",
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "package_visible": True,
        "activation_photo_ok": True,
        "activation_evidence_type": "identity_info",
        "sn_candidates": [
            {
                "raw_text": "SN: ABD123",
                "normalized_text": "ABD123",
                "readable": True,
                "matches_system_sn": False,
            }
        ],
        "image_risk": False,
        "duplicate_image_evidence": False,
        "invoice_orange_warning": False,
        "confidence": 0.95,
    }

    result = v2.enforce_photo_noncompliance_manual(decision)

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_verified_watch_pairing_screen_cannot_use_package_sn_as_identity():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "_sn_already_verified_by_system": True,
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "WXGW2FK03K",
            "normalized_system_sn": "WXGW2FK03K",
            "observed_sn": "WXGW2FK03K",
            "effective_category": "ordinary_3c",
            "product_type": "[B03] 智能手表手环",
            "product_type_match": "match",
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_ACTIVE_WITH_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "PAIRING_OR_SETUP",
                "screen_sn_visible": False,
                "screen_sn_text": "",
                "screen_identity_text": "Apple Watch 01130",
            },
            "sn_candidates": [
                {
                    "source": "PACKAGE_LABEL",
                    "field_type": "SN",
                    "raw_text": "(S) Serial No. WXGW2FK03K",
                    "normalized_text": "WXGW2FK03K",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "same_photo_or_same_group_chain": True,
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["ACTIVATION_PHOTO_INVALID"]


def test_verified_watch_about_screen_with_serial_remains_valid():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "_sn_already_verified_by_system": True,
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "WXGW2FK03K",
            "normalized_system_sn": "WXGW2FK03K",
            "observed_sn": "WXGW2FK03K",
            "effective_category": "ordinary_3c",
            "product_type": "[B03] 智能手表手环",
            "product_type_match": "match",
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "WXGW2FK03K",
                "screen_identity_text": "Serial Number WXGW2FK03K",
            },
            "sn_candidates": [
                {
                    "source": "DEVICE_SCREEN",
                    "field_type": "SN",
                    "raw_text": "Serial Number WXGW2FK03K",
                    "normalized_text": "WXGW2FK03K",
                    "readable": True,
                    "matches_system_sn": True,
                }
            ],
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_verified_phone_imei_screen_is_not_treated_as_sn_conflict():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "_sn_already_verified_by_system": True,
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "3L164L00G4M00000",
            "normalized_system_sn": "3L164L00G4M00000",
            "observed_sn": "3L164L00G4M00000",
            "effective_category": "ordinary_3c",
            "product_type": "[B01] 手机",
            "product_type_match": "match",
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "package_visible": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "SCREEN_ACTIVE_WITH_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "DEVICE_INFO_WITH_ID",
                "screen_sn_visible": True,
                "screen_sn_text": "864443087137938",
                "screen_identity_text": "IMEI号: 864443087137938",
            },
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_verified_home_appliance_without_visible_packaging_is_manual():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "_sn_already_verified_by_system": True,
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "system_sn": "600961101427",
            "normalized_system_sn": "600961101427",
            "observed_sn": "600961101427",
            "effective_category": "home_appliance",
            "product_type": "[A02] 电冰箱",
            "product_type_match": "match",
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "package_visible": False,
            "activation_photo_ok": True,
            "activation_evidence_type": "PACKAGE_SN_ONLY",
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["UNBOXING_PHOTO_INVALID"]


def test_verified_home_appliance_without_packaging_passes_strict_home_scene_gate():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "_sn_already_verified_by_system": True,
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "effective_category": "home_appliance",
            "category_name": "电冰箱",
            "product_type_match": "match",
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "package_visible": False,
            "whole_product_visible": True,
            "home_or_installation_scene_visible": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "PACKAGE_SN_ONLY",
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_verified_home_appliance_packaging_without_product_body_is_manual():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "_sn_already_verified_by_system": True,
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "effective_category": "home_appliance",
            "category_name": "电冰箱",
            "product_type_match": "match",
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "package_visible": True,
            "whole_product_visible": False,
            "home_or_installation_scene_visible": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "PACKAGE_SN_ONLY",
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["UNBOXING_PHOTO_INVALID"]


def test_verified_home_appliance_ignores_activation_photo_false_after_sn_verified():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "_sn_already_verified_by_system": True,
            "manual_required": True,
            "manual_reason_codes": ["ACTIVATION_PHOTO_INVALID"],
            "manual_reason": "模型误判家电激活照片未亮屏",
            "system_sn": "600961101427",
            "normalized_system_sn": "600961101427",
            "observed_sn": "600961101427",
            "effective_category": "home_appliance",
            "product_type": "[A02] 电冰箱",
            "category_name": "电冰箱",
            "product_type_match": "match",
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "package_visible": True,
            "activation_photo_ok": False,
            "activation_evidence_type": "PACKAGE_SN_ONLY",
            "image_risk": False,
            "photo_integrity": {
                "collage_or_edit_risk": False,
                "evidence_chain_trustworthy": True,
            },
            "duplicate_image_evidence": False,
            "_exact_duplicate_image_groups": [],
            "invoice_orange_warning": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is False
    assert "ACTIVATION_PHOTO_INVALID" not in result["manual_reason_codes"]


@pytest.mark.parametrize(
    ("effective_category", "product_type"),
    [
        ("ordinary_3c", "[B01] 手机"),
        ("computer", "[C01] 笔记本电脑"),
    ],
)
def test_verified_non_home_activation_photo_false_still_blocks(effective_category, product_type):
    result = v2.enforce_photo_noncompliance_manual(
        {
            "_sn_already_verified_by_system": True,
            "manual_required": True,
            "manual_reason_codes": ["ACTIVATION_PHOTO_INVALID"],
            "manual_reason": "非家电激活照片不合格",
            "system_sn": "ABC123",
            "normalized_system_sn": "ABC123",
            "observed_sn": "ABC123",
            "effective_category": effective_category,
            "product_type": product_type,
            "product_type_match": "match",
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "package_visible": True,
            "activation_photo_ok": False,
            "activation_evidence_type": "SCREEN_SN",
            "activation_screen": {
                "screen_on": True,
                "screen_content_type": "ABOUT_DEVICE_SN",
                "screen_sn_visible": True,
                "screen_sn_text": "ABC123",
                "screen_identity_text": "Serial Number ABC123",
            },
            "image_risk": False,
            "photo_integrity": {
                "collage_or_edit_risk": False,
                "evidence_chain_trustworthy": True,
            },
            "duplicate_image_evidence": False,
            "_exact_duplicate_image_groups": [],
            "invoice_orange_warning": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["ACTIVATION_PHOTO_INVALID"]


@pytest.mark.parametrize("missing_field", ["whole_product_visible", "home_or_installation_scene_visible"])
def test_verified_home_appliance_without_packaging_requires_complete_home_scene_gate(missing_field):
    decision = {
        "_sn_already_verified_by_system": True,
        "manual_required": False,
        "manual_reason_codes": [],
        "manual_reason": "",
        "effective_category": "home_appliance",
        "category_name": "电冰箱",
        "product_type_match": "match",
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "package_visible": False,
        "whole_product_visible": True,
        "home_or_installation_scene_visible": True,
        "activation_photo_ok": True,
        "activation_evidence_type": "PACKAGE_SN_ONLY",
        "image_risk": False,
        "duplicate_image_evidence": False,
        "confidence": 0.95,
    }
    decision[missing_field] = False

    result = v2.enforce_photo_noncompliance_manual(decision)

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["UNBOXING_PHOTO_INVALID"]


@pytest.mark.parametrize("effective_category", ["ordinary_3c", "computer"])
def test_no_box_home_scene_gate_does_not_apply_to_3c_or_computer(effective_category):
    result = v2.enforce_photo_noncompliance_manual(
        {
            "_sn_already_verified_by_system": True,
            "manual_required": False,
            "manual_reason_codes": [],
            "manual_reason": "",
            "effective_category": effective_category,
            "product_type_match": "match",
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "package_visible": False,
            "whole_product_visible": True,
            "home_or_installation_scene_visible": True,
            "activation_photo_ok": True,
            "activation_evidence_type": "PACKAGE_SN_ONLY",
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_reason_codes"] != ["UNBOXING_PHOTO_INVALID"]


@pytest.mark.parametrize(
    "code, field",
    [
        ("IMAGE_STRONG_RISK", "image_risk"),
        ("DUPLICATE_IMAGE_EVIDENCE", "duplicate_image_evidence"),
        ("PRODUCT_TYPE_MISMATCH", "product_type_match"),
        ("PRODUCT_PHOTO_INVALID", "product_photo_ok"),
    ],
)
def test_home_scene_gate_does_not_override_higher_priority_photo_failures(code, field):
    decision = {
        "_sn_already_verified_by_system": True,
        "manual_required": True,
        "manual_reason_codes": [code],
        "manual_reason": code,
        "effective_category": "home_appliance",
        "category_name": "电冰箱",
        "product_type_match": "match",
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "package_visible": False,
        "whole_product_visible": True,
        "home_or_installation_scene_visible": True,
        "activation_photo_ok": True,
        "activation_evidence_type": "PACKAGE_SN_ONLY",
        "image_risk": False,
        "duplicate_image_evidence": False,
        "confidence": 0.95,
    }
    if field == "image_risk":
        decision[field] = True
    elif field == "duplicate_image_evidence":
        decision[field] = True
        decision["_exact_duplicate_image_groups"] = [["img_001", "img_002", "img_003"]]
    elif field == "product_type_match":
        decision[field] = "mismatch"
    elif field == "product_photo_ok":
        decision[field] = False

    result = v2.enforce_photo_noncompliance_manual(decision)

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == [code]


def test_verified_home_activation_fallback_does_not_clear_invoice_orange_warning():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "_sn_already_verified_by_system": True,
            "manual_required": True,
            "manual_reason_codes": ["INVOICE_ORANGE_WARNING"],
            "manual_reason": "INVOICE_ORANGE_WARNING",
            "invoice_orange_warning": True,
            "effective_category": "home_appliance",
            "category_name": "电冰箱",
            "product_type_match": "match",
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "package_visible": True,
            "whole_product_visible": True,
            "home_or_installation_scene_visible": True,
            "activation_photo_ok": False,
            "activation_evidence_type": "PACKAGE_SN_ONLY",
            "image_risk": False,
            "duplicate_image_evidence": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["INVOICE_ORANGE_WARNING"]


def test_compliance_nonstandard_reason_codes_are_normalized():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "_sn_already_verified_by_system": True,
            "manual_required": True,
            "manual_reason_codes": ["SCREEN_PHOTO_OF_SCREEN"],
            "manual_reason": "screen photo",
            "effective_category": "ordinary_3c",
            "product_type_match": "match",
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "image_risk": False,
            "duplicate_image_evidence": False,
            "invoice_orange_warning": False,
            "confidence": 0.95,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["IMAGE_STRONG_RISK"]


def test_hybrid_uses_targeted_review_for_near_sn_mismatch(monkeypatch):
    calls = []
    task = _base_task()
    task["fields"]["system_sn"] = "AB97L200000N7Q4AEUPZ"

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append((stage, payload))
        if stage == "hybrid_sn":
            return (
                {
                    "sn_match": True,
                    "observed_sn": "AB97L 20000 0N7Q4 AFUPZ",
                    "normalized_observed_sn": "AB97L200000N7Q4AFUPZ",
                    "manual_reason_code": "SN_FOUND",
                    "confidence": 0.93,
                },
                "{}",
                2.0,
                {"total_tokens": 900},
                False,
            )
        if stage == "hybrid_sn_targeted_review":
            assert payload["system_sn"] == "AB97L200000N7Q4AEUPZ"
            assert payload["previous_decision"]["normalized_observed_sn"] == "AB97L200000N7Q4AFUPZ"
            return (
                {
                    "sn_match": True,
                    "observed_sn": "AB97L200000N7Q4AEUPZ",
                    "normalized_observed_sn": "AB97L200000N7Q4AEUPZ",
                    "manual_reason_code": "SN_MATCH",
                    "confidence": 0.96,
                },
                "{}",
                3.0,
                {"total_tokens": 700},
                False,
            )
        return (
            _screen_sn_compliance_pass(),
            "{}",
            3.0,
            {"total_tokens": 1100},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", task)

    assert [stage for stage, _payload in calls] == ["hybrid_sn", "hybrid_sn_targeted_review", "hybrid_compliance"]
    assert result["sn_match"] is True
    assert result["observed_sn"] == "AB97L200000N7Q4AEUPZ"
    assert result["model_calls"] == 3


def test_hybrid_targeted_review_explicit_rejection_blocks_pass(monkeypatch):
    calls = []
    task = _base_task()
    task["fields"]["system_sn"] = "AB97L200000N7Q4AEUPZ"

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append(stage)
        if stage == "hybrid_sn":
            return (
                {
                    "sn_match": True,
                    "observed_sn": "AB97L200000N7Q4AFUPZ",
                    "normalized_observed_sn": "AB97L200000N7Q4AFUPZ",
                    "manual_reason_code": "SN_FOUND",
                    "confidence": 0.93,
                },
                "{}",
                2.0,
                {"total_tokens": 900},
                False,
            )
        if stage == "hybrid_sn_targeted_review":
            return (
                {
                    "sn_match": False,
                    "matches_given_system_sn": False,
                    "observed_sn": "AB97L200000N7Q4AEUPZ",
                    "normalized_observed_sn": "AB97L200000N7Q4AEUPZ",
                    "manual_reason_code": "MODEL_UNCERTAIN",
                    "manual_reason": "cannot confirm the target SN from the image",
                    "confidence": 0.6,
                },
                "{}",
                3.0,
                {"total_tokens": 700},
                False,
            )
        return (
            _screen_sn_compliance_pass(),
            "{}",
            3.0,
            {"total_tokens": 1100},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", task)

    assert calls == ["hybrid_sn", "hybrid_sn_targeted_review"]
    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert result["strategy"] == "hybrid_sn_targeted_review_manual"


def test_hybrid_targeted_review_conflicting_flags_block_pass(monkeypatch):
    calls = []
    task = _base_task()
    task["fields"]["system_sn"] = "AB97L200000N7Q4AEUPZ"

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append(stage)
        if stage == "hybrid_sn":
            return (
                {
                    "sn_match": True,
                    "observed_sn": "AB97L200000N7Q4AFUPZ",
                    "normalized_observed_sn": "AB97L200000N7Q4AFUPZ",
                    "manual_reason_code": "SN_FOUND",
                    "confidence": 0.93,
                },
                "{}",
                2.0,
                {"total_tokens": 900},
                False,
            )
        if stage == "hybrid_sn_targeted_review":
            return (
                {
                    "sn_match": False,
                    "matches_given_system_sn": True,
                    "observed_sn": "AB97L200000N7Q4AEUPZ",
                    "normalized_observed_sn": "AB97L200000N7Q4AEUPZ",
                    "manual_reason_code": "MODEL_UNCERTAIN",
                    "manual_reason": "conflicting confirmation flags",
                    "confidence": 0.95,
                },
                "{}",
                3.0,
                {"total_tokens": 700},
                False,
            )
        return (
            _screen_sn_compliance_pass(),
            "{}",
            3.0,
            {"total_tokens": 1100},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", task)

    assert calls == ["hybrid_sn", "hybrid_sn_targeted_review"]
    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert result["strategy"] == "hybrid_sn_targeted_review_manual"


def test_hybrid_can_disable_targeted_review_for_first_pass_only(monkeypatch):
    calls = []
    task = _base_task()
    task["fields"]["system_sn"] = "AB97L200000N7Q4AEUPZ"

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append(stage)
        return (
            {
                "sn_match": True,
                "observed_sn": "AB97L200000N7Q4AFUPZ",
                "normalized_observed_sn": "AB97L200000N7Q4AFUPZ",
                "manual_reason_code": "SN_FOUND",
                "confidence": 0.93,
            },
            "{}",
            2.0,
            {"total_tokens": 900},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid(
        "https://example.invalid/v1",
        "key",
        "model",
        task,
        allow_targeted_review=False,
    )

    assert calls == ["hybrid_sn"]
    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["strategy"] == "hybrid_sn_manual"


def test_audit_task_path_passes_targeted_review_flag_to_hybrid(monkeypatch, tmp_path):
    captured = {}
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(_base_task()), encoding="utf-8")

    def fake_audit_task_hybrid(
        base_url,
        api_key,
        model,
        task,
        *,
        cache_dir=None,
        allow_review=True,
        allow_targeted_review=True,
    ):
        captured["allow_targeted_review"] = allow_targeted_review
        return {"id": task["channel_order_no"], "manual_reason_code": ""}

    monkeypatch.setattr(v2, "audit_task_hybrid", fake_audit_task_hybrid)

    v2.audit_task_path(
        1,
        1,
        task_path,
        base_url="https://example.invalid/v1",
        api_key="key",
        model="model",
        mode="hybrid",
        cache_dir=tmp_path / "cache",
        allow_review=True,
        allow_targeted_review=False,
    )

    assert captured["allow_targeted_review"] is False


def test_hybrid_does_not_target_review_short_unrelated_sn_mismatch(monkeypatch):
    calls = []
    task = _base_task()
    task["fields"]["system_sn"] = "511310A1111B4301050610"

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        calls.append(stage)
        return (
            {
                "sn_match": True,
                "observed_sn": "BC93MF",
                "normalized_observed_sn": "BC93MF",
                "manual_reason_code": "SN_FOUND",
                "confidence": 0.96,
            },
            "{}",
            2.0,
            {"total_tokens": 900},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", task)

    assert calls == ["hybrid_sn"]
    assert result["manual_reason_code"] == "SN_MISMATCH"
    assert result["strategy"] == "hybrid_sn_manual"


def test_hybrid_forces_manual_when_pass_candidate_lacks_activation_evidence_type(monkeypatch):
    monkeypatch.setenv("DIGITAL_ACTIVATION_EVIDENCE_MODE", "off")
    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
                "{}",
                1.0,
                {"total_tokens": 10},
                False,
            )
        return (
            {
                "manual_required": False,
                "manual_reason_codes": [],
                "manual_reason": "",
                "product_type_match": True,
                "product_photo_ok": True,
                "unboxing_photo_ok": True,
                "activation_photo_ok": True,
                "duplicate_image_evidence": False,
                "confidence": 0.9,
            },
            "{}",
            1.0,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", _base_task())

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "ACTIVATION_PHOTO_INVALID"
    assert result["activation_photo_ok"] is True


def test_hybrid_forces_manual_when_activation_evidence_is_screen_on_without_sn(monkeypatch):
    monkeypatch.setenv("DIGITAL_ACTIVATION_EVIDENCE_MODE", "off")
    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
                "{}",
                1.0,
                {"total_tokens": 10},
                False,
            )
        return (
            {
                "manual_required": False,
                "manual_reason_codes": [],
                "manual_reason": "",
                "product_type_match": True,
                "product_photo_ok": True,
                "unboxing_photo_ok": True,
                "activation_photo_ok": True,
                "activation_evidence_type": "SCREEN_ON_NO_SN",
                "image_risk": False,
                "duplicate_image_evidence": False,
                "confidence": 0.9,
            },
            "{}",
            1.0,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", _base_task())

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "ACTIVATION_PHOTO_INVALID"
    assert result["manual_reason_code"] == "ACTIVATION_PHOTO_INVALID"


def test_hybrid_forces_manual_when_image_risk_true_even_if_other_fields_pass(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    monkeypatch.setenv("SN_LABEL_AUTH_REVIEW_MODE", "off")

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
                "{}",
                1.0,
                {"total_tokens": 10},
                False,
            )
        return (
            {
                "manual_required": False,
                "manual_reason_codes": [],
                "manual_reason": "",
                "product_type_match": True,
                "product_photo_ok": True,
                "unboxing_photo_ok": True,
                "activation_photo_ok": True,
                "activation_evidence_type": "SCREEN_SN",
                "image_risk": True,
                "duplicate_image_evidence": False,
                "confidence": 0.9,
            },
            "{}",
            1.0,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", _base_task())

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "IMAGE_STRONG_RISK"


def test_hybrid_forces_manual_when_activation_photo_invalid_after_sn_passes(monkeypatch):
    monkeypatch.setenv("DIGITAL_ACTIVATION_EVIDENCE_MODE", "off")
    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
                "{}",
                1.0,
                {"total_tokens": 10},
                False,
            )
        return (
            {
                "manual_required": False,
                "manual_reason_codes": [],
                "manual_reason": "",
                "product_type_match": True,
                "product_photo_ok": True,
                "unboxing_photo_ok": True,
                "activation_photo_ok": False,
                "duplicate_image_evidence": False,
                "confidence": 0.9,
            },
            "{}",
            1.0,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", _base_task())

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "ACTIVATION_PHOTO_INVALID"
    assert result["manual_reason_code"] == "ACTIVATION_PHOTO_INVALID"
    assert result["activation_photo_ok"] is False


def test_hybrid_forces_manual_when_image_strong_risk_code_returned_without_manual_flag(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    monkeypatch.setenv("SN_LABEL_AUTH_REVIEW_MODE", "off")

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
                "{}",
                1.0,
                {"total_tokens": 10},
                False,
            )
        return (
            {
                "manual_required": False,
                "manual_reason_codes": ["IMAGE_STRONG_RISK"],
                "manual_reason": "possible collage",
                "product_type_match": True,
                "product_photo_ok": True,
                "unboxing_photo_ok": True,
                "activation_photo_ok": True,
                "activation_evidence_type": "SCREEN_SN",
                "image_risk": False,
                "duplicate_image_evidence": False,
                "confidence": 0.9,
            },
            "{}",
            1.0,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", _base_task())

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "IMAGE_STRONG_RISK"
    assert result["manual_reason_code"] == "IMAGE_STRONG_RISK"


def test_photo_noncompliance_enforcer_forces_model_uncertain_manual():
    result = v2.enforce_photo_noncompliance_manual(
        {
            "manual_required": False,
            "manual_reason_codes": ["MODEL_UNCERTAIN"],
            "manual_reason": "unclear activation evidence",
            "product_type_match": True,
            "product_photo_ok": True,
            "unboxing_photo_ok": True,
            "activation_photo_ok": True,
            "duplicate_image_evidence": False,
        }
    )

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["MODEL_UNCERTAIN"]


def test_hybrid_forces_manual_when_model_uncertain_code_returned_without_manual_flag(monkeypatch):
    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
                "{}",
                1.0,
                {"total_tokens": 10},
                False,
            )
        return (
            {
                "manual_required": False,
                "manual_reason_codes": ["MODEL_UNCERTAIN"],
                "manual_reason": "unclear activation evidence",
                "product_type_match": True,
                "product_photo_ok": True,
                "unboxing_photo_ok": True,
                "activation_photo_ok": True,
                "duplicate_image_evidence": False,
                "confidence": 0.5,
            },
            "{}",
            1.0,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", _base_task())

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert result["manual_reason_cn"].startswith("模型识别不稳定或超时")


def test_hybrid_uses_activation_title_not_third_image(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    seen_sn_images = []

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        if stage == "hybrid_sn":
            seen_sn_images.extend(image["image_id"] for image in images)
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
                "{}",
                1.0,
                {"total_tokens": 10},
                False,
            )
        return (
            _screen_sn_compliance_pass(),
            "{}",
            1.0,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)
    task = {
        "channel_order_no": "1",
        "fields": {
            "product_type": "电视机[A01]",
            "system_sn": "ABC123",
            "is_home_appliance": True,
            "address": "西藏自治区拉萨市城关区顺通建材市场B-14",
        },
        "images": [
            {"image_id": "img_001", "title": "鍟嗗搧鐓х墖", "source_url": "a"},
            {"image_id": "img_002", "title": "鎷嗗皝鐓х墖", "source_url": "b"},
            {"image_id": "img_003", "title": "鎷嗗皝鐓х墖", "source_url": "c"},
            {"image_id": "img_004", "title": "SN photo", "source_url": "d"},
        ],
    }

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", task)

    assert seen_sn_images == ["img_004"]
    assert result["manual_flag"] == "否"


def test_hybrid_compliance_payload_preserves_explicit_image_groups(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    seen_payloads = {}
    seen_images = {}
    unboxing_title = "\u62c6\u5c01\u7167\u7247"
    activation_title = "SN\u7801\u91c7\u96c6 / \u6fc0\u6d3b\u7167\u7247"

    def fake_call_model(base_url, api_key, model, prompt, payload, images, *, stage, cache_dir=None, detail="auto", timeout_sec=60):
        seen_payloads[stage] = payload
        seen_images[stage] = images
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "normalized_observed_sn": "ABC123", "confidence": 0.95},
                "{}",
                1.0,
                {"total_tokens": 10},
                False,
            )
        return (
            _screen_sn_compliance_pass(),
            "{}",
            1.0,
            {"total_tokens": 10},
            False,
        )

    monkeypatch.setattr(v2, "call_model", fake_call_model)
    task = {
        "channel_order_no": "1",
        "fields": {
            "product_type": "\u7535\u51b0\u7bb1[A02]",
            "category_name": "\u7535\u51b0\u7bb1",
            "system_sn": "ABC123",
            "is_home_appliance": True,
            "address": "\u897f\u85cf\u81ea\u6cbb\u533a\u62c9\u8428\u5e02\u57ce\u5173\u533a\u987a\u901a\u5efa\u6750\u5e02\u573aB-14",
        },
        "image_groups": {
            unboxing_title: [{"image_id": "img_001", "source_url": "a"}],
            activation_title: [{"image_id": "img_002", "source_url": "b"}],
        },
    }

    result = audit_task_hybrid("https://example.invalid/v1", "key", "model", task)

    assert [image["image_id"] for image in seen_images["hybrid_sn"]] == ["img_002"]
    image_groups = seen_payloads["hybrid_compliance"]["image_groups"]
    assert set(image_groups) == {unboxing_title, activation_title}
    assert image_groups[unboxing_title][0]["image_id"] == "img_001"
    assert image_groups[activation_title][0]["image_id"] == "img_002"
    assert seen_payloads["hybrid_compliance"]["category_name"] == "\u7535\u51b0\u7bb1"
    assert result["manual_flag"] == "\u5426"

def test_home_appliance_unboxing_rule_accepts_installation_scene_without_packaging():
    checklist = open("docs/guobu-collector-field-checklist.md", encoding="utf-8").read()

    assert "拆封/安装照片组自身必须看到商品本体" in v2.HOME_APPLIANCE_COMPLIANCE_PROMPT
    assert "无包装时，必须能看到商品本体已经到家、安装、摆放或处于家庭/店铺/使用场景中" in v2.HOME_APPLIANCE_COMPLIANCE_PROMPT
    assert "不得用商品照片中的商品本体去补拆封/安装照片缺失的商品本体" in v2.HOME_APPLIANCE_COMPLIANCE_PROMPT
    assert "whole_product_visible" in v2.HOME_APPLIANCE_COMPLIANCE_PROMPT
    assert "home_or_installation_scene_visible" in v2.HOME_APPLIANCE_COMPLIANCE_PROMPT
    assert "whole_product_visible" not in v2.ORDINARY_3C_COMPLIANCE_PROMPT
    assert "home_or_installation_scene_visible" not in v2.COMPUTER_COMPLIANCE_PROMPT
    assert "当前品类：家电" in v2.HOME_APPLIANCE_COMPLIANCE_PROMPT
    assert "整组" in checklist


def test_compliance_prompt_contains_split_category_rules_and_invoice_warning():
    prompts = (
        v2.HOME_APPLIANCE_COMPLIANCE_PROMPT,
        v2.ORDINARY_3C_COMPLIANCE_PROMPT,
        v2.COMPUTER_COMPLIANCE_PROMPT,
    )

    for prompt in prompts:
        assert "SN 一致性已由系统完成，本阶段不重新识别或比对 SN" in prompt
        assert "发票编号旁有橘色感叹号，转人工 INVOICE_ORANGE_WARNING" in prompt
        assert "分类由系统完成，不要改判品类，不要输出自造原因码" in prompt
    assert "当前品类：家电" in v2.HOME_APPLIANCE_COMPLIANCE_PROMPT
    assert "当前品类：普通3C" in v2.ORDINARY_3C_COMPLIANCE_PROMPT
    assert "当前品类：电脑" in v2.COMPUTER_COMPLIANCE_PROMPT
    assert "电脑不得套用普通3C或家电规则" in v2.COMPUTER_COMPLIANCE_PROMPT
    assert "SN 已由第一阶段核验后，家电不要求亮屏或开机证据" in v2.HOME_APPLIANCE_COMPLIANCE_PROMPT
    assert "家电不要求亮屏或开机证据" not in v2.ORDINARY_3C_COMPLIANCE_PROMPT
    assert "家电不要求亮屏或开机证据" not in v2.COMPUTER_COMPLIANCE_PROMPT


def test_compliance_prompts_limit_product_type_mismatch_to_visible_product_form():
    scoped_rule = (
        "仅根据照片中可明确辨认的商品形态，判断其是否与订单商品类型（category_name）属于同类商品，"
        "不得增加订单未提及的条件。仅当两者明显不属于同一类商品时才返回 "
        "PRODUCT_TYPE_MISMATCH，无法判断时返回 MODEL_UNCERTAIN。"
    )

    for prompt in (
        v2.HOME_APPLIANCE_COMPLIANCE_PROMPT,
        v2.ORDINARY_3C_COMPLIANCE_PROMPT,
        v2.COMPUTER_COMPLIANCE_PROMPT,
    ):
        assert scoped_rule in prompt
        assert "若商品类型与订单类型明显不一致" not in prompt

    ordinary_3c_runtime_prompt = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="[B01] 手机",
        digital_activation_evidence_mode="on",
    )
    assert scoped_rule in ordinary_3c_runtime_prompt
    assert "若商品类型与订单类型明显不一致" not in ordinary_3c_runtime_prompt


def test_model_and_order_timeout_budget_is_60_seconds():
    assert v2.MODEL_TIMEOUT_SEC == 60
    assert v2.ORDER_TIMEOUT_SEC == 60


def test_photo_authenticity_report_columns_are_appended_and_off_defaults_are_csv_safe():
    legacy = [key for key, _ in v2.CSV_COLUMNS[:28]]
    assert legacy == [
        "id", "manual_flag", "source_flow_status", "manual_reason_code", "manual_reason_cn",
        "manual_reason", "business_pass", "elapsed_sec", "strategy", "model_calls", "total_tokens",
        "precheck_elapsed_sec", "sn_elapsed_sec", "compliance_elapsed_sec", "product_type",
        "source_examine_status", "source_settle_status", "system_sn", "observed_sn", "sn_match",
        "product_type_match", "address_ok", "product_photo_ok", "unboxing_photo_ok",
        "activation_photo_ok", "activation_evidence_type", "image_risk", "confidence",
    ]
    row = v2._final_row(
        {"channel_order_no": "1", "fields": {}}, {}, {}, {}, 0, 0, 0, 0,
    )
    assert row["photo_authenticity_mode"] == "off"
    assert row["photo_authenticity_would_manual"] is False
    assert row["photo_authenticity_image_results"] == ""
    assert set(row) <= {key for key, _ in v2.CSV_COLUMNS}


def test_photo_authenticity_image_results_are_serialized_deterministically():
    row = {
        "photo_authenticity_image_results": {
            "b": {"result": "manual_review", "score": 0.999},
            "a": {"result": "no_evidence", "score": 0.1},
            "c": {"result": "high_risk_non_real", "rule": "LOCAL_TREE"},
            "d": {"result": "no_evidence", "status": "local_tree_unavailable"},
        }
    }
    v2.prepare_photo_authenticity_report_fields(row)
    assert row["photo_authenticity_local_tree_hit_count"] == 1
    assert row["photo_authenticity_local_tree_unavailable_count"] == 1
    assert row["photo_authenticity_image_results"] == (
        '{"a":{"result":"no_evidence","score":0.1},'
        '"b":{"result":"manual_review","score":0.999},'
        '"c":{"result":"high_risk_non_real","rule":"LOCAL_TREE"},'
        '"d":{"result":"no_evidence","status":"local_tree_unavailable"}}'
    )


def test_photo_authenticity_cli_defaults_enforce_and_allows_explicit_off_before_runtime(monkeypatch):
    monkeypatch.delenv("PHOTO_AUTHENTICITY_MODE", raising=False)
    args = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert args.photo_authenticity_mode == "enforce"
    assert args.photo_authenticity_artifact_dir is None
    assert args.photo_authenticity_local_tree_enabled == "true"
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    env_off = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert env_off.photo_authenticity_mode == "off"
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    explicit_off = v2.parse_cli_args([
        "--tasks-dir", "tasks", "--out-dir", "out",
        "--photo-authenticity-mode", "off",
        "--photo-authenticity-local-tree-enabled", "false",
    ])
    assert explicit_off.photo_authenticity_mode == "off"
    assert explicit_off.photo_authenticity_local_tree_enabled == "false"
    with pytest.raises(SystemExit):
        v2.parse_cli_args([
            "--tasks-dir", "tasks", "--out-dir", "out",
            "--photo-authenticity-mode", "invalid",
        ])


def test_sn_char_review_prompt_plugin_is_reversible_without_changing_base_prompt(monkeypatch):
    monkeypatch.delenv("SN_CHAR_REVIEW_MODE", raising=False)

    assert v2.build_sn_prompt("off") == v2.SN_PROMPT

    enabled = v2.build_sn_prompt("on")
    assert enabled == v2.SN_PROMPT + "\n\n" + v2.SN_SIMILAR_CHAR_REVIEW_PROMPT
    assert "不进行视觉字符容错" in enabled
    assert "V/Y" in enabled
    assert "0/O/Q" in enabled
    assert "8/B" in enabled
    assert "5/S" in enabled
    assert "2/Z" in enabled
    assert "6/G" in enabled
    assert "1/I/L" in enabled


def test_sn_char_review_v2_prompt_is_mutually_exclusive_and_glyph_focused(monkeypatch):
    monkeypatch.delenv("SN_CHAR_REVIEW_MODE", raising=False)

    enabled = v2.build_sn_prompt("v2")

    assert enabled == v2.SN_PROMPT + "\n\n" + v2.SN_SIMILAR_CHAR_REVIEW_V2_PROMPT
    assert v2.SN_SIMILAR_CHAR_REVIEW_PROMPT not in enabled
    assert "0：轮廓近似纵向椭圆，比同字体 O 更窄；若该差异不可见则保持不确定" in enabled
    assert "O：轮廓更接近圆形，整体比 0 更圆润" in enabled
    assert "Q：圆形或椭圆形轮廓的右下方带有一条短斜线或尾笔" in enabled
    assert "不得新增、覆盖、解释或改变来源优先级、匹配、归一化、后续系统比对、有限视觉容错、目标复核或合规规则" in enabled
    assert "observed_sn 必须与最终选定的 sn_candidates 对应候选逐字符一致" in enabled
    assert "可按主提示词在 uncertain_positions 或 visual_ambiguity_notes 中记录位置和候选字符" in enabled
    assert "本插件只约束本轮图片读取" not in v2.SN_SIMILAR_CHAR_REVIEW_V2_PROMPT


def test_sn_char_review_cli_defaults_off_supports_env_and_rejects_invalid(monkeypatch):
    monkeypatch.delenv("SN_CHAR_REVIEW_MODE", raising=False)
    default_args = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert default_args.sn_char_review_mode == "off"

    monkeypatch.setenv("SN_CHAR_REVIEW_MODE", "on")
    env_args = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert env_args.sn_char_review_mode == "on"

    monkeypatch.setenv("SN_CHAR_REVIEW_MODE", "v2")
    env_v2 = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert env_v2.sn_char_review_mode == "v2"

    explicit_off = v2.parse_cli_args([
        "--tasks-dir", "tasks", "--out-dir", "out",
        "--sn-char-review-mode", "off",
    ])
    assert explicit_off.sn_char_review_mode == "off"

    explicit_v2 = v2.parse_cli_args([
        "--tasks-dir", "tasks", "--out-dir", "out",
        "--sn-char-review-mode", "v2",
    ])
    assert explicit_v2.sn_char_review_mode == "v2"

    with pytest.raises(SystemExit):
        v2.parse_cli_args([
            "--tasks-dir", "tasks", "--out-dir", "out",
            "--mode", "sn_only",
            "--sn-char-review-mode", "v2",
        ])

    monkeypatch.setenv("SN_CHAR_REVIEW_MODE", "invalid")
    with pytest.raises(SystemExit):
        v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])

    with pytest.raises(SystemExit):
        v2.parse_cli_args([
            "--tasks-dir", "tasks", "--out-dir", "out",
            "--sn-char-review-mode", "invalid",
        ])


def test_hybrid_uses_enabled_sn_char_review_prompt_and_records_mode(monkeypatch):
    monkeypatch.setenv("SN_CHAR_REVIEW_MODE", "on")
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    prompts = {}

    def fake_call(_base, _key, _model, prompt, _payload, _images, *, stage, **_kwargs):
        prompts[stage] = prompt
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        return (_screen_sn_compliance_pass(), "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)

    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", _base_task())

    assert prompts["hybrid_sn"] == v2.build_sn_prompt("on")
    assert result["sn_char_review_mode"] == "on"


def test_hybrid_uses_sn_char_review_v2_prompt_and_records_exact_mode(monkeypatch):
    monkeypatch.setenv("SN_CHAR_REVIEW_MODE", "v2")
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    prompts = {}

    def fake_call(_base, _key, _model, prompt, _payload, _images, *, stage, **_kwargs):
        prompts[stage] = prompt
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        return (_screen_sn_compliance_pass(), "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "enforce_photo_noncompliance_manual", lambda decision, **_: decision)

    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", _base_task())

    assert prompts["hybrid_sn"] == v2.build_sn_prompt("v2")
    assert v2.SN_SIMILAR_CHAR_REVIEW_PROMPT not in prompts["hybrid_sn"]
    assert result["sn_char_review_mode"] == "v2"


def test_sn_label_auth_review_prompt_plugin_defaults_off_and_explicit_on_appends_prompt(monkeypatch):
    monkeypatch.delenv("SN_LABEL_AUTH_REVIEW_MODE", raising=False)

    default_disabled = v2.compliance_prompt_for_category("home_appliance", include_photo_authenticity=True)
    explicit_on = v2.compliance_prompt_for_category(
        "home_appliance",
        include_photo_authenticity=True,
        sn_label_auth_review_mode="on",
    )

    assert explicit_on == default_disabled + "\n\n" + v2.read_sn_label_auth_review_prompt()
    assert "SN/条码标签非实拍专项审查插件" not in default_disabled


def test_sn_label_auth_review_prompt_plugin_appends_only_when_enabled(monkeypatch):
    monkeypatch.delenv("SN_LABEL_AUTH_REVIEW_MODE", raising=False)

    disabled = v2.compliance_prompt_for_category(
        "home_appliance",
        include_photo_authenticity=True,
        sn_label_auth_review_mode="off",
    )
    enabled = v2.compliance_prompt_for_category(
        "home_appliance",
        include_photo_authenticity=True,
        sn_label_auth_review_mode="on",
    )

    assert enabled == disabled + "\n\n" + v2.read_sn_label_auth_review_prompt()
    assert "SN/条码标签非实拍专项审查插件" in enabled
    assert "IMAGE_STRONG_RISK" in enabled
    assert "即使 SN 清晰、即使 SN 与系统一致" in enabled
    assert "跨载体摩尔纹/屏幕纹理专项观察" in enabled
    assert "CROSS_OBJECT_MOIRE" in enabled
    assert "至少两个非屏幕物理区域" in enabled
    assert "塑料膜局部反光、金属门板纹理、墙面/地砖自身纹理" in enabled
    assert "本次输入的每一张 image_id" in enabled
    assert "[AUTH_EVIDENCE:CROSS_OBJECT_MOIRE:" not in enabled
    assert "reason 只用于中文解释，不是程序控制字段" in enabled
    assert "只有明确命中时" in enabled

    fragment = v2.read_sn_label_auth_review_prompt()
    assert "本地图片真实性引擎是唯一裁决者" in fragment
    assert "不得因为本插件命中而直接设置 manual_required=true" in fragment
    assert "manual_required=true，image_risk=true" not in fragment


def test_sn_label_auth_plugin_defers_only_image_authenticity_to_local_engine():
    image_only = _screen_sn_compliance_pass()
    image_only.update({
        "manual_required": True,
        "manual_reason_codes": ["IMAGE_STRONG_RISK"],
        "manual_reason": "model-only authenticity claim",
        "image_risk": True,
        "effective_category": "ordinary_3c",
        "system_sn": "ABC123",
        "normalized_system_sn": "ABC123",
        "_sn_already_verified_by_system": True,
    })

    legacy = v2.enforce_photo_noncompliance_manual(image_only)
    deferred = v2.enforce_photo_noncompliance_manual(
        image_only,
        defer_image_authenticity_to_local=True,
    )

    assert legacy["manual_reason_codes"] == ["IMAGE_STRONG_RISK"]
    assert deferred["manual_required"] is False
    assert deferred["manual_reason_codes"] == []

    product_invalid = dict(image_only, product_photo_ok=False)
    preserved = v2.enforce_photo_noncompliance_manual(
        product_invalid,
        defer_image_authenticity_to_local=True,
    )
    assert preserved["manual_required"] is True
    assert preserved["manual_reason_codes"] == ["PRODUCT_PHOTO_INVALID"]


def test_sn_label_auth_review_requires_photo_authenticity_schema(monkeypatch):
    monkeypatch.delenv("SN_LABEL_AUTH_REVIEW_MODE", raising=False)

    base = v2.compliance_prompt_for_category("home_appliance", include_photo_authenticity=False)
    enabled_without_schema = v2.compliance_prompt_for_category(
        "home_appliance",
        include_photo_authenticity=False,
        sn_label_auth_review_mode="on",
    )

    assert enabled_without_schema == base
    assert "SN/条码标签非实拍专项审查插件" not in enabled_without_schema
    assert "跨载体摩尔纹/屏幕纹理专项观察" not in enabled_without_schema


def test_sn_label_auth_review_cli_defaults_off_supports_env_and_rejects_invalid(monkeypatch):
    monkeypatch.delenv("SN_LABEL_AUTH_REVIEW_MODE", raising=False)
    default_args = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert default_args.sn_label_auth_review_mode == "off"

    monkeypatch.setenv("SN_LABEL_AUTH_REVIEW_MODE", "on")
    env_args = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert env_args.sn_label_auth_review_mode == "on"

    explicit_off = v2.parse_cli_args([
        "--tasks-dir", "tasks", "--out-dir", "out",
        "--sn-label-auth-review-mode", "off",
    ])
    assert explicit_off.sn_label_auth_review_mode == "off"

    with pytest.raises(SystemExit):
        v2.parse_cli_args([
            "--tasks-dir", "tasks", "--out-dir", "out",
            "--sn-label-auth-review-mode", "invalid",
        ])


def test_photo_auth_edge_mapping_prompt_plugin_defaults_off_and_explicit_on_appends_once(monkeypatch):
    monkeypatch.delenv("PHOTO_AUTH_EDGE_MAPPING_MODE", raising=False)

    baseline = v2.compliance_prompt_for_category(
        "home_appliance",
        include_photo_authenticity=True,
        sn_label_auth_review_mode="off",
        digital_activation_evidence_mode="off",
    )
    explicit_off = v2.compliance_prompt_for_category(
        "home_appliance",
        include_photo_authenticity=True,
        sn_label_auth_review_mode="off",
        digital_activation_evidence_mode="off",
        photo_auth_edge_mapping_mode="off",
    )
    explicit_on = v2.compliance_prompt_for_category(
        "home_appliance",
        include_photo_authenticity=True,
        sn_label_auth_review_mode="off",
        digital_activation_evidence_mode="off",
        photo_auth_edge_mapping_mode="on",
    )
    fragment = v2.read_photo_auth_edge_mapping_prompt()

    assert explicit_off == baseline
    assert explicit_on == baseline + "\n\n" + fragment
    assert explicit_on.count(fragment) == 1


def test_photo_auth_edge_mapping_prompt_plugin_requires_authenticity_schema():
    baseline = v2.compliance_prompt_for_category(
        "ordinary_3c",
        include_photo_authenticity=False,
        digital_activation_evidence_mode="off",
    )
    enabled_without_schema = v2.compliance_prompt_for_category(
        "ordinary_3c",
        include_photo_authenticity=False,
        digital_activation_evidence_mode="off",
        photo_auth_edge_mapping_mode="on",
    )

    assert enabled_without_schema == baseline


def test_photo_auth_edge_mapping_prompt_preserves_safe_evidence_boundaries():
    fragment = v2.read_photo_auth_edge_mapping_prompt()

    assert "洋红线本身不是证据" in fragment
    assert "黑衣裤、阴影、深色背景" in fragment
    assert "商品自身机身或屏幕边框、包装结构和普通裁切" in fragment
    assert "仅有黑边、摩尔纹、反光、发光或像素纹理均不得单独确认" in fragment
    assert "classification=external_screen" in fragment
    assert "confirmed_external_screen=true" in fragment
    assert "查看器界面只能辅助，不能单独确认" in fragment
    assert "candidate_id、image_id、diagnostic_image_id、side" in fragment
    assert "photo_auth_edge_candidate_reviews" in fragment
    assert "不得因洋红线本身增加任何强弱证据" in fragment
    assert "不改变商品、拆封、激活、重复照片或其他合规字段" in fragment


@pytest.mark.parametrize(
    ("sn_label_mode", "digital_mode"),
    [("off", "off"), ("on", "off"), ("off", "on"), ("on", "on")],
)
def test_photo_auth_edge_mapping_prompt_is_the_final_append_after_existing_plugins(
    sn_label_mode, digital_mode,
):
    kwargs = {
        "product_type": "智能手机",
        "include_photo_authenticity": True,
        "sn_label_auth_review_mode": sn_label_mode,
        "digital_activation_evidence_mode": digital_mode,
    }
    disabled = v2.compliance_prompt_for_category(
        "ordinary_3c", photo_auth_edge_mapping_mode="off", **kwargs,
    )
    enabled = v2.compliance_prompt_for_category(
        "ordinary_3c", photo_auth_edge_mapping_mode="on", **kwargs,
    )

    assert enabled == disabled + "\n\n" + v2.read_photo_auth_edge_mapping_prompt()


def test_photo_auth_edge_mapping_off_does_not_load_experimental_prompt(monkeypatch):
    monkeypatch.setattr(
        v2,
        "read_photo_auth_edge_mapping_prompt",
        lambda: pytest.fail("experimental prompt loaded while plugin is off"),
    )

    v2.compliance_prompt_for_category(
        "home_appliance",
        include_photo_authenticity=True,
        photo_auth_edge_mapping_mode="off",
    )


def test_photo_auth_edge_mapping_off_is_exact_input_identity(monkeypatch, tmp_path):
    image = {"image_id": "i1", "local_path": str(tmp_path / "i1.jpg"), "source_url": "https://example/i1.jpg"}
    images = [image]
    payload = {"id": "order-1", "image_groups": {"activation": ["i1"]}}
    monkeypatch.setattr(v2, "scan_photo_auth_edge_candidates", lambda *_args, **_kwargs: pytest.fail("scanner called while plugin is off"), raising=False)

    result_images, result_payload = v2.prepare_photo_auth_edge_mapping_inputs(
        images, payload, mode="off", output_dir=tmp_path
    )

    assert result_images is images
    assert result_payload is payload


def test_photo_auth_edge_mapping_on_marks_only_strong_candidates_without_new_model_call(monkeypatch, tmp_path):
    source = tmp_path / "i1.jpg"
    source.write_bytes(b"image")
    image = {
        "image_id": "i1", "local_path": str(source),
        "source_url": "https://example/i1.jpg", "url": "https://fallback/i1.jpg",
    }
    payload = {"id": "order-1"}

    class StrongScan:
        status = "strong_candidate"
        sides = {"bottom": type("Side", (), {
            "status": "strong_candidate",
            "tangent_start_fraction": 0.1,
            "tangent_end_fraction": 0.9,
            "boundary_depth_fraction": 0.08,
            "reason": "outer_dark_run_with_abrupt_linear_boundary",
        })()}

        def to_dict(self):
            return {"status": self.status, "sides": {"bottom": {"status": "strong_candidate"}}}

    monkeypatch.setattr(v2, "scan_photo_auth_edge_candidates", lambda *_args, **_kwargs: {"i1": StrongScan()})
    monkeypatch.setattr(v2, "annotate_photo_auth_edge_candidates", lambda source_path, destination, scan: destination.write_bytes(b"annotated") or destination)

    result_images, result_payload = v2.prepare_photo_auth_edge_mapping_inputs(
        [image], payload, mode="on", output_dir=tmp_path
    )

    assert len(result_images) == 1
    assert result_images[0]["image_id"] == "i1"
    assert result_images[0].get("source_url") is None
    assert result_images[0].get("url") is None
    assert Path(result_images[0]["local_path"]).read_bytes() == b"annotated"
    assert "outer-edge-geometry-v2" in Path(result_images[0]["local_path"]).name
    assert "full-scene-magenta-v1" in Path(result_images[0]["local_path"]).name
    assert result_payload is not payload
    assert result_payload["photo_auth_edge_candidates"][0]["image_id"] == "i1"
    assert result_payload["photo_auth_edge_candidates"][0]["diagnostic_image_id"] == "edge_candidate__i1"
    assert result_payload["photo_auth_edge_candidates"][0]["diagnostic_image_position"] == 1


def test_photo_auth_edge_mapping_blocks_unconfirmed_edge_evidence(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTH_EDGE_MAPPING_MODE", "on")
    compliance = {
        "photo_authenticity_by_image": [{
            **_auth_observation("i1"),
            "edges": {
                "top": "scene_continues", "right": "scene_continues",
                "bottom": "carrier_boundary", "left": "scene_continues",
            },
            "strong_evidence": [{"code": "EXTERNAL_PHOTO_CARRIER", "regions": ["image_edge"]}],
            "weak_evidence": [{"code": "EDGE_CUTOFF", "regions": ["image_edge"]}],
        }],
        "photo_auth_edge_candidate_reviews": [],
    }
    candidates = [{"candidate_id": "i1:bottom", "image_id": "i1", "side": "bottom"}]

    result = v2.apply_photo_auth_edge_candidate_reviews(compliance, candidates)

    observation = result["photo_authenticity_by_image"][0]
    assert observation["edges"]["bottom"] == "scene_continues"
    assert observation["strong_evidence"] == []
    assert observation["weak_evidence"] == []


def test_photo_auth_edge_mapping_blocks_edge_evidence_when_review_field_is_missing(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTH_EDGE_MAPPING_MODE", "on")
    compliance = {
        "photo_authenticity_by_image": [{
            **_auth_observation("i1"),
            "edges": {
                "top": "scene_continues", "right": "scene_continues",
                "bottom": "carrier_boundary", "left": "scene_continues",
            },
            "strong_evidence": [{"code": "EXTERNAL_PHOTO_CARRIER", "regions": ["image_edge"]}],
        }],
    }

    result = v2.apply_photo_auth_edge_candidate_reviews(
        compliance,
        [{"candidate_id": "i1:bottom", "image_id": "i1", "side": "bottom"}],
    )

    observation = result["photo_authenticity_by_image"][0]
    assert observation["edges"]["bottom"] == "scene_continues"
    assert observation["strong_evidence"] == []


def test_photo_auth_edge_mapping_preserves_non_edge_model_evidence(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTH_EDGE_MAPPING_MODE", "on")
    carrier = {"code": "EXTERNAL_PHOTO_CARRIER", "regions": ["background"]}
    compliance = {
        "photo_authenticity_by_image": [{
            **_auth_observation("i1"),
            "edges": {
                "top": "scene_continues", "right": "scene_continues",
                "bottom": "carrier_boundary", "left": "scene_continues",
            },
            "strong_evidence": [carrier],
        }],
        "photo_auth_edge_candidate_reviews": [],
    }
    candidates = [{"candidate_id": "i1:bottom", "image_id": "i1", "side": "bottom"}]

    result = v2.apply_photo_auth_edge_candidate_reviews(compliance, candidates)

    observation = result["photo_authenticity_by_image"][0]
    assert observation["strong_evidence"] == [carrier]


def test_photo_auth_edge_mapping_rejects_mismatched_diagnostic_image(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTH_EDGE_MAPPING_MODE", "on")
    compliance = {
        "photo_authenticity_by_image": [_auth_observation("i1")],
        "photo_auth_edge_candidate_reviews": [{
            "candidate_id": "i1:bottom", "image_id": "i1",
            "diagnostic_image_id": "edge_candidate__other", "side": "bottom",
            "classification": "external_screen", "confirmed_external_screen": True,
            "supporting_features": ["screen_frame"],
        }],
    }
    candidates = [{
        "candidate_id": "i1:bottom", "image_id": "i1",
        "diagnostic_image_id": "edge_candidate__i1", "side": "bottom",
    }]

    result = v2.apply_photo_auth_edge_candidate_reviews(compliance, candidates)

    assert result["photo_authenticity_by_image"][0] == _auth_observation("i1")


def test_cache_key_changes_when_local_image_content_changes(tmp_path):
    image_path = tmp_path / "i1.jpg"
    image_path.write_bytes(b"first")
    image = [{"image_id": "i1", "local_path": str(image_path)}]

    first = v2._cache_key("model", "stage", "prompt", {}, image)
    image_path.write_bytes(b"second")
    second = v2._cache_key("model", "stage", "prompt", {}, image)

    assert first != second


def test_atomic_json_writer_never_leaves_partial_cache_file(tmp_path):
    path = tmp_path / "cache.json"

    v2._write_json_atomically(path, {"ok": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}


def test_photo_auth_edge_mapping_failure_preserves_original_image(monkeypatch, tmp_path):
    source = tmp_path / "i1.jpg"
    source.write_bytes(b"image")
    image = {"image_id": "i1", "local_path": str(source), "source_url": "https://example/i1.jpg"}
    payload = {"id": "order-1"}
    monkeypatch.setattr(v2, "scan_photo_auth_edge_candidates", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("scan failed")))

    result_images, result_payload = v2.prepare_photo_auth_edge_mapping_inputs(
        [image], payload, mode="on", output_dir=tmp_path
    )

    assert result_images[0] == image
    assert "photo_auth_edge_candidates" not in result_payload


def test_photo_auth_edge_mapping_requires_local_and_model_physical_confirmation(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTH_EDGE_MAPPING_MODE", "on")
    compliance = {
        "photo_authenticity_by_image": [_auth_observation("i1")],
        "photo_auth_edge_candidate_reviews": [{
            "candidate_id": "i1:bottom",
            "image_id": "i1",
            "diagnostic_image_id": "edge_candidate__i1",
            "side": "bottom",
            "classification": "external_screen",
            "confirmed_external_screen": True,
            "supporting_features": ["screen_frame"],
            "reason": "marked band is the frame of another display",
        }],
    }
    candidates = [{
        "candidate_id": "i1:bottom", "image_id": "i1",
        "diagnostic_image_id": "edge_candidate__i1", "side": "bottom",
    }]

    result = v2.apply_photo_auth_edge_candidate_reviews(compliance, candidates)

    observation = result["photo_authenticity_by_image"][0]
    assert observation["edges"]["bottom"] == "carrier_boundary"
    assert observation["screen_owner"] == "external_screen"
    assert {item["code"] for item in observation["strong_evidence"]} == {"EXTERNAL_PHOTO_CARRIER"}


def test_photo_auth_edge_mapping_maps_only_the_confirmed_candidate_side(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTH_EDGE_MAPPING_MODE", "on")
    compliance = {
        "photo_authenticity_by_image": [_auth_observation("i1")],
        "photo_auth_edge_candidate_reviews": [{
            "candidate_id": "i1:bottom", "image_id": "i1",
            "diagnostic_image_id": "edge_candidate__i1", "side": "bottom",
            "classification": "external_screen", "confirmed_external_screen": True,
            "supporting_features": ["display_boundary"], "reason": "confirmed bottom frame",
        }],
    }
    candidates = [
        {"candidate_id": "i1:bottom", "image_id": "i1", "side": "bottom"},
        {"candidate_id": "i1:right", "image_id": "i1", "side": "right"},
    ]

    result = v2.apply_photo_auth_edge_candidate_reviews(compliance, candidates)

    edges = result["photo_authenticity_by_image"][0]["edges"]
    assert edges["bottom"] == "carrier_boundary"
    assert edges["right"] == "scene_continues"


@pytest.mark.parametrize(
    "review",
    [
        {
            "candidate_id": "i1:bottom", "image_id": "i1", "side": "bottom", "classification": "uncertain",
            "confirmed_external_screen": False, "supporting_features": ["screen_frame"],
        },
        {
            "candidate_id": "i1:bottom", "image_id": "i1", "side": "bottom", "classification": "external_screen",
            "confirmed_external_screen": True, "supporting_features": ["pixel_texture"],
        },
        {
            "candidate_id": "i1:bottom", "image_id": "i1", "side": "bottom", "classification": "external_screen",
            "confirmed_external_screen": True, "supporting_features": ["viewer_ui"],
        },
        {
            "candidate_id": "i1:bottom", "image_id": "i1", "side": "right", "classification": "external_screen",
            "confirmed_external_screen": True, "supporting_features": ["screen_frame"],
        },
        {
            "candidate_id": "i1:bottom", "image_id": "i1", "side": "bottom", "classification": "clothing_or_scene",
            "confirmed_external_screen": False, "supporting_features": [],
        },
    ],
)
def test_photo_auth_edge_mapping_does_not_promote_uncertain_texture_or_scene(monkeypatch, review):
    monkeypatch.setenv("PHOTO_AUTH_EDGE_MAPPING_MODE", "on")
    compliance = {
        "photo_authenticity_by_image": [_auth_observation("i1")],
        "photo_auth_edge_candidate_reviews": [review],
    }

    result = v2.apply_photo_auth_edge_candidate_reviews(
        compliance, [{"candidate_id": "i1:bottom", "image_id": "i1", "side": "bottom"}],
    )

    assert result is compliance
    assert result["photo_authenticity_by_image"][0] == _auth_observation("i1")


def test_photo_auth_edge_mapping_review_is_ignored_when_plugin_is_off(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTH_EDGE_MAPPING_MODE", "off")
    compliance = {
        "photo_authenticity_by_image": [_auth_observation("i1")],
        "photo_auth_edge_candidate_reviews": [{
            "candidate_id": "i1:bottom", "image_id": "i1", "side": "bottom", "classification": "external_screen",
            "confirmed_external_screen": True, "supporting_features": ["screen_frame"],
        }],
    }

    result = v2.apply_photo_auth_edge_candidate_reviews(
        compliance, [{"candidate_id": "i1:bottom", "image_id": "i1", "side": "bottom"}],
    )

    assert result is compliance


def test_photo_auth_edge_mapping_drops_only_its_diagnostic_observation(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTH_EDGE_MAPPING_MODE", "on")
    original = _auth_observation("i1")
    diagnostic = _auth_observation("edge_candidate__i1")
    compliance = {
        "photo_authenticity_by_image": [original, diagnostic],
        "photo_auth_edge_candidate_reviews": [],
    }
    candidates = [{
        "candidate_id": "i1:bottom",
        "image_id": "i1",
        "diagnostic_image_id": "edge_candidate__i1",
        "side": "bottom",
    }]

    result = v2.apply_photo_auth_edge_candidate_reviews(compliance, candidates)

    assert result is not compliance
    assert result["photo_authenticity_by_image"] == [original]


def test_photo_auth_edge_mapping_cache_validation_uses_original_image_ids_only():
    prompt = v2.compliance_prompt_for_category(
        "home_appliance",
        include_photo_authenticity=True,
        photo_auth_edge_mapping_mode="on",
    )
    images = [
        {"image_id": "i1"},
        {"image_id": "edge_candidate__i1"},
    ]

    assert v2._is_cacheable_model_result(
        "hybrid_compliance",
        prompt,
        {"photo_authenticity_by_image": [_auth_observation("i1")]},
        images,
    ) is True
    assert v2._is_cacheable_model_result(
        "hybrid_compliance",
        prompt,
        {"photo_authenticity_by_image": [_auth_observation("edge_candidate__i1")]},
        images,
    ) is False


def test_photo_auth_edge_mapping_cli_defaults_off_supports_env_and_rejects_invalid(monkeypatch):
    monkeypatch.delenv("PHOTO_AUTH_EDGE_MAPPING_MODE", raising=False)
    default_args = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert default_args.photo_auth_edge_mapping_mode == "off"

    monkeypatch.setenv("PHOTO_AUTH_EDGE_MAPPING_MODE", "on")
    env_args = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert env_args.photo_auth_edge_mapping_mode == "on"

    explicit_off = v2.parse_cli_args([
        "--tasks-dir", "tasks", "--out-dir", "out",
        "--photo-auth-edge-mapping-mode", "off",
    ])
    assert explicit_off.photo_auth_edge_mapping_mode == "off"

    with pytest.raises(SystemExit):
        v2.parse_cli_args([
            "--tasks-dir", "tasks", "--out-dir", "out",
            "--photo-auth-edge-mapping-mode", "invalid",
        ])

    with pytest.raises(SystemExit):
        v2.parse_cli_args([
            "--tasks-dir", "tasks", "--out-dir", "out",
            "--photo-auth-edge-mapping-mode", "on",
            "--photo-authenticity-mode", "off",
        ])

    with pytest.raises(SystemExit):
        v2.parse_cli_args([
            "--tasks-dir", "tasks", "--out-dir", "out",
            "--mode", "v2",
            "--photo-auth-edge-mapping-mode", "on",
        ])


def test_photo_authenticity_report_records_effective_sn_label_plugin_state():
    disabled = v2.finalize_photo_authenticity_report_fields(
        {},
        v2.PhotoAuthenticityConfig.from_env({
            "PHOTO_AUTHENTICITY_MODE": "off",
            "SN_LABEL_AUTH_REVIEW_MODE": "on",
        }),
    )
    enabled = v2.finalize_photo_authenticity_report_fields(
        {},
        v2.PhotoAuthenticityConfig.from_env({
            "PHOTO_AUTHENTICITY_MODE": "enforce",
            "SN_LABEL_AUTH_REVIEW_MODE": "on",
        }),
    )

    assert disabled["sn_label_auth_review_mode"] == "off"
    assert enabled["sn_label_auth_review_mode"] == "on"


def test_photo_authenticity_report_records_effective_local_tree_state():
    disabled = v2.finalize_photo_authenticity_report_fields(
        {},
        v2.PhotoAuthenticityConfig.from_env({
            "PHOTO_AUTHENTICITY_MODE": "off",
            "PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED": "true",
        }),
    )
    enabled = v2.finalize_photo_authenticity_report_fields(
        {},
        v2.PhotoAuthenticityConfig.from_env({
            "PHOTO_AUTHENTICITY_MODE": "enforce",
            "PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED": "true",
        }),
    )

    assert disabled["photo_authenticity_local_tree_enabled"] == "off"
    assert disabled["photo_authenticity_local_tree_artifact_sha256"] == ""
    assert enabled["photo_authenticity_local_tree_enabled"] == "on"
    assert enabled["photo_authenticity_local_tree_artifact_sha256"] == v2.EXPECTED_LOCAL_TREE_SHA256


def test_photo_authenticity_local_tree_startup_check_fails_fast_when_artifact_missing(tmp_path):
    config = v2.PhotoAuthenticityConfig.from_env({
        "PHOTO_AUTHENTICITY_MODE": "enforce",
        "PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED": "true",
        "PHOTO_AUTHENTICITY_LOCAL_TREE_ARTIFACT_PATH": str(tmp_path / "missing-tree.json"),
    })

    with pytest.raises(RuntimeError, match="local tree artifact unavailable"):
        v2.verify_photo_authenticity_local_tree_artifact(config)

    disabled = v2.PhotoAuthenticityConfig.from_env({
        "PHOTO_AUTHENTICITY_MODE": "enforce",
        "PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED": "false",
        "PHOTO_AUTHENTICITY_LOCAL_TREE_ARTIFACT_PATH": str(tmp_path / "missing-tree.json"),
    })
    v2.verify_photo_authenticity_local_tree_artifact(disabled)


def test_hybrid_uses_enabled_sn_label_auth_review_prompt_only_for_compliance(monkeypatch):
    monkeypatch.setenv("SN_LABEL_AUTH_REVIEW_MODE", "on")
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    monkeypatch.setenv("DIGITAL_ACTIVATION_EVIDENCE_MODE", "off")
    prompts = {}

    def fake_call(_base, _key, _model, prompt, _payload, _images, *, stage, **_kwargs):
        prompts[stage] = prompt
        if stage == "hybrid_sn":
            return ({"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}, "sn", 0.1, {}, False)
        compliance = _screen_sn_compliance_pass()
        compliance["photo_authenticity_by_image"] = [
            _auth_observation("img_001"),
            _auth_observation("img_002"),
            _auth_observation("img_003"),
        ]
        return (compliance, "compliance", 0.1, {}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    monkeypatch.setattr(v2, "apply_photo_authenticity_gate", lambda **kwargs: kwargs["legacy_row"])

    result = audit_task_hybrid("https://unused", "key", "qwen3.7-plus", _base_task())

    assert "SN/条码标签非实拍专项审查插件" not in prompts["hybrid_sn"]
    assert prompts["hybrid_compliance"].endswith(v2.read_sn_label_auth_review_prompt())
    assert result["sn_label_auth_review_mode"] == "on"


def test_photo_authenticity_summary_counts_routes_and_resources():
    rows = [
        {
            "photo_authenticity_mode": "shadow", "photo_authenticity_would_manual": True,
            "photo_authenticity_strong_count": 1, "photo_authenticity_manual_count": 2,
            "photo_authenticity_fft_count": 1, "photo_authenticity_service_failure": False,
            "photo_authenticity_fallback_calls": 1, "photo_authenticity_elapsed_sec": 2.5,
            "photo_authenticity_tokens": 123,
        },
        {
            "photo_authenticity_mode": "shadow", "photo_authenticity_would_manual": False,
            "photo_authenticity_strong_count": 0, "photo_authenticity_manual_count": 0,
            "photo_authenticity_fft_count": 0, "photo_authenticity_service_failure": True,
            "photo_authenticity_fallback_calls": 0, "photo_authenticity_elapsed_sec": 1.5,
            "photo_authenticity_tokens": 7,
        },
    ]
    assert v2.summarize_photo_authenticity(rows) == {
        "mode_counts": {"shadow": 2}, "would_manual_orders": 1, "strong_images": 1,
        "manual_images": 2, "fft_images": 1, "failure_orders": 1, "fallback_calls": 1,
        "local_tree_hit_images": 0, "local_tree_unavailable_images": 0,
        "latency_sec": 4.0, "tokens": 130,
        "merged_compliance_total_tokens": 0, "merged_compliance_total_elapsed_sec": 0,
        "postprocess_tokens": 0, "postprocess_elapsed_sec": 0,
        "available_incremental_tokens": 0, "available_incremental_elapsed_sec": 0,
        "baseline_coverage": {"orders_with_baseline": 0, "total_orders": 2, "rate": 0.0},
    }


@pytest.mark.parametrize("configured_mode", ["shadow", "enforce"])
def test_photo_authenticity_mode_is_preserved_on_precheck_early_return(monkeypatch, configured_mode):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", configured_mode)
    monkeypatch.setattr(v2, "precheck_task", lambda _task: {
        "manual_required": True, "manual_reason_codes": ["IMAGE_MISSING"],
        "manual_reason": "missing", "address_ok": True,
    })
    result = audit_task_hybrid("https://unused", "key", "model", _base_task())
    assert result["manual_flag"] == "是"
    assert result["photo_authenticity_mode"] == configured_mode
    assert result["photo_authenticity_would_manual"] is False
    assert result["photo_authenticity_image_results"] == ""


@pytest.mark.parametrize("configured_mode", ["shadow", "enforce"])
def test_photo_authenticity_mode_is_preserved_on_sn_early_return(monkeypatch, configured_mode):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", configured_mode)

    def fake_call(*_args, **_kwargs):
        return ({"sn_match": False, "observed_sn": "WRONG", "confidence": 0.99}, "{}", 1.0, {"total_tokens": 5}, False)

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    result = audit_task_hybrid(
        "https://unused", "key", "model", _base_task(), allow_review=False, allow_targeted_review=False,
    )
    assert result["manual_flag"] == "是"
    assert result["photo_authenticity_mode"] == configured_mode
    assert result["photo_authenticity_tokens"] == 0


@pytest.mark.parametrize("configured_mode", ["shadow", "enforce"])
def test_photo_authenticity_mode_is_preserved_when_legacy_compliance_is_already_manual(monkeypatch, configured_mode):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", configured_mode)

    def fake_call(*_args, stage, **_kwargs):
        if stage == "hybrid_sn":
            parsed = {"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99}
        else:
            parsed = _screen_sn_compliance_pass()
            parsed.update({
                "manual_required": True, "manual_reason_codes": ["PRODUCT_PHOTO_INVALID"],
                "manual_reason": "legacy manual", "photo_authenticity_by_image": [],
            })
        return parsed, "{}", 1.0, {"total_tokens": 5}, False

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)
    result = audit_task_hybrid(
        "https://unused", "key", "model", _base_task(), allow_review=False, allow_targeted_review=False,
    )
    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "PRODUCT_PHOTO_INVALID"
    assert result["photo_authenticity_mode"] == configured_mode
    assert result["photo_authenticity_would_manual"] is False
    assert result["photo_authenticity_tokens"] == 0
    assert result["merged_compliance_total_tokens"] == 5
    assert result["photo_authenticity_incremental_tokens"] == ""
    assert v2.summarize_photo_authenticity([result])["mode_counts"] == {configured_mode: 1}


@pytest.mark.parametrize("configured_mode", ["shadow", "enforce"])
def test_audit_task_path_exception_preserves_photo_authenticity_mode(
    monkeypatch, tmp_path, configured_mode,
):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", configured_mode)
    monkeypatch.setattr(v2, "audit_task_hybrid", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad model json")))
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(_base_task(), ensure_ascii=False), encoding="utf-8")

    _, payload = v2.audit_task_path(
        1, 1, task_path, base_url="https://unused", api_key="key", model="model",
        mode="hybrid", cache_dir=tmp_path / "cache", allow_review=False,
        allow_targeted_review=False,
    )

    result = payload["result"]
    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert result["photo_authenticity_mode"] == configured_mode
    assert result["photo_authenticity_would_manual"] is False
    assert v2.summarize_photo_authenticity([result])["mode_counts"] == {configured_mode: 1}


@pytest.mark.parametrize("legacy_mode", ["fast", "v2", "sn_only"])
def test_audit_task_path_legacy_mode_exception_still_reports_authenticity_off(
    monkeypatch, tmp_path, legacy_mode,
):
    monkeypatch.delenv("PHOTO_AUTHENTICITY_MODE", raising=False)
    target = "audit_task_sn_only" if legacy_mode == "sn_only" else f"audit_task_{legacy_mode}"
    monkeypatch.setattr(v2, target, lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad model json")))
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(_base_task(), ensure_ascii=False), encoding="utf-8")

    _, payload = v2.audit_task_path(
        1, 1, task_path, base_url="https://unused", api_key="key", model="model",
        mode=legacy_mode, cache_dir=tmp_path / "cache", allow_review=False,
        allow_targeted_review=False,
    )

    assert payload["result"]["photo_authenticity_mode"] == "off"


def test_photo_authenticity_cost_fields_do_not_treat_merged_usage_as_incremental_without_baseline():
    row = {
        "photo_authenticity_mode": "shadow",
        "merged_compliance_total_tokens": 900,
        "merged_compliance_total_elapsed_sec": 8.0,
        "photo_authenticity_postprocess_tokens": 25,
        "photo_authenticity_postprocess_elapsed_sec": 1.5,
    }
    v2.prepare_photo_authenticity_report_fields(row)
    assert row["photo_authenticity_tokens"] == 25
    assert row["photo_authenticity_elapsed_sec"] == 1.5
    assert row["baseline_compliance_tokens"] == ""
    assert row["photo_authenticity_incremental_tokens"] == ""
    assert row["photo_authenticity_incremental_available"] is False


def test_photo_authenticity_cost_fields_calculate_increment_only_with_baseline():
    row = {
        "photo_authenticity_mode": "shadow",
        "merged_compliance_total_tokens": 900,
        "merged_compliance_total_elapsed_sec": 8.0,
        "photo_authenticity_postprocess_tokens": 25,
        "photo_authenticity_postprocess_elapsed_sec": 1.5,
        "baseline_compliance_tokens": 700,
        "baseline_compliance_elapsed_sec": 6.0,
    }
    v2.prepare_photo_authenticity_report_fields(row)
    assert row["photo_authenticity_incremental_tokens"] == 225
    assert row["photo_authenticity_incremental_elapsed_sec"] == 3.5
    assert row["photo_authenticity_incremental_available"] is True


def test_optional_compliance_baseline_is_injected_by_order_id(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"order-1": {"tokens": 700, "elapsed_sec": 6.0}}), encoding="utf-8",
    )
    row = {"id": "order-1"}
    v2.apply_optional_compliance_baseline(
        row, {"PHOTO_AUTHENTICITY_BASELINE_PATH": str(baseline_path)},
    )
    assert row["baseline_compliance_tokens"] == 700
    assert row["baseline_compliance_elapsed_sec"] == 6.0


def test_photo_authenticity_summary_separates_merged_postprocess_and_available_delta():
    rows = [
        {
            "photo_authenticity_mode": "shadow", "merged_compliance_total_tokens": 900,
            "merged_compliance_total_elapsed_sec": 8.0, "photo_authenticity_postprocess_tokens": 25,
            "photo_authenticity_postprocess_elapsed_sec": 1.5, "baseline_compliance_tokens": 700,
            "baseline_compliance_elapsed_sec": 6.0, "photo_authenticity_incremental_tokens": 225,
            "photo_authenticity_incremental_elapsed_sec": 3.5,
            "photo_authenticity_incremental_available": True,
        },
        {
            "photo_authenticity_mode": "shadow", "merged_compliance_total_tokens": 800,
            "merged_compliance_total_elapsed_sec": 7.0, "photo_authenticity_postprocess_tokens": 0,
            "photo_authenticity_postprocess_elapsed_sec": 0.2,
            "photo_authenticity_incremental_available": False,
        },
    ]
    summary = v2.summarize_photo_authenticity(rows)
    assert summary["merged_compliance_total_tokens"] == 1700
    assert summary["merged_compliance_total_elapsed_sec"] == 15.0
    assert summary["postprocess_tokens"] == 25
    assert summary["postprocess_elapsed_sec"] == 1.7
    assert summary["available_incremental_tokens"] == 225
    assert summary["available_incremental_elapsed_sec"] == 3.5
    assert summary["baseline_coverage"] == {"orders_with_baseline": 1, "total_orders": 2, "rate": 0.5}


def _activation_identity_observation(
    image_id="img_003",
    *,
    field_type="SN",
    raw_value="ABC123",
    readable=True,
    complete=True,
):
    return {
        "image_id": image_id,
        "identity_fields": [{
            "field_type": field_type,
            "raw_value": raw_value,
            "readable": readable,
            "complete": complete,
        }],
    }


def _digital_activation_decision(product_type, observations):
    return {
        "digital_activation_evidence_mode": "on",
        "effective_category": "ordinary_3c",
        "product_type": product_type,
        "_activation_image_ids": ["img_003"],
        "activation_identity_by_image": observations,
        "activation_evidence_type": "SCREEN_ACTIVE_WITH_SN",
        "activation_photo_ok": True,
    }


def test_digital_activation_prompt_delegates_authenticity_and_uses_minimal_identity_schema():
    prompt = v2.read_digital_activation_evidence_prompt()

    assert '"activation_identity_by_image"' in prompt
    assert "照片真实性" not in prompt
    assert "EXTERNAL_DISPLAY_OR_PHOTO" not in prompt
    assert "screen_source" not in prompt
    assert "screen_on" not in prompt
    assert "page_type" not in prompt
    assert "只有IMEI1或只有IMEI2也属于有效激活信息" in prompt
    assert "平板、手表、手环仅以SN或SERIAL_NUMBER为有效" in prompt


def test_digital_activation_gate_accepts_minimal_identity_schema_without_screen_metadata():
    decision = _digital_activation_decision(
        "智能手机",
        [_activation_identity_observation(field_type="IMEI1", raw_value="867530900000001")],
    )

    assert v2._verified_sn_activation_form_reason(decision) == ""


def test_tablet_rejects_imei_but_accepts_serial_number():
    imei = _digital_activation_decision(
        "平板电脑",
        [_activation_identity_observation(field_type="IMEI1", raw_value="867530900000001")],
    )
    serial = _digital_activation_decision(
        "平板电脑",
        [_activation_identity_observation(field_type="SERIAL_NUMBER")],
    )

    assert v2._verified_sn_activation_form_reason(imei) == "ACTIVATION_PHOTO_INVALID"
    assert v2._verified_sn_activation_form_reason(serial) == ""


def test_digital_activation_prompt_plugin_is_independent_and_ordinary_3c_only():
    ordinary_off = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="智能手机",
        digital_activation_evidence_mode="off",
    )
    ordinary_on = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="智能手机",
        digital_activation_evidence_mode="on",
    )

    assert ordinary_on.endswith("\n\n" + v2.read_digital_activation_evidence_prompt())
    assert "普通3C激活证据统一口径插件" in ordinary_on
    assert "智能手表/手环的配对页、设备名称、开机标志、二维码不属于 SN/IMEI/序列号身份信息" not in ordinary_on
    for category in ("home_appliance", "computer", "unknown"):
        assert v2.compliance_prompt_for_category(
            category, digital_activation_evidence_mode="on",
        ) == v2.compliance_prompt_for_category(
            category, digital_activation_evidence_mode="off",
        )

    glasses_on = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="智能眼镜",
        digital_activation_evidence_mode="on",
    )
    glasses_off = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="智能眼镜",
        digital_activation_evidence_mode="off",
    )
    assert glasses_on == glasses_off
    assert "activation_identity_by_image" not in glasses_on

    headphone_on = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="HEADPHONE",
        digital_activation_evidence_mode="on",
    )
    headphone_off = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="HEADPHONE",
        digital_activation_evidence_mode="off",
    )
    assert headphone_on == headphone_off


@pytest.mark.parametrize("field_type", ["SN", "SERIAL_NUMBER", "IMEI1", "IMEI2"])
def test_phone_accepts_any_complete_identity_field(field_type):
    decision = _digital_activation_decision(
        "智能手机", [_activation_identity_observation(field_type=field_type)],
    )

    assert v2._verified_sn_activation_form_reason(decision) == ""


def test_watch_accepts_serial_but_rejects_imei():
    serial = _digital_activation_decision(
        "智能手表", [_activation_identity_observation(field_type="SERIAL_NUMBER")],
    )
    imei = _digital_activation_decision(
        "智能手表", [_activation_identity_observation(field_type="IMEI1")],
    )

    assert v2._verified_sn_activation_form_reason(serial) == ""
    assert v2._verified_sn_activation_form_reason(imei) == "ACTIVATION_PHOTO_INVALID"


def test_watch_does_not_inherit_phone_imei_rule_from_product_description():
    decision = _digital_activation_decision(
        "智能手表",
        [_activation_identity_observation(field_type="IMEI1", raw_value="867530900000001")],
    )
    decision["goods_name"] = "智能手表，支持手机通话"

    assert v2._verified_sn_activation_form_reason(decision) == "ACTIVATION_PHOTO_INVALID"


@pytest.mark.parametrize(
    "observation, expected",
    [
        (_activation_identity_observation(field_type="OTHER"), "ACTIVATION_PHOTO_INVALID"),
        (_activation_identity_observation(readable=False), "ACTIVATION_PHOTO_INVALID"),
        (_activation_identity_observation(complete=False), "ACTIVATION_PHOTO_INVALID"),
        (_activation_identity_observation(image_id="img_other"), "MODEL_UNCERTAIN"),
    ],
)
def test_digital_activation_gate_fails_closed_without_same_image_valid_evidence(observation, expected):
    decision = _digital_activation_decision("平板电脑", [observation])

    assert v2._verified_sn_activation_form_reason(decision) == expected


def test_digital_activation_gate_rejects_missing_structured_output():
    decision = _digital_activation_decision("智能手机", [])

    assert v2._verified_sn_activation_form_reason(decision) == "MODEL_UNCERTAIN"


@pytest.mark.parametrize(
    "observations",
    [
        [_activation_identity_observation("img_003")],
        [
            _activation_identity_observation("img_003"),
            _activation_identity_observation("img_003"),
        ],
        [
            _activation_identity_observation("img_003"),
            _activation_identity_observation("img_extra"),
        ],
    ],
)
def test_digital_activation_gate_requires_exact_unique_image_coverage(observations):
    decision = _digital_activation_decision("智能手机", observations)
    decision["_activation_image_ids"] = ["img_003", "img_004"]

    assert v2._verified_sn_activation_form_reason(decision) == "MODEL_UNCERTAIN"


def test_digital_activation_mode_off_preserves_legacy_gate():
    decision = _digital_activation_decision("智能手机", [])
    decision["digital_activation_evidence_mode"] = "off"

    assert v2._verified_sn_activation_form_reason(decision) == ""


def test_digital_activation_gate_does_not_capture_headphones_by_substring():
    decision = _digital_activation_decision("HEADPHONE", [])

    assert v2._verified_sn_activation_form_reason(decision) == ""


def test_structured_activation_pass_overrides_legacy_activation_failure_fields():
    decision = _digital_activation_decision(
        "智能手机", [_activation_identity_observation(field_type="IMEI1")],
    )
    decision.update({
        "_sn_already_verified_by_system": True,
        "manual_required": True,
        "manual_reason_codes": ["ACTIVATION_PHOTO_INVALID"],
        "manual_reason": "旧字段误判激活照片无效",
        "activation_photo_ok": False,
        "product_type_match": True,
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "duplicate_image_evidence": False,
        "_exact_duplicate_image_groups": [],
        "confidence": 0.99,
    })

    result = v2.enforce_photo_noncompliance_manual(decision)

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_dedicated_authenticity_risk_overrides_activation_identity_pass():
    decision = _digital_activation_decision(
        "智能手机",
        [_activation_identity_observation(field_type="IMEI1")],
    )
    decision.update({
        "_sn_already_verified_by_system": True,
        "manual_required": True,
        "manual_reason_codes": ["ACTIVATION_PHOTO_INVALID"],
        "manual_reason": "旧字段只报激活照片无效",
        "activation_photo_ok": False,
        "image_risk": True,
        "product_type_match": True,
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "duplicate_image_evidence": False,
        "_exact_duplicate_image_groups": [],
        "confidence": 0.99,
    })

    result = v2.enforce_photo_noncompliance_manual(decision)

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["IMAGE_STRONG_RISK"]


def _image_group(image_id, path):
    return {"image_id": image_id, "local_path": str(path), "source_url": f"https://unused/{image_id}.jpg"}


def test_exact_duplicate_groups_require_three_identical_files(tmp_path):
    same_a = tmp_path / "a.jpg"
    same_b = tmp_path / "b.jpg"
    different = tmp_path / "c.jpg"
    same_a.write_bytes(b"same-image")
    same_b.write_bytes(b"same-image")
    different.write_bytes(b"different-image")
    groups = {
        "product": [_image_group("img_001", same_a)],
        "unboxing": [_image_group("img_002", different)],
        "activation": [_image_group("img_003", same_b)],
    }

    assert v2.exact_duplicate_image_groups(groups) == []


def test_four_images_with_three_identical_files_are_duplicate(tmp_path):
    paths = []
    for index, content in enumerate((b"same", b"same", b"different", b"same"), start=1):
        path = tmp_path / f"{index}.jpg"
        path.write_bytes(content)
        paths.append(path)
    groups = {
        "product": [_image_group("img_001", paths[0]), _image_group("img_002", paths[1])],
        "unboxing": [_image_group("img_003", paths[2])],
        "activation": [_image_group("img_004", paths[3])],
    }

    assert v2.exact_duplicate_image_groups(groups) == [["img_001", "img_002", "img_004"]]


def test_model_duplicate_claim_cannot_block_without_local_exact_group():
    decision = {
        "_sn_already_verified_by_system": True,
        "digital_activation_evidence_mode": "off",
        "manual_required": True,
        "manual_reason_codes": ["DUPLICATE_IMAGE_EVIDENCE"],
        "manual_reason": "三张证据位图片完全重复",
        "duplicate_image_evidence": True,
        "_exact_duplicate_image_groups": [],
        "effective_category": "ordinary_3c",
        "product_type": "智能手机",
        "product_type_match": True,
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "activation_photo_ok": True,
        "activation_evidence_type": "SCREEN_SN",
        "confidence": 0.99,
    }

    result = v2.enforce_photo_noncompliance_manual(decision)

    assert result["manual_required"] is False
    assert result["manual_reason_codes"] == []


def test_local_three_image_group_blocks_even_when_model_does_not_claim_duplicate():
    decision = {
        "_sn_already_verified_by_system": True,
        "digital_activation_evidence_mode": "off",
        "manual_required": False,
        "manual_reason_codes": [],
        "manual_reason": "",
        "duplicate_image_evidence": False,
        "_exact_duplicate_image_groups": [["img_001", "img_002", "img_003"]],
        "effective_category": "ordinary_3c",
        "product_type": "智能手机",
        "product_type_match": True,
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "activation_photo_ok": True,
        "activation_evidence_type": "SCREEN_SN",
        "confidence": 0.99,
    }

    result = v2.enforce_photo_noncompliance_manual(decision)

    assert result["manual_required"] is True
    assert result["manual_reason_codes"] == ["DUPLICATE_IMAGE_EVIDENCE"]
