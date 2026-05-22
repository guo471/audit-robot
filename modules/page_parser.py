# -*- coding: utf-8 -*-
"""Parse coarse audit page text into structured fields."""
from __future__ import annotations

import logging
import re
from typing import Any

from .privacy import redact_text


FIELD_STOP = r"(?:嘉联订单号|渠道订单号|订单号|ID|姓名|客户姓名|SN码|SN|序列号|产品序列号|S/N|IMEI1|IMEI2|地址|收货地址|所在商户|商户名称|配送方式|手机号|客户手机号|产品类型|品类类型|类型|商品名称|产品名称|$)"


def _first_match(patterns: list[str], text: str, flags: int = 0) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip()
    return ""


def parse_page_text(text: str) -> dict[str, str]:
    """Extract common audit fields from a large visible page text block."""
    result: dict[str, str] = {}
    if not text:
        return result

    normalized = str(text).replace("\r\n", "\n")

    jl_order_no = _first_match([
        r"(?:嘉联订单号|(?<!渠道)订单号)\s*[:：]?\s*([A-Za-z0-9_\-]{1,64})",
        r"(?:^|\n)\s*ID\s*[:：]?\s*([A-Za-z0-9_\-]{1,64})",
    ], normalized)
    if jl_order_no:
        result["jl_order_no"] = jl_order_no

    channel_order_no = _first_match([
        r"渠道订单号\s*[:：]?\s*([A-Za-z0-9_\-]{1,64})",
    ], normalized)
    if channel_order_no:
        result["channel_order_no"] = channel_order_no

    name = _first_match([
        rf"(?:姓名|客户姓名|申请人姓名)\s*[:：]?\s*([^\s:：,，;；]{{2,10}})",
    ], normalized)
    if name:
        result["name"] = name

    merchant_name = _first_match([
        r"(?:所在商户|商户名称)\s*[:：]?\s*([^\n\r:：,，;；]{2,60})",
    ], normalized)
    if merchant_name:
        result["merchant_name"] = merchant_name

    delivery_method = _first_match([
        r"配送方式\s*[:：]?\s*([^\n\r:：,，;；]{2,30})",
    ], normalized)
    if delivery_method:
        result["delivery_method"] = delivery_method

    phone = _first_match([
        r"(?:手机号|客户手机号)\s*[:：]?\s*(1[3-9]\d{9})",
    ], normalized)
    if phone:
        result["phone"] = phone

    sn = _first_match([
        r"(?:SN码|sn码|SN|序列号|产品序列号|Serial|S/N)\s*[:：]?\s*([A-Za-z0-9\-]{6,40})",
    ], normalized, re.IGNORECASE)
    if sn:
        result["sn"] = sn

    imei1 = _first_match([r"IMEI1\s*[:：]?\s*(\d{15})"], normalized, re.IGNORECASE)
    if imei1:
        result["imei1"] = imei1

    imei2 = _first_match([r"IMEI2\s*[:：]?\s*(\d{15})"], normalized, re.IGNORECASE)
    if imei2:
        result["imei2"] = imei2

    id_number = _first_match([
        r"(?:身份证号|证件号码|身份证号码)\s*[:：]?\s*(\d{17}[\dXx])",
    ], normalized)
    if id_number:
        result["id_number"] = id_number.upper()

    address = _first_match([
        rf"(?:收货地址|地址)\s*[:：]?\s*(.{{5,120}}?)(?=\s*{FIELD_STOP})",
    ], normalized, re.DOTALL)
    if address:
        result["address"] = re.sub(r"\s+", "", address)

    product_type = _first_match([
        r"(?:产品类型|品类类型|类型)\s*[:：]?\s*([^\s:：,，;；]{2,30})",
    ], normalized)
    if product_type:
        result["product_type"] = product_type

    product_name = _first_match([
        r"(?:商品名称|产品名称)\s*[:：]?\s*(.{2,80}?)(?=\s*(?:SN码|SN|序列号|产品类型|品类类型|地址|$))",
    ], normalized, re.DOTALL)
    if product_name:
        result["product_name"] = re.sub(r"\s+", " ", product_name).strip()

    safe_result = {
        key: ("[REDACTED]" if key in {"name", "address", "id_number", "phone"} else redact_text(value))
        for key, value in result.items()
    }
    logging.getLogger("audit").info("从页面文本解析到: %s", safe_result)
    return result


def merge_page_text_fields(page_text: str, fields: dict[str, Any] | None) -> dict[str, Any]:
    """Merge parsed page text with explicit fields; explicit fields win."""
    parsed = parse_page_text(page_text)
    merged: dict[str, Any] = dict(parsed)
    for key, value in dict(fields or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        merged[key] = value
    return merged
