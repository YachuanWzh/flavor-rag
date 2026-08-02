"""Feedback-driven quality alert and auto-promotion criteria."""
from __future__ import annotations

import httpx

from app.config.logging_config import get_logger

_log = get_logger("flavorag.evaluation.alert")

# Default: 3 negative feedbacks on the same KB within the alert window
_DEFAULT_ALERT_THRESHOLD = 3
# Default: 3 positive feedbacks and zero negatives for auto-promotion
_AUTO_PROMOTE_POSITIVE_MIN = 3


async def send_webhook_alert(
    webhook_url: str,
    *,
    title: str,
    metrics: dict,
    failed_gates: list[dict] | None = None,
) -> bool:
    """Send a quality alert to an external webhook.

    Compatible with DingTalk / Feishu / Slack markdown webhooks.
    Returns True if the alert was sent successfully (3.3).
    """
    if not webhook_url:
        return False

    lines = [f"## {title}"]
    if failed_gates:
        lines.append("\n### 未通过的质量门禁")
        for gate in failed_gates:
            lines.append(
                f"- **{gate.get('metric')}**: {gate.get('value'):.4f} "
                f"(门禁: {gate.get('operator')} {gate.get('threshold')})"
            )
    lines.append("\n### 关键指标")
    for key, value in sorted(metrics.items()):
        if isinstance(value, (int, float)):
            lines.append(f"- {key}: {value:.4f}")

    payload = {
        # DingTalk / Feishu markdown format
        "msgtype": "markdown",
        "markdown": {"title": title, "text": "\n".join(lines)},
        # Slack-compatible fallback
        "text": title,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
            _log.info(
                "webhook_alert_sent",
                url=webhook_url[:50],
                status=resp.status_code,
            )
            return True
    except Exception as exc:
        _log.warning("webhook_alert_failed", error=str(exc)[:200])
        return False


def should_trigger_alert(
    negative_count: int,
    threshold: int = _DEFAULT_ALERT_THRESHOLD,
) -> bool:
    """Return True when negative feedback count warrants a quality alert."""
    return negative_count >= threshold


def meets_auto_promote(
    positive_count: int,
    negative_count: int,
    *,
    positive_min: int = _AUTO_PROMOTE_POSITIVE_MIN,
) -> bool:
    """Return True when a case qualifies for automatic golden promotion."""
    return positive_count >= positive_min and negative_count == 0
