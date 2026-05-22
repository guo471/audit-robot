# -*- coding: utf-8 -*-
import json
import subprocess
import sys

from run_audit import build_audit_request, sanitize_audit_output


def test_cli_returns_manual_for_missing_images(tmp_path):
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
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )

    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["decision"] == "manual"
    assert body["action"] == "next"


def test_cli_request_file_accepts_audit_request_shape(tmp_path):
    request_file = tmp_path / "request.json"
    id_front = tmp_path / "id_front.jpg"
    id_back = tmp_path / "id_back.jpg"
    sn_image = tmp_path / "sn.jpg"
    request_file.write_text(
        json.dumps({
            "jl_order_no": "JL123",
            "scene_hint": "非发券审核",
            "fields": {"product_type": "手机数码", "sn": "SN001234", "name": "张三"},
            "images": [
                {"title": "身份证人像面", "path": str(id_front)},
                {"title": "身份证国徽面", "path": str(id_back)},
                {"title": "SN码采集照片", "path": str(sn_image)},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "run_audit.py", "--request_file", str(request_file)],
        cwd="C:/audit_robot",
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )

    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["decision"] == "manual"
    assert body["scene"] == "no_coupon"


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
    assert "found_sns" in dumped
    assert "found_imeis" in dumped
    assert "match_details" in dumped
    assert "rules" in dumped
    assert "440101199001011234" not in dumped
    assert "SN999.jpg" not in dumped
    assert "系统: [CODE]" in dumped
    assert "OCR识别结果: [CODE_LIST]" in dumped


def test_build_audit_request_preserves_cli_order_for_last_sn_check(tmp_path):
    first = tmp_path / "id.jpg"
    second = tmp_path / "sn.jpg"

    request = build_audit_request(
        [first, second],
        {"jl_order_no": "JL123", "sn": "SN001234", "product_type": "3C"},
        "guobu",
    )

    assert request.images[0].path == str(first)
    assert request.images[-1].path == str(second)
    assert request.scene_hint == "guobu"
