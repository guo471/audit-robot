# -*- coding: utf-8 -*-
from modules.category_classifier import classify_audit_category
from run_audit import determine_product_type


def test_classifies_non_coupon_3c():
    result = classify_audit_category("非发券审核", {"product_type": "手机数码", "product_name": "华为手机"})

    assert result.scene == "no_coupon"
    assert result.category == "3c"
    assert result.supported is True


def test_classifies_guobu_3c():
    result = classify_audit_category("家电数码3C（国补2026）", {"product_type": "3C", "product_name": "笔记本电脑"})

    assert result.scene == "guobu"
    assert result.category == "3c"
    assert result.supported is True


def test_classifies_guobu_enum_3c():
    result = classify_audit_category("guobu", {"product_type": "3C", "product_name": "笔记本电脑"})

    assert result.scene == "guobu"
    assert result.category == "3c"
    assert result.supported is True


def test_classifies_guobu_home_appliance():
    result = classify_audit_category("家电数码3C（国补2026）", {"product_type": "家电", "product_name": "海尔冰箱"})

    assert result.scene == "guobu"
    assert result.category == "home_appliance"
    assert result.supported is True


def test_unsupported_car_goes_manual_category():
    result = classify_audit_category("汽车审核", {"product_name": "车辆置换"})

    assert result.scene == "unsupported"
    assert result.category == "unsupported"
    assert result.supported is False


def test_legacy_product_type_helper_uses_shared_classifier_for_guobu_queue():
    is_home_appliance, is_3c = determine_product_type(
        {"product_type": "3C", "product_name": "笔记本电脑"},
        scene_hint="家电数码3C（国补2026）",
    )

    assert is_home_appliance is False
    assert is_3c is True


def test_classifies_computer_by_name_even_when_collector_marked_home_appliance():
    result = classify_audit_category(
        "guobu",
        {
            "product_type": "[A05]",
            "goods_name": "\u7b14\u8bb0\u672c\u7535\u8111",
            "cate_code_name": "PC",
            "is_home_appliance": True,
        },
    )

    assert result.category == "3c"
    assert result.supported is True


def test_classifies_home_appliance_by_name_not_a_prefix():
    result = classify_audit_category(
        "guobu",
        {
            "product_type": "[A02]",
            "goods_name": "\u6d77\u5c14\u7535\u51b0\u7bb1",
        },
    )

    assert result.category == "home_appliance"
    assert result.supported is True


def test_does_not_classify_home_appliance_from_a_prefix_only():
    result = classify_audit_category("guobu", {"product_type": "[A02]"})

    assert result.category == "unsupported"
    assert result.supported is False
