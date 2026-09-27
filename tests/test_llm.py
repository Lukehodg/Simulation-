from __future__ import annotations

from zoneinfo import ZoneInfo

from health import llm
from health.config import Config


def _config(tmp_path):
    return Config(root=tmp_path, timezone=ZoneInfo("Europe/London"), apple_export_dir=None)


def test_a_workspace_id_travels_as_a_header(tmp_path, monkeypatch):
    """An organisation-level key is refused without it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_123")

    client = llm.client(_config(tmp_path))

    assert client.default_headers[llm.WORKSPACE_HEADER] == "wrkspc_123"


def test_no_workspace_id_sends_no_header(tmp_path, monkeypatch):
    """A workspace-scoped key needs nothing extra."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)

    client = llm.client(_config(tmp_path))

    assert llm.WORKSPACE_HEADER not in client.default_headers
