# -*- coding: utf-8 -*-
from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryResult:
    scene: str
    category: str
    supported: bool
    reason: str = ""


HOME_APPLIANCE_KEYWORDS = (
    "家电",
    "冰箱",
    "电冰箱",
    "冷柜",
    "电视",
    "电视机",
    "洗衣机",
    "空调",
    "热水器",
    "电器",
    "refrigerator",
    "fridge",
    "freezer",
    "washing machine",
    "washer",
    "air conditioner",
    "television",
    "water heater",
)
THREE_C_KEYWORDS = (
    "3c",
    "手机",
    "电脑",
    "笔记本",
    "平板",
    "数码",
    "相机",
    "耳机",
    "手表",
    "手环",
    "pc",
    "computer",
    "laptop",
    "notebook",
    "phone",
    "tablet",
    "digital",
    "camera",
    "watch",
)
CAR_KEYWORDS = ("汽车", "车辆", "行驶证", "车架号", "车牌")


def classify_audit_category(scene_hint, fields):
    fields = fields or {}
    product_parts = [
        fields.get("product_type"),
        fields.get("type"),
        fields.get("product_name"),
        fields.get("goods_name"),
        fields.get("cate_code_name"),
        fields.get("brand"),
        fields.get("model"),
    ]
    text_parts = [
        scene_hint,
        *product_parts,
    ]
    text = " ".join(str(part) for part in text_parts if part is not None).lower()
    product_text = " ".join(str(part) for part in product_parts if part is not None).lower()

    if any(keyword in text for keyword in CAR_KEYWORDS):
        return CategoryResult("unsupported", "unsupported", False)

    is_guobu = (
        "国补" in text
        or "家电数码3c" in text
        or "guobu" in text
        or "national_subsidy" in text
    )
    scene = "guobu" if is_guobu else "no_coupon"

    category_text = product_text or text

    if any(keyword in category_text for keyword in ("phone", "computer", "laptop", "digital")):
        return CategoryResult(scene, "3c", True)

    if any(keyword in category_text for keyword in THREE_C_KEYWORDS):
        return CategoryResult(scene, "3c", True)

    if any(keyword in category_text for keyword in ("home_appliance", "appliance")):
        return CategoryResult(scene, "home_appliance", is_guobu)

    if any(keyword in category_text for keyword in HOME_APPLIANCE_KEYWORDS):
        return CategoryResult(scene, "home_appliance", is_guobu)

    return CategoryResult(
        "unsupported",
        "unsupported",
        False,
        "品类未命中首期支持范围",
    )
