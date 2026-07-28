import httpx
import pytest

import app.error_handling as error_handling
from app.error_handling import describe_error, record_system_error


def test_friendly_error_does_not_expose_internal_details():
    descriptor = describe_error(RuntimeError("database password leaked"))
    assert descriptor.code == "INTERNAL_ERROR"
    assert "password" not in descriptor.message
    assert "自动记录" in descriptor.message

    timeout = describe_error(httpx.ReadTimeout("provider timed out"))
    assert timeout.code == "REQUEST_TIMEOUT"
    assert timeout.retryable is True


@pytest.mark.asyncio
async def test_system_error_audit_redacts_credentials(monkeypatch):
    captured = {}

    async def fake_record_audit(**kwargs):
        captured.update(kwargs)
        return "audit-1"

    monkeypatch.setattr(error_handling, "record_audit", fake_record_audit)
    error_id = await record_system_error(
        RuntimeError("api_key=super-secret provider failed"),
        component="test.component",
        context={"token": "token=also-secret"},
        error_id="error-1",
    )

    assert error_id == "error-1"
    assert captured["biz_type"] == "system_error"
    assert captured["biz_id"] == "error-1"
    assert captured["success"] is False
    assert "super-secret" not in captured["error_message"]
    assert "also-secret" not in str(captured["after_snapshot"])


@pytest.mark.asyncio
async def test_background_error_logs_are_flushed_to_audit(monkeypatch):
    from app.config.logging_config import (
        get_logger,
        start_error_audit_worker,
        stop_error_audit_worker,
    )

    captured = []

    async def fake_record_system_error(exc, *, component, context, error_id=None):
        captured.append((str(exc), component, context))
        return "error-1"

    monkeypatch.setattr(
        error_handling, "record_system_error", fake_record_system_error
    )
    await start_error_audit_worker()
    get_logger("flavorag.test.background").error(
        "background_job_failed",
        error="dependency unavailable",
    )
    await stop_error_audit_worker()

    assert len(captured) == 1
    assert captured[0][1] == "flavorag.test.background"
    assert captured[0][2]["event"] == "background_job_failed"
