"""Explicit recovery for a managed partition whose OS lock is still held.

This module never removes ``.import.lock`` and never opens a database for
writing.  It only releases an abandoned OS handle by stopping the exact
verified process returned for that lock resource.
"""

from __future__ import annotations

import ctypes
import datetime as dt
import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .managed_lock import ManagedPartitionBusy, ManagedPartitionLock


@dataclass(frozen=True)
class LockOwner:
    pid: int
    name: str = ""
    executable: str = ""
    command_line: str = ""
    process_start_time: float | None = None
    application_name: str = ""
    importer_like: bool = False
    target_verified: bool = False
    status: str = "untracked"
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "executable": self.executable,
            "command_line": self.command_line,
            "process_start_time": self.process_start_time,
            "application_name": self.application_name,
            "importer_like": self.importer_like,
            "target_verified": self.target_verified,
            "status": self.status,
            "reason": self.reason,
        }


class OwnerResolver(Protocol):
    def __call__(self, lock_path: Path, partition_root: Path, metadata: dict[str, Any] | None) -> list[LockOwner]: ...


class ProcessController(Protocol):
    def is_alive(self, pid: int) -> bool: ...

    def request_graceful_stop(self, pid: int) -> bool: ...

    def force_terminate(self, pid: int) -> bool: ...


def _read_metadata(partition_root: Path) -> dict[str, Any] | None:
    path = partition_root / ManagedPartitionLock.OWNER_METADATA_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _process_details(pid: int) -> tuple[str, str, str, float | None]:
    try:
        import psutil

        process = psutil.Process(pid)
        with process.oneshot():
            return (
                str(process.name() or ""),
                str(process.exe() or ""),
                " ".join(str(item) for item in process.cmdline()),
                float(process.create_time()),
            )
    except Exception:
        return "", "", "", None


def _looks_like_importer(name: str, executable: str, command_line: str, application_name: str) -> bool:
    haystack = " ".join((name, executable, command_line, application_name)).lower()
    return any(
        marker in haystack
        for marker in (
            "chroma_db_import",
            "chroma db import",
            "redundancy_cli",
            "run-chromadbimport",
            "chroma-db-import",
            "chromadbimport",
        )
    )


