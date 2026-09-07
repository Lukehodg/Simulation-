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

    PDF    a report from a UK panel provider, read with `health labs add
           report.pdf`. The extracted text is what gets landed in raw/, so a
           better line parser later re-reads the same report rather than
           needing the PDF again.

The lab's own reference range is used wherever it is given, because it belongs
to their assay and their population. Where it is missing we fall back to a
generic adult range and say so — a flag you cannot trace to a range is worse
than no flag.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import analytes as A
from .. import raw as rawstore
from ..models import LabResult, Records
from ..timeutil import parse_ts
from .base import Source


def extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is declared
        raise SystemExit("Reading PDFs needs pypdf: uv pip install pypdf")
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


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


#: A result row: name, value, unit, then up to two reference bounds and an
#: optional L/H marker before the sample type and status columns.
ROW = re.compile(
    r"^(?P<name>.+?)\s+(?P<value>[<>]?\d[\d.,]*)\s+(?P<unit>\S+)\s*(?P<rest>.*)$"
)
NUMBER = re.compile(r"^[<>]?\d[\d.,]*$")
#: Units are the giveaway that a line is a result rather than a page header.
UNIT_HINT = ("/", "%", "^", "_")
UNIT_WORDS = {"pg", "fl", "ratio"}
DATE_FORMATS = ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d")


def _looks_like_unit(token: str) -> bool:
    lowered = token.lower()
    return any(hint in lowered for hint in UNIT_HINT) or lowered in UNIT_WORDS


def _report_date(text: str) -> str | None:
    match = re.search(r"Sample Collection Date:\s*([0-9]{1,2}[-/][A-Za-z0-9]+[-/][0-9]{4})",
                      text)
    if not match:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(match.group(1), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_report_text(text: str) -> dict:
    """Turn an extracted lab report into our panel shape.

    Every line that does not name an analyte we know is dropped by the caller,
    which is also what keeps page furniture out of the results.
    """
    sex = None
    match = re.search(r"Biological Sex:\s*(\w+)", text)
    if match:
        sex = match.group(1)

    lab = None
    match = re.search(r"on behalf of ([A-Z][A-Za-z ]+?)[.,]", text)
    if match:
        lab = match.group(1).strip()

    results: list[dict] = []
    for line in text.splitlines():
        row = ROW.match(line.strip())
        if not row or not _looks_like_unit(row.group("unit")):
            continue

        rest = row.group("rest").split()
        bounds = [token for token in rest[:3] if NUMBER.match(token)]
        marker = next((t for t in rest if t in ("L", "H")), None)

        entry: dict[str, Any] = {
            "analyte": row.group("name").strip(),
            "value": row.group("value"),
            "unit": row.group("unit"),
            "lab_flag": {"L": "low", "H": "high"}.get(marker or ""),
        }
        if len(bounds) >= 2:
            entry["ref_low"], entry["ref_high"] = bounds[0], bounds[1]
        elif len(bounds) == 1:
            entry["ref_bound"] = bounds[0]   # which side is decided at parse time
        results.append(entry)

    return {"date": _report_date(text), "lab": lab, "sex": sex, "results": results}


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
        if path.suffix.lower() == ".pdf":
            payload = {"format": "pdf_text", "file": path.name,
                       "text": extract_pdf_text(path)}
            if date:
                payload["date"] = date
            if lab:
                payload["lab"] = lab
            if not (date or _report_date(payload["text"])):
                raise ValueError(
                    f"could not find a collection date in {path.name}; "
                    f"pass --date YYYY-MM-DD"
                )
            return rawstore.write(self.config.raw_dir, self.name, "panel", payload)

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
        payload = self._panel(path)
        records = Records()

        drawn = parse_ts(payload.get("date"))
        if drawn is None:
            return records
        day = drawn.date()
        lab = payload.get("lab")
        fasting = payload.get("fasting")
        profile = A.profile_for(payload.get("sex"))
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
            bound = _number(row.get("ref_bound"))
            if bound is not None:
                low, high = self._place_bound(key, bound, row.get("lab_flag"), profile)

            if low is not None or high is not None:
                ref_source = "lab"
                if converted and A.normalise_unit(raw_unit) not in ("", A.normalise_unit(spec.unit)):
                    # Convert the range with the value or the flag is nonsense.
                    low = A.convert(key, low, raw_unit)[0]
                    high = A.convert(key, high, raw_unit)[0]
            elif spec.ranges(profile) != (None, None):
                low, high = spec.ranges(profile)
                ref_source = "generic"
            else:
                ref_source = "none"

            # The lab's own L/H marking beats ours: they know their assay.
            computed = flag_for(value, low, high) if converted else "unknown"
            flag = row.get("lab_flag") or computed

            records.lab_results.append(LabResult(
                source=self.name, panel_id=panel_id, analyte=key, local_date=day,
                value=value, unit=unit, ref_low=low, ref_high=high,
                ref_source=ref_source, flag=flag,
                converted=converted, lab=lab, fasting=fasting,
                note=row.get("note") or spec.note,
                raw_name=raw_name, raw_value=str(raw_value), raw_unit=raw_unit,
            ))
        return records

    @staticmethod
    def _place_bound(analyte: str, bound: float, lab_flag: str | None,
                     profile: str) -> tuple[float | None, float | None]:
        """Decide whether a single printed bound is the floor or the ceiling.

        Text extraction loses the column, and the two readings are opposite:
        5.0 next to cholesterol is a ceiling, 60 next to eGFR is a floor. The
        lab's own L/H marking settles it exactly where present, and otherwise
        the analyte's known one-sided direction does.
        """
        if lab_flag == "low":
            return bound, None
        if lab_flag == "high":
            return None, bound
        spec = A.ANALYTES.get(analyte)
        if spec:
            low, high = spec.ranges(profile)
            if high is not None and low is None:
                return None, bound
            if low is not None and high is None:
                return bound, None
        return None, None   # ambiguous: better no range than the wrong one

    def _panel(self, path: Path) -> dict:
        payload = json.loads(path.read_text())
        if payload.get("format") != "pdf_text":
            return payload
        # Re-read the report from the landed text, so improvements to the line
        # parser reach reports added months ago.
        parsed = parse_report_text(payload["text"])
        parsed["date"] = payload.get("date") or parsed.get("date")
        parsed["lab"] = payload.get("lab") or parsed.get("lab")
        return parsed

    def unknown_analytes(self, path: Path) -> list[str]:
        """What we skipped, so it can be added to the vocabulary."""
        payload = self._panel(path)
        return sorted({
            str(row.get("analyte") or row.get("name") or "").strip()
            for row in payload.get("results", [])
            if not A.canonical_analyte(str(row.get("analyte") or row.get("name") or ""))
        } - {""})
