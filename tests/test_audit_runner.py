# -*- coding: utf-8 -*-
import time

from modules.audit_models import AuditImage, AuditRequest
from modules.audit_runner import AuditDependencies, audit_request


class FakeOCR:
    def __init__(self, enhanced_texts, tiled_texts=None, sn_region_texts=None, delay_sec=0):
        self.enhanced_texts = enhanced_texts if isinstance(enhanced_texts, dict) else list(enhanced_texts)
        self.tiled_texts = tiled_texts if isinstance(tiled_texts, dict) else list(tiled_texts or [])
        self.sn_region_texts = (
            sn_region_texts if isinstance(sn_region_texts, dict) else list(sn_region_texts or [])
        )
        self.delay_sec = delay_sec
        self.enhanced_calls = 0
        self.tiled_calls = 0
        self.sn_region_calls = 0

    def extract_text_enhanced(self, path):
        self.enhanced_calls += 1
        if self.delay_sec:
            time.sleep(self.delay_sec)
        texts = self.enhanced_texts.get(path, []) if isinstance(self.enhanced_texts, dict) else self.enhanced_texts
        return [{"text": text, "confidence": 0.99, "box": None} for text in texts]

    def extract_text_tiled(self, path):
        self.tiled_calls += 1
        if self.delay_sec:
            time.sleep(self.delay_sec)
        texts = self.tiled_texts.get(path, []) if isinstance(self.tiled_texts, dict) else self.tiled_texts
        return [{"text": text, "confidence": 0.99, "box": None} for text in texts]

    def extract_text_sn_regions(self, path):
        self.sn_region_calls += 1
        if self.delay_sec:
            time.sleep(self.delay_sec)
        texts = self.sn_region_texts.get(path, []) if isinstance(self.sn_region_texts, dict) else self.sn_region_texts
        return [{"text": text, "confidence": 0.99, "box": None} for text in texts]


class RaisingOCR:
    def __init__(self, fail_on="enhanced"):
        self.fail_on = fail_on

    def extract_text_enhanced(self, path):
        if self.fail_on == "enhanced":
            raise RuntimeError(f"OCR failed for {path}")
        return [{"text": "NOISE", "confidence": 0.99, "box": None}]

    def extract_text_tiled(self, path):
        if self.fail_on == "tiled":
            raise RuntimeError(f"OCR failed for {path}")
        return [{"text": "NOISE", "confidence": 0.99, "box": None}]


class FakeForensics:
    def __init__(self, status="pass"):
        self.status = status
        self.calls = []

    def full_analysis(self, path):
        self.calls.append(path)
        status = self.status.get(path, "pass") if isinstance(self.status, dict) else self.status
        return {"status": status, "message": status}


def guobu_request(images=None, sn="SN001234"):
    return AuditRequest(
        jl_order_no="JL001",
        scene_hint="guobu",
        fields={"product_type": "3c", "sn": sn},
        images=images or [
            AuditImage(title="product", path="C:/tmp/product.jpg"),
            AuditImage(title="unbox", path="C:/tmp/unbox.jpg"),
            AuditImage(title="sn", path="C:/tmp/sn.jpg"),
        ],
    )


def guobu_home_appliance_request(address):
    return AuditRequest(
        jl_order_no="JL001",
        scene_hint="guobu",
        fields={"product_type": "home_appliance", "product_name": "home_appliance", "sn": "SN001234", "address": address},
        images=[
            AuditImage(title="product", path="C:/tmp/product.jpg"),
            AuditImage(title="unbox", path="C:/tmp/unbox.jpg"),
            AuditImage(title="sn", path="C:/tmp/sn.jpg"),
        ],
    )


def guobu_home_appliance_sn_request():
    return AuditRequest(
        jl_order_no="JL001",
        scene_hint="guobu",
        fields={
            "product_type": "home_appliance",
            "product_name": "home_appliance",
            "sn": "SN001234",
            "address": "\u6e56\u5357\u7701\u957f\u6c99\u5e02\u671b\u57ce\u533a\u67d0\u9547\u67d0\u6751",
        },
        images=[
            AuditImage(title="product", path="C:/tmp/product.jpg"),
            AuditImage(title="unbox", path="C:/tmp/unbox.jpg"),
            AuditImage(title="sn", path="C:/tmp/sn.jpg"),
        ],
    )


def no_coupon_request(images=None, sn="SN001234", fields=None):
    request_fields = {"product_type": "phone", "sn": sn, "name": "ZhangSan"}
    if fields:
        request_fields.update(fields)
    return AuditRequest(
        jl_order_no="JL002",
        scene_hint="no_coupon",
        fields=request_fields,
        images=images or [
            AuditImage(title="id-front", path="C:/tmp/id-front.jpg"),
            AuditImage(title="id-back", path="C:/tmp/id-back.jpg"),
            AuditImage(title="sn", path="C:/tmp/sn.jpg"),
        ],
    )


