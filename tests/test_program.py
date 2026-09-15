from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from health.features import program
from health.models import ExerciseTemplate, Observation, Records, StrengthSet

BENCH = "Bench Press (Barbell)"
MONDAY = date(2026, 9, 14)          # push day in the week map

#: Enough of the catalogue to resolve every slot the programme asks for.
CATALOGUE = [
    (BENCH, "chest", "barbell"),
    ("Bench Press (Dumbbell)", "chest", "dumbbell"),
    ("Chest Press (Machine)", "chest", "machine"),
    ("Incline Bench Press (Dumbbell)", "chest", "dumbbell"),
    ("Incline Bench Press (Barbell)", "chest", "barbell"),
    ("Shoulder Press (Dumbbell)", "shoulders", "dumbbell"),
    ("Overhead Press (Barbell)", "shoulders", "barbell"),
    ("Chest Fly (Machine)", "chest", "machine"),
    ("Lateral Raise (Dumbbell)", "shoulders", "dumbbell"),
    ("Lateral Raise (Cable)", "shoulders", "machine"),
    ("Triceps Pushdown", "triceps", "machine"),
    ("Triceps Extension (Cable)", "triceps", "machine"),
    ("Pull Up", "lats", "none"),
    ("Lat Pulldown (Cable)", "lats", "machine"),
    ("Bent Over Row (Barbell)", "upper_back", "barbell"),
    ("Chest Supported Row (Dumbbell)", "upper_back", "dumbbell"),
    ("Seated Row (Cable)", "upper_back", "machine"),
    ("Face Pull", "shoulders", "machine"),
    ("Bicep Curl (Barbell)", "biceps", "barbell"),
    ("Bicep Curl (Dumbbell)", "biceps", "dumbbell"),
    ("Hammer Curl (Dumbbell)", "biceps", "dumbbell"),
    ("Squat (Barbell)", "quadriceps", "barbell"),
    ("Romanian Deadlift (Barbell)", "hamstrings", "barbell"),
    ("Deadlift (Barbell)", "hamstrings", "barbell"),
    ("Leg Press (Machine)", "quadriceps", "machine"),
    ("Seated Leg Curl (Machine)", "hamstrings", "machine"),
    ("Lying Leg Curl (Machine)", "hamstrings", "machine"),
    ("Leg Extension (Machine)", "quadriceps", "machine"),
    ("Standing Calf Raise (Machine)", "calves", "machine"),
    ("Seated Calf Raise (Machine)", "calves", "machine"),
    ("Bulgarian Split Squat", "quadriceps", "dumbbell"),
    ("Hip Thrust (Barbell)", "glutes", "barbell"),
    ("Hanging Leg Raise", "abdominals", "none"),
    ("Triceps Rope Pushdown", "triceps", "machine"),
    ("Skullcrusher (Barbell)", "triceps", "barbell"),
    ("Preacher Curl (Machine)", "biceps", "machine"),
    ("Iso-Lateral Row (Machine)", "upper_back", "machine"),
    ("Bulgarian Split Squat (Dumbbell)", "quadriceps", "dumbbell"),
    ("Seated Calf Raise", "calves", "machine"),
    ("Treadmill", "cardio", "machine"),
    ("Air Bike", "cardio", "machine"),
]


def _template_id(title: str) -> str:
    return "tpl-" + title.lower().replace(" ", "-")[:20]


@pytest.fixture
def gym(store):
    store.load(Records(exercise_templates=[
        ExerciseTemplate(source="hevy", template_id=_template_id(title), title=title,
                         primary_muscle=muscle, equipment=equipment)
        for title, muscle, equipment in CATALOGUE
    ]))
    return store


def _log(store, day: date, title: str, sets: list[tuple[float, int, str]]) -> None:
    ts = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    store.load(Records(strength_sets=[
        StrengthSet(source="hevy", workout_id=f"w-{day}-{title[:6]}", exercise_idx=0,
                    set_idx=i, ts=ts, local_date=day, exercise=title,
                    exercise_id=_template_id(title), set_type=kind,
                    weight_kg=weight, reps=reps)
        for i, (weight, reps, kind) in enumerate(sets)
    ]))


