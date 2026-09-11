"""Pre-registering an n-of-1 trial.

Everything a hypothesis needs to be tested honestly has to exist *before* the
data does: the metric, the direction predicted, and the exact block schedule.
`start()` writes all of that in one immutable event, and nothing later can
reshape it — the only other event a trial can ever get is `stop`. This is what
makes `features/experiment.py`'s analysis worth trusting: the comparison it
runs at the end is the comparison that was promised at the start.

Two kinds of payload land here — `start`/`stop` events, and `log` entries
recording a day's adherence to a `manual` exposure — distinguished by their
own `kind` field on the way in, and by shape on the way back out through
`parse()`.
"""

from __future__ import annotations

import json
from datetime import date as date_cls, datetime
from pathlib import Path

from .. import metrics as M
from .. import raw as rawstore
from ..models import ExperimentAdherence, ExperimentEvent, Records
from ..timeutil import parse_ts
from .base import Source

EVENTS = ("start", "stop")
EXPOSURE_TYPES = ("manual", "metric_threshold")
DIRECTIONS = ("raises", "lowers")
MIN_BLOCKS = 4
MIN_BLOCK_DAYS = 7


def slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:40] or "experiment"


class ExperimentSource(Source):
    name = "experiment"
    pollable = False
    windowed_backfill = False

    def fetch(self, since: datetime | None = None,
             until: datetime | None = None) -> list[Path]:
        return []  # entered by hand with `health experiment`

    # -- writing -------------------------------------------------------

    def start(self, spec: dict) -> Path:
        if not spec.get("hypothesis"):
            raise ValueError("an experiment needs a hypothesis")
        experiment_id = spec.get("experiment_id") or slug(spec["hypothesis"])
        existing = {e.get("experiment_id") for e in self._events()}
        if experiment_id in existing:
            raise ValueError(f"{experiment_id!r} already exists — pass --id to "
                             f"pick a different one")
        if spec.get("exposure_type") not in EXPOSURE_TYPES:
            raise ValueError(f"exposure must be one of {', '.join(EXPOSURE_TYPES)}")
        if spec.get("exposure_type") == "metric_threshold":
            if not spec.get("exposure_metric") or spec.get("exposure_metric") not in M.UNITS:
                raise ValueError("--exposure must name a known metric for a "
                                 "metric_threshold experiment — see `health sql "
                                 "\"SELECT DISTINCT metric FROM daily_metrics\"`")
            if spec.get("exposure_threshold") is None:
                raise ValueError("metric_threshold needs --exposure-threshold")
        outcome = spec.get("outcome_metric")
        if not outcome or outcome not in M.UNITS:
            raise ValueError(f"{outcome!r} is not a metric this system tracks")
        if spec.get("predicted_direction") not in DIRECTIONS:
            raise ValueError(f"--direction must be one of {', '.join(DIRECTIONS)}")
        if int(spec.get("blocks_planned", 0)) < MIN_BLOCKS:
            raise ValueError(f"need at least {MIN_BLOCKS} blocks — fewer than "
                             f"that, the test could never show anything")
        if int(spec.get("block_days", 0)) < MIN_BLOCK_DAYS:
            raise ValueError(f"blocks need at least {MIN_BLOCK_DAYS} days")
        if spec.get("starting_condition") not in ("A", "B"):
            raise ValueError("starting_condition must be A or B")

        payload = {"kind": "event", "event": "start", "experiment_id": experiment_id,
                  "date": spec.get("date") or str(date_cls.today()), **spec}
        payload["experiment_id"] = experiment_id
        return rawstore.write(self.config.raw_dir, self.name, "event", payload)

    def stop(self, experiment_id: str, reason: str | None, on: str | None = None) -> Path:
        payload = {"kind": "event", "event": "stop", "experiment_id": experiment_id,
                  "date": on or str(date_cls.today()), "reason": reason}
        return rawstore.write(self.config.raw_dir, self.name, "event", payload)

    def log(self, experiment_id: str, day: str, adhered: bool,
           note: str | None = None) -> Path:
        payload = {"kind": "adherence", "experiment_id": experiment_id,
                  "date": day, "adhered": adhered, "note": note}
        return rawstore.write(self.config.raw_dir, self.name, "adherence", payload)

    # -- reading ---------------------------------------------------------

    def _events(self) -> list[dict]:
        out = []
        for raw_file in rawstore.iter_raw(self.config.raw_dir, self.name, "event"):
            payload = raw_file.load()
            if payload.get("kind") == "event" and payload.get("event") == "start":
                out.append(payload)
        return out

    def parse(self, path: Path) -> Records:
        payload = json.loads(path.read_text())
        drawn = parse_ts(payload.get("date"))
        if drawn is None:
            return Records()
        day = drawn.date()

        if payload.get("kind") == "adherence":
            return Records(experiment_adherence=[ExperimentAdherence(
                source=self.name, experiment_id=payload["experiment_id"],
                local_date=day, adhered=bool(payload["adhered"]),
                note=payload.get("note"))])

        start_date = parse_ts(payload.get("start_date")) if payload.get("event") == "start" \
            else None
        return Records(experiment_events=[ExperimentEvent(
            source=self.name, local_date=day, event=payload["event"],
            experiment_id=payload["experiment_id"], hypothesis=payload.get("hypothesis"),
            exposure_type=payload.get("exposure_type"),
            exposure_metric=payload.get("exposure_metric"),
            exposure_threshold=payload.get("exposure_threshold"),
            outcome_metric=payload.get("outcome_metric"),
            predicted_direction=payload.get("predicted_direction"),
            block_days=payload.get("block_days"), blocks_planned=payload.get("blocks_planned"),
            start_date=start_date.date() if start_date else None,
            starting_condition=payload.get("starting_condition"),
            reason=payload.get("reason"))])

    def check(self) -> tuple[bool, str]:
        return True, f"{len(self._events())} experiment(s)"
