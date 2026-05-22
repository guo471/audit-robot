# -*- coding: utf-8 -*-
import time

from modules.audit_models import AuditImage, AuditRequest
from modules.audit_runner import AuditDependencies, audit_request


class FakeOCR:
    def __init__(self, enhanced_texts, tiled_texts=None, delay_sec=0):
        self.enhanced_texts = enhanced_texts if isinstance(enhanced_texts, dict) else list(enhanced_texts)
        self.tiled_texts = tiled_texts if isinstance(tiled_texts, dict) else list(tiled_texts or [])
        self.delay_sec = delay_sec
        self.enhanced_calls = 0
        self.tiled_calls = 0

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
        scene_hint="家电数码3C（国补2026）",
        fields={"product_type": "3c", "sn": sn},
        images=images or [
            AuditImage(title="商品照片", path="C:/tmp/product.jpg"),
            AuditImage(title="拆封照片", path="C:/tmp/unbox.jpg"),
            AuditImage(title="SN码采集照片", path="C:/tmp/sn.jpg"),
        ],
    )


def guobu_home_appliance_request(address):
    return AuditRequest(
        jl_order_no="JL001",
        scene_hint="家电数码3C（国补2026）",
        fields={"product_type": "家电", "product_name": "海尔冰箱", "sn": "SN001234", "address": address},
        images=[
            AuditImage(title="商品照片", path="C:/tmp/product.jpg"),
            AuditImage(title="拆封照片", path="C:/tmp/unbox.jpg"),
            AuditImage(title="SN码采集照片", path="C:/tmp/sn.jpg"),
        ],
    )


def no_coupon_request(images=None, sn="SN001234", fields=None):
    request_fields = {"product_type": "手机数码", "sn": sn, "name": "张三"}
    if fields:
        request_fields.update(fields)
    return AuditRequest(
        jl_order_no="JL002",
        scene_hint="非发券审核",
        fields=request_fields,
        images=images or [
            AuditImage(title="二代居民身份证人像面", path="C:/tmp/id-front.jpg"),
            AuditImage(title="二代居民身份证国徽面", path="C:/tmp/id-back.jpg"),
            AuditImage(title="SN码采集照片", path="C:/tmp/sn.jpg"),
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

    assert response.decision == "pass"
    assert response.path == "fast"
    assert response.evidence["sn_match"] is True
    assert response.evidence["sn_checked_image"] == "last"
    assert ocr.enhanced_calls == 1
    assert "found_sns" not in response.evidence
    assert "match_details" not in response.evidence


def test_channel_order_no_fallback_reaches_later_precheck():
    request = AuditRequest(
        jl_order_no="",
        channel_order_no="CH-FALLBACK-001",
        scene_hint="家电数码3C（国补2026）",
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

    assert response.decision == "pass"
    assert response.path == "slow"
    assert ocr.tiled_calls >= 1


def test_unknown_image_role_does_not_go_manual_when_last_sn_matches():
    deps = AuditDependencies(ocr=FakeOCR({"C:/tmp/other.jpg": ["SN001234"]}), forensics=FakeForensics())

    response = audit_request(
        guobu_request(images=[AuditImage(title="其他材料", path="C:/tmp/other.jpg")]),
        deps=deps,
    )

    assert response.decision == "pass"
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
        lambda texts: {"name": "李四", "is_valid": True},
    )
    deps = AuditDependencies(ocr=FakeOCR({"C:/tmp/sn.jpg": ["SN001234"]}), forensics=FakeForensics())

    response = audit_request(no_coupon_request(), deps=deps)

    assert response.decision == "manual"
    assert response.evidence["id_name_match"] is False
    assert response.evidence["id_valid"] is True
    assert "李四" not in str(response.evidence)
    assert "张三" not in str(response.evidence)


def test_no_coupon_expired_id_goes_manual(monkeypatch):
    monkeypatch.setattr(
        "modules.audit_runner.IDCardParser.parse",
        lambda texts: {"name": "张三", "is_valid": False},
    )
    deps = AuditDependencies(ocr=FakeOCR({"C:/tmp/sn.jpg": ["SN001234"]}), forensics=FakeForensics())

    response = audit_request(no_coupon_request(), deps=deps)

    assert response.decision == "manual"
    assert response.evidence["id_name_match"] is True
    assert response.evidence["id_valid"] is False


def test_no_coupon_id_number_mismatch_goes_manual(monkeypatch):
    monkeypatch.setattr(
        "modules.audit_runner.IDCardParser.parse",
        lambda texts: {"name": "张三", "id_number": "440101199001011234", "is_valid": True},
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
        return {"name": "张三", "id_number": "440101199001011234", "is_valid": True}

    monkeypatch.setattr("modules.audit_runner.IDCardParser.parse", parse_id)
    deps = AuditDependencies(
        ocr=FakeOCR(
            {
                "C:/tmp/id-front.jpg": ["姓名张三 SN001234"],
                "C:/tmp/id-back.jpg": ["有效期限2020.01.01-2040.01.01"],
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
    assert parsed_batches == [["姓名张三 SN001234"], ["有效期限2020.01.01-2040.01.01"]]


def test_no_coupon_ignores_id_image_forensics_when_sn_image_passes(monkeypatch):
    monkeypatch.setattr(
        "modules.audit_runner.IDCardParser.parse",
        lambda texts: {"name": "张三", "id_number": "440101199001011234", "is_valid": True},
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
                "C:/tmp/id-front.jpg": ["姓名张三"],
                "C:/tmp/id-back.jpg": ["有效期限2020.01.01-2040.01.01"],
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
        lambda texts: {"name": "张三", "id_number": "440101199001011234", "is_valid": True},
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
                "C:/tmp/id-front.jpg": ["姓名张三"],
                "C:/tmp/id-back.jpg": ["有效期限2020.01.01-2040.01.01"],
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

    response = audit_request(guobu_home_appliance_request("广东省广州市天河区某街道"), deps=deps)

    assert response.decision == "manual"
    assert response.evidence["address_detail_ok"] is False
    assert "广东省" not in str(response.evidence)


def test_guobu_home_appliance_urban_village_address_goes_manual():
    deps = AuditDependencies(ocr=FakeOCR(["SN001234"]), forensics=FakeForensics())

    response = audit_request(guobu_home_appliance_request("广东省广州市天河区某村"), deps=deps)

    assert response.decision == "manual"
    assert response.evidence["address_detail_ok"] is False


def test_guobu_home_appliance_village_address_passes():
    deps = AuditDependencies(ocr=FakeOCR(["SN001234"]), forensics=FakeForensics())

    response = audit_request(guobu_home_appliance_request("湖南省长沙市望城区某镇某村"), deps=deps)

    assert response.decision == "pass"
    assert response.evidence["address_detail_ok"] is True


def test_guobu_home_appliance_city_shop_address_passes():
    deps = AuditDependencies(ocr=FakeOCR(["SN001234"]), forensics=FakeForensics())

    response = audit_request(guobu_home_appliance_request("广东省广州市天河区某街道某某门店"), deps=deps)

    assert response.decision == "pass"
    assert response.evidence["address_detail_ok"] is True
