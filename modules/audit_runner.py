# -*- coding: utf-8 -*-
import time
from dataclasses import dataclass
from typing import Optional

from .audit_models import AuditRequest, AuditResponse
from .address_checker import AddressChecker
from .category_classifier import classify_audit_category
from .code_extractor import CodeExtractor
from .id_card_parser import IDCardParser
from .image_forensics import ImageForensics
from .image_role import group_images_by_role, required_roles_present
from .ocr_engine import OCREngine


@dataclass
class AuditDependencies:
    ocr: Optional[object] = None
    forensics: Optional[object] = None

    def get_ocr(self):
        if self.ocr is None:
            self.ocr = OCREngine()
        return self.ocr

    def get_forensics(self):
        if self.forensics is None:
            self.forensics = ImageForensics()
        return self.forensics


def audit_request(
    request: AuditRequest,
    deps: Optional[AuditDependencies] = None,
    timeout_sec: float = 60,
) -> AuditResponse:
    deps = deps or AuditDependencies()
    started = time.monotonic()

    def elapsed() -> float:
        return time.monotonic() - started

    def timed_out() -> bool:
        return elapsed() >= timeout_sec

    def evidence(sn_match: bool = False) -> dict:
        return {
            "sn_match": sn_match,
            "image_roles_ok": True,
            "real_photo_pass": True,
            "id_name_match": None,
            "id_number_match": None,
            "id_valid": None,
            "address_detail_ok": None,
        }

    def system_name() -> str:
        for key in ("name", "customer_name", "user_name", "real_name", "id_name", "applicant_name"):
            value = request.fields.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    def system_address() -> str:
        for key in (
            "address",
            "receiver_address",
            "receive_address",
            "shipping_address",
            "store_address",
            "shop_address",
        ):
            value = request.fields.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    def system_id_number() -> str:
        for key in ("id_number", "id_no", "id_card_no", "identity_no", "cert_no", "certificate_no"):
            value = request.fields.get(key)
            if value is not None and str(value).strip():
                return str(value).strip().upper().replace(" ", "")
        return ""

    def safe_evidence(**updates) -> dict:
        result = evidence(False)
        result.update(updates)
        return result

    def manual(scene: str, path: str, reason: str, extra_evidence: Optional[dict] = None):
        return AuditResponse.manual(
            jl_order_no=request.jl_order_no,
            scene=scene,
            path=path,
            elapsed_sec=timeout_sec if reason == "\u5355\u5355\u8d85\u65f6" else elapsed(),
            manual_reason=reason,
            evidence=extra_evidence,
        )

    category = classify_audit_category(request.scene_hint, request.fields)
    scene = category.scene
    if not category.supported:
        return manual(scene, "precheck", category.reason or "\u5ba1\u6838\u54c1\u7c7b\u6682\u4e0d\u652f\u6301")

    if not request.jl_order_no:
        return manual(scene, "precheck", "\u5409\u8054\u8ba2\u5355\u53f7\u4e3a\u7a7a")

    grouped = group_images_by_role(request.images)
    if not required_roles_present(grouped, scene):
        return manual(scene, "precheck", "\u56fe\u7247\u89d2\u8272\u4e0d\u5b8c\u6574\u6216\u65e0\u6cd5\u8bc6\u522b")

    if category.category == "home_appliance":
        address = system_address()
        if not address:
            return manual(scene, "precheck", "\u6536\u8d27\u5730\u5740\u4e3a\u7a7a", safe_evidence(address_detail_ok=False))
        address_result = AddressChecker.is_small_range_address(address)
        address_ok = address_result.get("status") == "pass"
        if not address_ok:
            return manual(scene, "precheck", "\u6536\u8d27\u5730\u5740\u7c92\u5ea6\u4e0d\u8db3", safe_evidence(address_detail_ok=False))

    system_sn = str(
        request.fields.get("sn")
        or request.fields.get("SN")
        or request.fields.get("serial_no")
        or request.fields.get("system_sn")
        or ""
    )
    if not system_sn:
        return manual(scene, "precheck", "\u9875\u9762SN\u4e3a\u7a7a")

    paths = [image.path for image in request.images]
    forensics = deps.get_forensics()
    for path in paths:
        if timed_out():
            return manual(scene, "precheck", "\u5355\u5355\u8d85\u65f6")
        result = forensics.full_analysis(path)
        if timed_out():
            return manual(scene, "precheck", "\u5355\u5355\u8d85\u65f6")
        if result.get("status") != "pass":
            return manual(scene, "precheck", "\u56fe\u7247\u98ce\u9669\u672a\u901a\u8fc7", {"forensics": result})

    ocr = deps.get_ocr()
    enhanced_ocr_by_path = {}
    sn_images = (
        grouped.get("product_photo", [])
        + grouped.get("unboxing_photo", [])
        + grouped.get("activation_photo", [])
    )
    sn_paths = [image.path for image in sn_images]

    if scene == "no_coupon":
        expected_name = system_name()
        if not expected_name:
            return manual(scene, "precheck", "\u7cfb\u7edf\u59d3\u540d\u4e3a\u7a7a", safe_evidence(id_name_match=False, id_valid=False))

        parsed_cards = []
        for image in grouped.get("id_front", []) + grouped.get("id_back", []):
            if timed_out():
                return manual(scene, "fast", "\u5355\u5355\u8d85\u65f6")
            ocr_texts = ocr.extract_text_enhanced(image.path)
            enhanced_ocr_by_path[image.path] = ocr_texts
            if timed_out():
                return manual(scene, "fast", "\u5355\u5355\u8d85\u65f6")
            parsed_cards.append(IDCardParser.parse(ocr_texts) or {})

        parsed_name = next((str(card.get("name")).strip() for card in parsed_cards if card.get("name")), "")
        parsed_id_number = next(
            (
                str(card.get("id_number")).strip().upper().replace(" ", "")
                for card in parsed_cards
                if card.get("id_number")
            ),
            "",
        )
        id_valid = any(bool(card.get("is_valid")) for card in parsed_cards)
        names_match = bool(parsed_name) and parsed_name.replace(" ", "") == expected_name.replace(" ", "")
        expected_id_number = system_id_number()
        id_number_match = None
        if expected_id_number:
            id_number_match = bool(parsed_id_number) and parsed_id_number == expected_id_number

        if not parsed_name:
            return manual(scene, "precheck", "\u8eab\u4efd\u8bc1\u4fe1\u606f\u8bc6\u522b\u5931\u8d25", safe_evidence(id_name_match=False, id_number_match=id_number_match, id_valid=id_valid))
        if not names_match:
            return manual(scene, "precheck", "\u8eab\u4efd\u8bc1\u59d3\u540d\u4e0e\u7cfb\u7edf\u59d3\u540d\u4e0d\u4e00\u81f4", safe_evidence(id_name_match=False, id_number_match=id_number_match, id_valid=id_valid))
        if expected_id_number and not id_number_match:
            return manual(scene, "precheck", "\u8eab\u4efd\u8bc1\u53f7\u4e0e\u7cfb\u7edf\u8bb0\u5f55\u4e0d\u4e00\u81f4", safe_evidence(id_name_match=True, id_number_match=False, id_valid=id_valid))
        if not id_valid:
            return manual(scene, "precheck", "\u8eab\u4efd\u8bc1\u6709\u6548\u671f\u65e0\u6548", safe_evidence(id_name_match=True, id_number_match=id_number_match, id_valid=False))

    for path in sn_paths:
        if timed_out():
            return manual(scene, "fast", "\u5355\u5355\u8d85\u65f6")
        ocr_texts = enhanced_ocr_by_path.get(path)
        if ocr_texts is None:
            ocr_texts = ocr.extract_text_enhanced(path)
            enhanced_ocr_by_path[path] = ocr_texts
        if timed_out():
            return manual(scene, "fast", "\u5355\u5355\u8d85\u65f6")
        match = CodeExtractor.match_system_sn(ocr_texts, system_sn)
        if match.get("sn_match"):
            pass_evidence = evidence(True)
            if scene == "no_coupon":
                pass_evidence["id_name_match"] = True
                pass_evidence["id_number_match"] = id_number_match
                pass_evidence["id_valid"] = True
            if category.category == "home_appliance":
                pass_evidence["address_detail_ok"] = True
            return AuditResponse.pass_(
                jl_order_no=request.jl_order_no,
                scene=scene,
                path="fast",
                elapsed_sec=elapsed(),
                evidence=pass_evidence,
            )

    for path in sn_paths:
        if timed_out():
            return manual(scene, "slow", "\u5355\u5355\u8d85\u65f6")
        ocr_texts = ocr.extract_text_tiled(path)
        if timed_out():
            return manual(scene, "slow", "\u5355\u5355\u8d85\u65f6")
        match = CodeExtractor.match_system_sn(ocr_texts, system_sn)
        if match.get("sn_match"):
            pass_evidence = evidence(True)
            if scene == "no_coupon":
                pass_evidence["id_name_match"] = True
                pass_evidence["id_number_match"] = id_number_match
                pass_evidence["id_valid"] = True
            if category.category == "home_appliance":
                pass_evidence["address_detail_ok"] = True
            return AuditResponse.pass_(
                jl_order_no=request.jl_order_no,
                scene=scene,
                path="slow",
                elapsed_sec=elapsed(),
                evidence=pass_evidence,
            )

    miss_evidence = evidence(False)
    if scene == "no_coupon":
        miss_evidence["id_name_match"] = True
        miss_evidence["id_number_match"] = id_number_match
        miss_evidence["id_valid"] = True
    if category.category == "home_appliance":
        miss_evidence["address_detail_ok"] = True
    return manual(scene, "slow", "SN\u672a\u9ad8\u7f6e\u4fe1\u8bc6\u522b", miss_evidence)
