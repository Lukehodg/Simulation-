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


# -- Pushover ----------------------------------------------------------------

class _Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _fake_post(monkeypatch, response, seen: dict):
    import httpx

    def post(url, data=None, files=None, timeout=None):
        seen["url"], seen["data"], seen["files"] = url, data, files
        return response

    monkeypatch.setattr(httpx, "post", post)


def test_pushover_posts_the_token_user_and_message(monkeypatch):
    seen: dict = {}
    _fake_post(monkeypatch, _Response(200, {"status": 1}), seen)

    notify.send_pushover("user-key", "app-token", "recovery is low today",
                         title="today brief")

    assert seen["url"] == notify.PUSHOVER_ENDPOINT
    assert seen["data"]["user"] == "user-key"
    assert seen["data"]["token"] == "app-token"
    assert seen["data"]["message"] == "recovery is low today"
    assert seen["data"]["title"] == "today brief"


def test_a_long_brief_fits_inside_pushovers_own_limit(monkeypatch):
    """Pushover rejects an over-length message outright, so the pointer at the
    end has to be inside the budget rather than appended past it."""
    seen: dict = {}
    _fake_post(monkeypatch, _Response(200, {"status": 1}), seen)

    notify.send_pushover("u", "t", "word " * 1000)

    assert len(seen["data"]["message"]) <= notify.PUSHOVER_MAX_CHARS
    assert seen["data"]["message"].endswith("full brief in data/briefs/")


def test_a_long_title_is_trimmed_rather_than_rejected(monkeypatch):
    seen: dict = {}
    _fake_post(monkeypatch, _Response(200, {"status": 1}), seen)

    notify.send_pushover("u", "t", "hi", title="x" * 400)

    assert len(seen["data"]["title"]) == notify.PUSHOVER_MAX_TITLE


def test_a_bad_token_explains_which_credential_to_check(monkeypatch):
    _fake_post(monkeypatch,
               _Response(400, {"errors": ["application token is invalid"]}), {})

    with pytest.raises(notify.NotifyError) as excinfo:
        notify.send_pushover("u", "bad", "hi")

    assert "application token is invalid" in str(excinfo.value)
    assert "PUSHOVER_API_TOKEN" in str(excinfo.value)


def test_an_unreachable_pushover_is_a_notify_error_not_a_traceback(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "post", boom)

    with pytest.raises(notify.NotifyError, match="could not reach Pushover"):
        notify.send_pushover("u", "t", "hi")


# -- picking a channel -------------------------------------------------------

def _config(tmp_path, **kw):
    from zoneinfo import ZoneInfo

    from health.config import Config

    return Config(root=tmp_path, timezone=ZoneInfo("Europe/London"),
                  apple_export_dir=None, **kw)


def test_pushover_is_preferred_because_it_works_everywhere(tmp_path):
    both = _config(tmp_path, notify_imessage="+15551234567",
                   notify_pushover_user="user-key")
    assert both.notify_channel == "pushover"
    assert _config(tmp_path, notify_imessage="+15551234567").notify_channel == "imessage"
    assert _config(tmp_path).notify_channel is None


def test_an_explicit_channel_wins_over_the_inference(tmp_path):
    forced = _config(tmp_path, notify_imessage="+15551234567",
                     notify_pushover_user="user-key",
                     notify_channel_override="imessage")
    assert forced.notify_channel == "imessage"


def test_send_routes_to_the_configured_channel(tmp_path, monkeypatch):
    seen: dict = {}
    _fake_post(monkeypatch, _Response(200, {"status": 1}), seen)
    monkeypatch.setenv("PUSHOVER_API_TOKEN", "app-token")

    channel = notify.send(_config(tmp_path, notify_pushover_user="user-key"),
                          "the brief", title="today")

    assert channel == "pushover"
    assert seen["data"]["message"] == "the brief"


def test_a_user_key_without_a_token_says_which_half_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("PUSHOVER_API_TOKEN", raising=False)

    with pytest.raises(notify.NotifyError, match="PUSHOVER_API_TOKEN"):
        notify.send(_config(tmp_path, notify_pushover_user="user-key"), "hi")


def test_sending_with_nothing_configured_is_an_error_not_a_silent_no_op(tmp_path):
    """A brief that quietly went nowhere looks exactly like a quiet morning."""
    with pytest.raises(notify.NotifyError, match="no delivery channel"):
        notify.send(_config(tmp_path), "hi")


def test_status_catches_a_user_key_with_no_token(tmp_path, monkeypatch):
    """The half-finished shape this is usually in, and the one whose failure
    turns up at 07:45 rather than at a prompt."""
    monkeypatch.delenv("PUSHOVER_API_TOKEN", raising=False)
    ready, detail = notify.status(_config(tmp_path, notify_pushover_user="u"))

    assert ready is False
    assert "PUSHOVER_API_TOKEN" in detail


def test_status_is_happy_with_both_halves(tmp_path, monkeypatch):
    monkeypatch.setenv("PUSHOVER_API_TOKEN", "app-token")
    ready, detail = notify.status(_config(tmp_path, notify_pushover_user="u"))

    assert ready is True
    assert "pushover" in detail


def test_status_says_imessage_cannot_deliver_off_a_mac(tmp_path, monkeypatch):
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    ready, detail = notify.status(_config(tmp_path, notify_imessage="+15551234567"))

    assert ready is False
    assert "macOS only" in detail and "HEALTH_PUSHOVER_USER" in detail


def test_status_reports_nothing_configured(tmp_path):
    ready, detail = notify.status(_config(tmp_path))

    assert ready is False
    assert "not configured" in detail


def test_the_chart_card_rides_along_as_an_attachment(monkeypatch):
    seen: dict = {}
    _fake_post(monkeypatch, _Response(200, {"status": 1}), seen)

    notify.send_pushover("u", "t", "the brief", image=b"\x89PNG fake")

    name, body, mime = seen["files"]["attachment"]
    assert (name, mime) == ("brief.png", "image/png")
    assert body == b"\x89PNG fake"


def test_an_oversized_image_is_dropped_not_the_message(monkeypatch):
    seen: dict = {}
    _fake_post(monkeypatch, _Response(200, {"status": 1}), seen)

    notify.send_pushover("u", "t", "the brief",
                         image=b"x" * (notify.PUSHOVER_MAX_ATTACHMENT + 1))

    assert seen["files"] is None
    assert seen["data"]["message"] == "the brief"
