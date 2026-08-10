from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import json
import sqlite3
import subprocess

import pytest

from tools.guobu_linux_auto_audit import (
    GuobuExamineCollectorClient,
    GuobuLinuxAutoAuditRunner,
    JsonHttpClient,
    LinuxAutoAuditConfig,
    MonthlyAuditStateStore,
    assert_production_startup_allowed,
    build_audit_task,
    build_examine_page_payload,
    build_machine_approval_request,
    collect_runtime_metadata,
    machine_examine_status_is_pending,
    normalize_audit_result_observability,
    parse_args,
    sn_barcode_observability_from_audit_result,
)
from tools.auto_audit_dashboard_server import HTML, load_status


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeCollector:
    def __init__(self, orders: list[dict[str, object]] | None = None, detail_map: dict[str, dict[str, object]] | None = None):
        self.orders = list(orders or [])
        self.detail_map = dict(detail_map or {})
        self.heartbeat_calls = 0
        self.fetch_calls = 0
        self.detail_calls: list[str] = []

    def heartbeat(self, **kwargs):
        self.heartbeat_calls += 1
        self.last_heartbeat_kwargs = kwargs
        return {"ok": True}

    def fetch_orders(self, **kwargs):
        self.fetch_calls += 1
        self.last_fetch_kwargs = kwargs
        return list(self.orders)

    def fetch_detail(self, apply_id):
        self.detail_calls.append(str(apply_id))
        return dict(self.detail_map.get(str(apply_id), {}))


class FakeAuditor:
    def __init__(self, result: dict[str, object] | None = None):
        self.result = dict(result or {
            "id": "1",
            "manual_flag": "否",
            "manual_reason_code": "",
            "manual_reason_cn": "",
            "manual_reason": "",
        })
        self.calls: list[dict[str, object]] = []

    def audit_order(self, task: dict[str, object], *, temp_dir: Path) -> dict[str, object]:
        self.calls.append({"task": task, "temp_dir": temp_dir})
        return dict(self.result)


class FakeCallbackClient:
    def __init__(
        self,
        outcomes: list[object] | None = None,
        status_outcomes: list[object] | None = None,
    ):
        self.outcomes = list(outcomes or [
            {"ok": True, "http_status": 200, "body": {"status": 200}},
        ])
        self.status_outcomes = list(status_outcomes or [])
        self.calls: list[dict[str, object]] = []
        self.status_calls: list[str] = []

    def submit(self, request: dict[str, object]) -> dict[str, object]:
        self.calls.append(dict(request))
        outcome = self.outcomes.pop(0) if self.outcomes else {"ok": True, "http_status": 200, "body": {"status": 200}}
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)

    def fetch_machine_status(self, apply_id) -> dict[str, object]:
        self.status_calls.append(str(apply_id))
        outcome = self.status_outcomes.pop(0) if self.status_outcomes else {}
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)


class FakeSleeper:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class FakeClock:
    def __init__(self):
        self._value = datetime(2026, 8, 7, 10, 0, 0)

    def now(self) -> datetime:
        return self._value


class FakeHttp:
    def __init__(self, response: dict[str, object]):
        self.response = response

    def post_json(self, suffix: str, payload: dict[str, object]) -> dict[str, object]:
        return dict(self.response)


def valid_minimal_order(apply_id: int, channel_order_no: str) -> dict[str, object]:
    return {
        "apply_id": apply_id,
        "channel_order_no": channel_order_no,
        "machineExamineStatus": None,
        "fields": {
            "system_sn": f"SN-{apply_id}",
            "product_type": "手机",
            "category_name": "手机",
        },
        "image_groups": {
            "商品照片": [{"source_url": f"https://example.invalid/{apply_id}/goods.jpg"}],
            "拆封照片": [{"source_url": f"https://example.invalid/{apply_id}/unbox.jpg"}],
            "SN码采集 / 激活照片": [{"source_url": f"https://example.invalid/{apply_id}/sn.jpg"}],
        },
    }


def test_build_examine_page_payload_filters_pending_and_null_machine_status_for_production_pending():
    payload = build_examine_page_payload(current_page=3, page_size=20)

    assert payload["currentPage"] == 3
    assert payload["pageSize"] == 20
    assert payload["status"] == 0
    assert payload["machineExamineStatus"] is None


def test_machine_examine_status_filter_rejects_any_non_pending_alias():
    assert machine_examine_status_is_pending({"machineExamineStatus": None}) is True
    assert machine_examine_status_is_pending({"machine_examine_status": "null"}) is True
    assert machine_examine_status_is_pending({"apply_id": 99, "channel_order_no": "order-99"}) is False
    assert machine_examine_status_is_pending({"machineExamineStatus": None, "machine_examine_status": 2}) is False
    assert machine_examine_status_is_pending({"machineExamineStatus": 1, "machine_examine_status": None}) is False


def test_state_store_uses_monthly_sqlite_and_apply_id_dedup(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)

    first_inserted = store.reserve_order(
        {
            "apply_id": 123,
            "channel_order_no": " test-order-dedup-001 ",
            "status": "NEW",
        },
        now=now,
    )
    second_inserted = store.reserve_order(
        {
            "apply_id": 123,
            "channel_order_no": "test-order-dedup-001",
            "status": "NEW",
        },
        now=now,
    )

    assert first_inserted is True
    assert second_inserted is False
    assert store.db_path_for(now).name == "audit_state_2026_08.sqlite"
    assert store.count_pending(now=now) == 1


