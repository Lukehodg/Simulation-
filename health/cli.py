"""Command line.

    health init                 create the database and views
    health auth whoop           one-time OAuth consent
    health sync [source ...]    fetch new data and load it
    health ingest PATH          load an export file or folder by hand
    health replay [source]      rebuild every table from raw/, no network
    health status               what's in the database, and how fresh
    health doctor               check credentials and connectivity
    health sql "SELECT ..."     ask the database directly
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

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
