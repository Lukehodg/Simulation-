"""What you said, read against what was measured.

Two things land here that nothing else in the system can supply: a cuff blood
pressure reading, and how you actually feel. Both matter for the same reason —
the wearables infer strain and recovery from proxies, and every so often the
proxy and the person disagree. Either direction is informative: pushing
through a fatigue the watch hasn't caught yet, or a green day that feels wrong
because of something none of the sensors measure.

Blood pressure gets an escalation verdict because two of the compounds
`protocol.py` tracks are documented to raise it and there was previously no way
to know. The verdict is always "see a doctor", never a dose — see the module
docstring in `protocol.py` for why that line is held everywhere in this system.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from .. import compounds as compounds_kb
from .. import metrics as M
from ..store import Store
from . import daily
from . import protocol as protocol_features
from . import sleep as sleep_features
from . import trend as trend_features

#: higher is worse for stress; higher is better for everything else
_INVERTED = {M.STRESS}

_LABELS = {M.ENERGY: "energy", M.MOOD: "mood", M.STRESS: "stress",
          M.SLEEP_QUALITY: "sleep quality (felt)", M.LIBIDO: "libido",
          M.GI_COMFORT: "GI comfort"}

#: Home-BP escalation lines. 130/80 is where US/UK guidance starts calling it
#: raised; 135/85 is the practical "worth a clinician" line on an androgen;
#: 140/90 is the harder one.
BP_WATCH = (135.0, 85.0)
BP_ESCALATE = (140.0, 90.0)


def prompts() -> list[tuple[str, str, str]]:
    """(field, question, hint) in the order the interactive CLI asks them."""
    return [
        (M.ENERGY, "Energy today?", "1 = wiped out, 5 = full tank"),
        (M.MOOD, "Mood?", "1 = low, 5 = great"),
        (M.STRESS, "Stress?", "1 = calm, 5 = high"),
        (M.SLEEP_QUALITY, "How did last night's sleep feel?", "1 = terrible, 5 = great"),
        (M.LIBIDO, "Libido?", "1 = low, 5 = high"),
        (M.GI_COMFORT, "GI comfort?", "1 = rough, 5 = fine"),
    ]


@dataclass
class CheckIn:
    day: date
    ratings: dict[str, float]
    note: str | None
    bp_systolic: float | None
    bp_diastolic: float | None
    bp_pulse: float | None
    readings: str | None

    def as_dict(self) -> dict:
        return {"date": str(self.day),
                "ratings": {_LABELS.get(k, k): v for k, v in self.ratings.items()},
                "note": self.note,
                "blood_pressure": (f"{self.bp_systolic:g}/{self.bp_diastolic:g}"
                                   if self.bp_systolic else None),
                "pulse": self.bp_pulse, "readings": self.readings}


def latest(store: Store, day: date | None = None) -> CheckIn | None:
    day = day or date.today()
    row = store.query(
        "SELECT note, readings FROM checkins WHERE local_date = ? "
        "ORDER BY ingested_at DESC LIMIT 1", [day])
    ratings = dict(store.query(
        "SELECT metric, value FROM daily_metrics WHERE local_date = ? AND metric IN ?",
        [day, list(M.CHECKIN_RATINGS)]))
    bp = dict(store.query(
        "SELECT metric, value FROM daily_metrics WHERE local_date = ? AND metric IN ?",
        [day, [M.BP_SYSTOLIC, M.BP_DIASTOLIC, M.BP_PULSE]]))
    if not row and not ratings and not bp:
        return None
    note, readings = row[0] if row else (None, None)
    return CheckIn(day=day, ratings=ratings, note=note,
                   bp_systolic=bp.get(M.BP_SYSTOLIC), bp_diastolic=bp.get(M.BP_DIASTOLIC),
                   bp_pulse=bp.get(M.BP_PULSE), readings=readings)


def history(store: Store, days: int = 14, as_of: date | None = None) -> list[CheckIn]:
    as_of = as_of or date.today()
    days_seen = [r[0] for r in store.query(
        "SELECT DISTINCT local_date FROM checkins WHERE local_date > ? AND local_date <= ? "
        "ORDER BY local_date", [as_of - timedelta(days=days), as_of])]
    return [c for d in days_seen if (c := latest(store, d)) is not None]


def blood_pressure(store: Store, as_of: date | None = None, days: int = 7) -> dict:
    as_of = as_of or date.today()
    rows = store.query(
        "SELECT local_date, "
        "  MAX(CASE WHEN metric = ? THEN value END), "
        "  MAX(CASE WHEN metric = ? THEN value END) "
        "FROM daily_metrics WHERE metric IN ? AND local_date > ? AND local_date <= ? "
        "GROUP BY local_date ORDER BY local_date",
        [M.BP_SYSTOLIC, M.BP_DIASTOLIC, [M.BP_SYSTOLIC, M.BP_DIASTOLIC],
         as_of - timedelta(days=days), as_of])
    rows = [(d, s, di) for d, s, di in rows if s is not None and di is not None]
    if not rows:
        return {"nights": 0, "note": "no blood pressure logged yet — `health checkin --bp S/D`"}

    avg_sys = round(statistics.mean(s for _, s, _ in rows), 1)
    avg_dia = round(statistics.mean(di for _, _, di in rows), 1)
    latest_row = rows[-1]

    sys_trend = trend_features.metric_trend(store, M.BP_SYSTOLIC, as_of=as_of)
    dia_trend = trend_features.metric_trend(store, M.BP_DIASTOLIC, as_of=as_of)

    watch = avg_sys >= BP_WATCH[0] or avg_dia >= BP_WATCH[1]
    escalate = avg_sys >= BP_ESCALATE[0] or avg_dia >= BP_ESCALATE[1]
    rising = sys_trend.verdict == "rising"
    verdict = "see a doctor" if escalate else "watch" if (watch or rising) else "ok"

    on_androgen = any(compounds_kb.COMPOUNDS[a.compound].klass == "androgen"
                      for a in protocol_features.active(store, as_of)
                      if a.compound in compounds_kb.COMPOUNDS)
    note = None
    if verdict != "ok":
        note = (f"{days}-day average {avg_sys:g}/{avg_dia:g}"
               + (f", and systolic is trending up over six weeks" if rising else "")
               + (". Testosterone and other androgens raise blood pressure — a "
                  "sustained reading like this is a clinician's call, not "
                  "something to adjust the protocol over yourself."
                  if on_androgen else ". Worth a clinician's read."))

    return {
        "as_of": str(as_of), "days": days, "readings": len(rows),
        "average_systolic": avg_sys, "average_diastolic": avg_dia,
        "latest": {"date": str(latest_row[0]), "systolic": latest_row[1],
                   "diastolic": latest_row[2]},
        "six_week_trend": {"systolic": sys_trend.describe(),
                           "diastolic": dia_trend.describe()},
        "verdict": verdict, "note": note,
    }


def _computed_state(store: Store, day: date) -> dict:
    """The objective picture for the day, in the shape subjective_vs_objective compares to."""
    from . import readiness as readiness_features

    rec = readiness_features.readiness(store, day)
    hrv = daily.baseline(store, M.HRV_RMSSD, as_of=day)
    hrv_val = store.query(
        "SELECT value FROM daily_metrics WHERE metric = ? AND local_date = ?",
        [M.HRV_RMSSD, day])
    hrv_z = hrv.z(hrv_val[0][0]) if hrv_val and hrv_val[0][0] is not None else None
    return {"recommendation": rec.recommendation, "hrv_z": hrv_z,
           "sleep_debt_hours": rec.sleep_debt_hours}


def subjective_vs_objective(store: Store, day: date | None = None) -> dict:
    """Each rating, next to what the computed state says, and where they agree."""
    day = day or date.today()
    entry = latest(store, day)
    if not entry or not entry.ratings:
        return {"date": str(day), "note": "no check-in today — `health checkin`"}

    computed = _computed_state(store, day)
    rows = []

    for field in (M.ENERGY, M.MOOD, M.STRESS):
        value = entry.ratings.get(field)
        if value is None:
            continue
        felt_good = value <= 2 if field in _INVERTED else value >= 4
        felt_bad = value >= 4 if field in _INVERTED else value <= 2
        data_good = computed["recommendation"] in ("push", "proceed") and \
            (computed["hrv_z"] is None or computed["hrv_z"] >= -0.5)
        data_bad = computed["recommendation"] == "pull_back" or \
            (computed["hrv_z"] is not None and computed["hrv_z"] <= -1.0)
        if felt_bad and data_good:
            agreement = "you feel worse than the data"
        elif felt_good and data_bad:
            agreement = "you feel better than the data"
        else:
            agreement = "match"
        rows.append({
            "dimension": _LABELS[field], "you_said": value,
            "data_says": f"readiness {computed['recommendation']}"
                        + (f", HRV {computed['hrv_z']:+.1f} SD" if computed['hrv_z'] is not None else ""),
            "agreement": agreement,
        })

    sq = entry.ratings.get(M.SLEEP_QUALITY)
    if sq is not None:
        quality = sleep_features.sleep_quality(store, as_of=day)
        flags = quality.get("flags", [])
        felt_good, felt_bad = sq >= 4, sq <= 2
        data_bad = bool(flags)
        agreement = ("you feel worse than the data" if felt_bad and not data_bad else
                     "you feel better than the data" if felt_good and data_bad else "match")
        rows.append({"dimension": _LABELS[M.SLEEP_QUALITY], "you_said": sq,
                    "data_says": ("flagged: " + ", ".join(flags)) if flags
                                 else "nothing flagged in the architecture",
                    "agreement": agreement})

    active = protocol_features.active(store, day)
    for field in (M.LIBIDO, M.GI_COMFORT):
        value = entry.ratings.get(field)
        if value is None or value > 2:
            continue  # only worth a note when it's actually low
        context = protocol_features.context(store, field, day, z=-1.5)
        if context:
            rows.append({"dimension": _LABELS[field], "you_said": value,
                        "data_says": "; ".join(context), "agreement": "context"})

    divergent = [r for r in rows if r["agreement"] != "match" and r["agreement"] != "context"]
    summary = ("how you feel and the numbers line up" if not divergent else
              "; ".join(f"{r['dimension']}: {r['agreement']}" for r in divergent))

    return {"date": str(day), "rows": rows, "summary": summary,
           "note": entry.note, "active_compounds": [a.label for a in active]}


def divergence_history(store: Store, days: int = 60,
                       as_of: date | None = None) -> dict:
    """Does how you feel lead or lag the numbers?

    Correlates `energy` against next-day and same-day HRV, so the lag itself is
    the finding — a lag of 1 means your own read tends to catch up to the data
    a day late.
    """
    as_of = as_of or date.today()
    same_day = daily.correlate(store, M.ENERGY, M.HRV_RMSSD, lag_days=0,
                               start=as_of - timedelta(days=days), end=as_of)
    energy_leads_hrv = daily.correlate(store, M.ENERGY, M.HRV_RMSSD, lag_days=1,
                                       start=as_of - timedelta(days=days), end=as_of)
    hrv_leads_energy = daily.correlate(store, M.HRV_RMSSD, M.ENERGY, lag_days=1,
                                       start=as_of - timedelta(days=days), end=as_of)
    return {
        "window_days": days,
        "energy_vs_hrv_same_day": same_day.describe(),
        "yesterdays_energy_vs_todays_hrv": energy_leads_hrv.describe(),
        "yesterdays_hrv_vs_todays_energy": hrv_leads_energy.describe(),
        "note": "each is a hypothesis with its own interval, not a settled lead/lag",
    }
