"""What you are on.

There is no API for this — it is what you tell the system. `health protocol
add` builds one event and this lands it in raw/ exactly like a blood panel, so
`health replay` rebuilds the whole protocol history from the raw events and a
better parser later re-reads them.

An event is one compound changing state on one day: `start`, `change` (a new
dose), or `stop`. `features/protocol.py` walks those into a day-by-day picture.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .. import compounds as C
from .. import raw as rawstore
from ..models import ProtocolEvent, Records
from ..timeutil import parse_ts
from .base import Source

EVENTS = ("start", "change", "stop")


class ProtocolSource(Source):
    name = "protocol"
    pollable = False
    windowed_backfill = False

    def fetch(self, since: datetime | None = None,
              until: datetime | None = None) -> list[Path]:
        return []  # entered by hand with `health protocol add`

    def add(self, event: dict) -> Path:
        """Land one protocol event. Validated here so a bad one never reaches raw/."""
        compound = C.canonical(str(event.get("compound", "")))
        if not compound:
            raise ValueError(
                f"{event.get('compound')!r} is not a compound I know. Known: "
                + ", ".join(sorted(C.COMPOUNDS)))
        if event.get("event") not in EVENTS:
            raise ValueError(f"event must be one of {', '.join(EVENTS)}")
        if not event.get("date"):
            raise ValueError("an event needs a date (YYYY-MM-DD)")
        if event["event"] != "stop" and event.get("dose") is None:
            raise ValueError("start and change need --dose")
        payload = {**event, "compound": compound}
        return rawstore.write(self.config.raw_dir, self.name, "event", payload)

    def parse(self, path: Path) -> Records:
        payload = json.loads(path.read_text())
        drawn = parse_ts(payload.get("date"))
        if drawn is None:
            return Records()
        return Records(protocol_events=[ProtocolEvent(
            source=self.name,
            local_date=drawn.date(),
            event=payload["event"],
            compound=payload["compound"],
            dose=_number(payload.get("dose")),
            unit=payload.get("unit"),
            freq=payload.get("freq"),
            route=payload.get("route"),
            note=payload.get("note"),
        )])

    def check(self) -> tuple[bool, str]:
        events = list(rawstore.iter_raw(self.config.raw_dir, self.name, "event"))
        return True, f"{len(events)} protocol event(s)"


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
