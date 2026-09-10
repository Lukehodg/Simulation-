"""The one place an Anthropic client is built.

Every call that leaves the machine — the blood-panel reading in `analysis.py`
and the daily brief in `brief.py` — goes through here, so the key is looked up
in exactly one way and the boundary is easy to audit.
"""

from __future__ import annotations

from .config import Config
from .secrets import get_secret


def client(config: Config):
    """An `anthropic.Anthropic`, keyed from the environment, the Keychain, or
    the 0600 secrets file — the same order as every other credential."""
    import anthropic

    key = get_secret("ANTHROPIC_API_KEY", config.config_dir, required=False)
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