def test_fast_path_passes_when_sn_and_roles_match():
    ocr = FakeOCR(
        {
            "C:/tmp/product.jpg": ["NOISE"],
            "C:/tmp/unbox.jpg": ["NOISE"],
            "C:/tmp/sn.jpg": ["SN001234"],
        }
    )
    deps = AuditDependencies(ocr=ocr, forensics=FakeForensics())

    response = audit_request(guobu_request(), deps=deps)

    assert response.decision == "manual"
    assert response.path == "fast"
    assert response.evidence["sn_match"] is True
    assert response.evidence["sn_checked_image"] == "last"
    assert ocr.enhanced_calls == 1
    assert ocr.sn_region_calls == 0
    assert "found_sns" not in response.evidence
    assert "match_details" not in response.evidence


def test_channel_order_no_fallback_reaches_later_precheck():
    request = AuditRequest(
        jl_order_no="",
        channel_order_no="CH-FALLBACK-001",
        scene_hint="guobu",
        fields={"product_type": "3c", "sn": "SN001234"},
        images=[],
    )

    response = audit_request(request, deps=AuditDependencies(ocr=FakeOCR([]), forensics=FakeForensics()))

    assert response.jl_order_no == "CH-FALLBACK-001"
    assert response.manual_reason == "图片为空"


def test_sn_match_on_non_last_image_still_goes_manual_when_last_misses():
    deps = AuditDependencies(
        ocr=FakeOCR(
            {
                "C:/tmp/product.jpg": ["SN001234"],
                "C:/tmp/unbox.jpg": ["SN001234"],
                "C:/tmp/sn.jpg": ["NOISE"],
            },
            {
                "C:/tmp/sn.jpg": ["NOISE"],
            },
        ),
        forensics=FakeForensics(),
    )

    response = audit_request(guobu_request(), deps=deps)

    assert response.decision == "manual"
    assert response.evidence["sn_match"] is False
    assert response.evidence["sn_checked_image"] == "last"


def test_slow_path_uses_tiled_ocr_when_fast_sn_misses():
    ocr = FakeOCR(
        {"C:/tmp/sn.jpg": ["NOISE"]},
        {"C:/tmp/sn.jpg": ["SN001234"]},
    )
    deps = AuditDependencies(ocr=ocr, forensics=FakeForensics())

    response = audit_request(guobu_request(), deps=deps)

    assert response.decision == "manual"
    assert response.path == "slow"
    assert ocr.tiled_calls >= 1
    assert ocr.sn_region_calls == 0


def test_sn_region_scan_is_not_used_by_default_in_audit_flow():
    ocr = FakeOCR(
        {"C:/tmp/sn.jpg": ["NOISE"]},
        {"C:/tmp/sn.jpg": ["NOISE"]},
        {"C:/tmp/sn.jpg": ["SN001234"]},
    )
    deps = AuditDependencies(ocr=ocr, forensics=FakeForensics())

    response = audit_request(guobu_home_appliance_sn_request(), deps=deps)

    assert response.decision == "manual"
    assert response.path == "slow"
    assert response.evidence["sn_match"] is False
    assert ocr.enhanced_calls == 1
    assert ocr.tiled_calls == 1
    assert ocr.sn_region_calls == 0
    assert response.evidence["sn_ocr_attempts"][-1]["path"] == "slow"
    assert "texts" not in response.evidence["sn_ocr_attempts"][-1]


def test_sn_region_scan_does_not_run_for_3c_miss():
    ocr = FakeOCR(
        {"C:/tmp/sn.jpg": ["NOISE"]},
        {"C:/tmp/sn.jpg": ["NOISE"]},
        {"C:/tmp/sn.jpg": ["SN001234"]},
    )
    deps = AuditDependencies(ocr=ocr, forensics=FakeForensics())

    response = audit_request(guobu_request(), deps=deps)

    assert response.decision == "manual"
    assert response.path == "slow"
    assert ocr.sn_region_calls == 0


def test_unknown_image_role_does_not_go_manual_when_last_sn_matches():
    deps = AuditDependencies(ocr=FakeOCR({"C:/tmp/other.jpg": ["SN001234"]}), forensics=FakeForensics())

    response = audit_request(
        guobu_request(images=[AuditImage(title="鍏朵粬鏉愭枡", path="C:/tmp/other.jpg")]),
        deps=deps,
    )

    assert response.decision == "manual"
    assert response.evidence["sn_match"] is True
    assert response.evidence["image_roles_ok"] is False


