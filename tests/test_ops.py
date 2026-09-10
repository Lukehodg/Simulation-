"""Scheduling, backup, and not letting two syncs fight over the database."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from health import backup, schedule
from health.sync import only_one


# -- scheduling -------------------------------------------------------------

def test_the_agent_runs_the_interpreter_we_are_actually_using(config):
    """launchd has almost no PATH, so a bare `health` would not resolve."""
    plan = schedule.plan(config, "07:15")
    agent = schedule.plist(config, plan)

    assert Path(agent["ProgramArguments"][0]).is_absolute()
    assert agent["ProgramArguments"][1:] == ["--root", str(config.root), "sync"]
    assert agent["EnvironmentVariables"]["HEALTH_TIMEZONE"] == str(config.timezone)


def test_times_become_calendar_intervals(config):
    agent = schedule.plist(config, schedule.plan(config, "07:15,19:00"))

    assert agent["StartCalendarInterval"] == [
        {"Hour": 7, "Minute": 15}, {"Hour": 19, "Minute": 0}]


def test_output_goes_to_a_log_so_silent_failure_is_visible(config):
    plan = schedule.plan(config, None)
    agent = schedule.plist(config, plan)

    assert agent["StandardOutPath"] == agent["StandardErrorPath"] == str(plan.log)
    assert plan.log.name == "sync.log"


def test_a_nonsense_time_is_refused_before_anything_is_written(config):
    for bad in ("25:00", "07:99", "morning", "7"):
        with pytest.raises(ValueError):
            schedule.plan(config, bad)


def test_the_cron_fallback_says_the_same_thing(config):
    line = schedule.cron_line(config, schedule.plan(config, "07:15,19:15"))

    assert line.startswith("15 7,19 * * *")
    assert "sync" in line and str(config.root) in line


def test_status_reports_nothing_when_nothing_is_scheduled(config, monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "plan",
                        lambda cfg, times=None, job="sync": schedule.Schedule(
                            label="x", plist_path=tmp_path / "absent.plist",
                            times=(), log=tmp_path / "sync.log"))
    assert schedule.status(config)["installed"] is False


# -- backup -----------------------------------------------------------------

def _land(config, source: str, name: str = "one.json") -> Path:
    from health import raw as rawstore
    return rawstore.write(config.raw_dir, source, "kind", {"hello": name})


def test_a_backup_holds_the_raw_payloads_and_a_manifest(config):
    _land(config, "whoop")
    _land(config, "hevy")
    _land(config, "hevy", "two.json")

    result = backup.create(config)

    assert result.files == 3
    assert result.sources == {"whoop": 1, "hevy": 2}
    with tarfile.open(result.path, "r:gz") as tar:
        names = tar.getnames()
    assert backup.MANIFEST in names
    assert all(n == backup.MANIFEST or n.startswith("raw/") for n in names)


def test_the_manifest_says_what_is_inside_without_opening_it(config):
    _land(config, "whoop")
    archive = backup.create(config).path

    manifest = backup.read_manifest(archive)

    assert manifest["files"] == 1
    assert manifest["sources"] == {"whoop": 1}
    assert "no credentials" in manifest["note"]


def test_backing_up_nothing_is_an_error_not_an_empty_archive(config):
    config.ensure_dirs()
    with pytest.raises(ValueError, match="nothing in raw/"):
        backup.create(config)


def test_a_restore_will_not_quietly_merge_into_existing_data(config):
    _land(config, "whoop")
    archive = backup.create(config).path

    with pytest.raises(ValueError, match="already holds"):
        backup.restore(archive, config)

    result = backup.restore(archive, config, force=True)
    assert result["restored"] == 1


def test_a_backup_round_trips_into_an_empty_project(config, tmp_path):
    _land(config, "whoop", "payload.json")
    archive = backup.create(config).path

    from zoneinfo import ZoneInfo
    from health.config import Config
    fresh = Config(root=tmp_path / "elsewhere", timezone=ZoneInfo("Europe/London"),
                   apple_export_dir=None)

    result = backup.restore(archive, fresh)

    assert result["restored"] == 1
    restored = list(fresh.raw_dir.rglob("*.json"))
    assert restored and json.loads(restored[0].read_text())["hello"] == "payload.json"


def test_an_archive_cannot_write_outside_the_project(config, tmp_path):
    """A tar file is a list of paths someone else wrote, and "../" is legal."""
    config.ensure_dirs()
    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("nope")
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo(backup.MANIFEST)
        body = json.dumps({"created": "now", "files": 1, "bytes": 1}).encode()
        info.size = len(body)
        import io
        tar.addfile(info, io.BytesIO(body))
        tar.add(payload, arcname="../../escaped.txt")

    with pytest.raises(ValueError, match="outside"):
        backup.restore(evil, config, force=True)
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_an_archive_without_a_manifest_is_not_one_of_ours(config, tmp_path):
    plain = tmp_path / "plain.tar.gz"
    with tarfile.open(plain, "w:gz") as tar:
        pass

    with pytest.raises(ValueError, match="not a health backup"):
        backup.read_manifest(plain)


# -- one sync at a time -----------------------------------------------------

def test_a_second_sync_stops_rather_than_fighting_for_the_database(config):
    with only_one(config):
        with pytest.raises(RuntimeError, match="already running"):
            with only_one(config):
                pass


def test_the_lock_is_released_even_when_the_sync_fails(config):
    with pytest.raises(ZeroDivisionError):
        with only_one(config):
            1 / 0

    with only_one(config):
        pass          # free again
