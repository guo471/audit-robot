from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from tools import compliance_candidate_rules as candidate
from tools import run_guobu_model_audit_v2 as v2


def _candidate_phone_response() -> dict:
    return {
        "manual_reason_codes": [],
        "product_type_match": "match",
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "activation_photo_ok": True,
        "activation_evidence_type": "SCREEN_SN",
        "duplicate_image_evidence": False,
        "evidence_summary": "设备本体、拆封设备及亮屏身份页可见",
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
    }


def _legacy_phone_response() -> dict:
    return {
        "manual_required": False,
        "manual_reason_codes": [],
        "manual_reason": "",
        "product_type_match": "match",
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "package_visible": True,
        "activation_photo_ok": True,
        "activation_evidence_type": "SCREEN_SN",
        "activation_identity_by_image": _candidate_phone_response()[
            "activation_identity_by_image"
        ],
        "image_risk": False,
        "duplicate_image_evidence": False,
        "invoice_orange_warning": False,
        "confidence": 0.99,
    }


def _phone_task() -> dict:
    return {
        "channel_order_no": "SYNTHETIC-COMPLIANCE-001",
        "fields": {
            "product_type": "[B01] 手机",
            "system_sn": "ABC123",
            "address": "",
        },
        "image_groups": {
            "商品照片": [
                {"image_id": "img_001", "source_url": "https://example.invalid/1.jpg"}
            ],
            "拆封照片": [
                {"image_id": "img_002", "source_url": "https://example.invalid/2.jpg"}
            ],
            "SN码采集/激活照片": [
                {"image_id": "img_003", "source_url": "https://example.invalid/3.jpg"}
            ],
        },
    }


def test_compliance_ruleset_defaults_to_candidate_and_legacy_is_explicit(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_RULESET", raising=False)
    assert v2.resolve_compliance_ruleset() == "candidate"

    monkeypatch.setenv("COMPLIANCE_RULESET", "legacy")
    assert v2.resolve_compliance_ruleset() == "legacy"


def test_cli_defaults_candidate_and_accepts_explicit_legacy(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_RULESET", raising=False)
    default = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    legacy = v2.parse_cli_args(
        [
            "--tasks-dir",
            "tasks",
            "--out-dir",
            "out",
            "--compliance-ruleset",
            "legacy",
        ]
    )

    assert default.compliance_ruleset == "candidate"
    assert legacy.compliance_ruleset == "legacy"

    monkeypatch.setenv("COMPLIANCE_RULESET", " LEGACY ")
    env_legacy = v2.parse_cli_args(["--tasks-dir", "tasks", "--out-dir", "out"])
    assert env_legacy.compliance_ruleset == "legacy"


def test_batch_plan_defaults_candidate_and_records_legacy_rollback(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    wrapper = project_root / "tools" / "run_guobu_audit_batch.ps1"
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "synthetic.json").write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env.pop("COMPLIANCE_RULESET", None)

    def run_plan(*extra: str) -> dict:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(wrapper),
                "-ProjectRoot",
                str(project_root),
                "-PythonExe",
                sys.executable,
                "-TasksDir",
                str(tasks),
                "-RunName",
                "synthetic_compliance_plan",
                "-PlanOnly",
                *extra,
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=60,
            env=env,
        )
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)

    default = run_plan()
    legacy = run_plan("-ComplianceRuleset", "legacy")

    assert default["complianceRuleset"] == "candidate"
    assert default["runManifest"]["compliance_ruleset"] == "candidate"
    assert legacy["complianceRuleset"] == "legacy"
    assert legacy["runManifest"]["compliance_ruleset"] == "legacy"
    assert "tools/compliance_candidate_rules.py" in default["runManifest"][
        "runtime_sha256"
    ]
    assert "tools/compliance_candidate_rules.py" not in legacy["runManifest"][
        "runtime_sha256"
    ]


