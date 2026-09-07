"""Credential lookup.

Order: environment (and .env), then the macOS Keychain, then a 0600 file in
~/.config/health. Nothing is ever read from, or written to, the repository.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path

KEYCHAIN_ACCOUNT = "health"


class MissingSecret(RuntimeError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"{name} is not set. Add it with one of:\n"
            f"  security add-generic-password -a {KEYCHAIN_ACCOUNT} -s {name} -w 'value'\n"
            f"  echo '{name}=value' >> .env"
        )
        self.name = name


def _keychain_get(name: str) -> str | None:
    if platform.system() != "Darwin":
        return None
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", name, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _file_path(config_dir: Path) -> Path:
    return config_dir / "secrets.json"


def _file_get(config_dir: Path, name: str) -> str | None:
    path = _file_path(config_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text()).get(name)
    except (json.JSONDecodeError, OSError):
        return None


def get_secret(name: str, config_dir: Path, required: bool = True) -> str | None:
    value = os.environ.get(name) or _keychain_get(name) or _file_get(config_dir, name)
    if not value and required:
        raise MissingSecret(name)
    return value


def set_secret(name: str, value: str, config_dir: Path) -> str:
    """Store a secret. Returns where it went, for the caller to report."""
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["security", "add-generic-password", "-U", "-a", KEYCHAIN_ACCOUNT,
             "-s", name, "-w", value],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return "macOS Keychain"

    config_dir.mkdir(parents=True, exist_ok=True)
    path = _file_path(config_dir)
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            data = {}
    data[name] = value
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)
    return str(path)


def read_tokens(config_dir: Path, source: str) -> dict | None:
    path = config_dir / "tokens" / f"{source}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_tokens(config_dir: Path, source: str, tokens: dict) -> Path:
    directory = config_dir / "tokens"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / f"{source}.json"
    path.write_text(json.dumps(tokens, indent=2))
    path.chmod(0o600)
    return path
