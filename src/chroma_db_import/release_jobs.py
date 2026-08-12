"""Resumable orchestration around the immutable corpus release store."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from .contract import validate_podcast_metadata
from .releases import ReleaseError, ReleaseStore, export_fingerprint, validate_release


def _atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _job_id(plan: Mapping[str, Any], export_dir: Path) -> str:
    payload = f"{plan['plan_id']}:{export_fingerprint(export_dir)}".encode()
    return "release_job_" + hashlib.sha256(payload).hexdigest()


def consumer_checks(export_dir: str | Path) -> dict[str, Any]:
    """Exercise the stable metadata surfaces used by Chat and RAGScope."""
    root = Path(export_dir)
    podcast = json.loads((root / "podcast.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "import_manifest.json").read_text(encoding="utf-8"))
    validation = validate_podcast_metadata(podcast)
    release_id = str(podcast.get("corpus_release_id") or "")
    checks = {
        "podcast_chat": validation.valid and bool(podcast.get("database_id")) and bool(podcast.get("collection_name")),
        "ragscope": bool(release_id) and release_id == str(manifest.get("corpus_release_id") or ""),
    }
    return {"passed": all(checks.values()), "checks": checks, "errors": list(validation.errors), "release_id": release_id}


class ReleaseJobStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, job_id: str) -> Path:
        if not job_id.startswith("release_job_") or len(job_id) != 76:
            raise ReleaseError("invalid release job ID")
        return self.root / f"{job_id}.json"

    def load(self, job_id: str) -> dict[str, Any]:
        path = self.path(job_id)
        if not path.is_file():
            raise ReleaseError(f"release job is unavailable: {job_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, job: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(job)
        value["updated_at_epoch_ms"] = int(time.time() * 1000)
        _atomic(self.path(str(value["job_id"])), value)
        return value

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.load(job_id)
        if job["status"] in {"completed", "failed"}:
            raise ReleaseError(f"cannot cancel a {job['status']} release job")
        job["cancel_requested"] = True
        job["status"] = "cancel_requested"
        return self.save(job)


def plan_job(plan: Mapping[str, Any], export_dir: str | Path, release_store: str | Path,
             job_store: ReleaseJobStore, *, reserve_factor: float = 2.1) -> dict[str, Any]:
    validate_release(plan)
    source = Path(export_dir).resolve()
    required = max(1, int(sum(p.stat().st_size for p in source.rglob("*") if p.is_file()) * reserve_factor))
    available = shutil.disk_usage(Path(release_store).resolve().parent).free
    if available < required:
        raise ReleaseError(f"insufficient disk space: required {required} bytes, available {available} bytes")
    job_id = _job_id(plan, source)
    return job_store.save({
        "contract_version": "corpus-release-job-v1", "job_id": job_id, "status": "planned",
        "cancel_requested": False, "release_store": str(Path(release_store).resolve()),
        "export_dir": str(source), "export_fingerprint": export_fingerprint(source), "plan": dict(plan),
        "preflight": {"required_bytes": required, "available_bytes": available, "reserve_factor": reserve_factor},
        "checkpoints": {"staging": False, "validation": False, "promotion": False, "consumer_checks": False},
        "created_at_epoch_ms": int(time.time() * 1000),
    })


def run_job(job_store: ReleaseJobStore, job_id: str, *, approved_plan_id: str) -> dict[str, Any]:
    job = job_store.load(job_id)
    if job.get("status") == "completed":
        return job
    if job.get("cancel_requested"):
        job["status"] = "cancelled"
        return job_store.save(job)
    plan = job["plan"]
    if approved_plan_id != plan["plan_id"]:
        raise ReleaseError("human approval does not match release plan")
    if export_fingerprint(job["export_dir"]) != job["export_fingerprint"]:
        raise ReleaseError("export changed after release preflight")
    store = ReleaseStore(job["release_store"])
    try:
        if not job["checkpoints"]["staging"]:
            store.stage_export(plan, job["export_dir"])
            job["checkpoints"]["staging"] = True
            job["status"] = "staged"
            job_store.save(job)
        if not job["checkpoints"]["validation"]:
            staged = store.staging / plan["release_id"] / "export"
            result = consumer_checks(staged)
            if not result["passed"]:
                raise ReleaseError(f"consumer pre-promotion checks failed: {result}")
            job["pre_promotion_checks"] = result
            job["checkpoints"]["validation"] = True
            job_store.save(job)
        if not job["checkpoints"]["promotion"]:
            store.promote(plan, approved_plan_id=approved_plan_id)
            job["checkpoints"]["promotion"] = True
            job["status"] = "promoted"
            job_store.save(job)
        result = consumer_checks(store.active_path())
        job["post_promotion_checks"] = result
        job["checkpoints"]["consumer_checks"] = result["passed"]
        job["status"] = "completed" if result["passed"] else "consumer_check_failed"
        job["safe_next_action"] = None if result["passed"] else f"rollback from release {plan['release_id']}"
        return job_store.save(job)
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = {"type": type(exc).__name__, "message": str(exc), "owner": "chroma-db-import"}
        job["retryable"] = not job["checkpoints"]["promotion"]
        job_store.save(job)
        raise


def backup_store(store_dir: str | Path, destination: str | Path) -> dict[str, Any]:
    source, target = Path(store_dir).resolve(), Path(destination).resolve()
    if target.exists():
        raise ReleaseError(f"backup destination already exists: {target}")
    shutil.copytree(source, target)
    manifest = {"contract_version": "corpus-release-backup-v1", "source": str(source), "active_release_id": ReleaseStore(source).active()}
    _atomic(target / "backup-manifest.json", manifest)
    return manifest


def restore_store(backup_dir: str | Path, destination: str | Path) -> dict[str, Any]:
    source, target = Path(backup_dir).resolve(), Path(destination).resolve()
    manifest_path = source / "backup-manifest.json"
    if not manifest_path.is_file() or target.exists():
        raise ReleaseError("restore requires a valid backup and a new destination")
    shutil.copytree(source, target)
    return json.loads(manifest_path.read_text(encoding="utf-8"))
