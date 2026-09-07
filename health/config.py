"""Configuration and paths.

Everything the app writes lives under one project directory so that backing it
up (or deleting it) is a single operation. Secrets never live there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/London"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader. Existing environment variables always win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Config:
    root: Path
    timezone: ZoneInfo
    apple_export_dir: Path | None

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "health.duckdb"

    @property
    def config_dir(self) -> Path:
        """Credentials and tokens — outside the project, never in git."""
        return Path(os.environ.get("HEALTH_CONFIG_DIR", "~/.config/health")).expanduser()

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.config_dir.chmod(0o700)
        except OSError:
            pass


def load_config(root: Path | None = None) -> Config:
    root = Path(root or os.environ.get("HEALTH_ROOT") or Path.cwd()).expanduser().resolve()
    _load_dotenv(root / ".env")

    tz_name = os.environ.get("HEALTH_TIMEZONE", DEFAULT_TIMEZONE)
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:  # a typo here would silently shift every date
        raise SystemExit(f"HEALTH_TIMEZONE={tz_name!r} is not a valid timezone: {exc}")

    raw_export_dir = os.environ.get("HEALTH_APPLE_EXPORT_DIR")
    export_dir = Path(raw_export_dir).expanduser() if raw_export_dir else None

    return Config(root=root, timezone=tz, apple_export_dir=export_dir)
