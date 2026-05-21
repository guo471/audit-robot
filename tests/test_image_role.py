# -*- coding: utf-8 -*-
from modules.audit_models import AuditImage
from modules.image_role import classify_image_role, group_images_by_role, required_roles_present


def test_classify_known_titles():
    assert classify_image_role("\u4e8c\u4ee3\u5c45\u6c11\u8eab\u4efd\u8bc1\u4eba\u50cf\u9762") == "id_front"
    assert classify_image_role("\u4e8c\u4ee3\u5c45\u6c11\u8eab\u4efd\u8bc1\u56fd\u5fbd\u9762") == "id_back"
    assert classify_image_role("\u5546\u54c1\u7167\u7247") == "product_photo"
    assert classify_image_role("\u62c6\u5c01\u7167\u7247") == "unboxing_photo"
    assert classify_image_role("SN\u7801\u91c7\u96c6/\u6fc0\u6d3b\u7167\u7247") == "activation_photo"


def test_group_duplicate_titles_keeps_all_images():
    images = [
        AuditImage(title="\u5546\u54c1\u7167\u7247", path="C:/tmp/a.jpg"),
        AuditImage(title="\u5546\u54c1\u7167\u7247", path="C:/tmp/b.jpg"),
        AuditImage(title="SN\u7801\u91c7\u96c6\u7167\u7247", path="C:/tmp/c.jpg"),
    ]

    grouped = group_images_by_role(images)

    assert [img.path for img in grouped["product_photo"]] == ["C:/tmp/a.jpg", "C:/tmp/b.jpg"]
    assert [img.path for img in grouped["activation_photo"]] == ["C:/tmp/c.jpg"]
    assert grouped["unknown"] == []


def test_unknown_title_goes_to_unknown():
    grouped = group_images_by_role([AuditImage(title="\u5176\u4ed6\u6750\u6599", path="C:/tmp/x.jpg")])

    assert grouped["unknown"][0].path == "C:/tmp/x.jpg"


def test_required_roles_for_no_coupon_accepts_product_or_activation():
    with_product = group_images_by_role([
        AuditImage(title="\u4e8c\u4ee3\u5c45\u6c11\u8eab\u4efd\u8bc1\u4eba\u50cf\u9762", path="C:/tmp/front.jpg"),
        AuditImage(title="\u4e8c\u4ee3\u5c45\u6c11\u8eab\u4efd\u8bc1\u56fd\u5fbd\u9762", path="C:/tmp/back.jpg"),
        AuditImage(title="\u5546\u54c1\u7167\u7247", path="C:/tmp/product.jpg"),
    ])
    with_activation = group_images_by_role([
        AuditImage(title="\u4e8c\u4ee3\u5c45\u6c11\u8eab\u4efd\u8bc1\u4eba\u50cf\u9762", path="C:/tmp/front.jpg"),
        AuditImage(title="\u4e8c\u4ee3\u5c45\u6c11\u8eab\u4efd\u8bc1\u56fd\u5fbd\u9762", path="C:/tmp/back.jpg"),
        AuditImage(title="SN\u7801\u91c7\u96c6\u7167\u7247", path="C:/tmp/sn.jpg"),
    ])

    assert required_roles_present(with_product, "no_coupon") is True
    assert required_roles_present(with_activation, "no_coupon") is True


def test_required_roles_for_no_coupon_rejects_missing_id_back():
    grouped = group_images_by_role([
        AuditImage(title="\u4e8c\u4ee3\u5c45\u6c11\u8eab\u4efd\u8bc1\u4eba\u50cf\u9762", path="C:/tmp/front.jpg"),
        AuditImage(title="\u5546\u54c1\u7167\u7247", path="C:/tmp/product.jpg"),
    ])

    assert required_roles_present(grouped, "no_coupon") is False


def test_required_roles_for_guobu_requires_product_unboxing_and_activation():
    complete = group_images_by_role([
        AuditImage(title="\u5546\u54c1\u7167\u7247", path="C:/tmp/product.jpg"),
        AuditImage(title="\u62c6\u5c01\u7167\u7247", path="C:/tmp/unbox.jpg"),
        AuditImage(title="SN\u7801\u91c7\u96c6\u7167\u7247", path="C:/tmp/sn.jpg"),
    ])
    missing_unboxing = group_images_by_role([
        AuditImage(title="\u5546\u54c1\u7167\u7247", path="C:/tmp/product.jpg"),
        AuditImage(title="SN\u7801\u91c7\u96c6\u7167\u7247", path="C:/tmp/sn.jpg"),
    ])

    assert required_roles_present(complete, "guobu") is True
    assert required_roles_present(complete, "default") is True
    assert required_roles_present(missing_unboxing, "guobu") is False
    assert required_roles_present(missing_unboxing, "default") is False
