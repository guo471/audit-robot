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
