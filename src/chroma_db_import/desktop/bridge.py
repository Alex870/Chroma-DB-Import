from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Mapping

from chroma_db_import.workflow.models import BridgeError
from chroma_db_import.workflow.service import WorkflowService


class ApplicationBridge:
    """Small allowlisted JSON bridge exposed to JavaScript."""

    def __init__(self, service: WorkflowService, *, folder_picker: Callable[[], str | None] | None = None) -> None:
        self.service = service
        self.folder_picker = folder_picker

    @staticmethod
    def _ok(data: Any) -> dict[str, Any]:
        return {"ok": True, "data": data}

    @staticmethod
    def _error(error: Exception) -> dict[str, Any]:
        if isinstance(error, BridgeError):
            detail = error.as_dict()
        else:
            detail = {"code": "JOB_FAILED", "message": "The request could not be completed.", "field": None, "details_id": None}
        return {"ok": False, "error": detail}

    def _call(self, function: Callable[[], Any]) -> dict[str, Any]:
        try:
            return self._ok(function())
        except Exception as exc:
            return self._error(exc)

    def handshake(self) -> dict[str, Any]:
        return self._call(self.service.version)

    def echo(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(lambda: dict(self._mapping(payload if payload is not None else {})))

    def synthetic_progress(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(lambda: self.service.queue_synthetic_progress(self._int(self._mapping(payload if payload is not None else {}), "seconds", 10, minimum=1, maximum=10)))

    def list_databases(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(lambda: self.service.list_databases(include_archived=bool(self._mapping(payload if payload is not None else {}).get("include_archived"))))

    def get_database(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.get_database(self._id(self._mapping(payload), "database_id")))

    def list_jobs(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(lambda: self.service.list_jobs(self._int(self._mapping(payload if payload is not None else {}), "limit", 100, minimum=1, maximum=500)))

    def get_job_events(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            return self.service.get_job_events(self._id(normalized, "job_id"), after=self._int(normalized, "after", 0, minimum=0), limit=self._int(normalized, "limit", 200, minimum=1, maximum=1000))
        return self._call(invoke)

    def get_job(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.get_job(self._id(self._mapping(payload), "job_id")))

    def retry_job(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.retry_job(self._id(self._mapping(payload), "job_id")))

    def get_database_history(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.get_database_history(self._id(self._mapping(payload), "database_id")))

    def get_database_content(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.get_database_content(self._id(self._mapping(payload), "database_id")))

    def environment_report(self, _payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(self.service.environment_report)

    def migration_candidates(self, _payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(self.service.migration_candidates)

    def cancel_job(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.cancel_job(self._id(self._mapping(payload), "job_id")))

    def pick_folder(self, _payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(lambda: self.folder_picker() if self.folder_picker else None)

    def inspect_existing(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.inspect_existing(self._mapping(payload)))

    def register_existing(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.register_existing(self._mapping(payload)))

    def save_draft(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            return self.service.save_draft(
                dict(normalized.get("payload") or {}),
                draft_id=self._optional_text(normalized, "draft_id"),
                database_id=self._optional_text(normalized, "database_id"),
            )
        return self._call(invoke)

    def get_draft(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.get_draft(self._id(self._mapping(payload), "draft_id")))

    def scan_source(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.queue_scan(self._mapping(payload)))

    def check_database(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.queue_preview({**self._mapping(payload), "operation": "update"}))

    def create_preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.queue_preview(self._mapping(payload)))

    def create_maintenance_preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            return self.service.create_maintenance_preview(self._id(normalized, "database_id"), self._strings(normalized, "delete_ids"))
        return self._call(invoke)

    def get_preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            return self.service.get_preview(self._id(normalized, "preview_id"), offset=self._int(normalized, "offset", 0, minimum=0), limit=self._int(normalized, "limit", 200, minimum=1, maximum=1000))
        return self._call(invoke)

    def apply_preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            return self.service.apply_preview(self._id(normalized, "preview_id"), self._strings(normalized, "acknowledgments", required=False))
        return self._call(invoke)

    def start_source_action(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.start_source_action(self._mapping(payload)))

    def archive_database(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.archive_database(self._id(self._mapping(payload), "database_id")))

    def rename_database(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            return self.service.rename_database(self._id(normalized, "database_id"), self._required_text(normalized, "display_name"))
        return self._call(invoke)

    def update_database_settings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            return self.service.update_database_settings(self._id(normalized, "database_id"), self._mapping(normalized.get("changes")))
        return self._call(invoke)

    def open_database_folder(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self._open_database_folder(self._id(self._mapping(payload), "database_id")))

    def _open_database_folder(self, database_id: str) -> str:
        path = Path(self.service.open_database_folder(database_id))
        if not path.is_dir():
            raise BridgeError("SOURCE_UNAVAILABLE", "The registered database folder is not available. The active database was not changed.")
        startfile = getattr(os, "startfile", None)
        if callable(startfile):
            startfile(str(path))
        return str(path)

    def export_report(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            return self.service.export_report(self._mapping(normalized.get("report")), report_id=self._optional_text(normalized, "report_id"))
        return self._call(invoke)

    @staticmethod
    def _mapping(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise BridgeError("VALIDATION_FAILED", "The request payload must be an object.")
        return dict(payload)

    @staticmethod
    def _id(payload: Mapping[str, Any], field: str) -> str:
        raw = payload.get(field)
        if not isinstance(raw, str) or not raw.strip():
            raise BridgeError("VALIDATION_FAILED", f"{field} is required.", field=field)
        return raw.strip()

    @classmethod
    def _optional_text(cls, payload: Mapping[str, Any], field: str) -> str | None:
        raw = payload.get(field)
        if raw in (None, ""):
            return None
        if not isinstance(raw, str) or not raw.strip():
            raise BridgeError("VALIDATION_FAILED", f"{field} must be a non-empty string.", field=field)
        return raw.strip()

    @classmethod
    def _required_text(cls, payload: Mapping[str, Any], field: str) -> str:
        value = cls._optional_text(payload, field)
        if value is None:
            raise BridgeError("VALIDATION_FAILED", f"{field} is required.", field=field)
        return value

    @staticmethod
    def _int(payload: Mapping[str, Any], field: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
        raw = payload.get(field)
        if raw in (None, ""):
            value = default
        else:
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise BridgeError("VALIDATION_FAILED", f"{field} must be an integer.", field=field) from exc
        if minimum is not None and value < minimum:
            raise BridgeError("VALIDATION_FAILED", f"{field} must be at least {minimum}.", field=field)
        if maximum is not None and value > maximum:
            raise BridgeError("VALIDATION_FAILED", f"{field} must be at most {maximum}.", field=field)
        return value

    @staticmethod
    def _list(payload: Mapping[str, Any], field: str, *, required: bool = True) -> list[Any]:
        raw = payload.get(field)
        if raw is None and not required:
            return []
        if not isinstance(raw, list):
            raise BridgeError("VALIDATION_FAILED", f"{field} must be an array.", field=field)
        return list(raw)

    @classmethod
    def _strings(cls, payload: Mapping[str, Any], field: str, *, required: bool = True) -> list[str]:
        values = cls._list(payload, field, required=required)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise BridgeError("VALIDATION_FAILED", f"{field} must contain non-empty strings.", field=field)
        return [value.strip() for value in values]
