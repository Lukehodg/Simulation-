"""Blood tests.

Labs arrive as a file you write or export, not over an API, so this source has
no fetch step worth the name: it reads panel files, lands them in raw/ like
everything else, and parses them.

Two formats, both deliberately boring:

    JSON   {"date": "2026-08-14", "lab": "Medichecks", "fasting": true,
            "results": [{"analyte": "Ferritin", "value": 28, "unit": "ug/L",
                         "ref_low": 13, "ref_high": 150}, ...]}

    CSV    analyte,value,unit,ref_low,ref_high
           Ferritin,28,ug/L,13,150

The lab's own reference range is used wherever it is given, because it belongs
to their assay and their population. Where it is missing we fall back to a
generic adult range and say so — a flag you cannot trace to a range is worse
than no flag.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import analytes as A
from .. import raw as rawstore
from ..models import LabResult, Records
from ..timeutil import parse_ts
from .base import Source


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-") or "lab"


def _number(value: Any) -> float | None:
    """Lab exports say '<5', '> 90', '28.4 ' and occasionally ''."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lstrip("<>=~").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def flag_for(value: float | None, low: float | None, high: float | None) -> str:
    if value is None or (low is None and high is None):
        return "unknown"
    if low is not None and value < low:
        return "low"
    if high is not None and value > high:
        return "high"
    return "normal"


class LabsSource(Source):
    name = "labs"
    pollable = False

    def fetch(self, since: datetime | None = None,
              until: datetime | None = None) -> list[Path]:
        return []  # panels are added explicitly with `health labs add`

    def add(self, path: Path, date: str | None = None,
            lab: str | None = None) -> Path:
        """Land a panel file. Parsing happens afterwards, like every source."""
        payload = self.read_panel(path, date=date, lab=lab)
        return rawstore.write(self.config.raw_dir, self.name, "panel", payload)

    def read_panel(self, path: Path, date: str | None = None,
                   lab: str | None = None) -> dict:
        """Normalise either input format into the JSON shape we land."""
        if path.suffix.lower() == ".csv":
            with path.open(newline="") as handle:
                rows = [dict(row) for row in csv.DictReader(handle)]
            payload = {"results": rows}
        else:
            payload = json.loads(path.read_text())
            if isinstance(payload, list):
                payload = {"results": payload}

        first = payload.get("results") or []
        payload["date"] = (date or payload.get("date")
                           or (first[0].get("date") if first else None))
        payload["lab"] = lab or payload.get("lab") or (
            first[0].get("lab") if first else None)
        if not payload["date"]:
            raise ValueError(
                f"{path.name} has no date. Put a \"date\" in the file or pass "
                f"--date YYYY-MM-DD; a blood test without one cannot be placed "
                f"in a cycle or a trend."
            )
        return payload

    def check(self) -> tuple[bool, str]:
        panels = list(rawstore.iter_raw(self.config.raw_dir, self.name, "panel"))
        return True, f"{len(panels)} panel(s) added"

    # -- parse -------------------------------------------------------------

    def parse(self, path: Path) -> Records:
        payload = json.loads(path.read_text())
        records = Records()

        drawn = parse_ts(payload.get("date"))
        if drawn is None:
            return records
        day = drawn.date()
        lab = payload.get("lab")
        fasting = payload.get("fasting")
        panel_id = f"{day.isoformat()}:{_slug(lab or 'panel')}"

        seen: set[str] = set()
        for row in payload.get("results", []):
            raw_name = str(row.get("analyte") or row.get("name") or "").strip()
            key = A.canonical_analyte(raw_name)
            if not key or key in seen:
                # An analyte we do not know is skipped rather than stored under
                # a name nothing can query. `health labs --unknown` lists them.
                continue
            seen.add(key)
            spec = A.ANALYTES[key]

            raw_value = row.get("value")
            raw_unit = row.get("unit") or row.get("units")
            value = _number(raw_value)

            # HbA1c in percent is an affine conversion, not a factor.
            if key == "hba1c" and A.normalise_unit(raw_unit) in ("%", "percent"):
                value = A.hba1c_percent_to_mmol(value) if value is not None else None
                unit, converted = spec.unit, True
            else:
                value, unit, converted = A.convert(key, value, raw_unit)

            low, high = _number(row.get("ref_low")), _number(row.get("ref_high"))
            if low is not None or high is not None:
                ref_source = "lab"
                if converted and A.normalise_unit(raw_unit) not in ("", A.normalise_unit(spec.unit)):
                    # Convert the range with the value or the flag is nonsense.
                    low = A.convert(key, low, raw_unit)[0]
                    high = A.convert(key, high, raw_unit)[0]
            elif spec.ref_low is not None or spec.ref_high is not None:
                low, high, ref_source = spec.ref_low, spec.ref_high, "generic"
            else:
                ref_source = "none"

            records.lab_results.append(LabResult(
                source=self.name, panel_id=panel_id, analyte=key, local_date=day,
                value=value, unit=unit, ref_low=low, ref_high=high,
                ref_source=ref_source,
                flag=flag_for(value, low, high) if converted else "unknown",
                converted=converted, lab=lab, fasting=fasting,
                note=row.get("note") or spec.note,
                raw_name=raw_name, raw_value=str(raw_value), raw_unit=raw_unit,
            ))
        return records

    def unknown_analytes(self, path: Path) -> list[str]:
        """What we skipped, so it can be added to the vocabulary."""
        payload = json.loads(path.read_text())
        return sorted({
            str(row.get("analyte") or row.get("name") or "").strip()
            for row in payload.get("results", [])
            if not A.canonical_analyte(str(row.get("analyte") or row.get("name") or ""))
        } - {""})
