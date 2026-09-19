from __future__ import annotations

import json
import hashlib
import os
import threading
from pathlib import Path
from typing import Any, Mapping

from chroma_db_import.releases import inspect_export_work

from .catalog import AppCatalog, normalize_target
from .folder_adapter import FolderAdapter
from .jobs import JobService
from .managed_adapter import ManagedAdapter
from .models import BridgeError, DatabaseRecord, FrozenPreview, SelectionPolicy, make_report, new_id, stable_hash, utc_now
from .inspection import inspect_database, inventory_database, resolve_active_export
from .planning import create_folder_preview, scan_folder


class WorkflowService:
    """Application-facing service used by both the bridge and tests."""

    API_VERSION = "gui-api-v1"

    def __init__(self, state_dir: Path | None = None, *, catalog: AppCatalog | None = None) -> None:
        self.state_dir = Path(state_dir or (Path.cwd() / "state" / "gui")).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog or AppCatalog(self.state_dir / "gui_catalog.sqlite3")
        self.folder_adapter = FolderAdapter()
        self.managed_adapter = ManagedAdapter()
        self.jobs = JobService(self.catalog, self.folder_adapter, self)
        self._lock = threading.RLock()
        self.jobs.recover()

    def version(self) -> dict[str, Any]:
        return {
            "api_version": self.API_VERSION,
            "backend_version": "0.2.0",
            "capabilities": [
                "database_details", "content_inspection", "archive_restore", "source_connections",
                "managed_contexts", "redundancy_analysis", "settings_transfer", "environment_repair",
                "producer_reconciliation", "database_release_history", "partition_lock_repair",
            ],
        }

    def list_databases(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return self.catalog.list_databases(include_archived=include_archived)

    def get_database(self, database_id: str) -> dict[str, Any]:
        return self.catalog.get_database(database_id)

    def restore_database(self, database_id: str) -> dict[str, Any]:
        return self.catalog.restore_database(database_id)

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.catalog.list_jobs(limit=limit)

    def retry_job(self, job_id: str) -> dict[str, Any]:
        """Retry a failed operation without reusing its accepted execution plan."""
        job = self.catalog.get_job(job_id)
        if str(job.get("state") or "") not in {"failed", "interrupted"}:
            raise BridgeError("OPERATION_UNSUPPORTED", "Only failed or interrupted operations can be retried.")
        kind = str(job.get("kind") or "")
        payload = self.catalog.get_job_payload(job_id)
        if kind == "import":
            preview_id = str(job.get("preview_id") or payload.get("preview_id") or "")
            if not preview_id:
                raise BridgeError("VALIDATION_FAILED", "The failed import does not have a review to retry.")
            preview = FrozenPreview.from_mapping(self.catalog.get_preview(preview_id))
            fresh_payload: dict[str, Any] = {"operation": preview.operation}
            if preview.database_id:
                fresh_payload["database_id"] = preview.database_id
            elif preview.draft_id:
                fresh_payload["draft_id"] = preview.draft_id
            if preview.operation == "remove_outdated":
                fresh_payload["delete_ids"] = list(preview.effects.delete_ids)
            return self.jobs.submit_preview(fresh_payload)
        if kind == "preview":
            return self.jobs.submit_preview(payload)
        if kind == "scan":
            return self.jobs.submit_scan(payload)
        if kind == "inspection":
            return self.jobs.submit_inspection(payload)
        if kind == "validation":
            return self.jobs.submit_validation(payload)
        if kind == "source":
            return self.jobs.submit_source_action(payload)
        if kind == "redundancy":
            return self.jobs.submit_redundancy(payload)
        if kind == "environment_repair":
            return self.jobs.submit_environment_repair(payload)
        if kind == "diagnostic":
            return self.jobs.submit_synthetic_progress(int(payload.get("seconds") or 10))
        raise BridgeError("OPERATION_UNSUPPORTED", f"Retry is not supported for job type {kind or 'unknown'}.")

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self.catalog.get_job(job_id)

    def get_job_events(self, job_id: str, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        return self.catalog.get_job_events(job_id, after=after, limit=limit)

    def get_database_history(self, database_id: str) -> dict[str, Any]:
        record = self.get_database(database_id)
        target = self._active_target(record)
        manifest: dict[str, Any] = {}
        metadata: dict[str, Any] = {}
        for filename, destination in (("import_manifest.json", manifest), ("podcast.json", metadata)):
            path = target / filename
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                destination.update(payload)
        jobs = [job for job in self.list_jobs(limit=500) if job.get("database_id") == database_id]
        return {
            "database_id": database_id,
            "target": str(target),
            "active_state": "active" if target.is_dir() else "unavailable",
            "active_representation": manifest.get("representation") or {},
            "active_release_id": manifest.get("corpus_release_id") or metadata.get("corpus_release_id"),
            "source_release_id": manifest.get("upstream_release_id") or metadata.get("upstream_release_id"),
            "manifest": manifest,
            "metadata_summary": {"display_name": metadata.get("podcast_name"), "episodes": len(metadata.get("episodes") or [])},
            "jobs": jobs,
            "tracking": {
                "link": self.catalog.get_database_link(database_id),
                "release_history": self.catalog.list_database_release_history(database_id),
            },
        }

    @staticmethod
    def _active_target(record: Mapping[str, Any]) -> Path:
        return resolve_active_export(record)

    def get_database_details(self, database_id: str) -> dict[str, Any]:
        return inspect_database(self.get_database(database_id))

    def get_database_content(self, database_id: str, *, selection_policy: Mapping[str, Any] | None = None, offset: int = 0, limit: int = 100, search: str = "") -> dict[str, Any]:
        return inventory_database(self.get_database(database_id), policy=selection_policy, offset=offset, limit=limit, search=search)

    def inspect_content(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        database_id = str(payload.get("database_id") or "").strip()
        if not database_id:
            raise BridgeError("VALIDATION_FAILED", "database_id is required.", field="database_id")
        policy = payload.get("selection_policy")
        if policy is not None and not isinstance(policy, Mapping):
            raise BridgeError("VALIDATION_FAILED", "selection_policy must be an object.", field="selection_policy")
        return self.get_database_content(database_id, selection_policy=policy, offset=int(payload.get("offset") or 0), limit=int(payload.get("limit") or 100), search=str(payload.get("search") or ""))

    def environment_report(self) -> dict[str, Any]:
        assets = Path(__file__).resolve().parent.parent / "desktop" / "assets" / "index.html"
        report: dict[str, Any] = {"python": os.sys.executable, "assets_ready": assets.is_file(), "pywebview": False, "cuda": None}
        try:
            import webview  # type: ignore
            try:
                from importlib.metadata import version

                report["pywebview"] = version("pywebview")
            except Exception:
                report["pywebview"] = getattr(webview, "__version__", "installed")
        except Exception as exc:
            report["pywebview_error"] = type(exc).__name__
        try:
            import torch  # type: ignore
            report["cuda"] = {"available": bool(torch.cuda.is_available()), "device_count": int(torch.cuda.device_count())}
        except Exception as exc:
            report["cuda_error"] = type(exc).__name__
        return report

    def get_app_defaults(self) -> dict[str, Any]:
        return self.catalog.get_app_setting("creation_defaults", {
            "output_parent": "", "asset_filter": "reviewed_speaker_transcript", "asset_pattern": "", "execution_options": {"embedding_device": "auto"},
        })

    def save_app_defaults(self, changes: Mapping[str, Any], *, base_revision: int | None = None) -> dict[str, Any]:
        allowed = {"output_parent", "asset_filter", "asset_pattern", "execution_options"}
        unknown = set(changes).difference(allowed)
        if unknown:
            raise BridgeError("VALIDATION_FAILED", f"Unsupported application default: {sorted(unknown)[0]}.")
        current = self.catalog.get_app_setting("creation_defaults", {
            "output_parent": "", "asset_filter": "reviewed_speaker_transcript", "asset_pattern": "", "execution_options": {"embedding_device": "auto"},
        })
        merged = dict(current["value"])
        merged.update(dict(changes))
        if "execution_options" in merged:
            from .models import ExecutionOptions

            merged["execution_options"] = ExecutionOptions.from_mapping(merged["execution_options"]).as_dict()
        if str(merged.get("asset_filter") or "") == "custom" and not str(merged.get("asset_pattern") or "").strip():
            raise BridgeError("VALIDATION_FAILED", "A filename pattern is required for the custom asset filter.", field="asset_pattern")
        return self.catalog.save_app_setting("creation_defaults", merged, base_revision=base_revision)

    def _settings_scope(self, scope_kind: str, reference: Mapping[str, Any] | None = None, *, include_redundancy: bool = False) -> dict[str, Any]:
        from .redundancy_adapter import get_settings

        kind = str(scope_kind or "").strip()
        if kind == "app_defaults":
            saved = self.get_app_defaults()
            return {"scope_kind": kind, "identity": {"application": "Chroma DB Import"}, "revision": int(saved.get("revision") or 0), "current": dict(saved.get("value") or {})}
        if kind == "database":
            ref = dict(reference or {})
            database_id = str(ref.get("database_id") or "").strip()
            record = DatabaseRecord.from_mapping(self.get_database(database_id))
            return {"scope_kind": kind, "identity": {"database_id": record.id, "target_path": record.target.get("path")}, "revision": record.settings_revision, "current": {"selection_policy": record.selection_policy, "execution_options": record.execution_options}}
        if kind == "context":
            context = self.get_context(dict(reference or {}))
            ref = dict(context["ref"])
            detail = dict(context.get("detail") or {})
            profile = dict((detail.get("profile") or {}).get("profile") or {})
            saved_options = dict(detail.get("context_settings") or {}).get("settings") or {"execution_options": {"embedding_device": "auto"}}
            current = {"profile": profile, "execution_options": dict(saved_options.get("execution_options") or {"embedding_device": "auto"})}
            if include_redundancy:
                current["redundancy_policy"] = dict(get_settings(str(context["catalog_path"]), str(ref["partition_id"])).get("policy") or {})
            return {"scope_kind": kind, "identity": {"connection_id": ref.get("connection_id"), "partition_id": ref.get("partition_id"), "corpus_id": context.get("corpus_id")}, "revision": int(saved_options.get("revision") or 0), "current": current, "profile_fingerprint": (detail.get("profile") or {}).get("profile_fingerprint")}
        raise BridgeError("VALIDATION_FAILED", "scope_kind is not supported.", field="scope_kind")

    def export_settings(self, scope_kind: str, reference: Mapping[str, Any] | None = None, *, include_redundancy: bool = False) -> dict[str, Any]:
        from .settings_transfer import build_transfer

        scope = self._settings_scope(scope_kind, reference, include_redundancy=include_redundancy)
        settings = dict(scope["current"])
        excluded = ["runtime_config", "secrets", "jobs", "active_release_pointer", "database_content"]
        if scope_kind == "context" and not include_redundancy:
            excluded.append("redundancy_policy")
        return build_transfer(scope_kind, scope["identity"], settings, excluded_fields=excluded)

    def save_settings_transfer(self, transfer: Mapping[str, Any], output_path: str) -> dict[str, Any]:
        from .settings_transfer import write_transfer

        return write_transfer(output_path, transfer)

    def preview_settings_import(self, path: str, scope_kind: str, reference: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from .settings_transfer import preview_import, read_transfer

        scope = self._settings_scope(scope_kind, reference, include_redundancy=True)
        proposal = preview_import(read_transfer(path, scope_kind=scope_kind, identity=scope["identity"]), scope, scope["current"], base_revision=int(scope["revision"]))
        review_dir = self.state_dir / "settings-reviews"
        review_dir.mkdir(parents=True, exist_ok=True)
        review = {**proposal, "input_path": str(Path(path).expanduser().resolve()), "scope_reference": dict(reference or {})}
        (review_dir / f"{proposal['review_id']}.json").write_text(json.dumps(review, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return review

    def apply_settings_import(self, path: str, proposal: Mapping[str, Any], selected_fields: list[str], scope_kind: str, reference: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from .settings_transfer import apply_import, read_transfer

        scope = self._settings_scope(scope_kind, reference, include_redundancy=True)
        transfer = read_transfer(path, scope_kind=scope_kind, identity=scope["identity"])
        applied = apply_import(proposal, scope["current"], selected_fields, current_revision=int(scope["revision"]), transfer=transfer)
        fields = set(applied["applied_fields"])
        if not fields:
            return {"applied_fields": [], "skipped_fields": list(applied["skipped_fields"]), "scope_kind": scope_kind, "revision": scope["revision"]}
        if scope_kind == "app_defaults":
            saved = self.catalog.save_app_setting("creation_defaults", applied["value"], base_revision=int(scope["revision"]))
            return {**applied, "scope_kind": scope_kind, "revision": saved["revision"]}
        if scope_kind == "database":
            database_id = str((scope["identity"] or {}).get("database_id") or "")
            record = self.get_database(database_id)
            changes = {field: applied["value"][field] for field in fields}
            changes["settings_revision"] = int(record["settings_revision"]) + 1
            saved = self.catalog.update_database(database_id, changes)
            return {**applied, "scope_kind": scope_kind, "revision": saved["settings_revision"]}
        context = self.get_context(dict(reference or {}))
        ref = dict(context["ref"])
        from chroma_db_import.managed import ManagedCatalog

        with ManagedCatalog(Path(str(context["catalog_path"]))) as catalog:
            new_profile = applied["value"].get("profile") if "profile" in fields else None
            new_settings = {"execution_options": applied["value"].get("execution_options", scope["current"].get("execution_options"))} if "execution_options" in fields else None
            new_redundancy = applied["value"].get("redundancy_policy") if "redundancy_policy" in fields else None
            saved = catalog.save_context_settings_bundle(ref["partition_id"], profile=new_profile, settings=new_settings, redundancy_policy=new_redundancy, base_revision=int(scope["revision"]), base_profile_fingerprint=str(scope.get("profile_fingerprint") or ""))
        return {**applied, "scope_kind": scope_kind, "revision": saved["revision"], "profile_fingerprint": saved.get("profile_fingerprint"), "policy_fingerprint": saved.get("policy_fingerprint")}

    def apply_settings_review(self, review_id: str, selected_fields: list[str]) -> dict[str, Any]:
        if not review_id or Path(review_id).name != review_id:
            raise BridgeError("VALIDATION_FAILED", "The settings review ID is invalid.", field="review_id")
        review_path = (self.state_dir / "settings-reviews" / f"{review_id}.json").resolve()
        try:
            review_path.relative_to((self.state_dir / "settings-reviews").resolve())
        except ValueError as exc:
            raise BridgeError("VALIDATION_FAILED", "The settings review ID is invalid.", field="review_id") from exc
        if not review_path.is_file():
            raise BridgeError("IDENTITY_UNRESOLVED", "The settings review is no longer available.", field="review_id")
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BridgeError("VALIDATION_FAILED", "The settings review is unreadable.") from exc
        return self.apply_settings_import(str(review.get("input_path") or ""), review, selected_fields, str(review.get("scope_kind") or ""), review.get("scope_reference") or None)

    def preview_environment_repair(self) -> dict[str, Any]:
        from .environment_repair import build_review

        review = build_review(self.state_dir.parent.parent)
        review_dir = self.state_dir / "environment-repair"
        review_dir.mkdir(parents=True, exist_ok=True)
        (review_dir / f"{review['review_id']}.json").write_text(json.dumps(review, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return {**review, "review_path": str(review_dir / f"{review['review_id']}.json")}

    def apply_environment_repair(self, review_id: str) -> dict[str, Any]:
        from .environment_repair import validate_review

        review_path = (self.state_dir / "environment-repair" / f"{review_id}.json").resolve()
        root = self.state_dir.parent.parent
        try:
            review_path.relative_to((self.state_dir / "environment-repair").resolve())
        except ValueError as exc:
            raise BridgeError("VALIDATION_FAILED", "The repair review ID is invalid.", field="review_id") from exc
        if not review_path.is_file():
            raise BridgeError("IDENTITY_UNRESOLVED", "The environment repair review is no longer available.", field="review_id")
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BridgeError("VALIDATION_FAILED", "The environment repair review is unreadable.") from exc
        validate_review(review, root)
        return self.jobs.submit_environment_repair({"review_id": review_id, "review_path": str(review_path), "root": str(root)})

    def list_source_connections(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return self.catalog.list_source_connections(include_archived=include_archived)

    def reconcile_sources(self) -> dict[str, Any]:
        from .sources import reconcile_sources

        return reconcile_sources(self.catalog, self.state_dir)

    def adopt_database_link(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        from .reconciliation import adopt_database_link

        return adopt_database_link(self.catalog, payload, self.state_dir)

    def select_database_link(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        from .reconciliation import select_database_link

        return select_database_link(self.catalog, payload)

    def link_source(self, source_root: str) -> dict[str, Any]:
        from .sources import link_source

        return link_source(self.catalog, self.state_dir, source_root)

    def discover_contexts(self, connection_id: str) -> dict[str, Any]:
        from .sources import discover_contexts

        return discover_contexts(self.catalog, connection_id)

    def list_contexts(self, *, connection_id: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]:
        from .sources import list_contexts

        return list_contexts(self.catalog, connection_id=connection_id, include_archived=include_archived)

    def get_context(self, ref: Mapping[str, Any]) -> dict[str, Any]:
        from .sources import get_context

        return get_context(self.catalog, ref)

    def set_context_archived(self, ref: Mapping[str, Any], archived: bool) -> dict[str, Any]:
        from .sources import set_context_archived

        return set_context_archived(self.catalog, ref, archived)

    def save_context_import_defaults(self, ref: Mapping[str, Any], changes: Mapping[str, Any], *, base_revision: int | None = None) -> dict[str, Any]:
        allowed = {"execution_options", "selection_policy"}
        unknown = set(changes).difference(allowed)
        if unknown:
            raise BridgeError("VALIDATION_FAILED", f"Unsupported context import default: {sorted(unknown)[0]}.")
        context = self.get_context(ref)
        detail = dict(context.get("detail") or {})
        current_profile = dict((detail.get("profile") or {}).get("profile") or {})
        current_settings = dict((detail.get("context_settings") or {}).get("settings") or {"execution_options": {"embedding_device": "auto"}})
        new_profile = None
        if "selection_policy" in changes:
            new_profile = {**current_profile, "selection_policy": SelectionPolicy.from_mapping(changes["selection_policy"]).as_dict()}
        new_settings = None
        if "execution_options" in changes:
            from .models import ExecutionOptions

            new_settings = {**current_settings, "execution_options": ExecutionOptions.from_mapping(changes["execution_options"]).as_dict()}
        from chroma_db_import.managed import ManagedCatalog

        with ManagedCatalog(Path(str(context["catalog_path"]))) as catalog:
            saved = catalog.save_context_settings_bundle(
                str(context["ref"]["partition_id"]), profile=new_profile, settings=new_settings,
                base_revision=base_revision, base_profile_fingerprint=str((detail.get("profile") or {}).get("profile_fingerprint") or ""),
            )
        return {"context": context["ref"], "revision": saved["revision"], "profile_fingerprint": saved.get("profile_fingerprint"), "settings": saved.get("settings")}

    def save_context_defaults(self, ref: Mapping[str, Any], base_profile_fingerprint: str | None, profile_changes: Mapping[str, Any], execution_options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        changes: dict[str, Any] = {"selection_policy": dict(profile_changes.get("selection_policy") or profile_changes)}
        if execution_options is not None:
            changes["execution_options"] = dict(execution_options)
        context = self.get_context(ref)
        detail = dict(context.get("detail") or {})
        current_profile_fp = str((detail.get("profile") or {}).get("profile_fingerprint") or "")
        if base_profile_fingerprint and str(base_profile_fingerprint) != current_profile_fp:
            raise BridgeError("STALE_SETTINGS", "Context import defaults changed in another window. Reload before saving.", field="base_profile_fingerprint")
        return self.save_context_import_defaults(ref, changes, base_revision=int((detail.get("context_settings") or {}).get("revision") or 0))

    def open_context_folder(self, ref: Mapping[str, Any]) -> str:
        from .sources import open_context_folder

        return open_context_folder(self.catalog, ref)

    def repair_partition_lock(self, partition_root: str, *, confirm: bool = False, grace_timeout: float = 10.0) -> dict[str, Any]:
        """Repair one physical partition without consulting or changing the catalog."""
        root = Path(str(partition_root).strip()).expanduser()
        if not root.is_absolute():
            raise BridgeError("VALIDATION_FAILED", "The physical partition path must be absolute.", field="partition_root")
        if not root.name or root.name in {".", ".."}:
            raise BridgeError("VALIDATION_FAILED", "A physical partition path is required.", field="partition_root")
        if grace_timeout < 0 or grace_timeout > 120:
            raise BridgeError("VALIDATION_FAILED", "grace_timeout must be between 0 and 120 seconds.", field="grace_timeout")
        from chroma_db_import.lock_repair import repair_partition_lock

        return repair_partition_lock(root, confirm=confirm, grace_timeout=grace_timeout)

    def get_source_suggestions(self, current_path: str | None = None) -> list[dict[str, str]]:
        from .folder_inspector import suggestions

        return suggestions(current_path)

    def list_source_folders(self, root: str, relative_path: str | None = None) -> list[dict[str, Any]]:
        from .folder_inspector import list_folders

        return list_folders(root, relative_path)

    def inspect_folder(self, path: str, asset_filter: str, asset_pattern: str = "") -> dict[str, Any]:
        from .folder_inspector import inspect_folder

        return inspect_folder(path, asset_filter, asset_pattern)

    def get_redundancy_settings(self, ref: Mapping[str, Any]) -> dict[str, Any]:
        from .redundancy_adapter import get_settings

        context = self.get_context(ref)
        settings = get_settings(str(context["catalog_path"]), str(context["ref"]["partition_id"]))
        settings["judge_config_fingerprint"] = stable_hash(settings.get("judge_config") or {})
        return {"context": context["ref"], **settings}

    def save_redundancy_policy(self, ref: Mapping[str, Any], changes: Mapping[str, Any], base_fingerprint: str | None = None) -> dict[str, Any]:
        from .redundancy_adapter import save_policy

        context = self.get_context(ref)
        return save_policy(str(context["catalog_path"]), str(context["ref"]["partition_id"]), changes, base_fingerprint)

    def start_redundancy_action(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        from .redundancy_adapter import validate_action

        normalized = validate_action(payload)
        return self.jobs.submit_redundancy(normalized)

    def preview_context_dedup(self, ref: Mapping[str, Any], upstream_release_id: str | None = None) -> dict[str, Any]:
        context = self.get_context(ref)
        from chroma_db_import.config import ImportConfig
        from chroma_db_import.managed import ManagedCatalog, resolve_managed_config, run_managed_import

        try:
            with ManagedCatalog(Path(str(context["catalog_path"]))) as catalog:
                profile = catalog.profile(str(context["ref"]["partition_id"]))
                settings = catalog.context_settings(str(context["ref"]["partition_id"]))
                config = resolve_managed_config(ImportConfig(), profile)
                config.embedding_device = str(((settings.get("settings") or {}).get("execution_options") or {}).get("embedding_device") or "auto")
                result = run_managed_import(config, Path(str(context["source_root"])), catalog, str(context["ref"]["partition_id"]), upstream_release_id=upstream_release_id, output_root=Path(str(context["source_root"])) / "exports", dry_run=True, validation_only=True, selection_policy=(profile or {}).get("profile", {}).get("selection_policy"))
            return make_report("dedup_preview", {"context": context["ref"], "upstream_release_id": result.get("upstream_release_id")}, summary=dict(result.get("dedup") or {}), details={"plan_fingerprint": result.get("plan_fingerprint"), "profile_fingerprint": result.get("import_profile_fingerprint"), "embedding_work": 0, "active_database_state": "unchanged"})
        except Exception as exc:
            return make_report("dedup_preview", {"context": context["ref"], "upstream_release_id": upstream_release_id}, status="unavailable", findings=[{"severity": "error", "code": "DEDUP_PREVIEW_UNAVAILABLE", "message": str(exc)}], details={"active_database_state": "unchanged"})

    def get_context_dedup_settings(self, ref: Mapping[str, Any]) -> dict[str, Any]:
        from .redundancy_adapter import get_dedup_settings

        context = self.get_context(ref)
        return {"context": context["ref"], **get_dedup_settings(str(context["catalog_path"]), str(context["ref"]["partition_id"]))}

    def save_context_dedup_policy(self, ref: Mapping[str, Any], changes: Mapping[str, Any], base_profile_fingerprint: str | None = None) -> dict[str, Any]:
        from .redundancy_adapter import save_dedup_policy

        context = self.get_context(ref)
        return save_dedup_policy(str(context["catalog_path"]), str(context["ref"]["partition_id"]), changes, base_profile_fingerprint)

    def review_context_dedup(self, ref: Mapping[str, Any], upstream_release_id: str | None = None) -> dict[str, Any]:
        from .redundancy_adapter import review_dedup_export

        context = self.get_context(ref)
        candidates = [self.get_database(database_id) for database_id in context.get("matching_database_ids") or []]
        if len(candidates) != 1:
            return make_report("dedup_review", {"context": context["ref"], "upstream_release_id": upstream_release_id}, status="unavailable", findings=[{"severity": "warning", "code": "ACTIVE_EXPORT_UNRESOLVED", "message": "A single linked database is required for active deduplication review."}])
        try:
            export = resolve_active_export(candidates[0])
        except BridgeError as exc:
            return make_report("dedup_review", {"context": context["ref"], "upstream_release_id": upstream_release_id}, status="unavailable", findings=[{"severity": "error", "code": exc.code, "message": exc.message}])
        return review_dedup_export(export, scope={"context": context["ref"], "upstream_release_id": upstream_release_id})

    def validate_database(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        database_id = str(payload.get("database_id") or "").strip()
        scope = str(payload.get("scope") or "both")
        if scope not in {"source", "database", "both"}:
            raise BridgeError("VALIDATION_FAILED", "Validation scope must be source, database, or both.", field="scope")
        record = DatabaseRecord.from_mapping(self.get_database(database_id))
        reports: dict[str, Any] = {}
        if scope in {"database", "both"}:
            reports["database"] = inspect_database(record.as_dict())
        if scope in {"source", "both"}:
            try:
                if record.source_kind == "folder":
                    reports["source"] = self.inspect_folder(str(record.source_ref.get("path") or ""), str(record.selection_policy.get("asset_filter") or "reviewed"), str(record.selection_policy.get("asset_pattern") or ""))
                else:
                    reports["source"] = make_report("source_validation", {"database_id": database_id}, details=self.managed_adapter.inspect(Path(str(record.source_ref.get("source_root") or "")), str(record.source_ref.get("partition_id") or "")))
            except Exception as exc:
                reports["source"] = make_report("source_validation", {"database_id": database_id}, status="unavailable", findings=[{"severity": "error", "code": "SOURCE_UNAVAILABLE", "message": str(exc)}])
        return make_report("database_validation", {"database_id": database_id}, status="warnings" if any(item.get("status") in {"warnings", "unavailable"} for item in reports.values() if isinstance(item, Mapping)) else "pass", details={"scope": scope, "reports": reports})

    def save_judge_config(self, ref: Mapping[str, Any], changes: Mapping[str, Any], base_fingerprint: str | None = None) -> dict[str, Any]:
        context = self.get_context(ref)
        current = dict((context.get("detail") or {}).get("judge_config") or {})
        if base_fingerprint and str(base_fingerprint) != stable_hash(current):
            raise BridgeError("STALE_SETTINGS", "Judge configuration changed in another window. Reload before saving.")
        allowed = {"base_url", "model", "model_fingerprint", "timeout_seconds", "auth_ref"}
        if set(changes).difference(allowed) or not str(changes.get("model") or "").strip():
            raise BridgeError("VALIDATION_FAILED", "A supported judge model is required.", field="model")
        from chroma_db_import.managed import ManagedCatalog

        with ManagedCatalog(Path(str(context["catalog_path"]))) as catalog:
            catalog.set_redundancy_judge_config(str(context["ref"]["partition_id"]), dict(changes))
        return {"judge_config": dict(changes), "judge_config_fingerprint": stable_hash(dict(changes)), "saved_at": utc_now()}

    def probe_judge(self, ref: Mapping[str, Any], changes: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Check a loopback OpenAI-compatible endpoint without changing settings."""
        context = self.get_context(ref)
        saved = dict((context.get("detail") or {}).get("judge_config") or {})
        candidate = {**saved, **dict(changes or {})}
        model = str(candidate.get("model") or "").strip()
        if not model:
            raise BridgeError("VALIDATION_FAILED", "Enter the exact model ID served by the local endpoint before testing it.", field="model")
        try:
            from chroma_db_import.local_judge_client import LMStudioClient, LocalJudgeError

            client = LMStudioClient(
                str(candidate.get("base_url") or "http://127.0.0.1:1234/v1"),
                model=model,
                timeout=float(candidate.get("timeout_seconds") or 30),
            )
            models = client.list_models()
        except LocalJudgeError as exc:
            raise BridgeError("SOURCE_UNAVAILABLE", f"The local judge endpoint could not be reached: {exc}") from exc
        available = [str(item.get("id") or item.get("model") or "") for item in models if str(item.get("id") or item.get("model") or "")]
        return {
            "reachable": True,
            "configured_model": model,
            "model_available": model in available,
            "models": available,
            "base_url": str(candidate.get("base_url") or "http://127.0.0.1:1234/v1"),
            "checked_at": utc_now(),
        }

    def review_judge_pilot(self, ref: Mapping[str, Any], upstream_release_id: str, channels: list[str]) -> dict[str, Any]:
        context = self.get_context(ref)
        allowed_channels = {"lexical", "structural", "dense"}
        normalized_channels = sorted({str(item) for item in channels if str(item)})
        if not normalized_channels or not set(normalized_channels) <= allowed_channels:
            raise BridgeError("VALIDATION_FAILED", "Select at least one analysis channel.", field="channels")
        analyzable = next((item for item in context.get("release_inventory") or [] if str(item.get("upstream_release_id") or "") == str(upstream_release_id) and item.get("analyzable")), None)
        if not analyzable:
            raise BridgeError("ANALYSIS_UNAVAILABLE", "The selected release has no validated analysis export.")
        settings = self.get_redundancy_settings(ref)
        policy = dict(settings.get("policy") or {})
        if not bool(policy.get("judge_enabled")):
            raise BridgeError("VALIDATION_FAILED", "Enable the semantic judge in the analysis policy before reviewing a pilot.", field="judge_enabled")
        judge = dict(settings.get("judge_config") or {})
        if not str(judge.get("model") or "").strip():
            raise BridgeError("VALIDATION_FAILED", "Save a judge model before reviewing a pilot.", field="model")
        export_path = Path(str(analyzable.get("export_path") or "")).expanduser().resolve()
        review = {"schema_version": "gui-judge-pilot-review-v1", "review_id": new_id("judge_review"), "context": context["ref"], "upstream_release_id": upstream_release_id, "export_path": str(export_path), "export_hash": self._analysis_export_hash(export_path), "channels": normalized_channels, "policy_fingerprint": settings.get("policy_fingerprint"), "judge_config_fingerprint": stable_hash(judge), "candidate_fraction": 0.1, "maximum_calls": 20, "calculated_upper_bound": 20, "created_at": utc_now()}
        review_dir = self.state_dir / "redundancy-reviews"
        review_dir.mkdir(parents=True, exist_ok=True)
        (review_dir / f"{review['review_id']}.json").write_text(json.dumps(review, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return review

    @staticmethod
    def _analysis_export_hash(path: Path) -> str:
        if not path.is_dir():
            return ""
        digest = hashlib.sha256()
        for relative in ("import_manifest.json", "podcast.json", "chroma.sqlite3"):
            candidate = path / relative
            if not candidate.is_file():
                return ""
            digest.update(relative.encode("utf-8"))
            with candidate.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    def _validated_pilot_review(self, review_id: str, context: Mapping[str, Any], requested_channels: list[str] | None) -> dict[str, Any]:
        if not review_id or Path(review_id).name != review_id:
            raise BridgeError("VALIDATION_FAILED", "A reviewed pilot ID is required.", field="review_id")
        review_path = (self.state_dir / "redundancy-reviews" / f"{review_id}.json").resolve()
        try:
            review_path.relative_to((self.state_dir / "redundancy-reviews").resolve())
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            raise BridgeError("IDENTITY_UNRESOLVED", "The reviewed pilot is no longer available.", field="review_id") from exc
        if review.get("schema_version") != "gui-judge-pilot-review-v1" or dict(review.get("context") or {}) != dict(context.get("ref") or {}):
            raise BridgeError("STALE_SETTINGS", "The reviewed pilot does not match the selected context.")
        channels = sorted({str(item) for item in review.get("channels") or [] if str(item)})
        if not channels or not set(channels) <= {"lexical", "structural", "dense"}:
            raise BridgeError("VALIDATION_FAILED", "The reviewed pilot has no valid channels.", field="review_id")
        if requested_channels is not None and sorted({str(item) for item in requested_channels if str(item)}) != channels:
            raise BridgeError("STALE_SETTINGS", "The selected channels differ from the reviewed pilot.", field="channels")
        settings = self.get_redundancy_settings(dict(context["ref"]))
        judge = dict(settings.get("judge_config") or {})
        if str(review.get("policy_fingerprint") or "") != str(settings.get("policy_fingerprint") or "") or str(review.get("judge_config_fingerprint") or "") != stable_hash(judge):
            raise BridgeError("STALE_SETTINGS", "Policy or judge settings changed after the pilot review.")
        release_id = str(review.get("upstream_release_id") or "")
        release = next((item for item in context.get("release_inventory") or [] if str(item.get("upstream_release_id") or "") == release_id and item.get("analyzable")), None)
        if not release or str(Path(str(release.get("export_path") or "")).expanduser().resolve()) != str(review.get("export_path") or "") or self._analysis_export_hash(Path(str(release.get("export_path") or "")).expanduser().resolve()) != str(review.get("export_hash") or ""):
            raise BridgeError("STALE_SETTINGS", "The reviewed analysis export changed after the pilot review.")
        return {**review, "channels": channels}

    def _run_redundancy_action(self, payload: Mapping[str, Any], *, job_id: str) -> dict[str, Any]:
        from .redundancy_adapter import coverage_preview, validate_action, validate_redundancy_bundle

        normalized = validate_action(payload)
        action = str(normalized["action"])
        if action in {"label_export", "evaluate"}:
            artifact_id = str(normalized.get("artifact_id") or "")
            artifact_file = (self.state_dir / "redundancy-artifacts" / f"{artifact_id}.json").resolve()
            if not artifact_file.is_file():
                raise BridgeError("IDENTITY_UNRESOLVED", "The selected redundancy bundle is no longer available.", field="artifact_id")
            try:
                artifact = json.loads(artifact_file.read_text(encoding="utf-8"))
                bundle_path = str(artifact.get("path") or "")
            except (OSError, json.JSONDecodeError) as exc:
                raise BridgeError("BUNDLE_INVALID", "The selected redundancy bundle reference is unreadable.") from exc
            result = validate_redundancy_bundle(bundle_path, expected_scope=normalized.get("context"))
            if result.get("artifact_id") is None:
                return result
            if action == "label_export":
                from .redundancy_adapter import label_export

                result = {"report": label_export(bundle_path, str(normalized.get("output_path") or ""), expected_scope=normalized.get("context")), "artifact_id": artifact_id}
            else:
                from .redundancy_adapter import evaluate_export

                result = {"report": evaluate_export(bundle_path, str(normalized.get("labels_path") or ""), queries_path=normalized.get("queries_path"), query_results_path=normalized.get("query_results_path"), output_path=str(normalized.get("output_path") or ""), expected_scope=normalized.get("context")), "artifact_id": artifact_id}
        elif action in {"assess", "pilot", "resume"}:
            from chroma_db_import.managed import ManagedCatalog
            from chroma_db_import.redundancy_cli import _run_assessment

            context = self.get_context(normalized.get("context") or {})
            pilot_review = self._validated_pilot_review(str(normalized.get("review_id") or ""), context, list(normalized.get("channels") or []) or None) if action == "pilot" else None
            if pilot_review is not None:
                normalized = {**normalized, "context": context["ref"], "upstream_release_id": pilot_review["upstream_release_id"], "channels": pilot_review["channels"]}
            release_id = str(normalized.get("upstream_release_id") or "")
            frozen_job_id = str(normalized.get("frozen_job_id") or "") or None
            if not release_id and frozen_job_id:
                release_id = str(next((item.get("upstream_release_id") for item in context.get("release_inventory") or [] if item.get("analyzable")), "") or "")
            if not release_id:
                release_id = str(next((item.get("upstream_release_id") for item in context.get("release_inventory") or [] if item.get("analyzable")), "") or "")
            if not release_id:
                raise BridgeError("ANALYSIS_UNAVAILABLE", "Select a release with a validated analysis export before continuing.")
            cancel_path = self.catalog.path.parent / "job-cancel" / job_id
            try:
                with ManagedCatalog(Path(str(context["catalog_path"]))) as catalog:
                    exit_code, assessment = _run_assessment(catalog, str(context["ref"]["partition_id"]), release_id, channels=list(normalized.get("channels") or []) or None, judge_requested=(True if action == "pilot" else False if action == "assess" else None), resume_job_id=frozen_job_id, cancel_file=cancel_path, progress_callback=lambda current, total, message: self.catalog.add_event(job_id, "analyzing", message, {"current": current, "total": total}))
                status = "cancelled" if assessment.get("status") == "cancelled" else ("partial" if exit_code == 2 or assessment.get("status") == "completed_partial" else ("pass" if exit_code == 0 else "failed"))
                result = {"report": make_report("redundancy_assessment", {"context": context["ref"], "release_id": release_id}, status=status, summary={"action": action, **{key: value for key, value in assessment.items() if key not in {"publication"}}}, details={"assessment_job_id": assessment.get("job_id"), "bundle_id": assessment.get("bundle_id"), "publication": assessment.get("publication"), "exit_code": exit_code}), "artifact_id": assessment.get("bundle_id")}
            except Exception as exc:
                result = {"report": make_report("redundancy_assessment", {"context": context["ref"], "release_id": release_id}, status="failed", findings=[{"severity": "error", "code": "ASSESSMENT_FAILED", "message": str(exc)}], details={"assessment_job_id": f"ui_{job_id}", "active_database_state": "unchanged"}), "artifact_id": None}
            finally:
                try:
                    cancel_path.unlink(missing_ok=True)
                except OSError:
                    pass
        else:
            context = self.get_context(normalized.get("context") or {})
            release_id = str(normalized.get("upstream_release_id") or "")
            releases = list(context.get("release_inventory") or [])
            release = next((item for item in releases if str(item.get("upstream_release_id") or "") == release_id), None) if release_id else next((item for item in releases if item.get("analyzable")), None)
            if not release or not release.get("analyzable") or not release.get("export_path"):
                raise BridgeError("ANALYSIS_UNAVAILABLE", "Select a release with a validated analysis export before continuing.")
            result = {"report": coverage_preview(str(release["export_path"]), scope={"context": context["ref"], "release_id": release["upstream_release_id"]}), "artifact_id": None}
            if action in {"assess", "pilot"}:
                result["report"]["status"] = "partial" if result["report"].get("status") == "pass" else result["report"].get("status")
                result["report"]["details"]["assessment_job_id"] = f"ui_{job_id}"
        saved = self.export_report(result["report"])
        result["report_id"] = saved["report_id"]
        result["report_path"] = saved["path"]
        return result

    def open_redundancy_bundle(self, path: str, expected_scope: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from .redundancy_adapter import validate_redundancy_bundle

        result = validate_redundancy_bundle(path, expected_scope=expected_scope)
        artifact_id = result.get("artifact_id")
        if artifact_id:
            artifact_dir = self.state_dir / "redundancy-artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / f"{artifact_id}.json").write_text(json.dumps({"artifact_id": artifact_id, "path": str(Path(path).expanduser().resolve()), "scope": expected_scope or {}}, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return result

    def migration_candidates(self) -> dict[str, Any]:
        """Return read-only proposals from legacy UI state and managed catalogs."""
        roots = {
            Path.cwd().resolve(),
            self.state_dir.parent.resolve(),
            self.state_dir.parent.parent.resolve(),
        }
        candidates: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        legacy_fields = (
            "podcast_name", "database_id", "processed_data_dir", "output_root", "collection_name",
            "asset_filter", "asset_pattern", "representation_profile", "embedding_model",
            "embedding_model_revision", "embedding_device", "included_speakers_by_episode",
        )
        for root in sorted(roots, key=str):
            state_path = root / "state" / "ui_state.json"
            normalized = os.path.normcase(str(state_path.resolve())).casefold()
            if normalized in seen_paths or not state_path.is_file():
                continue
            seen_paths.add(normalized)
            try:
                raw = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                candidates.append({
                    "kind": "legacy_ui_state", "path": str(state_path), "status": "unreadable",
                    "message": f"Legacy UI state could not be read: {type(exc).__name__}.",
                })
                continue
            if not isinstance(raw, dict):
                candidates.append({
                    "kind": "legacy_ui_state", "path": str(state_path), "status": "unresolved",
                    "message": "Legacy UI state is not an object and needs manual review.",
                })
                continue
            fields = {name: raw[name] for name in legacy_fields if name in raw}
            candidates.append({
                "kind": "legacy_ui_state", "path": str(state_path), "status": "proposal",
                "source_hash": stable_hash(raw),
                "changes": {
                    "display_name": str(fields.get("podcast_name") or "").strip(),
                    "source_ref": {"path": str(fields.get("processed_data_dir") or "")},
                    "target": {
                        "path": str(fields.get("output_root") or ""),
                        "collection_name": str(fields.get("collection_name") or ""),
                    },
                    "selection_policy": {
                        "asset_filter": str(fields.get("asset_filter") or "reviewed"),
                        "asset_pattern": str(fields.get("asset_pattern") or ""),
                        "speaker_mode": "all",
                        "allowlist_speakers": [],
                        "excluded_speakers": [],
                        "episode_overrides": fields.get("included_speakers_by_episode") or {},
                        "excluded_episode_ids": [],
                    },
                    "representation": {
                        "profile": str(fields.get("representation_profile") or ""),
                        "model_id": str(fields.get("embedding_model") or ""),
                        "model_revision": str(fields.get("embedding_model_revision") or ""),
                        "device": str(fields.get("embedding_device") or ""),
                    },
                },
                "original_preserved": True,
            })

        for root in sorted(roots, key=str):
            catalog_path = root / "state" / "context_catalog.sqlite3"
            normalized_catalog = os.path.normcase(str(catalog_path.resolve())).casefold()
            if normalized_catalog in seen_paths or not catalog_path.is_file():
                continue
            seen_paths.add(normalized_catalog)
            try:
                from chroma_db_import.managed import ManagedCatalog

                catalog = ManagedCatalog(catalog_path)
                try:
                    contexts = catalog.contexts()
                finally:
                    catalog.connection.close()
            except Exception as exc:
                candidates.append({
                    "kind": "managed_context_catalog", "path": str(catalog_path), "status": "unreadable",
                    "message": f"Managed context catalog could not be read: {type(exc).__name__}.",
                })
            else:
                for context in contexts:
                    candidates.append({
                        "kind": "managed_context", "path": str(catalog_path), "status": "proposal",
                        "source_hash": stable_hash(context), "original_preserved": True,
                        "changes": {
                            "display_name": str(context.get("display_name") or ""),
                            "source_kind": "managed",
                            "source_ref": {
                                "source_root": str(context.get("source_root") or ""),
                                "partition_id": str(context.get("partition_id") or ""),
                                "corpus_id": str(context.get("corpus_id") or ""),
                            },
                            "managed_profile": {
                                "workflow_profile": str(context.get("workflow_profile") or ""),
                                "partition_config_fingerprint": str(context.get("partition_config_fingerprint") or ""),
                            },
                        },
                    })
        return {"candidates": candidates, "originals_unchanged": True}

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self.jobs.cancel(job_id)

    def save_draft(self, payload: Mapping[str, Any], *, draft_id: str | None = None, database_id: str | None = None) -> dict[str, Any]:
        return self.catalog.save_draft(payload, draft_id=draft_id, database_id=database_id)

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        return self.catalog.get_draft(draft_id)

    def scan_source(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        source_kind = self._validate_source_payload(payload)
        if source_kind not in {"folder", "managed"}:
            raise BridgeError("VALIDATION_FAILED", "Source kind must be folder or managed.", field="source_kind")
        if source_kind == "managed":
            root_raw = str(payload["source_root"])
            if not root_raw:
                raise BridgeError("SOURCE_UNAVAILABLE", "A managed source root is required.", field="source_root")
            root = Path(root_raw)
            catalog_path = str(payload.get("catalog_path") or "").strip() or None
            return self.managed_adapter.inspect(root, str(payload.get("partition_id") or ""), catalog_path)
        source_path = str(payload["path"])
        policy = SelectionPolicy.from_mapping(payload.get("selection_policy"))
        return scan_folder(Path(source_path), policy)

    @staticmethod
    def _validate_source_payload(payload: Mapping[str, Any]) -> str:
        if not isinstance(payload, Mapping):
            raise BridgeError("VALIDATION_FAILED", "The source request must be an object.")
        raw_source_kind = payload.get("source_kind")
        if raw_source_kind is not None and not isinstance(raw_source_kind, str):
            raise BridgeError("VALIDATION_FAILED", "Source kind must be a string.", field="source_kind")
        source_kind = str(raw_source_kind or "folder")
        if source_kind not in {"folder", "managed"}:
            raise BridgeError("VALIDATION_FAILED", "Source kind must be folder or managed.", field="source_kind")
        if source_kind == "managed":
            for field_name in ("source_root", "partition_id"):
                value = payload.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    code = "SOURCE_UNAVAILABLE" if field_name == "source_root" else "VALIDATION_FAILED"
                    raise BridgeError(code, f"A managed {field_name.replace('_', ' ')} is required.", field=field_name)
        else:
            value = payload.get("path")
            if not isinstance(value, str) or not value.strip():
                raise BridgeError("SOURCE_UNAVAILABLE", "A processed source folder is required.", field="path")
        selection_policy = payload.get("selection_policy")
        if selection_policy is not None and not isinstance(selection_policy, Mapping):
            raise BridgeError("VALIDATION_FAILED", "selection_policy must be an object.", field="selection_policy")
        SelectionPolicy.from_mapping(selection_policy)
        for field_name in ("database_id",):
            value = payload.get(field_name)
            if value is not None and not isinstance(value, str):
                raise BridgeError("VALIDATION_FAILED", f"{field_name} must be a string.", field=field_name)
        return source_kind

    def inspect_existing(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        target_value = payload.get("target_path")
        if not isinstance(target_value, str) or not target_value.strip():
            raise BridgeError("VALIDATION_FAILED", "An existing database folder is required.", field="target_path")
        target_raw = target_value.strip()
        target = Path(target_raw).expanduser().resolve()
        raw_source_kind = payload.get("source_kind")
        if raw_source_kind is not None and not isinstance(raw_source_kind, str):
            raise BridgeError("VALIDATION_FAILED", "Source kind must be a string.", field="source_kind")
        source_kind = str(raw_source_kind or "folder")
        if source_kind not in {"folder", "managed"}:
            raise BridgeError("VALIDATION_FAILED", "Source kind must be folder or managed.", field="source_kind")
        if source_kind == "managed":
            source_root_value = payload.get("source_root")
            partition_value = payload.get("partition_id")
            if not isinstance(source_root_value, str) or not source_root_value.strip():
                raise BridgeError("SOURCE_UNAVAILABLE", "A managed source root is required.", field="source_root")
            if not isinstance(partition_value, str) or not partition_value.strip():
                raise BridgeError("VALIDATION_FAILED", "A managed partition is required.", field="partition_id")
            source_root_raw = source_root_value.strip()
            partition_id = partition_value.strip()
            source_root = Path(source_root_raw).expanduser().resolve()
            status = self.managed_adapter.inspect(source_root, partition_id)
            from chroma_db_import.managed import managed_paths

            output_root = target
            if output_root.name == partition_id and output_root.parent.name.casefold() == "partitions":
                output_root = output_root.parent.parent
            paths = managed_paths(output_root, partition_id)
            partition_root = paths["partition_root"]
            active_release_id, active_export = self.managed_adapter._active_export(output_root, partition_id, partition_root)
            metadata: dict[str, Any] = {}
            manifest: dict[str, Any] = {}
            if active_export:
                for filename, destination in (("podcast.json", metadata), ("import_manifest.json", manifest)):
                    path = active_export / filename
                    if not path.is_file():
                        continue
                    try:
                        loaded = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if isinstance(loaded, dict):
                        destination.update(loaded)
            representation = dict(manifest.get("representation") or {})
            identity = {
                "database_id": metadata.get("database_id") or manifest.get("database_id"),
                "collection_name": manifest.get("collection_name") or metadata.get("collection_name"),
                "representation_id": manifest.get("representation_id") or metadata.get("representation_id") or representation.get("representation_id"),
                "embedding_model": manifest.get("embedding_model") or metadata.get("embedding_model"),
                "profile": manifest.get("representation_profile") or representation.get("profile") or metadata.get("representation_profile"),
                "upstream_release_id": manifest.get("upstream_release_id") or metadata.get("upstream_release_id"),
                "downstream_release_id": manifest.get("downstream_release_id") or manifest.get("release_id") or metadata.get("downstream_release_id") or metadata.get("release_id"),
            }
            warnings = list(status.get("warnings") or [])
            if not active_export:
                warnings.append("No active managed export was found; resolve the identity before updating.")
            if not identity["database_id"] or not identity["collection_name"]:
                identity_status = "unresolved"
                if "Database metadata does not contain a stable ID and collection name." not in warnings:
                    warnings.append("Database metadata does not contain a stable ID and collection name.")
            else:
                identity_status = "resolved"
            return {
                "target": {
                    "path": str(partition_root),
                    "normalized": os.path.normcase(str(partition_root)).casefold(),
                    "managed_output_root": str(output_root),
                    "partition_id": partition_id,
                    "collection_name": identity["collection_name"],
                    "representation_profile": identity["profile"] or "",
                },
                "source_ref": {
                    "source_root": str(source_root),
                    "partition_id": partition_id,
                    "catalog_path": str(status.get("catalog_path") or ""),
                    "corpus_id": str(status.get("corpus_id") or ""),
                },
                "source_kind": "managed",
                "identity_status": identity_status,
                "warnings": warnings,
                "display_name": metadata.get("podcast_name") or status.get("display_name") or partition_id,
                "downstream_identity": identity,
                "technical": {"managed_status": status, "active_release_id": active_release_id, "manifest": manifest, "metadata": metadata},
            }
        if not target.is_dir():
            raise BridgeError("SOURCE_UNAVAILABLE", f"The database folder is unavailable: {target}")
        proposal: dict[str, Any] = {
            "target": {"path": str(target), "normalized": os.path.normcase(str(target)).casefold()},
            "source_ref": {"path": str(payload.get("source_path") or "")} if payload.get("source_path") else {},
            "source_kind": source_kind,
            "identity_status": "resolved",
            "warnings": [],
        }
        source_path_value = payload.get("source_path")
        if source_path_value is not None and not isinstance(source_path_value, str):
            raise BridgeError("VALIDATION_FAILED", "source_path must be a string.", field="source_path")
        try:
            metadata = json.loads((target / "podcast.json").read_text(encoding="utf-8"))
            manifest = json.loads((target / "import_manifest.json").read_text(encoding="utf-8")) if (target / "import_manifest.json").is_file() else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise BridgeError("IDENTITY_UNRESOLVED", "This folder does not contain readable database metadata.") from exc
        identity = {
            "database_id": metadata.get("database_id"), "collection_name": metadata.get("collection_name"),
            "representation_id": manifest.get("representation_id") or metadata.get("representation_id"),
            "embedding_model": manifest.get("embedding_model") or metadata.get("embedding_model"),
            "profile": (manifest.get("representation") or {}).get("profile"),
        }
        if not identity["database_id"] or not identity["collection_name"]:
            proposal["identity_status"] = "unresolved"
            proposal["warnings"].append("Database metadata does not contain a stable ID and collection name.")
        proposal.update({"display_name": metadata.get("podcast_name") or target.name, "downstream_identity": identity, "target": {**proposal["target"], "collection_name": identity["collection_name"], "representation_profile": identity["profile"] or ""}, "technical": {"manifest": manifest, "metadata": metadata}})
        if not proposal["source_ref"]:
            proposal["warnings"].append("Choose the original processed source folder before updating this entry.")
        return proposal

    def register_existing(self, proposal: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(proposal, Mapping):
            raise BridgeError("VALIDATION_FAILED", "The registration proposal must be an object.")
        for field_name in ("target", "source_ref", "downstream_identity", "selection_policy"):
            value = proposal.get(field_name)
            if value is not None and not isinstance(value, Mapping):
                raise BridgeError("VALIDATION_FAILED", f"{field_name} must be an object.", field=field_name)
        target = dict(proposal.get("target") or {})
        source_ref = dict(proposal.get("source_ref") or {})
        if not isinstance(target.get("path"), str) or not target["path"].strip():
            raise BridgeError("VALIDATION_FAILED", "A database destination is required.", field="target.path")
        identity = dict(proposal.get("downstream_identity") or {})
        source_kind = proposal.get("source_kind") or "folder"
        if not isinstance(source_kind, str):
            raise BridgeError("VALIDATION_FAILED", "Source kind must be a string.", field="source_kind")
        connection = None
        corpus_id = ""
        if source_kind == "managed" and source_ref.get("connection_id") and source_ref.get("partition_id"):
            from .reconciliation import catalog_cache_path

            connection = next((item for item in self.catalog.list_source_connections(include_archived=True) if str(item.get("id")) == str(source_ref.get("connection_id"))), None)
            if not connection:
                raise BridgeError("IDENTITY_UNRESOLVED", "The selected source connection is not registered.")
            if source_ref.get("source_root") and normalize_target(str(source_ref["source_root"])) != normalize_target(str(connection["root"])):
                raise BridgeError("VALIDATION_FAILED", "The database source root does not match the selected source connection.")
            corpus_id = str(source_ref.get("corpus_id") or identity.get("corpus_id") or "").strip()
            if not corpus_id:
                raise BridgeError("IDENTITY_UNRESOLVED", "The managed source corpus could not be validated.")
            source_ref["source_root"] = connection["root"]
            source_ref["catalog_path"] = str(catalog_cache_path(self.catalog, connection))
        display_name_value = proposal.get("display_name")
        if display_name_value is not None and not isinstance(display_name_value, str):
            raise BridgeError("VALIDATION_FAILED", "display_name must be a string.", field="display_name")
        source_path_value = source_ref.get("path")
        if source_path_value is not None and not isinstance(source_path_value, str):
            raise BridgeError("VALIDATION_FAILED", "source_ref.path must be a string.", field="source_ref.path")
        record = DatabaseRecord(
            # The library ID belongs to this application.  The downstream
            # database ID is an identity fact from the inspected export and
            # must remain separate so a legacy ID cannot become our catalog
            # primary key.
            id=new_id("db"),
            display_name=str(proposal.get("display_name") or Path(str(target["path"])).name),
            source_kind=source_kind,
            source_ref=source_ref or {"path": "", "status": "missing"},
            target=target,
            downstream_identity=identity or None,
            selection_policy=SelectionPolicy.from_mapping(proposal.get("selection_policy")).as_dict(),
        )
        if proposal.get("identity_status") == "unresolved":
            record.target["identity_status"] = "unresolved"
        saved = self.catalog.create_database(record.as_dict())
        if source_kind == "managed" and source_ref.get("connection_id") and source_ref.get("partition_id"):
            self.catalog.save_database_link({
                "database_id": saved["id"], "connection_id": source_ref["connection_id"],
                "partition_id": source_ref["partition_id"], "corpus_id": corpus_id,
                "source_root": str(connection.get("root") or source_ref.get("source_root") or ""),
                "target_key": normalize_target(target["path"]),
                "profile_fingerprint": str(identity.get("profile_fingerprint") or source_ref.get("profile_fingerprint") or ""),
                "origin": "adopted", "state": "linked",
                "last_source_release_id": str(identity.get("upstream_release_id") or source_ref.get("upstream_release_id") or "") or None,
                "last_downstream_release_id": str(identity.get("downstream_release_id") or "") or None,
                "detail": {"confirmed_at": utc_now(), "registered_existing": True},
            })
        return saved

    def _draft_for_preview(self, payload: Mapping[str, Any]) -> tuple[DatabaseRecord | None, dict[str, Any]]:
        database_id = str(payload.get("database_id") or "")
        if database_id:
            record = DatabaseRecord.from_mapping(self.get_database(database_id))
            draft = dict(payload)
            draft.setdefault("source_ref", record.source_ref)
            draft.setdefault("target", record.target)
            draft.setdefault("selection_policy", record.selection_policy)
            draft.setdefault("execution_options", record.execution_options)
            draft.setdefault("display_name", record.display_name)
            self._normalize_managed_source(record.source_kind, draft)
            return record, draft
        draft_id = str(payload.get("draft_id") or "")
        if draft_id:
            draft = self.get_draft(draft_id)["payload"]
            draft["draft_id"] = draft_id
            self._normalize_managed_source(str(draft.get("source_kind") or "folder"), draft)
            draft.setdefault("execution_options", {"embedding_device": "auto"})
            return None, draft
        draft = dict(payload)
        self._normalize_managed_source(str(draft.get("source_kind") or "folder"), draft)
        draft.setdefault("execution_options", {"embedding_device": "auto"})
        return None, draft

    @staticmethod
    def _normalize_managed_source(source_kind: str, draft: dict[str, Any]) -> None:
        if source_kind != "managed":
            return
        source_ref = dict(draft.get("source_ref") or {})
        if source_ref.get("path"):
            return
        root_raw = str(source_ref.get("source_root") or draft.get("source_root") or "").strip()
        root = Path(root_raw) if root_raw else Path()
        partition = str(source_ref.get("partition_id") or draft.get("partition_id") or "")
        if root_raw and partition:
            candidates = [root / "partitions" / partition / "processed_data", root / partition / "processed_data"]
            source_ref["path"] = str(next((candidate for candidate in candidates if candidate.is_dir()), candidates[0]))
            source_ref["source_root"] = str(root)
            source_ref["partition_id"] = partition
            draft["source_ref"] = source_ref

    def _create_preview_from_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_preview_payload(payload)
        record, draft = self._draft_for_preview(payload)
        if record and str(record.target.get("identity_status") or "") == "unresolved":
            raise BridgeError("IDENTITY_UNRESOLVED", "Resolve the existing database identity before preparing an update.")
        operation = str(payload.get("operation") or ("update" if record else "create"))
        source_kind = record.source_kind if record else str(draft.get("source_kind") or "folder")
        if source_kind == "managed":
            preview = self.managed_adapter.create_preview(record, draft, operation=operation)
        else:
            preview = create_folder_preview(record, draft, operation=operation)
        if record is not None:
            # A preview may carry a one-run selection policy. Keep the
            # registered database settings as the concurrency baseline so
            # applying that preview does not require persisting the policy.
            preview.base_settings_hash = self._database_settings_hash(record)
        saved = self.catalog.save_preview(preview)
        if record is not None and operation == "update":
            self.catalog.update_database(record.id, {
                "last_check": {
                    "checked_at": preview.created_at,
                    "status": "up_to_date" if preview.effects.writes == 0 else "changes_found",
                    "label": "Up to date" if preview.effects.writes == 0 else f"{preview.effects.writes} changes found",
                    "preview_id": preview.preview_id,
                    "source_hash": preview.source_snapshot.get("hash") or preview.source_snapshot.get("release_hash"),
                },
            })
        return saved

    def create_preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._create_preview_from_payload(payload)

    def create_maintenance_preview(self, database_id: str, delete_ids: list[str]) -> dict[str, Any]:
        record = DatabaseRecord.from_mapping(self.get_database(database_id))
        requested = sorted({str(value).strip() for value in delete_ids if str(value).strip()})
        if not requested:
            raise BridgeError("VALIDATION_FAILED", "Select at least one outdated record before preparing maintenance.")
        payload = {
            "database_id": record.id,
            "operation": "remove_outdated",
            "delete_ids": requested,
            "source_kind": record.source_kind,
            "source_ref": record.source_ref,
            "target": record.target,
            "selection_policy": record.selection_policy,
            "display_name": record.display_name,
        }
        return self._create_preview_from_payload(payload)

    def queue_preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_preview_payload(payload)
        return self.jobs.submit_preview(payload)

    def queue_scan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_source_payload(payload)
        return self.jobs.submit_scan(payload)

    def queue_synthetic_progress(self, seconds: int = 10) -> dict[str, Any]:
        return self.jobs.submit_synthetic_progress(seconds)

    def get_preview(self, preview_id: str, *, offset: int = 0, limit: int = 200) -> dict[str, Any]:
        preview = self.catalog.get_preview(preview_id)
        effects = dict(preview.get("effects") or {})
        for key in ("insert_ids", "replace_ids", "metadata_only_ids", "unchanged_ids", "retained_missing_ids", "delete_ids"):
            values = list(effects.get(key) or [])
            effects[key] = {"total": len(values), "items": values[max(0, offset): max(0, offset) + max(1, min(limit, 1000))]}
        preview["effects"] = effects
        return preview

    @staticmethod
    def _validate_preview_payload(payload: Mapping[str, Any]) -> None:
        if not isinstance(payload, Mapping):
            raise BridgeError("VALIDATION_FAILED", "The preview request must be an object.")
        operation = payload.get("operation")
        if operation is not None and (not isinstance(operation, str) or operation not in {"create", "update", "rebuild", "remove_outdated"}):
            raise BridgeError("VALIDATION_FAILED", "The preview operation is not supported.", field="operation")
        source_kind = payload.get("source_kind")
        if source_kind is not None and (not isinstance(source_kind, str) or source_kind not in {"folder", "managed"}):
            raise BridgeError("VALIDATION_FAILED", "Source kind must be folder or managed.", field="source_kind")
        for field_name in ("database_id", "draft_id"):
            value = payload.get(field_name)
            if value is not None and not isinstance(value, str):
                raise BridgeError("VALIDATION_FAILED", f"{field_name} must be a string.", field=field_name)
        if payload.get("database_id") and payload.get("draft_id"):
            raise BridgeError("VALIDATION_FAILED", "Use either database_id or draft_id, not both.")
        for field_name in ("source_ref", "target", "selection_policy", "representation", "execution_options"):
            value = payload.get(field_name)
            if value is not None and not isinstance(value, Mapping):
                raise BridgeError("VALIDATION_FAILED", f"{field_name} must be an object.", field=field_name)
        if payload.get("selection_policy") is not None:
            SelectionPolicy.from_mapping(payload["selection_policy"])
        if payload.get("execution_options") is not None:
            from .models import ExecutionOptions

            ExecutionOptions.from_mapping(payload["execution_options"])
        delete_ids = payload.get("delete_ids")
        if delete_ids is not None and (not isinstance(delete_ids, list) or any(not isinstance(item, str) or not item.strip() for item in delete_ids)):
            raise BridgeError("VALIDATION_FAILED", "delete_ids must contain non-empty strings.", field="delete_ids")

    @staticmethod
    def _database_settings_hash(current: DatabaseRecord) -> str:
        if current.source_kind == "managed":
            return stable_hash({
                "source_kind": "managed",
                "source_root": str(current.source_ref.get("source_root") or ""),
                "partition_id": str(current.source_ref.get("partition_id") or ""),
                "catalog_path": str(current.source_ref.get("catalog_path") or ""),
                "target_root": str(current.target.get("managed_output_root") or current.target.get("path") or ""),
                "execution_options": current.execution_options,
            })
        return stable_hash({
            "source_kind": current.source_kind,
            "source_path": str(current.source_ref.get("path") or ""),
            "target_path": str(current.target.get("path") or ""),
            "selection_policy": SelectionPolicy.from_mapping(current.selection_policy).as_dict(),
            "execution_options": current.execution_options,
        })

    @staticmethod
    def _validate_preview_current(current: DatabaseRecord, preview: FrozenPreview) -> None:
        """Reject an accepted review when its registered database changed."""
        if current.source_kind == "managed":
            # Managed creation registers the database only after review, so
            # its preview hash is intentionally release/profile-bound rather
            # than based on a not-yet-created catalog record.
            current_hash = stable_hash({
                "source_kind": "managed",
                "source_root": str(current.source_ref.get("source_root") or ""),
                "partition_id": str(current.source_ref.get("partition_id") or ""),
                "catalog_path": str(current.source_ref.get("catalog_path") or ""),
                "target_root": str(current.target.get("managed_output_root") or current.target.get("path") or ""),
                "execution_options": current.execution_options,
                "upstream_release_id": str(preview.source_snapshot.get("release_id") or ""),
                "profile_fingerprint": str(preview.representation.get("managed_profile_fingerprint") or ""),
            })
            expected_hash = preview.settings_hash
        else:
            current_hash = WorkflowService._database_settings_hash(current)
            expected_hash = preview.base_settings_hash or preview.settings_hash
        if current.settings_revision != preview.settings_revision or current_hash != expected_hash:
            raise BridgeError("PREVIEW_STALE", "Database settings changed after this review. Prepare a fresh review.")
        if current.source_kind == "managed":
            current_target = current.target.get("managed_output_root") or current.target.get("path") or ""
            preview_target = preview.target_identity.get("managed_output_root") or preview.target_identity.get("path") or ""
            targets_match = normalize_target(str(current_target)) == normalize_target(str(preview_target))
        else:
            targets_match = str(preview.target_identity.get("path") or "") == str(current.target.get("path") or "")
        if not targets_match:
            raise BridgeError("PREVIEW_STALE", "The database destination changed after this review. Prepare a fresh review.")

    def apply_preview(self, preview_id: str, acknowledgments: list[str] | None = None) -> dict[str, Any]:
        raw = self.catalog.get_preview(preview_id)
        preview = FrozenPreview.from_mapping(raw)
        existing_job = self.catalog.get_job_for_preview(preview_id)
        if existing_job:
            if str(existing_job.get("state") or "") == "failed" and existing_job.get("database_id") and preview.operation == "create":
                failed_database = self.catalog.get_database(str(existing_job["database_id"]))
                if failed_database.get("source_kind") == "managed" and not failed_database.get("archived"):
                    return self.jobs.submit_import(str(existing_job["database_id"]), preview_id, retry_failed=True)
            return existing_job
        acknowledgments = list(acknowledgments or [])
        missing = sorted(set(preview.required_acknowledgments).difference(acknowledgments))
        if missing:
            raise BridgeError("VALIDATION_FAILED", "Review acknowledgment is required before applying this operation.", details_id=stable_hash(missing)[:16])
        if preview.status != "ready":
            raise BridgeError("PREVIEW_STALE", "This review is no longer current. Prepare a fresh review.")
        blocking = [finding for finding in preview.validation_findings if str(finding.get("severity") or "").lower() == "error"]
        if blocking:
            code = str(blocking[0].get("code") or "VALIDATION_FAILED")
            if code == "MANAGED_RETENTION_UNSUPPORTED":
                raise BridgeError("OPERATION_UNSUPPORTED", str(blocking[0].get("message") or "This managed operation is not supported."))
            raise BridgeError("VALIDATION_FAILED", str(blocking[0].get("message") or "Resolve the blocking review finding before applying."))
        if preview.database_id is None:
            draft = self.get_draft(preview.draft_id)["payload"] if preview.draft_id else {}
            source_ref = dict(draft.get("source_ref") or {})
            if str(draft.get("source_kind") or "folder") == "managed":
                source_ref.setdefault("source_root", preview.source_snapshot.get("source_root"))
                source_ref.setdefault("partition_id", preview.source_snapshot.get("partition_id"))
                source_ref.setdefault("catalog_path", preview.source_snapshot.get("catalog_path"))
                source_ref.setdefault("upstream_release_id", preview.source_snapshot.get("release_id"))
                draft["source_ref"] = source_ref
            if str(draft.get("source_kind") or "folder") == "managed":
                draft["target"] = dict(preview.target_identity)
            else:
                draft.setdefault("target", preview.target_identity)
            draft.setdefault("source_ref", {"path": preview.source_snapshot.get("files", [{}])[0].get("path", "")})
            self._normalize_managed_source(str(draft.get("source_kind") or "folder"), draft)
            database = self._retryable_managed_creation(preview, draft)
            if database is None:
                database = self._register_draft(draft)
            database_id = database["id"]
            if str(draft.get("source_kind") or "folder") == "managed":
                link = self.catalog.get_database_link(database_id)
                if link:
                    self.catalog.save_database_link({
                        **link,
                        "profile_fingerprint": str(preview.representation.get("managed_base_profile_fingerprint") or link.get("profile_fingerprint") or ""),
                        "last_source_release_id": link.get("last_source_release_id"),
                    })
        else:
            current = DatabaseRecord.from_mapping(self.get_database(preview.database_id))
            self._validate_preview_current(current, preview)
            database_id = preview.database_id
        # Store an accepted copy only through the catalog; the executor reads
        # all inputs again under the stable target lock.
        return self.jobs.submit_import(database_id, preview_id)

    def _retryable_managed_creation(self, preview: FrozenPreview, draft: Mapping[str, Any]) -> dict[str, Any] | None:
        """Reuse only a failed, unactivated managed registration on retry.

        The first phase of a managed create registers the destination before
        the worker runs.  If that worker stops before promotion, a later
        review must be able to continue the same create instead of failing on
        the catalog's unique destination constraint.  Active, archived, or
        already-populated registrations remain ineligible and keep the normal
        create/use-existing separation.
        """
        if preview.operation != "create" or str(draft.get("source_kind") or "folder") != "managed":
            return None
        target_path = str(preview.target_identity.get("path") or "").strip()
        if not target_path:
            return None
        existing = self.catalog.get_database_by_target(target_path, include_archived=False)
        if not existing or existing.get("archived") or existing.get("source_kind") != "managed":
            return None
        if existing.get("downstream_identity"):
            return None
        source_ref = dict(existing.get("source_ref") or {})
        if str(source_ref.get("partition_id") or "") != str(preview.source_snapshot.get("partition_id") or ""):
            return None
        if normalize_target(str(source_ref.get("source_root") or "")) != normalize_target(str(preview.source_snapshot.get("source_root") or "")):
            return None
        link = self.catalog.get_database_link(str(existing["id"]))
        if not link or link.get("origin") != "created" or link.get("state") != "update_failed":
            return None
        if link.get("last_source_release_id") or link.get("last_downstream_release_id"):
            return None
        target = Path(target_path).expanduser().resolve()
        if target.exists() and not self.managed_adapter.can_retry_create_target(target):
            return None
        return existing

    def _register_draft(self, draft: Mapping[str, Any]) -> dict[str, Any]:
        target = dict(draft.get("target") or {})
        identity = dict(draft.get("downstream_identity") or {})
        source_kind = str(draft.get("source_kind") or "folder")
        target = dict(draft.get("target") or {})
        if source_kind == "managed":
            source_ref = dict(draft.get("source_ref") or {})
            partition_id = str(source_ref.get("partition_id") or draft.get("partition_id") or "")
            linked_connection = None
            linked_corpus_id = ""
            if source_ref.get("connection_id") and partition_id:
                from .reconciliation import catalog_cache_path

                linked_connection = next((item for item in self.catalog.list_source_connections(include_archived=True) if str(item.get("id")) == str(source_ref.get("connection_id"))), None)
                if not linked_connection:
                    raise BridgeError("IDENTITY_UNRESOLVED", "The selected source connection is not registered.")
                linked_corpus_id = str(source_ref.get("corpus_id") or identity.get("corpus_id") or "").strip()
                if not linked_corpus_id:
                    raise BridgeError("IDENTITY_UNRESOLVED", "The managed source corpus could not be validated.")
                source_ref["source_root"] = linked_connection["root"]
                source_ref["catalog_path"] = str(catalog_cache_path(self.catalog, linked_connection))
            output_root = Path(str(target.get("managed_output_root") or target.get("path") or "")).expanduser().resolve()
            if partition_id:
                from chroma_db_import.managed import managed_paths

                target = {**target, "managed_output_root": str(output_root), "path": str(managed_paths(output_root, partition_id)["partition_root"])}
        record = DatabaseRecord(
            # A draft may contain source metadata named database_id, but a
            # new library registration always receives its own catalog ID.
            id=new_id("db"), display_name=str(draft.get("display_name") or "Untitled database"),
            source_kind=source_kind, source_ref=source_ref if source_kind == "managed" else dict(draft.get("source_ref") or {}),
            target=target, downstream_identity=identity or None, selection_policy=SelectionPolicy.from_mapping(draft.get("selection_policy")).as_dict(),
        )
        saved = self.catalog.create_database(record.as_dict())
        source_ref = dict(saved.get("source_ref") or {})
        if source_kind == "managed" and source_ref.get("connection_id") and source_ref.get("partition_id"):
            identity = dict(saved.get("downstream_identity") or {})
            self.catalog.save_database_link({
                "database_id": saved["id"], "connection_id": source_ref["connection_id"],
                "partition_id": source_ref["partition_id"], "corpus_id": linked_corpus_id,
                "source_root": str(linked_connection.get("root") or source_ref.get("source_root") or ""),
                "target_key": os.path.normcase(str(saved["target"].get("path") or "")).casefold(),
                "profile_fingerprint": str(identity.get("managed_profile_fingerprint") or identity.get("profile_fingerprint") or ""),
                "origin": "created", "state": "linked",
                "last_source_release_id": None,
                "last_downstream_release_id": str(identity.get("downstream_release_id") or "") or None,
                "detail": {"created_at": utc_now()},
            })
        return saved

    def _record_managed_release_event(self, database_id: str, preview: FrozenPreview, *, status: str, result: Mapping[str, Any] | None = None, error: Mapping[str, Any] | None = None) -> None:
        link = self.catalog.get_database_link(database_id)
        if not link:
            return
        result = dict(result or {})
        error = dict(error or {})
        upstream_release_id = str(preview.source_snapshot.get("release_id") or "") or None
        downstream_release_id = str(result.get("downstream_identity", {}).get("downstream_release_id") or result.get("operation_report", {}).get("downstream_release_id") or "") or None
        next_state = "linked" if status == "succeeded" else "update_failed"
        self.catalog.save_database_link({
            **link, "state": next_state,
            "last_source_release_id": upstream_release_id if status == "succeeded" else link.get("last_source_release_id"),
            "last_downstream_release_id": downstream_release_id if status == "succeeded" else link.get("last_downstream_release_id"),
            "detail": {**dict(link.get("detail") or {}), "last_status": status, "last_error": error or None},
        })
        self.catalog.save_database_release_event({
            "database_id": database_id, "connection_id": link["connection_id"], "partition_id": link["partition_id"],
            "upstream_release_id": upstream_release_id, "downstream_release_id": downstream_release_id,
            "operation": preview.operation, "status": status,
            "detail": {"preview_id": preview.preview_id, "error": error or None, "result": result},
        })

    def _run_source_action(self, payload: Mapping[str, Any], callback: Any) -> dict[str, Any]:
        return self.managed_adapter.start_action(Path(str(payload.get("source_root") or "")), str(payload.get("partition_id") or ""), str(payload.get("action") or "inspect"), callback)

    def start_source_action(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        source_root = payload.get("source_root")
        partition_id = payload.get("partition_id")
        if isinstance(payload.get("context"), Mapping):
            context = self.get_context(payload["context"])
            source_root = context.get("source_root")
            partition_id = context["ref"]["partition_id"]
        if not isinstance(action, str) or action not in {"inspect", "prepare"}:
            raise BridgeError("OPERATION_UNSUPPORTED", "Source action is not allowlisted.")
        if not isinstance(source_root, str) or not source_root.strip():
            raise BridgeError("VALIDATION_FAILED", "A managed source root is required.", field="source_root")
        if not isinstance(partition_id, str) or not partition_id.strip():
            raise BridgeError("VALIDATION_FAILED", "A managed partition is required.", field="partition_id")
        return self.jobs.submit_source_action({**dict(payload), "action": action, "source_root": source_root.strip(), "partition_id": partition_id.strip()})

    def archive_database(self, database_id: str) -> dict[str, Any]:
        return self.catalog.archive_database(database_id)

    def rename_database(self, database_id: str, display_name: str) -> dict[str, Any]:
        if not str(display_name).strip():
            raise BridgeError("VALIDATION_FAILED", "Database name is required.", field="display_name")
        return self.catalog.update_database(database_id, {"display_name": str(display_name).strip()})

    def update_database_settings(self, database_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        record = self.get_database(database_id)
        allowed = {"selection_policy", "source_ref", "target", "execution_options"}
        if set(changes).difference(allowed):
            raise BridgeError("VALIDATION_FAILED", "Only database-scoped import settings can be changed here.")
        changes = dict(changes)
        if "target" in changes:
            requested_target = dict(changes.get("target") or {})
            if str(requested_target.get("path") or "") != str(record["target"].get("path") or ""):
                raise BridgeError("VALIDATION_FAILED", "A registered database destination is fixed; create a separate database for another target.", field="target.path")
            changes["target"] = {**dict(record["target"]), **requested_target, "path": record["target"]["path"]}
        changes["settings_revision"] = int(record["settings_revision"]) + 1
        return self.catalog.update_database(database_id, changes)

    def export_report(self, report: Mapping[str, Any], *, report_id: str | None = None) -> dict[str, Any]:
        identifier = report_id or new_id("report")
        path = self.state_dir / "reports" / f"{identifier}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(report), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return {"report_id": identifier, "path": str(path)}

    def save_report_copy(self, report_id: str, output_path: str) -> dict[str, Any]:
        if not report_id or Path(report_id).name != report_id or report_id in {".", ".."}:
            raise BridgeError("VALIDATION_FAILED", "The report ID is invalid.", field="report_id")
        source = (self.state_dir / "reports" / f"{report_id}.json").resolve()
        reports_root = (self.state_dir / "reports").resolve()
        try:
            source.relative_to(reports_root)
        except ValueError as exc:
            raise BridgeError("VALIDATION_FAILED", "The report ID is outside the application report store.") from exc
        if not source.is_file():
            raise BridgeError("IDENTITY_UNRESOLVED", "The requested report is no longer available.", field="report_id")
        destination = Path(output_path).expanduser().resolve()
        if destination == source:
            return {"report_id": report_id, "path": str(destination), "copied": False}
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        except OSError as exc:
            raise BridgeError("JOB_FAILED", f"The report copy could not be written: {exc}") from exc
        return {"report_id": report_id, "path": str(destination), "copied": True}

    def open_database_folder(self, database_id: str) -> str:
        record = self.get_database(database_id)
        path = self._active_target(record)
        return str(path)

    def shutdown(self) -> None:
        self.jobs.shutdown()
