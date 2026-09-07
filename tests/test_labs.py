from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from health import analytes as A
from health.features import cycle as cyc
from health.features import labs as lab_features
from health.models import CycleEvent, Records
from health.sources.labs import LabsSource

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def labs(config):
    config.ensure_dirs()
    return LabsSource(config)


@pytest.fixture
def panel(labs):
    return labs.add(FIXTURES / "panel.json")


def _by_analyte(records):
    return {r.analyte: r for r in records.lab_results}


def test_lab_spellings_are_canonicalised(labs, panel):
    results = _by_analyte(labs.parse(panel))
    assert "vitamin_d" in results      # "25-Hydroxyvitamin D"
    assert "ldl" in results            # "LDL Cholesterol"
    assert "haemoglobin" in results


def test_units_are_converted_into_one_canonical_unit(labs, panel):
    results = _by_analyte(labs.parse(panel))

    assert results["vitamin_d"].value == pytest.approx(74.9, abs=0.1)  # 30 ng/mL
    assert results["vitamin_d"].unit == "nmol/L"
    assert results["ldl"].value == pytest.approx(3.0, abs=0.01)        # 116 mg/dL
    assert results["haemoglobin"].value == pytest.approx(124.0)        # 12.4 g/dL


def test_hba1c_percent_uses_the_affine_conversion(labs, panel):
    """(% - 2.15) * 10.929, not a multiplication — the classic lab-maths bug."""
    results = _by_analyte(labs.parse(panel))
    assert results["hba1c"].value == pytest.approx(35.5, abs=0.1)   # 5.4%
    assert results["hba1c"].unit == "mmol/mol"
    assert results["hba1c"].flag == "normal"


def test_a_lab_range_is_converted_along_with_its_value(labs, panel):
    """Converting one and not the other is how a normal result becomes a scare."""
    results = _by_analyte(labs.parse(panel))
    haemoglobin = results["haemoglobin"]

    assert haemoglobin.ref_low == pytest.approx(120.0)   # 12.0 g/dL
    assert haemoglobin.ref_high == pytest.approx(150.0)
    assert haemoglobin.flag == "normal"


def test_the_labs_own_range_beats_the_generic_one(labs, panel):
    results = _by_analyte(labs.parse(panel))

    assert results["ferritin"].ref_source == "lab"
    assert results["ferritin"].ref_low == 13          # not our generic 15
    assert results["ferritin"].flag == "low"
    assert results["vitamin_d"].ref_source == "generic"


def test_censored_values_are_read_as_numbers(labs, panel):
    results = _by_analyte(labs.parse(panel))
    assert results["crp"].value == 0.3
    assert results["crp"].raw_value == "<0.3"


def test_unknown_analytes_are_reported_not_silently_dropped(labs, panel):
    assert labs.unknown_analytes(panel) == ["Klingon Particle Index"]
    assert "klingon" not in str(_by_analyte(labs.parse(panel)).keys()).lower()


def test_an_unrecognised_unit_withholds_the_flag(labs, config):
    path = config.root / "odd.json"
    path.write_text(json.dumps({"date": "2026-08-14", "results": [
        {"analyte": "Ferritin", "value": 40, "unit": "furlongs/fortnight"}]}))
    results = _by_analyte(labs.parse(labs.add(path)))

    assert results["ferritin"].converted is False
    assert results["ferritin"].flag == "unknown"


def test_a_panel_without_a_date_is_refused(labs, config):
    path = config.root / "undated.json"
    path.write_text(json.dumps({"results": [{"analyte": "Ferritin", "value": 40}]}))

    with pytest.raises(ValueError, match="no date"):
        labs.add(path)


def test_csv_panels_work_too(labs, config):
    path = config.root / "panel.csv"
    path.write_text("analyte,value,unit,ref_low,ref_high\n"
                    "Ferritin,55,ug/L,13,150\nTSH,1.8,mIU/L,0.27,4.2\n")

    results = _by_analyte(labs.parse(labs.add(path, date="2026-09-01", lab="GP")))

    assert results["ferritin"].value == 55
    assert results["ferritin"].lab == "GP"
    assert results["tsh"].local_date == date(2026, 9, 1)


def test_flags_and_notes_reach_the_store(labs, panel, store):
    store.load(labs.parse(panel))

    flagged = lab_features.flagged(store)
    assert [v.analyte for v in flagged] == ["ferritin"]
    assert flagged[0].flag == "low"
    assert "acute-phase reactant" in flagged[0].note
    assert flagged[0].range_text == "13-150"

    vitamin_d = next(v for v in lab_features.latest_panel(store)
                     if v.analyte == "vitamin_d")
    assert vitamin_d.range_text.endswith("(generic)")


def test_a_draw_is_annotated_with_the_cycle_phase_it_was_taken_in(labs, panel, store):
    records = Records()
    for start in (date(2026, 7, 20), date(2026, 8, 17)):
        records.cycle_events.append(CycleEvent(
            source="apple_health", local_date=start, event="period_start", flow="medium"))
        for offset in range(4):
            records.cycle_events.append(CycleEvent(
                source="apple_health", local_date=start + timedelta(days=offset),
                event="flow", flow="light"))
    store.load(records)
    store.load(labs.parse(panel))
    cyc.rebuild(store)

    oestradiol = next(v for v in lab_features.latest_panel(store)
                      if v.analyte == "oestradiol")
    assert oestradiol.phase == cyc.LUTEAL      # 14 Aug, cycle day 26 of 29
    assert oestradiol.cycle_day == 26


