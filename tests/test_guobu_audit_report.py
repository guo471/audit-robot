import pytest

from tools.guobu_audit_report import parse_manual_flag, sn_display, standard_reason


REASONS = {
    "PRODUCT_TYPE_MISMATCH": "商品类型不一致",
    "PRODUCT_PHOTO_INVALID": "商品照片不符合要求",
    "UNBOXING_PHOTO_INVALID": "拆封/安装照片不符合要求",
    "ACTIVATION_PHOTO_INVALID": "激活照片不符合要求",
    "SN_MISSING_IN_ACTIVATION_PHOTO": "激活照片不符合要求",
    "ADDRESS_TOO_COARSE": "收货地址不符合要求",
    "DUPLICATE_IMAGE_EVIDENCE": "存在重复图片，不符合要求",
    "NON_REAL_PHOTO_REVIEW": "图片疑似非实拍",
    "NON_REAL_PHOTO_STRONG_RISK": "图片疑似非实拍",
    "IMAGE_STRONG_RISK": "图片疑似非实拍",
    "SN_MISMATCH": "SN不一致",
    "INVOICE_ORANGE_WARNING": "发票疑似已红冲",
    "MODEL_UNCERTAIN": "图片信息无法确认",
    "PHOTO_AUTHENTICITY_SERVICE_FAILURE": "审核服务异常",
    "ARTIFACT_LOAD_FAILURE": "审核服务异常",
    "FFT_FAILURE": "审核服务异常",
    "SN_TRUNCATED_OBSCURED": "SN不完整，无法识别",
    "SN_NOT_FOUND": "SN无法识别",
    "SYSTEM_SN_MISSING": "系统SN缺失",
    "IMAGE_MISSING": "图片缺失",
    "FIELD_MISSING": "订单信息缺失",
    "PRODUCT_TYPE_MISSING": "商品类型信息缺失",
    "NON_REAL_PHOTO_FFT_RESCUE": "图片疑似非实拍",
}


@pytest.mark.parametrize(("code", "text"), REASONS.items())
def test_standard_reason_maps_every_confirmed_code(code, text):
    assert standard_reason(code) == text


def test_standard_reason_handles_empty_and_unknown_codes():
    assert standard_reason("") == ""
    assert standard_reason("NEW_REASON") == "图片信息无法确认"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("是", True), ("否", False)],
)
def test_parse_manual_flag_accepts_only_explicit_values(value, expected):
    assert parse_manual_flag(value) is expected


@pytest.mark.parametrize(
    "value",
    [None, 0, 1, "", "true", " 是", "否 ", "\u93c4\u75d0", "\u935a\ue6c6"],
)
def test_parse_manual_flag_rejects_ambiguous_or_corrupt_values(value):
    with pytest.raises(ValueError):
        parse_manual_flag(value)


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"sn_match": True, "system_sn": "", "observed_sn": ""}, ("是", "")),
        ({"sn_match": False, "system_sn": "", "observed_sn": "ABC"}, ("无系统SN", "")),
        (
            {"sn_match": False, "system_sn": "ABC", "observed_sn": ""},
            ("未读取", "模型未读取到SN"),
        ),
        ({"sn_match": False, "system_sn": "ABC", "observed_sn": "ABC"}, ("否", "")),
    ],
)
def test_sn_display_uses_fixed_status_priority(row, expected):
    assert sn_display(row) == expected


@pytest.mark.parametrize(
    ("system_sn", "observed_sn", "difference"),
    [
        ("ABOCD", "AB0CD", "第3位不同：系统O，模型0"),
        ("ABCD", "SABCD", "模型第1位多读S"),
        ("ABCD", "ABXCD", "模型第3位多读X"),
        ("ABXCD", "ABCD", "模型第3位少读X"),
        ("ABCD", "ACBD", "字符顺序不同：系统BC，模型CB"),
        ("ABCDJ", "ABCD", "模型末尾少读J"),
        ("PQABCD", "ABCD", "模型开头少读PQ"),
        ("ABCD", "AB", "模型末尾少读CD"),
        ("ABCD", "CD", "模型开头少读AB"),
        ("00123", "00123", ""),
        ("ABCD", "AXYD", "SN存在多处差异"),
        ("ABCDE", "AXC", "SN存在多处差异"),
    ],
)
def test_sn_display_classifies_raw_string_differences(system_sn, observed_sn, difference):
    row = {"sn_match": False, "system_sn": system_sn, "observed_sn": observed_sn}
    assert sn_display(row) == ("否", difference)


def test_sn_display_does_not_mutate_sn_match_or_apply_visual_equivalence():
    row = {"sn_match": False, "system_sn": "O123", "observed_sn": "0123"}
    assert sn_display(row) == ("否", "第1位不同：系统O，模型0")
    assert row["sn_match"] is False


def test_sn_display_transposition_names_the_raw_character_pairs():
    row = {"sn_match": False, "system_sn": "12XY34", "observed_sn": "12YX34"}
    assert sn_display(row) == ("否", "字符顺序不同：系统XY，模型YX")
