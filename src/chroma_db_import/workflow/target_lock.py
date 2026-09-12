from __future__ import annotations

import os
from pathlib import Path

from .models import BridgeError


def target_lock_path(target: Path) -> Path:
    """Return a stable sibling lock path for a database target."""
    target = Path(target)
    return target.parent / f".{target.name}.gui.lock"


class TargetLock:
    """Hold an OS-backed lock that survives target-folder replacement."""

    def __init__(self, target: Path, operation_id: str) -> None:
        self.path = target_lock_path(target)
        self.operation_id = operation_id
        self._handle = None

    def acquire(self) -> "TargetLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if self.path.stat().st_size < 1:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            handle.close()
            raise BridgeError("TARGET_BUSY", "This database is being used by another operation. Wait for it to finish.") from exc
        self._handle = handle
        try:
            handle.seek(0)
            handle.write(self.operation_id.encode("ascii", errors="ignore")[:256])
            handle.flush()
        except OSError:
            # The lock itself remains valid even when a diagnostic owner ID
            # cannot be written, so cleanup must still release it normally.
            pass
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        handle = self._handle
        self._handle = None
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "TargetLock":
        return self.acquire()

    def __exit__(self, *_args: object) -> None:
        self.release()


__all__ = ["TargetLock", "target_lock_path"]
