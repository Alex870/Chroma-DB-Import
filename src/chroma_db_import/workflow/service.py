from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

from chroma_db_import.releases import inspect_export_work

from .catalog import AppCatalog
from .folder_adapter import FolderAdapter
from .jobs import JobService
from .managed_adapter import ManagedAdapter
from .models import BridgeError, DatabaseRecord, FrozenPreview, SelectionPolicy, new_id, stable_hash, utc_now
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

    def version(self) -> dict[str, str]:
        return {"api_version": self.API_VERSION, "backend_version": "0.1.0"}

    def list_databases(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return self.catalog.list_databases(include_archived=include_archived)

    def get_database(self, database_id: str) -> dict[str, Any]:
        return self.catalog.get_database(database_id)

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
        if kind == "source":
            return self.jobs.submit_source_action(payload)
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
        }

    @staticmethod
    def _active_target(record: Mapping[str, Any]) -> Path:
        target = Path(str((record.get("target") or {}).get("path") or ""))
        if record.get("source_kind") == "managed":
            managed_root = str((record.get("target") or {}).get("managed_output_root") or "")
            partition_id = str((record.get("source_ref") or {}).get("partition_id") or "")
            if managed_root and partition_id:
                from chroma_db_import.managed import managed_paths

                pointer_root = managed_paths(Path(managed_root), partition_id)["partition_root"]
                pointer = pointer_root / "active-release.json"
                if pointer.is_file():
                    try:
                        release_id = str(json.loads(pointer.read_text(encoding="utf-8")).get("release_id") or "")
                    except (OSError, json.JSONDecodeError):
                        release_id = ""
                    if release_id:
                        target = managed_paths(Path(managed_root), partition_id, release_id).get("export_root", target)
        return target

    def get_database_content(self, database_id: str) -> dict[str, Any]:
        """Return compact episode/speaker inventory without sending document text to the UI."""
        record = self.get_database(database_id)
        target = self._active_target(record)
        metadata_path = target / "podcast.json"
        metadata: dict[str, Any] = {}
        if metadata_path.is_file():
            try:
                loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    metadata = loaded
            except (OSError, json.JSONDecodeError):
                pass
        policy = SelectionPolicy.from_mapping(record.get("selection_policy"))
        episodes: list[dict[str, Any]] = []
        for item in metadata.get("episodes") or []:
            if not isinstance(item, Mapping):
                continue
            episode_id = str(item.get("episode_id") or item.get("episode_uid") or "").strip()
            if not episode_id:
                continue
            speakers = sorted({str(speaker.get("name") or speaker.get("id") or "").strip() for speaker in item.get("speakers") or [] if isinstance(speaker, Mapping) and str(speaker.get("name") or speaker.get("id") or "").strip()})
            episodes.append({
                "episode_id": episode_id,
                "title": str(item.get("episode_title") or episode_id),
                "date": str(item.get("episode_date") or ""),
                "document_count": int(item.get("document_count") or 0),
                "speakers": speakers,
                "excluded": episode_id in policy.excluded_episode_ids,
                "override_speakers": list(policy.episode_overrides.get(episode_id) or []),
            })
        global_speakers = sorted({
            str(speaker.get("name") or speaker.get("id") or "").strip()
            for speaker in metadata.get("speakers") or []
            if isinstance(speaker, Mapping) and str(speaker.get("name") or speaker.get("id") or "").strip()
        })
        return {
            "database_id": database_id,
            "target": str(target),
            "episodes": episodes,
            "speakers": global_speakers or sorted({speaker for episode in episodes for speaker in episode["speakers"]}),
            "shared_context_note": "Episode thesis and other shared-context records remain available when at least one selected speaker is included.",
        }

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
        return self.catalog.cancel_queued_job(job_id)

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
            return self.managed_adapter.inspect(root, str(payload.get("partition_id") or ""))
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
        return self.catalog.create_database(record.as_dict())

    def _draft_for_preview(self, payload: Mapping[str, Any]) -> tuple[DatabaseRecord | None, dict[str, Any]]:
        database_id = str(payload.get("database_id") or "")
        if database_id:
            record = DatabaseRecord.from_mapping(self.get_database(database_id))
            draft = dict(payload)
            draft.setdefault("source_ref", record.source_ref)
            draft.setdefault("target", record.target)
            draft.setdefault("selection_policy", record.selection_policy)
            draft.setdefault("display_name", record.display_name)
            self._normalize_managed_source(record.source_kind, draft)
            return record, draft
        draft_id = str(payload.get("draft_id") or "")
        if draft_id:
            draft = self.get_draft(draft_id)["payload"]
            draft["draft_id"] = draft_id
            self._normalize_managed_source(str(draft.get("source_kind") or "folder"), draft)
            return None, draft
        draft = dict(payload)
        self._normalize_managed_source(str(draft.get("source_kind") or "folder"), draft)
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
        for field_name in ("source_ref", "target", "selection_policy", "representation"):
            value = payload.get(field_name)
            if value is not None and not isinstance(value, Mapping):
                raise BridgeError("VALIDATION_FAILED", f"{field_name} must be an object.", field=field_name)
        if payload.get("selection_policy") is not None:
            SelectionPolicy.from_mapping(payload["selection_policy"])
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
            })
        return stable_hash({
            "source_kind": current.source_kind,
            "source_path": str(current.source_ref.get("path") or ""),
            "target_path": str(current.target.get("path") or ""),
            "selection_policy": SelectionPolicy.from_mapping(current.selection_policy).as_dict(),
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
                "upstream_release_id": str(preview.source_snapshot.get("release_id") or ""),
                "profile_fingerprint": str(preview.representation.get("managed_profile_fingerprint") or ""),
            })
            expected_hash = preview.settings_hash
        else:
            current_hash = WorkflowService._database_settings_hash(current)
            expected_hash = preview.base_settings_hash or preview.settings_hash
        if current.settings_revision != preview.settings_revision or current_hash != expected_hash:
            raise BridgeError("PREVIEW_STALE", "Database settings changed after this review. Prepare a fresh review.")
        if str(preview.target_identity.get("path") or "") != str(current.target.get("path") or ""):
            raise BridgeError("PREVIEW_STALE", "The database destination changed after this review. Prepare a fresh review.")

    def apply_preview(self, preview_id: str, acknowledgments: list[str] | None = None) -> dict[str, Any]:
        raw = self.catalog.get_preview(preview_id)
        preview = FrozenPreview.from_mapping(raw)
        existing_job = self.catalog.get_job_for_preview(preview_id)
        if existing_job:
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
            if str(draft.get("source_kind") or "folder") == "managed":
                draft["target"] = dict(preview.target_identity)
            else:
                draft.setdefault("target", preview.target_identity)
            draft.setdefault("source_ref", {"path": preview.source_snapshot.get("files", [{}])[0].get("path", "")})
            self._normalize_managed_source(str(draft.get("source_kind") or "folder"), draft)
            database = self._register_draft(draft)
            database_id = database["id"]
        else:
            current = DatabaseRecord.from_mapping(self.get_database(preview.database_id))
            self._validate_preview_current(current, preview)
            database_id = preview.database_id
        # Store an accepted copy only through the catalog; the executor reads
        # all inputs again under the stable target lock.
        return self.jobs.submit_import(database_id, preview_id)

    def _register_draft(self, draft: Mapping[str, Any]) -> dict[str, Any]:
        target = dict(draft.get("target") or {})
        identity = dict(draft.get("downstream_identity") or {})
        source_kind = str(draft.get("source_kind") or "folder")
        target = dict(draft.get("target") or {})
        if source_kind == "managed":
            source_ref = dict(draft.get("source_ref") or {})
            partition_id = str(source_ref.get("partition_id") or draft.get("partition_id") or "")
            output_root = Path(str(target.get("managed_output_root") or target.get("path") or "")).expanduser().resolve()
            if partition_id:
                from chroma_db_import.managed import managed_paths

                target = {**target, "managed_output_root": str(output_root), "path": str(managed_paths(output_root, partition_id)["partition_root"])}
        record = DatabaseRecord(
            # A draft may contain source metadata named database_id, but a
            # new library registration always receives its own catalog ID.
            id=new_id("db"), display_name=str(draft.get("display_name") or "Untitled database"),
            source_kind=source_kind, source_ref=dict(draft.get("source_ref") or {}),
            target=target, downstream_identity=identity or None, selection_policy=SelectionPolicy.from_mapping(draft.get("selection_policy")).as_dict(),
        )
        return self.catalog.create_database(record.as_dict())

    def _run_source_action(self, payload: Mapping[str, Any], callback: Any) -> dict[str, Any]:
        return self.managed_adapter.start_action(Path(str(payload.get("source_root") or "")), str(payload.get("partition_id") or ""), str(payload.get("action") or "inspect"), callback)

    def start_source_action(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        source_root = payload.get("source_root")
        partition_id = payload.get("partition_id")
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
        allowed = {"selection_policy", "source_ref", "target"}
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

    def open_database_folder(self, database_id: str) -> str:
        record = self.get_database(database_id)
        path = self._active_target(record)
        return str(path)

    def shutdown(self) -> None:
        self.jobs.shutdown()
