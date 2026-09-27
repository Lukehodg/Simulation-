"""How you feel, your blood pressure, and the morning weigh-in, entered by hand.

There is no API for either. `health checkin` builds one day's entry and this
lands it in raw/ exactly like a blood panel or a protocol event, so
`health replay` rebuilds the whole history from what was actually typed.

Blood pressure is 2-3 cuff readings averaged, because a single reading is
noisy and averaging is standard home-BP practice. The average is what becomes
`bp_systolic` / `bp_diastolic` / `bp_pulse`; the readings behind it are kept in
`checkins.readings` so the average is always reproducible. Ratings are 1-5;
`stress` is the one where higher is worse, everything else higher is better.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

from .. import metrics as M
from .. import raw as rawstore
from ..models import CheckIn, Observation, Records
from ..timeutil import parse_ts
from .base import Source

RATING_RANGE = range(1, 6)


class CheckInSource(Source):
    name = "checkin"
    pollable = False
    windowed_backfill = False

    def fetch(self, since: datetime | None = None,
             until: datetime | None = None) -> list[Path]:
        return []  # entered by hand with `health checkin`

    def add(self, entry: dict) -> Path:
        """Validate and land one day's check-in."""
        if not entry.get("date"):
            raise ValueError("a check-in needs a date (YYYY-MM-DD)")
        for field in M.CHECKIN_RATINGS:
            value = entry.get(field)
            if value is not None and int(value) not in RATING_RANGE:
                raise ValueError(f"{field} must be 1-5, got {value!r}")
        readings = entry.get("bp_readings") or []
        for reading in readings:
            sys_, dia_ = reading[0], reading[1]
            if not (60 <= sys_ <= 260 and 30 <= dia_ <= 200):
                raise ValueError(f"{sys_}/{dia_} doesn't look like a blood "
                                 f"pressure reading")
        weight = entry.get("weight_kg")
        if weight is not None and not (30 <= float(weight) <= 300):
            raise ValueError(f"{weight!r} kg doesn't look like a bodyweight")
        fat = entry.get("body_fat_pct")
        if fat is not None and not (2 <= float(fat) <= 70):
            raise ValueError(f"{fat!r}% doesn't look like a body-fat reading")
        ffm = entry.get("fat_free_mass_kg")
        if ffm is not None and not (20 <= float(ffm) <= 150):
            raise ValueError(f"{ffm!r} kg doesn't look like a fat-free mass")
        if ffm is not None and weight is not None and float(ffm) >= float(weight):
            raise ValueError("fat-free mass has to be less than bodyweight")
        body = fat is not None or ffm is not None
        if (not readings and weight is None and not body and not entry.get("note")
                and not any(entry.get(f) is not None for f in M.CHECKIN_RATINGS)):
            raise ValueError("nothing to log — give a reading, a weight, a rating, or a note")
        return rawstore.write(self.config.raw_dir, self.name, "entry", entry)

    def parse(self, path: Path) -> Records:
        entry = json.loads(path.read_text())
        drawn = parse_ts(entry.get("date"))
        if drawn is None:
            return Records()
        day = drawn.date()
        ts = datetime.combine(day, datetime.min.time().replace(hour=8))

        records = Records()

        def observe(metric: str, value: float | None) -> None:
            if value is None:
                return
            records.observations.append(Observation(
                ts=ts, local_date=day, metric=metric, value=float(value),
                unit=M.unit_for(metric) or "", source=self.name, source_id=str(day)))

        for field in M.CHECKIN_RATINGS:
            observe(field, entry.get(field))
        observe(M.BODY_MASS, entry.get("weight_kg"))
        observe(M.BODY_FAT, entry.get("body_fat_pct"))
        # Fat-free mass is what energy availability divides by. Scales also
        # report "soft lean" and "muscle" mass, which leave out bone mineral
        # and are smaller; only the fat-free figure belongs here.
        observe(M.LEAN_MASS, entry.get("fat_free_mass_kg"))

        readings = entry.get("bp_readings") or []
        if readings:
            observe(M.BP_SYSTOLIC, statistics.mean(r[0] for r in readings))
            observe(M.BP_DIASTOLIC, statistics.mean(r[1] for r in readings))
            pulses = [r[2] for r in readings if len(r) > 2 and r[2] is not None]
            if pulses:
                observe(M.BP_PULSE, statistics.mean(pulses))

        note = entry.get("note")
        if note or readings:
            records.checkins.append(CheckIn(
                source=self.name, local_date=day, note=note,
                readings=",".join(f"{s:g}/{d:g}" for s, d, *_ in readings) or None))
        return records

    def check(self) -> tuple[bool, str]:
        entries = list(rawstore.iter_raw(self.config.raw_dir, self.name, "entry"))
        return True, f"{len(entries)} check-in(s)"
