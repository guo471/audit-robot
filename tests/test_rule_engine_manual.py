# -*- coding: utf-8 -*-
from modules.audit_models import normalize_decision
from modules.rule_engine import RuleEngine


def test_skip_is_external_manual():
    assert normalize_decision("skip") == "manual"


def test_error_is_external_error():
    assert normalize_decision("engine_error") == "error"


def test_rule_engine_external_decision_helper():
    assert RuleEngine.external_decision("pass") == "pass"
    assert RuleEngine.external_decision("skip") == "manual"
    assert RuleEngine.external_decision("review") == "manual"
    assert RuleEngine.external_decision("engine_error") == "error"


def test_rule_engine_missing_id_is_manual_not_pass_skip():
    result = RuleEngine.evaluate(
        system_data={"name": "张三", "sn": "SN001234"},
        id_card_info={},
        sn_result={"sn_match": True},
        imei_result={},
        forensics_results=[{"status": "pass"}],
    )

    assert result["decision"] == "skip"
    assert result["details"]["name"]["status"] == "review"
    assert result["details"]["id_validity"]["status"] == "review"


def test_rule_engine_imei_mismatch_is_not_blocking():
    result = RuleEngine.evaluate(
        system_data={"name": "张三", "sn": "SN001234", "imei1": "861234567890123"},
        id_card_info={"name": "张三", "is_valid": True},
        sn_result={"sn_match": True},
        imei_result={"imei1_match": False, "imei2_match": False},
        forensics_results=[{"status": "pass"}],
    )

    assert result["decision"] == "pass"
    assert result["details"]["imei"]["status"] == "info"


def test_rule_engine_id_number_mismatch_is_manual_without_leaking_values():
    result = RuleEngine.evaluate(
        system_data={
            "name": "张三",
            "sn": "SN001234",
            "id_number": "110101199001019999",
        },
        id_card_info={
            "name": "张三",
            "id_number": "440101199001011234",
            "is_valid": True,
        },
        sn_result={"sn_match": True},
        imei_result={},
        forensics_results=[{"status": "pass"}],
    )

    assert result["decision"] == "skip"
    assert result["details"]["id_number"]["status"] == "reject"
    assert "440101199001011234" not in str(result)
    assert "110101199001019999" not in str(result)
