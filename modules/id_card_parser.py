# -*- coding: utf-8 -*-
"""
身份证信息解析模块
从 OCR 结果中结构化提取身份证信息
"""
import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger("id_card_parser")


class IDCardParser:
    """身份证 OCR 结果解析器"""

    # 姓名关键词（中英文）
    NAME_KEYWORDS = ["姓名", "Name", "NAME", "姓  名"]

    # 身份证号关键词
    ID_NUMBER_KEYWORDS = [
        "公民身份号码", "身份证号", "身份证号码",
        "身份号码", "ID", "身份证",
    ]

    # 有效期关键词
    VALIDITY_KEYWORDS = ["有效期限", "有效期", "Valid"]

    # 住址关键词
    ADDRESS_KEYWORDS = ["住址", "Address", "地址", "住  址"]

    # 身份证号正则 (18 位，末位可能为 X)
    ID_NUMBER_PATTERN = r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"

    # 日期正则 (多种格式)
    DATE_PATTERN = r"(\d{4})[.\-年/](\d{1,2})[.\-月/](\d{1,2})[.\-日]?"

    @staticmethod
    def clean_text(text: str) -> str:
        """去除文本中的空格和特殊字符"""
        return text.replace(" ", "").replace("　", "").strip()

    @classmethod
    def find_field(cls, texts: list[dict], keywords: list[str]) -> Optional[str]:
        """在 OCR 结果中查找关键词后面的字段值"""
        for i, item in enumerate(texts):
            t = cls.clean_text(item["text"])
            for kw in keywords:
                if kw in t:
                    # 提取关键词后面的内容
                    idx = t.find(kw)
                    value = t[idx + len(kw):]
                    if value:
                        return value
                    # 如果关键词在行末，取下一行
                    if i + 1 < len(texts):
                        return cls.clean_text(texts[i + 1]["text"])
        return None

    @classmethod
    def extract_name(cls, texts: list[dict]) -> Optional[str]:
        """提取姓名"""
        result = cls.find_field(texts, cls.NAME_KEYWORDS)
        if result:
            # 过滤掉明显的非姓名内容
            result = cls.clean_text(result)
            if len(result) <= 10 and not re.search(r"\d", result):
                return result
        return None

    @classmethod
    def extract_id_number(cls, texts: list[dict]) -> Optional[str]:
        """提取身份证号（优先从关键词后面找，再全文正则匹配）"""
        # 先找关键词后面的内容
        result = cls.find_field(texts, cls.ID_NUMBER_KEYWORDS)
        if result:
            cleaned = cls.clean_text(result)
            # 从结果中提取身份证号
            match = re.search(cls.ID_NUMBER_PATTERN, cleaned)
            if match:
                return match.group(0)

        # 全文正则匹配
        for item in texts:
            match = re.search(cls.ID_NUMBER_PATTERN, item["text"])
            if match:
                return match.group(0)
        return None

    @classmethod
    def extract_validity(cls, texts: list[dict]) -> tuple[Optional[str], Optional[str]]:
        """提取有效期

        Returns:
            (valid_from, valid_to): 起始日期和截止日期字符串，如 ("2020-01-01", "2040-01-01")
        """
        result = cls.find_field(texts, cls.VALIDITY_KEYWORDS)
        if result:
            dates = re.findall(cls.DATE_PATTERN, result)
            if len(dates) >= 2:
                return (
                    f"{dates[0][0]}-{dates[0][1].zfill(2)}-{dates[0][2].zfill(2)}",
                    f"{dates[1][0]}-{dates[1][1].zfill(2)}-{dates[1][2].zfill(2)}",
                )

        # 全文搜索日期对（find_field 拿不到有效日期时回退到这里）
        for item in texts:
            dates = re.findall(cls.DATE_PATTERN, item["text"])
            if len(dates) >= 2:
                return (
                    f"{dates[0][0]}-{dates[0][1].zfill(2)}-{dates[0][2].zfill(2)}",
                    f"{dates[1][0]}-{dates[1][1].zfill(2)}-{dates[1][2].zfill(2)}",
                )
        return None, None

    @classmethod
    def extract_address(cls, texts: list[dict]) -> Optional[str]:
        """提取住址"""
        result = cls.find_field(texts, cls.ADDRESS_KEYWORDS)
        if result:
            result = cls.clean_text(result)
            if len(result) > 5:
                return result
        return None

    @classmethod
    def check_validity(cls, valid_to: Optional[str]) -> tuple[bool, str]:
        """检查身份证是否在有效期内

        Returns:
            (is_valid, message)
        """
        if not valid_to:
            return False, "无法提取有效期"

        try:
            # 处理 "长期" 这种情况
            if "长期" in valid_to or "永久" in valid_to:
                return True, "长期有效"

            end_date = datetime.strptime(valid_to, "%Y-%m-%d")
            if end_date >= datetime.now():
                return True, f"有效至 {valid_to}"
            else:
                return False, f"已过期（有效期至 {valid_to}）"
        except ValueError:
            return False, f"无法解析有效期: {valid_to}"

    @classmethod
    def parse(cls, texts: list[dict]) -> dict:
        """从 OCR 结果中解析完整的身份证信息"""
        name = cls.extract_name(texts)
        id_number = cls.extract_id_number(texts)
        valid_from, valid_to = cls.extract_validity(texts)
        address = cls.extract_address(texts)

        if name is None and id_number is None:
            logger.warning("未在 OCR 结果中找到身份证信息")

        valid_check = cls.check_validity(valid_to)

        return {
            "name": name,
            "id_number": id_number,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "address": address,
            "is_valid": valid_check[0],
            "validity_message": valid_check[1],
        }
