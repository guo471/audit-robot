# -*- coding: utf-8 -*-
import json
import subprocess
import sys

from run_audit import sanitize_audit_output


def test_cli_returns_engine_error_for_missing_images(tmp_path):
    request_file = tmp_path / "request.json"
    request_file.write_text(
        json.dumps({
            "system_data": {"jl_order_no": "JL123", "sn": "SN001", "product_type": "3C"},
            "image_urls": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "run_audit.py", "--request_file", str(request_file)],
        cwd="C:/audit_robot",
        text=True,
        capture_output=True,
    )

    assert result.returncode == 3
    body = json.loads(result.stdout)
    assert body["decision"] == "engine_error"


def test_cli_output_sanitizer_removes_code_details_and_sensitive_values():
    body = sanitize_audit_output({
        "decision": "manual",
        "skip_reason": "SN码不匹配。系统: SN123，OCR识别结果: ['SN999']",
        "codes": {
            "sn": {
                "sn_match": False,
                "found_sns": ["SN999"],
                "match_details": "SN 系统=SN123 不匹配",
            },
            "imei": {
                "imei1_match": False,
                "found_imeis": ["861234567890123"],
            },
        },
        "rules": {"sn": {"reason": "系统=SN123"}},
        "id_card": {"id_number": "440101199001011234", "is_valid": True},
        "image_forensics": {"per_image": [{"name": "SN999.jpg", "status": "pass"}]},
    })

    dumped = json.dumps(body, ensure_ascii=False)
    assert "found_sns" not in dumped
    assert "found_imeis" not in dumped
    assert "match_details" not in dumped
    assert "rules" not in dumped
    assert "440101199001011234" not in dumped
    assert "SN999" not in dumped
    assert "SN999.jpg" not in dumped
    assert "系统: [CODE]" in dumped
    assert "OCR识别结果: [CODE_LIST]" in dumped
