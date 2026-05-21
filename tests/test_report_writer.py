import csv

from modules.privacy import SAFE_REPORT_COLUMNS
from modules.report_writer import append_report_row


def test_append_report_row_writes_header_and_safe_redacted_values(tmp_path):
    report_path = tmp_path / "nested" / "audit_report.csv"

    append_report_row(
        report_path,
        {
            "jl_order_no": "JL001",
            "channel_order_no": "CH001",
            "scene": "home",
            "category": "appliance",
            "decision": "manual",
            "path": "manual_review",
            "elapsed_sec": 1.25,
            "manual_reason": "inspect https://x.test/a.jpg",
            "sn_match": True,
            "image_roles_ok": True,
            "real_photo_pass": False,
            "id_name_match": True,
            "id_valid": True,
            "address_detail_ok": False,
            "image_url": "https://x.test/a.jpg",
        },
    )

    with report_path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    assert rows[0].keys() == set(SAFE_REPORT_COLUMNS)
    assert rows[0]["manual_reason"] == "inspect [URL]"
    assert rows[0]["image_roles_ok"] == "True"
    assert "image_url" not in rows[0]
