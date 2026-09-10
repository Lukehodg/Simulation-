from __future__ import annotations

import subprocess

import pytest

from health import notify


class _Result:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def test_it_shells_out_to_osascript_with_the_handle_and_text(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["script"] = kwargs.get("input")
        return _Result(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    notify.send_imessage("+15551234567", "recovery is low today")

    assert seen["cmd"][:2] == ["osascript", "-"]
    assert seen["cmd"][2] == "+15551234567"
    assert seen["cmd"][3] == "recovery is low today"
    assert "service type = iMessage" in seen["script"]


def test_a_long_brief_is_clipped_to_a_notification_sized_message(monkeypatch):
    sent = {}

    def fake_run(cmd, **kwargs):
        sent["text"] = cmd[3]
        return _Result(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    notify.send_imessage("me@example.com", "word " * 1000)

    assert len(sent["text"]) <= notify.MAX_CHARS + 40
    assert sent["text"].endswith("full brief in data/briefs/")


def test_a_permission_failure_explains_how_to_fix_it(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: _Result(1, "Not authorized to send Apple events"))

    with pytest.raises(notify.NotifyError) as excinfo:
        notify.send_imessage("+15551234567", "hi")

    assert "Automation" in str(excinfo.value)


def test_not_being_on_macos_is_a_clean_error(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", boom)

    with pytest.raises(notify.NotifyError, match="macOS"):
        notify.send_imessage("+15551234567", "hi")
