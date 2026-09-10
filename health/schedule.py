"""Making the sync — and, optionally, the brief — run without you.

Everything in this project assumes data keeps arriving. It does not, unless
something runs `health sync` — so this writes a launchd agent on macOS (and
prints the cron equivalent elsewhere), pointed at your own interpreter and
project directory.

Two properties that matter on a laptop rather than a server: a job whose time
passes while the machine is asleep runs when it wakes, rather than being
skipped until tomorrow; and output goes to a log inside the project, so a sync
that has been quietly failing for a fortnight is visible rather than assumed.

There are two jobs. `sync` is the one that must run. `brief` is opt-in: it runs
`health brief --save` a little after the morning sync and drops the day's
reading in `data/briefs/`, and it is only installed when an Anthropic key is
available for it to use.
"""

from __future__ import annotations

import plistlib
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .secrets import get_secret

#: job -> (launchd label, trailing CLI args, log filename, default times)
JOBS: dict[str, tuple[str, tuple[str, ...], str, tuple[str, ...]]] = {
    "sync": ("com.health.sync", ("sync",), "sync.log", ("07:15", "19:15")),
    "brief": ("com.health.brief", ("brief", "--save"), "brief.log", ("07:45",)),
}


@dataclass
class Schedule:
    label: str
    plist_path: Path
    times: tuple[str, ...]
    log: Path
    program: tuple[str, ...] = ("sync",)


def _parse_times(times: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not times:
        return default
    out = []
    for item in times.split(","):
        hour, _, minute = item.strip().partition(":")
        if not hour.isdigit() or not minute.isdigit():
            raise ValueError(f"{item!r} is not a time like 07:15")
        if not (0 <= int(hour) < 24 and 0 <= int(minute) < 60):
            raise ValueError(f"{item!r} is not a real time of day")
        out.append(f"{int(hour):02d}:{int(minute):02d}")
    if not out:
        raise ValueError("give at least one time")
    return tuple(out)


def executable() -> str:
    """The `health` next to the interpreter running us, not whatever is on a
    PATH that launchd will not have."""
    candidate = Path(sys.executable).with_name("health")
    return str(candidate if candidate.exists() else "health")


def plan(config: Config, times: str | None = None, job: str = "sync",
         notify: bool = False) -> Schedule:
    label, program, log_name, default_times = JOBS[job]
    if job == "brief" and notify:
        program = (*program, "--notify")
    return Schedule(
        label=label,
        plist_path=Path("~/Library/LaunchAgents").expanduser() / f"{label}.plist",
        times=_parse_times(times, default_times),
        log=config.data_dir / "logs" / log_name,
        program=program,
    )


def plist(config: Config, schedule: Schedule) -> dict:
    """The launchd agent, as a dictionary so it can be tested without writing
    anything to disk."""
    return {
        "Label": schedule.label,
        "ProgramArguments": [executable(), "--root", str(config.root),
                             *schedule.program],
        "WorkingDirectory": str(config.root),
        "EnvironmentVariables": {
            "HEALTH_TIMEZONE": str(config.timezone),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
        },
        "StartCalendarInterval": [
            {"Hour": int(t.split(":")[0]), "Minute": int(t.split(":")[1])}
            for t in schedule.times
        ],
        # A time that passes while the lid is shut runs on wake rather than
        # being skipped until the next day.
        "RunAtLoad": False,
        "StandardOutPath": str(schedule.log),
        "StandardErrorPath": str(schedule.log),
        "ProcessType": "Background",
        "LowPriorityIO": True,
    }


def cron_line(config: Config, schedule: Schedule) -> str:
    """The equivalent for anything that is not macOS."""
    minutes = ",".join(sorted({t.split(":")[1].lstrip("0") or "0" for t in schedule.times}))
    hours = ",".join(sorted({t.split(":")[0].lstrip("0") or "0" for t in schedule.times},
                            key=int))
    program = " ".join(schedule.program)
    return (f"{minutes} {hours} * * *  {executable()} --root {config.root} {program} "
            f">> {schedule.log} 2>&1")


def _load(schedule: Schedule, config: Config) -> str:
    schedule.log.parent.mkdir(parents=True, exist_ok=True)
    if platform.system() != "Darwin":
        return ("not macOS — add this to your crontab instead:\n  "
                + cron_line(config, schedule))

    schedule.plist_path.parent.mkdir(parents=True, exist_ok=True)
    schedule.plist_path.write_bytes(plistlib.dumps(plist(config, schedule)))
    subprocess.run(["launchctl", "unload", str(schedule.plist_path)],
                   capture_output=True)
    result = subprocess.run(["launchctl", "load", str(schedule.plist_path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return f"wrote {schedule.plist_path} but launchctl said: {result.stderr.strip()}"
    return f"loaded · {' '.join(schedule.program)} at {', '.join(schedule.times)} daily"


def install(config: Config, times: str | None = None, job: str = "sync",
            notify: bool = False) -> tuple[Schedule, str]:
    """Write and load an agent. Returns the schedule and what happened."""
    schedule = plan(config, times, job=job, notify=notify)
    if job == "brief" and not get_secret("ANTHROPIC_API_KEY", config.config_dir,
                                         required=False):
        return schedule, ("ANTHROPIC_API_KEY is not set, and the brief needs it "
                          "to write anything — skipped. Add the key, then "
                          "`health schedule --brief`.")
    if job == "brief" and notify and not config.notify_imessage:
        return schedule, ("--notify needs HEALTH_NOTIFY_IMESSAGE set (your own "
                          "number or Apple ID, in .env) — not installed.")
    return schedule, _load(schedule, config)


def remove(config: Config, job: str = "sync") -> str:
    schedule = plan(config, job=job)
    if not schedule.plist_path.exists():
        return f"nothing scheduled for {job}"
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "unload", str(schedule.plist_path)],
                       capture_output=True)
    schedule.plist_path.unlink()
    return f"removed {schedule.plist_path}"


def status(config: Config, job: str = "sync") -> dict:
    schedule = plan(config, job=job)
    installed = schedule.plist_path.exists()
    loaded = False
    if installed and platform.system() == "Darwin":
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        loaded = schedule.label in result.stdout

    times: tuple[str, ...] = ()
    if installed:
        try:
            data = plistlib.loads(schedule.plist_path.read_bytes())
            times = tuple(f"{i['Hour']:02d}:{i['Minute']:02d}"
                          for i in data.get("StartCalendarInterval", []))
        except Exception:  # noqa: BLE001 - a malformed plist is reportable, not fatal
            times = ()

    tail = ""
    if schedule.log.exists():
        lines = schedule.log.read_text(errors="replace").strip().splitlines()
        tail = "\n".join(lines[-8:])

    return {"job": job, "installed": installed, "loaded": loaded, "times": times,
            "plist": str(schedule.plist_path), "log": str(schedule.log),
            "recent": tail}