def _file_owner_pids(lock_path: Path) -> list[int]:
    """Use the native Windows file-owner query when Restart Manager is empty."""
    if os.name != "nt":
        return []
    try:
        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.GetFileInformationByHandleEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        kernel32.GetFileInformationByHandleEx.restype = ctypes.c_bool
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.CreateFileW(str(lock_path), 0x80, 0x7, None, 3, 0, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            return []
        try:
            # FILE_PROCESS_IDS_USING_FILE_INFORMATION = 47.
            buffer = ctypes.create_string_buffer(16 * 1024)
            if not kernel32.GetFileInformationByHandleEx(handle, 47, buffer, len(buffer)):
                return []
            count = int.from_bytes(buffer.raw[:4], "little")
            offset = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 4
            width = ctypes.sizeof(ctypes.c_void_p)
            return [int.from_bytes(buffer.raw[offset + index * width: offset + (index + 1) * width], "little") for index in range(count)]
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return []


def _owners_from_pids(pids: list[int], partition_root: Path, metadata: dict[str, Any] | None) -> list[LockOwner]:
    expected_path = str(partition_root.resolve()).lower()
    metadata_pid = int(metadata.get("pid")) if metadata and str(metadata.get("pid") or "").isdigit() else None
    owners: list[LockOwner] = []
    for pid in dict.fromkeys(pids):
        name, executable, command_line, start_time = _process_details(pid)
        target_verified = metadata_pid == pid and bool(metadata and str(metadata.get("partition_path") or "").lower() == expected_path)
        importer_like = _looks_like_importer(name, executable, command_line, "")
        status = "untracked"
        if target_verified and metadata:
            recorded_start = metadata.get("process_start_time")
            if recorded_start is None or start_time is None or abs(float(recorded_start) - start_time) < 2.0:
                status = str(metadata.get("status") or "untracked").lower()
        owners.append(LockOwner(pid, name, executable, command_line, start_time, "", importer_like, True, status, "Windows reports this PID as using the exact lock file"))
    return owners


def _metadata_owner(lock_path: Path, partition_root: Path, metadata: dict[str, Any] | None) -> list[LockOwner]:
    if not metadata:
        return []
    try:
        pid = int(metadata.get("pid"))
    except (TypeError, ValueError):
        return []
    process_name, executable, command_line, start_time = _process_details(pid)
    expected_path = str(partition_root.resolve()).lower()
    recorded_path = str(metadata.get("partition_path") or "").lower()
    target_verified = recorded_path == expected_path
    recorded_start = metadata.get("process_start_time")
    if recorded_start is not None and start_time is not None:
        target_verified = target_verified and abs(float(recorded_start) - start_time) < 2.0
    status = str(metadata.get("status") or "untracked").strip().lower()
    if status not in {"active", "completed", "failed", "untracked"}:
        status = "untracked"
    importer_like = _looks_like_importer(process_name, executable, command_line, "")
    reason = "owner metadata matches the target partition"
    if not target_verified:
        reason = "owner metadata does not match the current process identity or partition"
    return [LockOwner(pid, process_name, executable, command_line, start_time, "", importer_like, target_verified, status, reason)]


def _restart_manager_owners_from_api(lock_path: Path, partition_root: Path, metadata: dict[str, Any] | None) -> list[LockOwner]:
    """Return processes reported by Windows Restart Manager for this file."""
    if os.name != "nt":
        return _metadata_owner(lock_path, partition_root, metadata)
    try:
        rms = ctypes.WinDLL("rstrtmgr.dll")
        rms.RmStartSession.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32, ctypes.c_wchar_p]
        rms.RmStartSession.restype = ctypes.c_uint32
        rms.RmRegisterResources.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        rms.RmRegisterResources.restype = ctypes.c_uint32
        rms.RmGetList.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        rms.RmGetList.restype = ctypes.c_uint32
        rms.RmEndSession.argtypes = [ctypes.c_uint32]
        rms.RmEndSession.restype = ctypes.c_uint32
    except OSError:
        return _metadata_owner(lock_path, partition_root, metadata)

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", ctypes.c_uint32), ("ProcessStartTime", FILETIME)]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [
            ("Process", RM_UNIQUE_PROCESS),
            ("strAppName", ctypes.c_wchar * 256),
            ("strServiceShortName", ctypes.c_wchar * 64),
            ("ApplicationType", ctypes.c_uint32),
            ("AppStatus", ctypes.c_uint32),
            ("TSSessionId", ctypes.c_uint32),
            ("bRestartable", ctypes.c_bool),
        ]

    session = ctypes.c_uint32()
    key = ctypes.create_unicode_buffer(33)
    if rms.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return _metadata_owner(lock_path, partition_root, metadata)
    try:
        resource = ctypes.c_wchar_p(str(lock_path.resolve()))
        resources = (ctypes.c_wchar_p * 1)(resource)
        if rms.RmRegisterResources(session, 1, resources, 0, None, 0, None) != 0:
            return _metadata_owner(lock_path, partition_root, metadata)
        needed = ctypes.c_uint32()
        count = ctypes.c_uint32()
        reasons = ctypes.c_uint32()
        result = rms.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
        if result not in (0, 234):  # ERROR_MORE_DATA
            return _metadata_owner(lock_path, partition_root, metadata)
        count.value = needed.value
        if not count.value:
            return []
        entries = (RM_PROCESS_INFO * count.value)()
        if rms.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), entries, ctypes.byref(reasons)) != 0:
            return _metadata_owner(lock_path, partition_root, metadata)
        owners: list[LockOwner] = []
        expected_path = str(partition_root.resolve()).lower()
        metadata_pid = int(metadata.get("pid")) if metadata and str(metadata.get("pid") or "").isdigit() else None
        for entry in entries[: count.value]:
            pid = int(entry.Process.dwProcessId)
            name, executable, command_line, start_time = _process_details(pid)
            app_name = str(entry.strAppName or "")
            # Restart Manager has already tied this PID to the exact lock
            # file.  The metadata match is useful for status classification,
            # but it is not needed to prove the target partition.
            target_verified = True
            importer_like = _looks_like_importer(name, executable, command_line, app_name)
            status = "untracked"
            reason = "Windows Restart Manager reports this PID owns the exact lock resource"
            if metadata_pid == pid and metadata and str(metadata.get("partition_path") or "").lower() == expected_path:
                recorded_start = metadata.get("process_start_time")
                if recorded_start is None or start_time is None or abs(float(recorded_start) - start_time) < 2.0:
                    status = str(metadata.get("status") or "untracked").lower()
            owners.append(LockOwner(pid, name, executable, command_line, start_time, app_name, importer_like, target_verified, status, reason))
        return owners
    finally:
        rms.RmEndSession(session)


def _restart_manager_owners(lock_path: Path, partition_root: Path, metadata: dict[str, Any] | None) -> list[LockOwner]:
    owners = _restart_manager_owners_from_api(lock_path, partition_root, metadata)
    if owners:
        return owners
    pids = _file_owner_pids(lock_path)
    return _owners_from_pids(pids, partition_root, metadata) if pids else []


