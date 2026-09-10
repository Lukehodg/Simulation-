"""Sending the brief to your phone without it leaving Apple's world.

The scheduled brief writes a file; this texts it to you. It uses iMessage
through `osascript` rather than a push service on purpose: the one path the
brief's numbers travel is the same iMessage/iCloud route that already carries
your Health data — no third-party account, no new endpoint to trust.

Message yourself (your own number or Apple ID) and it lands as a notification
on every device you are signed in on. Set `HEALTH_NOTIFY_IMESSAGE` to that
handle.

The one-time cost is a macOS "Automation" permission prompt the first time
this runs — Terminal (or whatever runs the scheduled job) asking to control
Messages. Approve it once.
"""

from __future__ import annotations

import subprocess

#: iMessage delivers long texts fine, but a wall of text is a bad notification.
#: Past this, send the head and point at the file.
MAX_CHARS = 1400

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


def _clip(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS].rsplit(" ", 1)[0].rstrip() + \
        "\n\n…full brief in data/briefs/"


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
