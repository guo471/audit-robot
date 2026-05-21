# -*- coding: utf-8 -*-
"""
地址合规模块
检查家电类产品的收货地址是否细粒度到村到户
"""
import logging
import re
from typing import Optional

from config import HOME_APPLIANCE_KEYWORDS, ADDRESS_MIN_LENGTH

logger = logging.getLogger("address_checker")


class AddressChecker:
    """收货地址合规检查"""

    # 省市区关键词（用于判断地址层级）
    PROVINCE_KEYWORDS = ["省", "自治区", "直辖市"]
    CITY_KEYWORDS = ["市", "自治州", "地区", "盟"]
    DISTRICT_KEYWORDS = ["区", "县", "县级市", "旗", "自治县"]

    @classmethod
    def extract_address(cls, ocr_texts: list[dict]) -> Optional[str]:
        """从 OCR 结果中提取收货地址

        尝试从所有文本中拼接出可能的地址
        """
        # 地址通常包含省/市/区关键词
        address_candidates = []
        for item in ocr_texts:
            text = item["text"].strip()
            # 判断是否包含地址特征
            score = 0
            for kw in cls.PROVINCE_KEYWORDS + cls.CITY_KEYWORDS + cls.DISTRICT_KEYWORDS:
                if kw in text:
                    score += 1
            # 地址通常较长
            if len(text) >= 10 and score >= 1:
                address_candidates.append((text, score))

        if not address_candidates:
            return None

        # 按地址特征得分排序，取最高分
        address_candidates.sort(key=lambda x: x[1], reverse=True)
        return address_candidates[0][0]

    @classmethod
    def check_address_detail_level(cls, address: str) -> dict:
        """检查地址的细粒度

        Returns:
            {"is_detailed": bool, "detail_level": str, "matched_keywords": list}
        """
        if not address:
            return {"is_detailed": False, "detail_level": "无地址", "matched_keywords": []}

        matched_kws = []
        for kw in HOME_APPLIANCE_KEYWORDS:
            if kw in address:
                matched_kws.append(kw)

        # 判断详细程度
        is_long_enough = len(address) >= ADDRESS_MIN_LENGTH
        has_detail_kw = len(matched_kws) > 0

        if is_long_enough and has_detail_kw:
            detail_level = "详细"
            is_detailed = True
        elif is_long_enough and not has_detail_kw:
            detail_level = "较笼统（缺少村/组/户/号等细节）"
            is_detailed = False
        else:
            detail_level = f"过短({len(address)}字)"
            is_detailed = False

        return {
            "is_detailed": is_detailed,
            "detail_level": detail_level,
            "address_length": len(address),
            "matched_keywords": matched_kws,
        }

    @staticmethod
    def is_small_range_address(address: str | None) -> dict:
        """Conservative detail check for home-appliance delivery addresses."""
        if not address or len(address.strip()) < 12:
            return {
                "status": "review",
                "detail": {"is_detailed": False},
                "message": "地址不够细：长度不足",
            }

        normalized_address = address.strip()
        building_markers = ("号", "栋", "幢", "单元", "室", "房", "门牌", "店铺", "门店", "商铺", "店")
        coarse_markers = ("省", "市", "区", "县", "镇", "乡", "街道", "村")
        rural_markers = ("镇", "乡")
        urban_markers = ("街道",)

        has_building_detail = any(marker in normalized_address for marker in building_markers)
        has_rural_village_detail = (
            "村" in normalized_address
            and any(marker in normalized_address for marker in rural_markers)
            and not any(marker in normalized_address for marker in urban_markers)
        )

        if has_building_detail or has_rural_village_detail:
            return {
                "status": "pass",
                "detail": {"is_detailed": True},
                "message": "地址达到小范围粒度",
            }

        if any(marker in normalized_address for marker in coarse_markers):
            return {
                "status": "review",
                "detail": {"is_detailed": False},
                "message": "地址不够细：仅到行政区划或村级",
            }

        return {
            "status": "review",
            "detail": {"is_detailed": False},
            "message": "地址不够细：缺少门牌/楼栋/村组信息",
        }

    @classmethod
    def validate_address(
        cls, ocr_texts: list[dict], system_address: Optional[str] = None
    ) -> dict:
        """完整的地址校验

        Args:
            ocr_texts: OCR 结果
            system_address: 系统记录中的地址（可选，用于交叉验证）

        Returns:
            {"status": str, "detail": dict, "message": str}
        """
        if system_address:
            detail_result = cls.is_small_range_address(system_address)
            return detail_result

        extracted = cls.extract_address(ocr_texts)
        address = extracted or system_address

        if not address:
            return {
                "status": "review",
                "detail": {"is_detailed": False},
                "message": "无法提取收货地址",
            }

        detail_check = cls.check_address_detail_level(address)

        if detail_check["is_detailed"]:
            return {
                "status": "pass",
                "detail": detail_check,
                "message": f"地址已到村到户（含关键词: {detail_check['matched_keywords']}）",
            }
        else:
            return {
                "status": "review",
                "detail": detail_check,
                "message": f"地址不够详细: {detail_check['detail_level']}",
            }
