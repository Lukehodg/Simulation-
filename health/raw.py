"""The raw landing zone.

Every byte a source gives us is written here before anything interprets it, and
never edited afterwards. When a parser turns out to be wrong — and one will —
we fix the parser and replay, instead of re-downloading a year of history that
the API may no longer be willing to hand over.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class RawFile:
    path: Path
    source: str
    kind: str
    fetched_at: datetime

    def load(self) -> Any:
        return json.loads(self.path.read_text())


def _stamp(when: datetime) -> str:
    return when.strftime("%Y%m%dT%H%M%S%f")[:-3]


def write(raw_dir: Path, source: str, kind: str, payload: Any,
          fetched_at: datetime | None = None) -> Path:
    """Land one payload. Returns the path written."""
    fetched_at = fetched_at or datetime.now(timezone.utc)
    directory = raw_dir / source / kind / fetched_at.strftime("%Y-%m")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_stamp(fetched_at)}.json"
    # Collisions only happen when two fetches land in the same millisecond.
    counter = 1
    while path.exists():
        path = directory / f"{_stamp(fetched_at)}-{counter}.json"
        counter += 1
    path.write_text(json.dumps(payload, indent=None, separators=(",", ":"), default=str))
    return path


def copy_in(raw_dir: Path, source: str, kind: str, src: Path,
            fetched_at: datetime | None = None) -> Path:
    """Land a file that arrived some other way (an export, a phone drop)."""
    fetched_at = fetched_at or datetime.now(timezone.utc)
    directory = raw_dir / source / kind / fetched_at.strftime("%Y-%m")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_stamp(fetched_at)}-{src.name}"
    path.write_bytes(src.read_bytes())
    return path


def iter_raw(raw_dir: Path, source: str | None = None,
             kind: str | None = None) -> Iterator[RawFile]:
    """Every landed payload, oldest first."""
    root = raw_dir / source if source else raw_dir
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        rel = path.relative_to(raw_dir).parts
        if len(rel) < 4:
            continue
        file_source, file_kind = rel[0], rel[1]
        if kind and file_kind != kind:
            continue
        yield RawFile(
            path=path,
            source=file_source,
            kind=file_kind,
            fetched_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
        )
