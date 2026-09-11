from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from health.brief import SYSTEM
from health.features import experiment
from health.models import (ExperimentAdherence, ExperimentEvent, Observation,
                           Records)
from health.sources.experiment import ExperimentSource

START = date(2026, 1, 5)


def _start(store, *, id="exp1", exposure_type="manual", exposure_metric=None,
          exposure_threshold=None, outcome="hrv_rmssd", direction="lowers",
          block_days=7, blocks=6, starting="A", start_date=START):
    store.load(Records(experiment_events=[ExperimentEvent(
        source="experiment", local_date=start_date, event="start",
        experiment_id=id, hypothesis="test hypothesis", exposure_type=exposure_type,
        exposure_metric=exposure_metric, exposure_threshold=exposure_threshold,
        outcome_metric=outcome, predicted_direction=direction, block_days=block_days,
        blocks_planned=blocks, start_date=start_date, starting_condition=starting)]))
    return experiment.load(store, id)


def _seed_outcome(store, exp, values_by_condition: dict[str, float]) -> None:
    for block in experiment.block_windows(exp):
        val = values_by_condition[block.condition]
        day = block.start
        while day <= block.end:
            store.load(Records(observations=[Observation(
                ts=datetime.combine(day, time(6), tzinfo=timezone.utc), local_date=day,
                metric=exp.outcome_metric, value=val, unit="x", source="whoop",
                source_id=f"{exp.outcome_metric}-{day}")]))
            day += timedelta(days=1)


# -- the source: validation -------------------------------------------

class _Cfg:
    def __init__(self, tmp_path):
        self.raw_dir = tmp_path / "raw"


def test_start_rejects_too_few_blocks(tmp_path):
    source = ExperimentSource(_Cfg(tmp_path))
    with pytest.raises(ValueError, match="at least 4"):
        source.start({"hypothesis": "x", "exposure_type": "manual",
                     "outcome_metric": "hrv_rmssd", "predicted_direction": "lowers",
                     "block_days": 14, "blocks_planned": 2,
                     "starting_condition": "A", "start_date": str(START)})


def test_start_rejects_an_unknown_outcome_metric(tmp_path):
    source = ExperimentSource(_Cfg(tmp_path))
    with pytest.raises(ValueError, match="not a metric"):
        source.start({"hypothesis": "x", "exposure_type": "manual",
                     "outcome_metric": "made_up_metric", "predicted_direction": "lowers",
                     "block_days": 14, "blocks_planned": 6,
                     "starting_condition": "A", "start_date": str(START)})


def test_start_requires_a_threshold_for_metric_exposure(tmp_path):
    source = ExperimentSource(_Cfg(tmp_path))
    with pytest.raises(ValueError, match="exposure-threshold"):
        source.start({"hypothesis": "x", "exposure_type": "metric_threshold",
                     "exposure_metric": "caffeine", "outcome_metric": "hrv_rmssd",
                     "predicted_direction": "lowers", "block_days": 14,
                     "blocks_planned": 6, "starting_condition": "A",
                     "start_date": str(START)})


def test_start_refuses_a_duplicate_id(tmp_path):
    source = ExperimentSource(_Cfg(tmp_path))
    spec = {"hypothesis": "x", "experiment_id": "dupe", "exposure_type": "manual",
           "outcome_metric": "hrv_rmssd", "predicted_direction": "lowers",
           "block_days": 14, "blocks_planned": 6, "starting_condition": "A",
           "start_date": str(START)}
    source.start(spec)
    with pytest.raises(ValueError, match="already exists"):
        source.start(spec)


# -- schedule -----------------------------------------------------------

def test_blocks_alternate_from_the_starting_condition(store):
    exp = _start(store, starting="A", blocks=4, block_days=7)
    windows = experiment.block_windows(exp)
    assert [w.condition for w in windows] == ["A", "B", "A", "B"]
    assert windows[0].start == START
    assert windows[0].end == START + timedelta(days=6)
    assert windows[1].start == START + timedelta(days=7)


def test_current_block_finds_the_day(store):
    exp = _start(store, blocks=4, block_days=7)
    block = experiment.current_block(exp, as_of=START + timedelta(days=8))
    assert block.index == 1 and block.condition == "B"
    assert experiment.current_block(exp, as_of=START - timedelta(days=1)) is None


def test_status_transitions_running_to_complete(store):
    exp = _start(store, blocks=4, block_days=7)
    last_day = experiment.block_windows(exp)[-1].end
    assert experiment.status(exp, as_of=last_day) == "running"
    assert experiment.status(exp, as_of=last_day + timedelta(days=1)) == "complete"


def test_a_stopped_experiment_reports_stopped_even_mid_block(store):
    exp = _start(store, id="stoppable", blocks=6, block_days=14)
    store.load(Records(experiment_events=[ExperimentEvent(
        source="experiment", local_date=START + timedelta(days=3), event="stop",
        experiment_id="stoppable", reason="changed my mind")]))
    exp = experiment.load(store, "stoppable")
    assert exp.stopped and exp.stop_reason == "changed my mind"
    assert experiment.status(exp, as_of=START + timedelta(days=3)) == "stopped"


