"""Energy availability — and the version of the same question that runs today.

`energy_availability` reads `energy_intake`, `active_energy` and `lean_mass`.
All three are defined in `metrics.py`; none has a source wired up yet, so it
reports `available: False` on a real database. That is the honest state, not a
bug — the moment MyFitnessPal or Apple Health nutrition connects, it activates
with no further code.

The formula and its thresholds are Loucks & Thuma's (2003) exercising-women
work, generalised by the IOC's Relative Energy Deficiency in Sport (RED-S)
consensus statements: energy availability below ~30 kcal/kg fat-free mass/day
is where LH pulsatility and other reproductive/metabolic function start to
disrupt. This is a screening signal, not a clinical measurement — it says
when the underlying inputs are worth a closer, professional look, never a
diagnosis.

Two checks sit on top of it, and the difference between them is what data they
need:

  * `red_s_watch` is the full three-legged signature, and it needs the
    nutrition data. Its menstrual leg is now gated on there being cycle logs
    at all: reporting `insufficient data` for ever to anyone who tracks no
    cycle meant the check could never fire for them, which is worse than
    firing on the legs that do exist and saying which one is unavailable.
  * `underfuelling_watch` asks the same question from signals that are live —
    the scale, the training load, the protein log and what you are on. It
    exists because `compounds.py` points a GLP-1's `monitor` entry at
    `energy_availability`, and a marker that can never be read is not
    monitoring. It is a coarser instrument and says so.

Neither one recommends a protocol change. `protocol.py`'s docstring holds that
line and this module is no exception: the outputs here are "this pattern wants
a clinician's read", plus the two levers `health plan` already prescribes.
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

#: Acute:chronic training load above this is a ramp rather than a steady state.
LOAD_ELEVATED = 1.3


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


def _compound_caveats(store: Store, as_of: date) -> list[str]:
    """What the active compounds do to the signals these checks read.

    Not advice on the compounds — the same documented-effects-only line
    `protocol.py` draws. One of these matters more than it looks: on exogenous
    androgens the hormonal leg usually used to screen men for RED-S cannot be
    read at all, so its absence is not reassurance.
    """
    from .. import compounds as compounds_kb
    from . import protocol as protocol_features

    out: list[str] = []
    for active in protocol_features.active(store, as_of):
        spec = compounds_kb.COMPOUNDS.get(active.compound)
        if not spec:
            continue
        if spec.klass == "glp1":
            out.append(f"on {spec.label.lower()}: appetite suppression is a "
                       f"mechanism for low intake rather than a coincidence "
                       f"beside it, so a low reading here is more expected, not "
                       f"less concerning")
        if spec.klass == "androgen":
            out.append(f"on {spec.label.lower()}: the hormonal leg normally used "
                       f"to screen men — low testosterone, suppressed LH — cannot "
                       f"be read on exogenous androgens, so not seeing it is not "
                       f"reassurance")
    return out


def red_s_watch(store: Store, as_of: date | None = None) -> dict:
    """The concrete cross-domain case this feature exists for: sustained low
    energy availability, an elevated training load, and cycle disruption,
    together. Any one alone is common and usually nothing; the three together
    are the recognised RED-S signature, and the only honest output for that is
    an escalation, not a nutrition tweak.

    The cycle leg is reported, not required. With no period logs it is
    `unavailable` — which is the truth whether someone has no cycle to track or
    simply does not log one — and the check runs on the two legs that are left,
    saying plainly that the signature is less specific without the third. The
    alternative, which this replaced, was `insufficient data` for ever: a check
    that could not fire, standing in for one that could.
    """
    as_of = as_of or date.today()
    ea = energy_availability(store, as_of=as_of, days=14)
    load = daily.training_load(store, as_of=as_of)
    cycle = cycle_features.summary(store, today=as_of)
    cycle_tracked = bool(cycle.get("cycles"))

    missing = []
    if not ea.available:
        missing.append("energy availability (needs nutrition + body composition data)")
    if load.ratio is None:
        missing.append("training load")
    if missing:
        return {"flag": "insufficient data", "missing": missing,
                "cycle_leg": "tracked" if cycle_tracked else "unavailable",
                "energy_availability": ea.as_dict(),
                "instead": "`underfuelling_watch` asks the same question from the "
                           "signals that are connected today"}

    low_ea = ea.verdict == "low"
    high_load = load.ratio > LOAD_ELEVATED
    disrupted_cycle = cycle_tracked and (bool(cycle.get("irregular"))
                                         or bool(cycle.get("overdue_days")))

    legs = {"low_energy_availability": low_ea, "elevated_load": high_load,
            "cycle_disrupted": disrupted_cycle if cycle_tracked else None}
    common = {"legs": legs, "cycle_leg": "tracked" if cycle_tracked else "unavailable",
              "caveats": _compound_caveats(store, as_of),
              "energy_availability": ea.as_dict()}

    if low_ea and high_load and (disrupted_cycle or not cycle_tracked):
        note = ("low energy availability and an elevated training load are "
                "present together")
        note += (" alongside cycle disruption — the full RED-S signature, and "
                 "worth a conversation with a doctor rather than a nutrition "
                 "tweak on your own." if disrupted_cycle else
                 ". The third leg, cycle disruption, is not tracked here, so "
                 "this is two of three: less specific than the full signature "
                 "and still worth a doctor's read rather than a nutrition tweak "
                 "on your own.")
        return {"flag": "watch", "note": note, "training_load": load.describe(),
                **common}

    return {"flag": "clear",
            "note": ("the RED-S signals are not all present together right now"
                     if cycle_tracked else
                     "the two RED-S legs that can be read here are not both "
                     "present right now"),
            **common}


def underfuelling_watch(store: Store, as_of: date | None = None) -> dict:
    """The same question, from the data that exists today.

    `energy_availability` needs logged nutrition, so `red_s_watch` cannot fire
    on a database without it — which left a GLP-1's `energy_availability`
    monitor entry pointing at a marker nobody could read. This asks it from
    what is live: the scale, the training load, the protein log, and what you
    are on.

    Two legs, and it takes both.

      * **A mechanism for intake being short** — an appetite-suppressing
        compound is active, or measured energy availability is actually low.
      * **The consequence showing up on the scale** — weight coming off faster
        than the ceiling of the recomposition band `health plan` prescribes
        against.

    Either alone is ordinary: a GLP-1 is supposed to suppress appetite, and one
    fast fortnight happens to everybody. Together they are what `compounds.py`
    calls "rapid weight loss alongside high training load", and an elevated
    load or a week of short protein makes it worse rather than making it fire.

    A coarser instrument than real energy availability, and it says so in the
    output. It prescribes nothing: the two levers it names — loss rate and
    protein — are ones `health plan` already sets from bodyweight.
    """
    as_of = as_of or date.today()

    # `program` is the writer; reading its band and its weight trend here keeps
    # one definition of "too fast" instead of two that can drift apart.
    from .. import compounds as compounds_kb
    from . import program as program_features
    from . import protocol as protocol_features

    trend, weight = program_features.weight_trend(store, as_of)
    load = daily.training_load(store, as_of=as_of)
    ea = energy_availability(store, as_of=as_of, days=14)
    protein = program_features.protein_target(store, as_of)
    suppressing = [compounds_kb.COMPOUNDS[a.compound].label.lower()
                   for a in protocol_features.active(store, as_of)
                   if a.compound in compounds_kb.COMPOUNDS
                   and compounds_kb.COMPOUNDS[a.compound].klass == "glp1"]

    if trend is None or weight is None:
        return {"flag": "insufficient data",
                "missing": ["a bodyweight trend (needs about a fortnight of "
                            "weigh-ins)"],
                "caveats": _compound_caveats(store, as_of)}

    ceiling_kg = weight * program_features.LOSS_BAND_PCT[1] / 100
    losing = -trend
    too_fast = losing > ceiling_kg
    low_ea = ea.verdict == "low" if ea.available else False
    high_load = load.ratio > LOAD_ELEVATED if load.ratio is not None else False

    legs = {
        "appetite_suppressed": bool(suppressing),
        "low_energy_availability": low_ea if ea.available else None,
        "losing_faster_than_ceiling": too_fast,
        "elevated_load": high_load if load.ratio is not None else None,
        "protein_short": protein.flag == "watch",
    }
    common = {"as_of": str(as_of), "legs": legs,
              "loss_kg_per_week": round(losing, 2),
              "ceiling_kg_per_week": round(ceiling_kg, 2),
              "caveats": _compound_caveats(store, as_of),
              "coarse": "a screening pattern from the scale and the training "
                        "log, not measured energy availability — connect "
                        "nutrition data and `red_s_watch` answers this properly"}

    if not (bool(suppressing) or low_ea) or not too_fast:
        return {"flag": "clear",
                "note": (f"losing {losing:.2f} kg/wk against a {ceiling_kg:.2f} kg "
                         f"ceiling at {weight:g} kg; a mechanism for short intake "
                         f"and a scale moving faster than the band are not both "
                         f"present"),
                **common}

    why = (f"on {', '.join(suppressing)}" if suppressing
           else "with measured energy availability low")
    amplifiers = [label for label, present in
                  (("training load is elevated", high_load),
                   ("protein has been short most of the week",
                    protein.flag == "watch")) if present]
    note = (f"losing {losing:.2f} kg/wk past the {ceiling_kg:.2f} kg ceiling for "
            f"{weight:g} kg, {why}")
    if amplifiers:
        note += " — and " + ", ".join(amplifiers)
    note += (". Rapid loss on an appetite suppressant is where the weight stops "
             "being fat; the loss rate and the protein target are levers "
             "`health plan` already sets, and a run of this is worth a doctor's "
             "read. It is not a reason to change the protocol yourself.")
    return {"flag": "watch", "note": note, "training_load": load.describe(),
            **common}
