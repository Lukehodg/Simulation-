from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone

from health import briefcard
from health.models import Observation, Records

DAY = date(2026, 9, 27)


def _seed(store, metrics=("hrv_rmssd", "resting_hr", "sleep_duration", "body_mass")):
    values = {"hrv_rmssd": lambda i: 60 + 5 * math.sin(i / 3),
              "resting_hr": lambda i: 54 + (i % 3),
              "sleep_duration": lambda i: 420 + 10 * (i % 4),
              "body_mass": lambda i: 92 - 0.1 * i,
              "protein": lambda i: 150 + 5 * (i % 3)}
    obs = []
    for metric in metrics:
        for i in range(40):
            d = DAY - timedelta(days=39 - i)
            obs.append(Observation(
                source="whoop", source_id=f"{metric}-{d}", metric=metric,
                value=values[metric](i), unit="x", local_date=d,
                ts=datetime.combine(d, time(6), tzinfo=timezone.utc)))
    store.load(Records(observations=obs))


def test_the_card_is_a_png(store):
    _seed(store)
    png = briefcard.render_png(store, DAY)

    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_nothing_to_draw_is_no_card_rather_than_a_blank_one(store):
    """A notification with no data behind it goes out as text alone."""
    assert briefcard.render_png(store, DAY) is None


def test_thin_data_leaves_a_panel_out(store):
    _seed(store, metrics=("hrv_rmssd",))
    titles = [p.title for p in briefcard.panels(store, DAY)]

    assert titles == ["HRV"]


def test_hrv_is_drawn_against_the_personal_baseline(store):
    _seed(store)
    hrv = next(p for p in briefcard.panels(store, DAY) if p.title == "HRV")

    lo, hi = hrv.band
    assert lo < hi
    assert len(hrv.points) == briefcard.DAYS


def test_protein_carries_the_plans_target(store):
    _seed(store, metrics=("body_mass", "protein"))
    protein = next(p for p in briefcard.panels(store, DAY) if p.title == "Protein")

    assert protein.target and protein.target_label.startswith("target")


def test_both_themes_render(store):
    _seed(store)
    for theme in briefcard.THEMES:
        assert briefcard.render_png(store, DAY, theme=theme)