def _weigh(store, day: date, kg: float) -> None:
    store.load(Records(observations=[
        Observation(source="apple_health", source_id=f"weight-{day}",
                    metric="body_mass", value=kg,
                    ts=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
                    local_date=day, unit="kg")
    ]))


# -- the week ----------------------------------------------------------------

def test_the_week_runs_push_pull_legs_rest_upper_lower():
    kinds = [program.day_type(MONDAY + timedelta(days=offset)) for offset in range(7)]
    assert kinds == ["push", "pull", "legs", "rest", "upper", "lower", "rest"]


def test_deload_lands_every_sixth_week():
    anchor = date(2026, 1, 5)
    weeks = [program.is_deload(anchor + timedelta(weeks=n), anchor) for n in range(7)]
    assert weeks == [False, False, False, False, False, True, False]


def test_deloads_count_from_your_first_workout_not_the_calendar(gym):
    _log(gym, date(2026, 8, 19), BENCH, [(60.0, 8, "normal")])       # a Wednesday
    assert program.training_anchor(gym, MONDAY) == date(2026, 8, 17)  # that week's Monday
    assert not program.plan_session(gym, date(2026, 9, 12), auto_regulate=False).deload
    assert program.plan_session(gym, date(2026, 9, 26), auto_regulate=False).deload


def test_with_no_history_the_anchor_is_the_week_asked_for(store):
    assert program.training_anchor(store, date(2026, 9, 12)) == date(2026, 9, 7)


# -- matching ----------------------------------------------------------------

def test_every_slot_resolves_against_a_normal_catalogue(gym):
    rows = program.catalogue(gym)
    for kind, slots in program.SESSIONS.items():
        for slot in slots:
            assert program.match(slot.candidates, rows, program.EQUIPMENT), \
                f"{kind}/{slot.label} did not resolve"


def test_a_near_miss_is_not_accepted_as_the_lift(gym):
    rows = program.catalogue(gym)
    assert program.match(["Zercher Squat (Barbell)"], rows) is None


def test_missing_equipment_falls_through_to_the_next_candidate(gym):
    rows = program.catalogue(gym)
    chosen = program.match(program.SESSIONS["push"][0].candidates, rows,
                           {"machine", "dumbbell"})
    assert chosen["title"] != BENCH          # no barbell in this gym
    assert chosen["equipment"] in {"machine", "dumbbell"}


# -- progression -------------------------------------------------------------

SLOT = program.Slot("horizontal press", [BENCH], "compound", 4, (6, 9))


def test_filling_the_rep_range_adds_load():
    last = program.LastSession(BENCH, MONDAY, 80.0, 9, 9, 4)
    target = program.next_target(SLOT, last, 2.5)
    assert (target.weight_kg, target.reps) == (82.5, 6)


def test_short_of_the_top_chases_one_more_rep():
    last = program.LastSession(BENCH, MONDAY, 80.0, 7, 9, 4)
    target = program.next_target(SLOT, last, 2.5)
    assert (target.weight_kg, target.reps) == (80.0, 8)


def test_the_target_never_asks_for_fewer_reps_than_the_range():
    last = program.LastSession(BENCH, MONDAY, 80.0, 5, 6, 4)   # one short of the bottom
    target = program.next_target(SLOT, last, 2.5)
    assert target.reps == 6


def test_well_under_the_range_brings_the_load_down_by_epley():
    """5 reps at 80 kg is a ~93 kg e1RM; 10 reps off that is about 70 kg, not
    77.5 — a single plate off would still not give the reps asked for."""
    curl = program.Slot("hamstring curl", ["Lying Leg Curl (Machine)"], "isolation", 3, (10, 15))
    last = program.LastSession("Lying Leg Curl (Machine)", MONDAY, 80.0, 5, 5, 3)
    target = program.next_target(curl, last, 2.5)
    assert target.weight_kg == 70.0
    assert target.reps == 10
    assert "own the 10-15 range" in target.why


