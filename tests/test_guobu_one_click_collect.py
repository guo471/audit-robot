import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "tools" / "guobu_one_click_collect.js"


def run_dry_run(*args: str) -> dict:
    output = subprocess.check_output(
        ["node", str(SCRIPT), "--dry-run", *args],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
    )
    return json.loads(output)


def test_one_click_collector_uses_api_time_field_that_matches_backend_audit_time():
    result = run_dry_run(
        "--status",
        "failed",
        "--count",
        "19",
        "--current-page",
        "1",
        "--no-check-time",
        "--approval-start-time",
        "2026-07-24 00:00:00",
        "--approval-end-time",
        "2026-07-24 15:59:36",
        "--label",
        "fail_20260724_19",
    )

    args = result["powershellArgs"]
    assert "-SkipPageFilter" in args
    assert args[args.index("-Status") + 1] == "failed"
    assert args[args.index("-Count") + 1] == "19"
    assert args[args.index("-CurrentPage") + 1] == "1"
    assert "-CheckStartTime" not in args
    assert "-CheckEndTime" not in args
    assert args[args.index("-ApprovalStartTime") + 1] == "2026-07-24 00:00:00"
    assert args[args.index("-ApprovalEndTime") + 1] == "2026-07-24 15:59:36"


def test_one_click_collector_defaults_to_current_19_order_failed_batch():
    result = run_dry_run()

    assert result["config"]["status"] == "failed"
    assert result["config"]["collectionMode"] == "shadow"
    assert result["config"]["count"] == 19
    assert result["config"]["expectTotal"] == 19
    assert result["config"]["checkStartTime"] == ""
    assert result["config"]["checkEndTime"] == ""
    assert result["config"]["approvalStartTime"] == "2026-07-24 00:00:00"
    assert result["config"]["approvalEndTime"] == "2026-07-24 15:59:36"
    assert result["config"]["label"] == "fail_20260724_19"


def test_one_click_collector_dry_run_never_outputs_token():
    result = run_dry_run("--dry-run")
    serialized = json.dumps(result, ensure_ascii=False)

    assert "token" not in serialized.lower()
    assert "authorization" not in serialized.lower()


def test_one_click_collector_accepts_collection_mode_switch():
    result = run_dry_run("--collection-mode", "enforce")

    assert result["config"]["collectionMode"] == "enforce"