def test_state_store_counts_pending_across_monthly_sqlite_files(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    august = datetime(2026, 8, 31, 23, 50, 0)
    september = datetime(2026, 9, 1, 0, 10, 0)

    store.reserve_order({"apply_id": 801, "channel_order_no": "order-801"}, now=august)
    store.reserve_order({"apply_id": 901, "channel_order_no": "order-901"}, now=september)

    assert store.count_pending(now=september) == 1
    assert store.count_pending_all(now=september) == 2


def test_state_store_rejects_duplicate_apply_id_across_months(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    august = datetime(2026, 8, 31, 23, 50, 0)
    september = datetime(2026, 9, 1, 0, 10, 0)

    assert store.reserve_order({"apply_id": 801, "channel_order_no": "order-801"}, now=august) is True
    assert store.reserve_order({"apply_id": 801, "channel_order_no": "order-801"}, now=september) is False

    assert store.count_pending_all(now=september) == 1


def test_state_store_merges_channel_only_order_when_apply_id_arrives_later(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)

    assert store.reserve_order({"channel_order_no": "order-merge-001"}, now=now) is True
    assert store.reserve_order({"apply_id": 9001, "channel_order_no": "order-merge-001"}, now=now) is False

    with sqlite3.connect(store.db_path_for(now)) as conn:
        rows = conn.execute(
            "SELECT dedup_key, apply_id, channel_order_no, status FROM orders"
        ).fetchall()

    assert rows == [("apply:9001", "9001", "order-merge-001", "NEW")]
    assert store.count_pending(now=now) == 1


def test_state_store_collapses_legacy_duplicate_channel_rows_when_apply_id_arrives(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    stamp = now.isoformat(timespec="seconds")
    db_path = store.db_path_for(now)
    tmp_path.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE orders (
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
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                audited_at TEXT,
                feedback_done_at TEXT,
                manual_required_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE TABLE status_history (id INTEGER PRIMARY KEY AUTOINCREMENT, dedup_key TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, detail_json TEXT)"
        )
        conn.execute(
            """
            INSERT INTO orders (dedup_key, apply_id, channel_order_no, status, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "channel:legacy-duplicate",
                "",
                "legacy-duplicate",
                "NEW",
                json.dumps({"channel_order_no": "legacy-duplicate"}),
                stamp,
                stamp,
            ),
        )
        conn.execute(
            """
            INSERT INTO orders (dedup_key, apply_id, channel_order_no, status, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-shadow-row",
                "",
                "legacy-duplicate",
                "NEW",
                json.dumps({"channel_order_no": "legacy-duplicate", "source": "old"}),
                stamp,
                stamp,
            ),
        )

    assert store.reserve_order({"apply_id": 9002, "channel_order_no": "legacy-duplicate"}, now=now) is False

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT dedup_key, apply_id, channel_order_no, status FROM orders ORDER BY dedup_key"
        ).fetchall()

    assert rows == [("apply:9002", "9002", "legacy-duplicate", "NEW")]


def test_state_store_preserves_finished_duplicate_when_collapsing_apply_identity(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    stamp = now.isoformat(timespec="seconds")
    db_path = store.db_path_for(now)
    tmp_path.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE orders (
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
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                audited_at TEXT,
                feedback_done_at TEXT,
                manual_required_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE TABLE status_history (id INTEGER PRIMARY KEY AUTOINCREMENT, dedup_key TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, detail_json TEXT)"
        )
        rows = [
            (
                "channel:finished-duplicate",
                "",
                "finished-duplicate",
                "FEEDBACK_DONE",
                json.dumps({"channel_order_no": "finished-duplicate", "finished": True}),
            ),
            (
                "apply:9003",
                "9003",
                "finished-duplicate",
                "NEW",
                json.dumps({"apply_id": 9003, "channel_order_no": "finished-duplicate"}),
            ),
        ]
        for row in rows:
            conn.execute(
                """
                INSERT INTO orders (dedup_key, apply_id, channel_order_no, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (*row, stamp, stamp),
            )

    assert store.reserve_order({"apply_id": 9003, "channel_order_no": "finished-duplicate"}, now=now) is False

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT dedup_key, apply_id, channel_order_no, status FROM orders ORDER BY dedup_key"
        ).fetchall()

    assert rows == [("apply:9003", "9003", "finished-duplicate", "FEEDBACK_DONE")]


def test_state_store_closes_sqlite_connections(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)

    store.reserve_order({"apply_id": 701, "channel_order_no": "order-701"}, now=now)
    assert store.count_pending(now=now) == 1

    store.db_path_for(now).unlink()
    assert not store.db_path_for(now).exists()


def test_state_store_does_not_double_claim_active_auditing_order(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    store.reserve_order({"apply_id": 711, "channel_order_no": "order-711"}, now=now)

    assert store.claim_order("apply:711", now=now, stale_after_seconds=3600) is True
    assert store.claim_order("apply:711", now=now + timedelta(seconds=30), stale_after_seconds=3600) is False
    assert store.claim_order("apply:711", now=now + timedelta(seconds=3700), stale_after_seconds=3600) is True


def test_state_store_attempt_cas_blocks_slow_stale_attempt_from_callback(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    started = datetime(2026, 8, 7, 12, 30, 0)
    stolen = started + timedelta(seconds=3700)
    store.reserve_order({"apply_id": 712, "channel_order_no": "order-712"}, now=started)

    first_attempt = store.claim_order_attempt("apply:712", now=started, stale_after_seconds=3600)
    second_attempt = store.claim_order_attempt("apply:712", now=stolen, stale_after_seconds=3600)

    assert first_attempt
    assert second_attempt
    assert first_attempt != second_attempt
    assert store.set_status(
        "apply:712",
        "FEEDBACK_DONE",
        now=stolen,
        expected_attempt=first_attempt,
    ) is False
    assert store.set_status(
        "apply:712",
        "FEEDBACK_DONE",
        now=stolen,
        expected_attempt=second_attempt,
    ) is True


def test_runner_only_heartbeats_when_pending_queue_exceeds_threshold(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    for index in range(6):
        store.reserve_order(valid_minimal_order(index + 1, f"order-{index + 1}"), now=now)

    collector = FakeCollector(orders=[{"apply_id": 99, "channel_order_no": "order-99"}])
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
        ),
        store=store,
        collector=collector,
        auditor=FakeAuditor(),
        callback_client=FakeCallbackClient(),
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["heartbeat_only"] is True
    assert collector.heartbeat_calls == 1
    assert collector.fetch_calls == 0
    assert summary["pending_before"] == 6
    assert summary["processed_count"] == 6
    assert summary["feedback_done_count"] == 6


def test_runner_processes_all_local_pending_orders_before_heartbeat_without_page_size_cap(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    for index in range(25):
        apply_id = index + 1
        store.reserve_order(valid_minimal_order(apply_id, f"order-{apply_id}"), now=now)

    collector = FakeCollector(orders=[valid_minimal_order(999, "order-999")])
    callback_client = FakeCallbackClient()
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
            page_size=10,
        ),
        store=store,
        collector=collector,
        auditor=FakeAuditor(),
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["heartbeat_only"] is True
    assert collector.fetch_calls == 0
    assert collector.heartbeat_calls == 1
    assert summary["processed_count"] == 25
    assert summary["feedback_done_count"] == 25
    assert len(callback_client.calls) == 25


def test_runner_processes_order_and_maps_refuse_message(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    collector = FakeCollector(
        orders=[{"apply_id": 2001, "channel_order_no": " test-order-sn-001 ", "machineExamineStatus": None}],
        detail_map={
            "2001": {
                "id": 2001,
                "jlPayOrder": " test-order-sn-001 ",
                "sn": "SN-001",
                "cateCodeName": "手机",
                "goodsPhoto": [{"url": "https://example.invalid/goods.jpg"}],
                "unsealingPhoto": [{"url": "https://example.invalid/unbox.jpg"}],
                "activatePhoto": [{"url": "https://example.invalid/sn.jpg"}],
            }
        },
    )
    auditor = FakeAuditor(
        {
            "id": "test-order-sn-001",
            "manual_flag": "是",
            "manual_reason_code": "SN_MISMATCH",
            "manual_reason_cn": "SN不一致",
            "manual_reason": "raw model reason should not leak",
        }
    )
    callback_client = FakeCallbackClient([{ "ok": True, "http_status": 200, "body": {"status": 200}}])
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
        ),
        store=store,
        collector=collector,
        auditor=auditor,
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["processed_count"] == 1
    assert collector.fetch_calls == 1
    assert auditor.calls
    assert callback_client.calls[0]["status"] == 2
    assert callback_client.calls[0]["refuseMessage"] == "SN不一致"
    assert "raw model reason" not in callback_client.calls[0]["refuseMessage"]
    assert summary["feedback_done_count"] == 1


def test_runner_summary_order_result_uses_final_reason_not_compliance_observation(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    collector = FakeCollector(
        orders=[{"apply_id": 2101, "channel_order_no": "order-2101", "machineExamineStatus": None}],
        detail_map={
            "2101": {
                "id": 2101,
                "jlPayOrder": "order-2101",
                "sn": "SN-2101",
                "cateCodeName": "手机",
                "goodsPhoto": [{"url": "https://example.invalid/goods.jpg"}],
                "unsealingPhoto": [{"url": "https://example.invalid/unbox.jpg"}],
                "activatePhoto": [{"url": "https://example.invalid/sn.jpg"}],
            }
        },
    )
    auditor = FakeAuditor(
        {
            "id": "order-2101",
            "manual_flag": "是",
            "manual_reason_code": "SN_MISMATCH",
            "manual_reason_cn": "SN不一致",
            "manual_reason": "系统SN与照片SN不一致",
            "evidence_summary": "商品照片、拆封照片和激活照片均符合合规照片要求。",
        }
    )
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
        ),
        store=store,
        collector=collector,
        auditor=auditor,
        callback_client=FakeCallbackClient(),
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert len(summary["order_results"]) == 1
    row = summary["order_results"][0]
    assert row["apply_id"] == "2101"
    assert row["channel_order_no"] == "order-2101"
    assert row["final_result"] == "不通过"
    assert row["new_final_result"] == "不通过"
    assert row["final_reason_code"] == "SN_MISMATCH"
    assert row["final_reason"] == "SN不一致"
    assert row["reason_code_cn"] == "SN不一致"
    assert row["system_sn"] == "SN-2101"
    assert row["model_sn"] == ""
    assert row["sn_barcode_result"] == ""
    assert row["barcode_attempted"] is False
    assert row["barcode_matched"] is False
    assert row["barcode_values"] == []
    assert row["barcode_error"] == ""
    assert row["barcode_rescued"] is False
    assert row["compliance_observation"] == ""


def test_sn_barcode_observability_reads_top_level_and_raw_results():
    raw_only = {
        "manual_flag": "是",
        "manual_reason_code": "SN_MISMATCH",
        "_raw": {
            "sn_barcode_result": {
                "matched": False,
                "decoded": [{"text": "WRONG-SN", "format": "CODE_128"}],
                "reject_reasons": ["no_barcode_match"],
            }
        },
    }
    top_level = normalize_audit_result_observability(
        {
            "manual_flag": "否",
            "sn_barcode_result": {
                "matched": True,
                "matched_text": "SN-OK",
                "match_type": "exact",
                "decoded": [{"text": "SN-OK", "format": "CODE_128"}],
            },
        }
    )

    assert sn_barcode_observability_from_audit_result(raw_only) == {
        "barcode_attempted": True,
        "barcode_matched": False,
        "barcode_values": ["WRONG-SN"],
        "barcode_error": "",
        "barcode_rescued": False,
    }
    assert top_level["barcode_attempted"] is True
    assert top_level["barcode_matched"] is True
    assert top_level["barcode_values"] == ["SN-OK"]
    assert top_level["barcode_rescued"] is True


def test_runner_records_barcode_rescue_and_pass_callback_in_sqlite(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    order = valid_minimal_order(2201, "order-2201")
    collector = FakeCollector(
        orders=[order],
        detail_map={
            "2201": {
                "id": 2201,
                "jlPayOrder": "order-2201",
                "sn": "SN-2201",
                "cateCodeName": "手机",
                "goodsPhoto": [{"url": "https://example.invalid/2201/goods.jpg"}],
                "unsealingPhoto": [{"url": "https://example.invalid/2201/unbox.jpg"}],
                "activatePhoto": [{"url": "https://example.invalid/2201/sn.jpg"}],
            }
        },
    )
    auditor = FakeAuditor(
        {
            "id": "order-2201",
            "manual_flag": "否",
            "manual_reason_code": "",
            "manual_reason_cn": "",
            "manual_reason": "",
            "system_sn": "SN-2201",
            "observed_sn": "SN-2201",
            "model_sn": "WRONG-2201",
            "sn_match": True,
            "_raw": {
                "sn_barcode_result": {
                    "matched": True,
                    "match_type": "exact",
                    "matched_text": "SN-2201",
                    "decoded": [{"text": "SN-2201", "format": "CODE_128"}],
                }
            },
        }
    )
    callback_client = FakeCallbackClient()
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
        ),
        store=store,
        collector=collector,
        auditor=auditor,
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert callback_client.calls[0]["status"] == 1
    result = summary["order_results"][0]
    assert result["new_final_result"] == "通过"
    assert result["model_sn"] == "WRONG-2201"
    assert result["barcode_attempted"] is True
    assert result["barcode_matched"] is True
    assert result["barcode_values"] == ["SN-2201"]
    assert result["barcode_rescued"] is True
    with sqlite3.connect(store.db_path_for(now)) as conn:
        stored_json = conn.execute(
            "SELECT audit_result_json FROM orders WHERE dedup_key = ?",
            ("apply:2201",),
        ).fetchone()[0]
    stored = json.loads(stored_json)
    assert stored["barcode_attempted"] is True
    assert stored["barcode_matched"] is True
    assert stored["barcode_values"] == ["SN-2201"]
    assert stored["barcode_rescued"] is True


def test_runner_records_barcode_miss_and_manual_callback_in_sqlite(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    order = valid_minimal_order(2202, "order-2202")
    collector = FakeCollector(
        orders=[order],
        detail_map={
            "2202": {
                "id": 2202,
                "jlPayOrder": "order-2202",
                "sn": "SN-2202",
                "cateCodeName": "手机",
                "goodsPhoto": [{"url": "https://example.invalid/2202/goods.jpg"}],
                "unsealingPhoto": [{"url": "https://example.invalid/2202/unbox.jpg"}],
                "activatePhoto": [{"url": "https://example.invalid/2202/sn.jpg"}],
            }
        },
    )
    auditor = FakeAuditor(
        {
            "id": "order-2202",
            "manual_flag": "是",
            "manual_reason_code": "SN_MISMATCH",
            "manual_reason_cn": "SN不一致",
            "manual_reason": "SN不一致",
            "system_sn": "SN-2202",
            "observed_sn": "WRONG-2202",
            "sn_match": False,
            "_raw": {
                "sn_barcode_result": {
                    "matched": False,
                    "decoded": [{"text": "OTHER-2202", "format": "CODE_128"}],
                    "reject_reasons": ["no_barcode_match"],
                }
            },
        }
    )
    callback_client = FakeCallbackClient()
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
        ),
        store=store,
        collector=collector,
        auditor=auditor,
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert callback_client.calls[0]["status"] == 2
    assert callback_client.calls[0]["refuseMessage"] == "SN不一致"
    result = summary["order_results"][0]
    assert result["new_final_result"] == "不通过"
    assert result["model_sn"] == "WRONG-2202"
    assert result["barcode_attempted"] is True
    assert result["barcode_matched"] is False
    assert result["barcode_values"] == ["OTHER-2202"]
    assert result["barcode_rescued"] is False
    with sqlite3.connect(store.db_path_for(now)) as conn:
        stored_json = conn.execute(
            "SELECT audit_result_json FROM orders WHERE dedup_key = ?",
            ("apply:2202",),
        ).fetchone()[0]
    stored = json.loads(stored_json)
    assert stored["barcode_attempted"] is True
    assert stored["barcode_matched"] is False
    assert stored["barcode_values"] == ["OTHER-2202"]


def test_runner_skips_non_pending_machine_status_and_fetches_enough_pending_orders(tmp_path: Path):
    class PagedCollector(FakeCollector):
        def __init__(self):
            super().__init__(
                detail_map={
                    str(apply_id): {
                        "id": apply_id,
                        "jlPayOrder": f"order-{apply_id}",
                        "sn": f"SN-{apply_id}",
                        "cateCodeName": "手机",
                        "goodsPhoto": [{"url": f"https://example.invalid/{apply_id}/goods.jpg"}],
                        "unsealingPhoto": [{"url": f"https://example.invalid/{apply_id}/unbox.jpg"}],
                        "activatePhoto": [{"url": f"https://example.invalid/{apply_id}/sn.jpg"}],
                    }
                    for apply_id in (101, 102, 103, 201, 202, 203)
                }
            )
            self.pages = {
                1: [
                    {"apply_id": 101, "channel_order_no": "order-101", "machineExamineStatus": 2},
                    {"apply_id": 102, "channel_order_no": "order-102", "machineExamineStatus": 1},
                    {"apply_id": 103, "channel_order_no": "order-103", "machineExamineStatus": 2},
                ],
                2: [
                    {"apply_id": 201, "channel_order_no": "order-201", "machineExamineStatus": None},
                    {"apply_id": 202, "channel_order_no": "order-202", "machineExamineStatus": None},
                    {"apply_id": 203, "channel_order_no": "order-203", "machineExamineStatus": None},
                ],
            }

        def fetch_orders(self, **kwargs):
            self.fetch_calls += 1
            payload = kwargs.get("payload") or {}
            return list(self.pages.get(int(payload.get("currentPage", 1)), []))

    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    collector = PagedCollector()
    callback_client = FakeCallbackClient()
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
            page_size=3,
        ),
        store=store,
        collector=collector,
        auditor=FakeAuditor(),
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert collector.fetch_calls == 3
    assert summary["fetched_count"] == 6
    assert summary["skipped_non_pending_machine_status_count"] == 3
    assert summary["processed_count"] == 3
    assert [call["applyId"] for call in callback_client.calls] == [201, 202, 203]


def test_runner_skips_orders_missing_machine_examine_status_without_inserting(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    collector = FakeCollector(orders=[{"apply_id": 204, "channel_order_no": "order-204"}])
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
        ),
        store=store,
        collector=collector,
        auditor=FakeAuditor(),
        callback_client=FakeCallbackClient(),
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["fetched_count"] == 1
    assert summary["skipped_non_pending_machine_status_count"] == 1
    assert summary["reserved_count"] == 0
    assert summary["processed_count"] == 0
    assert load_status(store.db_path_for(now))["total"] == 0


def test_runner_fetches_all_pending_orders_without_page_size_processing_cap(tmp_path: Path):
    class ManyPagedCollector(FakeCollector):
        def __init__(self):
            apply_ids = range(1, 46)
            super().__init__(
                detail_map={
                    str(apply_id): {
                        "id": apply_id,
                        "jlPayOrder": f"order-{apply_id}",
                        "sn": f"SN-{apply_id}",
                        "cateCodeName": "手机",
                        "goodsPhoto": [{"url": f"https://example.invalid/{apply_id}/goods.jpg"}],
                        "unsealingPhoto": [{"url": f"https://example.invalid/{apply_id}/unbox.jpg"}],
                        "activatePhoto": [{"url": f"https://example.invalid/{apply_id}/sn.jpg"}],
                    }
                    for apply_id in apply_ids
                }
            )

        def fetch_orders(self, **kwargs):
            self.fetch_calls += 1
            payload = kwargs.get("payload") or {}
            current_page = int(payload.get("currentPage", 1))
            page_size = int(payload.get("pageSize", 20))
            start = (current_page - 1) * page_size + 1
            end = min(start + page_size, 46)
            return [
                {"apply_id": apply_id, "channel_order_no": f"order-{apply_id}", "machineExamineStatus": None}
                for apply_id in range(start, end)
            ]

    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    callback_client = FakeCallbackClient()
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
            page_size=20,
        ),
        store=store,
        collector=ManyPagedCollector(),
        auditor=FakeAuditor(),
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["fetched_count"] == 45
    assert summary["processed_count"] == 45
    assert summary["feedback_done_count"] == 45
    assert len(callback_client.calls) == 45


def test_runner_reserves_full_fetched_batch_before_first_audit(tmp_path: Path):
    class StoreAwareAuditor(FakeAuditor):
        def __init__(self, store: MonthlyAuditStateStore, stamp: datetime):
            super().__init__()
            self.store = store
            self.stamp = stamp
            self.total_rows_seen: list[int] = []

        def audit_order(self, task: dict[str, object], *, temp_dir: Path) -> dict[str, object]:
            dashboard = load_status(self.store.db_path_for(self.stamp))
            self.total_rows_seen.append(int(dashboard["total"]))
            return super().audit_order(task, temp_dir=temp_dir)

    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    orders = [valid_minimal_order(apply_id, f"order-{apply_id}") for apply_id in (301, 302, 303)]
    collector = FakeCollector(orders=orders)
    auditor = StoreAwareAuditor(store, now)
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
        ),
        store=store,
        collector=collector,
        auditor=auditor,
        callback_client=FakeCallbackClient(),
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["reserved_count"] == 3
    assert summary["processed_count"] == 3
    assert auditor.total_rows_seen[0] == 3


def test_refuse_message_uses_standard_code_mapping_before_raw_cn():
    request = build_machine_approval_request(
        2002,
        {
            "manual_flag": "是",
            "manual_reason_code": "SN_MISMATCH",
            "manual_reason_cn": "raw model reason should not be sent to backend",
        },
    )

    assert request["status"] == 2
    assert request["refuseMessage"] == "SN不一致"


def test_refuse_message_maps_address_reason_to_utf8_chinese_text():
    request = build_machine_approval_request(
        2003,
        {
            "manual_flag": "是",
            "manual_reason_code": "ADDRESS_TOO_COARSE",
            "manual_reason_cn": "瀹剁數鍦板潃涓嶅绮剧‘",
        },
    )

    assert request["status"] == 2
    assert request["refuseMessage"] == "收货地址不符合要求"
    assert "瀹" not in request["refuseMessage"]


def test_runner_recovers_previous_month_new_order_before_fetching_new(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    august = datetime(2026, 8, 31, 23, 50, 0)
    september = datetime(2026, 9, 1, 0, 10, 0)
    store.reserve_order(valid_minimal_order(8801, "order-8801"), now=august)
    collector = FakeCollector(orders=[])
    auditor = FakeAuditor()
    callback_client = FakeCallbackClient()
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
        ),
        store=store,
        collector=collector,
        auditor=auditor,
        callback_client=callback_client,
        now_fn=lambda: september,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["recovered_count"] == 1
    assert summary["processed_count"] == 1
    assert collector.detail_calls == ["8801"]
    assert callback_client.calls[0]["status"] == 1
    with sqlite3.connect(store.db_path_for(august)) as conn:
        status = conn.execute("SELECT status FROM orders WHERE dedup_key = ?", ("apply:8801",)).fetchone()[0]
    assert status == "FEEDBACK_DONE"


def test_runner_recovers_audit_done_by_feedback_without_rerunning_model(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    store.reserve_order({"apply_id": 9901, "channel_order_no": "order-9901"}, now=now)
    store.set_status(
        "apply:9901",
        "AUDIT_DONE",
        now=now,
        audit_result={"manual_flag": "是", "manual_reason_code": "SN_MISMATCH"},
    )
    auditor = FakeAuditor()
    callback_client = FakeCallbackClient()
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
        ),
        store=store,
        collector=FakeCollector(orders=[]),
        auditor=auditor,
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["recovered_count"] == 1
    assert summary["processed_count"] == 0
    assert auditor.calls == []
    assert callback_client.calls[0] == {"applyId": 9901, "status": 2, "refuseMessage": "SN不一致"}


def test_runner_resumes_callback_retry_count_without_exceeding_three_attempts(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    store.reserve_order({"apply_id": 9911, "channel_order_no": "order-9911"}, now=now)
    store.set_status(
        "apply:9911",
        "FEEDBACK_RETRY_PENDING",
        now=now,
        audit_result={"manual_flag": "是", "manual_reason_code": "SN_MISMATCH"},
        callback_request={"applyId": 9911, "status": 2, "refuseMessage": "SN不一致"},
        retry_count=2,
    )
    callback_client = FakeCallbackClient([RuntimeError("third failure")])
    sleeper = FakeSleeper()
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
        ),
        store=store,
        collector=FakeCollector(orders=[]),
        auditor=FakeAuditor(),
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=sleeper,
    )

    summary = runner.run_once()

    assert len(callback_client.calls) == 1
    assert sleeper.calls == []
    assert summary["manual_feedback_required_count"] == 1
    with sqlite3.connect(store.db_path_for(now)) as conn:
        row = conn.execute(
            "SELECT status, retry_count FROM orders WHERE dedup_key = ?",
            ("apply:9911",),
        ).fetchone()
    assert row == ("MANUAL_FEEDBACK_REQUIRED", 3)


def test_runner_updates_recovered_order_in_its_original_month_db(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    august = datetime(2026, 8, 31, 23, 50, 0)
    september = datetime(2026, 9, 1, 0, 10, 0)
    store.reserve_order(valid_minimal_order(7701, "order-7701"), now=august)
    store.set_status("apply:7701", "AUDITING", now=august)
    with store._connect_path(store.db_path_for(september)) as conn:
        conn.execute(
            """
            INSERT INTO orders (
                dedup_key, apply_id, channel_order_no, status, payload_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "apply:7701",
                "7701",
                "order-7701",
                "FEEDBACK_DONE",
                "{}",
                september.isoformat(timespec="seconds"),
                september.isoformat(timespec="seconds"),
            ),
        )
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
        ),
        store=store,
        collector=FakeCollector(orders=[]),
        auditor=FakeAuditor(),
        callback_client=FakeCallbackClient(),
        now_fn=lambda: september,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["recovered_count"] == 1
    with sqlite3.connect(store.db_path_for(august)) as conn:
        august_status = conn.execute("SELECT status FROM orders WHERE dedup_key = ?", ("apply:7701",)).fetchone()[0]
    with sqlite3.connect(store.db_path_for(september)) as conn:
        september_status = conn.execute("SELECT status FROM orders WHERE dedup_key = ?", ("apply:7701",)).fetchone()[0]
    assert august_status == "FEEDBACK_DONE"
    assert september_status == "FEEDBACK_DONE"


def test_runner_retries_callback_three_times_then_marks_manual(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    collector = FakeCollector(
        orders=[{"apply_id": 3001, "channel_order_no": "order-3001", "machineExamineStatus": None}],
        detail_map={
            "3001": {
                "id": 3001,
                "jlPayOrder": "order-3001",
                "sn": "SN-3001",
                "cateCodeName": "手机",
                "goodsPhoto": [{"url": "https://example.invalid/goods.jpg"}],
                "unsealingPhoto": [{"url": "https://example.invalid/unbox.jpg"}],
                "activatePhoto": [{"url": "https://example.invalid/sn.jpg"}],
            }
        },
    )
    auditor = FakeAuditor(
        {
            "id": "order-3001",
            "manual_flag": "是",
            "manual_reason_code": "MODEL_UNCERTAIN",
            "manual_reason_cn": "图片信息无法确认",
            "manual_reason": "uncertain",
        }
    )
    callback_client = FakeCallbackClient([
        RuntimeError("first failure"),
        RuntimeError("second failure"),
        RuntimeError("third failure"),
    ])
    sleeper = FakeSleeper()
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
        ),
        store=store,
        collector=collector,
        auditor=auditor,
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=sleeper,
    )

    summary = runner.run_once()

    assert len(callback_client.calls) == 3
    assert sleeper.calls == [5, 30]
    assert summary["manual_feedback_required_count"] == 1
    assert summary["callback_failed_count"] == 1
    assert summary["manual_feedback_required_orders"] == [
        {
            "apply_id": "3001",
            "channel_order_no": "order-3001",
            "dedup_key": "apply:3001",
            "reason": "RuntimeError: third failure",
            "stage": "feedback",
        }
    ]
    with sqlite3.connect(store.db_path_for(now)) as conn:
        statuses = [
            row[0]
            for row in conn.execute(
                "SELECT status FROM status_history WHERE dedup_key = ? ORDER BY id",
                ("apply:3001",),
            )
        ]
    assert "FEEDBACK_RETRY_PENDING" in statuses
    assert statuses[-1] == "MANUAL_FEEDBACK_REQUIRED"


def test_runner_reconciles_timeout_after_success_before_retrying_callback(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    collector = FakeCollector(
        orders=[{"apply_id": 3101, "channel_order_no": "order-3101", "machineExamineStatus": None}],
        detail_map={
            "3101": {
                "id": 3101,
                "jlPayOrder": "order-3101",
                "sn": "SN-3101",
                "cateCodeName": "手机",
                "goodsPhoto": [{"url": "https://example.invalid/goods.jpg"}],
                "unsealingPhoto": [{"url": "https://example.invalid/unbox.jpg"}],
                "activatePhoto": [{"url": "https://example.invalid/sn.jpg"}],
            }
        },
    )
    callback_client = FakeCallbackClient(
        [TimeoutError("write timed out after backend accepted it")],
        status_outcomes=[{"machineExamineStatus": 1}],
    )
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
        ),
        store=store,
        collector=collector,
        auditor=FakeAuditor(),
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert len(callback_client.calls) == 1
    assert callback_client.status_calls == ["3101"]
    assert summary["feedback_done_count"] == 1
    assert summary["callback_failed_count"] == 0
    with sqlite3.connect(store.db_path_for(now)) as conn:
        row = conn.execute(
            "SELECT status, retry_count FROM orders WHERE dedup_key = ?",
            ("apply:3101",),
        ).fetchone()
    assert row == ("FEEDBACK_DONE", 1)


def test_runner_retries_5xx_after_reconcile_still_pending_then_dead_letters(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    collector = FakeCollector(
        orders=[{"apply_id": 3102, "channel_order_no": "order-3102", "machineExamineStatus": None}],
        detail_map={
            "3102": {
                "id": 3102,
                "jlPayOrder": "order-3102",
                "sn": "SN-3102",
                "cateCodeName": "手机",
                "goodsPhoto": [{"url": "https://example.invalid/goods.jpg"}],
                "unsealingPhoto": [{"url": "https://example.invalid/unbox.jpg"}],
                "activatePhoto": [{"url": "https://example.invalid/sn.jpg"}],
            }
        },
    )
    callback_client = FakeCallbackClient(
        [
            {"ok": False, "http_status": 500, "body": {"status": 500}},
            {"ok": False, "http_status": 500, "body": {"status": 500}},
            {"ok": False, "http_status": 500, "body": {"status": 500}},
        ],
        status_outcomes=[
            {"machineExamineStatus": None},
            {"machineExamineStatus": None},
            {"machineExamineStatus": None},
        ],
    )
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
        ),
        store=store,
        collector=collector,
        auditor=FakeAuditor(),
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert len(callback_client.calls) == 3
    assert callback_client.status_calls == ["3102", "3102", "3102"]
    assert summary["callback_failed_count"] == 1
    assert summary["manual_feedback_required_count"] == 1
    with sqlite3.connect(store.db_path_for(now)) as conn:
        status = conn.execute(
            "SELECT status FROM orders WHERE dedup_key = ?",
            ("apply:3102",),
        ).fetchone()[0]
    assert status == "MANUAL_FEEDBACK_REQUIRED"


def test_runner_limits_non_conversion_audit_failures_instead_of_returning_new_forever(tmp_path: Path):
    class FailingAuditor(FakeAuditor):
        def audit_order(self, task: dict[str, object], *, temp_dir: Path) -> dict[str, object]:
            self.calls.append({"task": task, "temp_dir": temp_dir})
            raise RuntimeError("model service unavailable")

    store = MonthlyAuditStateStore(tmp_path)
    base = datetime(2026, 8, 7, 12, 30, 0)
    collector = FakeCollector(
        orders=[{"apply_id": 3201, "channel_order_no": "order-3201", "machineExamineStatus": None}],
        detail_map={
            "3201": {
                "id": 3201,
                "jlPayOrder": "order-3201",
                "sn": "SN-3201",
                "cateCodeName": "手机",
                "goodsPhoto": [{"url": "https://example.invalid/goods.jpg"}],
                "unsealingPhoto": [{"url": "https://example.invalid/unbox.jpg"}],
                "activatePhoto": [{"url": "https://example.invalid/sn.jpg"}],
            }
        },
    )
    auditor = FailingAuditor()
    clock = {"now": base}
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_retry_delays=(1, 1),
        ),
        store=store,
        collector=collector,
        auditor=auditor,
        callback_client=FakeCallbackClient(),
        now_fn=lambda: clock["now"],
        sleep_fn=lambda _seconds: None,
    )

    first = runner.run_once()
    clock["now"] = base + timedelta(seconds=1)
    second = runner.run_once()
    clock["now"] = base + timedelta(seconds=2)
    third = runner.run_once()

    assert first["manual_feedback_required_count"] == 0
    assert second["manual_feedback_required_count"] == 0
    assert third["manual_feedback_required_count"] == 1
    assert len(auditor.calls) == 3
    with sqlite3.connect(store.db_path_for(base)) as conn:
        row = conn.execute(
            "SELECT status, audit_retry_count FROM orders WHERE dedup_key = ?",
            ("apply:3201",),
        ).fetchone()
    assert row == ("MANUAL_FEEDBACK_REQUIRED", 3)


