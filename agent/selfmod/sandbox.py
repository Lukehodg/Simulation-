"""Containment rules for an agent that is allowed to delete itself.

Every destructive operation in this package goes through :class:`Sandbox`.
The rules are deliberately paranoid, because the caller is code that has just
decided to remove its own source tree:

* a target must resolve to a real directory inside the workspace root;
* it must not *be* the workspace root, and must not be a symlink;
* it must carry the generation marker file that the orchestrator writes, so
  only directories this package created can ever be removed;
* a ``.protected`` marker vetoes deletion outright;
* nothing outside the workspace is deletable, which keeps the pristine source
  tree in the repository immortal.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

MARKER = ".selfmod-generation"
PROTECT = ".protected"


class SandboxError(Exception):
    """Raised when a destructive request violates a containment rule."""


@dataclass
class DeletionReport:
    target: str
    performed: bool
    files: int = 0
    bytes: int = 0
    reason: str = ""
    refusal: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "performed": self.performed,
            "files": self.files,
            "bytes": self.bytes,
            "reason": self.reason,
            "refusal": self.refusal,
            "notes": list(self.notes),
        }


class Sandbox:
    """A single directory that destructive operations may never leave."""

    def __init__(self, root: Path):
        root = Path(root).expanduser()
        if not root.is_dir():
            raise SandboxError(f"sandbox root does not exist: {root}")
        self.root = root.resolve(strict=True)

    def contains(self, path: Path) -> bool:
        """True when ``path`` resolves to something strictly inside the root."""
        try:
            resolved = Path(path).resolve(strict=False)
        except OSError:
            return False
        return resolved != self.root and self.root in resolved.parents

    def require_inside(self, path: Path) -> Path:
        resolved = Path(path).resolve(strict=False)
        if not self.contains(resolved):
            raise SandboxError(
                f"refusing to touch {resolved}: outside sandbox {self.root}"
            )
        return resolved

    def check_deletable(self, path: Path) -> str | None:
        """Return the reason ``path`` may not be deleted, or ``None`` if it may."""
        raw = Path(path)
        if raw.is_symlink():
            return f"{raw} is a symlink"
        try:
            resolved = raw.resolve(strict=True)
        except OSError:
            return f"{raw} does not exist"
        if resolved == self.root:
            return "target is the sandbox root itself"
        if not self.contains(resolved):
            return f"{resolved} is outside sandbox {self.root}"
        if not resolved.is_dir():
            return f"{resolved} is not a directory"
        if not (resolved / MARKER).is_file():
            return f"{resolved} carries no {MARKER} marker"
        if (resolved / PROTECT).exists():
            return f"{resolved} is marked {PROTECT}"
        return None

    def measure(self, path: Path) -> tuple[int, int]:
        files = 0
        total = 0
        for item in Path(path).rglob("*"):
            if item.is_file() and not item.is_symlink():
                files += 1
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
        return files, total

    def delete_tree(self, path: Path, *, armed: bool, reason: str = "") -> DeletionReport:
        """Delete a generation directory, or explain why it was refused.

        With ``armed=False`` the call is a rehearsal: every check runs and the
        report is filled in, but nothing is removed.
        """
        target = Path(path)
        refusal = self.check_deletable(target)
        if refusal is not None:
            return DeletionReport(
                target=str(target), performed=False, reason=reason, refusal=refusal
            )

        resolved = target.resolve(strict=True)
        files, size = self.measure(resolved)
        report = DeletionReport(
            target=str(resolved),
            performed=False,
            files=files,
            bytes=size,
            reason=reason,
        )
        if not armed:
            report.notes.append("dry run: directory left in place")
            return report

        shutil.rmtree(resolved)
        report.performed = True
        return report
