from __future__ import annotations
import argparse, json, os, re
from pathlib import Path
from typing import Any

NETWORK_FAILURE_MARKERS = ("timeouterror", "timed out", "orderbudgetexceeded",
                           "超过每单60秒总期限", "modelconnectionerror", "connect failed",
                           "winerror 10060", "http error 500",
                           "remote end closed connection without response")
REMOTE_DISCONNECTED_RE = re.compile(r"(?<![A-Za-z])RemoteDisconnected(?![A-Za-z])", re.I)
MANIFEST_COMPATIBILITY_FIELDS = (
    "model",
    "mode",
    "compliance_ruleset",
    "sn_policy_version",
    "workers",
    "targeted_sn_review",
    "sn_char_review_mode",
    "sn_label_auth_review_mode",
    "photo_auth_edge_mapping_mode",
    "digital_activation_evidence_mode",
    "photo_authenticity_mode",
    "photo_authenticity_local_tree_enabled",
    "order_timeout_seconds",
    "git_commit",
    "python_path",
    "python_version",
    "cv2_version",
    "git_worktree_dirty",
    "runtime_sha256",
    "prompt_sha256",
)


def load_json_utf8(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json_utf8(path: Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _missing_manifest_fields(manifest: dict, label: str) -> list[str]:
    return [f"{label}.{field}" for field in MANIFEST_COMPATIBILITY_FIELDS if field not in manifest]


def _validate_hashes_compatible(field: str, first: dict, retry: dict) -> None:
    if not isinstance(first, dict) or not isinstance(retry, dict):
        raise ValueError(f"run manifest drift: {field} must be an object")
    for key in sorted(set(first) | set(retry)):
        if first.get(key) != retry.get(key):
            raise ValueError(f"run manifest drift: {field}.{key} differs")


def validate_manifest_compatibility(first_manifest: dict, retry_manifest: dict) -> None:
    missing = _missing_manifest_fields(first_manifest, "first") + _missing_manifest_fields(
        retry_manifest, "retry"
    )
    if missing:
        raise ValueError("run manifest missing field(s): " + ", ".join(missing))
    for field in MANIFEST_COMPATIBILITY_FIELDS:
        if field in {"prompt_sha256", "runtime_sha256"}:
            _validate_hashes_compatible(field, first_manifest[field], retry_manifest[field])
        elif first_manifest[field] != retry_manifest[field]:
            raise ValueError(f"run manifest drift: {field} differs")

def network_failure(item: dict) -> bool:
    row = item.get("row") or {}
    text = "\n".join(str(x) for x in [item.get("_error"), row.get("manual_reason"),
        row.get("manual_reason_cn"), row.get("strategy"), row.get("error")] if x is not None)
    return any(x in text.lower() for x in NETWORK_FAILURE_MARKERS) or bool(REMOTE_DISCONNECTED_RE.search(text))

def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8-sig").splitlines() if x.strip()]

def item_order_id(item: dict) -> str:
    return str((item.get("row") or {}).get("id") or (item.get("task") or {}).get("channel_order_no") or "").strip()

def validate_first_run(tasks_dir: Path, first_jsonl: Path) -> None:
    expected = []
    for path in sorted(tasks_dir.glob("*.json")):
        task = json.loads(path.read_text(encoding="utf-8-sig"))
        value = str(task.get("channel_order_no") or task.get("task_id") or (task.get("row") or {}).get("id") or "").strip()
        if not value: raise ValueError(f"task has no order ID: {path}")
        expected.append(value)
    actual = [item_order_id(x) for x in load_jsonl(first_jsonl)]
    if any(not x for x in actual): raise ValueError("first JSONL contains an empty order ID")
    if len(expected) != len(set(expected)) or len(actual) != len(set(actual)):
        raise ValueError("duplicate order ID in tasks or first JSONL")
    if set(expected) != set(actual):
        raise ValueError(f"first JSONL ID mismatch: missing={sorted(set(expected)-set(actual))}, extra={sorted(set(actual)-set(expected))}")

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tasks-dir")
    p.add_argument("--first-jsonl")
    p.add_argument("--first-manifest")
    p.add_argument("--retry-manifest")
    p.add_argument("--retry-manifest-json-env")
    a = p.parse_args()
    validated = {}
    if a.tasks_dir or a.first_jsonl:
        if not (a.tasks_dir and a.first_jsonl):
            p.error("--tasks-dir and --first-jsonl must be passed together")
        validate_first_run(Path(a.tasks_dir), Path(a.first_jsonl))
        validated["first_run"] = True
    if a.first_manifest or a.retry_manifest or a.retry_manifest_json_env:
        retry_sources = [bool(a.retry_manifest), bool(a.retry_manifest_json_env)]
        if not a.first_manifest or sum(retry_sources) != 1:
            p.error("--first-manifest and exactly one retry manifest source must be passed together")
        retry_manifest = (
            load_json_utf8(Path(a.retry_manifest))
            if a.retry_manifest
            else json.loads(os.environ.get(a.retry_manifest_json_env, ""))
        )
        validate_manifest_compatibility(
            load_json_utf8(Path(a.first_manifest)),
            retry_manifest,
        )
        validated["manifest_compatible"] = True
    if not validated:
        p.error("nothing to validate")
    print(json.dumps({"validated": True, **validated}, ensure_ascii=False))

if __name__ == "__main__": main()