def test_light_loads_move_in_small_steps():
    raise_ = program.Slot("lateral raise", ["Lateral Raise (Cable)"], "isolation", 4, (12, 20))
    last = program.LastSession("Lateral Raise (Cable)", MONDAY, 6.25, 20, 20, 4)
    target = program.next_target(raise_, last, 2.5)
    assert target.weight_kg == 7.5                     # +1.25, not +2.5


def test_no_history_leaves_the_load_blank_rather_than_guessing():
    target = program.next_target(SLOT, None, 2.5)
    assert target.weight_kg is None
    assert target.reps == 6
    assert "no logged history" in target.why


def test_deload_drops_load_and_a_set():
    last = program.LastSession(BENCH, MONDAY, 80.0, 9, 9, 4)
    target = program.next_target(SLOT, last, 2.5, deload=True)
    assert target.weight_kg == 67.5
    assert target.sets == 3


def test_hold_repeats_the_session_even_when_the_range_was_filled():
    last = program.LastSession(BENCH, MONDAY, 80.0, 9, 9, 4)
    target = program.next_target(SLOT, last, 2.5, hold=True)
    assert target.weight_kg == 80.0


def test_history_reads_the_last_session_and_ignores_warmups(gym):
    _log(gym, MONDAY - timedelta(days=14), BENCH, [(70.0, 8, "normal")])
    _log(gym, MONDAY - timedelta(days=7), BENCH,
         [(40.0, 12, "warmup"), (80.0, 9, "normal"), (80.0, 9, "normal")])
    history = program.last_sessions(gym)
    last = history[_template_id(BENCH)]
    assert last.day == MONDAY - timedelta(days=7)
    assert (last.weight_kg, last.min_reps, last.sets) == (80.0, 9, 2)


def test_a_ramp_logged_as_working_sets_reads_its_top_set(gym):
    """Plenty of people log 20, 40, 50, 60 kg all as "normal". The session's
    load is the 60, and progression is judged on the reps done there."""
    _log(gym, MONDAY - timedelta(days=7), BENCH,
         [(20.0, 10, "normal"), (40.0, 10, "normal"), (50.0, 10, "normal"), (60.0, 8, "normal")])
    last = program.last_sessions(gym)[_template_id(BENCH)]
    assert (last.weight_kg, last.min_reps, last.sets) == (60.0, 8, 1)


def test_the_variant_you_actually_do_beats_the_textbook_one(gym):
    _log(gym, MONDAY - timedelta(days=7), "Triceps Rope Pushdown", [(30.0, 12, "normal")])
    plan = program.plan_session(gym, MONDAY, auto_regulate=False)
    triceps = next(e for e in plan.exercises if e.slot == "triceps pushdown")
    assert triceps.exercise == "Triceps Rope Pushdown"


def test_an_exercise_is_never_used_twice_in_one_session(gym):
    _log(gym, MONDAY - timedelta(days=7), "Leg Press (Machine)", [(200.0, 10, "normal")])
    plan = program.plan_session(gym, MONDAY + timedelta(days=2), auto_regulate=False)  # legs
    names = [e.exercise for e in plan.exercises]
    assert len(names) == len(set(names))
    assert "Leg Press (Machine)" in names


# -- steps -------------------------------------------------------------------

def test_a_stalled_scale_adds_steps(gym):
    for offset in range(14):
        _weigh(gym, MONDAY - timedelta(days=13 - offset), 85.0)
    steps = program.step_target(gym, MONDAY, "push")
    assert steps.target > program.STEPS_BASE
    assert "under" in steps.why


def test_losing_too_fast_takes_steps_away(gym):
    for offset in range(14):
        _weigh(gym, MONDAY - timedelta(days=13 - offset), 88.0 - offset * 0.25)
    steps = program.step_target(gym, MONDAY, "push")
    assert steps.target < program.STEPS_BASE
    assert "ceiling" in steps.why


