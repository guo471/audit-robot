# -*- coding: utf-8 -*-
"""Linux unattended Guobu audit loop.

This module only orchestrates collection, mainline audit, callback feedback,
and monthly local state. It does not change SN, compliance, or photo
authenticity policy.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import subprocess
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REASON_CN = "图片信息无法确认，请参照示例图上传符合活动要求的照片"
DONE_STATUSES = {"FEEDBACK_DONE"}
FINAL_STATUSES = {"FEEDBACK_DONE", "MANUAL_FEEDBACK_REQUIRED"}
REQUIRED_STARTUP_ENV = (
    "VISION_API_BASE_URL",
    "VISION_API_KEY",
    "GUOBU_COLLECTOR_BASE_URL",
    "GUOBU_AUTH_TOKEN",
    "MACHINE_APPROVAL_AUTH_TOKEN",
)
REQUIRED_STARTUP_MODULES = ("zxingcpp", "cv2", "joblib", "sklearn", "numpy", "PIL")
MIN_STARTUP_PYTHON = (3, 11)

REASON_CN_BY_CODE = {
    "ADDRESS_TOO_COARSE": "收货地址不符合要求，请按照要求补充相关信息后再提交",
    "PRODUCT_TYPE_MISMATCH": "商品/拆封/激活照片与商品/拆封/激活照片显示商品不一致，请参照示例图上传符合活动要求的照片",
    "PRODUCT_PHOTO_INVALID": "商品照片不符合要求，请参照示例图上传符合活动要求的照片",
    "UNBOXING_PHOTO_INVALID": "拆封/安装照片不符合要求，请参照示例图上传符合活动要求的照片",
    "ACTIVATION_PHOTO_INVALID": "激活照片不符合要求，请参照示例图上传符合活动要求的照片",
    "DUPLICATE_IMAGE_EVIDENCE": "照片不能重复上传，请参照示例图上传符合活动要求的照片",
    "IMAGE_STRONG_RISK": "商品/拆封/激活照片非实拍图，请参照示例图上传符合活动要求的照片",
    "NON_REAL_PHOTO_REVIEW": "商品/拆封/激活照片非实拍图，请参照示例图上传符合活动要求的照片",
    "NON_REAL_PHOTO_STRONG_RISK": "商品/拆封/激活照片非实拍图，请参照示例图上传符合活动要求的照片",
    "NON_REAL_PHOTO_FFT_RESCUE": "商品/拆封/激活照片非实拍图，请参照示例图上传符合活动要求的照片",
    "SN_MISMATCH": "激活照片中SN码/序列码与系统显示不一致，请参照示例图上传符合活动要求的照片",
    "SN_NOT_FOUND": "激活照片未体现激活码，请参照示例图上传符合活动要求的照片",
    "SN_TRUNCATED_OBSCURED": "激活照片模糊完全无法识别激活码，请参照示例图上传符合活动要求的照片",
    "SYSTEM_SN_MISSING": "系统SN缺失，请按照要求补充相关信息后再提交",
    "IMAGE_MISSING": "图片缺失，请按照要求补充相关信息后再提交",
    "FIELD_MISSING": "订单信息缺失，请按照要求补充相关信息后再提交",
    "PRODUCT_TYPE_MISSING": "商品类型信息缺失，请按照要求补充相关信息后再提交",
    "INVOICE_ORANGE_WARNING": "发票已红冲，请核实后重新上传",
    "MODEL_UNCERTAIN": DEFAULT_REASON_CN,
    "PHOTO_AUTHENTICITY_SERVICE_FAILURE": "审核服务异常，模型超时",
    "ARTIFACT_LOAD_FAILURE": "审核服务异常，模型超时",
    "FFT_FAILURE": "审核服务异常，模型超时",
}


def _now() -> datetime:
    return datetime.now()


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _json_loads_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_runtime_metadata(argv: list[str] | None = None) -> dict[str, Any]:
    critical_files = [
        PROJECT_ROOT / "tools" / "guobu_linux_auto_audit.py",
        PROJECT_ROOT / "tools" / "guobu_machine_approval_feedback.js",
        PROJECT_ROOT / "tools" / "auto_audit_dashboard_server.py",
        PROJECT_ROOT / "tools" / "run_guobu_model_audit_v2.py",
        PROJECT_ROOT / "tools" / "guobu_sn_barcode.py",
        PROJECT_ROOT / "tools" / "start_guobu_linux_auto_audit.sh",
        PROJECT_ROOT / "tools" / "start_guobu_auto_audit.ps1",
    ]
    try:
        git_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        ).stdout.strip()
    except Exception:
        git_commit = ""
    try:
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
                check=False,
            ).stdout.strip()
        )
    except Exception:
        dirty = True
    runtime_hashes = {
        str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): _sha256_file(path)
        for path in critical_files
        if path.exists()
    }
    command = " ".join(argv if argv is not None else sys.argv)
    return {
        "git_commit": git_commit,
        "git_worktree_dirty": dirty,
        "runtime_file_sha256": runtime_hashes,
        "python_path": sys.executable,
        "startup_command_summary": redact_secret_text(command),
    }


def assert_production_startup_allowed(
    *,
    photo_authenticity_mode: str,
    startup_safety_override: str,
    metadata: Mapping[str, Any],
) -> None:
    mode = str(photo_authenticity_mode or "").strip().lower()
    override = str(startup_safety_override or "").strip().lower()
    if mode == "enforce" and bool(metadata.get("git_worktree_dirty")) and override not in {"shadow", "local", "allow_dirty"}:
        raise SystemExit("dirty worktree blocks production enforce startup; set GUOBU_STARTUP_SAFETY_OVERRIDE=shadow, local, or allow_dirty")


def redact_secret_text(value: Any) -> str:
    text = str(value)
    for key in (
        "GUOBU_AUTH_TOKEN",
        "MACHINE_APPROVAL_AUTH_TOKEN",
        "VISION_API_KEY",
        "VISION_API_BASE_URL",
    ):
        secret = os.environ.get(key)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:2000]


def _presence_status(env: Mapping[str, str], name: str) -> str:
    return "set" if str(env.get(name) or "").strip() else "missing"


def _module_version(module: Any) -> str:
    return str(getattr(module, "__version__", "") or "available")


def run_startup_preflight(
    *,
    env: Mapping[str, str] | None = None,
    import_module: Callable[[str], Any] = importlib.import_module,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    python_version: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """Fail fast before collection/audit/feedback can touch real orders."""
    env = dict(os.environ if env is None else env)
    version_tuple = tuple(python_version or sys.version_info[:3])
    failures: list[str] = []
    required_env = {name: _presence_status(env, name) for name in REQUIRED_STARTUP_ENV}
    for name, status in required_env.items():
        if status != "set":
            failures.append(f"missing env: {name}")

    python_version_text = ".".join(str(part) for part in version_tuple[:3])
    if version_tuple < MIN_STARTUP_PYTHON:
        failures.append(f"Python >= 3.11 required, got {python_version_text}")

    module_versions: dict[str, str] = {}
    for name in REQUIRED_STARTUP_MODULES:
        try:
            module_versions[name] = _module_version(import_module(name))
        except Exception as exc:
            module_versions[name] = "missing"
            failures.append(f"missing python module: {name} ({type(exc).__name__})")

    node_version = ""
    try:
        node = run_command(
            ["node", "--version"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        node_version = str(getattr(node, "stdout", "") or "").strip()
        if int(getattr(node, "returncode", 1) or 0) != 0 or not node_version:
            failures.append("Node runtime unavailable: node --version failed")
    except Exception as exc:
        failures.append(f"Node runtime unavailable: {type(exc).__name__}")

    report = {
        "ok": not failures,
        "required_env": required_env,
        "python_version": python_version_text,
        "python_modules": module_versions,
        "node_version": node_version,
    }
    if failures:
        report["failures"] = failures
        raise SystemExit("startup preflight failed: " + "; ".join(failures))
    return report


def _maybe_int(value: Any) -> Any:
    text = str(value).strip()
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            return text
    return text


def first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def canonical_apply_id(order: Mapping[str, Any] | None) -> str:
    order = order or {}
    fields = order.get("fields") if isinstance(order.get("fields"), Mapping) else {}
    source = order.get("source") if isinstance(order.get("source"), Mapping) else {}
    return first_non_empty(
        order.get("apply_id"),
        order.get("applyId"),
        order.get("id"),
        fields.get("apply_id"),
        fields.get("applyId"),
        source.get("apply_id"),
        source.get("applyId"),
    )


def canonical_channel_order_no(order: Mapping[str, Any] | None) -> str:
    order = order or {}
    fields = order.get("fields") if isinstance(order.get("fields"), Mapping) else {}
    source = order.get("source") if isinstance(order.get("source"), Mapping) else {}
    return first_non_empty(
        order.get("channel_order_no"),
        order.get("channelOrderNo"),
        order.get("jlPayOrder"),
        order.get("jl_pay_order"),
        order.get("wxPayOrder"),
        fields.get("channel_order_no"),
        fields.get("jlPayOrder"),
        fields.get("jl_pay_order"),
        source.get("jl_pay_order"),
        source.get("jlPayOrder"),
        order.get("task_id"),
    )


def dedup_key_for_order(order: Mapping[str, Any]) -> str:
    apply_id = canonical_apply_id(order)
    if apply_id:
        return f"apply:{apply_id}"
    channel_order_no = canonical_channel_order_no(order)
    if channel_order_no:
        return f"channel:{channel_order_no}"
    raise ValueError("order is missing applyId and channel_order_no")


def build_examine_page_payload(
    *,
    current_page: int = 1,
    page_size: int = 20,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(extra or {})
    payload["currentPage"] = int(current_page)
    payload["pageSize"] = int(page_size)
    payload["status"] = 0
    payload["machineExamineStatus"] = 0
    return payload


def machine_examine_status_is_pending(order: Mapping[str, Any]) -> bool:
    if "machineExamineStatus" not in order and "machine_examine_status" not in order:
        return False
    values = [
        order[key]
        for key in ("machineExamineStatus", "machine_examine_status")
        if key in order
    ]
    return all(value is None or str(value).strip().lower() in {"", "none", "null"} for value in values)


def _manual_required(result: Mapping[str, Any]) -> bool:
    if bool(result.get("manual_required")):
        return True
    manual_flag = str(result.get("manual_flag") or "").strip().lower()
    if manual_flag in {"是", "true", "1", "yes", "manual"}:
        return True
    decision = str(result.get("decision") or "").strip().lower()
    if decision in {"manual", "error", "reject", "failed", "fail"}:
        return True
    if manual_flag in {"否", "false", "0", "no", "pass"} or decision in {"pass", "approve", "approved"}:
        return False
    return bool(result.get("manual_reason_code") or result.get("manual_reason_codes"))


def _primary_reason_code(result: Mapping[str, Any]) -> str:
    code = str(result.get("manual_reason_code") or "").strip()
    if code:
        return code
    codes = result.get("manual_reason_codes")
    if isinstance(codes, list) and codes:
        return str(codes[0]).strip()
    return ""


def refuse_message_from_result(result: Mapping[str, Any]) -> str:
    code = _primary_reason_code(result)
    if code:
        return REASON_CN_BY_CODE.get(code, DEFAULT_REASON_CN)
    reason_cn = str(result.get("manual_reason_cn") or "").strip()
    if reason_cn and len(reason_cn) <= 80:
        return reason_cn
    return DEFAULT_REASON_CN


def build_machine_approval_request(
    apply_id: Any,
    audit_result: Mapping[str, Any] | None = None,
    *,
    refuse_message: str | None = None,
) -> dict[str, Any]:
    result = dict(audit_result or {})
    request: dict[str, Any] = {"applyId": _maybe_int(apply_id)}
    if _manual_required(result):
        request["status"] = 2
        request["refuseMessage"] = str(refuse_message or refuse_message_from_result(result)).strip() or DEFAULT_REASON_CN
    else:
        request["status"] = 1
    return request


def _display_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _sn_barcode_mapping_from_audit_result(result: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("sn_barcode_result", "barcode_result"):
        value = result.get(key)
        if isinstance(value, Mapping):
            return value
    raw = result.get("_raw")
    if isinstance(raw, Mapping):
        for key in ("sn_barcode_result", "barcode_result"):
            value = raw.get(key)
            if isinstance(value, Mapping):
                return value
    return None


def _barcode_values_from_mapping(barcode: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(barcode, Mapping):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for decoded in barcode.get("decoded") or []:
        if not isinstance(decoded, Mapping):
            continue
        text = first_non_empty(decoded.get("text"), decoded.get("raw_text"), decoded.get("value"))
        if text and text not in seen:
            values.append(text)
            seen.add(text)
    matched = first_non_empty(barcode.get("matched_text"))
    if matched and matched not in seen:
        values.append(matched)
    return values


def sn_barcode_observability_from_audit_result(result: Mapping[str, Any]) -> dict[str, Any]:
    barcode = _sn_barcode_mapping_from_audit_result(result)
    if isinstance(result.get("barcode_attempted"), bool):
        return {
            "barcode_attempted": bool(result.get("barcode_attempted")),
            "barcode_matched": bool(result.get("barcode_matched")),
            "barcode_values": list(result.get("barcode_values") or []),
            "barcode_error": str(result.get("barcode_error") or ""),
            "barcode_rescued": bool(result.get("barcode_rescued")),
        }
    attempted = isinstance(barcode, Mapping)
    matched = bool(barcode.get("matched")) if attempted else False
    return {
        "barcode_attempted": attempted,
        "barcode_matched": matched,
        "barcode_values": _barcode_values_from_mapping(barcode),
        "barcode_error": str((barcode or {}).get("error") or "") if attempted else "",
        "barcode_rescued": bool(attempted and matched and not _manual_required(result)),
    }


def normalize_audit_result_observability(result: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(result or {})
    normalized.setdefault("model_sn", first_non_empty(normalized.get("model_sn"), normalized.get("observed_sn"), normalized.get("normalized_observed_sn")))
    normalized.setdefault("new_final_result", "不通过" if _manual_required(normalized) else "通过")
    normalized.setdefault("reason_code_cn", refuse_message_from_result(normalized) if _manual_required(normalized) else "")
    normalized.update(sn_barcode_observability_from_audit_result(normalized))
    return normalized


def sn_barcode_result_from_audit_result(result: Mapping[str, Any]) -> str:
    barcode = _sn_barcode_mapping_from_audit_result(result)
    if not isinstance(barcode, Mapping):
        return ""
    if barcode.get("matched") is True:
        return str(barcode.get("match_type") or "matched").strip()
    if barcode.get("matched") is False:
        return "no_barcode_match"
    return _display_text(barcode)


def build_order_result_summary(
    *,
    apply_id: Any,
    audit_result: Mapping[str, Any],
    order_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = order_context or {}
    fields = context.get("fields") if isinstance(context.get("fields"), Mapping) else {}
    manual = _manual_required(audit_result)
    code = _primary_reason_code(audit_result) if manual else ""
    barcode = sn_barcode_observability_from_audit_result(audit_result)
    return {
        "apply_id": str(first_non_empty(apply_id, canonical_apply_id(context))),
        "channel_order_no": first_non_empty(
            audit_result.get("id"),
            context.get("channel_order_no"),
            canonical_channel_order_no(context),
        ),
        "final_result": "不通过" if manual else "通过",
        "new_final_result": "不通过" if manual else "通过",
        "final_reason_code": code,
        "final_reason": refuse_message_from_result(audit_result) if manual else "",
        "reason_code_cn": refuse_message_from_result(audit_result) if manual else "",
        "system_sn": first_non_empty(audit_result.get("system_sn"), fields.get("system_sn")),
        "model_sn": first_non_empty(audit_result.get("model_sn"), audit_result.get("observed_sn"), audit_result.get("normalized_observed_sn")),
        "barcode_attempted": barcode["barcode_attempted"],
        "barcode_matched": barcode["barcode_matched"],
        "barcode_values": barcode["barcode_values"],
        "barcode_error": barcode["barcode_error"],
        "barcode_rescued": barcode["barcode_rescued"],
        "sn_barcode_result": sn_barcode_result_from_audit_result(audit_result),
        "compliance_observation": "" if manual else _display_text(audit_result.get("evidence_summary")),
    }


def append_manual_feedback_required_order(
    summary: dict[str, Any],
    *,
    dedup_key: str,
    apply_id: Any,
    order_context: Mapping[str, Any] | None = None,
    reason: Any = "",
    stage: str = "",
) -> None:
    context = order_context or {}
    summary.setdefault("manual_feedback_required_orders", []).append(
        {
            "apply_id": str(first_non_empty(apply_id, canonical_apply_id(context))),
            "channel_order_no": canonical_channel_order_no(context),
            "dedup_key": str(dedup_key or ""),
            "reason": redact_secret_text(reason),
            "stage": str(stage or ""),
        }
    )


@dataclass(frozen=True)
class LinuxAutoAuditConfig:
    state_dir: Path
    temp_dir: Path
    collector_base_url: str
    collector_auth_token: str = field(repr=False)
    approval_base_url: str = ""
    approval_auth_token: str = field(default="", repr=False)
    poll_interval_seconds: int = 600
    pending_heartbeat_threshold: int = 5
    audit_lease_seconds: int = 3600
    page_size: int = 20
    current_page: int = 1
    max_fetch_pages: int = 0
    callback_retry_delays: tuple[int, int] = (5, 30)
    audit_retry_delays: tuple[int, int] = (60, 300)
    startup_safety_override: str = ""
    model: str = "qwen3.7-plus"
    audit_mode: str = "hybrid"
    sn_policy_version: str = "v2"
    sn_barcode_mode: str = "enforce"
    photo_authenticity_mode: str = "enforce"
    photo_authenticity_new_rule_enabled: str = "true"
    photo_authenticity_local_tree_enabled: str = "false"
    photo_authenticity_local_tree_confirmation_enabled: str = "false"

    def __post_init__(self) -> None:
        object.__setattr__(self, "state_dir", Path(self.state_dir))
        object.__setattr__(self, "temp_dir", Path(self.temp_dir))
        if not self.approval_base_url:
            object.__setattr__(self, "approval_base_url", self.collector_base_url)
        if not self.approval_auth_token:
            object.__setattr__(self, "approval_auth_token", self.collector_auth_token)


class MonthlyAuditStateStore:
    def __init__(self, state_dir: Path | str):
        self.state_dir = Path(state_dir)

    def db_path_for(self, now: datetime | None = None) -> Path:
        stamp = (now or _now()).strftime("%Y_%m")
        return self.state_dir / f"audit_state_{stamp}.sqlite"

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                dedup_key TEXT PRIMARY KEY,
                apply_id TEXT,
                channel_order_no TEXT,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                task_json TEXT,
                audit_result_json TEXT,
                callback_request_json TEXT,
                callback_response_json TEXT,
                error_text TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                audit_retry_count INTEGER NOT NULL DEFAULT 0,
                audit_attempt INTEGER NOT NULL DEFAULT 0,
                lease_expires_at TEXT,
                next_audit_after TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                audited_at TEXT,
                feedback_done_at TEXT,
                manual_required_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
        order_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(orders)").fetchall()
        }
        for column, ddl in (
            ("audit_retry_count", "ALTER TABLE orders ADD COLUMN audit_retry_count INTEGER NOT NULL DEFAULT 0"),
            ("audit_attempt", "ALTER TABLE orders ADD COLUMN audit_attempt INTEGER NOT NULL DEFAULT 0"),
            ("lease_expires_at", "ALTER TABLE orders ADD COLUMN lease_expires_at TEXT"),
            ("next_audit_after", "ALTER TABLE orders ADD COLUMN next_audit_after TEXT"),
        ):
            if column not in order_columns:
                conn.execute(ddl)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_apply_id ON orders(apply_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_channel_order_no ON orders(channel_order_no)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedup_key TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                detail_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status_history_key ON status_history(dedup_key)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_status TEXT NOT NULL DEFAULT 'FINISHED',
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                next_loop_at TEXT,
                heartbeat_only INTEGER NOT NULL DEFAULT 0,
                pending_before INTEGER NOT NULL DEFAULT 0,
                fetched_count INTEGER NOT NULL DEFAULT 0,
                recovered_count INTEGER NOT NULL DEFAULT 0,
                reserved_count INTEGER NOT NULL DEFAULT 0,
                skipped_duplicate_count INTEGER NOT NULL DEFAULT 0,
                skipped_non_pending_machine_status_count INTEGER NOT NULL DEFAULT 0,
                processed_count INTEGER NOT NULL DEFAULT 0,
                feedback_done_count INTEGER NOT NULL DEFAULT 0,
                callback_failed_count INTEGER NOT NULL DEFAULT 0,
                manual_feedback_required_count INTEGER NOT NULL DEFAULT 0,
                errors_json TEXT NOT NULL,
                summary_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_runs_started_at ON audit_runs(started_at)")
        run_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(audit_runs)").fetchall()
        }
        if "run_status" not in run_columns:
            conn.execute("ALTER TABLE audit_runs ADD COLUMN run_status TEXT NOT NULL DEFAULT 'FINISHED'")

    @contextmanager
    def _connect(self, now: datetime | None = None) -> Iterator[sqlite3.Connection]:
        with self._connect_path(self.db_path_for(now)) as conn:
            yield conn

    @contextmanager
    def _connect_path(self, db_path: Path) -> Iterator[sqlite3.Connection]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            self._init_schema(conn)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _existing_db_paths(self) -> list[Path]:
        if not self.state_dir.exists():
            return []
        return sorted(self.state_dir.glob("audit_state_*.sqlite"))

    def _candidate_db_paths(self, now: datetime | None = None) -> list[Path]:
        current = self.db_path_for(now)
        paths = [current]
        for path in self._existing_db_paths():
            if path != current:
                paths.append(path)
        return paths

    def _target_db_paths(self, *, now: datetime | None = None, db_path: Path | str | None = None) -> list[Path]:
        if db_path:
            return [Path(db_path)]
        return self._candidate_db_paths(now)

    def contains_order(self, dedup_key: str, *, now: datetime | None = None) -> bool:
        for db_path in self._candidate_db_paths(now):
            if not db_path.exists():
                continue
            with self._connect_path(db_path) as conn:
                row = conn.execute("SELECT 1 FROM orders WHERE dedup_key = ? LIMIT 1", (dedup_key,)).fetchone()
                if row:
                    return True
        return False

    def _find_existing_order_ref(
        self,
        *,
        dedup_key: str,
        apply_id: str,
        channel_order_no: str,
        now: datetime | None = None,
    ) -> tuple[Path, sqlite3.Row] | None:
        for db_path in self._candidate_db_paths(now):
            if not db_path.exists():
                continue
            with self._connect_path(db_path) as conn:
                clauses = ["dedup_key = ?"]
                values: list[Any] = [dedup_key]
                if apply_id:
                    clauses.append("apply_id = ?")
                    values.append(apply_id)
                if channel_order_no:
                    clauses.append("channel_order_no = ?")
                    values.append(channel_order_no)
                row = conn.execute(
                    f"""
                    SELECT * FROM orders
                    WHERE {' OR '.join(clauses)}
                    ORDER BY
                        CASE
                            WHEN status IN ('FEEDBACK_DONE', 'MANUAL_FEEDBACK_REQUIRED') THEN 0
                            WHEN dedup_key = ? THEN 1
                            ELSE 2
                        END,
                        created_at
                    LIMIT 1
                    """,
                    values + [f"apply:{apply_id}" if apply_id else dedup_key],
                ).fetchone()
                if row:
                    return db_path, row
        return None

    def _collapse_duplicate_order_rows(
        self,
        conn: sqlite3.Connection,
        *,
        preferred_key: str,
        dedup_key: str,
        apply_id: str,
        channel_order_no: str,
    ) -> None:
        clauses = ["dedup_key = ?"]
        values: list[Any] = [dedup_key]
        if apply_id:
            clauses.append("apply_id = ?")
            values.append(apply_id)
        if channel_order_no:
            clauses.append("channel_order_no = ?")
            values.append(channel_order_no)
        duplicate_keys = [
            str(row["dedup_key"])
            for row in conn.execute(
                f"SELECT dedup_key FROM orders WHERE dedup_key <> ? AND ({' OR '.join(clauses)})",
                [preferred_key, *values],
            ).fetchall()
        ]
        for duplicate_key in duplicate_keys:
            conn.execute(
                "UPDATE status_history SET dedup_key = ? WHERE dedup_key = ?",
                (preferred_key, duplicate_key),
            )
            conn.execute("DELETE FROM orders WHERE dedup_key = ?", (duplicate_key,))

    def _append_history(
        self,
        conn: sqlite3.Connection,
        dedup_key: str,
        status: str,
        stamp: datetime,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO status_history (dedup_key, status, created_at, detail_json) VALUES (?, ?, ?, ?)",
            (dedup_key, status, _iso(stamp), _json_dumps(detail or {})),
        )

    def reserve_order(self, order: Mapping[str, Any], *, now: datetime | None = None) -> bool:
        stamp = now or _now()
        dedup_key = dedup_key_for_order(order)
        apply_id = canonical_apply_id(order)
        channel_order_no = canonical_channel_order_no(order)
        existing = self._find_existing_order_ref(
            dedup_key=dedup_key,
            apply_id=apply_id,
            channel_order_no=channel_order_no,
            now=stamp,
        )
        if existing:
            db_path, row = existing
            old_key = str(row["dedup_key"])
            preferred_key = f"apply:{apply_id}" if apply_id else old_key
            existing_payload = _json_loads_object(row["payload_json"])
            merged_payload = {**existing_payload, **dict(order)}
            with self._connect_path(db_path) as conn:
                if preferred_key != old_key:
                    conn.execute(
                        "UPDATE status_history SET dedup_key = ? WHERE dedup_key = ?",
                        (old_key, preferred_key),
                    )
                    conn.execute(
                        "DELETE FROM orders WHERE dedup_key = ?",
                        (preferred_key,),
                    )
                    conn.execute(
                        """
                        UPDATE orders
                        SET dedup_key = ?, apply_id = ?, channel_order_no = ?, payload_json = ?, updated_at = ?
                        WHERE dedup_key = ?
                        """,
                        (
                            preferred_key,
                            apply_id or str(row["apply_id"] or ""),
                            channel_order_no or str(row["channel_order_no"] or ""),
                            _json_dumps(merged_payload),
                            _iso(stamp),
                            old_key,
                        ),
                    )
                    conn.execute(
                        "UPDATE status_history SET dedup_key = ? WHERE dedup_key = ?",
                        (preferred_key, old_key),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE orders
                        SET apply_id = COALESCE(NULLIF(?, ''), apply_id),
                            channel_order_no = COALESCE(NULLIF(?, ''), channel_order_no),
                            payload_json = ?,
                            updated_at = ?
                        WHERE dedup_key = ?
                        """,
                        (apply_id, channel_order_no, _json_dumps(merged_payload), _iso(stamp), old_key),
                    )
                self._collapse_duplicate_order_rows(
                    conn,
                    preferred_key=preferred_key,
                    dedup_key=dedup_key,
                    apply_id=apply_id,
                    channel_order_no=channel_order_no,
                )
            return False
        with self._connect(stamp) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO orders (
                        dedup_key, apply_id, channel_order_no, status, payload_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'NEW', ?, ?, ?)
                    """,
                    (dedup_key, apply_id, channel_order_no, _json_dumps(order), _iso(stamp), _iso(stamp)),
                )
                self._append_history(conn, dedup_key, "NEW", stamp)
                return True
            except sqlite3.IntegrityError:
                return False

    def count_pending(self, *, now: datetime | None = None) -> int:
        with self._connect(now) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM orders WHERE status NOT IN (?, ?)",
                ("FEEDBACK_DONE", "MANUAL_FEEDBACK_REQUIRED"),
            ).fetchone()
            return int(row["count"])

    def count_pending_all(self, *, now: datetime | None = None) -> int:
        total = 0
        for db_path in self._candidate_db_paths(now):
            if not db_path.exists():
                continue
            with self._connect_path(db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM orders WHERE status NOT IN (?, ?)",
                    ("FEEDBACK_DONE", "MANUAL_FEEDBACK_REQUIRED"),
                ).fetchone()
                total += int(row["count"])
        return total

    def count_unaudited_all(self, *, now: datetime | None = None) -> int:
        total = 0
        for db_path in self._candidate_db_paths(now):
            if not db_path.exists():
                continue
            with self._connect_path(db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM orders WHERE status IN (?, ?)",
                    ("NEW", "AUDITING"),
                ).fetchone()
                total += int(row["count"])
        return total

    def list_pending_orders(self, *, now: datetime | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        stamp = now or _now()
        now_iso = _iso(stamp)
        for db_path in self._candidate_db_paths(now):
            if not db_path.exists():
                continue
            with self._connect_path(db_path) as conn:
                for row in conn.execute(
                    """
                    SELECT * FROM orders
                    WHERE status NOT IN (?, ?)
                    ORDER BY created_at, updated_at
                    """,
                    ("FEEDBACK_DONE", "MANUAL_FEEDBACK_REQUIRED"),
                ):
                    item = dict(row)
                    next_audit_after = str(item.get("next_audit_after") or "")
                    if item.get("status") == "NEW" and next_audit_after and next_audit_after > now_iso:
                        continue
                    item["_db_path"] = str(db_path)
                    rows.append(item)
        rows.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("updated_at") or "")))
        if limit is not None:
            return rows[:limit]
        return rows

    def set_status(
        self,
        dedup_key: str,
        status: str,
        *,
        now: datetime | None = None,
        task: Mapping[str, Any] | None = None,
        audit_result: Mapping[str, Any] | None = None,
        callback_request: Mapping[str, Any] | None = None,
        callback_response: Mapping[str, Any] | None = None,
        error_text: str | None = None,
        retry_count: int | None = None,
        audit_retry_count: int | None = None,
        next_audit_after: str | None = None,
        expected_attempt: int | None = None,
        db_path: Path | str | None = None,
    ) -> bool:
        stamp = now or _now()
        assignments = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, _iso(stamp)]
        if task is not None:
            assignments.append("task_json = ?")
            values.append(_json_dumps(task))
        if audit_result is not None:
            assignments.append("audit_result_json = ?")
            values.append(_json_dumps(audit_result))
        if callback_request is not None:
            assignments.append("callback_request_json = ?")
            values.append(_json_dumps(callback_request))
        if callback_response is not None:
            assignments.append("callback_response_json = ?")
            values.append(_json_dumps(callback_response))
        if error_text is not None:
            assignments.append("error_text = ?")
            values.append(redact_secret_text(error_text))
        if retry_count is not None:
            assignments.append("retry_count = ?")
            values.append(int(retry_count))
        if audit_retry_count is not None:
            assignments.append("audit_retry_count = ?")
            values.append(int(audit_retry_count))
        if next_audit_after is not None:
            assignments.append("next_audit_after = ?")
            values.append(next_audit_after)
        if status == "AUDIT_DONE":
            assignments.append("audited_at = ?")
            values.append(_iso(stamp))
        elif status == "FEEDBACK_DONE":
            assignments.append("feedback_done_at = ?")
            values.append(_iso(stamp))
        elif status == "MANUAL_FEEDBACK_REQUIRED":
            assignments.append("manual_required_at = ?")
            values.append(_iso(stamp))
        where = "dedup_key = ?"
        values.append(dedup_key)
        if expected_attempt is not None:
            where += " AND audit_attempt = ?"
            values.append(int(expected_attempt))
        for target_path in self._target_db_paths(now=stamp, db_path=db_path):
            if not target_path.exists():
                continue
            with self._connect_path(target_path) as conn:
                cursor = conn.execute(f"UPDATE orders SET {', '.join(assignments)} WHERE {where}", values)
                if cursor.rowcount == 1:
                    self._append_history(conn, dedup_key, status, stamp, {"error": redact_secret_text(error_text or "")})
                    return True
        return False

    def claim_order(
        self,
        dedup_key: str,
        *,
        now: datetime | None = None,
        db_path: Path | str | None = None,
        stale_after_seconds: int = 3600,
    ) -> bool:
        return self.claim_order_attempt(
            dedup_key,
            now=now,
            db_path=db_path,
            stale_after_seconds=stale_after_seconds,
        ) is not None

    def claim_order_attempt(
        self,
        dedup_key: str,
        *,
        now: datetime | None = None,
        db_path: Path | str | None = None,
        stale_after_seconds: int = 3600,
    ) -> int | None:
        stamp = now or _now()
        stale_before = _iso(datetime.fromtimestamp(stamp.timestamp() - int(stale_after_seconds)))
        lease_expires_at = _iso(datetime.fromtimestamp(stamp.timestamp() + int(stale_after_seconds)))
        for target_path in self._target_db_paths(now=stamp, db_path=db_path):
            if not target_path.exists():
                continue
            with self._connect_path(target_path) as conn:
                cursor = conn.execute(
                    """
                    UPDATE orders
                    SET status = 'AUDITING',
                        updated_at = ?,
                        audit_attempt = audit_attempt + 1,
                        lease_expires_at = ?,
                        next_audit_after = NULL
                    WHERE dedup_key = ? AND (
                        status = 'NEW'
                        OR (
                            status = 'AUDITING'
                            AND (
                                lease_expires_at IS NULL
                                OR lease_expires_at = ''
                                OR lease_expires_at <= ?
                                OR updated_at <= ?
                            )
                        )
                    )
                    """,
                    (_iso(stamp), lease_expires_at, dedup_key, _iso(stamp), stale_before),
                )
                if cursor.rowcount == 1:
                    row = conn.execute(
                        "SELECT audit_attempt FROM orders WHERE dedup_key = ?",
                        (dedup_key,),
                    ).fetchone()
                    self._append_history(conn, dedup_key, "AUDITING", stamp)
                    return int(row["audit_attempt"])
        return None

    def start_run_summary(self, summary: Mapping[str, Any], *, now: datetime | None = None) -> int:
        stamp = now or _now()
        running_summary = dict(summary)
        running_summary.setdefault("finished_at", "")
        running_summary.setdefault("next_loop_at", "")
        running_summary["run_status"] = "RUNNING"
        with self._connect(stamp) as conn:
            cursor = conn.execute(
                """
                INSERT INTO audit_runs (
                    run_status, started_at, finished_at, next_loop_at, heartbeat_only, pending_before,
                    fetched_count, recovered_count, reserved_count, skipped_duplicate_count,
                    skipped_non_pending_machine_status_count, processed_count, feedback_done_count,
                    callback_failed_count, manual_feedback_required_count, errors_json, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "RUNNING",
                    str(running_summary.get("started_at") or ""),
                    "",
                    "",
                    1 if running_summary.get("heartbeat_only") else 0,
                    int(running_summary.get("pending_before") or 0),
                    int(running_summary.get("fetched_count") or 0),
                    int(running_summary.get("recovered_count") or 0),
                    int(running_summary.get("reserved_count") or 0),
                    int(running_summary.get("skipped_duplicate_count") or 0),
                    int(running_summary.get("skipped_non_pending_machine_status_count") or 0),
                    int(running_summary.get("processed_count") or 0),
                    int(running_summary.get("feedback_done_count") or 0),
                    int(running_summary.get("callback_failed_count") or 0),
                    int(running_summary.get("manual_feedback_required_count") or 0),
                    _json_dumps(running_summary.get("errors") or []),
                    _json_dumps(running_summary),
                ),
            )
            return int(cursor.lastrowid)

    def record_run_summary(
        self,
        summary: Mapping[str, Any],
        *,
        now: datetime | None = None,
        run_id: int | None = None,
    ) -> None:
        stamp = now or _now()
        finished_summary = dict(summary)
        finished_summary["run_status"] = "FINISHED"
        with self._connect(stamp) as conn:
            if run_id:
                conn.execute(
                    """
                    UPDATE audit_runs
                    SET run_status = ?, started_at = ?, finished_at = ?, next_loop_at = ?,
                        heartbeat_only = ?, pending_before = ?, fetched_count = ?,
                        recovered_count = ?, reserved_count = ?, skipped_duplicate_count = ?,
                        skipped_non_pending_machine_status_count = ?, processed_count = ?,
                        feedback_done_count = ?, callback_failed_count = ?,
                        manual_feedback_required_count = ?, errors_json = ?, summary_json = ?
                    WHERE id = ?
                    """,
                    (
                        "FINISHED",
                        str(finished_summary.get("started_at") or ""),
                        str(finished_summary.get("finished_at") or ""),
                        str(finished_summary.get("next_loop_at") or ""),
                        1 if finished_summary.get("heartbeat_only") else 0,
                        int(finished_summary.get("pending_before") or 0),
                        int(finished_summary.get("fetched_count") or 0),
                        int(finished_summary.get("recovered_count") or 0),
                        int(finished_summary.get("reserved_count") or 0),
                        int(finished_summary.get("skipped_duplicate_count") or 0),
                        int(finished_summary.get("skipped_non_pending_machine_status_count") or 0),
                        int(finished_summary.get("processed_count") or 0),
                        int(finished_summary.get("feedback_done_count") or 0),
                        int(finished_summary.get("callback_failed_count") or 0),
                        int(finished_summary.get("manual_feedback_required_count") or 0),
                        _json_dumps(finished_summary.get("errors") or []),
                        _json_dumps(finished_summary),
                        int(run_id),
                    ),
                )
                return
            conn.execute(
                """
                INSERT INTO audit_runs (
                    run_status, started_at, finished_at, next_loop_at, heartbeat_only, pending_before,
                    fetched_count, recovered_count, reserved_count, skipped_duplicate_count,
                    skipped_non_pending_machine_status_count, processed_count, feedback_done_count,
                    callback_failed_count, manual_feedback_required_count, errors_json, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "FINISHED",
                    str(finished_summary.get("started_at") or ""),
                    str(finished_summary.get("finished_at") or ""),
                    str(finished_summary.get("next_loop_at") or ""),
                    1 if finished_summary.get("heartbeat_only") else 0,
                    int(finished_summary.get("pending_before") or 0),
                    int(finished_summary.get("fetched_count") or 0),
                    int(finished_summary.get("recovered_count") or 0),
                    int(finished_summary.get("reserved_count") or 0),
                    int(finished_summary.get("skipped_duplicate_count") or 0),
                    int(finished_summary.get("skipped_non_pending_machine_status_count") or 0),
                    int(finished_summary.get("processed_count") or 0),
                    int(finished_summary.get("feedback_done_count") or 0),
                    int(finished_summary.get("callback_failed_count") or 0),
                    int(finished_summary.get("manual_feedback_required_count") or 0),
                    _json_dumps(finished_summary.get("errors") or []),
                    _json_dumps(finished_summary),
                ),
            )

    def update_run_summary(
        self,
        summary: Mapping[str, Any],
        *,
        now: datetime | None = None,
        run_id: int | None = None,
    ) -> None:
        if not run_id:
            return
        stamp = now or _now()
        running_summary = dict(summary)
        with self._connect(stamp) as conn:
            conn.execute(
                """
                UPDATE audit_runs
                SET run_status = ?, started_at = ?, finished_at = ?, next_loop_at = ?,
                    heartbeat_only = ?, pending_before = ?, fetched_count = ?,
                    recovered_count = ?, reserved_count = ?, skipped_duplicate_count = ?,
                    skipped_non_pending_machine_status_count = ?, processed_count = ?,
                    feedback_done_count = ?, callback_failed_count = ?,
                    manual_feedback_required_count = ?, errors_json = ?, summary_json = ?
                WHERE id = ?
                """,
                (
                    "RUNNING",
                    str(running_summary.get("started_at") or ""),
                    str(running_summary.get("finished_at") or ""),
                    str(running_summary.get("next_loop_at") or ""),
                    1 if running_summary.get("heartbeat_only") else 0,
                    int(running_summary.get("pending_before") or 0),
                    int(running_summary.get("fetched_count") or 0),
                    int(running_summary.get("recovered_count") or 0),
                    int(running_summary.get("reserved_count") or 0),
                    int(running_summary.get("skipped_duplicate_count") or 0),
                    int(running_summary.get("skipped_non_pending_machine_status_count") or 0),
                    int(running_summary.get("processed_count") or 0),
                    int(running_summary.get("feedback_done_count") or 0),
                    int(running_summary.get("callback_failed_count") or 0),
                    int(running_summary.get("manual_feedback_required_count") or 0),
                    _json_dumps(running_summary.get("errors") or []),
                    _json_dumps(running_summary),
                    int(run_id),
                ),
            )


class JsonHttpClient:
    def __init__(self, base_url: str, auth_token: str, *, timeout_seconds: float = 30):
        self.base_url = self._normalize_base_url(base_url)
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds

    def _normalize_base_url(self, base_url: str) -> str:
        value = str(base_url).rstrip("/")
        for suffix in (
            "/api/cellPhone/26/apply/examinePage",
            "/api/cellPhone/26/apply/detail",
            "/api/cellPhone/26/apply/machineApproval",
            "/api/cellPhone/26/apply",
        ):
            if value.endswith(suffix):
                return value[: -len(suffix)].rstrip("/")
        return value

    def _endpoint(self, suffix: str) -> str:
        suffix = "/" + suffix.lstrip("/")
        if self.base_url.endswith(suffix):
            return self.base_url
        if self.base_url.endswith("/api") and suffix.startswith("/api/"):
            return self.base_url + suffix[len("/api") :]
        return self.base_url + suffix

    def post_json(self, suffix: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }
        if self.auth_token:
            headers["Authorization"] = self.auth_token
        request = urllib.request.Request(
            self._endpoint(suffix),
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                text = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(text) if text.strip() else None
                return {"ok": 200 <= response.status < 300, "http_status": response.status, "body": parsed}
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text) if text.strip() else None
            except json.JSONDecodeError:
                parsed = {"rawText": text}
            return {"ok": False, "http_status": exc.code, "body": parsed}


def _extract_records(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [dict(item) for item in body if isinstance(item, Mapping)]
    if not isinstance(body, Mapping):
        return []
    for key in ("records", "list", "rows", "data", "payload"):
        value = body.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, Mapping)]
        if isinstance(value, Mapping):
            nested = _extract_records(value)
            if nested:
                return nested
    return []


class GuobuExamineCollectorClient:
    def __init__(self, base_url: str, auth_token: str, *, timeout_seconds: float = 30):
        self.http = JsonHttpClient(base_url, auth_token, timeout_seconds=timeout_seconds)

    def _ensure_success(self, response: Mapping[str, Any], action: str) -> None:
        if response.get("ok") is False:
            raise RuntimeError(f"{action}_request_failed:http_status={response.get('http_status', 'unknown')}")
        body = response.get("body")
        if isinstance(body, Mapping):
            code = body.get("status", body.get("code"))
            if code is not None and str(code) not in {"0", "200", "success", "SUCCESS"}:
                raise RuntimeError(f"{action}_request_failed:body_status={code}")

    def heartbeat(self, **kwargs: Any) -> dict[str, Any]:
        payload = kwargs.get("payload") or build_examine_page_payload(current_page=1, page_size=1)
        response = self.http.post_json("/api/cellPhone/26/apply/examinePage", payload)
        self._ensure_success(response, "heartbeat")
        return response

    def fetch_orders(self, **kwargs: Any) -> list[dict[str, Any]]:
        payload = kwargs.get("payload") or build_examine_page_payload()
        response = self.http.post_json("/api/cellPhone/26/apply/examinePage", payload)
        self._ensure_success(response, "fetch_orders")
        return _extract_records(response.get("body"))

    def fetch_detail(self, apply_id: Any) -> dict[str, Any]:
        response = self.http.post_json(
            "/api/cellPhone/26/apply/detail",
            {"id": _maybe_int(apply_id)},
        )
        self._ensure_success(response, "fetch_detail")
        body = response.get("body")
        if isinstance(body, Mapping):
            payload = body.get("payload")
            if isinstance(payload, Mapping):
                return dict(payload)
            data = body.get("data")
            if isinstance(data, Mapping):
                return dict(data)
            return dict(body)
        return {}


class MachineApprovalCallbackClient:
    def __init__(self, base_url: str, auth_token: str, *, timeout_seconds: float = 30):
        self.http = JsonHttpClient(base_url, auth_token, timeout_seconds=timeout_seconds)

    def submit(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.http.post_json("/api/cellPhone/26/apply/machineApproval", request)

    def fetch_machine_status(self, apply_id: Any) -> dict[str, Any]:
        response = self.http.post_json(
            "/api/cellPhone/26/apply/detail",
            {"id": _maybe_int(apply_id)},
        )
        body = response.get("body")
        payload: Any = body
        if isinstance(body, Mapping):
            payload = body.get("payload", body.get("data", body))
        status_value = None
        if isinstance(payload, Mapping):
            status_value = payload.get("machineExamineStatus", payload.get("machine_examine_status"))
        return {
            "ok": response.get("ok"),
            "http_status": response.get("http_status"),
            "machineExamineStatus": status_value,
            "body": body,
        }


def _image_entry(value: Mapping[str, Any], *, title: str, index: int) -> dict[str, Any]:
    local_path = first_non_empty(value.get("local_path"), value.get("path"), value.get("filePath"))
    source_url = first_non_empty(value.get("source_url"), value.get("url"), value.get("ossUrl"), value.get("src"))
    fallback_seed = f"{title}|{index}|{source_url}|{local_path}"
    fallback_id = "img_" + hashlib.sha1(fallback_seed.encode("utf-8")).hexdigest()[:12]
    image_id = first_non_empty(value.get("image_id"), value.get("fileId"), fallback_id)
    return {
        "image_id": image_id,
        "title": first_non_empty(value.get("title"), title),
        "local_path": local_path,
        "source_url": source_url,
    }


def _coerce_image_items(items: Any) -> list[Any]:
    if isinstance(items, str):
        text = items.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            urls = re.findall(r"https?://[^\s\"'<>，。；;]+", text)
            return [{"url": url} for url in urls]
        return _coerce_image_items(parsed)
    if isinstance(items, Mapping):
        return [items]
    if isinstance(items, list):
        return items
    return []


def _image_group(items: Any, title: str) -> list[dict[str, Any]]:
    return [
        _image_entry(item, title=title, index=index)
        for index, item in enumerate(_coerce_image_items(items), 1)
        if isinstance(item, Mapping)
    ]


def _set_field_if_value(fields: dict[str, Any], key: str, value: Any) -> None:
    if value is not None and str(value).strip():
        fields.setdefault(key, value)


def build_audit_task(order: Mapping[str, Any], detail: Mapping[str, Any] | None = None) -> dict[str, Any]:
    detail = dict(detail or {})
    if detail.get("fields") and detail.get("image_groups"):
        task = dict(detail)
        task["apply_id"] = canonical_apply_id(detail) or canonical_apply_id(order)
        task["channel_order_no"] = canonical_channel_order_no(detail) or canonical_channel_order_no(order)
        return task

    merged = {**dict(order), **detail}
    apply_id = canonical_apply_id(merged)
    channel_order_no = canonical_channel_order_no(merged)
    fields = dict(merged.get("fields") or {})
    for source_key, target_key in (
        ("sn", "system_sn"),
        ("systemSn", "system_sn"),
        ("system_sn", "system_sn"),
        ("categoryName", "category_name"),
        ("category_name", "category_name"),
        ("goodsName", "product_name"),
        ("productName", "product_name"),
        ("product_type", "product_type"),
        ("customAddress", "address"),
        ("address", "address"),
        ("flowStatus", "flow_status"),
        ("status", "status"),
    ):
        value = merged.get(source_key)
        _set_field_if_value(fields, target_key, value)

    category_value = first_non_empty(
        merged.get("cateCodeName"),
        merged.get("categoryName"),
        merged.get("category_name"),
        fields.get("cate_code_name"),
        fields.get("category_name"),
        fields.get("product_type"),
    )
    _set_field_if_value(fields, "cate_code_name", category_value)
    _set_field_if_value(fields, "category_name", category_value)
    _set_field_if_value(fields, "product_type", category_value)
    fields.setdefault("apply_id", apply_id)
    fields.setdefault("channel_order_no", channel_order_no)

    image_groups = dict(merged.get("image_groups") or {})
    if not image_groups:
        image_groups = {
            "商品照片": _image_group(merged.get("goodsPhoto"), "商品照片"),
            "拆封照片": _image_group(merged.get("unsealingPhoto"), "拆封照片"),
            "SN码采集 / 激活照片": _image_group(merged.get("activatePhoto"), "SN码采集 / 激活照片"),
        }

    return {
        "task_id": channel_order_no or apply_id,
        "apply_id": apply_id,
        "channel_order_no": channel_order_no,
        "fields": fields,
        "image_groups": image_groups,
        "source": {
            "collector": "linux_auto_audit",
            "apply_id": apply_id,
            "raw_order": dict(order),
        },
    }


def validate_audit_task_ready(task: Mapping[str, Any]) -> list[str]:
    fields = task.get("fields") if isinstance(task.get("fields"), Mapping) else {}
    image_groups = task.get("image_groups") if isinstance(task.get("image_groups"), Mapping) else {}

    def usable_count(group_name: str) -> int:
        images = image_groups.get(group_name)
        if not isinstance(images, list):
            return 0
        return len(
            [
                item
                for item in images
                if isinstance(item, Mapping)
                and first_non_empty(item.get("source_url"), item.get("local_path"))
            ]
        )

    missing: list[str] = []
    if not str(task.get("channel_order_no") or "").strip():
        missing.append("channel_order_no")
    if not str(fields.get("product_type") or "").strip():
        missing.append("product_type")
    if not str(fields.get("system_sn") or "").strip():
        missing.append("system_sn")
    for group_name in ("商品照片", "拆封照片", "SN码采集 / 激活照片"):
        if usable_count(group_name) <= 0:
            missing.append(f"image_group:{group_name}")
    return missing


class MainlineGuobuAuditor:
    def __init__(self, config: LinuxAutoAuditConfig):
        self.config = config

    def audit_order(self, task: dict[str, Any], *, temp_dir: Path) -> dict[str, Any]:
        from tools.run_guobu_model_audit_v2 import audit_task_hybrid

        temp_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = temp_dir / "model_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        previous_env = {key: os.environ.get(key) for key in self._policy_env()}
        try:
            os.environ.update(self._policy_env())
            return audit_task_hybrid(
                os.environ["VISION_API_BASE_URL"],
                os.environ["VISION_API_KEY"],
                self.config.model,
                task,
                cache_dir=cache_dir,
                allow_review=True,
                allow_targeted_review=False,
                sn_policy_version=self.config.sn_policy_version,
                sn_barcode_mode=self.config.sn_barcode_mode,
            )
        finally:
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def _policy_env(self) -> dict[str, str]:
        return {
            "SN_POLICY_VERSION": self.config.sn_policy_version,
            "SN_BARCODE_MODE": self.config.sn_barcode_mode,
            "DIGITAL_ACTIVATION_EVIDENCE_MODE": "on",
            "PHOTO_AUTHENTICITY_MODE": self.config.photo_authenticity_mode,
            "PHOTO_AUTHENTICITY_NEW_RULE_ENABLED": self.config.photo_authenticity_new_rule_enabled,
            "PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED": self.config.photo_authenticity_local_tree_enabled,
            "PHOTO_AUTHENTICITY_LOCAL_TREE_CONFIRMATION_ENABLED": (
                self.config.photo_authenticity_local_tree_confirmation_enabled
            ),
        }


def _callback_succeeded(response: Mapping[str, Any]) -> bool:
    if response.get("ok") is False:
        return False
    status = response.get("http_status", response.get("httpStatus"))
    if status is not None:
        try:
            if not (200 <= int(status) < 300):
                return False
        except (TypeError, ValueError):
            return False
    body = response.get("body")
    if isinstance(body, Mapping):
        code = body.get("status", body.get("code"))
        if code is not None and str(code) not in {"0", "200", "success", "SUCCESS"}:
            return False
    return True


def _machine_status_reconciled_done(status_payload: Mapping[str, Any]) -> bool:
    candidate = {
        "machineExamineStatus": status_payload.get(
            "machineExamineStatus",
            status_payload.get("machine_examine_status"),
        )
    }
    return not machine_examine_status_is_pending(candidate)


class GuobuLinuxAutoAuditRunner:
    def __init__(
        self,
        *,
        config: LinuxAutoAuditConfig,
        store: MonthlyAuditStateStore | None = None,
        collector: Any | None = None,
        auditor: Any | None = None,
        callback_client: Any | None = None,
        now_fn: Callable[[], datetime] = _now,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.store = store or MonthlyAuditStateStore(config.state_dir)
        self.collector = collector or GuobuExamineCollectorClient(
            config.collector_base_url,
            config.collector_auth_token,
        )
        self.auditor = auditor or MainlineGuobuAuditor(config)
        self.callback_client = callback_client or MachineApprovalCallbackClient(
            config.approval_base_url,
            config.approval_auth_token,
        )
        self.now_fn = now_fn
        self.sleep_fn = sleep_fn

    def run_once(self) -> dict[str, Any]:
        now = self.now_fn()
        summary: dict[str, Any] = {
            "started_at": _iso(now),
            "heartbeat_only": False,
            "pending_before": self.store.count_pending_all(now=now),
            "fetched_count": 0,
            "recovered_count": 0,
            "reserved_count": 0,
            "skipped_duplicate_count": 0,
            "skipped_non_pending_machine_status_count": 0,
            "processed_count": 0,
            "feedback_done_count": 0,
            "callback_failed_count": 0,
            "manual_feedback_required_count": 0,
            "manual_feedback_required_orders": [],
            "order_results": [],
            "errors": [],
            "startup_evidence": collect_runtime_metadata(["tools.guobu_linux_auto_audit", "run_once"]),
        }
        run_id = self.store.start_run_summary(summary, now=now)

        if self.store.count_unaudited_all(now=now) > self.config.pending_heartbeat_threshold:
            summary["heartbeat_only"] = True
            try:
                self.collector.heartbeat(
                    payload=build_examine_page_payload(current_page=1, page_size=1)
                )
            except Exception as exc:
                summary["errors"].append(redact_secret_text(f"heartbeat_failed:{type(exc).__name__}:{exc}"))
            self.store.update_run_summary(summary, now=self.now_fn(), run_id=run_id)
        else:
            try:
                orders, fetched_count, skipped_non_pending = self._fetch_pending_machine_orders()
            except Exception as exc:
                summary["errors"].append(redact_secret_text(f"fetch_failed:{type(exc).__name__}:{exc}"))
                self.store.update_run_summary(summary, now=self.now_fn(), run_id=run_id)
                return self._finish_run_summary(summary, run_id=run_id)

            orders = list(orders or [])
            summary["fetched_count"] = fetched_count
            summary["skipped_non_pending_machine_status_count"] = skipped_non_pending
            for order in orders:
                self._reserve_fetched_order(order, now=self.now_fn(), summary=summary)
            self.store.update_run_summary(summary, now=self.now_fn(), run_id=run_id)

        pending_items = self.store.list_pending_orders(now=self.now_fn(), limit=None)
        for item in pending_items:
            self._process_pending_order(item, now=self.now_fn(), summary=summary)
            self.store.update_run_summary(summary, now=self.now_fn(), run_id=run_id)
        return self._finish_run_summary(summary, run_id=run_id)

    def _finish_run_summary(self, summary: dict[str, Any], *, run_id: int | None = None) -> dict[str, Any]:
        finished_at = self.now_fn()
        summary["finished_at"] = _iso(finished_at)
        summary["next_loop_at"] = _iso(finished_at + timedelta(seconds=int(self.config.poll_interval_seconds)))
        self.store.record_run_summary(summary, now=finished_at, run_id=run_id)
        return summary

    def _fetch_pending_machine_orders(self) -> tuple[list[dict[str, Any]], int, int]:
        selected: list[dict[str, Any]] = []
        fetched_count = 0
        skipped_non_pending = 0
        current_page = int(self.config.current_page)
        page_size = int(self.config.page_size)
        max_fetch_pages = max(0, int(self.config.max_fetch_pages))
        pages_seen = 0
        while True:
            if max_fetch_pages and pages_seen >= max_fetch_pages:
                break
            pages_seen += 1
            batch = self.collector.fetch_orders(
                payload=build_examine_page_payload(
                    current_page=current_page,
                    page_size=page_size,
                )
            )
            batch = list(batch or [])
            fetched_count += len(batch)
            if not batch:
                break
            for order in batch:
                if machine_examine_status_is_pending(order):
                    selected.append(order)
                else:
                    skipped_non_pending += 1
            if len(batch) < page_size:
                break
            current_page += 1
        return selected, fetched_count, skipped_non_pending

    def _process_pending_order(self, item: Mapping[str, Any], *, now: datetime, summary: dict[str, Any]) -> None:
        dedup_key = str(item.get("dedup_key") or "")
        status = str(item.get("status") or "")
        db_path = item.get("_db_path")
        payload = _json_loads_object(item.get("payload_json"))
        audit_result = _json_loads_object(item.get("audit_result_json"))
        callback_request = _json_loads_object(item.get("callback_request_json"))
        retry_count = int(item.get("retry_count") or 0)
        audit_retry_count = int(item.get("audit_retry_count") or 0)
        apply_id = first_non_empty(item.get("apply_id"), canonical_apply_id(payload))
        if not dedup_key:
            return
        try:
            if status in {"AUDIT_DONE", "FEEDBACK_RETRY_PENDING"} and audit_result:
                summary["recovered_count"] += 1
                self._feedback_result(
                    dedup_key,
                    apply_id=apply_id,
                    audit_result=audit_result,
                    order_context=payload,
                    now=now,
                    summary=summary,
                    callback_request=callback_request or None,
                    db_path=db_path,
                    existing_retry_count=retry_count,
                )
                return
            summary["recovered_count"] += 1
            self._audit_and_feedback(
                dedup_key,
                payload,
                now=now,
                summary=summary,
                db_path=db_path,
                existing_audit_retry_count=audit_retry_count,
            )
        except Exception as exc:
            error_text = redact_secret_text(f"pending_order_failed:{type(exc).__name__}:{exc}")
            summary["errors"].append(error_text)
            if "task_conversion_invalid" in error_text:
                summary["manual_feedback_required_count"] += 1
                self.store.set_status(
                    dedup_key,
                    "MANUAL_FEEDBACK_REQUIRED",
                    now=now,
                    error_text=error_text,
                    db_path=db_path,
                )
            else:
                self._record_audit_failure(
                    dedup_key,
                    error_text=error_text,
                    retry_count=audit_retry_count,
                    now=now,
                    summary=summary,
                    db_path=db_path,
                )

    def _reserve_fetched_order(self, order: Mapping[str, Any], *, now: datetime, summary: dict[str, Any]) -> None:
        try:
            inserted = self.store.reserve_order(order, now=now)
            if not inserted:
                summary["skipped_duplicate_count"] += 1
                return
            summary["reserved_count"] += 1
        except Exception as exc:
            summary["errors"].append(redact_secret_text(f"reserve_order_failed:{type(exc).__name__}:{exc}"))

    def _process_order(self, order: Mapping[str, Any], *, now: datetime, summary: dict[str, Any]) -> None:
        dedup_key = ""
        try:
            dedup_key = dedup_key_for_order(order)
            inserted = self.store.reserve_order(order, now=now)
            if not inserted:
                summary["skipped_duplicate_count"] += 1
                return
            summary["reserved_count"] += 1
            self._audit_and_feedback(dedup_key, order, now=now, summary=summary)
        except Exception as exc:
            error_text = redact_secret_text(f"order_failed:{type(exc).__name__}:{exc}")
            summary["errors"].append(error_text)
            if dedup_key:
                if "task_conversion_invalid" in error_text:
                    summary["manual_feedback_required_count"] += 1
                    self.store.set_status(
                        dedup_key,
                        "MANUAL_FEEDBACK_REQUIRED",
                        now=now,
                        error_text=error_text,
                    )
                else:
                    self._record_audit_failure(
                        dedup_key,
                        error_text=error_text,
                        retry_count=0,
                        now=now,
                        summary=summary,
                    )

    def _record_audit_failure(
        self,
        dedup_key: str,
        *,
        error_text: str,
        retry_count: int,
        now: datetime,
        summary: dict[str, Any],
        db_path: Path | str | None = None,
    ) -> None:
        next_retry_count = int(retry_count) + 1
        max_attempts = len(self.config.audit_retry_delays) + 1
        if next_retry_count >= max_attempts:
            summary["manual_feedback_required_count"] += 1
            self.store.set_status(
                dedup_key,
                "MANUAL_FEEDBACK_REQUIRED",
                now=now,
                error_text=error_text,
                audit_retry_count=next_retry_count,
                db_path=db_path,
            )
            return
        delay_index = min(next_retry_count - 1, len(self.config.audit_retry_delays) - 1)
        delay_seconds = int(self.config.audit_retry_delays[delay_index])
        next_audit_after = _iso(now + timedelta(seconds=delay_seconds))
        self.store.set_status(
            dedup_key,
            "NEW",
            now=now,
            error_text=error_text,
            audit_retry_count=next_retry_count,
            next_audit_after=next_audit_after,
            db_path=db_path,
        )

    def _audit_and_feedback(
        self,
        dedup_key: str,
        order: Mapping[str, Any],
        *,
        now: datetime,
        summary: dict[str, Any],
        db_path: Path | str | None = None,
        existing_audit_retry_count: int = 0,
    ) -> None:
        attempt = self.store.claim_order_attempt(
            dedup_key,
            now=now,
            db_path=db_path,
            stale_after_seconds=self.config.audit_lease_seconds,
        )
        if attempt is None:
            return
        apply_id = canonical_apply_id(order)
        detail = self.collector.fetch_detail(apply_id) if apply_id else {}
        task = build_audit_task(order, detail)
        missing = validate_audit_task_ready(task)
        if missing:
            raise RuntimeError(f"task_conversion_invalid:missing={','.join(missing)}")
        audit_result = normalize_audit_result_observability(
            self.auditor.audit_order(task, temp_dir=self.config.temp_dir)
        )
        summary["processed_count"] += 1
        if not self.store.set_status(
            dedup_key,
            "AUDIT_DONE",
            now=now,
            task=task,
            audit_result=audit_result,
            db_path=db_path,
            audit_retry_count=0,
            next_audit_after="",
            expected_attempt=attempt,
        ):
            summary["errors"].append(f"stale_audit_attempt_blocked:{dedup_key}:attempt={attempt}")
            return
        self._feedback_result(
            dedup_key,
            apply_id=apply_id or canonical_apply_id(task),
            audit_result=audit_result,
            order_context=task,
            now=now,
            summary=summary,
            db_path=db_path,
            expected_attempt=attempt,
        )

    def _feedback_result(
        self,
        dedup_key: str,
        *,
        apply_id: Any,
        audit_result: Mapping[str, Any],
        order_context: Mapping[str, Any] | None = None,
        now: datetime,
        summary: dict[str, Any],
        callback_request: Mapping[str, Any] | None = None,
        db_path: Path | str | None = None,
        existing_retry_count: int = 0,
        expected_attempt: int | None = None,
    ) -> None:
        audit_result = normalize_audit_result_observability(audit_result)
        request = dict(callback_request or build_machine_approval_request(apply_id, audit_result))
        summary.setdefault("order_results", []).append(
            build_order_result_summary(
                apply_id=apply_id,
                audit_result=audit_result,
                order_context=order_context,
            )
        )
        delays = list(self.config.callback_retry_delays)
        max_attempts = len(delays) + 1
        start_attempt = min(max(0, int(existing_retry_count)), max_attempts)
        if start_attempt >= max_attempts:
            response = {"ok": False, "error": "callback retry limit already reached"}
            summary["callback_failed_count"] += 1
            summary["manual_feedback_required_count"] += 1
            self.store.set_status(
                dedup_key,
                "MANUAL_FEEDBACK_REQUIRED",
                now=now,
                callback_request=request,
                callback_response=response,
                error_text=response["error"],
                retry_count=max_attempts,
                db_path=db_path,
            )
            return

        for index in range(start_attempt, max_attempts):
            attempts = index + 1
            try:
                response = self.callback_client.submit(dict(request))
                current_response = dict(response or {})
                if _callback_succeeded(current_response):
                    summary["feedback_done_count"] += 1
                    if not self.store.set_status(
                        dedup_key,
                        "FEEDBACK_DONE",
                        now=now,
                        callback_request=request,
                        callback_response=current_response,
                        retry_count=attempts,
                        db_path=db_path,
                        expected_attempt=expected_attempt,
                    ):
                        summary["errors"].append(f"stale_feedback_attempt_blocked:{dedup_key}:attempt={expected_attempt}")
                    return
            except Exception as exc:
                current_response = {"ok": False, "error": redact_secret_text(f"{type(exc).__name__}: {exc}")}

            try:
                status_payload = self.callback_client.fetch_machine_status(apply_id)
            except Exception as exc:
                status_payload = {
                    "ok": False,
                    "error": redact_secret_text(f"{type(exc).__name__}: {exc}"),
                }
            if _machine_status_reconciled_done(status_payload):
                reconciled_response = dict(current_response)
                reconciled_response["reconciled_after_failure"] = True
                reconciled_response["reconcile_status"] = {
                    "machineExamineStatus": status_payload.get("machineExamineStatus"),
                    "http_status": status_payload.get("http_status"),
                }
                summary["feedback_done_count"] += 1
                if not self.store.set_status(
                    dedup_key,
                    "FEEDBACK_DONE",
                    now=now,
                    callback_request=request,
                    callback_response=reconciled_response,
                    retry_count=attempts,
                    db_path=db_path,
                    expected_attempt=expected_attempt,
                ):
                    summary["errors"].append(f"stale_feedback_attempt_blocked:{dedup_key}:attempt={expected_attempt}")
                return

            self.store.set_status(
                dedup_key,
                "FEEDBACK_RETRY_PENDING",
                now=now,
                callback_request=request,
                callback_response=current_response,
                retry_count=attempts,
                db_path=db_path,
                expected_attempt=expected_attempt,
            )
            if index < len(delays):
                self.sleep_fn(delays[index])

        summary["callback_failed_count"] += 1
        summary["manual_feedback_required_count"] += 1
        append_manual_feedback_required_order(
            summary,
            dedup_key=dedup_key,
            apply_id=apply_id,
            order_context=order_context,
            reason=current_response.get("error") or current_response,
            stage="feedback",
        )
        self.store.set_status(
            dedup_key,
            "MANUAL_FEEDBACK_REQUIRED",
            now=now,
            callback_request=request,
            callback_response=current_response,
            error_text=redact_secret_text(current_response.get("error") or current_response),
            retry_count=max_attempts,
            db_path=db_path,
            expected_attempt=expected_attempt,
        )

    def run_forever(self, *, max_iterations: int | None = None) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        iteration = 0
        while True:
            summaries.append(self.run_once())
            iteration += 1
            if max_iterations is not None and iteration >= max_iterations:
                return summaries
            self.sleep_fn(self.config.poll_interval_seconds)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def config_from_env(args: argparse.Namespace) -> LinuxAutoAuditConfig:
    state_dir = Path(args.state_dir or _env("GUOBU_AUDIT_STATE_DIR", "data/audit_state"))
    temp_dir = Path(args.temp_dir or _env("GUOBU_AUDIT_TEMP_DIR", tempfile.gettempdir()))
    collector_base_url = args.collector_base_url or _env("GUOBU_COLLECTOR_BASE_URL")
    approval_base_url = args.approval_base_url or _env("GUOBU_APPROVAL_BASE_URL", collector_base_url)
    collector_auth_token = _env(args.collector_auth_env or "GUOBU_AUTH_TOKEN")
    approval_auth_token = _env(args.approval_auth_env or "MACHINE_APPROVAL_AUTH_TOKEN", collector_auth_token)
    if not collector_base_url:
        raise SystemExit("GUOBU_COLLECTOR_BASE_URL or --collector-base-url is required")
    if not collector_auth_token:
        raise SystemExit(f"{args.collector_auth_env or 'GUOBU_AUTH_TOKEN'} is required")
    return LinuxAutoAuditConfig(
        state_dir=state_dir,
        temp_dir=temp_dir,
        collector_base_url=collector_base_url,
        collector_auth_token=collector_auth_token,
        approval_base_url=approval_base_url,
        approval_auth_token=approval_auth_token,
        poll_interval_seconds=args.poll_interval_seconds,
        pending_heartbeat_threshold=args.pending_heartbeat_threshold,
        audit_lease_seconds=args.audit_lease_seconds,
        page_size=args.page_size,
        max_fetch_pages=args.max_fetch_pages,
        startup_safety_override=args.startup_safety_override or _env("GUOBU_STARTUP_SAFETY_OVERRIDE"),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Linux unattended Guobu audit loop.")
    parser.add_argument("--preflight-only", action="store_true", help="validate startup environment and exit")
    parser.add_argument("--once", action="store_true", help="run one collection/audit/feedback loop and exit")
    parser.add_argument(
        "--exit-nonzero-on-errors",
        action="store_true",
        help="exit with code 2 when a one-shot run has real errors; idle loops still exit 0",
    )
    parser.add_argument("--state-dir", default="")
    parser.add_argument("--temp-dir", default="")
    parser.add_argument("--collector-base-url", default="")
    parser.add_argument("--approval-base-url", default="")
    parser.add_argument("--collector-auth-env", default="GUOBU_AUTH_TOKEN")
    parser.add_argument("--approval-auth-env", default="MACHINE_APPROVAL_AUTH_TOKEN")
    parser.add_argument("--poll-interval-seconds", type=int, default=600)
    parser.add_argument("--pending-heartbeat-threshold", type=int, default=5)
    parser.add_argument("--audit-lease-seconds", type=int, default=3600)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument(
        "--max-fetch-pages",
        type=int,
        default=0,
        help="maximum backend pages to fetch per loop; 0 means keep fetching until the backend page is empty or short",
    )
    parser.add_argument(
        "--startup-safety-override",
        default="",
        help="allow dirty worktree only for shadow/local validation; production enforce blocks dirty worktrees",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    preflight = run_startup_preflight()
    if args.preflight_only:
        print(json.dumps(preflight, ensure_ascii=False, indent=2))
        return
    config = config_from_env(args)
    metadata = collect_runtime_metadata()
    assert_production_startup_allowed(
        photo_authenticity_mode=config.photo_authenticity_mode,
        startup_safety_override=config.startup_safety_override,
        metadata=metadata,
    )
    runner = GuobuLinuxAutoAuditRunner(config=config)
    if args.once:
        summary = runner.run_once()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.exit_nonzero_on_errors and (
            summary.get("errors") or int(summary.get("callback_failed_count") or 0) > 0
        ):
            raise SystemExit(2)
    else:
        while True:
            summary = runner.run_once()
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            runner.sleep_fn(config.poll_interval_seconds)


if __name__ == "__main__":
    main()
