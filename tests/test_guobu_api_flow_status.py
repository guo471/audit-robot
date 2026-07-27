from pathlib import Path

from tools.run_guobu_model_audit_v2 import CSV_COLUMNS, _final_row


def test_collector_supports_all_status_and_persists_flow_fields():
    collector = (
        Path.home()
        / ".codex"
        / "skills"
        / "guobu-examine-api-collector"
        / "scripts"
        / "collect_guobu_examine_api_from_edge.ps1"
    )
    script = collector.read_text(encoding="utf-8-sig")

    assert "[string]$Status" in script
    assert "[switch]$AllStatus" in script
    assert "[string]$ApprovalStartTime" in script
    assert "[string]$ApprovalEndTime" in script
    assert "$rawTargets = Invoke-RestMethod" in script
    assert "$targets = @($rawTargets)" in script
    assert "approval.jhddsz.com/admin/#/digital/review" in script
    assert "status: $statusJson" in script
    assert "approvalStartTime = $ApprovalStartTime" in script
    assert "approvalEndTime = $ApprovalEndTime" in script
    assert "approvalStartTime: $approvalStartTimeJson" in script
    assert "approvalEndTime: $approvalEndTimeJson" in script
    assert "flow_status = $record.status" in script
    assert "source_flow_status = $record.status" in script
    assert "examine_status = $record.examineStatus" in script
    assert "settle_status = $record.settleStatus" in script


def test_model_audit_row_includes_source_flow_status():
    task = {
        "channel_order_no": "order-1",
        "fields": {
            "product_type": "[B01] phone",
            "system_sn": "SN001",
            "flow_status": "\u5df2\u901a\u8fc7",
            "source_flow_status": "\u5df2\u901a\u8fc7",
            "examine_status": 2,
            "settle_status": 0,
        },
    }

    row = _final_row(
        task,
        {"manual_required": False, "manual_reason_codes": [], "manual_reason": ""},
        {"sn_match": True, "observed_sn": "SN001"},
        {"product_type_match": True},
        1.0,
        0.1,
        0.2,
        0.3,
    )

    assert row["source_flow_status"] == "\u5df2\u901a\u8fc7"
    assert row["source_examine_status"] == 2
    assert row["source_settle_status"] == 0


def test_source_flow_status_column_is_next_to_manual_flag():
    keys = [key for key, _label in CSV_COLUMNS]
    labels = dict(CSV_COLUMNS)

    manual_index = keys.index("manual_flag")
    assert keys[manual_index + 1] == "source_flow_status"
    assert labels["manual_flag"] == "\u662f\u5426\u8f6c\u4eba\u5de5"
    assert labels["source_flow_status"] == "\u539f\u59cb\u6d41\u7a0b\u72b6\u6001"
