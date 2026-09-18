"""The next session, written from your own sets — and pushed back to Hevy.

Everything else in `features/` reads. This module is the one that writes: it
takes the sets you actually logged, applies double progression, and produces a
routine Hevy can open. That is deliberate — the same rule as
`features/training.py`. Adjusting rep ranges is not medicine, the feedback loop
is one session long, and the cost of being wrong is a mediocre week.

Three things make this different from a programme written on paper:

  * **Loads come from your history, not a percentage of a max.** Hold the
    weight until every working set reaches the top of the rep range, then add
    one increment and reset to the bottom. No 1RM test, no RPE guesswork.
  * **The session is auto-regulated.** `readiness()` already knows whether
    today argues for pushing; a `pull_back` call takes volume and load off
    before you get to the gym rather than after a bad session.
  * **Cardio and steps are prescribed against the scale.** Losing slower than
    the target band adds steps; faster than it takes them away, because weight
    coming off too fast stops being fat.

The split is Push / Pull / Legs / Rest / Upper / Lower, with cardio placed
after lifting (doing it first measurably blunts strength output) and one HIIT
slot per cycle on push day, as far from leg day as the rotation allows.

It is a chest-and-arms specialisation block. Those muscles get 16-20 hard
sets a week across three exposures and go first in their sessions; legs,
back and shoulders sit at maintenance volume so the recovery budget of a
deficit is spent where the growth is wanted. `priority_volume` reports
whether the last seven days actually delivered that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean

from ..store import Store
from . import readiness as readiness_features

# -- the programme -----------------------------------------------------------

#: Weekday -> day type. Index 0 is Monday, matching date.weekday().
WEEK = ["push", "pull", "legs", "rest", "upper", "lower", "rest"]
#: Every sixth week backs off: 85% load, one set fewer, cardio halved.
DELOAD_EVERY_WEEKS = 6
#: Hevy's equipment vocabulary. Narrow this if your gym is missing something.
EQUIPMENT = {"barbell", "dumbbell", "machine", "kettlebell", "plate",
             "resistance_band", "suspension", "none", "other"}
#: What one progression step adds, by implement.
INCREMENT_KG = {"barbell": 2.5, "dumbbell": 2.0, "machine": 2.5, "kettlebell": 4.0,
                "plate": 2.5, "resistance_band": 0.0, "none": 2.5, "other": 2.5}
REST_SECONDS = {"compound": 180, "secondary": 120, "isolation": 75}
#: Sets above this rep count are endurance work; they still count for volume.
MAX_SENSIBLE_REPS = 30

#: Fat loss band, percent of bodyweight per week. Below the floor the deficit
#: is not working; above the ceiling you start paying in lean mass. This is
#: the recomposition band, slower than a straight cut on purpose: the goal
#: is to add chest and arm tissue while the scale comes down, and muscle is
#: not built in a steep deficit.
LOSS_BAND_PCT = (0.3, 0.7)
#: Protein per kg of bodyweight per day: the one nutrition lever that decides
#: whether a deficit costs muscle. Returns flatten around 1.6 in the
#: meta-analyses; 2.0 is the sensible target while cutting.
PROTEIN_G_PER_KG = 2.0
STEPS_BASE, STEPS_MIN, STEPS_MAX = 12_000, 10_000, 14_000
STEPS_REST_BONUS = 1_000
ZONE2_MINUTES = 30
#: Which day carries the intervals. None means every session finishes with
#: zone 2 instead: intervals cost recovery that a deep deficit and a sleep
#: debt do not leave spare, and the steps already do the fat-loss work.
#: Set back to "push" once intake is at target and sleep debt is under 3 h.
HIIT_DAY: str | None = None
HIIT_ROUNDS, HIIT_WORK_S, HIIT_EASY_S = 8, 30, 90
#: Hevy's own titles. Treadmill first: an incline walk is the zone 2 that
#: costs the legs least the day before they are trained.
ZONE2_MACHINES = ["Treadmill", "Cycling", "Elliptical Trainer", "Stair Machine (Steps)"]
HIIT_MACHINES = ["Air Bike", "Rowing Machine", "Spinning", "Treadmill"]


@dataclass
class Slot:
    """One exercise slot. Candidates are tried in order against your catalogue,
    so a missing machine falls through to the next choice rather than dropping
    the muscle from the session."""

    label: str
    candidates: list[str]
    role: str                  # compound | secondary | isolation
    sets: int
    reps: tuple[int, int]
    cue: str = ""


def _s(label, candidates, role, sets, lo, hi, cue=""):
    return Slot(label, candidates, role, sets, (lo, hi), cue)


#: Chest and arms are the point of this block. They get 16-20 hard sets a
#: week at three exposures; everything else sits at the bottom of the
#: productive range (8-10) so recovery goes where the growth is wanted.
#: Counted with secondary muscles at half weight, so the arm targets include
#: what pressing and pulling already give them.
PRIORITY = {"chest": (16, 22), "triceps": (12, 20), "biceps": (12, 20)}
MAINTENANCE = (8, 10)

SESSIONS: dict[str, list[Slot]] = {
    "push": [
        _s("horizontal press", ["Bench Press (Barbell)", "Bench Press (Dumbbell)",
                                "Chest Press (Machine)", "Bench Press (Smith Machine)"],
           "compound", 4, 6, 9, "1-2 reps in reserve; control the eccentric"),
        _s("incline press", ["Incline Bench Press (Dumbbell)", "Incline Chest Press (Machine)",
                             "Incline Bench Press (Barbell)"], "secondary", 4, 8, 12,
           "full stretch at the bottom; upper chest is where most people are behind"),
        _s("chest fly", ["Cable Fly Crossovers", "Butterfly (Pec Deck)", "Chest Fly (Machine)",
                         "Low Cable Fly Crossovers", "Chest Fly (Dumbbell)"],
           "isolation", 3, 12, 15, "stretch-focused, slow negative"),
        _s("overhead press", ["Shoulder Press (Dumbbell)", "Shoulder Press (Machine Plates)",
                              "Shoulder Press (Machine)", "Overhead Press (Barbell)"],
           "secondary", 2, 8, 12),
        _s("triceps pushdown", ["Triceps Rope Pushdown", "Triceps Pushdown",
                                "Triceps Extension (Cable)"], "isolation", 3, 10, 15,
           "elbows pinned, full lockout"),
        _s("triceps overhead", ["Skullcrusher (Barbell)", "Overhead Triceps Extension (Cable)",
                                "Triceps Extension (Dumbbell)", "Skullcrusher (Dumbbell)"],
           "isolation", 3, 10, 15, "overhead or lying: the long head only grows stretched"),
    ],
    "pull": [
        _s("vertical pull", ["Lat Pulldown (Cable)", "Pull Up", "Lat Pulldown - Close Grip (Cable)",
                             "Lat Pulldown (Machine)"], "compound", 3, 6, 10,
           "elbows down and back, pause at the bottom"),
        _s("horizontal row", ["Bent Over Row (Barbell)", "Iso-Lateral Row (Machine)",
                              "Seated Row (Cable)", "Seated Cable Row - V Grip (Cable)"],
           "compound", 3, 6, 10),
        _s("rear delts", ["Rear Delt Reverse Fly (Machine)", "Face Pull",
                          "Rear Delt Reverse Fly (Dumbbell)"], "isolation", 2, 15, 20,
           "high reps, no momentum"),
        _s("biceps heavy", ["EZ Bar Biceps Curl", "Bicep Curl (Barbell)", "Bicep Curl (Dumbbell)",
                            "Bicep Curl (Cable)"], "isolation", 4, 8, 12,
           "a priority lift, not a finisher: treat it like a compound"),
        _s("biceps stretch", ["Incline Curl (Dumbbell)", "Behind the Back Curl (Cable)",
                              "Preacher Curl (Machine)", "Bicep Curl (Cable)"],
           "isolation", 3, 10, 15, "lengthened position, full extension every rep"),
        _s("brachialis", ["Hammer Curl (Dumbbell)", "Hammer Curl (Cable)",
                          "Reverse Curl (Barbell)"], "isolation", 3, 10, 15,
           "fills out the arm from the side"),
    ],
    "legs": [
        _s("squat pattern", ["Squat (Barbell)", "Hack Squat (Machine)", "Leg Press (Machine)"],
           "compound", 3, 6, 10, "depth over load, brace hard"),
        _s("hip hinge", ["Romanian Deadlift (Barbell)", "Romanian Deadlift (Dumbbell)",
                         "Stiff Leg Deadlift (Barbell)", "Deadlift (Barbell)"],
           "compound", 3, 8, 10, "feel the hamstring stretch, back flat"),
        _s("hamstring curl", ["Lying Leg Curl (Machine)", "Seated Leg Curl (Machine)",
                              "Leg Curl (Machine)"], "isolation", 2, 10, 15),
        _s("quad extension", ["Leg Extension (Machine)", "Sissy Squat"],
           "isolation", 2, 12, 15, "pause at lockout"),
        _s("calves", ["Seated Calf Raise", "Standing Calf Raise (Machine)",
                      "Seated Calf Raise (Machine)", "Standing Calf Raise (Dumbbell)"],
           "isolation", 3, 10, 15, "full stretch, one second at the top"),
        _s("chest pump", ["Cable Fly Crossovers", "Butterfly (Pec Deck)", "Chest Fly (Machine)",
                          "Push Up"], "isolation", 3, 15, 20,
           "legs are done; a third weekly chest exposure, light and high rep"),
    ],
    "upper": [
        _s("incline press", ["Incline Bench Press (Barbell)", "Incline Bench Press (Dumbbell)",
                             "Incline Chest Press (Machine)"], "compound", 4, 6, 9),
        _s("chest press", ["Chest Press (Machine)", "Bench Press (Dumbbell)",
                           "Bench Press (Barbell)", "Bench Press (Smith Machine)"],
           "secondary", 3, 10, 12, "a different angle from push day"),
        _s("vertical pull", ["Lat Pulldown (Cable)", "Pull Up", "Reverse Grip Lat Pulldown (Cable)",
                             "Lat Pulldown (Machine)"], "compound", 3, 8, 12),
        _s("row", ["Seated Row (Cable)", "Seated Cable Row - V Grip (Cable)",
                   "Chest Supported Row (Dumbbell)", "Iso-Lateral Row (Machine)",
                   "Seated Row (Machine)"], "secondary", 2, 10, 12),
        _s("lateral raise", ["Lateral Raise (Dumbbell)", "Lateral Raise (Cable)",
                             "Lateral Raise (Machine)"], "isolation", 3, 15, 20),
        _s("biceps", ["Bicep Curl (Dumbbell)", "Behind the Back Curl (Cable)",
                      "Bicep Curl (Cable)", "Hammer Curl (Dumbbell)"], "isolation", 3, 10, 15,
           "superset with the triceps below"),
        _s("triceps", ["Triceps Extension (Cable)", "Skullcrusher (Barbell)",
                       "Triceps Rope Pushdown", "Triceps Pushdown"], "isolation", 3, 10, 15),
    ],
    "lower": [
        _s("hinge", ["Romanian Deadlift (Barbell)", "Deadlift (Barbell)",
                     "Romanian Deadlift (Dumbbell)", "Trap Bar Deadlift"], "compound", 3, 5, 8,
           "heavy but clean; stop the set when bar speed drops"),
        _s("unilateral", ["Bulgarian Split Squat (Dumbbell)", "Bulgarian Split Squat",
                          "Walking Lunge (Dumbbell)", "Step Up (Dumbbell)"],
           "secondary", 3, 8, 12, "per leg, front knee tracking the toes"),
        _s("hamstring curl", ["Seated Leg Curl (Machine)", "Lying Leg Curl (Machine)",
                              "Leg Curl (Machine)"], "isolation", 2, 10, 15),
        _s("calves", ["Standing Calf Raise (Machine)", "Seated Calf Raise",
                      "Seated Calf Raise (Machine)", "Calf Press (Machine)"],
           "isolation", 3, 12, 20),
        _s("biceps", ["Hammer Curl (Dumbbell)", "EZ Bar Biceps Curl", "Bicep Curl (Dumbbell)",
                      "Bicep Curl (Cable)"], "isolation", 3, 10, 15,
           "arms on leg day: they are fresh and the legs are finished"),
        _s("triceps", ["Triceps Rope Pushdown", "Triceps Pushdown", "Triceps Extension (Cable)",
                       "Skullcrusher (Barbell)"], "isolation", 3, 10, 15,
           "superset with the curls"),
        _s("core", ["Decline Crunch (Weighted)", "Hanging Leg Raise", "Cable Crunch", "Plank"],
           "isolation", 2, 10, 15, "slow and controlled, no swinging"),
    ],
}


def day_type(day: date) -> str:
    return WEEK[day.weekday()]


def is_deload(day: date, anchor: date) -> bool:
    """Deload weeks are counted in whole weeks from `anchor` (a Monday), so
    the cadence does not drift when a session is missed."""
    week_index = (day - anchor).days // 7
    return week_index >= 0 and week_index % DELOAD_EVERY_WEEKS == DELOAD_EVERY_WEEKS - 1


def training_anchor(store: Store, fallback: date) -> date:
    """The Monday of the week you first logged a working set. Counting deloads
    from here rather than from a calendar date means the first one arrives
    after six weeks of *your* training, not whenever the year happens to fall."""
    row = store.query("SELECT MIN(local_date) FROM working_sets WHERE local_date <= ?", [fallback])
    first = row[0][0] if row and row[0][0] else fallback
    return first - timedelta(days=first.weekday())


# -- matching your catalogue -------------------------------------------------

_PUNCT = re.compile(r"[^a-z0-9 ]+")


def _tokens(title: str) -> list[str]:
    return _PUNCT.sub(" ", (title or "").lower()).split()


def _score(candidate: str, title: str) -> float:
    want, have = _tokens(candidate), _tokens(title)
    if want == have:
        return 1.0
    if not want or not have:
        return 0.0
    coverage = sum(1 for token in want if token in have) / len(want)
    # Penalise extra words: "Bench Press (Smith Machine)" is not a bench press.
    return max(0.0, coverage - max(0, len(have) - len(want)) * 0.06)


def catalogue(store: Store) -> list[dict]:
    rows = store.query(
        "SELECT template_id, title, primary_muscle, equipment, is_custom "
        "FROM exercise_templates WHERE source = 'hevy' AND title IS NOT NULL")
    return [{"template_id": t, "title": n, "primary_muscle": m,
             "equipment": e or "other", "is_custom": bool(c)} for t, n, m, e, c in rows]


#: A candidate you have actually logged beats one you have not, even a few
#: places further down the list. The list ranks the textbook options; the
#: history says which variant is yours.
LOGGED_BONUS = 0.08


def match(candidates: list[str], catalogue_rows: list[dict],
          equipment: set[str] | None = None, *,
          logged: set[str] | None = None,
          exclude: set[str] | None = None) -> dict | None:
    """Best template for a list of candidate titles, or None if the catalogue
    has nothing close enough. `logged` template ids get a preference;
    `exclude` ids are already in the session and cannot be picked again."""
    best, best_score = None, 0.75          # anything below this is a wrong lift
    for rank, candidate in enumerate(candidates):
        for row in catalogue_rows:
            if equipment and row["equipment"] not in equipment:
                continue
            if exclude and row["template_id"] in exclude:
                continue
            score = _score(candidate, row["title"]) - rank * 0.01
            if score < 0.75:
                continue
            if logged and row["template_id"] in logged:
                score += LOGGED_BONUS
            if row["is_custom"]:
                score += 0.005             # your own version wins a tie
            if score > best_score:
                best, best_score = row, score
    return best


# -- what you last did -------------------------------------------------------

@dataclass
class LastSession:
    exercise: str
    day: date
    weight_kg: float
    min_reps: int
    max_reps: int
    sets: int


def last_sessions(store: Store, as_of: date | None = None) -> dict[str, LastSession]:
    """The top set of the most recent session per exercise, keyed by Hevy
    template id.

    The *top* set, not the median: a lot of people log an ascending ramp
    (20, 40, 50, 60 kg) with every rung marked "normal", and averaging that
    reports a working load nobody actually worked at. The heaviest weight of
    the session is the one that was meant, and progression is judged on the
    reps done at that weight. Warm-ups proper are already excluded by
    `working_sets`, which is why this reads the view rather than the table.
    """
    rows = store.query("""
        WITH last_day AS (
            SELECT exercise_id, MAX(local_date) AS day
            FROM working_sets
            WHERE exercise_id IS NOT NULL AND local_date <= ?
            GROUP BY exercise_id
        ),
        session AS (
            SELECT w.exercise_id, w.exercise, l.day, w.weight_kg, w.reps
            FROM working_sets w
            JOIN last_day l ON l.exercise_id = w.exercise_id AND l.day = w.local_date
            WHERE w.reps <= ?
        ),
        top AS (
            SELECT exercise_id, MAX(weight_kg) AS top_kg FROM session GROUP BY exercise_id
        )
        SELECT s.exercise_id, ANY_VALUE(s.exercise), ANY_VALUE(s.day), t.top_kg,
               MIN(s.reps), MAX(s.reps), COUNT(*)
        FROM session s
        JOIN top t ON t.exercise_id = s.exercise_id
        WHERE s.weight_kg >= t.top_kg * 0.975
        GROUP BY s.exercise_id, t.top_kg
    """, [as_of or date.max, MAX_SENSIBLE_REPS])
    return {
        str(tid): LastSession(str(name), day, float(weight), int(lo), int(hi), int(n))
        for tid, name, day, weight, lo, hi, n in rows if weight
    }


def logged_template_ids(store: Store, as_of: date | None = None) -> set[str]:
    """Every exercise with at least one working set — the ones you actually do."""
    rows = store.query(
        "SELECT DISTINCT exercise_id FROM working_sets WHERE exercise_id IS NOT NULL "
        "AND local_date <= ?", [as_of or date.max])
    return {str(tid) for (tid,) in rows}


# -- progression -------------------------------------------------------------

@dataclass
class Target:
    weight_kg: float | None
    reps: int
    reps_range: tuple[int, int]
    sets: int
    why: str


def _round_to(value: float, step: float) -> float:
    return round(value, 1) if step <= 0 else round(round(value / step) * step, 2)


#: Below this load a full plate is a big percentage jump: 2.5 kg on a 6 kg
#: lateral raise is 40%. Stacks and micro-plates go in 1.25s down there.
LIGHT_LOAD_KG = 20.0
LIGHT_STEP_KG = 1.25


def _step(weight_kg: float, increment: float) -> float:
    if increment <= 0:
        return 0.0
    if weight_kg < LIGHT_LOAD_KG and increment > LIGHT_STEP_KG:
        return LIGHT_STEP_KG
    return increment


def _load_for_reps(weight_kg: float, reps: int, target_reps: int) -> float:
    """Epley both ways: the load that should give `target_reps`, from a set
    of `reps` at `weight_kg`. Same formula as the `working_sets` view, so a
    number here and a number on the lifts table agree."""
    e1rm = weight_kg * (1 + reps / 30)
    return e1rm / (1 + target_reps / 30)


def next_target(slot: Slot, last: LastSession | None, increment: float,
                *, deload: bool = False, hold: bool = False) -> Target:
    """Double progression: fill the rep range, then add load and reset."""
    lo, hi = slot.reps

    if last is None:
        return Target(None, lo, slot.reps, slot.sets,
                      "no logged history — pick a load you can hold for the range at 2 RIR")

    step = _step(last.weight_kg, increment)

    if deload:
        return Target(_round_to(last.weight_kg * 0.85, step or 2.5), lo, slot.reps,
                      max(2, slot.sets - 1),
                      f"deload — 85% of {last.weight_kg:g} kg, one set fewer, stop well short")

    if last.min_reps >= hi and not hold:
        return Target(_round_to(last.weight_kg + step, step or 2.5), lo, slot.reps,
                      slot.sets,
                      f"hit {hi} at {last.weight_kg:g} kg — load goes up {step:g} kg")

    # Well under the range at that load means the weight was chosen for a
    # different rep target (a 5-rep top set on a 10-15 slot). Rather than ask
    # for reps that were not there last time, estimate the load that should
    # give the bottom of the range from the top set's e1RM, and never land
    # above one step below what was lifted.
    if last.min_reps < lo - 1 and step and not hold:
        estimate = _load_for_reps(last.weight_kg, last.min_reps, lo)
        lighter = min(_round_to(estimate, step), _round_to(last.weight_kg - step, step))
        return Target(lighter, lo, slot.reps, slot.sets,
                      f"{last.min_reps} at {last.weight_kg:g} kg puts {lo} reps at about "
                      f"{lighter:g} kg — own the {lo}-{hi} range there first")

    if hold:
        return Target(last.weight_kg, max(lo, min(hi, last.min_reps)), slot.reps, slot.sets,
                      f"recovery says hold — repeat {last.weight_kg:g} kg rather than adding")

    target = max(lo, min(hi, last.min_reps + 1))
    return Target(last.weight_kg, target, slot.reps, slot.sets,
                  f"hold {last.weight_kg:g} kg, chase {target} reps on every set")


# -- conditioning ------------------------------------------------------------

@dataclass
class Cardio:
    kind: str                      # zone2 | hiit | none
    title: str | None
    template_id: str | None
    sets: list[dict]
    minutes: int
    note: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "exercise": self.title, "minutes": self.minutes,
                "note": self.note}


def cardio_block(catalogue_rows: list[dict], kind: str, *, deload: bool = False) -> Cardio:
    if kind == "none":
        return Cardio("none", None, None, [], 0, "")

    if kind == "hiit":
        rounds = max(4, HIIT_ROUNDS // 2) if deload else HIIT_ROUNDS
        template = match(HIIT_MACHINES, catalogue_rows)
        minutes = round((rounds * (HIIT_WORK_S + HIIT_EASY_S) + 600) / 60)
        return Cardio(
            "hiit", template["title"] if template else None,
            template["template_id"] if template else None,
            [{"type": "normal", "duration_seconds": HIIT_WORK_S} for _ in range(rounds)],
            minutes,
            f"5 min easy, then {rounds} x {HIIT_WORK_S}s hard / {HIIT_EASY_S}s easy, "
            f"5 min down. Hard means you cannot hold a conversation.")

    minutes = max(15, ZONE2_MINUTES // 2) if deload else ZONE2_MINUTES
    template = match(ZONE2_MACHINES, catalogue_rows)
    return Cardio(
        "zone2", template["title"] if template else None,
        template["template_id"] if template else None,
        [{"type": "normal", "duration_seconds": minutes * 60}], minutes,
        f"{minutes} min steady at 60-70% of max heart rate — conversational, "
        f"not comfortable. An incline walk counts.")


# -- steps -------------------------------------------------------------------

@dataclass
class Steps:
    target: int
    why: str
    recent_average: int | None = None
    weight_kg: float | None = None
    trend_kg_per_week: float | None = None

    def as_dict(self) -> dict:
        return {"target": self.target, "why": self.why,
                "recent_average": self.recent_average,
                "weight_kg": self.weight_kg,
                "trend_kg_per_week": round(self.trend_kg_per_week, 2)
                if self.trend_kg_per_week is not None else None}


def weight_trend(store: Store, as_of: date, days: int = 21) -> tuple[float | None, float | None]:
    """(kg per week, latest weight). Negative means losing.

    Halves-comparison rather than a regression: bodyweight is noisy day to day
    and two means over a fortnight are harder to fool than a slope through
    water weight.
    """
    rows = store.query(
        "SELECT local_date, value FROM daily_metrics "
        "WHERE metric = 'body_mass' AND local_date BETWEEN ? AND ? "
        "ORDER BY local_date", [as_of - timedelta(days=days), as_of])
    points = [(d, float(v)) for d, v in rows if v is not None]
    if not points:
        return None, None
    if len(points) < 4:
        return None, points[-1][1]

    half = len(points) // 2
    early, late = points[:half], points[-half:]
    span = (late[-1][0] - early[0][0]).days or 1
    delta = mean(v for _, v in late) - mean(v for _, v in early)
    return delta / span * 7, points[-1][1]


def step_target(store: Store, day: date, kind: str) -> Steps:
    trend, weight = weight_trend(store, day)
    recent = store.query(
        "SELECT AVG(value) FROM daily_metrics "
        "WHERE metric = 'steps' AND local_date BETWEEN ? AND ?",
        [day - timedelta(days=7), day - timedelta(days=1)])
    average = int(recent[0][0]) if recent and recent[0][0] is not None else None
    bonus = STEPS_REST_BONUS if kind == "rest" else 0

    if trend is None or weight is None:
        return Steps(min(STEPS_MAX, STEPS_BASE + bonus),
                     "no bodyweight trend yet — log weight and the target starts adapting",
                     average, weight, None)

    losing = -trend
    floor_kg, ceiling_kg = (weight * pct / 100 for pct in LOSS_BAND_PCT)
    if losing < floor_kg:
        target = min(STEPS_MAX, STEPS_BASE + 1000 + bonus)
        why = (f"losing {losing:.2f} kg/wk, under the {floor_kg:.2f} kg floor "
               f"for {weight:g} kg — steps up")
    elif losing > ceiling_kg:
        target = max(STEPS_MIN, STEPS_BASE - 1000 + bonus)
        why = (f"losing {losing:.2f} kg/wk, past the {ceiling_kg:.2f} kg ceiling — "
               f"ease off before the weight starts coming off muscle")
    else:
        target = min(STEPS_MAX, STEPS_BASE + bonus)
        why = f"losing {losing:.2f} kg/wk, inside the target band — hold steady"

    return Steps(int(round(target / 500) * 500), why, average, weight, trend)


# -- did the week deliver the priorities? -------------------------------------

@dataclass
class MuscleVolume:
    muscle: str
    sets: float
    target: tuple[int, int]

    @property
    def verdict(self) -> str:
        lo, hi = self.target
        if self.sets < lo:
            return "under"
        if self.sets > hi:
            return "over"
        return "on target"

    def as_dict(self) -> dict:
        return {"muscle": self.muscle, "sets": round(self.sets, 1),
                "target": list(self.target), "verdict": self.verdict}


def priority_volume(store: Store, as_of: date, days: int = 7) -> list[MuscleVolume]:
    """Hard sets per priority muscle over the last `days`, against the block's
    targets. A secondary muscle counts half a set, the usual convention: a
    bench press is chest work that also happens to the triceps."""
    rows = store.query("""
        SELECT t.primary_muscle, t.secondary_muscles, COUNT(*)
        FROM working_sets w
        JOIN exercise_templates t ON t.template_id = w.exercise_id AND t.source = 'hevy'
        WHERE w.local_date > ? AND w.local_date <= ?
        GROUP BY t.primary_muscle, t.secondary_muscles
    """, [as_of - timedelta(days=days), as_of])
    counts: dict[str, float] = {m: 0.0 for m in PRIORITY}
    for primary, secondary, n in rows:
        if primary in counts:
            counts[primary] += n
        for muscle in (secondary or "").split(","):
            muscle = muscle.strip()
            if muscle in counts:
                counts[muscle] += n * 0.5
    return [MuscleVolume(m, counts[m], PRIORITY[m]) for m in PRIORITY]


@dataclass
class Protein:
    target_g: int | None
    yesterday_g: float | None
    why: str

    def as_dict(self) -> dict:
        return {"target_g": self.target_g, "yesterday_g": self.yesterday_g, "why": self.why}


def protein_target(store: Store, day: date) -> Protein:
    _trend, weight = weight_trend(store, day)
    row = store.query(
        "SELECT value FROM daily_metrics WHERE metric = 'protein' AND local_date = ?",
        [day - timedelta(days=1)])
    yesterday = float(row[0][0]) if row and row[0][0] is not None else None
    if weight is None:
        return Protein(None, yesterday,
                       f"{PROTEIN_G_PER_KG:g} g per kg: log bodyweight to get the number")
    target = int(round(weight * PROTEIN_G_PER_KG / 5) * 5)
    why = f"{PROTEIN_G_PER_KG:g} g/kg at {weight:g} kg"
    if yesterday is not None:
        gap = target - yesterday
        why += (f"; yesterday {yesterday:.0f} g, {gap:.0f} g short" if gap > 10
                else f"; yesterday {yesterday:.0f} g, on target")
    return Protein(target, yesterday, why)


# -- energy balance ------------------------------------------------------------
#
# Steps are a proxy for expenditure; WHOOP's daily kilojoules are the thing
# itself. With intake logged on the other side, the deficit stops being a
# guess: in minus out, against the deficit the recomposition band implies.

#: One kilogram of fat is roughly 7,700 kcal; a week is seven days.
KCAL_PER_KG_FAT = 7_700
#: An intake below this is almost always a day where logging stopped, not a
#: day of fasting. Counting it would turn a forgotten dinner into a deficit.
INTAKE_LOGGED_FLOOR_KCAL = 1_000


@dataclass
class EnergyBalance:
    deficit_target: int                   # kcal/day, positive number
    yesterday_in: float | None
    yesterday_out: float | None
    yesterday_balance: float | None       # in - out; negative is a deficit
    week_balance: float | None            # mean over the last 7 complete days
    week_days: int
    why: str

    def as_dict(self) -> dict:
        return {"deficit_target": self.deficit_target,
                "yesterday": {"in": self.yesterday_in, "out": self.yesterday_out,
                              "balance": self.yesterday_balance},
                "week_mean_balance": self.week_balance, "week_days": self.week_days,
                "why": self.why}


def energy_balance(store: Store, day: date) -> EnergyBalance:
    """Yesterday's and the week's energy balance against the deficit the fat
    loss band asks for. Days with only one side logged are left out of the
    week rather than counted as a zero on the missing side."""
    _trend, weight = weight_trend(store, day)
    weight = weight or 90.0
    lo_pct, hi_pct = LOSS_BAND_PCT
    target = int(round(weight * (lo_pct + hi_pct) / 2 / 100 * KCAL_PER_KG_FAT / 7 / 50) * 50)

    rows = store.query(
        "SELECT local_date, kcal_in, kcal_out FROM daily "
        "WHERE local_date BETWEEN ? AND ? ORDER BY local_date",
        [day - timedelta(days=7), day - timedelta(days=1)])
    rows = [(d, i if i is not None and i >= INTAKE_LOGGED_FLOOR_KCAL else None, o)
            for d, i, o in rows]
    complete = [(d, float(i), float(o)) for d, i, o in rows if i is not None and o is not None]
    yesterday = next(((i, o) for d, i, o in rows if d == day - timedelta(days=1)), (None, None))
    y_in = float(yesterday[0]) if yesterday[0] is not None else None
    y_out = float(yesterday[1]) if yesterday[1] is not None else None
    y_bal = (y_in - y_out) if y_in is not None and y_out is not None else None
    week = mean(i - o for _, i, o in complete) if complete else None

    if y_bal is None:
        missing = "intake" if y_in is None and y_out is not None else                   "expenditure" if y_out is None and y_in is not None else "intake and expenditure"
        why = f"aim for -{target} kcal/day; yesterday's {missing} not logged"
    else:
        gap = y_bal + target                      # 0 when exactly on target
        if abs(gap) <= 150:
            why = f"yesterday {y_in:.0f} in, {y_out:.0f} out = {y_bal:+.0f}: on target"
        elif gap > 0:
            why = (f"yesterday {y_in:.0f} in, {y_out:.0f} out = {y_bal:+.0f}: "
                   f"{gap:.0f} kcal short of the -{target} target, move more or eat less")
        else:
            why = (f"yesterday {y_in:.0f} in, {y_out:.0f} out = {y_bal:+.0f}: "
                   f"{-gap:.0f} kcal past the -{target} target, eat more — this is a recomp, not a crash")
    if week is not None:
        why += f"; 7-day mean {week:+.0f} over {len(complete)} complete days"
    return EnergyBalance(target, y_in, y_out, y_bal, week, len(complete), why)


# -- the session -------------------------------------------------------------

@dataclass
class PlannedExercise:
    slot: str
    exercise: str
    template_id: str
    sets: int
    reps: int
    reps_range: tuple[int, int]
    weight_kg: float | None
    rest_seconds: int
    why: str
    cue: str = ""

    def as_dict(self) -> dict:
        return {"slot": self.slot, "exercise": self.exercise, "sets": self.sets,
                "reps": self.reps, "rep_range": list(self.reps_range),
                "weight_kg": self.weight_kg, "why": self.why, "cue": self.cue or None}


@dataclass
class SessionPlan:
    day: date
    kind: str
    title: str
    deload: bool = False
    exercises: list[PlannedExercise] = field(default_factory=list)
    cardio: Cardio | None = None
    steps: Steps | None = None
    protein: Protein | None = None
    energy: EnergyBalance | None = None
    volume: list[MuscleVolume] = field(default_factory=list)
    readiness: str | None = None
    adjustments: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    @property
    def is_rest(self) -> bool:
        return self.kind == "rest"

    def as_dict(self) -> dict:
        return {
            "date": str(self.day), "session": self.kind, "title": self.title,
            "deload": self.deload, "readiness": self.readiness,
            "adjustments": self.adjustments,
            "exercises": [e.as_dict() for e in self.exercises],
            "cardio": self.cardio.as_dict() if self.cardio else None,
            "steps": self.steps.as_dict() if self.steps else None,
            "protein": self.protein.as_dict() if self.protein else None,
            "energy": self.energy.as_dict() if self.energy else None,
            "priority_volume": [v.as_dict() for v in self.volume],
            "unmatched": self.unmatched,
        }


def plan_session(store: Store, day: date | None = None, *,
                 auto_regulate: bool = True) -> SessionPlan:
    """Today's session: the lifts, the cardio and the step target.

    With `auto_regulate`, `readiness()` decides whether loads advance at all —
    a `pull_back` call trims the session before you get to the gym, which is
    the whole point of having recovery data in the same database.
    """
    day = day or date.today()
    kind = day_type(day)
    deload = is_deload(day, training_anchor(store, day))

    title = f"AI {kind.title()}" + (" (deload)" if deload and kind != "rest" else "")
    plan = SessionPlan(day=day, kind=kind, title=title, deload=deload)

    call = None
    if auto_regulate:
        call = readiness_features.readiness(store, day)
        plan.readiness = call.recommendation

    unknown = plan.readiness == readiness_features.INSUFFICIENT_DATA
    hold = plan.readiness == readiness_features.HOLD or unknown
    back_off = plan.readiness == readiness_features.PULL_BACK
    if unknown:
        plan.adjustments.append("readiness is unknown: loads repeat until current data is available")
    elif hold:
        plan.adjustments.append("recovery says hold: loads repeat rather than advancing")
    if back_off:
        plan.adjustments.append(
            "recovery says back off: last isolation lift dropped, a set off everything "
            "else, and the intervals swapped for easy zone 2")

    plan.steps = step_target(store, day, kind)
    plan.protein = protein_target(store, day)
    plan.energy = energy_balance(store, day)
    plan.volume = priority_volume(store, day)

    if kind == "rest":
        plan.cardio = cardio_block([], "none")
        return plan

    rows = catalogue(store)
    if not rows:
        plan.unmatched.append("exercise catalogue is empty — run `health sync hevy`")
        return plan

    history = last_sessions(store, as_of=day)
    logged = logged_template_ids(store, as_of=day)
    slots = SESSIONS[kind]
    if back_off:
        slots = [s for s in slots if s.role != "isolation"] + \
                [s for s in slots if s.role == "isolation"][:-1]

    used: set[str] = set()
    for slot in slots:
        template = match(slot.candidates, rows, EQUIPMENT, logged=logged, exclude=used)
        if template is None:
            plan.unmatched.append(slot.label)
            continue
        used.add(template["template_id"])
        increment = INCREMENT_KG.get(template["equipment"], 2.5)
        target = next_target(slot, history.get(template["template_id"]), increment,
                             deload=deload, hold=hold or back_off)
        sets = max(2, target.sets - 1) if back_off else target.sets
        weight = target.weight_kg
        if back_off and weight:
            weight = _round_to(weight * 0.9, increment or 2.5)
        plan.exercises.append(PlannedExercise(
            slot=slot.label, exercise=template["title"], template_id=template["template_id"],
            sets=sets, reps=target.reps, reps_range=target.reps_range, weight_kg=weight,
            rest_seconds=REST_SECONDS.get(slot.role, 120), why=target.why, cue=slot.cue,
        ))

    cardio_kind = "hiit" if kind == HIIT_DAY else "zone2"
    if back_off:
        cardio_kind = "zone2"
    plan.cardio = cardio_block(rows, cardio_kind, deload=deload or back_off)
    if plan.cardio.template_id is None:
        plan.unmatched.append("cardio machine")
    return plan


def week_plan(store: Store, start: date | None = None) -> list[SessionPlan]:
    start = start or date.today()
    # Only today's readiness is real; the rest of the week has no recovery data
    # to read yet, so auto-regulation would be inventing it.
    return [plan_session(store, start + timedelta(days=offset),
                         auto_regulate=(offset == 0))
            for offset in range(7)]


# -- the Hevy payload --------------------------------------------------------

def routine_payload(plan: SessionPlan, folder_id: int | None = None) -> dict:
    """The body Hevy's POST/PUT /v1/routines expects."""
    exercises = []
    for item in plan.exercises:
        lo, hi = item.reps_range
        note = " | ".join(part for part in (item.why, item.cue) if part)
        exercises.append({
            "exercise_template_id": item.template_id,
            "superset_id": None,
            "rest_seconds": item.rest_seconds,
            "notes": note[:255],
            "sets": [{"type": "normal", "weight_kg": item.weight_kg, "reps": item.reps,
                      "rep_range": {"start": lo, "end": hi}} for _ in range(item.sets)],
        })

    if plan.cardio and plan.cardio.template_id:
        exercises.append({
            "exercise_template_id": plan.cardio.template_id,
            "superset_id": None,
            "rest_seconds": 0,
            "notes": plan.cardio.note[:255],
            "sets": [dict(item) for item in plan.cardio.sets],
        })

    return {"title": plan.title, "folder_id": folder_id,
            "notes": routine_notes(plan)[:255], "exercises": exercises}


def routine_notes(plan: SessionPlan) -> str:
    parts = [plan.day.isoformat()]
    parts.append("DELOAD — back off, stop well short" if plan.deload
                 else "every working set within 1-2 reps of failure")
    if plan.readiness:
        parts.append(f"readiness: {plan.readiness.replace('_', ' ')}")
    if plan.cardio and plan.cardio.kind != "none":
        parts.append(f"{plan.cardio.kind} {plan.cardio.minutes} min after lifting")
    if plan.steps:
        parts.append(f"steps {plan.steps.target:,}")
    return ". ".join(parts)
