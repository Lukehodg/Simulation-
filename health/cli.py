"""Command line.

    health init                 create the database and views
    health auth whoop           one-time OAuth consent
    health sync [source ...]    fetch new data and load it
    health ingest PATH          load an export file or folder by hand
    health replay [source]      rebuild every table from raw/, no network
    health status               what's in the database, and how fresh
    health doctor               check credentials and connectivity
    health labs add FILE        add a blood panel (JSON or CSV)
    health labs [ANALYTE]       latest results, flags and trends
    health research "question"  search the literature, with citations
    health cycle [--metric M]   where you are, and how a metric moves by phase
    health lifts [EXERCISE]     strength progression, or one exercise's history
    health volume [--weeks N]   weekly tonnage by muscle group
    health sql "SELECT ..."     ask the database directly
    health mcp                  serve the tools Claude calls (stdio)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import research as research_api
from .analytes import ANALYTES, canonical_analyte
from .features import (
    cycle as cycle_features,
    labs as lab_features,
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


#: Shown under a source that is not working yet. The WHOOP redirect URI is
#: the single most common setup mistake: it has to match the registered value
#: character for character, port and path included.
SETUP_HINTS = {
    "whoop": [
        "create a free app at developer.whoop.com, then:",
        "  redirect URI (exactly):  http://localhost:8765/callback",
        "  scopes: read:recovery read:cycles read:sleep read:workout",
        "          read:profile read:body_measurement offline",
        "  offline is what gets you a refresh token — without it you",
        "  re-authorise every hour",
        "then: health auth whoop",
    ],
    "hevy": [
        "Hevy Pro only. Copy the key from hevy.com/settings?developer",
        "then store it as HEVY_API_KEY",
    ],
    "apple_health": [
        "install Health Auto Export on the iPhone, point an automation at",
        "iCloud Drive, then set HEALTH_APPLE_EXPORT_DIR to that folder",
    ],
}


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
        if not ok:
            for line in SETUP_HINTS.get(name, []):
                print(f"     {line}")
    return 1 if problems else 0


def cmd_mcp(args, config) -> int:
    """Serve the tool surface over stdio. Nothing is printed to stdout here —
    that channel is the protocol."""
    from .mcp_server import run

    run(config)
    return 0


def cmd_sql(args, config) -> int:
    with _store(config, read_only=True) as store:
        try:
            result = store.db.execute(args.query)
        except Exception as exc:  # noqa: BLE001 - a query tool, not a shell
            if "read-only" in str(exc):
                raise SystemExit(
                    "`health sql` opens the database read-only. Loading and "
                    "deleting data goes through `health sync`, `health ingest` "
                    "and `health replay`, so that raw/ always explains what is "
                    "in the tables."
                )
            raise SystemExit(f"{type(exc).__name__}: {exc}")
        columns = [d[0] for d in result.description or []]
        rows = result.fetchall()
    if columns:
        print(" | ".join(columns))
        print("-" * (sum(len(c) for c in columns) + 3 * len(columns)))
    for row in rows:
        print(" | ".join("" if v is None else str(v) for v in row))
    print(f"\n{len(rows)} row(s)")
    return 0


def _print_paper(index: int, paper) -> None:
    line = f"{index}. {paper.title.rstrip('.')}"
    print(line)
    meta = [paper.design]
    if paper.journal:
        meta.append(paper.journal)
    if paper.year:
        meta.append(str(paper.year))
    meta.append(f"cited {paper.cited_by}")
    if paper.open_access:
        meta.append("open access")
    print(f"   {' · '.join(meta)}")
    if paper.url:
        print(f"   {paper.url}")


def cmd_labs(args, config) -> int:
    config.ensure_dirs()
    source = build_source("labs", config)

    if args.add:
        path = Path(args.add).expanduser()
        if not path.exists():
            raise SystemExit(f"{path} does not exist")
        with _store(config) as store:
            landed = source.add(path, date=args.date, lab=args.lab)
            records = source.parse(landed)
            written = store.load(records)
            store.record_raw(landed, "labs", "panel",
                             datetime.now(timezone.utc), parsed=True)
            print(f"added {written.get('lab_results', 0)} result(s) from {path.name}")
            skipped = source.unknown_analytes(landed)
            if skipped:
                print("not recognised, so not stored: " + ", ".join(skipped))
                print("tell me and I will add them to the vocabulary.")
        return 0

    with _store(config, read_only=True) as store:
        if args.analyte:
            key = canonical_analyte(args.analyte) or args.analyte
            values = lab_features.history(store, key)
            if not values:
                print(f"No results stored for {args.analyte!r}.")
                return 1
            spec = ANALYTES.get(key)
            print(f"{spec.label if spec else key}")
            result = lab_features.trend(store, key)
            print(result.describe())
            if spec and spec.note:
                print(f"note: {spec.note}")
            print()
            print(f"{'date':<12}{'value':>14}  {'flag':<8}{'range':<18}phase")
            print("-" * 66)
            for value in values:
                shown = f"{value.value:.4g} {value.unit}" if value.value is not None else "-"
                print(f"{str(value.local_date):<12}{shown:>14}  {value.flag:<8}"
                      f"{value.range_text:<18}{value.phase or ''}")
            return 0

        all_panels = lab_features.panels(store)
        if not all_panels:
            print("No blood tests yet. Add one with:\n"
                  "  health labs add panel.json\n"
                  "  health labs add panel.csv --date 2026-08-14 --lab Medichecks")
            return 0

        day, lab, count = all_panels[0]
        print(f"latest panel: {day}" + (f" ({lab})" if lab else "")
              + f", {count} result(s); {len(all_panels)} panel(s) stored\n")
        print(f"{'analyte':<26}{'value':>18}  {'flag':<8}{'range':<18}phase")
        print("-" * 84)
        for value in lab_features.latest_panel(store):
            shown = f"{value.value:.4g} {value.unit}" if value.value is not None else "-"
            print(f"{value.label[:25]:<26}{shown:>18}  {value.flag:<8}"
                  f"{value.range_text:<18}{value.phase or ''}")

        unknown_units = lab_features.unconverted(store)
        if unknown_units:
            print("\nunit not recognised, so no flag was applied: " +
                  ", ".join(f"{a} ({u})" for a, u, _ in unknown_units))

        out_of_range = lab_features.flagged(store)
        if out_of_range:
            print("\noutside the reference range:")
            for value in out_of_range:
                print(f"  {value.label} {value.value:.4g} {value.unit} "
                      f"({value.flag}, range {value.range_text})")
                if value.note:
                    print(f"    {value.note}")
            print("\nThese are worth raising with your doctor. To see what the "
                  "literature says:\n  health research --analyte "
                  f"{out_of_range[0].analyte}")
    return 0


def cmd_research(args, config) -> int:
    if args.analyte:
        key = canonical_analyte(args.analyte) or args.analyte
        direction = None
        with _store(config, read_only=True) as store:
            history = lab_features.history(store, key)
        if history:
            latest = history[-1]
            direction = latest.flag if latest.flag in ("low", "high") else None
            print(f"your most recent {latest.label}: {latest.value:.4g} {latest.unit} "
                  f"({latest.flag}, range {latest.range_text})\n")
        terms = research_api.evidence_query(key, direction, args.context)
    else:
        terms = args.query
    if not terms:
        raise SystemExit("Give a question, or --analyte ferritin")

    designs = None if args.all else ["Meta-Analysis", "Systematic Review",
                                     "Randomized Controlled Trial", "Review"]
    print(f'searching: "{terms}"\n')
    try:
        papers = research_api.search(terms, limit=args.limit,
                                     since_year=args.since, designs=designs)
    except Exception as exc:  # noqa: BLE001 - a search tool, not a crash site
        raise SystemExit(f"Europe PMC lookup failed: {type(exc).__name__}: {exc}")

    if not papers:
        print("Nothing found. Try broader terms, or --all to include every "
              "study design.")
        return 1
    for index, paper in enumerate(papers, 1):
        _print_paper(index, paper)
        print()
    print("Study design and citation count are shown so you can weigh these "
          "yourself. They describe the paper, not whether it applies to you.")
    return 0


def cmd_cycle(args, config) -> int:
    with _store(config) as store:
        cycle_features.rebuild(store)
        summary = cycle_features.summary(store)

        if not summary.get("cycles"):
            print(summary["note"])
            return 1

        length = summary["median_length"]
        if summary["cycle_day"]:
            print(f"day {summary['cycle_day']} of a typical {length or '?'}-day cycle"
                  f"   ({summary['phase']})")
        else:
            print(f"today cannot be placed in a cycle")
        print(f"this cycle began {summary['current_start']}"
              f" ({summary['days_since_period']} days ago)")
        if summary.get("note"):
            print(summary["note"])
        if summary.get("next_period_estimate") and not summary["overdue_days"]:
            print(f"next period around {summary['next_period_estimate']}"
                  + ("   (estimated from your median cycle)" if length else ""))
        span = summary["length_range"]
        if span:
            print(f"\n{summary['completed']} completed cycle(s), "
                  f"{span[0]}-{span[1]} days"
                  + ("   — variable enough to be worth mentioning to a clinician"
                     if summary["irregular"] else ""))
        if summary["implausible_lengths"]:
            print(f"gaps of {summary['implausible_lengths']} days look like "
                  f"unlogged cycles rather than long ones, and are left out of "
                  f"the median")

        metrics = args.metric or ["hrv_rmssd", "resting_hr"]
        rows = []
        for metric in metrics:
            stats = cycle_features.phase_baselines(store, metric)
            if not any(s.n for s in stats.values()):
                continue
            rows.append((metric, stats, cycle_features.phase_signature(store, metric)))

        if not rows:
            print("\nNo metrics with enough history to break down by phase yet.")
            return 0

        print(f"\n{'metric':<18}" + "".join(f"{p[:10]:>12}" for p in cycle_features.PHASES)
              + "   luteal shift")
        print("-" * 84)
        for metric, stats, signature in rows:
            cells = "".join(
                f"{stats[p].mean:>12.1f}" if stats[p].usable else f"{'-':>12}"
                for p in cycle_features.PHASES
            )
            delta = signature["delta"]
            if delta is None:
                verdict = "not enough data"
            else:
                agrees = signature["agrees"]
                expected = signature["expected"]
                verdict = f"{delta:+.1f}"
                if expected:
                    verdict += ("  (as expected)" if agrees
                                else f"  (expected {expected} — worth a look)")
            print(f"{metric:<18}{cells}   {verdict}")

        if args.day:
            day = parse_ts(args.day).date()
            print()
            for metric in metrics:
                reading = cycle_features.phase_adjusted(store, metric, day)
                print(reading.describe())
                if reading.verdicts_disagree:
                    print("  ^ a phase-blind baseline would flag this; the same "
                          "phase of your own cycles says it is ordinary")
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

    labs = sub.add_parser("labs", help="blood tests")
    labs.add_argument("analyte", nargs="?", help="one analyte's history")
    labs.add_argument("--add", metavar="FILE", help="add a panel (JSON or CSV)")
    labs.add_argument("--date", help="date of the draw, if the file lacks one")
    labs.add_argument("--lab", help="which lab ran it")
    labs.set_defaults(fn=cmd_labs)

    research = sub.add_parser("research", help="search the literature")
    research.add_argument("query", nargs="?", help="what to search for")
    research.add_argument("--analyte", help="search around one of your results")
    research.add_argument("--context", help="extra terms, e.g. 'endurance athletes'")
    research.add_argument("--since", type=int, default=2015, help="earliest year")
    research.add_argument("--limit", type=int, default=8)
    research.add_argument("--all", action="store_true",
                          help="include every study design, not just reviews and trials")
    research.set_defaults(fn=cmd_research)

    cycle_cmd = sub.add_parser("cycle", help="cycle phase and phase-aware baselines")
    cycle_cmd.add_argument("--metric", action="append",
                           help="metric to break down by phase (repeatable)")
    cycle_cmd.add_argument("--day", help="judge one day against its phase")
    cycle_cmd.set_defaults(fn=cmd_cycle)

    lifts = sub.add_parser("lifts", help="strength progression")
    lifts.add_argument("exercise", nargs="?", help="exact Hevy exercise name")
    lifts.add_argument("--window", type=int, default=8,
                       help="sessions to fit the trend over (default 8)")
    lifts.add_argument("--limit", type=int, default=20, help="sessions to list")
    lifts.set_defaults(fn=cmd_lifts)

    volume = sub.add_parser("volume", help="weekly tonnage by muscle group")
    volume.add_argument("--weeks", type=int, default=8)
    volume.set_defaults(fn=cmd_volume)

    sub.add_parser("mcp", help="serve the tools Claude calls").set_defaults(fn=cmd_mcp)

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