def test_cycle_sensitive_analytes_are_not_trended_across_phases(labs, config, store):
    """Two oestradiol draws in different phases are two different questions."""
    records = Records()
    for start in (date(2026, 7, 20), date(2026, 8, 17)):
        records.cycle_events.append(CycleEvent(
            source="apple_health", local_date=start, event="period_start", flow="medium"))
        for offset in range(4):
            records.cycle_events.append(CycleEvent(
                source="apple_health", local_date=start + timedelta(days=offset),
                event="flow", flow="light"))
    store.load(records)

    labs_source = LabsSource(config)
    for day, value in (("2026-07-27", 180), ("2026-08-14", 620)):
        path = config.root / f"p-{day}.json"
        path.write_text(json.dumps({"date": day, "lab": "X", "results": [
            {"analyte": "Oestradiol", "value": value, "unit": "pmol/L"},
            {"analyte": "TSH", "value": 2.0, "unit": "mIU/L"}]}))
        store.load(labs_source.parse(labs_source.add(path)))
    cyc.rebuild(store)

    oestradiol = lab_features.trend(store, "oestradiol")
    assert oestradiol.comparable is False
    assert "same phase" in oestradiol.note
    assert oestradiol.change is None

    # A cycle-insensitive analyte still trends normally.
    tsh = lab_features.trend(store, "tsh")
    assert tsh.comparable is True
    assert tsh.change == 0.0

    grouped = lab_features.by_phase(store, "oestradiol")
    assert set(grouped) == {"follicular", "luteal"}


# -- PDF reports ------------------------------------------------------------

REPORT_TEXT = (FIXTURES / "report_text.txt").read_text()


@pytest.fixture
def report(config, labs):
    """Land an extracted report the way `health labs add report.pdf` does."""
    from health import raw as rawstore
    return rawstore.write(config.raw_dir, "labs", "panel",
                          {"format": "pdf_text", "file": "report.pdf",
                           "text": REPORT_TEXT})


def test_report_header_gives_date_lab_and_sex():
    from health.sources.labs import parse_report_text

    parsed = parse_report_text(REPORT_TEXT)
    assert parsed["date"] == "2026-08-17"      # collection date, not receipt
    assert parsed["lab"] == "Randox Health"
    assert parsed["sex"] == "Male"


def test_page_furniture_is_not_mistaken_for_results(labs, report):
    results = _by_analyte(labs.parse(report))
    assert set(results) == {
        "haemoglobin", "mchc", "cholesterol", "hdl", "egfr", "ggt", "ferritin",
        "testosterone",
    }
    assert labs.unknown_analytes(report) == []


def test_two_column_ranges_are_read_as_given(labs, report):
    results = _by_analyte(labs.parse(report))
    assert (results["haemoglobin"].ref_low, results["haemoglobin"].ref_high) == (130.0, 180.0)
    assert results["haemoglobin"].ref_source == "lab"
    assert results["haemoglobin"].flag == "normal"


def test_a_single_bound_is_placed_by_the_labs_own_marker(labs, report):
    """1.55 beside an HDL marked L is a floor, not a ceiling — and reading it
    the other way would report a low HDL as perfectly normal."""
    results = _by_analyte(labs.parse(report))
    hdl = results["hdl"]

    assert (hdl.ref_low, hdl.ref_high) == (1.55, None)
    assert hdl.flag == "low"


def test_a_single_bound_is_otherwise_placed_by_the_analytes_direction(labs, report):
    results = _by_analyte(labs.parse(report))

    # Cholesterol only has a ceiling...
    assert (results["cholesterol"].ref_low, results["cholesterol"].ref_high) == (None, 5.0)
    assert results["cholesterol"].flag == "normal"
    # ...and eGFR only has a floor, from the same shaped line.
    assert (results["egfr"].ref_low, results["egfr"].ref_high) == (60.0, None)
    assert results["egfr"].flag == "normal"


def test_the_labs_flag_wins_over_ours(labs, report):
    results = _by_analyte(labs.parse(report))
    assert results["ggt"].flag == "high"
    assert results["mchc"].flag == "low"


def test_biological_sex_selects_the_generic_range(labs, report):
    """240 ug/L of ferritin is unremarkable on a male range and flagged high on
    a female one, so the panel's own statement of sex has to drive it."""
    ferritin = _by_analyte(labs.parse(report))["ferritin"]

    assert ferritin.ref_source == "generic"
    assert (ferritin.ref_low, ferritin.ref_high) == (30, 400)
    assert ferritin.flag == "normal"


def test_reparsing_the_landed_text_picks_up_parser_improvements(labs, report):
    """raw/ holds the report text, not our reading of it, so `health replay`
    re-reads the original rather than needing the PDF again."""
    import json
    payload = json.loads(report.read_text())

    assert payload["format"] == "pdf_text"
    assert "Gamma-Glutamyltransferase" in payload["text"]
    assert len(labs.parse(report).lab_results) == 8
