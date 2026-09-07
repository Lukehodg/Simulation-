from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from health.features import cycle as cyc
from health.models import CycleEvent, Observation, Records

CYCLE_LENGTH = 28
FIRST_START = date(2026, 1, 5)
FOLLICULAR_HRV = 72.0
LUTEAL_HRV = 52.0
#: Day-to-day noise, on the scale HRV actually varies by. Repeats every five
#: days and sums to zero, so each phase's mean is exactly its base value while
#: still having real spread to score against.
NOISE = [-8.0, -4.0, 0.0, 4.0, 8.0]


def _typical(day: date) -> date:
    """The next day carrying no noise, so its value is its phase's mean."""
    while NOISE[(day - FIRST_START).days % len(NOISE)] != 0.0:
        day += timedelta(days=1)
    return day


def _seed(store, cycles: int = 6, hrv: bool = True,
          lengths: list[int] | None = None) -> list[date]:
    """Regular cycles with a luteal HRV depression — the pattern that makes a
    phase-blind baseline cry wolf for a third of every month."""
    starts: list[date] = []
    day = FIRST_START
    for i in range(cycles):
        starts.append(day)
        length = lengths[i] if lengths else CYCLE_LENGTH
        day = day + timedelta(days=length)

    records = Records()
    for start in starts:
        records.cycle_events.append(CycleEvent(
            source="apple_health", local_date=start, event="period_start", flow="medium"))
        for offset in range(5):
            records.cycle_events.append(CycleEvent(
                source="apple_health", local_date=start + timedelta(days=offset),
                event="flow", flow="medium"))

    if hrv:
        last = starts[-1] + timedelta(days=CYCLE_LENGTH - 1)
        current = FIRST_START
        while current <= last:
            # In a 28-day cycle ovulation lands on day 15, so luteal starts on
            # cycle day 17 (offset 16).
            offset = min(((current - s).days for s in starts if s <= current),
                         default=0)
            base = LUTEAL_HRV if offset >= 16 else FOLLICULAR_HRV
            # Real measurements vary; a perfectly flat series has no spread to
            # score against and would be a dishonestly easy test.
            value = base + NOISE[(current - FIRST_START).days % len(NOISE)]
            records.observations.append(Observation(
                ts=datetime.combine(current, time(6), tzinfo=timezone.utc),
                local_date=current, metric="hrv_rmssd", value=value, unit="ms",
                source="whoop", source_id=f"h-{current}"))
            current += timedelta(days=1)

    store.load(records)
    cyc.rebuild(store)
    return starts


def test_cycles_are_reconstructed_from_logs(store):
    starts = _seed(store, cycles=4)
    found = cyc.cycles(store)

    assert [c.start for c in found] == starts
    assert [c.length for c in found] == [28, 28, 28, None]  # last is still running
    assert found[-1].is_current
    assert found[0].menses_end == starts[0] + timedelta(days=4)


def test_cycles_can_be_inferred_from_bare_flow_days(store):
    """Not every app writes a cycle_start flag; a gap in flow is enough."""
    records = Records()
    for start in (date(2026, 3, 2), date(2026, 3, 30)):
        for offset in range(4):
            records.cycle_events.append(CycleEvent(
                source="apple_health", local_date=start + timedelta(days=offset),
                event="flow", flow="light"))
    store.load(records)

    assert cyc.period_starts(store) == [date(2026, 3, 2), date(2026, 3, 30)]


def test_ovulation_is_counted_back_from_the_next_period(store):
    """Not forward from the last one: luteal length is the stable quantity."""
    _seed(store, cycles=3, hrv=False)
    first, second = cyc.cycles(store)[0], cyc.cycles(store)[1]

    assert first.ovulation == first.start + timedelta(days=14)   # cycle day 15

    # A short cycle moves ovulation earlier, while "day 14" would not.
    store.db.execute("DELETE FROM cycle_events")
    _seed(store, cycles=3, hrv=False, lengths=[24, 24, 24])
    short = cyc.cycles(store)[0]
    assert short.length == 24
    assert short.ovulation == short.start + timedelta(days=10)   # cycle day 11


def test_phases_land_where_expected_in_a_28_day_cycle(store):
    starts = _seed(store, cycles=3, hrv=False)
    start = starts[0]
    phases = {r[0]: r[1] for r in store.query(
        "SELECT cycle_day, phase FROM cycle_days WHERE cycle_index = 0")}

    assert phases[1] == cyc.MENSES
    assert phases[5] == cyc.MENSES
    assert phases[10] == cyc.FOLLICULAR
    assert phases[15] == cyc.OVULATION       # ovulation day itself
    assert phases[14] == cyc.OVULATION       # +/- a day, since no estimate is exact
    assert phases[20] == cyc.LUTEAL
    assert phases[28] == cyc.LUTEAL


