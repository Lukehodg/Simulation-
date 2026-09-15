"""Apple Health, via Health Auto Export or the native export.

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

# -- the native export ------------------------------------------------------
#
# Settings > Health > Export All Health Data gives a zip with one export.xml
# holding every sample the phone has ever stored: 700 MB is normal. It is the
# fast way to backfill years of weight, steps and macros before the daily
# JSON takes over. Sample types map onto the same names Health Auto Export
# uses, so one parse path serves both.

HK_TYPES: dict[str, str] = {
    "HKQuantityTypeIdentifierHeartRate": "heart_rate",
    "HKQuantityTypeIdentifierRestingHeartRate": "resting_heart_rate",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "heart_rate_variability",
    "HKQuantityTypeIdentifierRespiratoryRate": "respiratory_rate",
    "HKQuantityTypeIdentifierOxygenSaturation": "blood_oxygen_saturation",
    "HKQuantityTypeIdentifierVO2Max": "vo2_max",
    "HKQuantityTypeIdentifierAppleSleepingWristTemperature": "apple_sleeping_wrist_temperature",
    "HKQuantityTypeIdentifierStepCount": "step_count",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "active_energy",
    "HKQuantityTypeIdentifierBasalEnergyBurned": "basal_energy_burned",
    "HKQuantityTypeIdentifierAppleExerciseTime": "apple_exercise_time",
    "HKQuantityTypeIdentifierBodyMass": "weight_body_mass",
    "HKQuantityTypeIdentifierBodyFatPercentage": "body_fat_percentage",
    "HKQuantityTypeIdentifierLeanBodyMass": "lean_body_mass",
    "HKQuantityTypeIdentifierDietaryEnergyConsumed": "dietary_energy",
    "HKQuantityTypeIdentifierDietaryProtein": "protein",
    "HKQuantityTypeIdentifierDietaryCarbohydrates": "carbohydrates",
    "HKQuantityTypeIdentifierDietaryFatTotal": "total_fat",
    "HKQuantityTypeIdentifierDietaryFiber": "fiber",
    "HKQuantityTypeIdentifierDietarySodium": "sodium",
    "HKQuantityTypeIdentifierDietaryCaffeine": "dietary_caffeine",
    "HKQuantityTypeIdentifierDietaryWater": "dietary_water",
    "HKQuantityTypeIdentifierDietarySugar": "dietary_sugar",
}
HK_SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
HK_FLOW = "HKCategoryTypeIdentifierMenstrualFlow"
HK_FLOW_WORDS = {"HKCategoryValueMenstrualFlowLight": "light",
                 "HKCategoryValueMenstrualFlowMedium": "medium",
                 "HKCategoryValueMenstrualFlowHeavy": "heavy",
                 "HKCategoryValueMenstrualFlowUnspecified": "unspecified",
                 "HKCategoryValueMenstrualFlowNone": "none"}
HK_SLEEP_STAGES = {"HKCategoryValueSleepAnalysisAsleepCore": "core",
                   "HKCategoryValueSleepAnalysisAsleepDeep": "deep",
                   "HKCategoryValueSleepAnalysisAsleepREM": "rem",
                   "HKCategoryValueSleepAnalysisAwake": "awake",
                   "HKCategoryValueSleepAnalysisInBed": "inBed",
                   "HKCategoryValueSleepAnalysisAsleepUnspecified": "asleep"}
#: Everything lands in the units the JSON path expects.
HK_UNIT_FACTORS = {"lb": 0.45359237, "Cal": 1.0, "kJ": 0.239006, "oz": 28.3495,
                   "fl_oz_us": 29.5735, "L": 1000.0}
#: Two devices both count your steps; Apple de-duplicates in the app but the
#: export carries both. Per day, the biggest single source is the honest one.
HK_PICK_LARGEST_SOURCE = {"step_count", "active_energy", "basal_energy_burned",
                          "apple_exercise_time"}


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

    # -- the native export ---------------------------------------------------

    def add(self, path: Path, **_: object) -> Path:
        """Land a native export (`export.zip` or `export.xml`) as one JSON file
        of daily values, in the same shape the daily JSON arrives in.

        The XML is not kept: at 700 MB it would dwarf everything else in
        raw/ and the phone can always regenerate it. What lands is one row
        per metric per day — the same reduction `parse` applies to the daily
        files, just done while streaming so the whole file never sits in
        memory. Keep the zip yourself if you want the samples back.
        """
        if path.suffix.lower() == ".zip":
            import zipfile
            with zipfile.ZipFile(path) as archive:
                member = next((n for n in archive.namelist() if n.endswith("export.xml")),
                              None)
                if member is None:
                    raise ValueError(f"{path.name} has no export.xml inside it")
                with archive.open(member) as handle:
                    payload = self._reduce_xml(handle)
        elif path.suffix.lower() == ".xml":
            with path.open("rb") as handle:
                payload = self._reduce_xml(handle)
        else:
            return rawstore.copy_in(self.config.raw_dir, self.name, "export", path)
        payload["format"] = "apple_native_export"
        payload["file"] = path.name
        return rawstore.write(self.config.raw_dir, self.name, "export", payload)

    def _reduce_xml(self, handle) -> dict:
        import xml.etree.ElementTree as ET

        # (metric, day, source) -> running sum / count / last, so a day's value
        # can be picked per source afterwards without holding the samples.
        sums: dict[tuple[str, date, str], float] = defaultdict(float)
        counts: dict[tuple[str, date, str], int] = defaultdict(int)
        last: dict[tuple[str, date], tuple[datetime, float]] = {}
        stamps: dict[tuple[str, date], datetime] = {}
        sleep: dict[tuple[date, str], dict] = {}
        flows: list[dict] = []

        root = None
        seen = 0
        for event, elem in ET.iterparse(handle, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = elem
                continue
            if elem.tag != "Record":
                continue
            kind = elem.get("type", "")
            try:
                if kind in HK_TYPES:
                    self._reduce_quantity(elem, HK_TYPES[kind], sums, counts, last, stamps)
                elif kind == HK_SLEEP:
                    self._reduce_sleep(elem, sleep)
                elif kind == HK_FLOW:
                    self._reduce_flow(elem, flows)
            finally:
                # Cleared elements still hang off the root as empty children;
                # a million of them is a gigabyte. Drop them as we go.
                elem.clear()
                seen += 1
                if root is not None and seen % 50_000 == 0:
                    root.clear()
                    self.report_progress("samples", seen)

        metrics: dict[str, list[dict]] = defaultdict(list)
        by_day: dict[tuple[str, date], dict[str, float]] = defaultdict(dict)
        for (name, day, source), total in sums.items():
            by_day[(name, day)][source] = total
        for (name, day), per_source in sorted(by_day.items()):
            rule = METRIC_MAP[name][1]
            if rule == LAST:
                value = last[(name, day)][1]
            elif name in HK_PICK_LARGEST_SOURCE:
                value = max(per_source.values())
            elif rule == SUM:
                value = sum(per_source.values())
            else:
                n = sum(counts[(name, day, s)] for s in per_source)
                value = sum(per_source.values()) / n
            metrics[name].append({"date": stamps[(name, day)].strftime("%Y-%m-%d %H:%M:%S %z"),
                                  "qty": round(value, 4)})

        sleep_entries = []
        for (day, source), night in sorted(sleep.items()):
            entry = {"sleepStart": night["start"].strftime("%Y-%m-%d %H:%M:%S %z"),
                     "sleepEnd": night["end"].strftime("%Y-%m-%d %H:%M:%S %z"),
                     "source": source}
            for stage, minutes in night["stages"].items():
                entry[stage] = round(minutes / 60, 3)
            stages = night["stages"]
            if "asleep" in stages and not any(k in stages for k in ("core", "deep", "rem")):
                entry["asleep"] = round(stages["asleep"] / 60, 3)
            sleep_entries.append(entry)
        # A watch and a phone both describe the same night; keep the one with
        # more detail (stages) or, failing that, the longer one.
        best_nights: dict[str, dict] = {}
        for entry in sleep_entries:
            key = entry["sleepEnd"][:10]
            score = (sum(k in entry for k in ("core", "deep", "rem")),
                     entry.get("asleep", 0) + entry.get("inBed", 0))
            if key not in best_nights or score > best_nights[key][0]:
                best_nights[key] = (score, entry)
        if best_nights:
            metrics["sleep_analysis"] = [e for _s, e in best_nights.values()]
        if flows:
            metrics["menstrual_flow"] = flows

        return {"data": {"metrics": [{"name": name, "data": entries}
                                     for name, entries in metrics.items()]}}

    def _reduce_quantity(self, elem, name, sums, counts, last, stamps) -> None:
        raw = elem.get("value")
        end = parse_ts(elem.get("endDate") or elem.get("startDate"))
        if raw is None or end is None:
            return
        try:
            value = float(raw)
        except ValueError:
            return
        value *= HK_UNIT_FACTORS.get(elem.get("unit", ""), 1.0)
        day = local_date(end, self.config.timezone)
        source = elem.get("sourceName") or "unknown"
        key = (name, day, source)
        sums[key] += value
        counts[key] += 1
        if (name, day) not in last or end >= last[(name, day)][0]:
            last[(name, day)] = (end, value)
        stamps[(name, day)] = max(stamps.get((name, day), end), end)

    def _reduce_sleep(self, elem, sleep) -> None:
        stage = HK_SLEEP_STAGES.get(elem.get("value", ""))
        start = parse_ts(elem.get("startDate"))
        end = parse_ts(elem.get("endDate"))
        if not (stage and start and end):
            return
        day = sleep_local_date(end, self.config.timezone)
        source = elem.get("sourceName") or "unknown"
        night = sleep.setdefault((day, source), {"start": start, "end": end, "stages": {}})
        night["start"] = min(night["start"], start)
        night["end"] = max(night["end"], end)
        minutes = (end - start).total_seconds() / 60
        night["stages"][stage] = night["stages"].get(stage, 0.0) + minutes

    def _reduce_flow(self, elem, flows) -> None:
        start = parse_ts(elem.get("startDate"))
        flow = HK_FLOW_WORDS.get(elem.get("value", ""))
        if not (start and flow):
            return
        entry = {"date": start.strftime("%Y-%m-%d %H:%M:%S %z"), "value": flow}
        for meta in elem.iter("MetadataEntry"):
            if meta.get("key") == "HKMenstrualCycleStart" and meta.get("value") == "1":
                entry["cycle_start"] = True
        flows.append(entry)

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
