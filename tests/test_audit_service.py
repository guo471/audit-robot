# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient

import audit_service


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


def test_audit_rejects_default_token_unless_enabled(monkeypatch):
    monkeypatch.delenv("AUDIT_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("AUDIT_ALLOW_DEFAULT_TOKEN", raising=False)

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
    assert body["manual_reason"] == "图片角色不完整或无法识别"
    assert b"\\u56fe\\u7247" in response.content


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
    assert body["decision"] == "error"
    assert body["manual_reason"] == "服务异常"
    assert "440101199001011234" not in body["manual_reason"]
    assert "https://x.test" not in body["manual_reason"]
