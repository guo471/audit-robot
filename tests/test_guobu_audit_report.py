import pytest

from tools.guobu_audit_report import parse_manual_flag, sn_display, standard_reason


REASONS = {
    "PRODUCT_TYPE_MISMATCH": "鍟嗗搧绫诲瀷涓嶄竴鑷碻",
    "PRODUCT_PHOTO_INVALID": "鍟嗗搧鐓х墖涓嶇鍚堣姹俙",
    "UNBOXING_PHOTO_INVALID": "鎷嗗皝/瀹夎鐓х墖涓嶇鍚堣姹俙",
    "ACTIVATION_PHOTO_INVALID": "婵€娲荤収鐗囦笉绗﹀悎瑕佹眰",
    "SN_MISSING_IN_ACTIVATION_PHOTO": "婵€娲荤収鐗囦笉绗﹀悎瑕佹眰",
    "ADDRESS_TOO_COARSE": "鏀惰揣鍦板潃涓嶇鍚堣姹俙",
    "DUPLICATE_IMAGE_EVIDENCE": "瀛樺湪閲嶅鍥剧墖锛屼笉绗﹀悎瑕佹眰",
    "NON_REAL_PHOTO_REVIEW": "鍥剧墖鐤戜技闈炲疄鎷峘",
    "NON_REAL_PHOTO_STRONG_RISK": "鍥剧墖鐤戜技闈炲疄鎷峘",
    "IMAGE_STRONG_RISK": "鍥剧墖鐤戜技闈炲疄鎷峘",
    "SN_MISMATCH": "SN涓嶄竴鑷碻",
    "INVOICE_ORANGE_WARNING": "鍙戠エ鐤戜技宸茬孩鍐瞏",
    "MODEL_UNCERTAIN": "鍥剧墖淇℃伅鏃犳硶纭",
    "PHOTO_AUTHENTICITY_SERVICE_FAILURE": "瀹℃牳鏈嶅姟寮傚父",
    "ARTIFACT_LOAD_FAILURE": "瀹℃牳鏈嶅姟寮傚父",
    "FFT_FAILURE": "瀹℃牳鏈嶅姟寮傚父",
    "SN_TRUNCATED_OBSCURED": "SN涓嶅畬鏁达紝鏃犳硶璇嗗埆",
    "SN_NOT_FOUND": "SN鏃犳硶璇嗗埆",
    "SYSTEM_SN_MISSING": "绯荤粺SN缂哄け",
    "IMAGE_MISSING": "鍥剧墖缂哄け",
    "FIELD_MISSING": "璁㈠崟淇℃伅缂哄け",
    "PRODUCT_TYPE_MISSING": "鍟嗗搧绫诲瀷淇℃伅缂哄け",
    "NON_REAL_PHOTO_FFT_RESCUE": "鍥剧墖鐤戜技闈炲疄鎷峘",
}


@pytest.mark.parametrize(("code", "text"), REASONS.items())
def test_standard_reason_maps_every_confirmed_code(code, text):
    assert standard_reason(code) == text


def test_standard_reason_handles_empty_and_unknown_codes():
    assert standard_reason("") == ""
    assert standard_reason("NEW_REASON") == "鍥剧墖淇℃伅鏃犳硶纭"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("鏄痐", True), ("鍚", False)],
)
def test_parse_manual_flag_accepts_only_explicit_values(value, expected):
    assert parse_manual_flag(value) is expected


@pytest.mark.parametrize("value", [None, 0, 1, "", "true", " 鏄痐", "鍚 "])
def test_parse_manual_flag_rejects_ambiguous_values(value):
    with pytest.raises(ValueError):
        parse_manual_flag(value)


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"sn_match": True, "system_sn": "", "observed_sn": ""}, ("鏄痐", "")),
        ({"sn_match": False, "system_sn": "", "observed_sn": "ABC"}, ("鏃犵郴缁烻N", "")),
        ({"sn_match": False, "system_sn": "ABC", "observed_sn": ""}, ("鏈鍙朻", "妯″瀷鏈鍙栧埌SN")),
        ({"sn_match": False, "system_sn": "ABC", "observed_sn": "ABC"}, ("鍚", "")),
    ],
)
def test_sn_display_uses_fixed_status_priority(row, expected):
    assert sn_display(row) == expected


@pytest.mark.parametrize(
    ("system_sn", "observed_sn", "difference"),
    [
        ("O123", "0123", "绗?浣嶄笉鍚岋細绯荤粺O锛屾ā鍨?"),
        ("ABCD", "ABXCD", "妯″瀷绗?浣嶅璇籗"),
        ("ABXCD", "ABCD", "妯″瀷绗?浣嶅皯璇籗"),
        (
            "ABCD",
            "ACBD",
            "瀛楃椤哄簭涓嶅悓锛氱郴缁" + "BC" + "锛屾ā鍨" + "CB",
        ),
        ("ABCDJ", "ABCD", "妯″瀷鏈熬灏戣J"),
        ("JABCD", "ABCD", "妯″瀷寮€澶村皯璇籎"),
        ("00123", "00123", ""),
        ("ABCD", "AXYD", "SN瀛樺湪澶氬宸紓"),
    ],
)
def test_sn_display_classifies_raw_string_differences(system_sn, observed_sn, difference):
    row = {"sn_match": False, "system_sn": system_sn, "observed_sn": observed_sn}
    assert sn_display(row) == ("鍚", difference)


def test_sn_display_does_not_mutate_sn_match():
    row = {"sn_match": False, "system_sn": "O123", "observed_sn": "0123"}
    sn_display(row)
    assert row["sn_match"] is False


def test_sn_display_transposition_names_the_raw_character_pairs():
    row = {"sn_match": False, "system_sn": "12XY34", "observed_sn": "12YX34"}
    _, difference = sn_display(row)
    assert "XY" in difference
    assert "YX" in difference
