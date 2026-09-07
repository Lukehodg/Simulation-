"""Time handling.

Two rules, applied everywhere:
  - every timestamp is stored in UTC;
  - `local_date` is the day *you* would call it, in your timezone.

A 00:30 heart rate reading belongs to the day that just ended in conversation
but to the new day on the clock. We follow the clock, consistently, so that
joins between sources line up; sleep is the one exception and handles its own
day attribution (see `sleep_local_date`).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_FORMATS = (
    "%Y-%m-%d %H:%M:%S %z",   # Health Auto Export
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


def parse_ts(value: str | datetime | None) -> datetime | None:
    """Parse the several timestamp dialects our sources emit into aware UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_utc(value)
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return to_utc(datetime.fromisoformat(text))
    except ValueError:
        pass
    for fmt in _FORMATS:
        try:
            return to_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    raise ValueError(f"unrecognised timestamp: {value!r}")


def to_utc(dt: datetime) -> datetime:
    """Naive datetimes are assumed to already be UTC — sources that emit them
    document them as UTC, and guessing a local zone would be worse."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def local_date(dt: datetime, tz: ZoneInfo) -> date:
    return to_utc(dt).astimezone(tz).date()


def sleep_local_date(end: datetime, tz: ZoneInfo) -> date:
    """A sleep is credited to the morning you woke up on.

    Waking at 02:00 after a very late night still belongs to the previous day,
    so anything ending before 10:00 is attributed by wake date and anything
    later (a nap) to the day it happened — both of which are the same date here,
    which is exactly why we key on the wake moment rather than sleep onset.
    """
    return local_date(end, tz)


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC instants bracketing a local calendar day (DST-correct)."""
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return to_utc(start), to_utc(end)


def isoformat(dt: datetime) -> str:
    return to_utc(dt).isoformat().replace("+00:00", "Z")
