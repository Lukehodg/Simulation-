from __future__ import annotations

from datetime import date

import httpx
import pytest

from health.sources.hevy import HevySource


@pytest.fixture
def hevy(config):
    return HevySource(config, client=httpx.Client(base_url="https://example.invalid"),
                      pause=0)


def test_workout_parses_every_set(hevy, land):
    records = hevy.parse(land("hevy", "workouts", "hevy_workouts.json"))

    assert len(records.workouts) == 1
    workout = records.workouts[0]
    assert workout.title == "Lower A"
    assert workout.local_date == date(2026, 9, 1)
    assert workout.duration_min == 75.0

    assert len(records.strength_sets) == 4          # warmup included, flagged
    working = [s for s in records.strength_sets if s.set_type == "normal"]
    assert len(working) == 3
    rdl = [s for s in working if s.exercise.startswith("Romanian")]
    assert [s.weight_kg for s in rdl] == [82.5, 82.5]
    assert [s.reps for s in rdl] == [6, 6]
    assert rdl[0].exercise_id == "rdl-tmpl"


def test_events_feed_carries_edits_and_deletions(hevy, land, store):
    """An edit that removes an exercise must remove its sets, not just update
    the ones that survived."""
    store.load(hevy.parse(land("hevy", "workouts", "hevy_workouts.json")))
    records = hevy.parse(land("hevy", "events", "hevy_events.json"))

    assert records.deleted_workouts == ["hw-999"]
    store.load(records)

    # The edited session overwrites in place rather than duplicating.
    assert store.query("SELECT COUNT(*) FROM workouts") == [(1,)]
    assert store.query("SELECT title FROM workouts") == [("Lower A (corrected)",)]
    weights = store.query(
        "SELECT DISTINCT weight_kg FROM strength_sets "
        "WHERE set_type = 'normal' ORDER BY weight_kg"
    )
    assert weights == [(85.0,)]


def test_paging_stops_at_page_count(config, monkeypatch):
    monkeypatch.setenv("HEVY_API_KEY", "k")
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"page": page, "page_count": 3,
                                         "workouts": [{"id": f"w{page}"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler),
                          base_url="https://example.invalid")
    source = HevySource(config, client=client, pause=0)

    pages = list(source.iter_pages("/workouts", {"pageSize": 10}))

    assert [p["page"] for p in pages] == [1, 2, 3]
    assert len(calls) == 3
