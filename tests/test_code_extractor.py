# -*- coding: utf-8 -*-
from modules.code_extractor import CodeExtractor


def texts(*values):
    return [{"text": value, "confidence": 0.99, "box": None} for value in values]


def test_sn_match_ignores_case_space_and_separators():
    result = CodeExtractor.match_system_sn(
        texts("511-320Q0745-B424-1260537"),
        "511320q0745 b424 1260537",
    )

    assert result["sn_match"] is True
    assert result["match_type"] == "normalized"


def test_sn_match_allows_single_long_prefix_when_system_has_extra_suffix():
    result = CodeExtractor.match_system_sn(
        texts("51138000MD1B4021C0032", "MB90V33B"),
        "51138000MD1B4021C00327MB90V33B",
    )

    assert result["sn_match"] is True
    assert result["match_type"] == "prefix"


def test_home_appliance_511_allows_limited_continuous_fragment():
    result = CodeExtractor.match_system_sn(
        texts("310A2251-B212-1211368"),
        "511310A2251B2121211368",
        allow_home_appliance_fragment=True,
    )

    assert result["sn_match"] is True
    assert result["match_type"] == "home_appliance_fragment"


def test_home_appliance_fragment_rejects_too_many_missing_chars():
    result = CodeExtractor.match_system_sn(
        texts("B212-1211368"),
        "511310A2251B2121211368",
        allow_home_appliance_fragment=True,
    )

    assert result["sn_match"] is False


def test_home_appliance_fragment_only_applies_to_511_512():
    result = CodeExtractor.match_system_sn(
        texts("310A2251-B212-1211368"),
        "611310A2251B2121211368",
        allow_home_appliance_fragment=True,
    )

    assert result["sn_match"] is False


def test_home_appliance_fragment_is_disabled_by_default():
    result = CodeExtractor.match_system_sn(
        texts("310A2251-B212-1211368"),
        "511310A2251B2121211368",
    )

    assert result["sn_match"] is False


def test_sn_match_does_not_stitch_multiple_fragments():
    result = CodeExtractor.match_system_sn(
        texts("51138000MD1B4021", "C00327MB90V33B"),
        "51138000MD1B4021C00327MB90V33B",
    )

    assert result["sn_match"] is False


def test_sn_match_allows_clear_prefix_when_system_has_customer_extra_suffix():
    result = CodeExtractor.match_system_sn(
        texts("CEAAJS00400PHP4BVFL2"),
        "CEAAJS00400PHP4BVFL2XQG100HLD58A1",
    )

    assert result["sn_match"] is True
    assert result["match_type"] == "prefix"


def test_short_prefix_is_not_enough_for_sn_match():
    result = CodeExtractor.match_system_sn(
        texts("CBAML4000"),
        "CBAML400000PBRAVE8HA",
    )

    assert result["sn_match"] is False
