"""Backing up the part that cannot be re-downloaded.

The database is disposable — `health replay` rebuilds every table from raw/.
What cannot be rebuilt is raw/ itself: WHOOP will not serve two-year-old
records forever, Garmin exports are rate-limited and manual, and a lab report
you deleted from your downloads folder is simply gone. So that is what this
archives, with a manifest describing what is inside it.

Restores refuse to overwrite by default, and every path in an archive is
checked before extraction — a tar file is a list of paths someone else wrote,
and "../" is a valid path.
"""

from __future__ import annotations

import json
import tarfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import Config

MANIFEST = "manifest.json"


@dataclass
class Backup:
    path: Path
    files: int
    bytes: int
    sources: dict[str, int]

    def describe(self) -> str:
        size = self.bytes / 1_048_576
        detail = ", ".join(f"{n} {source}" for source, n in sorted(self.sources.items()))
        return f"{self.files} payload(s), {size:.1f} MB uncompressed · {detail}"


def survey(config: Config) -> tuple[list[Path], Counter]:
    """Every raw payload, and where each came from."""
    if not config.raw_dir.exists():
        return [], Counter()
    files = [p for p in sorted(config.raw_dir.rglob("*")) if p.is_file()]
    sources = Counter(p.relative_to(config.raw_dir).parts[0] for p in files)
    return files, sources


def create(config: Config, destination: Path | None = None) -> Backup:
    """Archive raw/ with a manifest. Returns what went in."""
    files, sources = survey(config)
    if not files:
        raise ValueError("nothing in raw/ to back up yet")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    destination = Path(destination or config.root / "backups").expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"health-raw-{stamp}.tar.gz"

    total = sum(p.stat().st_size for p in files)
    manifest = {
        "created": datetime.now(timezone.utc).isoformat(),
        "health_version": __version__,
        "files": len(files),
        "bytes": total,
        "sources": dict(sources),
        "note": "raw payloads only — the database rebuilds from these with "
                "`health replay`. Contains no credentials.",
    }

    with tarfile.open(archive, "w:gz") as tar:
        for path in files:
            tar.add(path, arcname=str(Path("raw") / path.relative_to(config.raw_dir)))
        manifest_bytes = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo(MANIFEST)
        info.size = len(manifest_bytes)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
        import io
        tar.addfile(info, io.BytesIO(manifest_bytes))

    return Backup(path=archive, files=len(files), bytes=total, sources=dict(sources))


def read_manifest(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.extractfile(MANIFEST)
        except KeyError:
            member = None
        if member is None:
            raise ValueError(f"{archive.name} has no manifest — not a health backup")
        return json.loads(member.read())


def _safe_members(tar: tarfile.TarFile, root: Path):
    """Refuse anything that would land outside the destination.

    Archive paths are attacker-controlled in the general case, and absolute
    paths, "..", symlinks and device files are all legal tar entries.
    """
    for member in tar.getmembers():
        if member.name == MANIFEST:
            continue
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"{member.name} is not a regular file or directory")
        target = (root / member.name).resolve()
        if not str(target).startswith(str(root.resolve())):
            raise ValueError(f"{member.name} would extract outside {root}")
        yield member


def restore(archive: Path, config: Config, force: bool = False) -> dict:
    """Unpack an archive back into raw/. Will not overwrite unless told to."""
    manifest = read_manifest(archive)
    existing, _ = survey(config)
    if existing and not force:
        raise ValueError(
            f"raw/ already holds {len(existing)} payload(s). Restoring would "
            f"merge into it — pass --force if that is what you want."
        )

    config.data_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = list(_safe_members(tar, config.data_dir))
        for member in members:
            tar.extract(member, path=config.data_dir)

    return {"restored": len([m for m in members if m.isfile()]),
            "from": manifest.get("created"), "manifest": manifest}