def test_no_weight_data_says_so_instead_of_adapting(gym):
    steps = program.step_target(gym, MONDAY, "push")
    assert steps.target == program.STEPS_BASE
    assert "no bodyweight trend" in steps.why


def test_rest_days_carry_the_step_bonus(gym):
    lifting = program.step_target(gym, MONDAY, "push").target
    resting = program.step_target(gym, MONDAY + timedelta(days=3), "rest").target
    assert resting == lifting + program.STEPS_REST_BONUS


# -- the session -------------------------------------------------------------

def test_a_push_day_is_planned_end_to_end(gym):
    _log(gym, MONDAY - timedelta(days=7), BENCH, [(80.0, 9, "normal"), (80.0, 9, "normal")])
    plan = program.plan_session(gym, MONDAY, auto_regulate=False)

    assert plan.kind == "push"
    assert len(plan.exercises) == len(program.SESSIONS["push"])
    assert not plan.unmatched
    assert plan.exercises[0].weight_kg == 82.5          # progressed from history
    assert plan.cardio.kind == "zone2"                  # intervals are off while cutting
    assert plan.cardio.title == "Treadmill"
    assert plan.steps.target


def test_intervals_return_when_a_day_is_named(gym, monkeypatch):
    monkeypatch.setattr(program, "HIIT_DAY", "push")
    plan = program.plan_session(gym, MONDAY, auto_regulate=False)
    assert plan.cardio.kind == "hiit"
    assert plan.cardio.title == "Air Bike"


def test_other_lifting_days_get_zone_two(gym):
    plan = program.plan_session(gym, MONDAY + timedelta(days=1))
    assert plan.cardio.kind == "zone2"
    assert plan.cardio.minutes == program.ZONE2_MINUTES


def test_a_rest_day_plans_no_lifting_and_no_cardio(gym):
    plan = program.plan_session(gym, MONDAY + timedelta(days=3))
    assert plan.is_rest
    assert plan.exercises == []
    assert plan.cardio.kind == "none"
    assert plan.steps.target                            # steps still apply


def test_an_empty_catalogue_says_to_sync_rather_than_planning_nothing(store):
    plan = program.plan_session(store, MONDAY)
    assert plan.exercises == []
    assert any("sync" in reason for reason in plan.unmatched)


def test_pull_back_trims_the_session(gym, monkeypatch):
    _log(gym, MONDAY - timedelta(days=7), BENCH, [(80.0, 9, "normal"), (80.0, 9, "normal")])
    full = program.plan_session(gym, MONDAY, auto_regulate=False)

    from health.features import readiness as readiness_features

    class _Call:
        recommendation = readiness_features.PULL_BACK

    monkeypatch.setattr(readiness_features, "readiness", lambda *a, **k: _Call())
    trimmed = program.plan_session(gym, MONDAY)

    assert trimmed.readiness == "pull_back"
    assert len(trimmed.exercises) == len(full.exercises) - 1
    assert trimmed.exercises[0].sets < full.exercises[0].sets
    assert trimmed.exercises[0].weight_kg < full.exercises[0].weight_kg
    assert trimmed.cardio.kind == "zone2"               # intervals dropped
    assert trimmed.adjustments


def test_hold_stops_loads_advancing(gym, monkeypatch):
    _log(gym, MONDAY - timedelta(days=7), BENCH, [(80.0, 9, "normal"), (80.0, 9, "normal")])
    from health.features import readiness as readiness_features

    class _Call:
        recommendation = readiness_features.HOLD

    monkeypatch.setattr(readiness_features, "readiness", lambda *a, **k: _Call())
    plan = program.plan_session(gym, MONDAY)
    assert plan.exercises[0].weight_kg == 80.0


