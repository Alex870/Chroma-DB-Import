"""Reviewable dependency repair using the repository's supported launcher."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .models import BridgeError, new_id, stable_hash


SCHEMA_VERSION = "gui-environment-repair-v1"
ACTION = "install_dependencies"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _launcher(root: Path) -> Path:
    path = root / "scripts" / "Run-ChromaDbImportUi.ps1"
    if not path.is_file():
        raise BridgeError("SOURCE_UNAVAILABLE", "The supported desktop launcher is not available.")
    return path.resolve()


def build_review(root: str | Path | None = None) -> dict[str, Any]:
    repo = Path(root).expanduser().resolve() if root else repository_root()
    launcher = _launcher(repo)
    python = Path(sys.executable).resolve()
    args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher), "-InstallDependencies", "-Ui", "Modern", "-NoLaunch"]
    environment = {"python": str(python), "launcher": str(launcher), "launcher_hash": hashlib.sha256(launcher.read_bytes()).hexdigest(), "cwd": str(repo)}
    review = {
        "schema_version": SCHEMA_VERSION,
        "review_id": new_id("repair_review"),
        "action": ACTION,
        "environment": environment,
        "args": args,
        "impact": "Install or refresh the locked desktop dependencies in the current environment; no database or runtime source settings are changed.",
        "created_at": _now(),
    }
    review["review_hash"] = stable_hash(review)
    return review


def validate_review(review: Mapping[str, Any], root: str | Path | None = None) -> dict[str, Any]:
    value = dict(review)
    if value.get("schema_version") != SCHEMA_VERSION or value.get("action") != ACTION:
        raise BridgeError("VALIDATION_FAILED", "The environment repair review is unsupported or incomplete.")
    expected = build_review(root)
    expected_env = dict(expected["environment"])
    actual_env = dict(value.get("environment") or {})
    if actual_env != expected_env or list(value.get("args") or []) != list(expected["args"]):
        raise BridgeError("STALE_SETTINGS", "The environment changed after this repair was reviewed. Prepare a fresh review.")
    return value


def run_review(review: Mapping[str, Any], *, root: str | Path | None = None, runner: Callable[..., Any] | None = None) -> dict[str, Any]:
    value = validate_review(review, root)
    repo = Path(root).expanduser().resolve() if root else repository_root()
    invoke = runner or subprocess.run
    try:
        completed = invoke(["powershell.exe", *list(value["args"])], cwd=str(repo), shell=False, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise BridgeError("JOB_FAILED", f"The supported environment repair could not start: {exc}") from exc
    exit_code = int(getattr(completed, "returncode", 1))
    return {
        "review_id": value["review_id"],
        "action": value["action"],
        "status": "completed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "stdout": str(getattr(completed, "stdout", "") or "")[-4000:],
        "stderr": str(getattr(completed, "stderr", "") or "")[-4000:],
        "environment": value["environment"],
        "active_database_state": "unchanged",
    }


__all__ = ["ACTION", "SCHEMA_VERSION", "build_review", "repository_root", "run_review", "validate_review"]
