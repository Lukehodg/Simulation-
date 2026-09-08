"""The Hevy app's CSV export — a different shape from its API, and the one
most people actually have to hand."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from health.features import strength
from health.sources.hevy import HevySource

FIXTURE = Path(__file__).parent / "fixtures" / "hevy_export.csv"


@pytest.fixture
def hevy(config):
    config.ensure_dirs()
    return HevySource(config, pause=0)


@pytest.fixture
def records(hevy):
    return hevy.parse(hevy.add(FIXTURE))


def test_one_row_per_set_becomes_workouts_and_sets(records):
    assert len(records.workouts) == 2
    assert len(records.strength_sets) == 12
    push = next(w for w in records.workouts if w.title == "Day 1 — Push")
    assert push.local_date == date(2026, 9, 4)
    assert push.duration_min == 65.0


def test_export_timestamps_are_local_wall_time_not_utc(records):
    """A 09:00 session logged in London in September is 08:00 UTC; reading it
    as UTC would put an evening session on the wrong day."""
    push = next(w for w in records.workouts if w.title == "Day 1 — Push")
    assert push.start_ts.hour == 8          # 09:00 BST
    assert push.local_date == date(2026, 9, 4)


def test_an_exercise_repeated_in_one_session_keeps_both_blocks(records):
    """Set numbering restarts when a workout returns to an exercise, so keying
    the index on the exercise name would file the second block over the first."""
    rows = [s for s in records.strength_sets if s.exercise == "Row (Machine)"]

    assert len(rows) == 4
    assert {s.exercise_idx for s in rows} == {2, 4}   # two distinct blocks
    assert sorted(s.weight_kg for s in rows) == [30.0, 30.0, 90.0, 95.0]


def test_empty_cells_are_read_as_missing_not_zero(records):
    dip = next(s for s in records.strength_sets if s.exercise == "Chest Dip")
    assert dip.weight_kg is None      # bodyweight, not 0 kg
    assert dip.reps == 8


def test_distance_is_converted_from_kilometres(records):
    treadmill = next(s for s in records.strength_sets if s.exercise == "Treadmill")
    assert treadmill.distance_m == 3500
    assert treadmill.duration_s == 1200


def test_warmups_and_rpe_survive(records):
    bench = [s for s in records.strength_sets if s.exercise.startswith("Bench")]
    assert [s.set_type for s in bench] == ["warmup", "normal", "normal"]
    assert [s.rpe for s in bench] == [None, 7.0, 8.5]


def test_a_file_that_is_not_a_hevy_export_is_refused(hevy, config):
    path = config.root / "not-hevy.csv"
    path.write_text("date,weight\n2026-09-01,80\n")

    with pytest.raises(ValueError, match="does not look like a Hevy export"):
        hevy.add(path)


def test_the_rows_are_landed_verbatim_so_a_better_parser_can_reread_them(hevy):
    import json
    landed = hevy.add(FIXTURE)
    payload = json.loads(landed.read_text())

    assert payload["format"] == "hevy_csv"
    assert len(payload["rows"]) == 12
    assert payload["rows"][0]["exercise_title"] == "Bench Press (Barbell)"


# -- the false trend this data produced -------------------------------------

def test_one_name_two_implements_is_detected(hevy, records, store):
    """90 kg and 30 kg blocks of "Row (Machine)" in a single session are not
    the same movement, and the session best lurches with whichever was done."""
    store.load(records)

    conflicts = strength.variant_conflicts(store)

    assert "Row (Machine)" in conflicts
    assert conflicts["Row (Machine)"]["ranges"] == ["30-30 kg", "90-95 kg"]
    assert "Row (Cable)" not in conflicts      # one block only


def test_no_trend_is_reported_for_an_ambiguously_named_exercise(hevy, records, store):
    """Better no answer than a confident 25 kg/week decline that is really a
    naming problem."""
    store.load(records)
    # Enough sessions that a trend would otherwise be computed.
    for extra in ("8 Sep 2026, 09:00", "11 Sep 2026, 09:00", "15 Sep 2026, 09:00"):
        path = hevy.config.root / f"extra-{extra[:6].strip().replace(' ', '')}.csv"
        path.write_text(
            '"title","start_time","end_time","description","exercise_title",'
            '"superset_id","exercise_notes","set_index","set_type","weight_kg",'
            '"reps","distance_km","duration_seconds","rpe"\n'
            f'"Day 1","{extra}","{extra}","","Row (Machine)",,"",0,"normal",30,12,,,\n')
        store.load(hevy.parse(hevy.add(path)))

    result = strength.progression(store, "Row (Machine)")

    assert result.sessions >= 4
    assert result.trend_kg_per_week is None
    assert "two very different loads" in result.note
    assert "separate exercises" in result.describe()
