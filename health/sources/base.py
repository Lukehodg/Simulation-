from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import Config
from ..models import Records


class Source(ABC):
    """A place health data comes from.

    `fetch` may do network I/O and must land everything it receives in raw/.
    `parse` must be pure: same bytes in, same records out, no clock, no network.
    """

    name: str = "base"
    #: False for sources that arrive as files rather than over the wire.
    pollable: bool = True
    #: True when a first sync should ask for a window of history. False for a
    #: source whose backfill is "page the whole collection" — handing those a
    #: `since` silently routes them down an incremental path that was never
    #: meant to carry a backfill.
    windowed_backfill: bool = True

    def __init__(self, config: Config) -> None:
        self.config = config
        #: Set by the sync runner. A long backfill that prints nothing looks
        #: hung, and a user who cannot tell the difference will interrupt it.
        self.progress: Callable[[str, int], None] | None = None

    def report_progress(self, label: str, count: int) -> None:
        if self.progress:
            self.progress(label, count)

    @abstractmethod
    def fetch(self, since: datetime | None = None,
              until: datetime | None = None) -> list[Path]:
        """Land raw payloads. Returns the paths written."""

    @abstractmethod
    def parse(self, path: Path) -> Records:
        """Turn one landed payload into canonical records."""

    def check(self) -> tuple[bool, str]:
        """Cheap credential/connectivity check for `health doctor`."""
        return True, "no check implemented"
