"""Push only what is actually worth interrupting you for.

Illness watch, a blood-pressure escalation, a compound-monitoring marker
trending: all of this is already computed, and none of it reaches you unless
you happen to open the brief or the dashboard that day. This composes the
three of them into flags and, on request, texts the ones that are new — never
a daily digest, and never the same flag twice while it stays true.

Every message here is text another module already wrote (`illness_watch`'s
`note`, `blood_pressure`'s `note`, `monitoring`'s `why`/`current_trend`) —
this module decides *whether* to speak, not what to say.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .features import checkin as checkin_features
from .features import protocol as protocol_features
from .features import readiness as readiness_features
from .store import Store


@dataclass
class Alert:
    key: str
    message: str

    def as_dict(self) -> dict:
        return {"key": self.key, "message": self.message}


def check(store: Store, as_of: date | None = None) -> list[Alert]:
    """Every flag that is currently true, regardless of whether it has
    already been sent — `send` is what applies the dedup."""
    as_of = as_of or date.today()
    out: list[Alert] = []

    watch = readiness_features.illness_watch(store, as_of=as_of)
    if watch["flag"] == "watch":
        out.append(Alert("illness_watch", f"Illness watch: {watch['note']}"))

    bp = checkin_features.blood_pressure(store, as_of=as_of)
    if bp.get("verdict") == "see a doctor":
        out.append(Alert("bp_escalate", f"Blood pressure: {bp['note']}"))

    for marker in protocol_features.monitoring(store, as_of):
        if not marker.get("current_trend"):
            continue
        key = f"monitor:{marker['compound']}:{marker['marker']}"
        out.append(Alert(key, f"{marker['compound']} — {marker['marker']}: "
                              f"{marker['current_trend']} ({marker['why']})"))

    return out


def send(store: Store, as_of: date | None = None,
         handle: str | None = None) -> list[Alert]:
    """Run `check`, text whatever is newly flagged, and update the dedup
    state — clearing anything that stopped being true so it can alert again
    if it recurs. Returns only what was actually sent.

    A delivery failure (Automation permission not yet granted, Messages not
    signed in) is not marked as sent, so the next scheduled run retries it —
    but it does not stop the rest of this run's flags from being checked.
    """
    from .notify import NotifyError, send_imessage

    as_of = as_of or date.today()
    active = check(store, as_of=as_of)
    active_keys = {a.key for a in active}

    sent = []
    for alert in active:
        if store.alerted(alert.key):
            continue
        if handle:
            try:
                send_imessage(handle, alert.message)
            except NotifyError:
                continue
        store.mark_alerted(alert.key)
        sent.append(alert)

    for row in store.query("SELECT flag_key FROM alert_state"):
        if row[0] not in active_keys:
            store.clear_alert(row[0])

    return sent
