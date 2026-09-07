from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from health.config import Config
from health.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        root=tmp_path,
        timezone=ZoneInfo("Europe/London"),
        apple_export_dir=tmp_path / "phone",
    )


@pytest.fixture
def store() -> Store:
    with Store(":memory:") as store:
        store.init_schema()
        yield store


@pytest.fixture
def fixture_json():
    def _load(name: str) -> dict:
        return json.loads((FIXTURES / name).read_text())
    return _load


@pytest.fixture
def land(config):
    """Land a fixture in raw/ exactly where a real fetch would put it."""
    from health import raw as rawstore

    def _land(source: str, kind: str, name: str) -> Path:
        payload = json.loads((FIXTURES / name).read_text())
        return rawstore.write(config.raw_dir, source, kind, payload)
    return _land