# -- adherence ------------------------------------------------------------

def test_manual_adherence_defaults_true_and_respects_a_logged_miss(store):
    exp = _start(store, id="manual1", exposure_type="manual")
    assert experiment.adherence(store, exp, START, "A") is True

    store.load(Records(experiment_adherence=[ExperimentAdherence(
        source="experiment", experiment_id="manual1", local_date=START, adhered=False)]))
    assert experiment.adherence(store, exp, START, "A") is False


def test_metric_threshold_adherence_reads_the_metric_directly(store):
    exp = _start(store, id="thresh1", exposure_type="metric_threshold",
                exposure_metric="caffeine", exposure_threshold=100.0, starting="B")
    store.load(Records(observations=[Observation(
        ts=datetime.combine(START, time(9), tzinfo=timezone.utc), local_date=START,
        metric="caffeine", value=150.0, unit="mg", source="checkin", source_id="c1")]))
    # block 0 is B (exposed): 150mg >= 100 threshold => matches "exposed"
    assert experiment.adherence(store, exp, START, "B") is True
    assert experiment.adherence(store, exp, START, "A") is False
    # no reading at all -> unknown
    assert experiment.adherence(store, exp, START + timedelta(days=1), "B") is None


# -- block results & the permutation test ---------------------------------

def test_block_results_only_include_elapsed_blocks(store):
    exp = _start(store, blocks=4, block_days=7)
    _seed_outcome(store, exp, {"A": 70.0, "B": 60.0})
    as_of = experiment.block_windows(exp)[1].end  # only blocks 0-1 have elapsed
    blocks = experiment.block_results(store, exp, as_of=as_of)
    assert len(blocks) == 2


def test_a_thin_block_is_flagged_not_dropped(store):
    exp = _start(store, id="thin1", blocks=4, block_days=14)
    windows = experiment.block_windows(exp)
    # only 2 of 14 days get a reading in block 0
    for i in range(2):
        day = windows[0].start + timedelta(days=i)
        store.load(Records(observations=[Observation(
            ts=datetime.combine(day, time(6), tzinfo=timezone.utc), local_date=day,
            metric="hrv_rmssd", value=65.0, unit="ms", source="whoop",
            source_id=f"h-{day}")]))
    blocks = experiment.block_results(store, exp, as_of=windows[0].end + timedelta(days=1))
    assert blocks[0].thin is True
    assert blocks[0].outcome_median == 65.0   # reported, not dropped


def test_exact_permutation_p_matches_hand_enumeration(store):
    # 4 values, 2v2: control=[10,10], exposed=[0,0] -> perfectly separated,
    # predicted "lowers" -> should hit the floor p = 1/C(4,2) = 1/6
    result = experiment._exact_permutation_p(
        {experiment.CONTROL: [10.0, 10.0], experiment.EXPOSED: [0.0, 0.0]},
        predicted_direction="lowers")
    assert result["n_permutations"] == 6
    assert result["p_floor"] == pytest.approx(1 / 6, abs=1e-4)
    assert result["p"] == pytest.approx(1 / 6, abs=1e-4)


def test_analyse_says_too_early_before_two_blocks_each_side(store):
    exp = _start(store, id="early1", blocks=6, block_days=7)
    _seed_outcome(store, exp, {"A": 70.0, "B": 60.0})
    # only the first two blocks (A, B) have elapsed -> 1 each, not enough
    as_of = experiment.block_windows(exp)[1].end + timedelta(days=1)
    result = experiment.analyse(store, exp, as_of=as_of)
    assert result["verdict"] == "too early"


def test_analyse_reports_an_effect_with_a_clean_signal(store):
    exp = _start(store, id="clean1", blocks=6, block_days=7)
    _seed_outcome(store, exp, {"A": 70.0, "B": 60.0})
    as_of = experiment.block_windows(exp)[-1].end + timedelta(days=1)

    result = experiment.analyse(store, exp, as_of=as_of)

    assert result["verdict"] == "an effect in the predicted direction"
    assert result["test"]["p"] == result["test"]["p_floor"]
    assert "smallest p" in result["note"]


def test_analyse_flags_stopping_before_the_plan_completed(store):
    exp = _start(store, id="early-stop", blocks=6, block_days=7)
    _seed_outcome(store, exp, {"A": 70.0, "B": 60.0})
    store.load(Records(experiment_events=[ExperimentEvent(
        source="experiment", local_date=START + timedelta(days=30), event="stop",
        experiment_id="early-stop", reason="enough for now")]))
    exp = experiment.load(store, "early-stop")
    as_of = experiment.block_windows(exp)[-1].end + timedelta(days=1)

    result = experiment.analyse(store, exp, as_of=as_of)
    assert result["stopped_early"] is True


# -- the SYSTEM prompt never overclaims ------------------------------------

def test_system_prompt_never_claims_significance_or_proof():
    assert "EXPERIMENTS." in SYSTEM
    assert "p-value's floor" in SYSTEM
    assert "Never call a result" in SYSTEM
    assert '"significant" or' in SYSTEM
    assert "not a finding about anyone else" in SYSTEM