def test_forensics_risk_goes_manual():
    deps = AuditDependencies(ocr=FakeOCR(["SN001234"]), forensics=FakeForensics("suspicious"))

    response = audit_request(guobu_request(), deps=deps)

    assert response.decision == "manual"
    assert "图片风险" in response.manual_reason
    assert response.evidence["real_photo_pass"] is False


def test_timeout_after_expensive_ocr_returns_manual_not_pass():
    deps = AuditDependencies(
        ocr=FakeOCR(["SN001234"], delay_sec=0.02),
        forensics=FakeForensics(),
    )

    response = audit_request(guobu_request(), deps=deps, timeout_sec=0.001)

    assert response.decision == "manual"
    assert response.path == "fast"
    assert response.elapsed_sec == 0.001
    assert response.manual_reason == "单单超时"


def test_sn_ocr_exception_goes_manual_not_error():
    deps = AuditDependencies(ocr=RaisingOCR("enhanced"), forensics=FakeForensics())

    response = audit_request(guobu_request(), deps=deps)

    assert response.decision == "manual"
    assert response.action == "next"
    assert response.evidence["sn_match"] is False
    assert "C:/tmp" not in response.manual_reason


def test_no_coupon_missing_id_parse_goes_manual(monkeypatch):
    monkeypatch.setattr(
        "modules.audit_runner.IDCardParser.parse",
        lambda texts: {"name": None, "is_valid": False},
    )
    deps = AuditDependencies(ocr=FakeOCR({"C:/tmp/sn.jpg": ["SN001234"]}), forensics=FakeForensics())

    response = audit_request(no_coupon_request(), deps=deps)

    assert response.decision == "manual"
    assert response.evidence["id_name_match"] is False
    assert response.evidence["id_valid"] is False
    assert "id_number" not in response.evidence


def test_no_coupon_id_ocr_exception_goes_manual_not_error():
    deps = AuditDependencies(ocr=RaisingOCR("enhanced"), forensics=FakeForensics())

    response = audit_request(no_coupon_request(), deps=deps)

    assert response.decision == "manual"
    assert response.action == "next"
    assert response.evidence["id_name_match"] is False
    assert response.evidence["id_valid"] is False


def test_no_coupon_id_name_mismatch_goes_manual(monkeypatch):
    monkeypatch.setattr(
        "modules.audit_runner.IDCardParser.parse",
        lambda texts: {"name": "LiSi", "is_valid": True},
    )
    deps = AuditDependencies(ocr=FakeOCR({"C:/tmp/sn.jpg": ["SN001234"]}), forensics=FakeForensics())

    response = audit_request(no_coupon_request(), deps=deps)

    assert response.decision == "manual"
    assert response.evidence["id_name_match"] is False
    assert response.evidence["id_valid"] is True
    assert "LiSi" not in str(response.evidence)
    assert "ZhangSan" not in str(response.evidence)


def test_no_coupon_expired_id_goes_manual(monkeypatch):
    monkeypatch.setattr(
        "modules.audit_runner.IDCardParser.parse",
        lambda texts: {"name": "ZhangSan", "is_valid": False},
    )
    deps = AuditDependencies(ocr=FakeOCR({"C:/tmp/sn.jpg": ["SN001234"]}), forensics=FakeForensics())

    response = audit_request(no_coupon_request(), deps=deps)

    assert response.decision == "manual"
    assert response.evidence["id_name_match"] is True
    assert response.evidence["id_valid"] is False


def test_no_coupon_id_number_mismatch_goes_manual(monkeypatch):
    monkeypatch.setattr(
        "modules.audit_runner.IDCardParser.parse",
        lambda texts: {"name": "ZhangSan", "id_number": "440101199001011234", "is_valid": True},
    )
    deps = AuditDependencies(ocr=FakeOCR({"C:/tmp/sn.jpg": ["SN001234"]}), forensics=FakeForensics())

    response = audit_request(
        no_coupon_request(fields={"id_number": "110101199001019999"}),
        deps=deps,
    )

    assert response.decision == "manual"
    assert response.evidence["id_name_match"] is True
    assert response.evidence["id_number_match"] is False
    assert "440101199001011234" not in str(response.evidence)
    assert "110101199001019999" not in str(response.evidence)


