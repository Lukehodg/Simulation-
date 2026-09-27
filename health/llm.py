"""The one place an Anthropic client is built.

Every call that leaves the machine — the blood-panel reading in `analysis.py`
and the daily brief in `brief.py` — goes through here, so the key is looked up
in exactly one way and the boundary is easy to audit.
"""

from __future__ import annotations

from .config import Config
from .secrets import get_secret


#: Sent only when set. A key created at the organisation level rather than
#: inside a workspace is refused without it — "This API key is not scoped to a
#: workspace" — and a workspace-scoped key does not need it.
WORKSPACE_HEADER = "anthropic-workspace-id"


def client(config: Config):
    """An `anthropic.Anthropic`, keyed from the environment, the Keychain, or
    the 0600 secrets file — the same order as every other credential."""
    import anthropic

    key = get_secret("ANTHROPIC_API_KEY", config.config_dir, required=False)
    workspace = get_secret("ANTHROPIC_WORKSPACE_ID", config.config_dir, required=False)
    kwargs = {"default_headers": {WORKSPACE_HEADER: workspace}} if workspace else {}
    return anthropic.Anthropic(api_key=key, **kwargs) if key else anthropic.Anthropic(**kwargs)
