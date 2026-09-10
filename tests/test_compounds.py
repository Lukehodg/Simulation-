from __future__ import annotations

from health import compounds as C
from health import metrics as M
from health.analytes import ANALYTES

#: markers that are neither a canonical metric nor a stored analyte, but are
#: legitimate things for a clinician to watch.
_EXTERNAL_MARKERS = {"blood_pressure", "heart_rate", "energy_availability",
                     "kidney_function"}


def _known(key: str) -> bool:
    return key in M.UNITS or key in ANALYTES or key in _EXTERNAL_MARKERS or key == "appetite"


def test_every_documented_effect_names_something_real():
    for compound in C.COMPOUNDS.values():
        for key in (*compound.raises, *compound.lowers):
            assert _known(key), f"{compound.key} references unknown key {key!r}"
        for marker, _why in compound.monitor:
            assert _known(marker), f"{compound.key} monitors unknown {marker!r}"
        for analyte in compound.pre_panel:
            assert analyte in ANALYTES, f"{compound.key} pre_panel {analyte!r} unknown"


def test_the_two_named_compounds_are_fully_specified():
    for key in ("testosterone", "retatrutide"):
        spec = C.COMPOUNDS[key]
        assert spec.raises and spec.lowers and spec.monitor and spec.pre_panel
        assert spec.note


def test_aliases_resolve_the_gym_names():
    assert C.canonical("test") == "testosterone"
    assert C.canonical("reta") == "retatrutide"
    assert C.canonical("tren") == "trenbolone"
    assert C.canonical("semaglutide") == "glp1"
    assert C.canonical("not a drug") is None


def test_expected_direction_agrees_and_abstains():
    # both raise resting HR
    assert C.expected_direction("resting_hr", ["testosterone", "retatrutide"]) == "raises"
    # testosterone lowers HDL, retatrutide is silent on it
    assert C.expected_direction("hdl", ["testosterone", "retatrutide"]) == "lowers"
    # nothing documented either way
    assert C.expected_direction("vitamin_d", ["testosterone"]) is None
