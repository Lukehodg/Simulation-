"""DuckDB storage.

One file on disk, real SQL, no server. Writes are idempotent on the natural key
of each table, so re-running a sync — or replaying five years of raw payloads —
converges on the same database instead of duplicating it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import duckdb

from . import metrics as M
from .models import Records

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
VIEWS_PATH = Path(__file__).with_name("views.sql")

# table -> (natural key columns, all columns)
TABLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "observations": (
        ("source", "metric", "source_id"),
        ("ts", "local_date", "metric", "value", "unit", "source", "source_id", "ingested_at"),
    ),
    "sleeps": (
        ("source", "source_id"),
        ("source", "source_id", "start_ts", "end_ts", "local_date", "duration_min",
         "in_bed_min", "efficiency", "rem_min", "deep_min", "light_min", "awake_min",
         "is_nap", "hr_min", "hrv", "respiratory_rate", "ingested_at"),
    ),
    "workouts": (
        ("source", "source_id"),
        ("source", "source_id", "start_ts", "end_ts", "local_date", "type", "title",
         "duration_min", "distance_m", "avg_hr", "max_hr", "kcal", "strain", "rpe",
         "ingested_at"),
    ),
    "strength_sets": (
        ("source", "workout_id", "exercise_idx", "set_idx"),
        ("source", "workout_id", "exercise_idx", "set_idx", "ts", "local_date", "exercise",
         "exercise_id", "set_type", "weight_kg", "reps", "distance_m", "duration_s", "rpe",
         "ingested_at"),
    ),
    "exercise_templates": (
        ("source", "template_id"),
        ("source", "template_id", "title", "primary_muscle", "secondary_muscles",
         "equipment", "is_custom", "ingested_at"),
    ),
    "nutrition_days": (
        ("source", "local_date"),
        ("source", "local_date", "kcal", "protein_g", "carbs_g", "fat_g", "fibre_g",
         "sugar_g", "sodium_mg", "caffeine_mg", "water_ml", "ingested_at"),
    ),
    "nutrition_items": (
        ("source", "local_date", "meal", "item_idx"),
        ("source", "local_date", "meal", "item_idx", "food", "quantity", "kcal",
         "protein_g", "carbs_g", "fat_g", "ingested_at"),
    ),
    "lab_results": (
        ("source", "panel_id", "analyte"),
        ("source", "panel_id", "analyte", "local_date", "value", "unit", "ref_low",
         "ref_high", "ref_source", "flag", "converted", "lab", "fasting", "note",
         "raw_name", "raw_value", "raw_unit", "ingested_at"),
    ),
    "cycle_events": (
        ("source", "local_date", "event"),
        ("source", "local_date", "event", "flow", "value", "ingested_at"),
    ),
    "protocol_events": (
        ("source", "local_date", "event", "compound"),
        ("source", "local_date", "event", "compound", "dose", "unit", "freq",
         "route", "note", "ingested_at"),
    ),
}

# Records field -> table
_RECORD_TABLES = {
    "observations": "observations",
    "sleeps": "sleeps",
    "workouts": "workouts",
    "strength_sets": "strength_sets",
    "exercise_templates": "exercise_templates",
    "nutrition_days": "nutrition_days",
    "nutrition_items": "nutrition_items",
    "cycle_events": "cycle_events",
    "protocol_events": "protocol_events",
    "lab_results": "lab_results",
}


class Store:
    def __init__(self, path: Path | str, read_only: bool = False) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = duckdb.connect(str(path), read_only=read_only)

    # -- lifecycle ---------------------------------------------------------

    def init_schema(self) -> None:
        self.db.execute(SCHEMA_PATH.read_text())
        self._load_source_priority()
        self.db.execute(VIEWS_PATH.read_text())

    def _load_source_priority(self) -> None:
        """Materialise the priority table from metrics.py, which is the one
        place the ordering is written down."""
        self.db.execute(
            "CREATE OR REPLACE TABLE source_priority "
            "(metric VARCHAR, source VARCHAR, rank INTEGER)"
        )
        rows: list[tuple[str, str, int]] = []
        for metric, sources in M.SOURCE_PRIORITY.items():
            for rank, source in enumerate(sources):
                rows.append((metric, source, rank))
        # Anything not named explicitly falls back to the global ordering.
        for metric in M.UNITS:
            if metric in M.SOURCE_PRIORITY:
                continue
            for rank, source in enumerate(M.DEFAULT_PRIORITY):
                rows.append((metric, source, rank))
        if rows:
            self.db.executemany("INSERT INTO source_priority VALUES (?, ?, ?)", rows)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- writing -----------------------------------------------------------

    def upsert(self, table: str, rows: Sequence[dict[str, Any]]) -> int:
        if not rows:
            return 0
        key, columns = TABLES[table]
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{c} = excluded.{c}" for c in columns if c not in key
        )
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}"
        )
        payload = [tuple(row.get(c) for c in columns) for row in rows]
        self.db.executemany(sql, payload)
        return len(payload)

    def load(self, records: Records) -> dict[str, int]:
        """Write a parsed bundle. Returns rows written per table."""
        now = datetime.now(timezone.utc)
        written: dict[str, int] = {}
        if records.deleted_workouts:
            written["deleted"] = self.delete_workouts(records.deleted_workouts)
        # A workout payload always carries the complete exercise list, so a set
        # that is absent from it was removed in the app. Upsert alone would
        # leave it behind forever, quietly inflating training volume.
        self._clear_sets_for(records.strength_sets)
        for field, table in _RECORD_TABLES.items():
            items = getattr(records, field)
            if not items:
                continue
            rows = []
            for item in items:
                row = item.model_dump()
                row["ingested_at"] = now
                rows.append(row)
            written[table] = self.upsert(table, rows)
        return written

    def _clear_sets_for(self, sets: Sequence[Any]) -> None:
        pairs = {(item.source, item.workout_id) for item in sets}
        for source, workout_id in pairs:
            self.db.execute(
                "DELETE FROM strength_sets WHERE source = ? AND workout_id = ?",
                [source, workout_id],
            )

    def delete_workouts(self, source_ids: Sequence[str], source: str = "hevy") -> int:
        """Apply upstream deletions. The raw payload keeps the record of them."""
        if not source_ids:
            return 0
        ids = list(source_ids)
        placeholders = ", ".join("?" for _ in ids)
        self.db.execute(
            f"DELETE FROM strength_sets WHERE source = ? AND workout_id IN ({placeholders})",
            [source, *ids],
        )
        self.db.execute(
            f"DELETE FROM workouts WHERE source = ? AND source_id IN ({placeholders})",
            [source, *ids],
        )
        return len(ids)

    # -- sync bookkeeping --------------------------------------------------

    def get_cursor(self, source: str) -> str | None:
        row = self.db.execute(
            "SELECT cursor FROM sync_state WHERE source = ?", [source]
        ).fetchone()
        return row[0] if row else None

    def set_cursor(self, source: str, cursor: str | None, ok: bool = True,
                   note: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        self.db.execute(
            "INSERT INTO sync_state (source, cursor, last_run, last_ok, note) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (source) DO UPDATE SET "
            "  cursor = excluded.cursor, last_run = excluded.last_run, "
            "  last_ok = CASE WHEN ? THEN excluded.last_ok ELSE sync_state.last_ok END, "
            "  note = excluded.note",
            [source, cursor, now, now if ok else None, note, ok],
        )

    def record_raw(self, path: Path, source: str, kind: str,
                   fetched_at: datetime, parsed: bool = False) -> None:
        self.db.execute(
            "INSERT INTO raw_files (path, source, kind, fetched_at, size_bytes, parsed_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (path) DO UPDATE SET parsed_at = excluded.parsed_at",
            [str(path), source, kind, fetched_at,
             path.stat().st_size if path.exists() else None,
             datetime.now(timezone.utc) if parsed else None],
        )

    # -- reading -----------------------------------------------------------

    def replace_table(self, table: str, columns: Sequence[str],
                      rows: Sequence[Sequence[Any]]) -> int:
        """Rewrite a derived table wholesale. Derived tables are rebuilt, not
        migrated: recomputing from cycle_events is cheap and always correct,
        whereas a half-updated one is silently wrong."""
        self.db.execute(f"DELETE FROM {table}")
        if not rows:
            return 0
        placeholders = ", ".join("?" for _ in columns)
        self.db.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            [list(row) for row in rows],
        )
        return len(rows)

    def query(self, sql: str, params: Iterable[Any] | None = None) -> list[tuple]:
        return self.db.execute(sql, list(params) if params else None).fetchall()

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in TABLES:
            row = self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            out[table] = row[0] if row else 0
        return out

    def coverage(self) -> list[tuple[str, str, date, date, int]]:
        """What each source has actually delivered, and over what span."""
        return self.db.execute(
            """
            SELECT source, 'observations' AS table_name,
                   MIN(local_date), MAX(local_date), COUNT(*)
            FROM observations GROUP BY source
            UNION ALL
            SELECT source, 'sleeps', MIN(local_date), MAX(local_date), COUNT(*)
            FROM sleeps GROUP BY source
            UNION ALL
            SELECT source, 'workouts', MIN(local_date), MAX(local_date), COUNT(*)
            FROM workouts GROUP BY source
            UNION ALL
            SELECT source, 'strength_sets', MIN(local_date), MAX(local_date), COUNT(*)
            FROM strength_sets GROUP BY source
            UNION ALL
            SELECT source, 'nutrition_days', MIN(local_date), MAX(local_date), COUNT(*)
            FROM nutrition_days GROUP BY source
            UNION ALL
            SELECT source, 'cycle_events', MIN(local_date), MAX(local_date), COUNT(*)
            FROM cycle_events GROUP BY source
            UNION ALL
            SELECT source, 'protocol_events', MIN(local_date), MAX(local_date), COUNT(*)
            FROM protocol_events GROUP BY source
            UNION ALL
            SELECT source, 'lab_results', MIN(local_date), MAX(local_date), COUNT(*)
            FROM lab_results GROUP BY source
            ORDER BY 1, 2
            """
        ).fetchall()
