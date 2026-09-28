from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest

from health.models import Records
from health.sources.whoop import WhoopSource, _rmssd_ms


@pytest.fixture
def whoop(config):
    return WhoopSource(config, client=httpx.Client(base_url="https://example.invalid"),
                       pause=0)


def test_rmssd_dialects_normalise_to_milliseconds():
    assert _rmssd_ms(0.0684) == pytest.approx(68.4)   # seconds dialect
    assert _rmssd_ms(68.4) == pytest.approx(68.4)     # already milliseconds
    assert _rmssd_ms(None) is None


def test_sleep_parses_stages_and_credits_the_waking_day(whoop, land):
    records = whoop.parse(land("whoop", "sleep", "whoop_sleep.json"))

    night = next(s for s in records.sleeps if not s.is_nap)
    assert night.local_date == date(2026, 9, 1)      # woke on the 1st
    assert night.deep_min == 90.0                    # 5_400_000 ms
    assert night.rem_min == 105.0
    assert night.duration_min == 435.0               # light + deep + rem
    assert night.in_bed_min == 465.0
    assert night.efficiency == 93.5


def test_naps_do_not_enter_the_daily_series(whoop, land):
    records = whoop.parse(land("whoop", "sleep", "whoop_sleep.json"))

    assert len(records.sleeps) == 2                   # the nap is still stored
    durations = [o for o in records.observations if o.metric == "sleep_duration"]
    assert len(durations) == 1                        # but only the night is a daily value
    assert durations[0].value == 435.0


def test_recovery_parses_and_skips_uncalibrated_and_unscored(whoop, land):
    records = whoop.parse(land("whoop", "recovery", "whoop_recovery.json"))

    by_metric = {o.metric: o for o in records.observations}
    assert by_metric["recovery_score"].value == 71
    assert by_metric["resting_hr"].value == 52
    assert by_metric["hrv_rmssd"].value == pytest.approx(68.4)
    assert by_metric["spo2"].value == pytest.approx(96.4)
    # Only the first record contributes: the second is still calibrating, the
    # third has no score yet.
    assert {o.source_id for o in records.observations} == {"ec3a-sleep-1"}


def test_paging_follows_next_token_and_stops_on_repeat(config):
    pages = [
        {"records": [{"id": "a"}], "next_token": "t1"},
        {"records": [{"id": "b"}], "next_token": "t1"},  # the API repeats itself
    ]
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=pages[min(len(calls) - 1, len(pages) - 1)])

    client = httpx.Client(transport=httpx.MockTransport(handler),
                          base_url="https://example.invalid")
    source = WhoopSource(config, client=client, pause=0)
    source._tokens = {"access_token": "tok", "refresh_token": "r",
                      "expires_at": "2099-01-01T00:00:00Z"}

    seen = list(source.iter_pages("recovery", datetime(2026, 9, 1, tzinfo=timezone.utc)))

    assert len(seen) == 2               # both pages yielded
    assert len(calls) == 2              # then it stopped instead of looping
    assert calls[0]["start"] == "2026-09-01T00:00:00Z"
    assert calls[1]["nextToken"] == "t1"


def test_expired_token_triggers_a_refresh(config, monkeypatch):
    source = WhoopSource(config, pause=0)
    source._tokens = {"access_token": "old", "refresh_token": "refresh-me",
                      "expires_at": "2020-01-01T00:00:00Z"}
    monkeypatch.setenv("WHOOP_CLIENT_ID", "id")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "secret")

    posted: dict = {}

    def fake_post(url, data=None, timeout=None):
        posted.update(data or {})
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"access_token": "fresh", "refresh_token": "r2",
                                    "expires_in": 3600})

    monkeypatch.setattr("health.sources.whoop.httpx.post", fake_post)

    assert source._access_token() == "fresh"
    assert posted["grant_type"] == "refresh_token"
    assert posted["refresh_token"] == "refresh-me"


# -- the physiological day ----------------------------------------------------

def test_a_cycle_is_credited_to_the_day_it_ends_on(config):
    """WHOOP's day runs from one evening's sleep onset to the next, so the
    cycle starting Tuesday night is Wednesday's strain, burn and steps —
    keying on the start files every one of them a day early."""
    source = WhoopSource(config)
    records = Records()
    source._parse_cycle({
        "id": 1, "start": "2026-09-22T22:04:46.180Z", "end": "2026-09-23T20:47:10.120Z",
        "step_count": 16836,
        "score": {"strain": 12.5, "kilojoule": 9000.0, "average_heart_rate": 70},
    }, records)
    days = {o.metric: o.local_date for o in records.observations}
    assert days["steps"] == date(2026, 9, 23)
    assert days["strain"] == date(2026, 9, 23)
    assert days["energy_expenditure"] == date(2026, 9, 23)
    steps = next(o for o in records.observations if o.metric == "steps")
    assert steps.value == 16836


def test_a_cycle_ending_after_midnight_belongs_to_the_day_that_finished(config):
    source = WhoopSource(config)
    records = Records()
    source._parse_cycle({"id": 2, "start": "2026-09-23T21:00:00Z",
                         "end": "2026-09-25T00:40:00Z", "step_count": 5000,
                         "score": {"strain": 8.0}}, records)
    assert next(o for o in records.observations if o.metric == "steps").local_date         == date(2026, 9, 24)


def test_an_unscored_cycle_still_carries_its_steps(config):
    """step_count sits on the cycle, not inside score, so it survives a cycle
    WHOOP has not scored yet."""
    source = WhoopSource(config)
    records = Records()
    source._parse_cycle({"id": 3, "start": "2026-09-27T21:00:00Z",
                         "end": "2026-09-28T20:00:00Z", "step_count": 10957}, records)
    assert [(o.metric, o.value) for o in records.observations] == [("steps", 10957.0)]
