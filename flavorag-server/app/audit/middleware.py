"""Audit middleware — captures operator context (user, IP, UA) per request.

Usage:
  1. Register AuditMiddleware in FastAPI app.
  2. In API handlers, import `get_audit_context` to retrieve current operator info.
  3. Call `record_audit()` from service to persist audit entries.

Example in an API handler:
    from app.audit.middleware import get_audit_context
    from app.audit.service import record_audit

    ctx = get_audit_context()
    await record_audit(
        biz_type="knowledge_base",
        biz_id=kb.id,
        operation_type="DELETE",
        operator_id=ctx.get("operator_id"),
        operator_name=ctx.get("operator_name"),
        operator_role=ctx.get("operator_role"),
        ip=ctx.get("ip"),
        user_agent=ctx.get("user_agent"),
        db=db,
    )
"""

from __future__ import annotations

import contextvars
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.auth.jwt import decode_access_token

_audit_ctx: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "audit_context", default={}
)


def get_audit_context() -> dict[str, Any]:
    """Get the current request's audit context (operator info)."""
    return _audit_ctx.get()


class AuditMiddleware(BaseHTTPMiddleware):
    """Capture operator identity from the request's Authorization header.

    Sets a contextvar that can be read by audit service callers.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        ctx: dict[str, Any] = {}

        # Extract operator info from JWT in Authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):]
            payload = decode_access_token(token)
            if payload:
                ctx["operator_id"] = payload.get("sub", "")
                ctx["operator_name"] = payload.get("username", "")
                ctx["operator_role"] = payload.get("role", "")

        # Capture client IP and User-Agent
        ctx["ip"] = request.client.host if request.client else ""
        ctx["user_agent"] = request.headers.get("User-Agent", "")

        token = _audit_ctx.set(ctx)
        try:
            return await call_next(request)
        finally:
            _audit_ctx.reset(token)
