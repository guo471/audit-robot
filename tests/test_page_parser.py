# -*- coding: utf-8 -*-
import pytest

from modules.page_parser import merge_page_text_fields, parse_page_text


@pytest.mark.parametrize("empty_value", ["", "   ", "\t\n", None])
def test_merge_page_text_fields_ignores_empty_explicit_values(empty_value):
    result = merge_page_text_fields("SN: SN001234", {"sn": empty_value})

    assert result["sn"] == "SN001234"


def test_merge_page_text_fields_keeps_non_empty_explicit_values_first():
    result = merge_page_text_fields("SN: SN001234", {"sn": "FIELD_SN"})

    assert result["sn"] == "FIELD_SN"


def test_parse_page_text_supports_channel_alias_fields():
    result = parse_page_text(
        "\n".join(
            [
                "嘉联订单号 JL-PAGE-003",
                "渠道订单号 CH-PAGE-003",
                "所在商户 广州天河门店",
                "配送方式 快递配送",
                "客户手机号 13800138000",
                "品类类型 家电",
                "SN码 SN001234",
                "收货地址 广东省广州市天河区某路1号",
            ]
        )
    )

    assert result["jl_order_no"] == "JL-PAGE-003"
    assert result["channel_order_no"] == "CH-PAGE-003"
    assert result["merchant_name"] == "广州天河门店"
    assert result["delivery_method"] == "快递配送"
    assert result["phone"] == "13800138000"
    assert result["product_type"] == "家电"
    assert result["sn"] == "SN001234"
    assert "广东省" in result["address"]
