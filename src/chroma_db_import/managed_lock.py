"""Per-partition process locks for managed imports.

The lock file is deliberately only a one-byte sentinel.  Ownership is held by
the operating-system byte-range lock, while the adjacent JSON file is purely
diagnostic metadata for recovery and support tooling.
"""

from __future__ import annotations

import datetime as dt
import ctypes
import json
import os
import socket
import sys
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any


class ManagedPartitionBusy(RuntimeError):
    pass


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _process_start_time() -> float | None:
    """Return the current process start time when the optional API is present."""
    try:
        import psutil

        return float(psutil.Process(os.getpid()).create_time())
    except Exception:
        pass
    if os.name == "nt":
        try:
            class FileTime(ctypes.Structure):
                _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

            kernel32 = ctypes.WinDLL("kernel32.dll")
            kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p, ctypes.POINTER(FileTime), ctypes.POINTER(FileTime), ctypes.POINTER(FileTime), ctypes.POINTER(FileTime)]
            kernel32.GetProcessTimes.restype = ctypes.c_bool
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel32.OpenProcess(0x1000, False, os.getpid())
            if handle:
                creation = FileTime()
                exit_time = FileTime()
                kernel_time = FileTime()
                user_time = FileTime()
                try:
                    if kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
                        value = (int(creation.high) << 32) | int(creation.low)
                        return value / 10_000_000 - 11644473600
                finally:
                    kernel32.CloseHandle(handle)
        except Exception:
            pass
    return None


class ManagedPartitionLock:
    """Hold a real one-byte OS lock for the lifetime of an import process."""

    OWNER_METADATA_FILENAME = ".import.lock.owner.json"

    def __init__(self, partition_root: Path, *, run_id: str | None = None):
        self.partition_root = Path(partition_root)
        self.path = self.partition_root / ".import.lock"
        self.owner_metadata_path = self.partition_root / self.OWNER_METADATA_FILENAME
        self.run_id = str(run_id or f"lock_{uuid.uuid4().hex}")
        self._owner_token = uuid.uuid4().hex
        self._handle = None

    def acquire(self) -> "ManagedPartitionLock":
        if self._handle is not None:
            raise RuntimeError(f"partition lock is already held: {self.path.parent.name}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = None
        locked = False
        lock_attempted = False
        try:
            try:
                handle = self.path.open("a+b")
            except PermissionError as exc:
                # Windows may deny the second open before msvcrt.locking is
                # reached when another process owns the byte range.
                if os.name == "nt":
                    raise ManagedPartitionBusy(f"partition is busy: {self.path.parent.name}") from exc
                raise

            if self.path.stat().st_size < 1:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            lock_attempted = True
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            self._handle = handle
            self._write_owner_metadata()
        except ManagedPartitionBusy:
            self._close_handle(handle, locked=locked)
            raise
        except OSError as exc:
            self._close_handle(handle, locked=locked)
            if lock_attempted:
                raise ManagedPartitionBusy(f"partition is busy: {self.path.parent.name}") from exc
            raise
        except BaseException:
            self._close_handle(handle, locked=locked)
            raise
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        handle = self._handle
        self._handle = None
        try:
            self._unlock(handle)
        finally:
            handle.close()
            self._remove_owner_metadata()

    def _write_owner_metadata(self) -> None:
        process_name = Path(sys.executable).name
        try:
            import psutil

            process_name = str(psutil.Process(os.getpid()).name() or process_name)
        except Exception:
            pass
        payload: dict[str, Any] = {
            "schema_version": "managed-partition-lock-owner-v1",
            "owner_token": self._owner_token,
            "pid": os.getpid(),
            "process_start_time": _process_start_time(),
            "process_name": process_name,
            "partition_path": str(self.partition_root.resolve()),
            "lock_path": str(self.path.resolve()),
            "run_id": self.run_id,
            "acquired_at": _utc_now(),
            "status": "active",
            "host": socket.gethostname(),
        }
        # Metadata must never turn a valid OS lock into a failed import.  A
        # missing or unreadable sidecar is handled as an untracked owner by
        # repair tooling.
        temporary: Path | None = None
        try:
            temporary = self.owner_metadata_path.with_name(self.owner_metadata_path.name + f".{uuid.uuid4().hex}.tmp")
            temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.owner_metadata_path)
        except OSError:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink()

    def _remove_owner_metadata(self) -> None:
        try:
            payload = json.loads(self.owner_metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, dict) and payload.get("owner_token") == self._owner_token:
            with suppress(OSError):
                self.owner_metadata_path.unlink()

    def _unlock(self, handle) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _close_handle(self, handle, *, locked: bool) -> None:
        if handle is None:
            return
        if locked:
            with suppress(BaseException):
                self._unlock(handle)
        with suppress(BaseException):
            handle.close()

    def __enter__(self) -> "ManagedPartitionLock":
        return self.acquire()

    def __exit__(self, *_args) -> None:
        self.release()


__all__ = ["ManagedPartitionBusy", "ManagedPartitionLock"]