def test_readiness_computation_errors_are_not_silenced(gym, monkeypatch):
    from health.features import readiness as readiness_features

    def _boom(*args, **kwargs):
        raise RuntimeError("no recovery data")

    monkeypatch.setattr(readiness_features, "readiness", _boom)
    with pytest.raises(RuntimeError, match="no recovery data"):
        program.plan_session(gym, MONDAY)


def test_missing_readiness_holds_load(gym):
    _log(gym, MONDAY - timedelta(days=7), BENCH, [(80.0, 9, "normal")])
    plan = program.plan_session(gym, MONDAY)
    assert plan.readiness == "insufficient_data"
    assert plan.exercises[0].weight_kg == 80.0
    assert any("unknown" in a for a in plan.adjustments)


def test_historical_plan_ignores_later_workouts(gym):
    _log(gym, MONDAY - timedelta(days=7), BENCH, [(80.0, 9, "normal")])
    _log(gym, MONDAY + timedelta(days=7), BENCH, [(100.0, 9, "normal")])
    plan = program.plan_session(gym, MONDAY, auto_regulate=False)
    assert plan.exercises[0].weight_kg == 82.5


# -- the Hevy payload --------------------------------------------------------

def test_the_payload_is_shaped_the_way_hevy_expects(gym):
    _log(gym, MONDAY - timedelta(days=7), BENCH, [(80.0, 9, "normal")])
    plan = program.plan_session(gym, MONDAY)
    payload = program.routine_payload(plan, folder_id=7)

    assert payload["folder_id"] == 7
    assert payload["title"] == "AI Push"
    assert len(payload["exercises"]) == len(plan.exercises) + 1      # cardio appended
    first = payload["exercises"][0]
    assert len(first["sets"]) == plan.exercises[0].sets
    assert first["sets"][0]["rep_range"] == {"start": 6, "end": 9}
    assert plan.readiness == "insufficient_data"
    assert first["sets"][0]["weight_kg"] == 80.0
    assert payload["exercises"][-1]["sets"][0]["duration_seconds"] == program.ZONE2_MINUTES * 60


def test_notes_stay_inside_hevy_field_limits(gym):
    plan = program.plan_session(gym, MONDAY)
    payload = program.routine_payload(plan)
    assert len(payload["notes"]) <= 255
    assert all(len(exercise["notes"]) <= 255 for exercise in payload["exercises"])


def test_priority_volume_counts_secondary_muscles_at_half(store):
    store.load(Records(exercise_templates=[
        ExerciseTemplate(source="hevy", template_id="bench", title=BENCH,
                         primary_muscle="chest", secondary_muscles="triceps, shoulders",
                         equipment="barbell"),
    ]))
    _log_id = lambda day, sets: store.load(Records(strength_sets=[  # noqa: E731
        StrengthSet(source="hevy", workout_id=f"w-{day}", exercise_idx=0, set_idx=i,
                    ts=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
                    local_date=day, exercise=BENCH, exercise_id="bench",
                    set_type="normal", weight_kg=60.0, reps=8) for i in range(sets)]))
    _log_id(MONDAY - timedelta(days=2), 4)
    _log_id(MONDAY - timedelta(days=9), 4)          # outside the window
    by_muscle = {v.muscle: v for v in program.priority_volume(store, MONDAY)}
    assert by_muscle["chest"].sets == 4
    assert by_muscle["triceps"].sets == 2
    assert by_muscle["chest"].verdict == "under"


def test_protein_target_needs_a_bodyweight(gym):
    assert program.protein_target(gym, MONDAY).target_g is None
    for offset in range(4):
        _weigh(gym, MONDAY - timedelta(days=offset), 85.0)
    protein = program.protein_target(gym, MONDAY)
    assert protein.target_g == 170                   # 2 g/kg, rounded to 5
    assert "85 kg" in protein.why


def test_the_plan_serialises_for_the_agent(gym):
    plan = program.plan_session(gym, MONDAY)
    data = plan.as_dict()
    assert data["session"] == "push"
    assert data["steps"]["target"]
    assert isinstance(data["exercises"], list)
