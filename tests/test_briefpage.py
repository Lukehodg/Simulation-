from __future__ import annotations

from health.brief import Brief
from health.briefpage import render


def _daily_payload(**over):
    p = {
        "date": "2026-09-10",
        "readiness": {
            "recommendation": "pull_back",
            "advice": "back off today",
            "vitals": [
                {"metric": "hrv_rmssd", "label": "HRV", "value": 24.6,
                 "z": -1.8, "baseline": "28-day", "n": 28, "verdict": "adverse"},
                {"metric": "resting_hr", "label": "resting HR", "value": 69.0,
                 "z": 2.0, "baseline": "28-day", "n": 28, "verdict": "adverse"},
            ],
            "reasons": ["HRV 24.6, -1.8 SD"],
            "caveats": ["no recovery score for 2026-09-10"],
            "phase_context": None,
            "no_score": "not a number",
        },
        "sleep_debt": {"need_hours": 7.9, "debt_hours": 8.2, "last_night_hours": 5.4,
                       "nights": 14, "basis": "your own good nights", "note": None},
        "training_load": "steady",
        "training_observations": [],
    }
    p.update(over)
    return p


def test_the_recommendation_sets_the_headline_and_its_colour():
    page = render(Brief(text="", span="today", payload=_daily_payload()))

    assert '<span class="call bad">pull back</span>' in page
    assert "back off today" in page


def test_vitals_render_with_their_deviation_and_sample_size():
    page = render(Brief(text="", span="today", payload=_daily_payload()))

    assert "resting HR" in page
    assert "+2.0 SD · n28" in page
    assert 'class="bar"' in page          # a deviation bar was drawn


def test_prose_is_escaped_and_paragraphed():
    payload = _daily_payload()
    text = "First para with <script>alert(1)</script> and **bold**.\n\nSecond para."
    page = render(Brief(text=text, span="today", payload=payload))

    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
    assert "<strong>bold</strong>" in page
    assert page.count("<p>") >= 2


def test_the_page_is_self_contained():
    page = render(Brief(text="hi", span="today", payload=_daily_payload()))

    assert "<style>" in page and 'href="/style.css"' not in page
    assert page.strip().startswith("<!doctype html>")


def test_weekly_adds_the_cross_domain_sections():
    payload = {
        "week": "2026-09-04 to 2026-09-10",
        "readiness_today": {"recommendation": "hold", "advice": "moderate",
                            "vitals": [], "reasons": [], "caveats": []},
        "sleep_debt": {"need_hours": 7.9, "debt_hours": 3.0, "last_night_hours": 7.0,
                       "nights": 7, "basis": "default"},
        "daily_series": {"hrv_rmssd": [{"date": "d", "value": 30 + i} for i in range(7)]},
        "recovery_drivers": {"comparisons_made": 8, "survivors": [
            {"input": "strain", "recovery_metric": "hrv_rmssd", "lag_days": 1,
             "r": -0.4, "reading": "r=-0.40 holds up"}], "caveat": "8 pairs tested"},
        "strength_vs_recovery": {"exercises": [], "session_lows": {"total": 0,
                                 "on_a_red_or_under_slept_day": 0}},
    }
    page = render(Brief(text="week", span="week", payload=payload))

    assert "The week" in page and "<svg" in page          # sparkline
    assert "What moved recovery" in page
    assert "strain" in page and "8 pairs tested" in page


def test_protocol_banner_and_context_render():
    payload = _daily_payload()
    payload["readiness"]["on"] = [
        {"compound": "retatrutide", "label": "Retatrutide", "weekly_dose": 2,
         "unit": "mg", "weeks_on": 6.0, "class": "glp1", "settled": True}]
    payload["readiness"]["context"] = [
        "on retatrutide (week 6), which raises resting heart rate ~2–5 bpm"]
    payload["protocol"] = {"on": payload["readiness"]["on"], "monitoring": []}

    page = render(Brief(text="", span="today", payload=payload))

    assert 'class="banner"' in page
    assert "Retatrutide" in page and "2mg/wk" in page
    assert "Context</h3>" in page
    assert "raises resting heart rate" in page


def test_illness_watch_flag_renders_an_alert():
    payload = _daily_payload()
    payload["illness_watch"] = {"flag": "watch",
                                "note": "resting HR, skin temp and respiration "
                                        "are up and rising together"}
    page = render(Brief(text="", span="today", payload=payload))
    assert 'class="alert"' in page
    assert "rising together" in page


def test_six_week_drift_section_renders():
    payload = _daily_payload()
    payload["six_week_trends"] = [
        {"metric": "hrv_rmssd", "verdict": "falling",
         "summary": "hrv_rmssd down 0.9/week over 42 days — a real downward drift"}]
    page = render(Brief(text="", span="today", payload=payload))
    assert "Six-week drift" in page
    assert "downward drift" in page
