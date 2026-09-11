"""Energy availability — built now, dormant until MyFitnessPal or Apple
Health nutrition data actually lands.

Every metric this reads (`energy_intake`, `active_energy`, `lean_mass`) is
already defined in `metrics.py`; none of it has a source wired up yet, so
`energy_availability` will report `available: False` on a real database
today. That is the honest state, not a bug — the point of writing this now
is that the moment MFP/Apple Health connects, this activates with no further
code, rather than being a cold-start feature sprint later.

The formula and its thresholds are Loucks & Thuma's (2003) exercising-women
work, generalised by the IOC's Relative Energy Deficiency in Sport (RED-S)
consensus statements: energy availability below ~30 kcal/kg fat-free mass/day
is where LH pulsatility and other reproductive/metabolic function start to
disrupt. This is a screening signal, not a clinical measurement — it says
when the underlying inputs are worth a closer, professional look, never a
diagnosis.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from .. import metrics as M
from ..store import Store
from . import cycle as cycle_features
from . import daily


#: kcal per kg fat-free mass per day. See module docstring for the source.
EA_OPTIMAL = 45.0
EA_REDUCED = 30.0

MIN_USABLE_DAYS = 3


@dataclass
class EnergyAvailability:
    as_of: date
    days: int
    usable_days: int
    ea: float | None = None
    verdict: str | None = None
    note: str | None = None
    available: bool = False

    def as_dict(self) -> dict:
        return {"as_of": str(self.as_of), "days": self.days,
               "usable_days": self.usable_days, "ea_kcal_per_kg_ffm": self.ea,
               "verdict": self.verdict, "note": self.note,
               "available": self.available}


def _ffm_series(store: Store, start: date, end: date) -> dict[date, float]:
    """Fat-free mass per day: `lean_mass` directly if logged, else derived
    from `body_mass` and `body_fat_pct` on days both are present."""
    lean = dict(daily.series(store, M.LEAN_MASS, start, end))
    mass = dict(daily.series(store, M.BODY_MASS, start, end))
    fat_pct = dict(daily.series(store, M.BODY_FAT, start, end))
    out = dict(lean)
    for d, m in mass.items():
        if d not in out and d in fat_pct and m is not None and fat_pct[d] is not None:
            out[d] = m * (1 - fat_pct[d] / 100)
    return out


def energy_availability(store: Store, as_of: date | None = None,
                        days: int = 7) -> EnergyAvailability:
    """Median energy availability over the last `days` — a rolling window
    rather than a single day, since one day's intake logging is noisy and
    EA's clinical meaning is about a sustained state, not a single reading.
    """
    as_of = as_of or date.today()
    start = as_of - timedelta(days=days - 1)

    intake = dict(daily.series(store, M.ENERGY_INTAKE, start, as_of))
    exercise = dict(daily.series(store, M.ACTIVE_ENERGY, start, as_of))
    ffm = _ffm_series(store, start, as_of)

    per_day = []
    for d in (start + timedelta(days=i) for i in range(days)):
        if d in intake and d in exercise and d in ffm and ffm[d]:
            per_day.append((intake[d] - exercise[d]) / ffm[d])

    if len(per_day) < MIN_USABLE_DAYS:
        return EnergyAvailability(
            as_of=as_of, days=days, usable_days=len(per_day), available=False,
            note=("needs energy intake, active energy and either lean mass or "
                 "body-fat % logged on the same days — not connected yet "
                 "(MyFitnessPal or Apple Health for nutrition and body "
                 f"composition). Have {len(per_day)} of the {MIN_USABLE_DAYS} "
                 "usable days needed."))

    ea = round(statistics.median(per_day), 1)
    verdict = ("optimal" if ea >= EA_OPTIMAL else
              "reduced" if ea >= EA_REDUCED else "low")
    return EnergyAvailability(
        as_of=as_of, days=days, usable_days=len(per_day), ea=ea, verdict=verdict,
        available=True,
        note=(f"median of {len(per_day)} usable days — a screening signal, "
             f"not a clinical measurement; a clinician reads this together "
             f"with how you actually feel and perform"))


def red_s_watch(store: Store, as_of: date | None = None) -> dict:
    """The concrete cross-domain case this whole feature exists for: sustained
    low energy availability, an elevated training load, and cycle disruption,
    together. Any one alone is common and usually nothing; the three
    together are the recognised RED-S signature, and the only honest output
    for that is an escalation, not a nutrition tweak.
    """
    as_of = as_of or date.today()
    ea = energy_availability(store, as_of=as_of, days=14)
    load = daily.training_load(store, as_of=as_of)
    cycle = cycle_features.summary(store, today=as_of)

    missing = []
    if not ea.available:
        missing.append("energy availability (needs nutrition + body composition data)")
    if load.ratio is None:
        missing.append("training load")
    if not cycle.get("cycles"):
        missing.append("cycle logs")
    if missing:
        return {"flag": "insufficient data", "missing": missing,
               "energy_availability": ea.as_dict()}

    low_ea = ea.verdict == "low"
    high_load = load.ratio > 1.3
    disrupted_cycle = bool(cycle.get("irregular")) or bool(cycle.get("overdue_days"))

    if low_ea and high_load and disrupted_cycle:
        return {
            "flag": "watch",
            "note": ("low energy availability, an elevated training load, and "
                     "cycle disruption are all present together — that "
                     "combination is worth a conversation with a doctor, not "
                     "a nutrition tweak on your own."),
            "energy_availability": ea.as_dict(),
            "training_load": load.describe(),
            "cycle": {"irregular": cycle.get("irregular"),
                     "overdue_days": cycle.get("overdue_days")},
        }
    return {
        "flag": "clear",
        "note": "the three RED-S signals are not all present together right now",
        "legs": {"low_energy_availability": low_ea, "elevated_load": high_load,
                "cycle_disrupted": disrupted_cycle},
        "energy_availability": ea.as_dict(),
    }