def test_no_coupon_valid_id_allows_sn_pass_and_ignores_sn_on_id_images(monkeypatch):
    parsed_batches = []

    def parse_id(texts):
        parsed_batches.append([item["text"] for item in texts])
        return {"name": "ZhangSan", "id_number": "440101199001011234", "is_valid": True}

    monkeypatch.setattr("modules.audit_runner.IDCardParser.parse", parse_id)
    deps = AuditDependencies(
        ocr=FakeOCR(
            {
                "C:/tmp/id-front.jpg": ["name ZhangSan SN001234"],
                "C:/tmp/id-back.jpg": ["valid 2020.01.01-2040.01.01"],
                "C:/tmp/sn.jpg": ["SN001234"],
            }
        ),
        forensics=FakeForensics(),
    )

    response = audit_request(no_coupon_request(), deps=deps)

    assert response.decision == "pass"
    assert response.evidence["id_name_match"] is True
    assert response.evidence["id_number_match"] is None
    assert response.evidence["id_valid"] is True
    assert parsed_batches == [["name ZhangSan SN001234"], ["valid 2020.01.01-2040.01.01"]]


def test_no_coupon_ignores_id_image_forensics_when_sn_image_passes(monkeypatch):
    monkeypatch.setattr(
        "modules.audit_runner.IDCardParser.parse",
        lambda texts: {"name": "ZhangSan", "id_number": "440101199001011234", "is_valid": True},
    )
    forensics = FakeForensics(
        {
            "C:/tmp/id-front.jpg": "suspicious",
            "C:/tmp/id-back.jpg": "suspicious",
            "C:/tmp/sn.jpg": "pass",
        }
    )
    deps = AuditDependencies(
        ocr=FakeOCR(
            {
                "C:/tmp/id-front.jpg": ["name ZhangSan"],
                "C:/tmp/id-back.jpg": ["valid 2020.01.01-2040.01.01"],
                "C:/tmp/sn.jpg": ["SN001234"],
            }
        ),
        forensics=forensics,
    )

    response = audit_request(no_coupon_request(), deps=deps)

    assert response.decision == "pass"
    assert response.evidence["id_name_match"] is True
    assert response.evidence["id_valid"] is True
    assert forensics.calls == ["C:/tmp/sn.jpg"]


def test_no_coupon_sn_image_forensics_risk_goes_manual(monkeypatch):
    monkeypatch.setattr(
        "modules.audit_runner.IDCardParser.parse",
        lambda texts: {"name": "ZhangSan", "id_number": "440101199001011234", "is_valid": True},
    )
    forensics = FakeForensics(
        {
            "C:/tmp/id-front.jpg": "pass",
            "C:/tmp/id-back.jpg": "pass",
            "C:/tmp/sn.jpg": "suspicious",
        }
    )
    deps = AuditDependencies(
        ocr=FakeOCR(
            {
                "C:/tmp/id-front.jpg": ["name ZhangSan"],
                "C:/tmp/id-back.jpg": ["valid 2020.01.01-2040.01.01"],
                "C:/tmp/sn.jpg": ["SN001234"],
            }
        ),
        forensics=forensics,
    )

    response = audit_request(no_coupon_request(), deps=deps)

    assert response.decision == "manual"
    assert response.action == "next"
    assert response.evidence["real_photo_pass"] is False
    assert forensics.calls == ["C:/tmp/sn.jpg"]


def test_guobu_home_appliance_coarse_city_address_goes_manual():
    deps = AuditDependencies(ocr=FakeOCR(["SN001234"]), forensics=FakeForensics())

    response = audit_request(guobu_home_appliance_request("Guangdong Guangzhou Tianhe District Street"), deps=deps)

    assert response.decision == "manual"
    assert response.evidence["address_detail_ok"] is False


def test_guobu_home_appliance_urban_village_address_goes_manual():
    deps = AuditDependencies(ocr=FakeOCR(["SN001234"]), forensics=FakeForensics())

    response = audit_request(guobu_home_appliance_request("Guangdong Guangzhou Tianhe Village"), deps=deps)

    assert response.decision == "manual"
    assert response.evidence["address_detail_ok"] is False


def test_guobu_home_appliance_village_address_passes():
    deps = AuditDependencies(ocr=FakeOCR(["SN001234"]), forensics=FakeForensics())

    response = audit_request(guobu_home_appliance_request("\u6e56\u5357\u7701\u957f\u6c99\u5e02\u671b\u57ce\u533a\u67d0\u9547\u67d0\u6751"), deps=deps)

    assert response.decision == "manual"
    assert response.evidence["address_detail_ok"] is True


def test_guobu_home_appliance_city_shop_address_passes():
    deps = AuditDependencies(ocr=FakeOCR(["SN001234"]), forensics=FakeForensics())

    response = audit_request(guobu_home_appliance_request("\u5e7f\u4e1c\u7701\u5e7f\u5dde\u5e02\u5929\u6cb3\u533a\u67d0\u8857\u9053\u67d0\u95e8\u5e97"), deps=deps)

    assert response.decision == "manual"
    assert response.evidence["address_detail_ok"] is True
