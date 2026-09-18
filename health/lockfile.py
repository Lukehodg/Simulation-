"""One process at a time, across platforms.

`flock` on POSIX, `msvcrt.locking` on Windows, the same two calls either
way. Used by the sync (two syncs must not write the database at once) and by
OAuth token refresh (two refreshes with the same rotating refresh token get
the second one revoked, and the whole grant with it).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:                       # POSIX
    import fcntl

    def take(handle, *, wait: bool) -> None:
        flags = fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB)
        fcntl.flock(handle, flags)

    def release(handle) -> None:
        fcntl.flock(handle, fcntl.LOCK_UN)

except ModuleNotFoundError:  # Windows: same semantics, different call
    import msvcrt
    import time

    def take(handle, *, wait: bool) -> None:
        mode = msvcrt.LK_NBLCK
        deadline = time.monotonic() + 60
        while True:
            try:
                msvcrt.locking(handle.fileno(), mode, 1)
                return
            except OSError as exc:
                if not wait or time.monotonic() > deadline:
                    raise BlockingIOError(str(exc)) from None
                time.sleep(0.2)

    def release(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def held(path: Path, *, wait: bool = True) -> Iterator[None]:
    """Hold the lock at `path` for the block. With `wait=False` a held lock
    raises BlockingIOError immediately instead of queueing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        take(handle, wait=wait)
        yield
    finally:
        try:
            release(handle)
        except OSError:
            pass
        finally:
            handle.close()
