# -*- coding: utf-8 -*-
import hashlib
import json

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
    original = {category: v2.compliance_prompt_for_category(category) for category in categories}

    for category in categories:
        assert v2.compliance_prompt_for_category(category, include_photo_authenticity=False) == original[category]
        merged = v2.compliance_prompt_for_category(category, include_photo_authenticity=True)
        assert merged == original[category] + v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM
        assert merged.count(v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM) == 1
        assert merged.count('"photo_authenticity_by_image"') == 1


def test_legacy_compliance_prompts_match_frozen_pre_task2_sha256_baselines():
    # Baselines were frozen from the existing working-tree prompts when Task 2
    # first brought this previously-untracked mainline file under version control.
    # There is no earlier Git blob from which to reconstruct a historical diff.
    expected = {
        "home_appliance": "89be177453d4b9c4efafa9927d21255cd913c67b6cdeaf98835a9b9f16f3f572",
        "computer": "614a07fc1ec229d97958952ff2015864270eecc770d5f6da4d8ed078c626c772",
        "ordinary_3c": "8d9e55cae771522f91ec29aca9d8d06a4c3b639de5f03b3e9c2959132d8ebe71",
        "unknown": "578f0ade0ec8cfbd642b5467041e612e8683de08da5c4419656ed9db69f26315",
    }

    actual = {
        category: hashlib.sha256(
            v2.compliance_prompt_for_category(category, include_photo_authenticity=False).encode("utf-8")
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


def test_hybrid_enforce_routes_structured_weak_evidence_manual_even_when_reason_says_real(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")

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
    assert result["photo_authenticity_manual_count"] == 1
    assert result["photo_authenticity_fft_count"] == 0


def test_hybrid_enforce_exempts_only_product_screen_local_moire(monkeypatch):
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")

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
    assert calls[1] == ("hybrid_compliance", v2.compliance_prompt_for_category("ordinary_3c"))
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
            {"title": "鍟嗗搧鐓х墖", "source_url": "a"},
            {"title": "鎷嗗皝鐓х墖", "source_url": "b"},
            {"title": "SN photo", "source_url": "c"},
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

    monkeypatch.setattr(v2, "_wait_before_model_request", lambda: None)
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

    monkeypatch.setattr(v2, "_wait_before_model_request", lambda: None)
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

    monkeypatch.setattr(v2, "_wait_before_model_request", lambda: None)
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


def test_model_request_buffer_waits_until_three_seconds_after_previous_request(monkeypatch):
    sleeps = []
    moments = iter([101.0, 103.0])

    monkeypatch.setattr(v2.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(v2.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(v2, "_last_model_request_at", 100.0)

    v2._wait_before_model_request()

    assert sleeps == [2.0]
    assert v2._last_model_request_at == 103.0


def test_chat_completion_retries_connect_failure_once_with_five_second_connect_timeout(monkeypatch):
    calls = []

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
                raise TimeoutError("connect timed out")

        def getresponse(self):
            return FakeHTTPResponse()

        def close(self):
            return None

    monkeypatch.setattr(v2, "_wait_before_model_request", lambda: None)
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
    assert calls[1].sock.timeouts == [60]


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
            "ABC123",
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


def test_duplicate_cross_group_images_detected_by_same_url():
    groups = {
        "product": [{"source_url": "https://example.com/a.jpg", "local_path": "a.jpg"}],
        "unboxing": [{"source_url": "https://example.com/a.jpg", "local_path": "b.jpg"}],
        "SN photo": [{"source_url": "https://example.com/c.jpg", "local_path": "c.jpg"}],
    }

    assert has_duplicate_cross_group_images(groups)


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

    decision["manual_reason"] = "product, unboxing, and activation photos are all identical"
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
    assert v2.compliance_prompt_for_category("ordinary_3c") == v2.ORDINARY_3C_COMPLIANCE_PROMPT
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
            {"title": "鍟嗗搧鐓х墖", "source_url": "a"},
            {"title": "鎷嗗皝鐓х墖", "source_url": "b"},
            {"title": "SN photo", "source_url": "c"},
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
    assert timeouts[0] == ("hybrid_sn", 60)


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

    assert calls == [("hybrid_sn", 60)]

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
    assert calls[1][1] == v2.ORDINARY_3C_COMPLIANCE_PROMPT
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

def test_home_appliance_unboxing_rule_requires_visible_packaging():
    checklist = open("docs/guobu-collector-field-checklist.md", encoding="utf-8").read()

    assert "拆封照片必须出现可识别外箱或包装结构" in v2.HOME_APPLIANCE_COMPLIANCE_PROMPT
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
        }
    }
    v2.prepare_photo_authenticity_report_fields(row)
    assert row["photo_authenticity_image_results"] == (
        '{"a":{"result":"no_evidence","score":0.1},'
        '"b":{"result":"manual_review","score":0.999}}'
    )


def test_photo_authenticity_cli_defaults_enforce_and_allows_explicit_off_before_runtime(monkeypatch):
    monkeypatch.delenv("PHOTO_AUTHENTICITY_MODE", raising=False)
    args = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert args.photo_authenticity_mode == "enforce"
    assert args.photo_authenticity_artifact_dir is None
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    env_off = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert env_off.photo_authenticity_mode == "off"
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "enforce")
    explicit_off = v2.parse_cli_args([
        "--tasks-dir", "tasks", "--out-dir", "out",
        "--photo-authenticity-mode", "off",
    ])
    assert explicit_off.photo_authenticity_mode == "off"
    with pytest.raises(SystemExit):
        v2.parse_cli_args([
            "--tasks-dir", "tasks", "--out-dir", "out",
            "--photo-authenticity-mode", "invalid",
        ])


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
