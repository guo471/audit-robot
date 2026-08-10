# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient

import audit_service
from modules.audit_models import AuditResponse


def test_health_returns_ok():
    response = TestClient(audit_service.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_audit_rejects_missing_token():
    response = TestClient(audit_service.app).post("/audit", json={})

    assert response.status_code == 401


def test_audit_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("AUDIT_SERVICE_TOKEN", "secret")

    response = TestClient(audit_service.app).post(
        "/audit",
        json={},
        headers={"X-Audit-Token": "wrong"},
    )

    assert response.status_code == 401


def test_audit_rejects_default_token_even_if_legacy_flag_is_set(monkeypatch):
    monkeypatch.delenv("AUDIT_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("AUDIT_ALLOW_DEFAULT_TOKEN", "true")

    response = TestClient(audit_service.app).post(
        "/audit",
        json={},
        headers={"X-Audit-Token": "local-dev-token-change-me"},
    )

    assert response.status_code == 401


def test_audit_accepts_token_and_returns_manual_for_missing_images(monkeypatch):
    monkeypatch.setenv("AUDIT_SERVICE_TOKEN", "secret")
    payload = {
        "jl_order_no": "JL123",
        "scene_hint": "3C",
        "fields": {"product_type": "3c", "sn": "SN001234"},
        "images": [],
    }

    response = TestClient(audit_service.app).post(
        "/audit",
        json=payload,
        headers={"X-Audit-Token": "secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "manual"
    assert body["action"] == "next"
    assert body["jl_order_no"] == "JL123"
    assert body["manual_reason"]
    assert b"manual_reason" in response.content


def test_audit_returns_sanitized_error_for_bad_payload(monkeypatch):
    monkeypatch.setenv("AUDIT_SERVICE_TOKEN", "secret")

    response = TestClient(audit_service.app).post(
        "/audit",
        json={
            "jl_order_no": "JL999",
            "fields": "bad field with 440101199001011234 and https://x.test/a.jpg",
            "images": [],
        },
        headers={"X-Audit-Token": "secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "manual"
    assert body["action"] == "next"
    assert body["manual_reason"] == "服务异常，转人工"
    assert "440101199001011234" not in body["manual_reason"]
    assert "https://x.test" not in body["manual_reason"]


def test_audit_parses_page_text_when_fields_are_missing(monkeypatch):
    monkeypatch.setenv("AUDIT_SERVICE_TOKEN", "secret")
    captured = {}

    def fake_audit_request(request, deps=None, timeout_sec=60):
        captured["request"] = request
        return AuditResponse.manual(
            jl_order_no=request.jl_order_no,
            scene=request.scene_hint or "guobu",
            path="precheck",
            elapsed_sec=0,
            manual_reason="captured",
        )

    monkeypatch.setattr(audit_service, "audit_request", fake_audit_request)

    response = TestClient(audit_service.app).post(
        "/audit",
        json={
            "jl_order_no": "JL-PAGE-001",
            "scene_hint": "国补家电数码",
            "page_text": "产品类型 3C\nSN码 SN001234\n姓名 张三\n地址 广东省广州市天河区某路1号",
            "images": [],
        },
        headers={"X-Audit-Token": "secret"},
    )

    assert response.status_code == 200
    request = captured["request"]
    assert request.fields["sn"] == "SN001234"
    assert request.fields["product_type"] == "3C"
    assert request.fields["name"] == "张三"
    assert "广东省" in request.fields["address"]


def test_audit_explicit_fields_override_page_text(monkeypatch):
    monkeypatch.setenv("AUDIT_SERVICE_TOKEN", "secret")
    captured = {}

    def fake_audit_request(request, deps=None, timeout_sec=60):
        captured["request"] = request
        return AuditResponse.manual(
            jl_order_no=request.jl_order_no,
            scene=request.scene_hint or "guobu",
            path="precheck",
            elapsed_sec=0,
            manual_reason="captured",
        )

    monkeypatch.setattr(audit_service, "audit_request", fake_audit_request)

    response = TestClient(audit_service.app).post(
        "/audit",
        json={
            "jl_order_no": "JL-PAGE-002",
            "scene_hint": "国补家电数码",
            "page_text": "产品类型 家电\nSN码 PAGE_SN",
            "fields": {"product_type": "3C", "sn": "FIELD_SN"},
            "images": [],
        },
        headers={"X-Audit-Token": "secret"},
    )

    assert response.status_code == 200
    request = captured["request"]
    assert request.fields["sn"] == "FIELD_SN"
    assert request.fields["product_type"] == "3C"


def test_audit_uses_channel_order_no_when_jl_order_no_is_empty(monkeypatch):
    monkeypatch.setenv("AUDIT_SERVICE_TOKEN", "secret")
    captured = {}

    def fake_audit_request(request, deps=None, timeout_sec=60):
        captured["request"] = request
        return AuditResponse.manual(
            jl_order_no=request.jl_order_no,
            scene=request.scene_hint or "guobu",
            path="precheck",
            elapsed_sec=0,
            manual_reason="captured",
        )

    monkeypatch.setattr(audit_service, "audit_request", fake_audit_request)

    response = TestClient(audit_service.app).post(
        "/audit",
        json={
            "jl_order_no": "",
            "channel_order_no": "CH-FALLBACK-001",
            "scene_hint": "国补家电数码",
            "fields": {"product_type": "3C", "sn": "SN001234"},
            "images": [],
        },
        headers={"X-Audit-Token": "secret"},
    )

    assert response.status_code == 200
    assert captured["request"].jl_order_no == "CH-FALLBACK-001"
    assert response.json()["jl_order_no"] == "CH-FALLBACK-001"
