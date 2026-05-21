# -*- coding: utf-8 -*-
from modules.address_checker import AddressChecker


def test_detail_address_accepts_building_room():
    result = AddressChecker.is_small_range_address("广东省广州市天河区某路88号1栋2单元101室")
    assert result["status"] == "pass"


def test_detail_address_accepts_village_group():
    result = AddressChecker.is_small_range_address("湖南省长沙市望城区某镇某村三组12号")
    assert result["status"] == "pass"


def test_coarse_address_requires_manual():
    result = AddressChecker.is_small_range_address("广东省广州市天河区某街道")
    assert result["status"] == "review"
    assert "地址不够细" in result["message"]


def test_city_shop_address_passes():
    result = AddressChecker.is_small_range_address("广东省广州市天河区某街道某某门店")
    assert result["status"] == "pass"


def test_village_without_group_or_number_passes():
    result = AddressChecker.is_small_range_address("湖南省长沙市望城区某镇某村")
    assert result["status"] == "pass"


def test_urban_village_without_point_requires_manual():
    result = AddressChecker.is_small_range_address("广东省广州市天河区某村")
    assert result["status"] == "review"


def test_validate_address_returns_system_address_pass_without_ocr_fallback():
    result = AddressChecker.validate_address(
        [],
        "广东省广州市天河区某路88号1栋2单元101室",
    )

    assert result["status"] == "pass"
    assert result["message"] == "地址达到小范围粒度"