def test_batch_plan_uses_fixed_python_and_ignores_stale_local_tree_env(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    wrapper = project_root / "tools" / "run_guobu_audit_batch.ps1"
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "synthetic.json").write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env["PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED"] = "false"

    def run_plan(*extra: str) -> dict:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(wrapper),
                "-ProjectRoot",
                str(project_root),
                "-TasksDir",
                str(tasks),
                "-RunName",
                "synthetic_entry_gate_plan",
                "-PlanOnly",
                *extra,
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=60,
            env=env,
        )
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)

    default = run_plan()
    disabled = run_plan("-DisablePhotoAuthenticityLocalTree")

    assert Path(default["pythonPath"]) == Path(
        r"C:\Users\guoru\AppData\Local\Programs\Python\Python314\python.exe"
    )
    assert default["runManifest"]["photo_authenticity_local_tree_enabled"] == "false"
    assert disabled["runManifest"]["photo_authenticity_local_tree_enabled"] == "false"


def test_legacy_prompt_loads_when_candidate_module_import_is_blocked():
    project_root = Path(__file__).resolve().parents[1]
    script = r'''
import builtins
import importlib

original_import = builtins.__import__
original_import_module = importlib.import_module

def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "tools.compliance_candidate_rules" or (
        name == "tools" and "compliance_candidate_rules" in fromlist
    ):
        raise ImportError("candidate module intentionally blocked")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = blocked_import

def blocked_import_module(name, package=None):
    if name == "tools.compliance_candidate_rules":
        raise ImportError("candidate module intentionally blocked")
    return original_import_module(name, package)

importlib.import_module = blocked_import_module
from tools import run_guobu_model_audit_v2 as mainline

prompt = mainline.compliance_prompt_for_category(
    "ordinary_3c",
    ruleset="legacy",
    include_photo_authenticity=False,
    digital_activation_evidence_mode="off",
)
assert prompt == mainline.ORDINARY_3C_COMPLIANCE_PROMPT
assert mainline._is_cacheable_model_result(
    "hybrid_compliance", prompt, {}, [], {}
) is True
print("legacy-ok")
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "legacy-ok"


def test_candidate_prompt_is_exact_prefix_and_keeps_existing_plugins():
    base = candidate.prompt_for_category("ordinary_3c")
    base_sha = hashlib.sha256(base.encode("utf-8")).hexdigest()

    prompt = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="[B01] 手机",
        include_photo_authenticity=True,
        replace_legacy_authenticity_adjudication=True,
        digital_activation_evidence_mode="on",
        ruleset="candidate",
    )

    assert prompt.startswith(base)
    assert prompt.count(v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM) == 1
    assert prompt.count(v2.PHOTO_AUTHENTICITY_REPLACEMENT_DIRECTIVE) == 1
    assert v2.read_digital_activation_evidence_prompt() in prompt
    assert hashlib.sha256(base.encode("utf-8")).hexdigest() == base_sha
    assert base_sha == candidate.PROMPT_SHA256["ordinary_3c"]


def test_candidate_prompt_authenticity_mode_matrix_keeps_plugin_semantics():
    off = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="[B01] 手机",
        ruleset="candidate",
        include_photo_authenticity=False,
        digital_activation_evidence_mode="off",
    )
    shadow = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="[B01] 手机",
        ruleset="candidate",
        include_photo_authenticity=True,
        replace_legacy_authenticity_adjudication=False,
        digital_activation_evidence_mode="off",
    )
    enforce = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="[B01] 手机",
        ruleset="candidate",
        include_photo_authenticity=True,
        replace_legacy_authenticity_adjudication=True,
        digital_activation_evidence_mode="off",
    )

    assert v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM not in off
    assert v2.PHOTO_AUTHENTICITY_REPLACEMENT_DIRECTIVE not in off
    assert v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM in shadow
    assert v2.PHOTO_AUTHENTICITY_REPLACEMENT_DIRECTIVE not in shadow
    assert v2.PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM in enforce
    assert v2.PHOTO_AUTHENTICITY_REPLACEMENT_DIRECTIVE in enforce


def test_all_candidate_categories_use_frozen_prompt_as_exact_prefix():
    for category, base in candidate.PROMPTS.items():
        prompt = v2.compliance_prompt_for_category(
            category,
            ruleset="candidate",
            include_photo_authenticity=False,
            digital_activation_evidence_mode="off",
        )
        assert prompt == base
        assert hashlib.sha256(base.encode("utf-8")).hexdigest() == (
            candidate.PROMPT_SHA256[category]
        )


def test_explicit_legacy_prompt_remains_byte_identical():
    assert v2.compliance_prompt_for_category(
        "home_appliance",
        include_photo_authenticity=False,
        digital_activation_evidence_mode="off",
        ruleset="legacy",
    ) == v2.HOME_APPLIANCE_COMPLIANCE_PROMPT


def test_candidate_structure_anomaly_fails_closed_and_preserves_fields():
    response = _candidate_phone_response()
    response.pop("product_photo_ok")

    normalized = v2.normalize_candidate_compliance_response(
        "ordinary_3c",
        "[B01] 手机",
        response,
        unboxing_image_ids=("img_002",),
    )

    assert normalized["manual_required"] is True
    assert normalized["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert normalized["compliance_structure_anomaly"] is True
    assert normalized["compliance_missing_model_fields"] == ["product_photo_ok"]
    assert normalized["activation_photo_ok"] is True
    assert normalized["activation_identity_by_image"] == response[
        "activation_identity_by_image"
    ]


def test_candidate_non_object_fails_closed():
    normalized = v2.normalize_candidate_compliance_response(
        "ordinary_3c",
        "[B01] 手机",
        [],
        unboxing_image_ids=("img_002",),
    )

    assert normalized["manual_required"] is True
    assert normalized["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
    assert normalized["compliance_structure_anomaly"] is True
    assert normalized["compliance_invalid_model_fields"] == ["$"]


def test_invalid_json_can_reach_candidate_fail_closed_adapter(monkeypatch):
    monkeypatch.setattr(
        v2,
        "_post_chat_completion_json",
        lambda *_args, **_kwargs: {
            "choices": [{"message": {"content": "not-json"}}],
            "usage": {"total_tokens": 17},
        },
    )

    parsed, raw, elapsed, usage, cached = v2.call_model(
        "https://example.invalid/v1",
        "key",
        "qwen3.7-plus",
        "prompt",
        {},
        [],
        stage="hybrid_compliance",
        allow_non_object=True,
    )

    assert parsed is None
    assert raw == "not-json"
    assert elapsed >= 0
    assert usage["total_tokens"] == 17
    assert cached is False


def test_candidate_structure_anomaly_is_not_cached(monkeypatch, tmp_path):
    calls = 0

    def fake_post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "choices": [
                {"message": {"content": json.dumps({"manual_reason_codes": []})}}
            ],
            "usage": {"total_tokens": 7},
        }

    monkeypatch.setattr(v2, "_post_chat_completion_json", fake_post)
    prompt = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="[B01] 手机",
        ruleset="candidate",
        include_photo_authenticity=False,
        digital_activation_evidence_mode="off",
    )
    payload = {"product_type": "[B01] 手机", "image_groups": {}}

    first = v2.call_model(
        "https://example.invalid/v1",
        "key",
        "qwen3.7-plus",
        prompt,
        payload,
        [],
        stage="hybrid_compliance",
        cache_dir=tmp_path,
        allow_non_object=True,
    )
    second = v2.call_model(
        "https://example.invalid/v1",
        "key",
        "qwen3.7-plus",
        prompt,
        payload,
        [],
        stage="hybrid_compliance",
        cache_dir=tmp_path,
        allow_non_object=True,
    )

    assert calls == 2
    assert first[4] is False
    assert second[4] is False
    assert list(tmp_path.glob("*.json")) == []


def test_candidate_and_legacy_compliance_caches_are_isolated(monkeypatch, tmp_path):
    calls = 0

    def fake_post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            _candidate_phone_response(), ensure_ascii=False
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 9},
        }

    monkeypatch.setattr(v2, "_post_chat_completion_json", fake_post)
    payload = {"product_type": "[B01] 手机", "image_groups": {}}
    candidate_prompt = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="[B01] 手机",
        ruleset="candidate",
        include_photo_authenticity=False,
        digital_activation_evidence_mode="off",
    )
    legacy_prompt = v2.compliance_prompt_for_category(
        "ordinary_3c",
        product_type="[B01] 手机",
        ruleset="legacy",
        include_photo_authenticity=False,
        digital_activation_evidence_mode="off",
    )

    first_candidate = v2.call_model(
        "https://example.invalid/v1", "key", "qwen3.7-plus",
        candidate_prompt, payload, [], stage="hybrid_compliance",
        cache_dir=tmp_path, allow_non_object=True,
    )
    first_legacy = v2.call_model(
        "https://example.invalid/v1", "key", "qwen3.7-plus",
        legacy_prompt, payload, [], stage="hybrid_compliance",
        cache_dir=tmp_path,
    )
    second_candidate = v2.call_model(
        "https://example.invalid/v1", "key", "qwen3.7-plus",
        candidate_prompt, payload, [], stage="hybrid_compliance",
        cache_dir=tmp_path, allow_non_object=True,
    )

    assert calls == 2
    assert first_candidate[4] is False
    assert first_legacy[4] is False
    assert second_candidate[4] is True


def test_candidate_prompt_labels_are_part_of_cache_key():
    image = {
        "image_id": "img_001",
        "source_url": "https://example.invalid/1.jpg",
        "_detail": "low",
    }
    labeled = dict(image, _prompt_label="【商品照片｜img_001】")

    unlabeled_key = v2._cache_key(
        "qwen3.7-plus", "hybrid_compliance", "prompt", {}, [image]
    )
    labeled_key = v2._cache_key(
        "qwen3.7-plus", "hybrid_compliance", "prompt", {}, [labeled]
    )

    assert labeled_key != unlabeled_key


def test_candidate_input_gate_rejects_invalid_unboxing_image_ids_before_model():
    invalid_groups = (
        [{"image_id": 2, "source_url": "https://example.invalid/2.jpg"}],
        [{"image_id": "", "source_url": "https://example.invalid/2.jpg"}],
        [
            {"image_id": "img_002", "source_url": "https://example.invalid/2a.jpg"},
            {"image_id": "img_002", "source_url": "https://example.invalid/2b.jpg"},
        ],
    )

    for unboxing_images in invalid_groups:
        task = _phone_task()
        task["image_groups"]["拆封照片"] = unboxing_images
        precheck = v2.precheck_task(task)

        manual, raw_ids = v2._candidate_input_gate(precheck, task["fields"])

        assert manual is not None
        assert manual["manual_reason_codes"] == ["MODEL_UNCERTAIN"]
        assert manual["compliance_structure_anomaly"] is True
        assert manual["compliance_invalid_model_fields"] == [
            "$input.unboxing_image_ids"
        ]
        assert raw_ids == tuple(image.get("image_id") for image in unboxing_images)


def test_candidate_cache_validation_preserves_invalid_raw_unboxing_id():
    prompt = v2.compliance_prompt_for_category(
        "home_appliance",
        product_type="[A02] 电冰箱",
        ruleset="candidate",
        include_photo_authenticity=False,
        digital_activation_evidence_mode="off",
    )
    parsed = {
        "manual_reason_codes": [],
        "product_type_match": "match",
        "product_photo_ok": True,
        "unboxing_photo_ok": True,
        "unboxing_image_evidence": [
            {
                "image_id": "2",
                "product_visible": True,
                "package_visible": True,
                "home_or_installation_scene_visible": False,
            }
        ],
        "duplicate_image_evidence": False,
        "evidence_summary": "商品与包装同图可见",
        "confidence": 0.99,
    }
    payload = {
        "product_type": "[A02] 电冰箱",
        "image_groups": {
            "拆封照片": [
                {"image_id": 2, "source_url": "https://example.invalid/2.jpg"}
            ]
        },
    }

    assert v2._is_cacheable_model_result(
        "hybrid_compliance", prompt, parsed, [], payload
    ) is False


def test_hybrid_defaults_to_candidate_and_maps_complete_response(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_RULESET", raising=False)
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    monkeypatch.setenv("DIGITAL_ACTIVATION_EVIDENCE_MODE", "on")
    calls = []

    def fake_call(*args, stage, **kwargs):
        prompt = args[3]
        calls.append((stage, prompt, kwargs))
        if stage == "hybrid_sn":
            return (
                {
                    "sn_match": True,
                    "observed_sn": "ABC123",
                    "normalized_observed_sn": "ABC123",
                    "confidence": 0.99,
                },
                "sn raw",
                0.1,
                {"total_tokens": 10},
                False,
            )
        return (
            _candidate_phone_response(),
            "candidate raw",
            0.2,
            {"total_tokens": 20},
            False,
        )

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = v2.audit_task_hybrid(
        "https://example.invalid/v1", "key", "qwen3.7-plus", _phone_task()
    )

    assert calls[1][0] == "hybrid_compliance"
    assert calls[1][1].startswith(candidate.prompt_for_category("ordinary_3c"))
    assert calls[1][2]["allow_non_object"] is True
    assert result["manual_flag"] == "否"
    assert result["product_type_match"] == "match"
    assert result["product_photo_ok"] is True
    assert result["unboxing_photo_ok"] is True
    assert result["activation_photo_ok"] is True
    assert result["activation_evidence_type"] == "SCREEN_SN"
    assert result["compliance_ruleset"] == "candidate"
    assert result["compliance_version"] == candidate.CANDIDATE_VERSION
    assert result["compliance_stage"] == candidate.CANDIDATE_STAGE
    assert result["compliance_structure_anomaly"] is False
    assert result["_raw"]["compliance_raw"] == "candidate raw"


def test_hybrid_candidate_missing_field_is_manual_not_service_failure(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_RULESET", raising=False)
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    response = _candidate_phone_response()
    response.pop("activation_evidence_type")

    def fake_call(*_args, stage, **_kwargs):
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99},
                "sn raw",
                0.1,
                {},
                False,
            )
        return response, "malformed candidate raw", 0.2, {}, False

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = v2.audit_task_hybrid(
        "https://example.invalid/v1", "key", "qwen3.7-plus", _phone_task()
    )

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert result["compliance_structure_anomaly"] is True
    assert result["compliance_missing_model_fields"] == ["activation_evidence_type"]
    assert result["_raw"]["compliance_raw"] == "malformed candidate raw"


def test_hybrid_candidate_duplicate_reason_survives_legacy_postprocess(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_RULESET", raising=False)
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    response = _candidate_phone_response()
    response["manual_reason_codes"] = ["DUPLICATE_IMAGE_EVIDENCE"]
    response["duplicate_image_evidence"] = True

    def fake_call(*_args, stage, **_kwargs):
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99},
                "sn raw",
                0.1,
                {},
                False,
            )
        return response, "duplicate raw", 0.2, {}, False

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = v2.audit_task_hybrid(
        "https://example.invalid/v1", "key", "qwen3.7-plus", _phone_task()
    )

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "DUPLICATE_IMAGE_EVIDENCE"


def test_hybrid_candidate_activation_reason_survives_legacy_postprocess(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_RULESET", raising=False)
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    response = _candidate_phone_response()
    response["manual_reason_codes"] = ["ACTIVATION_PHOTO_INVALID"]
    response["activation_photo_ok"] = False
    response["activation_evidence_type"] = "SCREEN_ON_NO_IDENTITY"

    def fake_call(*_args, stage, **_kwargs):
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99},
                "sn raw",
                0.1,
                {},
                False,
            )
        return response, "activation invalid raw", 0.2, {}, False

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = v2.audit_task_hybrid(
        "https://example.invalid/v1", "key", "qwen3.7-plus", _phone_task()
    )

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "ACTIVATION_PHOTO_INVALID"


def test_hybrid_explicit_legacy_uses_old_prompt_and_result_contract(monkeypatch):
    monkeypatch.setenv("COMPLIANCE_RULESET", "legacy")
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    prompts = []

    def fake_call(*args, stage, **_kwargs):
        prompt = args[3]
        prompts.append((stage, prompt))
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99},
                "sn raw",
                0.1,
                {},
                False,
            )
        return _legacy_phone_response(), "legacy raw", 0.2, {}, False

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = v2.audit_task_hybrid(
        "https://example.invalid/v1", "key", "qwen3.7-plus", _phone_task()
    )

    assert prompts[1][1].startswith(v2.COMPLIANCE_COMMON_PROMPT)
    assert "当前品类：普通3C" in prompts[1][1]
    assert not prompts[1][1].startswith(candidate.prompt_for_category("ordinary_3c"))
    assert result["manual_flag"] == "否"
    assert result["compliance_ruleset"] == "legacy"


def test_hybrid_candidate_unknown_product_type_is_local_manual(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_RULESET", raising=False)
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    task = _phone_task()
    task["fields"]["product_type"] = "[B99] 手机壳"
    calls = []

    def fake_call(*_args, stage, **_kwargs):
        calls.append(stage)
        assert stage == "hybrid_sn"
        return (
            {"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99},
            "sn raw",
            0.1,
            {"total_tokens": 11},
            False,
        )

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = v2.audit_task_hybrid(
        "https://example.invalid/v1", "key", "qwen3.7-plus", task
    )

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert result["model_calls"] == 1
    assert result["observed_sn"] == "ABC123"
    assert result["sn_match"] is True
    assert calls == ["hybrid_sn"]


def test_hybrid_candidate_missing_unboxing_group_is_local_manual(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_RULESET", raising=False)
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    task = _phone_task()
    task["image_groups"].pop("拆封照片")
    calls = []

    def fake_call(*_args, stage, **_kwargs):
        calls.append(stage)
        assert stage == "hybrid_sn"
        return (
            {"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99},
            "sn raw",
            0.1,
            {},
            False,
        )

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = v2.audit_task_hybrid(
        "https://example.invalid/v1", "key", "qwen3.7-plus", task
    )

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert result["model_calls"] == 1
    assert result["observed_sn"] == "ABC123"
    assert calls == ["hybrid_sn"]


def test_hybrid_candidate_unusable_required_image_is_local_manual_after_sn(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("COMPLIANCE_RULESET", raising=False)
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    task = _phone_task()
    product_image = task["image_groups"]["商品照片"][0]
    product_image.pop("source_url")
    product_image["local_path"] = str(tmp_path / "missing.jpg")
    calls = []

    def fake_call(*_args, stage, **_kwargs):
        calls.append(stage)
        assert stage == "hybrid_sn"
        return (
            {"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99},
            "sn raw",
            0.1,
            {},
            False,
        )

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = v2.audit_task_hybrid(
        "https://example.invalid/v1", "key", "qwen3.7-plus", task
    )

    assert result["manual_flag"] == "是"
    assert result["manual_reason_code"] == "MODEL_UNCERTAIN"
    assert "商品照片组" in result["compliance_input_error"]
    assert result["observed_sn"] == "ABC123"
    assert calls == ["hybrid_sn"]


def test_hybrid_candidate_sends_group_and_image_id_labels(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_RULESET", raising=False)
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    captured_images = []

    def fake_call(*args, stage, **_kwargs):
        if stage == "hybrid_sn":
            return (
                {"sn_match": True, "observed_sn": "ABC123", "confidence": 0.99},
                "sn raw",
                0.1,
                {},
                False,
            )
        captured_images.extend(args[5])
        return _candidate_phone_response(), "candidate raw", 0.2, {}, False

    monkeypatch.setattr(v2, "call_model_with_retry", fake_call)

    result = v2.audit_task_hybrid(
        "https://example.invalid/v1", "key", "qwen3.7-plus", _phone_task()
    )

    assert result["manual_flag"] == "否"
    assert [image["_prompt_label"] for image in captured_images] == [
        "【商品照片｜img_001】",
        "【拆封/安装照片｜img_002】",
        "【激活/SN照片｜img_003】",
    ]


def test_hybrid_precheck_failure_does_not_claim_candidate_was_applied(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_RULESET", raising=False)
    monkeypatch.setenv("PHOTO_AUTHENTICITY_MODE", "off")
    task = _phone_task()
    task["fields"]["system_sn"] = ""
    monkeypatch.setattr(
        v2,
        "call_model_with_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("precheck failure must not call models")
        ),
    )

    result = v2.audit_task_hybrid(
        "https://example.invalid/v1", "key", "qwen3.7-plus", task
    )

    assert result["manual_flag"] == "是"
    assert result["compliance_ruleset"] == ""
    assert result["compliance_version"] == ""
    assert result["compliance_stage"] == ""
