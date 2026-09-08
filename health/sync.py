"""The sync loop: fetch, parse, load.

Deliberately re-fetches a little more than it strictly needs to. WHOOP re-scores
a night's sleep and recovery hours after the fact, and you can edit a Hevy
workout days later, so a cursor that advances to "now" every run would freeze
the first, worst version of each record into the database. Overlap plus
idempotent upsert costs a few API calls and gets the corrections.
"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .models import Records
from .raw import iter_raw
from .sources import SOURCES
from .sources.base import Source
from .store import Store
from .timeutil import isoformat, parse_ts

# How far back to re-fetch on each incremental sync.
OVERLAP = {
    "whoop": timedelta(days=3),      # recovery scores settle overnight
    "hevy": timedelta(days=2),       # workouts get edited after the session
    "apple_health": timedelta(0),    # file mtimes are exact
}
DEFAULT_BACKFILL_DAYS = 730


@dataclass
class SyncReport:
    source: str
    files: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def add(self, written: dict[str, int]) -> None:
        for table, count in written.items():
            self.rows[table] = self.rows.get(table, 0) + count

    def summary(self) -> str:
        if self.error:
            return f"{self.source}: failed — {self.error}"
        if not self.rows:
            return f"{self.source}: nothing new"
        detail = ", ".join(f"{count} {table}" for table, count in sorted(self.rows.items()))
        return f"{self.source}: {self.files} payload(s) → {detail}"


@contextmanager
def only_one(config: Config):
    """Hold a lock for the duration of a sync.

    Without it, the scheduled sync and a manual one can overlap: DuckDB gives
    the file to one writer, so the loser fails with a lock error that looks
    like a bug rather than a queue.
    """
    config.data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = config.data_dir / "sync.lock"
    handle = lock_path.open("w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "another sync is already running — this one stopped rather "
                "than fighting it for the database"
            ) from None
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def build_source(name: str, config: Config) -> Source:
    if name not in SOURCES:
        raise KeyError(f"unknown source {name!r}; known: {', '.join(sorted(SOURCES))}")
    return SOURCES[name](config)


def sync_source(store: Store, config: Config, name: str,
                since: datetime | None = None, full: bool = False) -> SyncReport:
    source = build_source(name, config)
    report = SyncReport(source=name)

    if since is None and not full:
        cursor = store.get_cursor(name)
        if cursor:
            since = (parse_ts(cursor) or datetime.now(timezone.utc)) - OVERLAP.get(
                name, timedelta(days=1)
            )
        elif source.pollable and source.windowed_backfill:
            since = datetime.now(timezone.utc) - timedelta(days=DEFAULT_BACKFILL_DAYS)

    started = datetime.now(timezone.utc)
    try:
        paths = source.fetch(since=since)
    except Exception as exc:  # noqa: BLE001 - one bad source shouldn't stop the rest
        store.set_cursor(name, store.get_cursor(name), ok=False, note=str(exc)[:500])
        report.error = f"{type(exc).__name__}: {exc}"
        return report

    report.files = len(paths)
    for path in paths:
        records = source.parse(path)
        report.add(store.load(records))
        store.record_raw(path, name, path.parent.parent.name, started, parsed=True)

    store.set_cursor(name, isoformat(started), ok=True)
    return report


def sync_all(store: Store, config: Config, names: list[str] | None = None,
             since: datetime | None = None, full: bool = False) -> list[SyncReport]:
    reports = [sync_source(store, config, name, since=since, full=full)
               for name in (names or list(SOURCES))]
    rebuild_derived(store)
    return reports


def rebuild_derived(store: Store) -> None:
    """Recompute the derived tables that sit on top of the loaded data."""
    from .features import cycle as cycle_features

    cycle_features.rebuild(store)


def replay(store: Store, config: Config, name: str | None = None) -> list[SyncReport]:
    """Re-parse every raw payload we have ever landed.

    This is the whole point of keeping raw/ immutable: a parser bug is a code
    fix and one command, not a year of lost history.
    """
    reports: dict[str, SyncReport] = {}
    parsers: dict[str, Source] = {}

    for raw_file in iter_raw(config.raw_dir, source=name):
        if raw_file.source not in SOURCES:
            continue
        parser = parsers.get(raw_file.source)
        if parser is None:
            parser = parsers[raw_file.source] = build_source(raw_file.source, config)
        report = reports.setdefault(raw_file.source, SyncReport(source=raw_file.source))
        try:
            records: Records = parser.parse(raw_file.path)
        except Exception as exc:  # noqa: BLE001 - report the file, keep going
            report.error = f"{raw_file.path.name}: {type(exc).__name__}: {exc}"
            continue
        report.files += 1
        report.add(store.load(records))

    rebuild_derived(store)
    return list(reports.values())


def ingest_path(store: Store, config: Config, path: Path,
                name: str = "apple_health") -> SyncReport:
    """Land and parse a file that arrived by hand (an export, a phone drop)."""
    from . import raw as rawstore

    source = build_source(name, config)
    report = SyncReport(source=name)
    patterns = ("*.json", "*.csv") if path.is_dir() else ()
    files = sorted(f for pattern in patterns for f in path.glob(pattern)) \
            if path.is_dir() else [path]
    for item in files:
        # A source that knows how to read its own export shape gets to; the
        # rest are landed as they arrived.
        adder = getattr(source, "add", None)
        landed = adder(item) if callable(adder) else \
            rawstore.copy_in(config.raw_dir, name, "export", item)
        report.files += 1
        report.add(store.load(source.parse(landed)))
        store.record_raw(landed, name, "export", datetime.now(timezone.utc), parsed=True)
    rebuild_derived(store)
    return report
