"""Per-partition process locks for managed imports."""

from __future__ import annotations

import os
from pathlib import Path


class ManagedPartitionBusy(RuntimeError):
    pass


class ManagedPartitionLock:
    """Hold a real one-byte OS lock for the lifetime of an import process."""

    def __init__(self, partition_root: Path):
        self.path = Path(partition_root) / ".import.lock"
        self._handle = None

    def acquire(self) -> "ManagedPartitionLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        if self.path.stat().st_size < 1:
            self._handle.write(b"\0")
            self._handle.flush()
        self._handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                self._handle.close()
                self._handle = None
                raise ManagedPartitionBusy(f"partition is busy: {self.path.parent.name}") from exc
        else:
            import fcntl
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self._handle.close()
                self._handle = None
                raise ManagedPartitionBusy(f"partition is busy: {self.path.parent.name}") from exc
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "ManagedPartitionLock":
        return self.acquire()

    def __exit__(self, *_args) -> None:
        self.release()


__all__ = ["ManagedPartitionBusy", "ManagedPartitionLock"]
