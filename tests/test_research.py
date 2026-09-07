from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from health import research

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "europepmc.json").read_text())


def _client(capture: dict | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.update(dict(request.url.params))
        return httpx.Response(200, json=FIXTURE)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_query_restricts_to_title_and_abstract_and_filters_design():
    query = research.build_query("low ferritin", since_year=2019,
                                 designs=["Meta-Analysis", "Review"])

    assert 'TITLE_ABS:"low ferritin"' in query   # not a full-text match
    assert 'PUB_TYPE:"Meta-Analysis"' in query
    assert "FIRST_PDATE:[2019-01-01" in query


def test_results_are_ordered_by_study_design_not_citations():
    """The meta-analysis outranks the more-cited review, because design is the
    better guide to weight and citation count is popularity."""
    papers = research.search("ferritin", client=_client())

    assert [p.design for p in papers] == ["meta-analysis", "review"]
    assert papers[0].cited_by < papers[1].cited_by


def test_papers_carry_what_is_needed_to_check_them():
    papers = research.search("ferritin", client=_client())
    meta = papers[0]

    assert meta.year == 2023
    assert meta.journal == "BJSM"
    assert meta.open_access is True
    assert meta.url == "https://doi.org/10.1000/example.2"
    assert meta.cite() == ("Iron supplementation and performance: a meta-analysis. "
                           "BJSM. 2023.")


def test_limit_is_respected_but_over_fetches_so_the_sort_is_meaningful():
    captured: dict = {}
    papers = research.search("ferritin", limit=1, client=_client(captured))

    assert len(papers) == 1
    assert int(captured["pageSize"]) >= 25
    assert papers[0].design == "meta-analysis"


def test_a_lab_result_becomes_a_direction_specific_query():
    """Low ferritin and high ferritin are different literatures."""
    assert research.evidence_query("ferritin", "low") == "low Ferritin"
    assert research.evidence_query("vitamin_d", "low", "endurance athletes") == (
        "low Vitamin D endurance athletes")
    assert research.evidence_query("ferritin", None) == "Ferritin"
