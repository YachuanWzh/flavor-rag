from datetime import datetime, timedelta, timezone

from app.time_utils import utc_isoformat
from app.services.schedule.scheduler import calculate_next_run


def test_utc_isoformat_marks_naive_database_time_as_utc():
    assert utc_isoformat(datetime(2026, 8, 1, 4, 45, 31)) == (
        "2026-08-01T04:45:31Z"
    )


def test_utc_isoformat_normalizes_aware_time_to_utc():
    china_time = datetime(
        2026,
        8,
        1,
        12,
        45,
        31,
        tzinfo=timezone(timedelta(hours=8)),
    )
    assert utc_isoformat(china_time) == "2026-08-01T04:45:31Z"


def test_utc_isoformat_accepts_none():
    assert utc_isoformat(None) is None


def test_cron_uses_configured_local_timezone(monkeypatch):
    monkeypatch.setattr(
        "app.services.schedule.scheduler.settings.app_timezone",
        "Asia/Shanghai",
    )
    now = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
    # 14:00 in Shanghai is 06:00 UTC.
    assert calculate_next_run("0 14 * * *", now_utc=now) == datetime(
        2026, 8, 1, 6, 0
    )


def test_interval_schedule_remains_elapsed_time_based():
    now = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
    assert calculate_next_run("3600", now_utc=now) == datetime(
        2026, 8, 1, 5, 0
    )
