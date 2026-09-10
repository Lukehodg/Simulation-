from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from health.features import sleep
from health.models import Records, Sleep

START = date(2026, 5, 1)


def _night(store, day: date, *, deep=70.0, awake=20.0, rem=90.0, dur=430.0) -> None:
    onset = datetime.combine(day, time(23), tzinfo=timezone.utc)
    store.load(Records(sleeps=[Sleep(
        source="whoop", source_id=f"s-{day}", start_ts=onset,
        end_ts=onset + timedelta(hours=8), local_date=day + timedelta(days=1),
        duration_min=dur, deep_min=deep, rem_min=rem, light_min=200.0,
        awake_min=awake, efficiency=90.0, is_nap=False)]))


def _run(store, n: int, **kw) -> None:
    for i in range(n):
        day = START + timedelta(days=i)
        _night(store, day, deep=kw.get("deep", 70.0) + (i % 5) - 2,
               awake=kw.get("awake", 20.0) + (i % 3) - 1)


def test_a_short_deep_night_is_flagged_against_your_own_baseline(store):
    _run(store, 20)                        # ~70 min deep is normal
    last = START + timedelta(days=20)
    _night(store, last, deep=25.0)         # deep sleep collapses

    result = sleep.sleep_quality(store, as_of=last + timedelta(days=1))

    deep = next(c for c in result["components"] if c["metric"] == "deep_min")
    assert deep["z"] < -1
    assert "deep sleep" in result["flags"]


def test_thin_history_says_so(store):
    _run(store, 3)
    result = sleep.sleep_quality(store, as_of=START + timedelta(days=4))
    assert "not enough" in result["note"]


def test_deep_sleep_debt_accumulates(store):
    _run(store, 20, deep=70.0)             # baseline ~70
    for i in range(20, 27):                # a week well below it
        _night(store, START + timedelta(days=i), deep=25.0)

    result = sleep.sleep_quality(store, as_of=START + timedelta(days=27))
    assert result["deep_sleep_debt_min"] > 100
