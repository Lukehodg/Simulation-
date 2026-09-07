from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

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

    def __init__(self, config: Config) -> None:
        self.config = config

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