def test_shadow_compare_writes_whitelisted_tasks_without_unrelated_page_content(tmp_path):
    tasks_dir = tmp_path / "tasks"
    report_path = tmp_path / "shadow_report.json"
    tasks_dir.mkdir()
    (tasks_dir / "order-1.json").write_text(
        json.dumps(
            {
                "task_id": "order-1",
                "channel_order_no": "JL20260730001",
                "scene": "failed",
                "expected_label": "failed",
                "apply_id": 1001,
                "product_type": "phone",
                "cate_code": "26",
                "cate_code_name": "手机",
                "goods_name": "Phone X",
                "brand": "BrandA",
                "model": "ModelA",
                "system_sn": "SN123",
                "imei1": "IMEI-A",
                "imei2": "",
                "barcode": "BAR-A",
                "address": "广东省深圳市南山区",
                "status": "未通过",
                "flow_status": "待审核",
                "source_flow_status": "list-flow",
                "price": "9999",
                "check_name": "operator",
                "whole_page_text": "this is unrelated copied page content",
                "image_groups": {
                    "商品照片": [
                        {
                            "title": "商品照片",
                            "local_path": "data/images/goods.jpg",
                            "source_url": "https://example.test/goods.jpg",
                            "extra_raw": "remove me",
                        }
                    ],
                    "拆封照片": [],
                    "SN码采集/激活照片": [
                        {
                            "title": "SN码采集/激活照片",
                            "source_url": "https://example.test/sn.jpg",
                        }
                    ],
                    "页面截图": [{"source_url": "https://example.test/page.jpg"}],
                },
                "source": {
                    "collector": "api",
                    "source_url": "https://approval.jhddsz.com/admin/#/digital/review",
                    "raw_response": {"customerName": "should not leave shadow output"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    output = subprocess.check_output(
        [
            "node",
            str(SCRIPT),
            "--shadow-compare-tasks-dir",
            str(tasks_dir),
            "--shadow-report-path",
            str(report_path),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(output)
    sanitized_task_path = Path(result["sanitizedTasksDir"]) / "order-1.json"
    sanitized = json.loads(sanitized_task_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert sanitized["channel_order_no"] == "JL20260730001"
    assert sanitized["fields"]["system_sn"] == "SN123"
    assert sanitized["fields"]["address"] == "广东省深圳市南山区"
    assert "price" not in sanitized["fields"]
    assert "check_name" not in sanitized["fields"]
    assert "whole_page_text" not in json.dumps(sanitized, ensure_ascii=False)
    assert "raw_response" not in json.dumps(sanitized, ensure_ascii=False)
    assert set(sanitized["image_groups"]) == {"商品照片", "拆封照片", "SN码采集/激活照片"}
    assert sanitized["image_groups"]["商品照片"][0] == {
        "title": "商品照片",
        "local_path": "data/images/goods.jpg",
        "source_url": "https://example.test/goods.jpg",
    }
    assert report["sampleCount"] == 1
    assert "price" in report["removedTopLevelFields"]


def test_shadow_compare_reports_missing_required_fields_without_crashing(tmp_path):
    tasks_dir = tmp_path / "tasks"
    report_path = tmp_path / "shadow_report.json"
    tasks_dir.mkdir()
    (tasks_dir / "order-missing.json").write_text(
        json.dumps(
            {
                "task_id": "order-missing",
                "channel_order_no": "JL20260730002",
                "flow_status": "待审核",
                "image_groups": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    subprocess.check_call(
        [
            "node",
            str(SCRIPT),
            "--shadow-compare-tasks-dir",
            str(tasks_dir),
            "--shadow-report-path",
            str(report_path),
        ],
        cwd=PROJECT_ROOT,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["missingRequiredCounts"]["fields.system_sn"] == 1
    assert report["missingRequiredCounts"]["fields.address"] == 1
    assert report["missingRequiredCounts"]["image_groups.商品照片"] == 1
    assert report["tasksWithMissingRequired"][0]["task_id"] == "order-missing"


def test_shadow_compare_normalizes_sn_photo_group_alias_with_spaces(tmp_path):
    tasks_dir = tmp_path / "tasks"
    report_path = tmp_path / "shadow_report.json"
    tasks_dir.mkdir()
    (tasks_dir / "order-sn-alias.json").write_text(
        json.dumps(
            {
                "task_id": "order-sn-alias",
                "channel_order_no": "JL20260730003",
                "product_type": "phone",
                "system_sn": "SN-ALIAS",
                "address": "广东省深圳市",
                "flow_status": "待审核",
                "image_groups": {
                    "商品照片": [{"source_url": "https://example.test/goods.jpg"}],
                    "拆封照片": [{"source_url": "https://example.test/open.jpg"}],
                    "SN码采集 / 激活照片": [{"source_url": "https://example.test/sn-alias.jpg"}],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    output = subprocess.check_output(
        [
            "node",
            str(SCRIPT),
            "--shadow-compare-tasks-dir",
            str(tasks_dir),
            "--shadow-report-path",
            str(report_path),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(output)
    sanitized = json.loads((Path(result["sanitizedTasksDir"]) / "order-sn-alias.json").read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert sanitized["image_groups"]["SN码采集/激活照片"][0]["source_url"] == "https://example.test/sn-alias.jpg"
    assert report["missingRequiredCounts"]["image_groups.SN码采集/激活照片"] == 0


def test_entry_has_clear_login_failure_message_and_local_storage_fallback():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "collectTokenCandidates" in script
    assert "Local Storage" in script
    assert "没有找到可用的后台登录态" in script
    assert "approval.jhddsz.com" in script


def test_entry_redacts_raw_response_when_collection_mode_is_enforced():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "raw_response.json" in script
    assert "raw_response_before_whitelist" in script
    assert "fullRawResponseRedacted" in script
