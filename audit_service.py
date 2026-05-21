# -*- coding: utf-8 -*-
import os
import time
import logging
import json

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from config import (
    AUDIT_ALLOW_DEFAULT_TOKEN_ENV,
    AUDIT_DEFAULT_TOKEN,
    AUDIT_ORDER_TIMEOUT_SEC,
    AUDIT_SERVICE_HOST,
    AUDIT_SERVICE_PORT,
    AUDIT_SERVICE_TOKEN_ENV,
)
from modules.audit_models import AuditRequest, AuditResponse
from modules.audit_runner import AuditDependencies, audit_request
from modules.privacy import redact_text


class AsciiJSONResponse(JSONResponse):
    """JSON response that is safe for Windows clients with legacy text decoding."""

    def render(self, content) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")


app = FastAPI(
    title="Local Audit Service",
    version="0.1.0",
    default_response_class=AsciiJSONResponse,
)
DEPS = AuditDependencies()
logger = logging.getLogger("audit_service")


def expected_token() -> str | None:
    configured = os.environ.get(AUDIT_SERVICE_TOKEN_ENV)
    if configured:
        return configured
    allow_default = os.environ.get(AUDIT_ALLOW_DEFAULT_TOKEN_ENV, "").strip().lower()
    if allow_default in {"1", "true", "yes"}:
        return AUDIT_DEFAULT_TOKEN
    return None


def verify_token(x_audit_token: str | None) -> None:
    token = expected_token()
    if not token or not x_audit_token or x_audit_token != token:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/audit")
def audit(payload: dict, x_audit_token: str | None = Header(default=None)) -> dict:
    verify_token(x_audit_token)
    started = time.monotonic()
    request = AuditRequest(jl_order_no="")
    try:
        request = AuditRequest.from_dict(payload)
        response = audit_request(request, deps=DEPS, timeout_sec=AUDIT_ORDER_TIMEOUT_SEC)
    except Exception:
        elapsed = time.monotonic() - started
        logger.error("Audit service failed: %s", redact_text(request.jl_order_no))
        response = AuditResponse.error(
            jl_order_no=request.jl_order_no,
            scene=request.scene_hint or "unknown",
            path="error",
            elapsed_sec=elapsed,
            manual_reason="服务异常",
        )
    return response.to_dict()


if __name__ == "__main__":
    uvicorn.run(app, host=AUDIT_SERVICE_HOST, port=AUDIT_SERVICE_PORT)
