"""UTC storage and API timestamp helpers.

Database timestamps are intentionally stored as naive UTC values for
compatibility with the existing schema. API timestamps must carry an explicit
UTC marker so browsers do not mistake them for local wall-clock time.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_naive() -> datetime:
    """Return the current UTC time in the database's naive representation."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_isoformat(value: datetime | None) -> str | None:
    """Serialize a database timestamp as an unambiguous ISO-8601 UTC value."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")
