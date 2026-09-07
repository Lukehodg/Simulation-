"""Command line.

    health init                 create the database and views
    health auth whoop           one-time OAuth consent
    health sync [source ...]    fetch new data and load it
    health ingest PATH          load an export file or folder by hand
    health replay [source]      rebuild every table from raw/, no network
    health status               what's in the database, and how fresh
    health doctor               check credentials and connectivity
    health lifts [EXERCISE]     strength progression, or one exercise's history
    health volume [--weeks N]   weekly tonnage by muscle group
    health sql "SELECT ..."     ask the database directly
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .features import (
    exercise_summary, progression, session_history, stale_lifts, weekly_volume,
)
from .config import load_config
from .secrets import MissingSecret
from .sources import SOURCES
from .store import Store
from .sync import build_source, ingest_path, replay, sync_all
from .timeutil import parse_ts


def _store(config, read_only: bool = False) -> Store:
    if not config.db_path.exists() and read_only:
        raise SystemExit("No database yet — run `health init` first.")
    store = Store(config.db_path, read_only=read_only)
    if not read_only:
        store.init_schema()
    return store


def cmd_init(args, config) -> int:
    config.ensure_dirs()
    with _store(config) as store:
        counts = store.counts()
    print(f"Database ready at {config.db_path}")
    print(f"Raw payloads land in {config.raw_dir}")
    print(f"Credentials read from environment, Keychain, or {config.config_dir}")
    if any(counts.values()):
        print("\nExisting rows: " + ", ".join(
            f"{table}={count}" for table, count in counts.items() if count
        ))
    return 0


def cmd_auth(args, config) -> int:
    if args.source != "whoop":
        raise SystemExit(f"{args.source} does not use OAuth — see `health doctor`.")
    config.ensure_dirs()
    build_source("whoop", config).authorize()
    return 0


def cmd_sync(args, config) -> int:
    config.ensure_dirs()
    since = parse_ts(args.since) if args.since else None
    names = args.sources or None
    with _store(config) as store:
        reports = sync_all(store, config, names, since=since, full=args.full)
    failed = False
    for report in reports:
        print(report.summary())
        failed |= report.error is not None
    return 1 if failed else 0


def cmd_ingest(args, config) -> int:
    config.ensure_dirs()
    path = Path(args.path).expanduser()
    if not path.exists():
        raise SystemExit(f"{path} does not exist")
    with _store(config) as store:
        print(ingest_path(store, config, path, name=args.source).summary())
    return 0


def cmd_replay(args, config) -> int:
    with _store(config) as store:
        reports = replay(store, config, args.source)
    if not reports:
        print("Nothing in raw/ to replay yet.")
    for report in reports:
        print(report.summary())
    return 0


def cmd_status(args, config) -> int:
    with _store(config, read_only=True) as store:
        rows = store.coverage()
        state = store.query(
            "SELECT source, last_ok, note FROM sync_state ORDER BY source"
        )
    if not rows:
        print("No data yet. Run `health sync`.")
        return 0

    print(f"{'source':<14}{'table':<16}{'from':<12}{'to':<12}{'rows':>8}")
    print("-" * 62)
    for source, table, start, end, count in rows:
        print(f"{source:<14}{table:<16}{str(start):<12}{str(end):<12}{count:>8}")

    if state:
        print("\nlast successful sync")
        for source, last_ok, note in state:
            when = last_ok.strftime("%Y-%m-%d %H:%M") if last_ok else "never"
            print(f"  {source:<14}{when}" + (f"   ({note})" if note else ""))
    return 0


def cmd_doctor(args, config) -> int:
    print(f"root       {config.root}")
    print(f"database   {config.db_path} ({'exists' if config.db_path.exists() else 'missing'})")
    print(f"timezone   {config.timezone}")
    print(f"apple dir  {config.apple_export_dir or 'not set'}\n")

    problems = 0
    for name in SOURCES:
        try:
            ok, detail = build_source(name, config).check()
        except MissingSecret as exc:
            ok, detail = False, str(exc).splitlines()[0]
        except Exception as exc:  # noqa: BLE001 - a report, not a crash
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        problems += 0 if ok else 1
        print(f"[{'ok' if ok else '--'}] {name:<14}{detail}")
    return 1 if problems else 0


def cmd_sql(args, config) -> int:
    with _store(config, read_only=True) as store:
        result = store.db.execute(args.query)
        columns = [d[0] for d in result.description or []]
        rows = result.fetchall()
    if columns:
        print(" | ".join(columns))
        print("-" * (sum(len(c) for c in columns) + 3 * len(columns)))
    for row in rows:
        print(" | ".join("" if v is None else str(v) for v in row))
    print(f"\n{len(rows)} row(s)")
    return 0


def cmd_lifts(args, config) -> int:
    with _store(config, read_only=True) as store:
        if args.exercise:
            history = session_history(store, args.exercise, limit=args.limit)
            if not history:
                print(f"No working sets recorded for {args.exercise!r}.")
                print("Names must match Hevy's exactly — try `health lifts` for the list.")
                return 1
            result = progression(store, args.exercise, window=args.window)
            print(f"{result.exercise}\n{result.describe()}")
            if result.best_e1rm:
                since = result.days_since_best
                ago = "today" if since == 0 else f"{since} days ago"
                print(f"best estimated 1RM {result.best_e1rm} kg, {ago}\n")
            print(f"{'date':<12}{'e1RM':>8}{'top set':>10}{'sets':>6}{'volume':>10}")
            print("-" * 46)
            for day, e1rm, top, sets, volume in history:
                shown = f"{e1rm:.1f}" if e1rm is not None else "-"
                print(f"{str(day):<12}{shown:>8}{top:>9.1f}kg{sets:>6}{volume:>9.0f}kg")
            return 0

        rows = exercise_summary(store)
        if not rows:
            print("No strength data yet. Run `health sync hevy`.")
            return 0
        stale = {row["exercise"] for row in stale_lifts(store)}
        print(f"{'exercise':<34}{'muscle':<13}{'last':<12}{'n':>3}{'best e1RM':>11}  trend")
        print("-" * 88)
        for row in rows:
            trend = progression(store, row["exercise"])
            flag = "  (stale)" if row["exercise"] in stale else ""
            best = f"{row['best_e1rm']:.1f} kg" if row["best_e1rm"] else "-"
            print(f"{row['exercise'][:33]:<34}{(row['muscle'] or '-')[:12]:<13}"
                  f"{str(row['last_done']):<12}{row['sessions']:>3}{best:>11}  "
                  f"{trend.describe()}{flag}")
    return 0


def cmd_volume(args, config) -> int:
    with _store(config, read_only=True) as store:
        rows = weekly_volume(store, weeks=args.weeks)
    if not rows:
        print("No strength data yet. Run `health sync hevy`.")
        return 0

    weeks = sorted({row[0] for row in rows}, reverse=True)
    muscles = sorted({row[1] for row in rows})
    table = {(row[0], row[1]): row[2] for row in rows}

    header = f"{'week':<12}" + "".join(f"{m[:10]:>11}" for m in muscles) + f"{'total':>11}"
    print(header)
    print("-" * len(header))
    for week in weeks:
        cells = "".join(
            f"{table.get((week, m), 0):>10.0f}kg" if table.get((week, m)) else f"{'-':>11}"
            for m in muscles
        )
        total = sum(table.get((week, m), 0) for m in muscles)
        print(f"{str(week.date() if hasattr(week, 'date') else week):<12}{cells}{total:>10.0f}kg")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="health", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", help="project directory (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database and views").set_defaults(fn=cmd_init)

    auth = sub.add_parser("auth", help="run a source's OAuth consent flow")
    auth.add_argument("source", choices=["whoop"])
    auth.set_defaults(fn=cmd_auth)

    sync = sub.add_parser("sync", help="fetch new data and load it")
    sync.add_argument("sources", nargs="*", choices=[*SOURCES, []], help="default: all")
    sync.add_argument("--since", help="ISO date/time to fetch from")
    sync.add_argument("--full", action="store_true", help="ignore the stored cursor")
    sync.set_defaults(fn=cmd_sync)

    ingest = sub.add_parser("ingest", help="load an export file or folder")
    ingest.add_argument("path")
    ingest.add_argument("--source", default="apple_health", choices=list(SOURCES))
    ingest.set_defaults(fn=cmd_ingest)

    replay_cmd = sub.add_parser("replay", help="rebuild tables from raw/, no network")
    replay_cmd.add_argument("source", nargs="?", choices=[*SOURCES, None])
    replay_cmd.set_defaults(fn=cmd_replay)

    sub.add_parser("status", help="what's in the database").set_defaults(fn=cmd_status)
    sub.add_parser("doctor", help="check credentials and connectivity").set_defaults(fn=cmd_doctor)

    lifts = sub.add_parser("lifts", help="strength progression")
    lifts.add_argument("exercise", nargs="?", help="exact Hevy exercise name")
    lifts.add_argument("--window", type=int, default=8,
                       help="sessions to fit the trend over (default 8)")
    lifts.add_argument("--limit", type=int, default=20, help="sessions to list")
    lifts.set_defaults(fn=cmd_lifts)

    volume = sub.add_parser("volume", help="weekly tonnage by muscle group")
    volume.add_argument("--weeks", type=int, default=8)
    volume.set_defaults(fn=cmd_volume)

    sql = sub.add_parser("sql", help="run a query")
    sql.add_argument("query")
    sql.set_defaults(fn=cmd_sql)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.root)
    try:
        return args.fn(args, config)
    except MissingSecret as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
