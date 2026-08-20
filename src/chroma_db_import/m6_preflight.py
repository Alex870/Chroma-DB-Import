"""Redacted clean-machine diagnostics for M6 operations."""

from __future__ import annotations
import ctypes, hashlib, importlib.util, json, os, platform, shutil, sys, tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

CONTRACT_VERSION = "runtime-preflight-1.0"
DEPENDENCY_COMMAND = "python -m pip install -r chroma_db_import_requirements.txt"


def _hash(v):
    return hashlib.sha256(
        json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _item(name, kind, required, ready):
    tool_commands = {
        "node": "winget install --id OpenJS.NodeJS.LTS -e",
        "ffmpeg": "winget install --id Gyan.FFmpeg -e",
    }
    return {
        "capability": name,
        "kind": kind,
        "requirement": "required" if required else "optional",
        "status": "available" if ready else "unavailable",
        "remediation": (
            None if ready else f"Install the pinned dependency providing {name}."
        ),
        "remediation_command": (
            None
            if ready
            else tool_commands.get(name) if kind == "executable" else DEPENDENCY_COMMAND
        ),
    }


def _module_ready(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _total_memory_bytes():
    try:
        if hasattr(os, "sysconf"):
            return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total_physical)
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return None


def build_preflight(
    component: str,
    workspace: Path,
    *,
    required_tools: Iterable[str] = (),
    optional_tools: Iterable[str] = (),
    required_modules: Iterable[str] = (),
    optional_modules: Iterable[str] = (),
    minimum_free_bytes: int = 2_000_000_000,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(workspace)
    writable = False
    try:
        with tempfile.NamedTemporaryFile(
            dir=workspace, prefix=".m6-write-", delete=True
        ):
            writable = True
    except OSError:
        pass
    caps = [
        {
            "capability": "python",
            "kind": "runtime",
            "requirement": "required",
            "status": "available" if sys.version_info >= (3, 10) else "incompatible",
            "version": platform.python_version(),
            "remediation": (
                None if sys.version_info >= (3, 10) else "Install Python 3.10 or newer."
            ),
            "remediation_command": (
                None
                if sys.version_info >= (3, 10)
                else "winget install --id Python.Python.3.12 -e"
            ),
        },
        *(_item(n, "executable", True, bool(shutil.which(n))) for n in required_tools),
        *(_item(n, "executable", False, bool(shutil.which(n))) for n in optional_tools),
        *(_item(n, "python_module", True, _module_ready(n)) for n in required_modules),
        *(_item(n, "python_module", False, _module_ready(n)) for n in optional_modules),
    ]
    blockers = []
    if not writable:
        blockers.append("workspace_not_writable")
    if disk.free < minimum_free_bytes:
        blockers.append("insufficient_free_disk")
    blockers.extend(
        f"missing_required:{x['capability']}"
        for x in caps
        if x["requirement"] == "required" and x["status"] != "available"
    )
    value = {
        "contract_version": CONTRACT_VERSION,
        "component": component,
        "profile": {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "total_memory_bytes": _total_memory_bytes(),
        },
        "workspace": {
            "label": workspace.name,
            "writable": writable,
            "free_bytes": disk.free,
            "minimum_free_bytes": minimum_free_bytes,
        },
        "capabilities": caps,
        "offline": {"network_probe_performed": False, "implicit_downloads": False},
        "blockers": blockers,
        "supported": not blockers,
        "redaction": {
            "absolute_paths_omitted": True,
            "environment_values_omitted": True,
            "credentials_omitted": True,
        },
    }
    value["report_id"] = "preflight_" + _hash(value)
    return value


def write_report(path: Path, report: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return path
