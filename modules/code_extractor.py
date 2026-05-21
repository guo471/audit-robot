# -*- coding: utf-8 -*-
"""
SN 码和 IMEI 码提取模块
使用正则匹配从 OCR 结果中提取序列号和设备标识
"""
import logging
import re
from collections import Counter
from typing import Optional

logger = logging.getLogger("code_extractor")


class CodeExtractor:
    """SN/IMEI 码提取器"""

    # IMEI: 15 位纯数字
    IMEI_PATTERN = re.compile(r"\b(\d{15})\b")

    # SN: 通常为字母+数字组合，8-20 位
    SN_PATTERN = re.compile(r"\b([A-Za-z0-9]{8,30})\b")

    # SN 带横杠（如 "43K3XXX-A005102"、"0694-1571-4163-16"）
    SN_HYPHEN_PATTERN = re.compile(r"\b([A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)\b")

    # 带前缀的 IMEI（如 "IMEI1：861234567890123"）
    IMEI_WITH_PREFIX = re.compile(
        r"(?:IMEI|imei|Imei)[\s:：]*(\d{15})"
    )

    # 带前缀的 SN
    SN_WITH_PREFIX = re.compile(
        r"(?:SN|sn|Sn|S/N|s/n|序列号|产品序列号|Serial|产品编号|机身号)[\s:：码#]*([A-Za-z0-9]{6,25})"
    )

    @staticmethod
    def _clean_sn(sn: str) -> str:
        """去掉 SN 中的空格和特殊空白字符，用于宽松比对"""
        return sn.replace(" ", "").replace(" ", "").replace("\t", "")

    @staticmethod
    def _levenshtein_distance(s1: str, s2: str) -> int:
        """计算两个字符串的编辑距离"""
        if len(s1) < len(s2):
            s1, s2 = s2, s1
        if not s2:
            return len(s1)
        prev = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                cost = 0 if c1 == c2 else 1
                curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
            prev = curr
        return prev[-1]

    @classmethod
    def extract_imei_list(cls, texts: list[dict]) -> list[str]:
        """从 OCR 文本中提取所有 IMEI 码

        优先匹配有 "IMEI" 前缀的，再匹配合法的 15 位数字
        """
        imeis = set()

        # 先匹配有前缀的
        for item in texts:
            matches = cls.IMEI_WITH_PREFIX.findall(item["text"])
            for m in matches:
                imeis.add(m)

        # 再匹配所有 15 位数字（去重）
        for item in texts:
            matches = cls.IMEI_PATTERN.findall(item["text"])
            for m in matches:
                imeis.add(m)

        result = sorted(imeis)
        if result:
            logger.info("提取到 IMEI 候选: %s 个", len(result))
        return result

    @classmethod
    def extract_sn_list(cls, texts: list[dict]) -> list[str]:
        """从 OCR 文本中提取所有 SN 码"""
        sns = set()

        for item in texts:
            raw = item["text"]

            # 先匹配有前缀的（如 "SN:xxx"、"序列号:xxx"）
            matches = cls.SN_WITH_PREFIX.findall(raw)
            for m in matches:
                # 纯数字且长度 <= 11 可能是电话号码，过滤掉
                if m.isdigit() and len(m) <= 11:
                    continue
                sns.add(cls._clean_sn(m))

            # 匹配带横杠的 SN（如 "43K3XXX-A005102"）
            hyphen_matches = cls.SN_HYPHEN_PATTERN.findall(raw)
            for m in hyphen_matches:
                cleaned = cls._clean_sn(m)
                # 带横杠的代码要足够长（>=12）且至少含1个字母，避免把型号当SN
                if len(cleaned) >= 12:
                    sns.add(cleaned)

            # 匹配无前缀的普通字母数字组合
            matches = cls.SN_PATTERN.findall(raw)
            for m in matches:
                cleaned = cls._clean_sn(m)
                # 至少 2 个字母，避免把纯数字当 SN
                if sum(c.isalpha() for c in cleaned) >= 2:
                    sns.add(cleaned)

        result = sorted(sns)
        if result:
            logger.info("提取到 SN 候选: %s 个", len(result))
        return result

    @classmethod
    def match_system_imei(
        cls, ocr_texts: list[dict], system_imei1: str, system_imei2: str
    ) -> dict:
        """将 OCR 提取的 IMEI 与系统 IMEI 做比对

        Returns:
            {
                "imei1_match": bool,
                "imei2_match": bool,
                "found_imeis": list[str],
                "match_details": str
            }
        """
        found_imeis = cls.extract_imei_list(ocr_texts)

        imei1_match = system_imei1 in found_imeis if system_imei1 else False
        imei2_match = system_imei2 in found_imeis if system_imei2 else False

        details = []
        if system_imei1:
            details.append(
                f"IMEI1 系统={system_imei1} {'匹配' if imei1_match else '不匹配'}"
            )
        if system_imei2:
            details.append(
                f"IMEI2 系统={system_imei2} {'匹配' if imei2_match else '不匹配'}"
            )

        return {
            "imei1_match": imei1_match,
            "imei2_match": imei2_match,
            "found_imeis": found_imeis,
            "match_details": "; ".join(details),
        }

    @classmethod
    def match_system_sn(
        cls, ocr_texts: list[dict], system_sn: str
    ) -> dict:
        """将 OCR 提取的 SN 与系统 SN 做比对（自动去除空格对比）"""
        found_sns = cls.extract_sn_list(ocr_texts)

        # 标准化系统 SN：去除空格
        clean_system = cls._clean_sn(system_sn) if system_sn else ""

        # 精确匹配（先去空格）
        sn_match = clean_system in found_sns if clean_system else False
        match_type = "exact"

        # 模糊匹配（大小写不敏感）
        if not sn_match and clean_system:
            sys_upper = clean_system.upper()
            for fs in found_sns:
                fs_upper = fs.upper()
                if fs_upper == sys_upper:
                    sn_match = True
                    match_type = "case_insensitive"
                    break

        return {
            "sn_match": sn_match,
            "found_sns": found_sns,
            "match_type": match_type,
            "match_details": (
                f"SN 系统={clean_system} {'匹配' if sn_match else '不匹配'}"
                if system_sn
                else "系统无 SN 数据"
            ),
        }

    @classmethod
    def multi_frame_vote(
        cls, ocr_results_per_image: list[list[dict]], system_value: str,
        extract_func, tolerance: int = 1
    ) -> tuple[bool, list[str], float]:
        """多帧投票：多张图对同一编码的识别结果进行投票

        Args:
            ocr_results_per_image: 每张图的 OCR 结果列表
            system_value: 系统值
            extract_func: 提取函数（extract_imei_list 或 extract_sn_list）
            tolerance: 允许的字符差异数（模糊匹配容差）

        Returns:
            (is_match, all_found_values, confidence)
        """
        all_values = []
        for texts in ocr_results_per_image:
            values = extract_func(texts)
            all_values.extend(values)

        if not all_values or not system_value:
            return False, all_values, 0.0

        # 统计出现次数
        counter = Counter(all_values)
        # 系统值出现的次数
        match_count = counter.get(system_value, 0)

        # 模糊匹配（大小写不敏感）
        system_upper = system_value.upper()
        fuzzy_match_count = sum(
            1 for v in all_values if v.upper() == system_upper
        )

        total = len(all_values) if all_values else 1
        confidence = max(match_count, fuzzy_match_count) / total

        is_match = match_count > 0 or fuzzy_match_count >= total * 0.5

        return is_match, list(set(all_values)), confidence