def test_only_the_unobserved_part_of_the_running_cycle_is_marked_predicted(store):
    """Bleeding days you logged are observed. Everything after them in the
    current cycle rests on an estimate of when the next period will come."""
    starts = _seed(store, cycles=3, hrv=False)
    current = starts[-1]

    observed = store.query(
        "SELECT is_predicted FROM cycle_days WHERE local_date = ?", [current])
    later = store.query(
        "SELECT is_predicted FROM cycle_days WHERE local_date = ?",
        [current + timedelta(days=20)])

    assert observed == [(False,)]
    assert later == [(True,)]
    # Completed cycles rest on nothing but logs.
    assert store.query(
        "SELECT DISTINCT is_predicted FROM cycle_days WHERE cycle_index = 0") == [(False,)]


def test_a_normal_luteal_day_is_flagged_by_the_yearly_baseline_but_not_the_phase(store):
    """The false alarm this module exists to remove."""
    starts = _seed(store, cycles=6)
    luteal_day = _typical(starts[-1] + timedelta(days=20))

    reading = cyc.phase_adjusted(store, "hrv_rmssd", luteal_day)

    assert reading.phase == cyc.LUTEAL
    assert reading.value == LUTEAL_HRV
    assert reading.overall_z <= -1.0          # "your HRV is way down"
    assert abs(reading.phase_z) < 0.5         # ...it is exactly normal for day 21
    assert reading.verdicts_disagree
    assert "same phase" in reading.describe()


def test_a_genuinely_bad_follicular_day_is_still_flagged(store):
    """The other half: phase awareness must not sand away real signal."""
    starts = _seed(store, cycles=6)
    bad_day = _typical(starts[-1] + timedelta(days=9))
    store.db.execute(
        "UPDATE observations SET value = 40 WHERE local_date = ? AND metric = 'hrv_rmssd'",
        [bad_day])

    reading = cyc.phase_adjusted(store, "hrv_rmssd", bad_day)

    assert reading.phase == cyc.FOLLICULAR
    assert reading.phase_z < -1.0
    assert not reading.verdicts_disagree


def test_a_phase_baseline_is_withheld_until_there_is_enough_of_it(store):
    starts = _seed(store, cycles=2)
    day = starts[-1] + timedelta(days=20)
    store.db.execute(
        "DELETE FROM observations WHERE local_date < ? OR local_date > ?",
        [day - timedelta(days=2), day])

    reading = cyc.phase_adjusted(store, "hrv_rmssd", day)

    assert reading.phase == cyc.LUTEAL
    assert reading.phase_z is None
    assert "need 5" in reading.note


def test_a_first_cycle_is_not_guessed_at(store):
    """With no completed cycle there is no length to anchor ovulation to, so
    the tail of that cycle is unplaced rather than assumed."""
    _seed(store, cycles=1)
    day = cyc.cycles(store)[0].start + timedelta(days=20)

    reading = cyc.phase_adjusted(store, "hrv_rmssd", day)

    assert reading.phase == cyc.UNKNOWN
    assert reading.phase_z is None
    assert "cannot be placed" in reading.note


def test_phase_signature_reports_agreement_with_the_expected_direction(store):
    _seed(store, cycles=6)
    signature = cyc.phase_signature(store, "hrv_rmssd")

    assert signature["delta"] == pytest.approx(LUTEAL_HRV - FOLLICULAR_HRV, abs=1.5)
    assert signature["expected"] == "lower"
    assert signature["agrees"] is True


def test_an_implausible_gap_does_not_move_the_median(store):
    """A 63-day gap is a month of unlogged cycles, not a 63-day cycle."""
    _seed(store, cycles=4, hrv=False, lengths=[28, 63, 27, 29])
    found = cyc.cycles(store)

    assert [c.length for c in found] == [28, 63, 27, None]
    assert cyc.median_length(found) == 28
    assert cyc.summary(store)["implausible_lengths"] == [63]


def test_summary_predicts_the_next_period_from_the_median(store):
    starts = _seed(store, cycles=5, hrv=False)
    today = starts[-1] + timedelta(days=10)

    result = cyc.summary(store, today=today)

    assert result["median_length"] == 28
    assert result["phase"] == cyc.FOLLICULAR
    assert result["cycle_day"] == 11
    assert result["next_period_estimate"] == starts[-1] + timedelta(days=28)
    assert result["irregular"] is False


def test_summary_says_so_when_there_are_no_logs(store):
    assert cyc.summary(store)["cycles"] == 0
    assert "no period logs" in cyc.summary(store)["note"]
