"""Making the sync — and, optionally, the brief — run without you.

Everything in this project assumes data keeps arriving. It does not, unless
something runs `health sync` — so this installs a real scheduled job, pointed
at your own interpreter and project directory: a launchd agent on macOS, a
Task Scheduler task on Windows, and the cron line printed for anything else.

Two properties that matter on a laptop rather than a server: a job whose time
passes while the machine is asleep runs when it wakes, rather than being
skipped until tomorrow (launchd does this by itself; the Windows task asks for
it with `/Z /V1`-era `StartWhenAvailable`, set through the XML that `schtasks
/Create /XML` reads); and output goes to a log inside the project, so a sync
that has been quietly failing for a fortnight is visible rather than assumed.

There are three jobs. `sync` is the one that must run. `brief` is opt-in: it
runs `health brief --save --html` a little after the morning sync, drops the
day's reading in `data/briefs/`, and with `--notify` puts it on your phone —
it is only installed when an Anthropic key is available for it to use. `alert`
is the quiet one: it speaks only when something is actually flagged, so it is
pointless without a delivery channel and is not installed without one.
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
    "brief": ("com.health.brief", ("brief", "--save", "--html"), "brief.log",
              ("07:45",)),
    # 15 minutes after each sync, so the flags it checks are reading the
    # morning's/evening's fresh data rather than yesterday's. Unlike `brief`,
    # this job is pointless without `--notify` — silence is the whole point
    # of an alert job that only speaks when something is actually flagged.
    "alert": ("com.health.alert", ("alert", "--notify"), "alert.log",
              ("07:30", "19:30")),
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
    for path in (candidate, candidate.with_suffix(".exe")):
        if path.exists():
            return str(path)
    return "health"


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
    """The equivalent for anything that is neither macOS nor Windows."""
    minutes = ",".join(sorted({t.split(":")[1].lstrip("0") or "0" for t in schedule.times}))
    hours = ",".join(sorted({t.split(":")[0].lstrip("0") or "0" for t in schedule.times},
                            key=int))
    program = " ".join(schedule.program)
    return (f"{minutes} {hours} * * *  {executable()} --root {config.root} {program} "
            f">> {schedule.log} 2>&1")


# -- Windows -----------------------------------------------------------------
#
# Task Scheduler, through `schtasks /Create /XML`. The XML route rather than
# the flag route because the flags cannot express two things this needs: more
# than one time of day in a single task, and StartWhenAvailable — the
# equivalent of launchd running a job on wake instead of skipping the day.
#
# Nothing sets HEALTH_TIMEZONE here the way the launchd agent does. It does not
# need to: `load_config` reads `.env` from the working directory, which is the
# project root, so the timezone travels with the project rather than with the
# scheduler.

#: Any date in the past works as an anchor — the daily recurrence is what
#: matters, and a fixed one keeps the generated XML stable between runs.
_ANCHOR_DAY = "2020-01-01"


def task_name(schedule: Schedule) -> str:
    """The Task Scheduler path for a job. launchd labels are dotted; Windows
    wants a path, and one folder keeps `schtasks /Query` readable."""
    return "\\Health\\" + schedule.label.rsplit(".", 1)[-1]


def task_command(config: Config, schedule: Schedule) -> str:
    """The `cmd.exe` arguments that run the job and append to its log.

    `cmd /c "..."` strips the outermost pair of quotes, which is what lets the
    executable and the log path keep theirs — both routinely live under a
    profile directory with a space in it.
    """
    program = " ".join(schedule.program)
    return (f'/c ""{executable()}" --root "{config.root}" {program} '
            f'>> "{schedule.log}" 2>&1"')


def task_xml(config: Config, schedule: Schedule) -> str:
    """The task definition, as a string so it can be tested without writing
    anything or calling schtasks."""
    from xml.sax.saxutils import escape

    triggers = "\n".join(
        f"""    <CalendarTrigger>
      <StartBoundary>{_ANCHOR_DAY}T{time}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>""" for time in schedule.times)

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>health {escape(' '.join(schedule.program))}</Description>
  </RegistrationInfo>
  <Triggers>
{triggers}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>{escape(task_command(config, schedule))}</Arguments>
      <WorkingDirectory>{escape(str(config.root))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def _install_windows(config: Config, schedule: Schedule) -> str:
    import tempfile

    name = task_name(schedule)
    # schtasks reads the definition as UTF-16; handing it UTF-8 fails with a
    # parse error that says nothing useful about why.
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False,
                                     encoding="utf-16") as handle:
        handle.write(task_xml(config, schedule))
        path = handle.name
    try:
        result = _schtasks("/Create", "/TN", name, "/XML", path, "/F")
    finally:
        Path(path).unlink(missing_ok=True)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return f"schtasks refused to create {name}: {detail}"
    return (f"created {name} · {' '.join(schedule.program)} at "
            f"{', '.join(schedule.times)} daily")


def _load(schedule: Schedule, config: Config) -> str:
    schedule.log.parent.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Windows":
        return _install_windows(config, schedule)
    if platform.system() != "Darwin":
        return ("neither macOS nor Windows — add this to your crontab instead:\n  "
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
    if job == "brief" and notify and not config.notify_channel:
        return schedule, ("--notify needs a delivery channel: HEALTH_PUSHOVER_USER "
                          "plus a PUSHOVER_API_TOKEN secret, or "
                          "HEALTH_NOTIFY_IMESSAGE on macOS — not installed.")
    if job == "alert" and not config.notify_channel:
        return schedule, ("no delivery channel is configured, and an alert job "
                          "that can never reach you is pointless — skipped. Set "
                          "HEALTH_PUSHOVER_USER (or HEALTH_NOTIFY_IMESSAGE on "
                          "macOS), then `health schedule --alert`.")
    return schedule, _load(schedule, config)


def remove(config: Config, job: str = "sync") -> str:
    schedule = plan(config, job=job)
    if platform.system() == "Windows":
        name = task_name(schedule)
        result = _schtasks("/Delete", "/TN", name, "/F")
        if result.returncode != 0:
            return f"nothing scheduled for {job}"
        return f"removed {name}"

    if not schedule.plist_path.exists():
        return f"nothing scheduled for {job}"
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "unload", str(schedule.plist_path)],
                       capture_output=True)
    schedule.plist_path.unlink()
    return f"removed {schedule.plist_path}"


def _windows_status(schedule: Schedule) -> tuple[bool, bool, tuple[str, ...]]:
    """(installed, enabled, times) from Task Scheduler's own copy of the task."""
    result = _schtasks("/Query", "/TN", task_name(schedule), "/XML", "ONE")
    if result.returncode != 0:
        return False, False, ()

    times: tuple[str, ...] = ()
    enabled = "<Enabled>false</Enabled>" not in result.stdout
    try:
        import re
        times = tuple(sorted(
            f"{h}:{m}" for h, m in
            re.findall(r"<StartBoundary>[^<]*T(\d{2}):(\d{2})", result.stdout)))
    except Exception:  # noqa: BLE001 - a task we cannot parse still exists
        times = ()
    return True, enabled, times


def status(config: Config, job: str = "sync") -> dict:
    schedule = plan(config, job=job)
    where = str(schedule.plist_path)

    if platform.system() == "Windows":
        installed, loaded, times = _windows_status(schedule)
        where = task_name(schedule)
    else:
        installed = schedule.plist_path.exists()
        loaded = False
        if installed and platform.system() == "Darwin":
            result = subprocess.run(["launchctl", "list"], capture_output=True,
                                    text=True)
            loaded = schedule.label in result.stdout

        times = ()
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
            "plist": where, "log": str(schedule.log), "recent": tail}
