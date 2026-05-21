# -*- coding: utf-8 -*-
from modules.audit_models import AuditImage, AuditRequest, AuditResponse, normalize_decision


def test_audit_request_keeps_jl_order_no_and_images():
    request = AuditRequest.from_dict({
        "jl_order_no": "JL123",
        "channel_order_no": "CH456",
        "scene_hint": "家电数码3C（国补2026）",
        "fields": {"sn": "SN001", "product_name": "手机"},
        "images": [{"title": "SN码采集照片", "path": "C:/tmp/1.jpg"}],
    })

    assert request.jl_order_no == "JL123"
    assert request.fields["sn"] == "SN001"
    assert request.images == [AuditImage(title="SN码采集照片", path="C:/tmp/1.jpg")]


def test_normalize_decision_maps_skip_to_manual():
    assert normalize_decision("skip") == "manual"
    assert normalize_decision("pass") == "pass"
    assert normalize_decision(" PASS ") == "pass"
    assert normalize_decision("engine_error") == "error"
    assert normalize_decision("unknown") == "manual"


def test_response_action_matches_decision():
    pass_response = AuditResponse.pass_(
        jl_order_no="JL123",
        scene="guobu_3c",
        path="fast",
        elapsed_sec=18.6,
    )
    response = AuditResponse.manual(
        jl_order_no="JL123",
        scene="guobu_3c",
        path="slow",
        elapsed_sec=60.0,
        manual_reason="SN未高置信识别",
    )

    assert pass_response.decision == "pass"
    assert pass_response.action == "approve"
    assert response.decision == "manual"
    assert response.action == "next"
    assert response.to_dict()["jl_order_no"] == "JL123"
