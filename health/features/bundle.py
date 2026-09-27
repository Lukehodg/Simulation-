"""Everything the phone needs, in one JSON document.

The dashboard this feeds cannot reach the database — it is a page on someone
else's server, and the database is a file on this machine. So the machine
pushes: one document, written after each sync, holding today's session, the
week's recovery, and the figures the brief would have said out loud. The page
renders that and nothing else, which also means it cannot be wrong about a
number the database never sent.

The reverse direction is requests: a weigh-in typed on the phone, a session
asked for. They come back as rows the CLI reads and applies, so the phone
never writes to the database either.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..store import Store
from . import program, readiness as readiness_features

#: Two weeks is enough to see a trend on a phone screen without scrolling.
WINDOW_DAYS = 14


def _rows(store: Store, day: date) -> list[dict]:
    rows = store.query(
        "SELECT local_date, recovery, hrv, resting_hr, sleep_min, strain, "
        "       steps, kcal_in, kcal_out, protein_g, weight_kg "
        "FROM daily WHERE local_date BETWEEN ? AND ? ORDER BY local_date",
        [day - timedelta(days=WINDOW_DAYS - 1), day])
    out = []
    for d, rec, hrv, rhr, sleep_min, strain, steps, kin, kout, protein, weight in rows:
        out.append({
            "date": str(d),
            "recovery": round(rec) if rec is not None else None,
            "hrv": round(hrv, 1) if hrv is not None else None,
            "resting_hr": round(rhr) if rhr is not None else None,
            "sleep_hours": round(sleep_min / 60, 1) if sleep_min is not None else None,
            "strain": round(strain, 1) if strain is not None else None,
            "steps": round(steps) if steps is not None else None,
            "kcal_in": round(kin) if kin is not None else None,
            "kcal_out": round(kout) if kout is not None else None,
            "protein_g": round(protein) if protein is not None else None,
            "weight_kg": round(weight, 1) if weight is not None else None,
        })
    return out


def _recent_sessions(store: Store, day: date, limit: int = 4) -> list[dict]:
    workouts = store.query(
        "SELECT local_date, ANY_VALUE(title) FROM workouts "
        "WHERE source = 'hevy' AND local_date <= ? GROUP BY local_date "
        "ORDER BY local_date DESC LIMIT ?", [day, limit])
    out = []
    for d, title in workouts:
        lifts = store.query(
            "SELECT exercise, MAX(weight_kg), COUNT(*) FROM working_sets "
            "WHERE local_date = ? GROUP BY exercise ORDER BY MAX(weight_kg) DESC", [d])
        out.append({
            "date": str(d),
            "title": title,
            "lifts": [{"exercise": e, "top_kg": w, "sets": n} for e, w, n in lifts],
        })
    return out


def build(store: Store, day: date | None = None) -> dict:
    """The document the dashboard reads. Every figure comes from a feature
    that already computes it; nothing is derived here."""
    day = day or date.today()
    plan = program.plan_session(store, day)

    try:
        call = readiness_features.readiness(store, day)
        recovery = {
            "call": call.recommendation,
            "advice": call.as_dict().get("advice"),
            "signals": [{"label": s.label, "value": round(s.value, 1), "z": round(s.z, 2)}
                        for s in call.signals],
            "reasons": call.reasons,
            "sleep_debt_hours": round(call.sleep_debt_hours, 1)
            if call.sleep_debt_hours is not None else None,
            "load_summary": call.load_summary,
        }
    except Exception as exc:  # noqa: BLE001 — the page says so rather than breaking
        recovery = {"call": None, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": str(day),
        "plan": plan.as_dict(),
        "recovery": recovery,
        "days": _rows(store, day),
        "recent_sessions": _recent_sessions(store, day),
    }


# -- requests coming back from the phone ---------------------------------------

def apply_request(store: Store, config, request: dict) -> str:
    """Apply one request typed on the phone. Returns what happened, for the
    reply the page shows. Unknown actions are reported, never guessed at."""
    action = (request.get("action") or "").strip().lower()

    if action == "weigh":
        kg = request.get("weight_kg")
        if not isinstance(kg, (int, float)) or not (30 <= float(kg) <= 300):
            return f"ignored: {kg!r} is not a bodyweight in kg"
        from ..sources.checkin import CheckInSource

        source = CheckInSource(config)
        landed = source.add({"date": request.get("date") or str(date.today()),
                             "weight_kg": float(kg)})
        with store.transaction():
            store.load(source.parse(landed))
            store.record_raw(landed, "checkin", "entry", datetime.now(timezone.utc),
                             parsed=True)
        return f"logged {float(kg):g} kg"

    if action == "note":
        text = (request.get("text") or "").strip()
        if not text:
            return "ignored: an empty note"
        from ..sources.checkin import CheckInSource

        source = CheckInSource(config)
        landed = source.add({"date": str(date.today()), "note": text[:500]})
        with store.transaction():
            store.load(source.parse(landed))
            store.record_raw(landed, "checkin", "entry", datetime.now(timezone.utc),
                             parsed=True)
        return "noted"

    return f"ignored: unknown action {action!r}"
