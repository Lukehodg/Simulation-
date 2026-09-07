from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from health.features import (
    exercise_summary, progression, session_history, stale_lifts, weekly_volume,
)
from health.models import ExerciseTemplate, Records, StrengthSet

RDL = "Romanian Deadlift (Barbell)"


def _sets(store, day: date, exercise: str, sets, exercise_id="rdl-tmpl"):
    ts = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    records = Records(strength_sets=[
        StrengthSet(source="hevy", workout_id=f"w-{day}-{exercise[:4]}",
                    exercise_idx=0, set_idx=i, ts=ts, local_date=day,
                    exercise=exercise, exercise_id=exercise_id,
                    set_type=set_type, weight_kg=weight, reps=reps)
        for i, (weight, reps, set_type) in enumerate(sets)
    ])
    store.load(records)


@pytest.fixture
def trained(store):
    store.load(Records(exercise_templates=[
        ExerciseTemplate(source="hevy", template_id="rdl-tmpl", title=RDL,
                         primary_muscle="hamstrings", equipment="barbell"),
        ExerciseTemplate(source="hevy", template_id="bp-tmpl", title="Bench Press",
                         primary_muscle="chest", equipment="barbell"),
    ]))
    start = date(2026, 7, 6)
    for week, weight in enumerate([80.0, 82.5, 85.0, 85.0, 87.5, 90.0]):
        day = start + timedelta(weeks=week)
        _sets(store, day, RDL, [(40, 8, "warmup"), (weight, 5, "normal"),
                                (weight, 5, "normal")])
    return store


def test_warmups_are_excluded_from_everything(trained):
    rows = trained.query("SELECT COUNT(*) FROM working_sets")
    assert rows == [(12,)]  # 6 sessions x 2 working sets, warmups dropped


def test_session_best_uses_the_best_working_set(trained):
    history = session_history(trained, RDL)
    assert len(history) == 6
    latest = history[0]
    assert latest[0] == date(2026, 8, 10)
    assert latest[1] == pytest.approx(90 * (1 + 5 / 30))   # 105.0
    assert latest[2] == 90.0                                # top weight
    assert latest[4] == pytest.approx(900.0)                # 2 x 90 x 5


def test_progression_reports_a_weekly_slope_with_its_fit(trained):
    result = progression(trained, RDL)

    assert result.sessions == 6
    # Endpoints alone would say 2.33 kg/week (11.67 kg over 5 weeks); the
    # regression is lower because it sees the plateau week, which is the point
    # of fitting a line rather than subtracting the first session from the last.
    assert result.trend_kg_per_week == pytest.approx(2.167, abs=0.01)
    assert result.r_squared > 0.9
    assert result.best_e1rm == pytest.approx(105.0)
    assert result.days_since_best == 0
    assert "up" in result.describe()


def test_progression_refuses_a_trend_from_too_few_sessions(store):
    _sets(store, date(2026, 9, 1), RDL, [(100, 5, "normal")])
    _sets(store, date(2026, 9, 8), RDL, [(105, 5, "normal")])

    result = progression(store, RDL)

    assert result.sessions == 2
    assert result.trend_kg_per_week is None
    assert "at least 4" in result.note
    assert result.describe() == result.note


def test_progression_is_case_insensitive_and_handles_unknown_exercises(trained):
    assert progression(trained, RDL.lower()).sessions == 6

    missing = progression(trained, "Zercher Squat")
    assert missing.sessions == 0
    assert "no working sets" in missing.note


def test_high_rep_sets_count_for_volume_but_not_for_strength(store):
    _sets(store, date(2026, 9, 1), "Leg Extension", [(40, 25, "normal")])

    rows = store.query("SELECT e1rm, volume_kg FROM working_sets")
    assert rows[0][0] is None            # 25 reps: Epley would be fiction
    assert rows[0][1] == 1000.0          # but a thousand kilos moved is real

    result = progression(store, "Leg Extension")
    assert result.sessions == 0


def test_weekly_volume_splits_by_muscle_and_keeps_unmapped_visible(trained):
    _sets(trained, date(2026, 8, 10), "Cable Thing", [(30, 10, "normal")],
          exercise_id="no-such-template")

    rows = weekly_volume(trained, weeks=12)
    latest_week = max(r[0] for r in rows)
    muscles = {r[1]: r[2] for r in rows if r[0] == latest_week}

    assert muscles["hamstrings"] == pytest.approx(900.0)
    assert muscles["unmapped"] == pytest.approx(300.0)


def test_weekly_volume_returns_exactly_the_weeks_asked_for(trained):
    assert len({row[0] for row in weekly_volume(trained, weeks=3)}) == 3
    assert len({row[0] for row in weekly_volume(trained, weeks=6)}) == 6


def test_stale_lifts_ignores_one_off_experiments(trained):
    _sets(trained, date(2026, 7, 6), "Zercher Squat", [(60, 5, "normal")])
    _sets(trained, date(2026, 7, 6), "Bench Press", [(50, 5, "normal")],
          exercise_id="bp-tmpl")
    _sets(trained, date(2026, 7, 13), "Bench Press", [(52.5, 5, "normal")],
          exercise_id="bp-tmpl")

    stale = stale_lifts(trained, days=21, as_of=date(2026, 8, 10))
    names = [row["exercise"] for row in stale]

    assert "Bench Press" in names          # trained twice, then dropped
    assert "Zercher Squat" not in names    # tried once; not an abandoned lift
    assert RDL not in names                # trained this week
    assert next(r for r in stale if r["exercise"] == "Bench Press")["days_since"] == 28


def test_exercise_summary_carries_the_muscle_group(trained):
    rows = {row["exercise"]: row for row in exercise_summary(trained)}
    assert rows[RDL]["muscle"] == "hamstrings"
    assert rows[RDL]["sessions"] == 6
    assert rows[RDL]["best_e1rm"] == pytest.approx(105.0)
