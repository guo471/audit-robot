import pytest

from config import TEMP_DIR
from modules.privacy import SAFE_REPORT_COLUMNS, redact_text, remove_temp_dir, safe_report_row


def test_redact_text_removes_sensitive_values():
    address = "\u5e7f\u4e1c\u7701\u5e7f\u5dde\u5e02\u5929\u6cb3\u533a\u4f53\u80b2\u897f\u8def123\u53f7A\u5ea71201\u5ba4"
    text = (
        "manual check: 440101199001011234 phone 13800138000 "
        f"image https://x.test/a.jpg address {address}"
    )

    redacted = redact_text(text)

    assert "440101199001011234" not in redacted
    assert "13800138000" not in redacted
    assert "https://x.test/a.jpg" not in redacted
    assert address not in redacted
    assert "[ID]" in redacted
    assert "[PHONE]" in redacted
    assert "[URL]" in redacted
    assert "[ADDRESS]" in redacted


def test_safe_report_row_keeps_safe_columns_and_redacts_manual_reason():
    address = "\u5e7f\u4e1c\u7701\u5e7f\u5dde\u5e02\u5929\u6cb3\u533a\u4f53\u80b2\u897f\u8def123\u53f7A\u5ea71201\u5ba4"
    row = safe_report_row(
        {
            "jl_order_no": "JL001",
            "channel_order_no": "CH001",
            "scene": "home",
            "category": "appliance",
            "decision": "manual",
            "path": "manual_review",
            "elapsed_sec": 1.25,
            "manual_reason": "see https://x.test/a.jpg phone 13800138000",
            "sn_match": True,
            "image_roles_ok": False,
            "real_photo_pass": True,
            "id_name_match": None,
            "id_valid": True,
            "address_detail_ok": False,
            "image_url": "https://x.test/a.jpg",
            "ocr_raw_text": "raw 440101199001011234",
            "address": address,
        }
    )

    assert list(row) == SAFE_REPORT_COLUMNS
    assert row["manual_reason"] == "see [URL] phone [PHONE]"
    assert row["id_name_match"] == ""
    assert "image_url" not in row
    assert "ocr_raw_text" not in row
    assert "address" not in row


def test_remove_temp_dir_only_deletes_temp_children(tmp_path):
    temp_child = TEMP_DIR / "pytest_remove_temp_child"
    temp_child.mkdir(parents=True, exist_ok=True)
    marker = temp_child / "marker.txt"
    marker.write_text("ok", encoding="utf-8")

    remove_temp_dir(temp_child)

    assert not temp_child.exists()
    with pytest.raises(ValueError):
        remove_temp_dir(tmp_path)
