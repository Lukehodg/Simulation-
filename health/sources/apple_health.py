"""Apple Health, via Health Auto Export.

This is the bridge that makes the whole thing work on an iPhone without a
single unofficial API: Garmin Connect writes its dailies into Apple Health,
MyFitnessPal writes your macros there, and your period logs land there from
whichever app you record them in. Health Auto Export drops that as JSON into
iCloud Drive on a schedule, and we read the folder.

It does not carry Body Battery, HRV status, training readiness or training
load — Garmin keeps those inside Connect. Those arrive with the data export
and the .FIT files instead.

One deliberate choice: readings are aggregated to one value per metric per
local day at parse time, summing what accumulates (steps, calories, macros)
and averaging what is instantaneous (heart rate, HRV). The raw file keeps the
full granularity if we ever want it back.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .. import metrics as M
from .. import raw as rawstore
from ..models import CycleEvent, NutritionDay, Observation, Records, Sleep
from ..timeutil import local_date, parse_ts, sleep_local_date
from .base import Source

SUM = "sum"
MEAN = "mean"
LAST = "last"

# Health Auto Export metric name -> (canonical metric, how to aggregate a day)
METRIC_MAP: dict[str, tuple[str, str]] = {
    "heart_rate": (M.HR_AVG, MEAN),
    "resting_heart_rate": (M.RESTING_HR, MEAN),
    "heart_rate_variability": (M.HRV_SDNN, MEAN),
    "respiratory_rate": (M.RESPIRATORY_RATE, MEAN),
    "blood_oxygen_saturation": (M.SPO2, MEAN),
    "vo2_max": (M.VO2_MAX, MEAN),
    "apple_sleeping_wrist_temperature": (M.WRIST_TEMP, MEAN),
    "step_count": (M.STEPS, SUM),
    "active_energy": (M.ACTIVE_ENERGY, SUM),
    "basal_energy_burned": (M.BASAL_ENERGY, SUM),
    "apple_exercise_time": (M.EXERCISE_MINUTES, SUM),
    "weight_body_mass": (M.BODY_MASS, LAST),
    "body_fat_percentage": (M.BODY_FAT, LAST),
    "lean_body_mass": (M.LEAN_MASS, LAST),
    "dietary_energy": (M.ENERGY_INTAKE, SUM),
    "protein": (M.PROTEIN, SUM),
    "carbohydrates": (M.CARBS, SUM),
    "total_fat": (M.FAT, SUM),
    "fiber": (M.FIBRE, SUM),
    "sodium": (M.SODIUM, SUM),
    "dietary_caffeine": (M.CAFFEINE, SUM),
    "dietary_water": (M.WATER, SUM),
    "dietary_sugar": ("sugar", SUM),
}

NUTRITION_COLUMNS = {
    M.ENERGY_INTAKE: "kcal", M.PROTEIN: "protein_g", M.CARBS: "carbs_g",
    M.FAT: "fat_g", M.FIBRE: "fibre_g", "sugar": "sugar_g",
    M.SODIUM: "sodium_mg", M.CAFFEINE: "caffeine_mg", M.WATER: "water_ml",
}

FLOW_WORDS = {0: "none", 1: "light", 2: "medium", 3: "heavy", 4: "unspecified"}


def _quantity(entry: dict) -> float | None:
    """HAE emits `qty` for simple metrics and Min/Avg/Max for sampled ones."""
    for key in ("qty", "Avg", "avg", "value"):
        value = entry.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _fraction_to_percent(metric: str, value: float) -> float:
    """Apple stores SpO2 and body fat as fractions; HAE passes that through."""
    if metric in (M.SPO2, M.BODY_FAT) and 0 < value <= 1:
        return value * 100
    return value


class AppleHealthSource(Source):
    name = "apple_health"
    pollable = False  # files arrive from the phone; there is nothing to poll

    def fetch(self, since: datetime | None = None,
              until: datetime | None = None) -> list[Path]:
        """Land any new export files from the watched folder."""
        directory = self.config.apple_export_dir
        if not directory or not directory.is_dir():
            return []
        landed: list[Path] = []
        for path in sorted(directory.glob("*.json")):
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if since and mtime <= since:
                continue
            landed.append(rawstore.copy_in(self.config.raw_dir, self.name,
                                           "export", path, fetched_at=mtime))
        return landed

    def check(self) -> tuple[bool, str]:
        directory = self.config.apple_export_dir
        if not directory:
            return False, "HEALTH_APPLE_EXPORT_DIR is not set"
        if not directory.is_dir():
            return False, f"{directory} does not exist"
        files = list(directory.glob("*.json"))
        return bool(files), f"{len(files)} export file(s) in {directory}"

    # -- parse -------------------------------------------------------------

    def parse(self, path: Path) -> Records:
        payload = json.loads(path.read_text())
        data = payload.get("data", payload)
        records = Records()

        buckets: dict[tuple[str, date], list[float]] = defaultdict(list)
        rules: dict[str, str] = {}
        stamps: dict[tuple[str, date], datetime] = {}

        for metric in data.get("metrics", []) or []:
            name = (metric.get("name") or "").lower()
            entries = metric.get("data") or []
            if name == "sleep_analysis":
                self._parse_sleep(entries, records)
                continue
            if name in ("menstrual_flow", "menstruation"):
                self._parse_cycle(entries, records)
                continue
            if name not in METRIC_MAP:
                continue
            canonical, rule = METRIC_MAP[name]
            rules[canonical] = rule
            for entry in entries:
                ts = parse_ts(entry.get("date"))
                value = _quantity(entry)
                if ts is None or value is None:
                    continue
                day = local_date(ts, self.config.timezone)
                buckets[(canonical, day)].append(_fraction_to_percent(canonical, value))
                stamps[(canonical, day)] = max(stamps.get((canonical, day), ts), ts)

        nutrition: dict[date, dict[str, float]] = defaultdict(dict)
        for (canonical, day), values in sorted(buckets.items()):
            rule = rules.get(canonical, MEAN)
            if rule == SUM:
                value = sum(values)
            elif rule == LAST:
                value = values[-1]
            else:
                value = sum(values) / len(values)
            value = round(value, 4)
            if canonical in NUTRITION_COLUMNS:
                nutrition[day][NUTRITION_COLUMNS[canonical]] = value
            if canonical == "sugar":
                continue  # carried in nutrition_days only; not a canonical metric
            records.observations.append(Observation(
                ts=stamps[(canonical, day)], local_date=day, metric=canonical,
                value=value, unit=M.unit_for(canonical),
                source=self.name, source_id=f"{canonical}:{day.isoformat()}",
            ))

        for day, columns in sorted(nutrition.items()):
            records.nutrition_days.append(
                NutritionDay(source=self.name, local_date=day, **columns)
            )
        return records

    def _parse_sleep(self, entries: Iterable[dict], records: Records) -> None:
        """HAE reports sleep stage durations in hours."""
        for entry in entries:
            start = parse_ts(entry.get("sleepStart") or entry.get("startDate")
                             or entry.get("inBedStart"))
            end = parse_ts(entry.get("sleepEnd") or entry.get("endDate")
                           or entry.get("inBedEnd"))
            if not (start and end):
                continue
            day = sleep_local_date(end, self.config.timezone)

            def hours(*keys: str) -> float | None:
                for key in keys:
                    value = entry.get(key)
                    if isinstance(value, (int, float)):
                        return round(float(value) * 60, 1)
                return None

            asleep = hours("asleep", "totalSleep")
            core, deep, rem = hours("core"), hours("deep"), hours("rem")
            if asleep is None and None not in (core, deep, rem):
                asleep = round(core + deep + rem, 1)
            in_bed = hours("inBed")
            source_id = f"{start.date().isoformat()}:{start.strftime('%H%M')}"

            records.sleeps.append(Sleep(
                source=self.name, source_id=source_id, start_ts=start, end_ts=end,
                local_date=day, duration_min=asleep, in_bed_min=in_bed,
                efficiency=round(asleep / in_bed * 100, 1) if asleep and in_bed else None,
                rem_min=rem, deep_min=deep, light_min=core, awake_min=hours("awake"),
            ))
            if asleep:
                records.observations.append(Observation(
                    ts=end, local_date=day, metric=M.SLEEP_DURATION, value=asleep,
                    unit=M.unit_for(M.SLEEP_DURATION), source=self.name,
                    source_id=f"sleep:{day.isoformat()}",
                ))

    def _parse_cycle(self, entries: Iterable[dict], records: Records) -> None:
        for entry in entries:
            ts = parse_ts(entry.get("date"))
            if ts is None:
                continue
            day = local_date(ts, self.config.timezone)
            raw_flow: Any = entry.get("value", entry.get("flow", entry.get("qty")))
            if isinstance(raw_flow, (int, float)):
                flow = FLOW_WORDS.get(int(raw_flow), "unspecified")
            else:
                flow = str(raw_flow).lower() if raw_flow else "unspecified"
            if flow == "none":
                continue  # a logged non-bleed day is not a cycle event
            records.cycle_events.append(CycleEvent(
                source=self.name, local_date=day, event="flow", flow=flow,
            ))
            if entry.get("cycle_start") or entry.get("cycleStart"):
                records.cycle_events.append(CycleEvent(
                    source=self.name, local_date=day, event="period_start", flow=flow,
                ))
