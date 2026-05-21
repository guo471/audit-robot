# -*- coding: utf-8 -*-
"""
规则引擎
综合所有分析结果进行最终审核判定
"""
import logging
from typing import Optional

from config import OCR_CONFIDENCE_THRESHOLD

logger = logging.getLogger("rule_engine")


class RuleEngine:
    """审核规则引擎"""

    @staticmethod
    def external_decision(decision: str | None) -> str:
        """Map internal rule decisions to external service decisions."""
        if decision == "pass":
            return "pass"
        if decision in ("engine_error", "error"):
            return "error"
        return "manual"

    @staticmethod
    def check_name_consistency(
        ocr_name: Optional[str], system_name: str
    ) -> dict:
        """规则 1: 姓名一致性（无身份证图片时跳过）"""
        if not ocr_name:
            return {
                "status": "review",
                "reason": "身份证姓名未识别",
            }
        if not system_name:
            return {
                "status": "review",
                "reason": "系统无姓名数据",
            }

        # 精确匹配
        if ocr_name == system_name:
            return {"status": "pass"}

        # 模糊匹配（OCR 可能多识别或少识别空格等）
        if ocr_name.replace(" ", "") == system_name.replace(" ", ""):
            return {"status": "pass"}

        return {
            "status": "reject",
            "reason": f"姓名不一致: OCR={ocr_name}, 系统={system_name}",
        }

    @staticmethod
    def check_id_validity(id_card_info: dict) -> dict:
        """规则 2: 身份证有效期（无身份证图片时跳过）"""
        has_id_card = id_card_info.get("name") is not None or id_card_info.get("id_number") is not None
        if not has_id_card:
            return {
                "status": "review",
                "reason": "身份证有效期未识别",
            }
        if id_card_info.get("is_valid"):
            return {"status": "pass"}
        else:
            return {
                "status": "reject",
                "reason": id_card_info.get("validity_message", "身份证无效"),
            }

    @staticmethod
    def check_id_number_consistency(ocr_id_number: Optional[str], system_id_number: str | None) -> dict:
        """身份证号一致性。系统未提供身份证号时不强制。"""
        if not system_id_number:
            return {"status": "pass_skip", "reason": "系统无身份证号数据"}
        if not ocr_id_number:
            return {"status": "review", "reason": "身份证号未识别"}
        normalized_ocr = str(ocr_id_number).strip().upper().replace(" ", "")
        normalized_system = str(system_id_number).strip().upper().replace(" ", "")
        if normalized_ocr == normalized_system:
            return {"status": "pass"}
        return {"status": "reject", "reason": "身份证号与系统记录不一致"}

    @staticmethod
    def check_sn_match(sn_result: dict) -> dict:
        """规则 3: SN码一致性"""
        if sn_result.get("sn_match"):
            return {"status": "pass"}
        else:
            found = sn_result.get("found_sns", [])
            return {
                "status": "reject",
                "reason": (
                    f"SN码不匹配。系统: {sn_result.get('match_details', '')}"
                    f"，OCR识别结果: {found}"
                ),
            }

    @staticmethod
    def check_imei_match(imei_result: dict) -> dict:
        """规则 4/5: IMEI1 和 IMEI2 一致性"""
        imei1_match = imei_result.get("imei1_match", False)
        imei2_match = imei_result.get("imei2_match", True)  # 若无 IMEI2 默认通过

        if imei1_match and imei2_match:
            return {"status": "pass"}
        else:
            return {
                "status": "reject",
                "reason": f"IMEI 不匹配: {imei_result.get('match_details', '')}",
            }

    @staticmethod
    def check_image_integrity(forensics_result: dict) -> dict:
        """规则 6: 图片真实性"""
        status = forensics_result.get("status", "pass")
        if status == "pass":
            return {"status": "pass"}
        else:
            return {
                "status": "review",
                "reason": f"图片疑似造假: {forensics_result.get('message', '')}",
            }

    @staticmethod
    def check_address(address_result: dict) -> dict:
        """规则 7: 地址合规（家电类）"""
        if address_result.get("status") == "pass":
            return {"status": "pass"}
        else:
            return {
                "status": "review",
                "reason": address_result.get("message", "地址不合规"),
            }

    @staticmethod
    def check_combined_photo(ocr_texts_per_image: list[list[dict]]) -> dict:
        """规则 8: 合拍检测（3C类）

        检测是否有至少一张图同时包含产品和包装盒文字特征
        用关键词：包装/盒/箱/Package/产品名等
        """
        package_keywords = ["包装", "盒", "箱", "Package", "产品", "型号"]
        product_keywords = ["SN", "IMEI", "序列号", "型号", "Model"]

        for image_texts in ocr_texts_per_image:
            all_text = " ".join(t["text"] for t in image_texts)
            has_package = any(kw in all_text for kw in package_keywords)
            has_product = any(kw in all_text for kw in product_keywords)
            if has_package and has_product:
                return {"status": "pass", "detail": "检测到产品与包装合拍"}

        return {
            "status": "review",
            "reason": "未检测到产品与包装的合拍照片",
        }

    @classmethod
    def evaluate(
        cls,
        system_data: dict,
        id_card_info: dict,
        sn_result: dict,
        imei_result: dict,
        forensics_results: list[dict],
        address_result: Optional[dict] = None,
        is_home_appliance: bool = False,
        is_3c_product: bool = False,
        ocr_texts_per_image: Optional[list[list[dict]]] = None,
    ) -> dict:
        """执行全部规则，做出最终审核决定

        规则优先级: reject > review > pass
        """
        decisions = {}
        reject_reasons = []
        review_reasons = []

        # 规则 1: 姓名
        name_result = cls.check_name_consistency(
            id_card_info.get("name"), system_data.get("name", "")
        )
        decisions["name"] = name_result
        if name_result["status"] == "reject":
            reject_reasons.append(name_result["reason"])
        elif name_result["status"] == "review":
            review_reasons.append(name_result["reason"])

        # 规则 2: 身份证有效期
        validity_result = cls.check_id_validity(id_card_info)
        decisions["id_validity"] = validity_result
        if validity_result["status"] == "reject":
            reject_reasons.append(validity_result["reason"])
        elif validity_result["status"] == "review":
            review_reasons.append(validity_result["reason"])

        id_number_result = cls.check_id_number_consistency(
            id_card_info.get("id_number"),
            system_data.get("id_number")
            or system_data.get("id_no")
            or system_data.get("id_card_no")
            or system_data.get("identity_no")
            or system_data.get("cert_no")
            or system_data.get("certificate_no"),
        )
        decisions["id_number"] = id_number_result
        if id_number_result["status"] == "reject":
            reject_reasons.append(id_number_result["reason"])
        elif id_number_result["status"] == "review":
            review_reasons.append(id_number_result["reason"])

        # 规则 3: SN
        if system_data.get("sn"):
            sn_check = cls.check_sn_match(sn_result)
            decisions["sn"] = sn_check
            if sn_check["status"] == "reject":
                reject_reasons.append(sn_check["reason"])
            elif sn_check["status"] == "review":
                review_reasons.append(sn_check["reason"])

        # 规则 4/5: IMEI 不是强制审核项，仅保留观察状态，不参与最终阻断。
        if system_data.get("imei1"):
            decisions["imei"] = {
                "status": "info",
                "imei1_match": bool(imei_result.get("imei1_match")),
                "imei2_match": bool(imei_result.get("imei2_match")),
            }

        # 规则 6: 图片真实性
        all_forensics_pass = all(
            f["status"] == "pass" for f in forensics_results
        )
        if not all_forensics_pass:
            suspicious_count = sum(1 for f in forensics_results if f["status"] != "pass")
            # 只取前3张有问题的图片摘要，避免消息过长
            first_few = [
                f for f in forensics_results if f["status"] != "pass"
            ][:3]
            summary = "; ".join(f["message"] for f in first_few)
            if suspicious_count > 3:
                summary += f" 等共{suspicious_count}张图片异常"
            decisions["image_integrity"] = {
                "status": "review",
                "reason": summary,
            }
            review_reasons.append(decisions["image_integrity"]["reason"])
        else:
            decisions["image_integrity"] = {"status": "pass"}

        # 规则 7: 地址合规（家电类）
        if is_home_appliance and address_result:
            addr_check = cls.check_address(address_result)
            decisions["address"] = addr_check
            if addr_check["status"] == "review":
                review_reasons.append(addr_check["reason"])

        # 规则 8: 合拍检测（3C类）
        if is_3c_product and ocr_texts_per_image:
            combined_check = cls.check_combined_photo(ocr_texts_per_image)
            decisions["combined_photo"] = combined_check
            if combined_check["status"] == "review":
                review_reasons.append(combined_check["reason"])

        # 最终决策：有任何问题都转人工（skip）
        all_reasons = reject_reasons + review_reasons
        if all_reasons:
            decision = "skip"
            final_reason = " | ".join(all_reasons)
        else:
            decision = "pass"
            final_reason = None

        return {
            "decision": decision,
            "skip_reason": final_reason,
            "details": decisions,
        }

    @classmethod
    def evaluate_guobu(
        cls,
        system_data: dict,
        sn_result: dict,
        forensics_results: list[dict],
        address_result: Optional[dict] = None,
        is_home_appliance: bool = False,
    ) -> dict:
        """国补场景规则引擎

        规则:
          1. SN码匹配
          2. 图片鉴伪
          3. 地址检查（家电类）
        """
        decisions = {}
        reject_reasons = []
        review_reasons = []

        # 规则 1: SN码匹配
        if system_data.get("sn"):
            sn_check = cls.check_sn_match(sn_result)
            decisions["sn"] = sn_check
            if sn_check["status"] == "reject":
                reject_reasons.append(sn_check["reason"])
            elif sn_check["status"] == "review":
                review_reasons.append(sn_check["reason"])

        # 规则 2: 图片鉴伪
        all_forensics_pass = all(f["status"] == "pass" for f in forensics_results)
        if not all_forensics_pass:
            suspicious_count = sum(1 for f in forensics_results if f["status"] != "pass")
            first_few = [f for f in forensics_results if f["status"] != "pass"][:3]
            summary = "; ".join(f["message"] for f in first_few)
            if suspicious_count > 3:
                summary += f" 等共{suspicious_count}张图片异常"
            decisions["image_integrity"] = {"status": "review", "reason": summary}
            review_reasons.append(summary)
        else:
            decisions["image_integrity"] = {"status": "pass"}

        # 规则 3: 地址检查（家电类）
        if is_home_appliance and address_result:
            addr_check = cls.check_address(address_result)
            decisions["address"] = addr_check
            if addr_check["status"] == "review":
                review_reasons.append(addr_check["reason"])

        # 最终决策：有任何问题都转人工（skip）
        all_reasons = reject_reasons + review_reasons
        if all_reasons:
            decision = "skip"
            final_reason = " | ".join(all_reasons)
        else:
            decision = "pass"
            final_reason = None

        return {
            "decision": decision,
            "skip_reason": final_reason,
            "details": decisions,
        }
