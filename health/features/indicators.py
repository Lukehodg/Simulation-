"""Domain indicators, and deliberately no single health score.

A composite score would need weights across incommensurable things — how many
milliseconds of HRV is one unit of GGT worth? — and there is no non-arbitrary
answer, no outcome it has been validated against, and no way to trace why it
moved. What it reliably produces is a number people optimise instead of the
thing it stands for.

So this returns several indicators, each one interpretable on its own, each
carrying the quality of evidence behind it and the data it was computed from.
The only figure here that aggregates anything is `completeness`, which measures
how much of the relevant data actually exists — a question with a real answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .. import metrics as M
from ..store import Store
from . import daily, labs as lab_features

#: How much weight the evidence behind an indicator can bear.
STRONG = "strong"        # clinical reference ranges, established physiology
MODERATE = "moderate"    # consistent evidence, individual variation matters
WEAK = "weak"            # plausible, contested, or n-of-1 only

STATUS_OK = "ok"
STATUS_WATCH = "watch"
STATUS_ATTENTION = "attention"
STATUS_UNKNOWN = "unknown"


@dataclass
class Indicator:
    name: str
    status: str
    evidence: str
    detail: str
    basis: list[str] = field(default_factory=list)
    n: int = 0

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "evidence": self.evidence,
                "detail": self.detail, "basis": self.basis, "n": self.n}


def _latest(store: Store, metric: str) -> tuple[date, float] | None:
    rows = store.query(
        "SELECT local_date, value FROM daily_metrics WHERE metric = ? "
        "ORDER BY local_date DESC LIMIT 1", [metric])
    return (rows[0][0], rows[0][1]) if rows else None


def blood_indicators(store: Store) -> list[Indicator]:
    """One indicator per clinical group, from the reference ranges themselves.

    This is the strongest evidence in the system: an interval from your own
    laboratory, for your own assay. Nothing here is inferred.
    """
    values = lab_features.latest_panel(store)
    if not values:
        return []

    groups = {
        "Metabolic": ("hba1c", "glucose", "insulin"),
        "Lipids": ("cholesterol", "ldl", "hdl", "triglycerides", "chol_hdl_ratio"),
        "Liver": ("alt", "ast", "ggt", "alp", "bilirubin", "albumin"),
        "Kidney": ("creatinine", "egfr", "urea"),
        "Iron & blood": ("ferritin", "haemoglobin", "haematocrit", "iron",
                         "transferrin_saturation", "mchc", "mcv", "mch"),
        "Thyroid": ("tsh", "free_t4", "free_t3"),
        "Hormones": ("testosterone", "free_testosterone", "shbg", "oestradiol",
                     "prolactin", "lh", "fsh"),
        "Vitamins": ("vitamin_d", "b12", "folate"),
        "Inflammation": ("crp",),
    }

    out = []
    for name, analytes in groups.items():
        present = [v for v in values if v.analyte in analytes]
        if not present:
            continue
        flagged = [v for v in present if v.flag in ("low", "high")]
        generic = any(v.ref_source == "generic" for v in flagged)
        if not flagged:
            status, detail = STATUS_OK, f"all {len(present)} inside range"
        else:
            status = STATUS_ATTENTION
            detail = ", ".join(
                f"{v.label} {v.value:.4g} {v.unit} ({v.flag}, range {v.range_text})"
                for v in flagged)
        out.append(Indicator(
            name=name, status=status,
            evidence=MODERATE if generic else STRONG,
            detail=detail,
            basis=[v.analyte for v in present], n=len(present),
        ))
    return out


def recovery_indicator(store: Store, as_of: date | None = None) -> Indicator:
    """HRV and resting heart rate against their own recent baselines."""
    as_of = as_of or date.today()
    parts, statuses, n = [], [], 0

    for metric, label, bad_direction in (
        (M.HRV_RMSSD, "HRV", -1), (M.RESTING_HR, "resting HR", 1),
    ):
        latest = _latest(store, metric)
        if not latest:
            continue
        day, value = latest
        base = daily.baseline(store, metric, as_of=day)
        n = max(n, base.n)
        z = base.z(value)
        if z is None:
            parts.append(f"{label} {value:.4g} (no baseline yet)")
            statuses.append(STATUS_UNKNOWN)
            continue
        parts.append(f"{label} {value:.4g}, {z:+.1f} SD")
        drift = z * bad_direction
        statuses.append(STATUS_WATCH if drift >= 1.5 else STATUS_OK)

    if not parts:
        return Indicator("Recovery", STATUS_UNKNOWN, STRONG,
                         "no HRV or resting heart rate yet")
    status = (STATUS_WATCH if STATUS_WATCH in statuses
              else STATUS_UNKNOWN if all(s == STATUS_UNKNOWN for s in statuses)
              else STATUS_OK)
    return Indicator("Recovery", status, STRONG, "; ".join(parts),
                     basis=[M.HRV_RMSSD, M.RESTING_HR], n=n)


def training_indicator(store: Store, as_of: date | None = None) -> Indicator:
    """Acute against chronic load.

    Evidence is deliberately marked moderate: the acute:chronic ratio is widely
    used and weakly evidenced as an injury predictor. It is a useful description
    of your own ramp rate, which is all it is treated as here.
    """
    as_of = as_of or date.today()
    load = daily.training_load(store, as_of=as_of)
    if load.ratio is None:
        return Indicator("Training load", STATUS_UNKNOWN, MODERATE,
                         "no training data yet")
    status = STATUS_WATCH if (load.ratio > 1.5 or load.ratio < 0.6) else STATUS_OK
    return Indicator("Training load", status, MODERATE, load.describe(),
                     basis=["strain", "strength_volume"], n=28)


def sleep_indicator(store: Store, as_of: date | None = None) -> Indicator:
    """Duration and, more predictive, regularity."""
    as_of = as_of or date.today()
    stats = daily.sleep_regularity(store, as_of=as_of)
    if stats.get("note"):
        return Indicator("Sleep", STATUS_UNKNOWN, STRONG, stats["note"])
    duration = stats["mean_duration_hours"]
    onset_sd = stats["onset_sd_hours"]
    status = STATUS_WATCH if (duration < 7 or onset_sd > 1.5) else STATUS_OK
    return Indicator(
        "Sleep", status, STRONG,
        f"{duration:.1f} h average over {stats['nights']} nights, "
        f"bedtime varying ±{onset_sd:.1f} h",
        basis=["sleeps"], n=stats["nights"])


def completeness(store: Store, as_of: date | None = None) -> dict:
    """How much of the relevant data actually exists.

    The one honest score here: it measures the system's coverage, not your
    health, and every point of it is checkable.
    """
    as_of = as_of or date.today()
    since = as_of - timedelta(days=28)
    checks = {
        "recovery (HRV, resting HR)": [M.HRV_RMSSD, M.RESTING_HR],
        "sleep": [M.SLEEP_DURATION],
        "activity": [M.STEPS],
        "nutrition": [M.ENERGY_INTAKE, M.PROTEIN],
        "body composition": [M.BODY_MASS],
    }
    have: dict[str, int] = {}
    for label, wanted in checks.items():
        rows = store.query(
            "SELECT COUNT(DISTINCT local_date) FROM daily_metrics "
            "WHERE metric IN ? AND local_date > ? AND local_date <= ?",
            [wanted, since, as_of])
        have[label] = rows[0][0] if rows else 0

    strength = store.query(
        "SELECT COUNT(DISTINCT local_date) FROM working_sets "
        "WHERE local_date > ? AND local_date <= ?", [since, as_of])
    have["strength training"] = strength[0][0] if strength else 0

    panels = lab_features.panels(store)
    covered = sum(1 for days in have.values() if days > 0) + (1 if panels else 0)
    total = len(have) + 1

    return {
        "days_covered": have,
        "window_days": 28,
        "sources_present": covered,
        "sources_possible": total,
        "percent": round(100 * covered / total),
        "bloods": len(panels),
        "missing": [label for label, days in have.items() if days == 0]
                   + ([] if panels else ["blood tests"]),
    }


def all_indicators(store: Store, as_of: date | None = None) -> dict:
    """Every indicator, and explicitly no total.

    `no_total` is not a joke: it is there so that anything consuming this —
    including a language model — is told the omission is deliberate rather than
    an oversight to be helpfully filled in.
    """
    as_of = as_of or date.today()
    indicators = [recovery_indicator(store, as_of), sleep_indicator(store, as_of),
                  training_indicator(store, as_of), *blood_indicators(store)]
    return {
        "as_of": str(as_of),
        "indicators": [i.as_dict() for i in indicators],
        "completeness": completeness(store, as_of),
        "no_total": "These are not combined into one score. Weighting a liver "
                    "enzyme against a heart-rate variability reading would be "
                    "arbitrary, and the result would not mean anything.",
    }
