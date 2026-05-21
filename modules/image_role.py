# -*- coding: utf-8 -*-
from typing import Dict, Iterable, List

from .audit_models import AuditImage


ROLE_KEYS = (
    "id_front",
    "id_back",
    "product_photo",
    "unboxing_photo",
    "activation_photo",
    "unknown",
)


def classify_image_role(title: str) -> str:
    normalized = str(title or "").lower()

    if "身份证" in normalized and ("人像" in normalized or "正面" in normalized):
        return "id_front"
    if "身份证" in normalized and ("国徽" in normalized or "反面" in normalized):
        return "id_back"
    if "拆封" in normalized or "开箱" in normalized:
        return "unboxing_photo"
    if (
        "激活" in normalized
        or "sn" in normalized
        or "序列号" in normalized
        or "采集" in normalized
    ):
        return "activation_photo"
    if "商品" in normalized or "新物" in normalized or "产品" in normalized:
        return "product_photo"
    return "unknown"


def group_images_by_role(images: Iterable[AuditImage]) -> Dict[str, List[AuditImage]]:
    grouped: Dict[str, List[AuditImage]] = {role: [] for role in ROLE_KEYS}
    for image in images:
        grouped[classify_image_role(image.title)].append(image)
    return grouped


def required_roles_present(grouped: Dict[str, List[AuditImage]], scene: str) -> bool:
    if scene == "no_coupon":
        return (
            bool(grouped.get("id_front"))
            and bool(grouped.get("id_back"))
            and (bool(grouped.get("product_photo")) or bool(grouped.get("activation_photo")))
        )

    return (
        bool(grouped.get("product_photo"))
        and bool(grouped.get("unboxing_photo"))
        and bool(grouped.get("activation_photo"))
    )
