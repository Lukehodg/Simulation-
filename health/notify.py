"""Getting the brief onto your phone.

The scheduled brief writes a file; this delivers it. Two channels, because the
best answer depends on what you run this on:

  * **iMessage**, through `osascript`. The brief's numbers travel the same
    iMessage/iCloud route that already carries your Health data — no
    third-party account, no new endpoint to trust. macOS only, and it costs a
    one-time "Automation" permission prompt the first time the job runs
    (Terminal, or whatever runs it, asking to control Messages). Set
    `HEALTH_NOTIFY_IMESSAGE` to your own number or Apple ID.
  * **Pushover**, over HTTPS. Works wherever Python does, which is the point:
    on Windows there is no iMessage and the brief had no way to reach a phone
    at all. It is a third party, and the honest trade is stated rather than
    buried — your brief's text passes through their servers, so this is the one
    channel where the numbers leave your own machines. Set `HEALTH_PUSHOVER_USER`
    to your user key and store `PUSHOVER_API_TOKEN` as a secret.

`send()` picks the channel from config so nothing upstream has to know which
one is in use. `HEALTH_NOTIFY_CHANNEL` forces it when both are configured.
"""

from __future__ import annotations

import subprocess

#: iMessage delivers long texts fine, but a wall of text is a bad notification.
#: Past this, send the head and point at the file.
MAX_CHARS = 1400
#: Pushover's own limits: 1024 characters of message, 250 of title.
PUSHOVER_MAX_CHARS = 1024
PUSHOVER_MAX_TITLE = 250
PUSHOVER_ENDPOINT = "https://api.pushover.net/1/messages.json"

# `participant` is the modern term; older macOS wants `buddy`. Try both so this
# does not become a per-OS-version support thread.
_SCRIPT = """
on run {targetHandle, messageText}
    tell application "Messages"
        set iMessageAccount to 1st account whose service type = iMessage
        try
            send messageText to participant targetHandle of iMessageAccount
        on error
            send messageText to buddy targetHandle of iMessageAccount
        end try
    end tell
end run
""".strip()


class NotifyError(RuntimeError):
    """osascript could not send — usually the Automation permission, or a
    handle Messages does not recognise."""


_TRUNCATED = "\n\n…full brief in data/briefs/"


def _clip(text: str, limit: int = MAX_CHARS) -> str:
    """Trim to `limit` *including* the pointer at the end — Pushover rejects an
    over-length message outright, so the suffix has to fit inside the budget."""
    if len(text) <= limit:
        return text
    head = text[:max(0, limit - len(_TRUNCATED))].rsplit(" ", 1)[0].rstrip()
    return head + _TRUNCATED


def send_imessage(handle: str, text: str, timeout: float = 30.0) -> None:
    """Text `text` to `handle` over iMessage. Raises NotifyError on failure."""
    try:
        result = subprocess.run(
            ["osascript", "-", handle, _clip(text)],
            input=_SCRIPT, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as exc:  # not macOS
        raise NotifyError("osascript not found — iMessage delivery is macOS only") from exc
    except subprocess.TimeoutExpired as exc:
        raise NotifyError("Messages did not respond — is it signed in?") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or "osascript exited non-zero"
        if "Not authorized" in detail or "1743" in detail:
            detail += ("\n  → grant Automation access: System Settings → Privacy "
                       "& Security → Automation → (your terminal) → Messages")
        raise NotifyError(detail)


def send_pushover(user_key: str, token: str, text: str, *,
                  title: str | None = None, timeout: float = 30.0) -> None:
    """Push `text` to the devices on `user_key`. Raises NotifyError on failure.

    Pushover answers 200 with `{"status": 1}` on success and 4xx with an
    `errors` list on a bad key or token, so the message the caller sees is
    Pushover's own rather than a status code.
    """
    import httpx

    payload = {"token": token, "user": user_key,
               "message": _clip(text, PUSHOVER_MAX_CHARS)}
    if title:
        payload["title"] = title[:PUSHOVER_MAX_TITLE]

    try:
        response = httpx.post(PUSHOVER_ENDPOINT, data=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        raise NotifyError(f"could not reach Pushover: {exc}") from exc

    if response.status_code == 200:
        return

    detail = f"Pushover returned {response.status_code}"
    try:
        errors = response.json().get("errors")
    except ValueError:
        errors = None
    if errors:
        detail += ": " + "; ".join(str(e) for e in errors)
    if response.status_code in (400, 401):
        detail += ("\n  → check HEALTH_PUSHOVER_USER (your 30-character user key) "
                   "and the PUSHOVER_API_TOKEN secret (the application token you "
                   "created at pushover.net/apps)")
    raise NotifyError(detail)


def send(config, text: str, *, title: str | None = None) -> str:
    """Deliver on whichever channel is configured. Returns the channel's name.

    The one place that knows how the brief and the alerts reach a phone, so
    neither of them has to. Raises NotifyError when nothing is configured —
    a silent no-op would look exactly like a quiet morning.
    """
    from .secrets import get_secret

    channel = config.notify_channel
    if channel == "pushover":
        token = get_secret("PUSHOVER_API_TOKEN", config.config_dir, required=False)
        if not token:
            raise NotifyError(
                "HEALTH_PUSHOVER_USER is set but PUSHOVER_API_TOKEN is not — "
                "add `PUSHOVER_API_TOKEN=...` to .env, or to the Keychain as "
                "the README describes")
        send_pushover(config.notify_pushover_user, token, text, title=title)
        return "pushover"

    if channel == "imessage":
        body = f"{title}\n\n{text}" if title else text
        send_imessage(config.notify_imessage, body)
        return "imessage"

    raise NotifyError(
        "no delivery channel configured — set HEALTH_PUSHOVER_USER (plus the "
        "PUSHOVER_API_TOKEN secret), or HEALTH_NOTIFY_IMESSAGE on macOS")
