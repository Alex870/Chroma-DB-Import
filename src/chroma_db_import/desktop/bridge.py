from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Mapping

from chroma_db_import.workflow.models import BridgeError
from chroma_db_import.workflow.service import WorkflowService


class ApplicationBridge:
    """Small allowlisted JSON bridge exposed to JavaScript."""

    def __init__(self, service: WorkflowService, *, folder_picker: Callable[[], str | None] | None = None, file_picker: Callable[[str], str | None] | None = None, save_file_picker: Callable[[str], str | None] | None = None) -> None:
        self.service = service
        self.folder_picker = folder_picker
        self.file_picker = file_picker
        self.save_file_picker = save_file_picker

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

    def handshake(self, _payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(self.service.version)

    def echo(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(lambda: dict(self._mapping(payload if payload is not None else {})))

    def synthetic_progress(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(lambda: self.service.queue_synthetic_progress(self._int(self._mapping(payload if payload is not None else {}), "seconds", 10, minimum=1, maximum=10)))

    def list_databases(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(lambda: self.service.list_databases(include_archived=bool(self._mapping(payload if payload is not None else {}).get("include_archived"))))

    def get_database(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.get_database(self._id(self._mapping(payload), "database_id")))

    def restore_database(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.restore_database(self._id(self._mapping(payload), "database_id")))

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
        def invoke() -> Any:
            normalized = self._mapping(payload)
            policy = normalized.get("selection_policy")
            if policy is not None and not isinstance(policy, Mapping):
                raise BridgeError("VALIDATION_FAILED", "selection_policy must be an object.", field="selection_policy")
            return self.service.get_database_content(
                self._id(normalized, "database_id"), selection_policy=policy,
                offset=self._int(normalized, "offset", 0, minimum=0), limit=self._int(normalized, "limit", 100, minimum=1, maximum=500),
                search=str(normalized.get("search") or ""),
            )
        return self._call(invoke)

    def get_database_details(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.get_database_details(self._id(self._mapping(payload), "database_id")))

    def inspect_content(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            self._id(normalized, "database_id")
            self._int(normalized, "offset", 0, minimum=0)
            self._int(normalized, "limit", 100, minimum=1, maximum=500)
            policy = normalized.get("selection_policy")
            if policy is not None and not isinstance(policy, Mapping):
                raise BridgeError("VALIDATION_FAILED", "selection_policy must be an object.", field="selection_policy")
            return self.service.jobs.submit_inspection(normalized)
        return self._call(invoke)

    def environment_report(self, _payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(self.service.environment_report)

    def preview_environment_repair(self, _payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(self.service.preview_environment_repair)

    def apply_environment_repair(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.apply_environment_repair(self._id(self._mapping(payload), "review_id")))

    def migration_candidates(self, _payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(self.service.migration_candidates)

    def cancel_job(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.cancel_job(self._id(self._mapping(payload), "job_id")))

    def pick_folder(self, _payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(lambda: self.folder_picker() if self.folder_picker else None)

    def pick_file(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        def invoke() -> str | None:
            normalized = self._mapping(payload if payload is not None else {})
            purpose = self._required_text(normalized, "purpose")
            if purpose not in {"report", "settings_import", "redundancy_bundle", "labels", "queries", "query_results"}:
                raise BridgeError("VALIDATION_FAILED", "File picker purpose is not allowlisted.", field="purpose")
            return self.file_picker(purpose) if self.file_picker else None
        return self._call(invoke)

    def pick_save_file(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        def invoke() -> str | None:
            normalized = self._mapping(payload if payload is not None else {})
            purpose = self._required_text(normalized, "purpose")
            if purpose not in {"report", "settings_export", "labels", "evaluation"}:
                raise BridgeError("VALIDATION_FAILED", "Save-file picker purpose is not allowlisted.", field="purpose")
            return self.save_file_picker(purpose) if self.save_file_picker else None
        return self._call(invoke)

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

    def save_report_copy(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            report_id = self._id(normalized, "report_id")
            output_path = self._required_text(normalized, "output_path")
            return self.service.save_report_copy(report_id, output_path)
        return self._call(invoke)

    def get_app_defaults(self, _payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(self.service.get_app_defaults)

    def save_app_defaults(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            changes = normalized.get("changes")
            if not isinstance(changes, Mapping):
                raise BridgeError("VALIDATION_FAILED", "changes must be an object.", field="changes")
            base = normalized.get("base_revision")
            return self.service.save_app_defaults(changes, base_revision=int(base) if base is not None else None)
        return self._call(invoke)

    def export_settings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            reference = normalized.get("reference")
            if reference is not None and not isinstance(reference, Mapping):
                raise BridgeError("VALIDATION_FAILED", "reference must be an object.", field="reference")
            transfer = self.service.export_settings(self._required_text(normalized, "scope_kind"), reference, include_redundancy=bool(normalized.get("include_redundancy")))
            output_path = normalized.get("output_path")
            if output_path is not None:
                if not isinstance(output_path, str) or not output_path.strip():
                    raise BridgeError("VALIDATION_FAILED", "output_path must be a non-empty string.", field="output_path")
                return self.service.save_settings_transfer(transfer, output_path)
            return transfer
        return self._call(invoke)

    def save_settings_transfer(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            return self.service.save_settings_transfer(self._mapping(normalized.get("transfer")), self._required_text(normalized, "output_path"))
        return self._call(invoke)

    def preview_settings_import(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            reference = normalized.get("reference")
            if reference is not None and not isinstance(reference, Mapping):
                raise BridgeError("VALIDATION_FAILED", "reference must be an object.", field="reference")
            return self.service.preview_settings_import(self._required_text(normalized, "path"), self._required_text(normalized, "scope_kind"), reference)
        return self._call(invoke)

    def apply_settings_import(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            fields = normalized.get("selected_fields") or normalized.get("selected_field_paths") or []
            review_id = normalized.get("review_id")
            if review_id is not None:
                if not isinstance(review_id, str):
                    raise BridgeError("VALIDATION_FAILED", "review_id must be a string.", field="review_id")
                if not isinstance(fields, list) or any(not isinstance(item, str) for item in fields):
                    raise BridgeError("VALIDATION_FAILED", "selected_fields must be a list of field names.", field="selected_fields")
                return self.service.apply_settings_review(review_id, fields)
            if not isinstance(fields, list) or any(not isinstance(item, str) for item in fields):
                raise BridgeError("VALIDATION_FAILED", "selected_fields must be a list of field names.", field="selected_fields")
            reference = normalized.get("reference")
            if reference is not None and not isinstance(reference, Mapping):
                raise BridgeError("VALIDATION_FAILED", "reference must be an object.", field="reference")
            return self.service.apply_settings_import(self._required_text(normalized, "path"), self._mapping(normalized.get("proposal")), fields, self._required_text(normalized, "scope_kind"), reference)
        return self._call(invoke)

    def list_source_connections(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        normalized = self._mapping(payload if payload is not None else {})
        return self._call(lambda: self.service.list_source_connections(include_archived=bool(normalized.get("include_archived"))))

    def reconcile_sources(self, _payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._call(self.service.reconcile_sources)

    def adopt_database_link(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            for field_name in ("database_id", "connection_id", "partition_id"):
                self._required_text(normalized, field_name)
            return self.service.adopt_database_link(normalized)
        return self._call(invoke)

    def select_database_link(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            for field_name in ("database_id", "connection_id", "partition_id"):
                self._required_text(normalized, field_name)
            return self.service.select_database_link(normalized)
        return self._call(invoke)

    def link_source(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.link_source(self._required_text(self._mapping(payload), "source_root")))

    def discover_contexts(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.discover_contexts(self._id(self._mapping(payload), "connection_id")))

    def list_contexts(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload if payload is not None else {})
            return self.service.list_contexts(connection_id=self._optional_text(normalized, "connection_id"), include_archived=bool(normalized.get("include_archived")))
        return self._call(invoke)

    def get_context(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._mapping(payload)
        return self._call(lambda: self.service.get_context(self._mapping(normalized.get("context"))))

    def set_context_archived(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            archived = normalized.get("archived")
            if not isinstance(archived, bool):
                raise BridgeError("VALIDATION_FAILED", "archived must be a boolean.", field="archived")
            return self.service.set_context_archived(self._mapping(normalized.get("context")), archived)
        return self._call(invoke)

    def save_context_import_defaults(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            changes = self._mapping(normalized.get("changes"))
            revision = normalized.get("base_revision")
            if revision is not None and not isinstance(revision, int):
                raise BridgeError("VALIDATION_FAILED", "base_revision must be an integer.", field="base_revision")
            return self.service.save_context_import_defaults(self._mapping(normalized.get("context")), changes, base_revision=revision)
        return self._call(invoke)

    def save_context_defaults(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            profile_changes = self._mapping(normalized.get("profile_changes"))
            execution = normalized.get("execution_options")
            if execution is not None and not isinstance(execution, Mapping):
                raise BridgeError("VALIDATION_FAILED", "execution_options must be an object.", field="execution_options")
            fingerprint = normalized.get("base_profile_fingerprint")
            if fingerprint is not None and not isinstance(fingerprint, str):
                raise BridgeError("VALIDATION_FAILED", "base_profile_fingerprint must be a string.", field="base_profile_fingerprint")
            return self.service.save_context_defaults(self._mapping(normalized.get("context")), fingerprint, profile_changes, execution)
        return self._call(invoke)

    def preview_context_dedup(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            release = normalized.get("upstream_release_id")
            if release is not None and not isinstance(release, str):
                raise BridgeError("VALIDATION_FAILED", "upstream_release_id must be a string.", field="upstream_release_id")
            return self.service.preview_context_dedup(self._mapping(normalized.get("context")), release)
        return self._call(invoke)

    def get_context_dedup_settings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.get_context_dedup_settings(self._mapping(self._mapping(payload).get("context"))))

    def save_context_dedup_policy(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            fingerprint = normalized.get("base_profile_fingerprint")
            if fingerprint is not None and not isinstance(fingerprint, str):
                raise BridgeError("VALIDATION_FAILED", "base_profile_fingerprint must be a string.", field="base_profile_fingerprint")
            return self.service.save_context_dedup_policy(self._mapping(normalized.get("context")), self._mapping(normalized.get("changes")), fingerprint)
        return self._call(invoke)

    def review_context_dedup(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            release = normalized.get("upstream_release_id")
            if release is not None and not isinstance(release, str):
                raise BridgeError("VALIDATION_FAILED", "upstream_release_id must be a string.", field="upstream_release_id")
            return self.service.review_context_dedup(self._mapping(normalized.get("context")), release)
        return self._call(invoke)

    def open_context_folder(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.open_context_folder(self._mapping(self._mapping(payload).get("context"))))

    def repair_partition_lock(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            confirmed = normalized.get("confirm", False)
            if not isinstance(confirmed, bool):
                raise BridgeError("VALIDATION_FAILED", "confirm must be a boolean.", field="confirm")
            raw_timeout = normalized.get("grace_timeout", 10.0)
            try:
                grace_timeout = float(raw_timeout)
            except (TypeError, ValueError) as exc:
                raise BridgeError("VALIDATION_FAILED", "grace_timeout must be a number.", field="grace_timeout") from exc
            return self.service.repair_partition_lock(self._required_text(normalized, "partition_root"), confirm=confirmed, grace_timeout=grace_timeout)
        return self._call(invoke)

    def get_source_suggestions(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload if payload is not None else {})
            current = normalized.get("current_path")
            if current is not None and not isinstance(current, str):
                raise BridgeError("VALIDATION_FAILED", "current_path must be a string.", field="current_path")
            return self.service.get_source_suggestions(current)
        return self._call(invoke)

    def list_source_folders(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            root = self._required_text(normalized, "root")
            relative = normalized.get("relative_path")
            if relative is not None and not isinstance(relative, str):
                raise BridgeError("VALIDATION_FAILED", "relative_path must be a string.", field="relative_path")
            return self.service.list_source_folders(root, relative)
        return self._call(invoke)

    def inspect_folder(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            path = self._required_text(normalized, "path")
            asset_filter = self._required_text(normalized, "asset_filter")
            pattern = normalized.get("asset_pattern") or ""
            if not isinstance(pattern, str):
                raise BridgeError("VALIDATION_FAILED", "asset_pattern must be a string.", field="asset_pattern")
            return self.service.inspect_folder(path, asset_filter, pattern)
        return self._call(invoke)

    def get_redundancy_settings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.get_redundancy_settings(self._mapping(self._mapping(payload).get("context"))))

    def save_redundancy_policy(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            changes = self._mapping(normalized.get("changes"))
            base = normalized.get("base_fingerprint")
            if base is not None and not isinstance(base, str):
                raise BridgeError("VALIDATION_FAILED", "base_fingerprint must be a string.", field="base_fingerprint")
            return self.service.save_redundancy_policy(self._mapping(normalized.get("context")), changes, base)
        return self._call(invoke)

    def start_redundancy_action(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.start_redundancy_action(self._mapping(payload)))

    def validate_database(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(lambda: self.service.jobs.submit_validation(self._mapping(payload)))

    def save_judge_config(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            changes = self._mapping(normalized.get("changes"))
            fingerprint = normalized.get("base_fingerprint")
            if fingerprint is not None and not isinstance(fingerprint, str):
                raise BridgeError("VALIDATION_FAILED", "base_fingerprint must be a string.", field="base_fingerprint")
            return self.service.save_judge_config(self._mapping(normalized.get("context")), changes, fingerprint)
        return self._call(invoke)

    def probe_judge(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            changes = normalized.get("changes")
            if changes is not None and not isinstance(changes, Mapping):
                raise BridgeError("VALIDATION_FAILED", "changes must be an object.", field="changes")
            return self.service.probe_judge(self._mapping(normalized.get("context")), self._mapping(changes) if changes is not None else None)
        return self._call(invoke)

    def review_judge_pilot(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            channels = normalized.get("channels")
            if not isinstance(channels, list) or any(not isinstance(item, str) for item in channels):
                raise BridgeError("VALIDATION_FAILED", "channels must be a list of strings.", field="channels")
            return self.service.review_judge_pilot(self._mapping(normalized.get("context")), self._required_text(normalized, "upstream_release_id"), channels)
        return self._call(invoke)

    def open_redundancy_bundle(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def invoke() -> Any:
            normalized = self._mapping(payload)
            path = self._required_text(normalized, "path")
            expected = normalized.get("context")
            if expected is not None and not isinstance(expected, Mapping):
                raise BridgeError("VALIDATION_FAILED", "context must be an object.", field="context")
            return self.service.open_redundancy_bundle(path, expected)
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
