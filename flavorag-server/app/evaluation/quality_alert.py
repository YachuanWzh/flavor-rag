"""Feedback-driven quality alert and auto-promotion criteria."""
from __future__ import annotations

# Default: 3 negative feedbacks on the same KB within the alert window
_DEFAULT_ALERT_THRESHOLD = 3
# Default: 3 positive feedbacks and zero negatives for auto-promotion
_AUTO_PROMOTE_POSITIVE_MIN = 3


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
