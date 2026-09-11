"""Data sources.

Each one does two separable things: `fetch` talks to the outside world and
lands raw payloads, `parse` turns a landed payload into canonical records.
Keeping them separate is what makes `health replay` possible — and what keeps
a parser bug from costing us data we can no longer re-download.
"""

from __future__ import annotations

from .apple_health import AppleHealthSource
from .base import Source
from .checkin import CheckInSource
from .hevy import HevySource
from .labs import LabsSource
from .protocol import ProtocolSource
from .whoop import WhoopSource

SOURCES: dict[str, type[Source]] = {
    "whoop": WhoopSource,
    "hevy": HevySource,
    "apple_health": AppleHealthSource,
    "labs": LabsSource,
    "protocol": ProtocolSource,
    "checkin": CheckInSource,
}

__all__ = ["SOURCES", "Source", "WhoopSource", "HevySource", "AppleHealthSource",
           "LabsSource", "ProtocolSource", "CheckInSource"]
