# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from tools import guobu_audit_contract as contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHINESE_CONTRACT_NOTE = "\u4e2d\u6587\u8fd0\u884c\u5951\u7ea6"


def _manifest() -> dict:
    return {
        "model": "qwen3.7-plus",
        "mode": "hybrid",
        "workers": 1,
        "targeted_sn_review": False,
        "sn_char_review_mode": "on",
        "sn_label_auth_review_mode": "on",
        "digital_activation_evidence_mode": "on",
        "photo_auth_edge_mapping_mode": "off",
        "photo_authenticity_mode": "enforce",
        "order_timeout_seconds": 60,
        "git_commit": "a" * 40,
        "python_path": r"C:\project\.venv-photo-auth\Scripts\python.exe",
        "python_version": "3.11.9",
        "cv2_version": "5.0.0",
        "git_worktree_dirty": True,
        "runtime_sha256": {
            "tools/run_guobu_audit_batch.ps1": "4" * 64,
            "tools/run_guobu_model_audit_v2.py": "5" * 64,
        },
        "prompt_sha256": {
            "sn_similar_char_review.txt": "1" * 64,
            "sn_label_authenticity_review.txt": "2" * 64,
            "digital_activation_evidence_review.txt": "3" * 64,
        },
        "note": CHINESE_CONTRACT_NOTE,
    }


def test_utf8_json_helpers_round_trip_chinese_manifest(tmp_path):
    path = tmp_path / "run_manifest.json"
    manifest = _manifest()

    contract.write_json_utf8(path, manifest)

    raw = path.read_bytes()
    assert CHINESE_CONTRACT_NOTE.encode("utf-8") in raw
    assert contract.load_json_utf8(path) == manifest


def test_manifest_compatibility_rejects_sn_character_mode_drift():
    first = _manifest()
    retry = deepcopy(first)
    retry["sn_char_review_mode"] = "off"

    with pytest.raises(ValueError, match="sn_char_review_mode"):
        contract.validate_manifest_compatibility(first, retry)


def test_manifest_compatibility_rejects_photo_auth_edge_mapping_mode_drift():
    first = _manifest()
    retry = deepcopy(first)
    retry["photo_auth_edge_mapping_mode"] = "on"

    with pytest.raises(ValueError, match="photo_auth_edge_mapping_mode"):
        contract.validate_manifest_compatibility(first, retry)


def test_manifest_compatibility_rejects_prompt_hash_drift():
    first = _manifest()
    retry = deepcopy(first)
    retry["prompt_sha256"]["sn_similar_char_review.txt"] = "f" * 64

    with pytest.raises(ValueError, match=r"prompt_sha256\.sn_similar_char_review\.txt"):
        contract.validate_manifest_compatibility(first, retry)


def test_manifest_compatibility_rejects_dirty_worktree_drift():
    first = _manifest()
    retry = deepcopy(first)
    retry["git_worktree_dirty"] = False

    with pytest.raises(ValueError, match="git_worktree_dirty"):
        contract.validate_manifest_compatibility(first, retry)


def test_manifest_compatibility_rejects_runtime_hash_drift():
    first = _manifest()
    retry = deepcopy(first)
    retry["runtime_sha256"]["tools/run_guobu_model_audit_v2.py"] = "f" * 64

    with pytest.raises(ValueError, match=r"runtime_sha256\.tools/run_guobu_model_audit_v2\.py"):
        contract.validate_manifest_compatibility(first, retry)


def test_network_failure_selects_order_budget_exceeded_timeout_result():
    item = {
        "_error": (
            "tools.run_guobu_model_audit_v2.OrderBudgetExceeded: "
            "模型审核超过每单60秒总期限，已转人工复核"
        ),
        "row": {
            "id": "order-timeout",
            "manual_reason": "OrderBudgetExceeded: 模型审核超过每单60秒总期限，已转人工复核",
            "manual_reason_cn": "模型审核超过每单60秒总期限，已转人工复核",
            "strategy": "error_to_manual",
        },
    }

    assert contract.network_failure(item) is True


def test_network_failure_ignores_unrelated_manual_result():
    item = {
        "row": {
            "id": "business-manual",
            "manual_reason": "商品照片不合规，转人工复核",
            "manual_reason_cn": "商品照片不合规，转人工复核",
            "strategy": "hybrid_compliance_manual",
        },
    }

    assert contract.network_failure(item) is False


def test_top_level_runtime_paths_are_ignored_without_ignoring_prompts():
    lines = {
        line.strip()
        for line in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "/.venv-photo-auth/" in lines
    assert "/reports/" in lines
    assert "/temp/" in lines
    assert "/data/" in lines
    assert "prompts/" not in lines
    assert "/prompts/" not in lines