def test_runner_does_not_raise_when_fetch_returns_no_orders(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    collector = FakeCollector(orders=[])
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
        ),
        store=store,
        collector=collector,
        auditor=FakeAuditor(),
        callback_client=FakeCallbackClient(),
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["heartbeat_only"] is False
    assert summary["fetched_count"] == 0
    assert summary["processed_count"] == 0


def test_dashboard_html_shows_order_collection_time():
    assert "采集时间" in HTML
    assert "${text(row.created_at)}" in HTML


def test_auto_audit_dashboard_server_is_tracked_for_deployment():
    result = subprocess.run(
        ["git", "ls-files", "--", "tools/auto_audit_dashboard_server.py"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.stdout.strip() == "tools/auto_audit_dashboard_server.py"


def test_dashboard_pending_to_audit_count_counts_only_new_and_auditing(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    for apply_id in range(1, 6):
        store.reserve_order(valid_minimal_order(apply_id, f"order-{apply_id}"), now=now)

    store.claim_order("apply:2", now=now, stale_after_seconds=600)
    store.set_status("apply:3", "AUDIT_DONE", now=now)
    store.set_status("apply:4", "FEEDBACK_RETRY_PENDING", now=now)
    store.set_status("apply:5", "FEEDBACK_DONE", now=now)

    dashboard = load_status(store.db_path_for(now))

    assert dashboard["total"] == 5
    assert dashboard["pending_to_audit_count"] == 2
    assert "data.pending_to_audit_count || 0" in HTML


def test_dashboard_exposes_sqlite_queue_metrics_and_last_run_counts(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    for apply_id in range(1, 7):
        store.reserve_order(valid_minimal_order(apply_id, f"order-{apply_id}"), now=now)

    store.claim_order("apply:2", now=now, stale_after_seconds=600)
    store.set_status("apply:3", "AUDIT_DONE", now=now)
    store.set_status("apply:4", "FEEDBACK_RETRY_PENDING", now=now)
    store.set_status("apply:5", "MANUAL_FEEDBACK_REQUIRED", now=now)
    store.set_status("apply:6", "FEEDBACK_DONE", now=now)
    store.record_run_summary(
        {
            "started_at": "2026-08-07T12:30:00",
            "finished_at": "2026-08-07T12:31:00",
            "next_loop_at": "2026-08-07T12:40:00",
            "heartbeat_only": False,
            "pending_before": 2,
            "fetched_count": 10,
            "reserved_count": 7,
            "skipped_non_pending_machine_status_count": 3,
            "processed_count": 4,
            "errors": [],
        },
        now=now,
    )

    dashboard = load_status(store.db_path_for(now))

    assert dashboard["cumulative_inserted_count"] == 6
    assert dashboard["pending_to_audit_count"] == 2
    assert dashboard["pending_feedback_count"] == 1
    assert dashboard["feedback_retry_count"] == 1
    assert dashboard["manual_dead_letter_count"] == 1
    assert dashboard["last_run"]["fetched_count"] == 10
    assert dashboard["last_run"]["reserved_count"] == 7
    assert dashboard["last_run"]["skipped_non_pending_machine_status_count"] == 3
    assert dashboard["last_run"]["processed_count"] == 4
    assert 'id="lastReserved"' in HTML
    assert "lastRun.reserved_count || 0" in HTML


def test_dashboard_reads_barcode_result_from_raw_sn_barcode_result(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    store.reserve_order(valid_minimal_order(2301, "order-2301"), now=now)
    store.set_status(
        "apply:2301",
        "FEEDBACK_DONE",
        now=now,
        audit_result={
            "id": "order-2301",
            "manual_flag": "是",
            "manual_reason_code": "SN_MISMATCH",
            "system_sn": "SN-2301",
            "observed_sn": "WRONG-2301",
            "_raw": {
                "sn_barcode_result": {
                    "matched": False,
                    "decoded": [{"text": "OTHER-2301", "format": "CODE_128"}],
                    "reject_reasons": ["no_barcode_match"],
                }
            },
        },
        callback_request={"applyId": 2301, "status": 2, "refuseMessage": "SN不一致"},
        callback_response={"ok": True, "http_status": 200, "body": {"status": 200}},
    )

    dashboard = load_status(store.db_path_for(now))
    row = dashboard["rows"][0]

    assert row["barcode_attempted"] is True
    assert row["barcode_matched"] is False
    assert row["barcode_values"] == ["OTHER-2301"]
    assert "OTHER-2301" in row["barcode_result"]


def test_dashboard_exposes_final_result_and_reason_per_order(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    store.reserve_order(valid_minimal_order(2302, "order-2302"), now=now)
    store.set_status(
        "apply:2302",
        "FEEDBACK_DONE",
        now=now,
        audit_result={
            "id": "order-2302",
            "manual_flag": "是",
            "manual_reason_code": "SN_MISMATCH",
            "manual_reason_cn": "raw model text should not be used",
            "system_sn": "SN-2302",
            "observed_sn": "WRONG-2302",
        },
        callback_request={"applyId": 2302, "status": 2, "refuseMessage": "SN不一致"},
        callback_response={"ok": True, "http_status": 200, "body": {"status": 200}},
    )

    dashboard = load_status(store.db_path_for(now))
    row = dashboard["rows"][0]

    assert row["final_result"] == "不通过"
    assert row["final_reason"] == "SN不一致"
    assert row["reason_cn"] == "SN不一致"


def test_runner_persists_loop_summary_for_dashboard(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    for index in range(6):
        store.reserve_order(valid_minimal_order(index + 1, f"order-{index + 1}"), now=now)

    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            poll_interval_seconds=600,
            pending_heartbeat_threshold=5,
            audit_lease_seconds=600,
        ),
        store=store,
        collector=FakeCollector(),
        auditor=FakeAuditor(),
        callback_client=FakeCallbackClient(),
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    runner.run_once()
    dashboard = load_status(store.db_path_for(now))

    assert dashboard["last_run"]["run_status"] == "FINISHED"
    assert dashboard["last_run"]["heartbeat_only"] is True
    assert dashboard["last_run"]["started_at"] == "2026-08-07T12:30:00"
    assert dashboard["last_run"]["finished_at"] == "2026-08-07T12:30:00"
    assert dashboard["last_run"]["next_loop_at"] == "2026-08-07T12:40:00"
    assert dashboard["last_run"]["pending_before"] == 6
    assert dashboard["last_run"]["processed_count"] == 6
    assert dashboard["last_run"]["feedback_done_count"] == 6


def test_runner_persists_startup_evidence_in_run_summary(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
        ),
        store=store,
        collector=FakeCollector(orders=[]),
        auditor=FakeAuditor(),
        callback_client=FakeCallbackClient(),
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    runner.run_once()

    with sqlite3.connect(store.db_path_for(now)) as conn:
        summary_json = conn.execute("SELECT summary_json FROM audit_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    summary = __import__("json").loads(summary_json)
    evidence = summary["startup_evidence"]
    assert evidence["git_commit"]
    assert isinstance(evidence["git_worktree_dirty"], bool)
    assert evidence["python_path"]
    assert evidence["startup_command_summary"]
    assert "tools/guobu_linux_auto_audit.py" in evidence["runtime_file_sha256"]


def test_enforce_startup_rejects_dirty_worktree_without_explicit_override():
    metadata = {
        "git_commit": "abc123",
        "git_worktree_dirty": True,
        "runtime_file_sha256": {},
        "python_path": "python",
        "startup_command_summary": "tools.guobu_linux_auto_audit --once",
    }

    with pytest.raises(SystemExit, match="dirty worktree"):
        assert_production_startup_allowed(
            photo_authenticity_mode="enforce",
            startup_safety_override="",
            metadata=metadata,
        )

    assert_production_startup_allowed(
        photo_authenticity_mode="enforce",
        startup_safety_override="shadow",
        metadata=metadata,
    )


def test_runner_does_not_raise_when_fetch_returns_none(tmp_path: Path):
    class NoneCollector(FakeCollector):
        def fetch_orders(self, **kwargs):
            self.fetch_calls += 1
            return None

    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
        ),
        store=store,
        collector=NoneCollector(),
        auditor=FakeAuditor(),
        callback_client=FakeCallbackClient(),
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["fetched_count"] == 0
    assert summary["processed_count"] == 0
    assert summary["errors"] == []


def test_collector_does_not_treat_http_failure_as_empty_idle():
    collector = GuobuExamineCollectorClient("https://approval.invalid/api", "token")
    collector.http = FakeHttp({"ok": False, "http_status": 500, "body": {"message": "server error"}})

    with pytest.raises(RuntimeError, match="fetch_orders_request_failed"):
        collector.fetch_orders(payload=build_examine_page_payload())


def test_collector_accepts_successful_empty_records_as_idle():
    collector = GuobuExamineCollectorClient("https://approval.invalid/api", "token")
    collector.http = FakeHttp({"ok": True, "http_status": 200, "body": {"status": 200, "data": {"records": []}}})

    assert collector.fetch_orders(payload=build_examine_page_payload()) == []


def test_collector_extracts_records_from_production_payload_wrapper():
    collector = GuobuExamineCollectorClient("https://approval.invalid/api", "token")
    collector.http = FakeHttp(
        {
            "ok": True,
            "http_status": 200,
            "body": {
                "status": 200,
                "success": True,
                "message": "success",
                "payload": {
                    "records": [
                        {
                            "id": 93,
                            "jlPayOrder": "test-order-payload-093",
                            "machineExamineStatus": None,
                        }
                    ],
                    "total": 162318,
                },
            },
        }
    )

    assert collector.fetch_orders(payload=build_examine_page_payload()) == [
        {
            "id": 93,
            "jlPayOrder": "test-order-payload-093",
            "machineExamineStatus": None,
        }
    ]


def test_collector_fetch_detail_extracts_production_payload_wrapper():
    collector = GuobuExamineCollectorClient("https://approval.invalid/api", "token")
    collector.http = FakeHttp(
        {
            "ok": True,
            "http_status": 200,
            "body": {
                "status": 200,
                "success": True,
                "payload": {
                    "id": 204106,
                    "jlPayOrder": "test-order-prod-001",
                    "sn": "AMJTUT6708000506",
                    "cateCodeName": "手机",
                    "goodsPhoto": [{"url": "https://example.invalid/goods.jpg"}],
                },
            },
        }
    )

    detail = collector.fetch_detail(204106)

    assert detail["sn"] == "AMJTUT6708000506"
    assert detail["cateCodeName"] == "手机"
    assert "payload" not in detail


def test_http_client_normalizes_full_endpoint_base_url():
    client = JsonHttpClient("https://approval.invalid/api/cellPhone/26/apply/examinePage", "token")

    assert client._endpoint("/api/cellPhone/26/apply/detail") == "https://approval.invalid/api/cellPhone/26/apply/detail"


def test_build_audit_task_trims_order_numbers_and_preserves_photo_groups():
    task = build_audit_task(
        {"applyId": 5001, "jlPayOrder": " test-order-prod-002 "},
        {
            "sn": "SYS-SN-001",
            "categoryName": "电冰箱",
            "goodsPhoto": [{"url": "https://example.invalid/goods.jpg"}],
            "unsealingPhoto": [{"url": "https://example.invalid/unbox.jpg"}],
            "activatePhoto": [{"url": "https://example.invalid/sn.jpg"}],
        },
    )

    assert task["apply_id"] == "5001"
    assert task["channel_order_no"] == "test-order-prod-002"
    assert task["fields"]["system_sn"] == "SYS-SN-001"
    assert set(task["image_groups"]) == {"商品照片", "拆封照片", "SN码采集 / 激活照片"}
    assert task["image_groups"]["商品照片"][0]["source_url"] == "https://example.invalid/goods.jpg"


def test_build_audit_task_accepts_production_detail_fields_and_json_photo_strings():
    task = build_audit_task(
        {"id": 204106, "jlPayOrder": " test-order-prod-001 "},
        {
            "id": 204106,
            "jlPayOrder": " test-order-prod-001 ",
            "sn": "AMJTUT6708000506",
            "cateCodeName": "手机",
            "goodsName": "5G手机-SER-AN00",
            "customAddress": "广东省深圳市南山区科技园1栋101室",
            "goodsPhoto": '[{"url":"https://example.invalid/goods.jpg"}]',
            "unsealingPhoto": '[{"url":"https://example.invalid/unbox.jpg"}]',
            "activatePhoto": '[{"url":"https://example.invalid/sn.jpg"}]',
        },
    )

    assert task["channel_order_no"] == "test-order-prod-001"
    assert task["fields"]["system_sn"] == "AMJTUT6708000506"
    assert task["fields"]["product_type"] == "手机"
    assert task["fields"]["category_name"] == "手机"
    assert task["fields"]["cate_code_name"] == "手机"
    assert task["fields"]["product_name"] == "5G手机-SER-AN00"
    assert task["fields"]["address"] == "广东省深圳市南山区科技园1栋101室"
    assert task["image_groups"]["商品照片"][0]["source_url"] == "https://example.invalid/goods.jpg"
    assert task["image_groups"]["拆封照片"][0]["source_url"] == "https://example.invalid/unbox.jpg"
    assert task["image_groups"]["SN码采集 / 激活照片"][0]["source_url"] == "https://example.invalid/sn.jpg"
    image_ids = [
        image["image_id"]
        for images in task["image_groups"].values()
        for image in images
    ]
    assert len(image_ids) == len(set(image_ids))


def test_build_audit_task_extracts_url_from_malformed_photo_text_without_truncating_s():
    task = build_audit_task(
        {"id": 204107, "jlPayOrder": "test-order-prod-107"},
        {
            "sn": "SN-204107",
            "cateCodeName": "手机",
            "goodsPhoto": "broken https://example.invalid/assets/goods.jpg tail",
            "unsealingPhoto": [{"url": "https://example.invalid/assets/unbox.jpg"}],
            "activatePhoto": [{"url": "https://example.invalid/assets/sn.jpg"}],
        },
    )

    assert task["image_groups"]["商品照片"][0]["source_url"] == "https://example.invalid/assets/goods.jpg"


def test_runner_does_not_callback_when_task_conversion_lacks_required_fields(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    collector = FakeCollector(
        orders=[{"apply_id": 3001, "channel_order_no": "test-order-invalid-001", "machineExamineStatus": None}],
        detail_map={"3001": {}},
    )
    auditor = FakeAuditor()
    callback_client = FakeCallbackClient()
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
        ),
        store=store,
        collector=collector,
        auditor=auditor,
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["processed_count"] == 0
    assert summary["feedback_done_count"] == 0
    assert not auditor.calls
    assert not callback_client.calls
    assert summary["errors"]
    assert "task_conversion_invalid" in summary["errors"][0]
    assert summary["manual_feedback_required_count"] == 1
    with sqlite3.connect(store.db_path_for(now)) as conn:
        row = conn.execute(
            "SELECT status, error_text FROM orders WHERE dedup_key = ?",
            ("apply:3001",),
        ).fetchone()
    assert row[0] == "MANUAL_FEEDBACK_REQUIRED"
    assert "task_conversion_invalid" in row[1]


@pytest.mark.parametrize(
    "bad_photo",
    [
        {},
        {"url": ""},
        {"source_url": "   "},
    ],
)
def test_runner_does_not_callback_when_task_images_lack_usable_location(tmp_path: Path, bad_photo: dict[str, object]):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    collector = FakeCollector(
        orders=[{"apply_id": 3002, "channel_order_no": "test-order-invalid-002", "machineExamineStatus": None}],
        detail_map={
            "3002": {
                "id": 3002,
                "jlPayOrder": "test-order-invalid-002",
                "sn": "SN-3002",
                "cateCodeName": "手机",
                "goodsPhoto": [bad_photo],
                "unsealingPhoto": [bad_photo],
                "activatePhoto": [bad_photo],
            }
        },
    )
    auditor = FakeAuditor()
    callback_client = FakeCallbackClient()
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
        ),
        store=store,
        collector=collector,
        auditor=auditor,
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["processed_count"] == 0
    assert not auditor.calls
    assert not callback_client.calls
    assert "task_conversion_invalid" in summary["errors"][0]


def test_runner_does_not_callback_when_activation_group_lacks_usable_location(tmp_path: Path):
    store = MonthlyAuditStateStore(tmp_path)
    now = datetime(2026, 8, 7, 12, 30, 0)
    collector = FakeCollector(
        orders=[{"apply_id": 3003, "channel_order_no": "test-order-invalid-003", "machineExamineStatus": None}],
        detail_map={
            "3003": {
                "id": 3003,
                "jlPayOrder": "test-order-invalid-003",
                "sn": "SN-3003",
                "cateCodeName": "手机",
                "goodsPhoto": [{"url": "https://example.invalid/goods.jpg"}],
                "unsealingPhoto": [{"url": "https://example.invalid/unbox.jpg"}],
                "activatePhoto": [{"url": ""}],
            }
        },
    )
    auditor = FakeAuditor()
    callback_client = FakeCallbackClient()
    runner = GuobuLinuxAutoAuditRunner(
        config=LinuxAutoAuditConfig(
            state_dir=tmp_path,
            temp_dir=tmp_path / "tmp",
            collector_base_url="https://approval.invalid/api",
            collector_auth_token="token",
            approval_base_url="https://approval.invalid/api",
            approval_auth_token="token",
            audit_lease_seconds=600,
        ),
        store=store,
        collector=collector,
        auditor=auditor,
        callback_client=callback_client,
        now_fn=lambda: now,
        sleep_fn=lambda _seconds: None,
    )

    summary = runner.run_once()

    assert summary["processed_count"] == 0
    assert not auditor.calls
    assert not callback_client.calls
    assert "image_group:SN码采集 / 激活照片" in summary["errors"][0]


def test_linux_start_script_sets_utf8_and_production_policy_switches():
    script = (PROJECT_ROOT / "tools" / "start_guobu_linux_auto_audit.sh").read_text(encoding="utf-8")

    assert "PYTHONUTF8=1" in script
    assert "PYTHONIOENCODING=utf-8" in script
    assert "SN_POLICY_VERSION=v2" in script
    assert "SN_BARCODE_MODE=enforce" in script
    assert "DIGITAL_ACTIVATION_EVIDENCE_MODE=on" in script
    assert "PHOTO_AUTHENTICITY_MODE=enforce" in script
    assert "PHOTO_AUTHENTICITY_NEW_RULE_ENABLED=true" in script
    assert "PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED=false" in script
    assert "PHOTO_AUTHENTICITY_LOCAL_TREE_CONFIRMATION_ENABLED=false" in script
    assert "tools.guobu_linux_auto_audit" in script
    assert "--once" in script
    assert ".venv/bin/python" in script
    assert "--audit-lease-seconds" in script
    assert "GUOBU_MAX_FETCH_PAGES" in script
    assert "--max-fetch-pages" in script
    assert "GUOBU_EXIT_NONZERO_ON_ERRORS" in script
    assert "--exit-nonzero-on-errors" in script


def test_windows_auto_audit_launcher_productizes_token_and_secret_bootstrap():
    script = (PROJECT_ROOT / "tools" / "start_guobu_auto_audit.ps1").read_text(encoding="utf-8")

    assert "[string]$PythonBin" in script
    assert "GUOBU_PYTHON_BIN" in script
    assert "Python 3.14" in script
    assert "LOCALAPPDATA" in script
    assert "run_with_local_vision_secrets.ps1" in script
    assert "guobu_one_click_collect.js" in script
    assert "--save-token-env" in script
    assert "Token bootstrap failed; falling back to cached token env" in script
    assert "Cached token env is missing after token bootstrap failure" in script
    assert "Invoke-TokenBootstrapWithTimeout" in script
    assert "Wait-Process -Timeout 30" in script
    assert "Token bootstrap timed out after 30 seconds" in script
    assert ".audit_robot\\secrets\\guobu_auto_audit.env" in script
    assert "https://approval.jhddsz.com" in script
    assert "GUOBU_AUTH_TOKEN" in script
    assert "MACHINE_APPROVAL_AUTH_TOKEN" in script
    assert "SN_POLICY_VERSION" in script
    assert "SN_BARCODE_MODE" in script
    assert "PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED" in script
    assert "tools.guobu_linux_auto_audit" in script
    assert "-CommandArgs" not in script
    assert "Start-Process" not in script


def test_parse_args_supports_xxljob_error_exit_mode():
    args = parse_args(["--once", "--exit-nonzero-on-errors", "--max-fetch-pages", "7"])

    assert args.once is True
    assert args.exit_nonzero_on_errors is True
    assert args.max_fetch_pages == 7


def test_linux_deployment_doc_records_timer_idle_and_feedback_contract():
    doc = (PROJECT_ROOT / "docs" / "linux_auto_audit_deployment.md").read_text(encoding="utf-8")

    assert "status=0" in doc
    assert "machineExamineStatus=null" in doc
    assert "audit_state_YYYY_MM.sqlite" in doc
    assert "status=1" in doc
    assert "status=2" in doc
    assert "refuseMessage" in doc
    assert "空转" in doc
    assert "不报错" in doc
    assert "systemd" in doc
    assert "XXL-JOB" in doc
    assert "GUOBU_EXIT_NONZERO_ON_ERRORS=true" in doc
    assert "0 0/10 * * * ?" in doc


def test_machine_approval_feedback_script_is_merged_into_mainline():
    source = (PROJECT_ROOT / "tools" / "guobu_machine_approval_feedback.js").read_text(encoding="utf-8")

    assert "machineApproval" in source
    assert "refuseMessage" in source
    assert "GUOBU_AUTH_TOKEN" in source
    assert "MACHINE_APPROVAL_AUTH_TOKEN" in source
    assert "confirm-apply-id" in source
    assert "confirm-prod-write" in source