class _WindowsProcessController:
    def is_alive(self, pid: int) -> bool:
        try:
            import psutil

            return psutil.pid_exists(pid)
        except Exception:
            if pid <= 0:
                return False
            try:
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if not handle:
                    return False
                code = ctypes.c_uint32()
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                ctypes.windll.kernel32.CloseHandle(handle)
                return code.value == 259  # STILL_ACTIVE
            except Exception:
                return False

    def request_graceful_stop(self, pid: int) -> bool:
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
            return True
        except (OSError, AttributeError, ValueError):
            return False

    def force_terminate(self, pid: int) -> bool:
        try:
            import psutil

            process = psutil.Process(pid)
            process.kill()
            return True
        except Exception:
            try:
                handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
                if not handle:
                    return False
                result = bool(ctypes.windll.kernel32.TerminateProcess(handle, 1))
                ctypes.windll.kernel32.CloseHandle(handle)
                return result
            except Exception:
                return False


def _try_lock(partition_root: Path) -> bool:
    try:
        with ManagedPartitionLock(partition_root, run_id="lock-repair-probe"):
            return True
    except ManagedPartitionBusy:
        return False


def _wait_for_release(partition_root: Path, controller: ProcessController, pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if _try_lock(partition_root):
            return True
        if time.monotonic() >= deadline:
            return False
        if not controller.is_alive(pid) and _try_lock(partition_root):
            return True
        time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))


def repair_partition_lock(
    partition_root: Path,
    *,
    confirm: bool = False,
    grace_timeout: float = 10.0,
    owner_resolver: OwnerResolver | None = None,
    process_controller: ProcessController | None = None,
) -> dict[str, Any]:
    """Repair one physical partition without deleting its lock sentinel.

    ``confirm`` is required before a live process is asked to stop.  An owner
    is eligible only when it is an importer runtime whose identity is verified
    for the exact lock resource, and whose metadata is not explicitly active.
    """
    root = Path(partition_root).expanduser().resolve()
    lock_path = root / ".import.lock"
    metadata = _read_metadata(root)
    if _try_lock(root):
        return {
            "status": "already_available",
            "partition_path": str(root),
            "lock_path": str(lock_path),
            "lock_file_preserved": lock_path.exists(),
            "owner": None,
            "reason": "The OS lock was not held.",
        }

    resolver = owner_resolver or _restart_manager_owners
    owners = list(resolver(lock_path, root, metadata))
    controller = process_controller or _WindowsProcessController()
    result_base: dict[str, Any] = {
        "partition_path": str(root),
        "lock_path": str(lock_path),
        "lock_file_preserved": lock_path.exists(),
        "owners": [owner.as_dict() for owner in owners],
    }
    if not owners:
        return {**result_base, "status": "verification_failed", "reason": "The OS lock is held, but its owning process could not be verified."}
    if len(owners) != 1:
        return {**result_base, "status": "blocked_active_owner", "reason": "The lock has an ambiguous owner set; no process was terminated."}
    owner = owners[0]
    if not (owner.importer_like and owner.target_verified):
        return {**result_base, "status": "blocked_active_owner", "reason": "The exact owner is not verified as the Chroma DB Import runtime for this partition."}
    if owner.status == "active":
        return {**result_base, "status": "blocked_active_owner", "reason": "The owner metadata marks this as an active import; automatic termination was refused."}
    if owner.status not in {"completed", "failed", "untracked"}:
        return {**result_base, "status": "blocked_active_owner", "reason": f"The owner status is ambiguous ({owner.status}); automatic termination was refused."}
    if not confirm:
        return {**result_base, "status": "blocked_active_owner", "reason": "Explicit confirmation is required before stopping the verified stale importer owner."}

    graceful_requested = controller.request_graceful_stop(owner.pid)
    if _wait_for_release(root, controller, owner.pid, grace_timeout):
        return {
            **result_base,
            "status": "released",
            "owner": owner.as_dict(),
            "graceful_stop_requested": graceful_requested,
            "forced": False,
            "reason": "The verified owner released the OS lock after a graceful stop request.",
        }
    forced = controller.force_terminate(owner.pid)
    if forced and _wait_for_release(root, controller, owner.pid, grace_timeout):
        return {
            **result_base,
            "status": "owner_terminated",
            "owner": owner.as_dict(),
            "graceful_stop_requested": graceful_requested,
            "forced": True,
            "reason": "The verified owner did not release the lock gracefully and was force-terminated.",
        }
    return {
        **result_base,
        "status": "verification_failed",
        "owner": owner.as_dict(),
        "graceful_stop_requested": graceful_requested,
        "forced": forced,
        "reason": "The verified owner did not release the OS lock after the permitted recovery actions.",
    }


__all__ = ["LockOwner", "repair_partition_lock"]
