from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .catalog import AppCatalog, normalize_target
from .folder_adapter import FolderAdapter
from .models import BridgeError, FrozenPreview, JobRecord, utc_now
from .recovery import recover_interrupted as inspect_recovery
from .target_lock import TargetLock, target_lock_path


class JobService:
    def __init__(self, catalog: AppCatalog, folder_adapter: FolderAdapter, service: Any) -> None:
        self.catalog = catalog
        self.folder_adapter = folder_adapter
        self.service = service
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chroma-gui-job")
        self._futures: dict[str, Any] = {}
        self._lock = threading.RLock()

    def submit_import(self, database_id: str, preview_id: str, *, retry_failed: bool = False) -> dict[str, Any]:
        preview = FrozenPreview.from_mapping(self.catalog.get_preview(preview_id))
        job = self.catalog.create_or_get_job(kind="import", database_id=database_id, preview_id=preview_id, payload={"preview_id": preview_id}, can_cancel=False, retry_failed=retry_failed)
        if job["state"] == "queued":
            with self._lock:
                future = self._futures.get(job["id"])
                if future is None or future.done():
                    self._futures[job["id"]] = self.executor.submit(self._run_import, job["id"], database_id, preview)
        return job

    def submit_preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        job = self.catalog.create_or_get_job(kind="preview", database_id=payload.get("database_id"), preview_id=None, payload=dict(payload), can_cancel=True)
        if job["state"] == "queued":
            with self._lock:
                self._futures.setdefault(job["id"], self.executor.submit(self._run_preview, job["id"], dict(payload)))
        return job

    def submit_scan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        job = self.catalog.create_or_get_job(kind="scan", database_id=payload.get("database_id"), preview_id=None, payload=dict(payload), can_cancel=True)
        if job["state"] == "queued":
            with self._lock:
                self._futures.setdefault(job["id"], self.executor.submit(self._run_scan, job["id"], dict(payload)))
        return job

    def submit_inspection(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        job = self.catalog.create_or_get_job(kind="inspection", database_id=payload.get("database_id"), preview_id=None, payload=dict(payload), can_cancel=True)
        if job["state"] == "queued":
            with self._lock:
                self._futures.setdefault(job["id"], self.executor.submit(self._run_inspection, job["id"], dict(payload)))
        return job

    def submit_validation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        job = self.catalog.create_or_get_job(kind="validation", database_id=payload.get("database_id"), preview_id=None, payload=dict(payload), can_cancel=True)
        if job["state"] == "queued":
            with self._lock:
                self._futures.setdefault(job["id"], self.executor.submit(self._run_validation, job["id"], dict(payload)))
        return job

    def submit_source_action(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        job = self.catalog.create_or_get_job(kind="source", database_id=payload.get("database_id"), preview_id=None, payload=dict(payload), can_cancel=True)
        if job["state"] == "queued":
            with self._lock:
                self._futures.setdefault(job["id"], self.executor.submit(self._run_source, job["id"], dict(payload)))
        return job

    def submit_redundancy(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        job = self.catalog.create_or_get_job(kind="redundancy", database_id=None, preview_id=None, payload=dict(payload), can_cancel=True)
        if job["state"] == "queued":
            with self._lock:
                self._futures.setdefault(job["id"], self.executor.submit(self._run_redundancy, job["id"], dict(payload)))
        return job

    def submit_environment_repair(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        job = self.catalog.create_or_get_job(kind="environment_repair", database_id=None, preview_id=None, payload=dict(payload), can_cancel=True)
        if job["state"] == "queued":
            with self._lock:
                self._futures.setdefault(job["id"], self.executor.submit(self._run_environment_repair, job["id"], dict(payload)))
        return job

    def submit_synthetic_progress(self, seconds: int = 10) -> dict[str, Any]:
        duration = max(1, min(int(seconds), 10))
        job = self.catalog.create_or_get_job(kind="diagnostic", database_id=None, preview_id=None, payload={"seconds": duration}, can_cancel=True)
        if job["state"] == "queued":
            with self._lock:
                self._futures.setdefault(job["id"], self.executor.submit(self._run_synthetic_progress, job["id"], duration))
        return job

    def _run_preview(self, job_id: str, payload: dict[str, Any]) -> None:
        if self.catalog.get_job(job_id)["state"] != "queued":
            return
        self.catalog.update_job(job_id, state="running", stage="checking")
        try:
            self.catalog.add_event(job_id, "checking", "Checking source and registered database.")
            preview = self.service._create_preview_from_payload(payload)
            self.catalog.update_job(job_id, state="succeeded", stage="complete", result={"preview_id": preview["preview_id"], "preview": preview})
            self.catalog.add_event(job_id, "complete", "Review is ready.")
        except BridgeError as exc:
            self.catalog.update_job(job_id, state="failed", stage="complete", error=exc.as_dict())
            self.catalog.add_event(job_id, "complete", exc.message, {"code": exc.code})
        except Exception as exc:
            self.catalog.update_job(job_id, state="failed", stage="complete", error={"code": "JOB_FAILED", "message": str(exc), "active_database_state": "unchanged"})
            self.catalog.add_event(job_id, "complete", "The review could not be prepared.", {"code": "JOB_FAILED"})

    def _run_scan(self, job_id: str, payload: dict[str, Any]) -> None:
        if self.catalog.get_job(job_id)["state"] != "queued":
            return
        self.catalog.update_job(job_id, state="running", stage="checking")
        try:
            self.catalog.add_event(job_id, "checking", "Scanning the selected source.")
            result = self.service.scan_source(payload)
            self.catalog.update_job(job_id, state="succeeded", stage="complete", result=result)
            self.catalog.add_event(job_id, "complete", "Source scan completed.")
        except Exception as exc:
            error = exc.as_dict() if isinstance(exc, BridgeError) else {"code": "JOB_FAILED", "message": str(exc)}
            self.catalog.update_job(job_id, state="failed", stage="complete", error=error)
            self.catalog.add_event(job_id, "complete", error["message"], {"code": error["code"]})

    def _run_inspection(self, job_id: str, payload: dict[str, Any]) -> None:
        if self.catalog.get_job(job_id)["state"] != "queued":
            return
        self.catalog.update_job(job_id, state="running", stage="inspecting")
        try:
            self.catalog.add_event(job_id, "inspecting", "Reading source and stored episode inventories.")
            result = self.service.inspect_content(payload)
            self.catalog.update_job(job_id, state="succeeded", stage="complete", result=result, can_cancel=False)
            self.catalog.add_event(job_id, "complete", "Content inspection completed.")
        except Exception as exc:
            error = exc.as_dict() if isinstance(exc, BridgeError) else {"code": "JOB_FAILED", "message": str(exc), "active_database_state": "unchanged"}
            self.catalog.update_job(job_id, state="failed", stage="complete", error=error, can_cancel=False)
            self.catalog.add_event(job_id, "complete", error["message"], {"code": error["code"]})

    def _run_validation(self, job_id: str, payload: dict[str, Any]) -> None:
        if self.catalog.get_job(job_id)["state"] != "queued":
            return
        self.catalog.update_job(job_id, state="running", stage="validating")
        try:
            self.catalog.add_event(job_id, "validating", "Running the selected validation scope.")
            result = self.service.validate_database(payload)
            self.catalog.update_job(job_id, state="succeeded", stage="complete", result=result, can_cancel=False)
            self.catalog.add_event(job_id, "complete", "Validation completed.")
        except Exception as exc:
            error = exc.as_dict() if isinstance(exc, BridgeError) else {"code": "JOB_FAILED", "message": str(exc), "active_database_state": "unchanged"}
            self.catalog.update_job(job_id, state="failed", stage="complete", error=error, can_cancel=False)
            self.catalog.add_event(job_id, "complete", error["message"], {"code": error["code"]})

    def _run_source(self, job_id: str, payload: dict[str, Any]) -> None:
        if self.catalog.get_job(job_id)["state"] != "queued":
            return
        self.catalog.update_job(job_id, state="running", stage="preparing")
        try:
            result = self.service._run_source_action(payload, lambda message: self.catalog.add_event(job_id, "preparing", message))
            self.catalog.update_job(job_id, state="succeeded", stage="complete", result=result)
            self.catalog.add_event(job_id, "complete", "Source action completed.")
        except Exception as exc:
            error = exc.as_dict() if isinstance(exc, BridgeError) else {"code": "JOB_FAILED", "message": str(exc)}
            self.catalog.update_job(job_id, state="failed", stage="complete", error=error)
            self.catalog.add_event(job_id, "complete", error["message"], {"code": error["code"]})

    def _run_redundancy(self, job_id: str, payload: dict[str, Any]) -> None:
        if self.catalog.get_job(job_id)["state"] != "queued":
            return
        self.catalog.update_job(job_id, state="running", stage="analyzing")
        try:
            self.catalog.add_event(job_id, "analyzing", "Running the selected advisory redundancy operation.")
            result = self.service._run_redundancy_action(payload, job_id=job_id)
            report_status = result.get("report", {}).get("status")
            job_state = "cancelled" if report_status == "cancelled" else ("succeeded_with_warnings" if report_status == "partial" else ("succeeded" if report_status not in {"failed", "unavailable"} else "failed"))
            self.catalog.update_job(job_id, state=job_state, stage="complete", result=result, can_cancel=False)
            self.catalog.add_event(job_id, "complete", "Redundancy operation completed.")
        except Exception as exc:
            error = exc.as_dict() if isinstance(exc, BridgeError) else {"code": "JOB_FAILED", "message": str(exc), "active_database_state": "unchanged"}
            self.catalog.update_job(job_id, state="failed", stage="complete", error=error, can_cancel=False)
            self.catalog.add_event(job_id, "complete", error["message"], {"code": error["code"]})

    def _run_environment_repair(self, job_id: str, payload: dict[str, Any]) -> None:
        if self.catalog.get_job(job_id)["state"] != "queued":
            return
        self.catalog.update_job(job_id, state="running", stage="repairing")
        try:
            self.catalog.add_event(job_id, "repairing", "Running the reviewed environment repair.")
            from .environment_repair import run_review

            review = __import__("json").loads(Path(str(payload["review_path"])).read_text(encoding="utf-8"))
            result = run_review(review, root=Path(str(payload["root"])))
            state = "succeeded" if result.get("status") == "completed" else "failed"
            error = None if state == "succeeded" else {"code": "JOB_FAILED", "message": "Environment repair exited with a nonzero status.", "active_database_state": "unchanged"}
            self.catalog.update_job(job_id, state=state, stage="complete", result=result, error=error, can_cancel=False)
            self.catalog.add_event(job_id, "complete", "Environment repair completed." if state == "succeeded" else "Environment repair failed.")
        except Exception as exc:
            error = exc.as_dict() if isinstance(exc, BridgeError) else {"code": "JOB_FAILED", "message": str(exc), "active_database_state": "unchanged"}
            self.catalog.update_job(job_id, state="failed", stage="complete", error=error, can_cancel=False)
            self.catalog.add_event(job_id, "complete", error["message"], {"code": error["code"]})

    def _run_synthetic_progress(self, job_id: str, seconds: int) -> None:
        if self.catalog.get_job(job_id)["state"] != "queued":
            return
        self.catalog.update_job(job_id, state="running", stage="checking", can_cancel=False)
        try:
            self.catalog.add_event(job_id, "checking", "Starting the desktop responsiveness check.", {"seconds": seconds})
            for current in range(1, seconds + 1):
                time.sleep(1)
                self.catalog.add_event(job_id, "preparing", f"Synthetic progress {current}/{seconds}.", {"current": current, "total": seconds})
            result = {"output": "Synthetic desktop progress completed.", "seconds": seconds, "active_database_state": "unchanged"}
            self.catalog.update_job(job_id, state="succeeded", stage="complete", result=result, can_cancel=False)
            self.catalog.add_event(job_id, "complete", "Desktop responsiveness check completed.")
        except Exception as exc:
            error = {"code": "JOB_FAILED", "message": str(exc), "active_database_state": "unchanged"}
            self.catalog.update_job(job_id, state="failed", stage="complete", error=error, can_cancel=False)
            self.catalog.add_event(job_id, "complete", "Desktop responsiveness check failed.", error)

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.catalog.get_job(job_id)
        if job["state"] == "queued":
            return self.catalog.cancel_queued_job(job_id)
        if job["state"] == "running" and job["kind"] == "redundancy" and job.get("can_cancel"):
            cancel_dir = self.catalog.path.parent / "job-cancel"
            cancel_dir.mkdir(parents=True, exist_ok=True)
            (cancel_dir / job_id).write_text("cancel", encoding="utf-8")
            self.catalog.add_event(job_id, "cancelling", "Cancellation requested; waiting for the backend to confirm.")
            return self.catalog.get_job(job_id)
        raise BridgeError("OPERATION_UNSUPPORTED", "This operation cannot be safely cancelled in its current state.")

    def _run_import(self, job_id: str, database_id: str, preview: FrozenPreview) -> None:
        if self.catalog.get_job(job_id)["state"] != "queued":
            return
        self.catalog.update_job(job_id, state="running", stage="checking", can_cancel=False)
        record = self.service.get_database(database_id)
        target = Path(str(record["target"]["path"]))
        lock = TargetLock(target, job_id) if str(record.get("source_kind") or "folder") != "managed" else None
        try:
            with (lock if lock is not None else nullcontext()):
                from .models import DatabaseRecord

                # Re-read and validate inside the writer lock. The UI checks
                # this before queueing, but settings can change while a job
                # waits in the executor; no write may start from that stale
                # record.
                record = self.service.get_database(database_id)
                database_record = DatabaseRecord.from_mapping(record)
                self.service._validate_preview_current(database_record, preview)
                target = Path(str(record["target"]["path"])).expanduser().resolve()
                journal_stage = target.parent / f".{target.name}.staging-{job_id}"
                journal_backup = target.parent / f".{target.name}.previous-{job_id}"
                self.catalog.save_activation_journal(job_id, str(target), str(journal_stage), str(journal_backup), "accepted", {"preview_id": preview.preview_id})
                self.catalog.add_event(job_id, "checking", "Validated the accepted review and acquired the database lock.")
                if preview.operation == "create" and target.exists():
                    recoverable_managed_target = (
                        database_record.source_kind == "managed"
                        and self.service.managed_adapter.can_retry_create_target(target)
                    )
                    if not recoverable_managed_target:
                        raise BridgeError("TARGET_EXISTS", "The destination appeared after review; existing data was preserved.")
                self.catalog.add_event(job_id, "embedding", "Starting the existing Python importer.")

                def emit(progress: Any) -> None:
                    message = getattr(progress, "message", str(progress))
                    current = int(getattr(progress, "current", 0) or 0)
                    total = int(getattr(progress, "total", 0) or 0)
                    self.catalog.add_event(job_id, "embedding", message, {"current": current, "total": total})

                if database_record.source_kind == "managed":
                    result = self.service.managed_adapter.execute(database_record, preview, emit)
                else:
                    result = self.folder_adapter.execute(
                        database_record,
                        preview,
                        emit,
                        operation_id_override=job_id,
                        journal_callback=lambda state, detail: self.catalog.save_activation_journal(
                            job_id,
                            str(target),
                            str(journal_stage),
                            str(journal_backup),
                            state,
                            detail,
                        ),
                        lock_held=True,
                    )
                if result.get("downstream_identity"):
                    self.catalog.update_database(database_id, {"downstream_identity": result["downstream_identity"]})
                self.catalog.save_activation_journal(job_id, str(target), str(journal_stage), str(journal_backup), "completed", {"preview_id": preview.preview_id, "active_database_state": result.get("active_database_state")})
                if preview.operation in {"update", "rebuild"}:
                    self.catalog.update_database(database_id, {
                        "last_check": {
                            "checked_at": utc_now(),
                            "status": "up_to_date",
                            "label": "Up to date",
                            "preview_id": preview.preview_id,
                            "source_hash": preview.source_snapshot.get("hash") or preview.source_snapshot.get("release_hash"),
                        },
                    })
                if database_record.source_kind == "managed":
                    self.service._record_managed_release_event(database_id, preview, status="succeeded", result=result)
                state = "succeeded_with_warnings" if result.get("warnings") else "succeeded"
                self.catalog.update_job(job_id, state=state, stage="complete", result=result, can_cancel=False)
                self.catalog.add_event(job_id, "complete", "Database operation completed.", {"active_database_state": result.get("active_database_state")})
        except BridgeError as exc:
            try:
                if preview.database_id or self.catalog.get_database_link(database_id):
                    self.service._record_managed_release_event(database_id, preview, status="failed", error=exc.as_dict())
            except Exception:
                pass
            self.catalog.save_activation_journal(job_id, str(target), str(journal_stage if 'journal_stage' in locals() else target.parent / f".{target.name}.staging-{job_id}"), str(journal_backup if 'journal_backup' in locals() else target.parent / f".{target.name}.previous-{job_id}"), "failed", {"code": exc.code})
            self.catalog.update_job(job_id, state="failed", stage="complete", error=exc.as_dict(), can_cancel=False)
            self.catalog.add_event(job_id, "complete", exc.message, {"code": exc.code})
        except Exception as exc:
            try:
                if preview.database_id or self.catalog.get_database_link(database_id):
                    self.service._record_managed_release_event(database_id, preview, status="failed", error={"code": "JOB_FAILED", "message": str(exc)})
            except Exception:
                pass
            self.catalog.save_activation_journal(job_id, str(target), str(journal_stage if 'journal_stage' in locals() else target.parent / f".{target.name}.staging-{job_id}"), str(journal_backup if 'journal_backup' in locals() else target.parent / f".{target.name}.previous-{job_id}"), "failed", {"code": "JOB_FAILED"})
            error = {"code": "JOB_FAILED", "message": str(exc), "active_database_state": "unknown"}
            self.catalog.update_job(job_id, state="failed", stage="complete", error=error, can_cancel=False)
            self.catalog.add_event(job_id, "complete", "The import failed; inspect active database state before retrying.", error)

    def recover(self) -> list[dict[str, Any]]:
        interrupted = self.catalog.mark_running_interrupted()
        for job in interrupted:
            if job.get("kind") != "import" or not job.get("database_id") or not job.get("preview_id"):
                continue
            try:
                preview = FrozenPreview.from_mapping(self.catalog.get_preview(str(job["preview_id"])))
                self.service._record_managed_release_event(
                    str(job["database_id"]),
                    preview,
                    status="interrupted",
                    error=dict(job.get("error") or {"code": "JOB_INTERRUPTED", "message": "The application stopped before this job completed."}),
                )
            except Exception:
                # Recovery must remain best effort. The interrupted job and
                # activation journal still provide the durable evidence even
                # when its optional source link cannot be recovered.
                pass
        for evidence in inspect_recovery(self.catalog):
            operation_id = str(evidence.get("operation_id") or "")
            if not operation_id:
                continue
            try:
                job = self.catalog.get_job(operation_id)
            except BridgeError:
                continue
            if job.get("state") != "interrupted":
                continue
            detail = dict(job.get("error") or {})
            recovery = dict(evidence.get("evidence") or {})
            detail["active_database_state"] = recovery.get("active_database_state", "unknown")
            detail["recovery_evidence"] = recovery
            self.catalog.update_job(operation_id, error=detail)
        return [self.catalog.get_job(str(job["id"])) for job in interrupted]

    def shutdown(self, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait, cancel_futures=False)
