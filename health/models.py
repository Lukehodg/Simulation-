"""Canonical records.

Every parser returns a `Records` bundle of these; the store knows how to write
them and nothing else. Parsers are pure functions from a raw payload to this —
which is what makes `health replay` possible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Observation(_Base):
    ts: datetime
    local_date: date
    metric: str
    value: float
    unit: str | None = None
    source: str
    source_id: str


class Sleep(_Base):
    source: str
    source_id: str
    start_ts: datetime
    end_ts: datetime
    local_date: date
    duration_min: float | None = None
    in_bed_min: float | None = None
    efficiency: float | None = None
    rem_min: float | None = None
    deep_min: float | None = None
    light_min: float | None = None
    awake_min: float | None = None
    is_nap: bool | None = None
    hr_min: float | None = None
    hrv: float | None = None
    respiratory_rate: float | None = None


class Workout(_Base):
    source: str
    source_id: str
    start_ts: datetime
    end_ts: datetime | None = None
    local_date: date
    type: str | None = None
    title: str | None = None
    duration_min: float | None = None
    distance_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    kcal: float | None = None
    strain: float | None = None
    rpe: float | None = None


class StrengthSet(_Base):
    source: str
    workout_id: str
    exercise_idx: int
    set_idx: int
    ts: datetime
    local_date: date
    exercise: str
    exercise_id: str | None = None
    set_type: str | None = None
    weight_kg: float | None = None
    reps: int | None = None
    distance_m: float | None = None
    duration_s: float | None = None
    rpe: float | None = None


class ExerciseTemplate(_Base):
    source: str
    template_id: str
    title: str | None = None
    primary_muscle: str | None = None
    secondary_muscles: str | None = None
    equipment: str | None = None
    is_custom: bool | None = None


class NutritionDay(_Base):
    source: str
    local_date: date
    kcal: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fibre_g: float | None = None
    sugar_g: float | None = None
    sodium_mg: float | None = None
    caffeine_mg: float | None = None
    water_ml: float | None = None


class NutritionItem(_Base):
    source: str
    local_date: date
    meal: str
    item_idx: int
    food: str | None = None
    quantity: str | None = None
    kcal: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None


class LabResult(_Base):
    source: str
    panel_id: str
    analyte: str
    local_date: date
    value: float | None = None
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    ref_source: str | None = None
    flag: str | None = None
    converted: bool | None = None
    lab: str | None = None
    fasting: bool | None = None
    note: str | None = None
    raw_name: str | None = None
    raw_value: str | None = None
    raw_unit: str | None = None


class CycleEvent(_Base):
    source: str
    local_date: date
    event: str
    flow: str | None = None
    value: float | None = None


@dataclass
class Records:
    """Whatever one raw payload turned out to contain."""

    observations: list[Observation] = field(default_factory=list)
    sleeps: list[Sleep] = field(default_factory=list)
    workouts: list[Workout] = field(default_factory=list)
    strength_sets: list[StrengthSet] = field(default_factory=list)
    exercise_templates: list[ExerciseTemplate] = field(default_factory=list)
    nutrition_days: list[NutritionDay] = field(default_factory=list)
    nutrition_items: list[NutritionItem] = field(default_factory=list)
    cycle_events: list[CycleEvent] = field(default_factory=list)
    lab_results: list[LabResult] = field(default_factory=list)
    #: Workouts the source says were deleted upstream. Parsers report them;
    #: the store applies them, so that a session you deleted in the app on
    #: Tuesday doesn't linger in your training load forever.
    deleted_workouts: list[str] = field(default_factory=list)

    def extend(self, other: "Records") -> "Records":
        for name in self.__dataclass_fields__:
            getattr(self, name).extend(getattr(other, name))
        return self

    def total(self) -> int:
        return sum(len(getattr(self, name)) for name in self.__dataclass_fields__)

    def counts(self) -> dict[str, int]:
        return {
            name: len(getattr(self, name))
            for name in self.__dataclass_fields__
            if getattr(self, name)
        }

    def __bool__(self) -> bool:
        return self.total() > 0
