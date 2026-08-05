# -*- coding: utf-8 -*-
import json

from tools.sn_acceptance_shadow_report import build_blocked_report, write_report


def test_blocked_shadow_report_uses_dataset_without_faking_result_changes(tmp_path):
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "channel_order_no": "ORDER-1",
                    "system_sn": "SN123",
                    "source_flow_status": "已通过",
                    "activation_sn_images": [{"image_id": "img_003", "source_url": "https://example.test/sn.jpg"}],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_blocked_report(dataset_path, reason="VISION_API_KEY missing")

    assert report["status"] == "blocked"
    assert report["dataset_records"] == 1
    assert report["old_new_comparison_ran"] is False
    assert report["pass_to_manual_changes"] == []
    assert report["manual_to_pass_changes"] == []
    assert report["blocked_reason"] == "VISION_API_KEY missing"


def test_write_report_creates_json_file(tmp_path):
    out = tmp_path / "report.json"
    write_report({"status": "blocked"}, out)
    assert json.loads(out.read_text(encoding="utf-8")) == {"status": "blocked"}
