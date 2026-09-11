"""Literature lookup, via Europe PMC.

Open, keyless, and it indexes MEDLINE plus preprints with publication types and
citation counts attached — which is what lets us hand back *what kind* of study
each result is rather than a flat list of titles.

This module retrieves and structures evidence. It does not interpret it, rank
it by quality, or draw conclusions from it: a citation count is popularity, a
publication type is a design, and neither is a verdict. The agent reading these
results is expected to say what the papers found and how good they are, with
the reference attached, so that you can check it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .store import Store

import httpx

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
USER_AGENT = "personal-health-agent/0.1 (local, single user)"

#: Study designs, strongest evidence first. Used to label and order results,
#: never to decide what a paper means.
DESIGN_RANK = {
    "meta-analysis": 0,
    "systematic review": 1,
    "randomized controlled trial": 2,
    "clinical trial": 3,
    "review": 4,
    "journal article": 5,
}


@dataclass
class Paper:
    title: str
    journal: str | None = None
    year: int | None = None
    authors: str | None = None
    doi: str | None = None
    pmid: str | None = None
    designs: tuple[str, ...] = ()
    cited_by: int = 0
    open_access: bool = False
    abstract: str | None = None

    @property
    def design(self) -> str:
        """The strongest design this paper is tagged with."""
        if not self.designs:
            return "journal article"
        return min(self.designs, key=lambda d: DESIGN_RANK.get(d, 99))

    @property
    def url(self) -> str | None:
        if self.doi:
            return f"https://doi.org/{self.doi}"
        if self.pmid:
            return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"
        return None

    def cite(self) -> str:
        bits = [self.title.rstrip(".")]
        if self.journal:
            bits.append(self.journal)
        if self.year:
            bits.append(str(self.year))
        return ". ".join(bits) + "."


def build_query(terms: str, since_year: int | None = None,
                designs: Iterable[str] | None = None) -> str:
    """Europe PMC query string.

    Restricted to titles and abstracts, because a full-text match returns every
    paper that merely cited something about ferritin.
    """
    parts = [f'(TITLE_ABS:"{terms}")' if " " in terms else f"(TITLE_ABS:{terms})"]
    if designs:
        clause = " OR ".join(f'PUB_TYPE:"{d}"' for d in designs)
        parts.append(f"({clause})")
    if since_year:
        parts.append(f"(FIRST_PDATE:[{since_year}-01-01 TO {date.today().year}-12-31])")
    parts.append("(SRC:MED OR SRC:PMC OR SRC:PPR)")
    return " AND ".join(parts)


def _paper(result: dict) -> Paper:
    designs = tuple(
        d.lower() for d in
        (result.get("pubTypeList", {}) or {}).get("pubType", []) or []
    )
    year = result.get("pubYear")
    return Paper(
        title=(result.get("title") or "").strip(),
        journal=result.get("journalTitle") or None,
        year=int(year) if str(year).isdigit() else None,
        authors=result.get("authorString") or None,
        doi=result.get("doi") or None,
        pmid=result.get("pmid") or None,
        designs=designs,
        cited_by=int(result.get("citedByCount") or 0),
        open_access=(result.get("isOpenAccess") == "Y"),
        abstract=(result.get("abstractText") or None),
    )


def search(terms: str, limit: int = 8, since_year: int | None = None,
           designs: Iterable[str] | None = None,
           client: httpx.Client | None = None) -> list[Paper]:
    """Search Europe PMC. Returns papers ordered by study design, then citations."""
    owns_client = client is None
    client = client or httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT})
    try:
        response = client.get(API, params={
            "query": build_query(terms, since_year=since_year, designs=designs),
            "format": "json",
            "pageSize": max(limit, 25),   # over-fetch so the sort has something to do
            "resultType": "core",
            "sort": "CITED desc",
        })
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            client.close()

    papers = [_paper(r) for r in
              (payload.get("resultList", {}) or {}).get("result", [])]
    papers.sort(key=lambda p: (DESIGN_RANK.get(p.design, 99), -p.cited_by))
    return papers[:limit]


def evidence_query(analyte: str, direction: str | None = None,
                   context: str | None = None) -> str:
    """Turn a lab result into something worth searching for.

    The direction matters: the literature on low ferritin and the literature on
    high ferritin are different literatures.
    """
    from .analytes import ANALYTES

    label = ANALYTES[analyte].label if analyte in ANALYTES else analyte
    label = label.split(" (")[0]
    parts = [f"{direction} {label}" if direction in ("low", "high") else label]
    if context:
        parts.append(context)
    return " ".join(parts)


#: Metric names read badly as search terms on their own; a few are worth
#: spelling out. Anything absent just has its underscores swapped for spaces.
_METRIC_LABELS = {
    "hrv_rmssd": "heart rate variability", "resting_hr": "resting heart rate",
    "sleep_duration": "sleep duration", "sleep_efficiency": "sleep efficiency",
    "recovery_score": "recovery", "strain": "training strain",
    "skin_temp_deviation": "skin temperature", "respiratory_rate": "respiratory rate",
    "steps": "daily steps", "energy_intake": "calorie intake", "protein": "protein intake",
    "bp_systolic": "systolic blood pressure", "bp_diastolic": "diastolic blood pressure",
    "energy": "energy levels", "sleep_quality": "sleep quality",
    "last_workout_hour": "exercise timing", "strength_tonnage": "resistance training volume",
    "body_mass": "body weight",
}


def _metric_label(metric: str) -> str:
    return _METRIC_LABELS.get(metric, metric.replace("_", " "))


def weekly_pattern_query(store: "Store", end: date | None = None) -> dict | None:
    """The week's most notable pattern, turned into something worth searching
    for — the same idea `evidence_query` applies to a lab result, for whatever
    the training and recovery data turned up instead.

    Checked in order of evidentiary weight, strongest first: a completed
    pre-registered experiment beats a bare correlation, and a compound
    monitoring marker actually moving beats either, because it is the one with
    a safety dimension. Returns `None` on a quiet week — there is no
    obligation to manufacture a pattern that is not there.
    """
    from . import compounds as compounds_kb
    from .features import experiment as experiment_features
    from .features import protocol as protocol_features
    from .features import readiness as readiness_features
    from .features import trend as trend_features

    end = end or date.today()
    start = end - timedelta(days=6)

    for exp in experiment_features.all_experiments(store):
        blocks = experiment_features.block_windows(exp)
        finished_this_week = any(start <= b.end <= end for b in blocks)
        just_completed = (experiment_features.status(exp, as_of=end) == "complete"
                          and blocks[-1].end <= end)
        if not (finished_this_week or just_completed):
            continue
        result = experiment_features.analyse(store, exp, as_of=end)
        if result.get("verdict") and result["verdict"] != "too early":
            exposure_label = _metric_label(exp.exposure_metric or "the exposure")
            outcome_label = _metric_label(exp.outcome_metric)
            return {"pattern": f'your experiment "{exp.hypothesis}" — {result["verdict"]}',
                   "query": f"{exposure_label} {outcome_label}", "source": "experiment"}

    monitoring = [m for m in protocol_features.monitoring(store, end)
                 if m.get("current_trend")]
    if monitoring:
        marker = monitoring[0]
        spec = compounds_kb.COMPOUNDS.get(marker["compound"])
        compound_label = spec.label if spec else marker["compound"]
        return {"pattern": f"{marker['marker']} is {marker['current_trend']} while "
                           f"on {compound_label.lower()}",
               "query": f"{compound_label} {_metric_label(marker['marker'])}",
               "source": "protocol"}

    drivers = readiness_features.recovery_drivers(store, end=end)
    survivors = drivers.get("survivors", [])
    if survivors:
        top = max(survivors, key=lambda f: abs(f["r"]))
        input_label = _metric_label(top["input"])
        target_label = _metric_label(top["recovery_metric"])
        return {"pattern": f"{input_label} correlates with {target_label} "
                           f"(r={top['r']:+.2f})",
               "query": f"{input_label} {target_label}", "source": "recovery_driver"}

    trends = trend_features.trends(store, as_of=end)
    if trends:
        top = trends[0]
        return {"pattern": top.describe(), "query": _metric_label(top.metric),
               "source": "trend"}

    return None


def weekly_research(store: "Store", end: date | None = None) -> dict | None:
    """The week's one piece of outside evidence, if there is a pattern worth
    it — `weekly_pattern_query` plus the literature call, in one place so the
    brief and the live dashboard read off the same result rather than each
    keeping their own copy.

    A search failure is reported, not raised — a bad week for Europe PMC
    should not take the pattern itself down with it.
    """
    pattern = weekly_pattern_query(store, end=end)
    if pattern is None:
        return None
    try:
        papers = search(pattern["query"], limit=4, since_year=2015,
                        designs=["Meta-Analysis", "Systematic Review",
                                "Randomized Controlled Trial", "Review"])
    except Exception as exc:  # noqa: BLE001 - reported, not fatal to the caller
        return {**pattern, "papers": [], "error": f"{type(exc).__name__}: {exc}"}
    return {**pattern, "papers": [
        {"title": p.title, "design": p.design, "journal": p.journal, "year": p.year,
         "cited_by": p.cited_by, "url": p.url, "abstract": (p.abstract or "")[:1200]}
        for p in papers]}
