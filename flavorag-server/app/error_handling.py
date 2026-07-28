"""Friendly client errors backed by durable administrator-only audit records."""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.audit.middleware import get_audit_context
from app.audit.service import record_audit
from app.models import gen_id
from app.observability.metrics import SYSTEM_ERRORS

_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|api[_-]?key|token|password|secret)(\s*[:=]\s*)([^\s,;]+)"
)


@dataclass(frozen=True)
class FriendlyError:
    code: str
    message: str
    retryable: bool
    status_code: int = 500


def describe_error(exc: BaseException) -> FriendlyError:
    """Map implementation details to a stable, user-facing error contract."""
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return FriendlyError(
            "REQUEST_TIMEOUT",
            "本次处理时间较长，系统未能及时完成。请稍后重试，或缩短问题后再试。",
            True,
            504,
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return FriendlyError(
                "UPSTREAM_BUSY",
                "当前请求较多，智能服务正在排队。请稍后再试。",
                True,
                503,
            )
        if status >= 500:
            return FriendlyError(
                "UPSTREAM_UNAVAILABLE",
                "智能服务暂时不可用，系统已记录问题。请稍后重试。",
                True,
                503,
            )
    if isinstance(exc, (httpx.ConnectError, ConnectionError)):
        return FriendlyError(
            "DEPENDENCY_UNAVAILABLE",
            "依赖服务暂时无法连接，系统已记录问题。请稍后重试。",
            True,
            503,
        )
    if "circuit breaker" in str(exc).lower():
        return FriendlyError(
            "SERVICE_PROTECTED",
            "智能服务暂时繁忙，保护机制已启动。请稍后重试。",
            True,
            503,
        )
    return FriendlyError(
        "INTERNAL_ERROR",
        "系统处理时遇到异常，问题已自动记录。请稍后重试。",
        False,
        500,
    )


def _redact(value: str, limit: int = 8000) -> str:
    return _SECRET_PATTERN.sub(r"\1\2***", value)[:limit]


async def record_system_error(
    exc: BaseException,
    *,
    component: str,
    context: dict[str, Any] | None = None,
    error_id: str | None = None,
) -> str:
    """Persist a system error independently from the failed business transaction."""
    error_id = error_id or gen_id()
    audit_context = get_audit_context()
    descriptor = describe_error(exc)
    safe_context = {
        str(key): _redact(str(value), 1000)
        for key, value in (context or {}).items()
        if value is not None
    }
    try:
        await record_audit(
            biz_type="system_error",
            biz_id=error_id,
            operation_type="ERROR",
            action_desc=f"{component}: {type(exc).__name__}",
            after_snapshot={
                "category": descriptor.code,
                "retryable": descriptor.retryable,
                "context": safe_context,
                "traceback": _redact(
                    "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                ),
            },
            operator_id=audit_context.get("operator_id"),
            operator_name=audit_context.get("operator_name"),
            operator_role=audit_context.get("operator_role"),
            success=False,
            error_message=_redact(str(exc) or type(exc).__name__, 4000),
            class_name=component,
            method_name=safe_context.get("method"),
            ip=audit_context.get("ip"),
            user_agent=audit_context.get("user_agent"),
        )
        SYSTEM_ERRORS.labels(
            component=component[:64], category=descriptor.code
        ).inc()
    except Exception:
        # Error auditing must never replace the original failure.
        pass
    return error_id


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    error_id = await record_system_error(
        exc,
        component="http.validation",
        context={"method": request.method, "path": request.url.path},
    )
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": "提交内容格式不正确，请检查必填项后重试。",
            "errorId": error_id,
            "retryable": False,
        },
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    error_id = await record_system_error(
        exc,
        component="http.request",
        context={
            "method": request.method,
            "path": request.url.path,
            "status": exc.status_code,
        },
    )
    # Explicit 4xx details are normally safe business guidance; 5xx details are not.
    message = (
        str(exc.detail)
        if exc.status_code < 500
        else describe_error(exc).message
    )
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "code": f"HTTP_{exc.status_code}",
            "message": message,
            "errorId": error_id,
            "retryable": exc.status_code in {408, 429, 502, 503, 504},
        },
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    descriptor = describe_error(exc)
    error_id = await record_system_error(
        exc,
        component="http.unhandled",
        context={"method": request.method, "path": request.url.path},
    )
    return JSONResponse(
        status_code=descriptor.status_code,
        content={
            "code": descriptor.code,
            "message": descriptor.message,
            "errorId": error_id,
            "retryable": descriptor.retryable,
        },
    )
