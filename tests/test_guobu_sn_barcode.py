# -*- coding: utf-8 -*-
from pathlib import Path

from tools.guobu_sn_barcode import barcode_second_check, scan_activation_barcodes


def decoded(text, *, fmt="CODE_128", field_type=""):
    item = {"text": text, "format": fmt}
    if field_type:
        item["field_type"] = field_type
    return item


def test_barcode_exact_match_accepts_numeric_system_sn():
    result = barcode_second_check({"system_sn": "123456789012345"}, [decoded("123456789012345")])

    assert result["matched"] is True
    assert result["match_type"] == "exact"


def test_barcode_allows_only_leading_s_prefix():
    result = barcode_second_check({"system_sn": "H0QW1CDVWD"}, [decoded("SH0QW1CDVWD")])

    assert result["matched"] is True
    assert result["match_type"] == "leading_s_prefix"


def test_barcode_does_not_normalize_o_to_zero_before_comparison():
    result = barcode_second_check({"system_sn": "ABC056"}, [decoded("ABCO56")])

    assert result["matched"] is False

    reverse = barcode_second_check({"system_sn": "ABCO56"}, [decoded("ABC056")])

    assert reverse["matched"] is False


def test_barcode_does_not_normalize_other_visual_or_extra_characters():
    fields = {"system_sn": "ABCO56"}

    for text in ("ABC066", "ABCO66", "ABCO56X", "ABC O56"):
        result = barcode_second_check(fields, [decoded(text)])
        assert result["matched"] is False


def test_barcode_only_trims_outer_whitespace():
    assert barcode_second_check({"system_sn": "ABC123"}, [decoded("  ABC123  ")])["matched"] is True
    assert barcode_second_check({"system_sn": "ABC123"}, [decoded("ABC 123")])["matched"] is False


def test_barcode_trims_outer_control_characters_without_normalizing_sn():
    assert barcode_second_check({"system_sn": "C0106000B004100006"}, [decoded("C0106000B004100006\x00")])["matched"] is True
    assert barcode_second_check({"system_sn": "C0106000B004100006"}, [decoded("\x00C0106000B004100006")])["matched"] is True
    assert barcode_second_check({"system_sn": "C0106000B004100006"}, [decoded("C0106000B004100006<NUL>")])["matched"] is True
    assert barcode_second_check({"system_sn": "C0106000B004100006"}, [decoded("<NULL>C0106000B004100006")])["matched"] is True
    assert barcode_second_check({"system_sn": "C0106000B004100006"}, [decoded("C0106000B0041\x0000006")])["matched"] is False
    assert barcode_second_check({"system_sn": "C0106000B004100006"}, [decoded("C0106000B0041<NUL>00006")])["matched"] is False


def test_barcode_rejects_identity_and_retail_codes_even_when_text_matches():
    cases = [
        ({"system_sn": "123456789012345"}, decoded("123456789012345", field_type="IMEI")),
        ({"system_sn": "123456789012345", "imei1": "123456789012345"}, decoded("123456789012345")),
        ({"system_sn": "6901234567890"}, decoded("6901234567890", fmt="EAN_13")),
        ({"system_sn": "123456789012"}, decoded("123456789012", fmt="UPC_A")),
    ]

    for fields, item in cases:
        result = barcode_second_check(fields, [item])
        assert result["matched"] is False


def test_scanner_failure_returns_empty_list_without_interrupting(monkeypatch, tmp_path):
    image_path = tmp_path / "sn.jpg"
    image_path.write_bytes(b"not a real image")

    def fail_scan(_path: Path, _image_id: str):
        raise RuntimeError("scanner exploded")

    monkeypatch.setattr("tools.guobu_sn_barcode.scan_image_barcodes", fail_scan)

    result = scan_activation_barcodes(
        {},
        [{"image_id": "img_003", "local_path": str(image_path)}],
    )

    assert result == []
