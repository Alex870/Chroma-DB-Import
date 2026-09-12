from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from chroma_db_import.config import ImportConfig
from chroma_db_import.managed import (
    ManagedCatalog,
    discover,
    managed_paths,
    resolve_managed_config,
    run_managed_import,
    validate_upstream_release,
)
from chroma_db_import.importer import representation_spec
from chroma_db_import.podcast_rag_adapter import PodcastRagSourceAdapter

from .models import BridgeError, DatabaseRecord, FrozenPreview, PreviewEffects, new_id, stable_hash, utc_now
from .planning import inspect_existing_records


class ManagedAdapter:
    """Read-only discovery and import of already-published managed sources."""

    @staticmethod
    def _catalog_path(source_root: Path, explicit: str | None = None) -> Path | None:
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit).expanduser())
        candidates.extend(
            [
                source_root / "state" / "context_catalog.sqlite3",
                source_root / "context_catalog.sqlite3",
                source_root / "catalog.sqlite3",
                source_root.parent / "state" / "context_catalog.sqlite3",
                source_root.parent / "context_catalog.sqlite3",
                source_root.parent / "catalog.sqlite3",
            ]
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return None

    @staticmethod
    def _source_values(value: dict[str, Any]) -> tuple[Path, str, Path | None]:
        source_ref = dict(value.get("source_ref") or {})
        source_root_raw = str(source_ref.get("source_root") or value.get("source_root") or "").strip()
        source_root = Path(source_root_raw).expanduser().resolve()
        partition_id = str(source_ref.get("partition_id") or value.get("partition_id") or "").strip()
        catalog_path = ManagedAdapter._catalog_path(source_root, str(source_ref.get("catalog_path") or value.get("catalog_path") or "") or None)
        if not source_root_raw or not source_root.is_dir():
            raise BridgeError("SOURCE_UNAVAILABLE", f"The managed source root is unavailable: {source_root}", field="source_ref.source_root")
        if not partition_id:
            raise BridgeError("VALIDATION_FAILED", "A managed partition is required.", field="source_ref.partition_id")
        if catalog_path is None:
            raise BridgeError("SOURCE_UNAVAILABLE", "The managed context catalog could not be located.")
        return source_root, partition_id, catalog_path

    @staticmethod
    def _output_root(value: dict[str, Any], source_root: Path, partition_id: str) -> Path:
        target = dict(value.get("target") or {})
        raw = str(target.get("managed_output_root") or target.get("path") or "").strip()
        if not raw:
            raw = str(source_root / "exports")
        path = Path(raw).expanduser().resolve()
        # Existing registrations store the managed partition root as target.path.
        if path.name == partition_id and path.parent.name.casefold() == "partitions":
            return path.parent.parent
        return path

    @staticmethod
    def _active_export(output_root: Path, partition_id: str, partition_root: Path) -> tuple[str, Path | None]:
        pointer = partition_root / "active-release.json"
        if not pointer.is_file():
            return "", None
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "", None
        release_id = str(payload.get("release_id") or "").strip()
        if not release_id:
            return "", None
        paths = managed_paths(output_root, partition_id, release_id)
        return release_id, paths.get("export_root")

    def inspect(self, source_root: Path, partition_id: str) -> dict[str, Any]:
        adapter = PodcastRagSourceAdapter(source_root, partition_id)
        status = adapter.inspect()
        result: dict[str, Any] = {
            "partition_id": status.partition_id,
            "corpus_id": status.corpus_id,
            "display_name": status.display_name,
            "ready_to_publish": status.ready_to_publish,
            "pending": status.pending,
            "failed": status.failed,
            "interrupted": status.interrupted,
            "quarantined": status.quarantined,
            "completed": status.completed,
            "warnings": list(status.warnings),
            "active_database_health": "unknown",
        }
        pointer = adapter.partition_root / "active-release.json"
        if pointer.is_file():
            try:
                result["active_release_id"] = str(json.loads(pointer.read_text(encoding="utf-8")).get("release_id") or "")
            except (OSError, json.JSONDecodeError):
                result.setdefault("warnings", []).append("The managed active-release pointer is unreadable.")
        catalog_path = self._catalog_path(source_root)
        if catalog_path is not None:
            try:
                with ManagedCatalog(catalog_path) as catalog:
                    context = catalog.context(partition_id)
                    releases = catalog.releases(partition_id)
                    profile = catalog.profile(partition_id)
                result.update({
                    "catalog_path": str(catalog_path),
                    "context": context or {},
                    "release_ids": [str(item.get("upstream_release_id") or "") for item in releases],
                    "latest_release_id": str(releases[0].get("upstream_release_id") or "") if releases else status.latest_release_id,
                    "managed_profile": (profile or {}).get("profile", {}),
                    "managed_profile_fingerprint": (profile or {}).get("profile_fingerprint", ""),
                })
            except Exception as exc:
                result.setdefault("warnings", []).append(f"Managed catalog inspection failed: {type(exc).__name__}.")
        return result

    def create_preview(self, record: DatabaseRecord | None, draft: dict[str, Any], *, operation: str) -> FrozenPreview:
        if operation == "remove_outdated":
            raise BridgeError("OPERATION_UNSUPPORTED", "Managed record removal requires an explicit producer-supported replacement workflow.")
        if operation == "rebuild":
            raise BridgeError("OPERATION_UNSUPPORTED", "Managed rebuild requires a newly prepared producer release. Use Source connections, then review the resulting Update.")
        source_root, partition_id, catalog_path = self._source_values(draft if record is None else {**record.as_dict(), **draft})
        output_root = self._output_root(draft if record is None else {**record.as_dict(), **draft}, source_root, partition_id)
        with ManagedCatalog(catalog_path) as catalog:
            context = catalog.context(partition_id)
            if not context:
                raise BridgeError("SOURCE_INVALID", f"The managed partition is not registered: {partition_id}")
            releases = catalog.releases(partition_id)
            requested_release = str(
                (draft.get("source_ref") or {}).get("upstream_release_id")
                or draft.get("upstream_release_id")
                or ""
            ).strip()
            release = next((item for item in releases if item.get("upstream_release_id") == requested_release), None) if requested_release else (releases[0] if releases else None)
            if not release:
                raise BridgeError("SOURCE_UNAVAILABLE", "The managed partition has no validated release to review.")
            upstream = dict(release.get("payload") or {})
            try:
                identity = validate_upstream_release(upstream)
            except Exception as exc:
                raise BridgeError("SOURCE_INVALID", "The selected managed release failed contract validation.") from exc
            profile = catalog.profile(partition_id)
            config = resolve_managed_config(ImportConfig(), profile)
            dry_run = run_managed_import(
                config,
                source_root,
                catalog,
                partition_id,
                upstream_release_id=str(release["upstream_release_id"]),
                output_root=output_root,
                dry_run=True,
                validation_only=True,
            )
            representation = representation_spec(config).as_dict()
            representation.update({
                "managed_profile_fingerprint": str(dry_run.get("import_profile_fingerprint") or ""),
                "upstream_release_id": str(release["upstream_release_id"]),
                "downstream_release_id": str(dry_run.get("downstream_release_id") or ""),
            })
            partition_root = managed_paths(output_root, partition_id)["partition_root"]
            active_release_id, active_export = self._active_export(output_root, partition_id, partition_root)
            existing = inspect_existing_records(active_export, "rag_documents") if active_export else {}
            current_ids = sorted(str(item) for item in dry_run.get("record_ids") or [])
            existing_ids = sorted(existing)
            if operation == "create":
                insert_ids, replace_ids, unchanged_ids, retained_ids = current_ids, [], [], []
            elif active_release_id == str(dry_run.get("downstream_release_id") or ""):
                insert_ids, replace_ids, unchanged_ids, retained_ids = [], [], existing_ids, []
            else:
                insert_ids = sorted(set(current_ids).difference(existing_ids))
                replace_ids = sorted(set(current_ids).intersection(existing_ids))
                unchanged_ids = []
                retained_ids = sorted(set(existing_ids).difference(current_ids))
            findings: list[dict[str, Any]] = []
            if dry_run.get("status") == "quarantined":
                findings.append({"severity": "error", "code": "SOURCE_QUARANTINED", "message": "The selected managed release is quarantined and cannot be imported."})
            if operation == "update" and retained_ids:
                findings.append({"severity": "error", "code": "MANAGED_RETENTION_UNSUPPORTED", "message": "This managed full-version build would remove active records. Use an explicit supported replacement/removal workflow."})
            effects = PreviewEffects(
                episodes_total=len(upstream.get("episode_uids") or []),
                records_total=len(current_ids),
                insert_ids=insert_ids,
                replace_ids=replace_ids,
                unchanged_ids=unchanged_ids,
                retained_missing_ids=retained_ids,
                reasons={item: "active managed record absent from prospective release" for item in retained_ids},
                episode_changes=[{"episode_uid": str(item), "source": "managed release"} for item in upstream.get("episode_uids") or []],
            )
            settings_hash = stable_hash({
                "source_kind": "managed",
                "source_root": str(source_root),
                "partition_id": partition_id,
                "catalog_path": str(catalog_path),
                "target_root": str(output_root),
                "upstream_release_id": str(release["upstream_release_id"]),
                "profile_fingerprint": str(dry_run.get("import_profile_fingerprint") or ""),
            })
            return FrozenPreview(
                preview_id=new_id("preview"), operation=operation, database_id=record.id if record else None,
                draft_id=str(draft.get("draft_id") or "") or None, created_at=utc_now(), settings_revision=record.settings_revision if record else int(draft.get("settings_revision") or 1),
                settings_hash=settings_hash,
                source_snapshot={"kind": "managed", "source_root": str(source_root), "partition_id": partition_id, "catalog_path": str(catalog_path), "release_id": str(release["upstream_release_id"]), "release_hash": stable_hash(upstream), "cache_files": list(dry_run.get("cache_files") or [])},
                target_identity={"path": str(partition_root), "normalized": str(partition_root).casefold(), "managed_output_root": str(output_root), "partition_id": identity.partition_id, "corpus_id": identity.corpus_id, "upstream_release_id": str(release["upstream_release_id"]), "downstream_release_id": str(dry_run.get("downstream_release_id") or "")},
                selection_policy=dict(record.selection_policy if record else draft.get("selection_policy") or {}), representation=representation, validation_findings=findings, effects=effects,
                required_acknowledgments=["MANAGED_RELEASE_REVIEW"],
            )

    def execute(self, record: DatabaseRecord, preview: FrozenPreview, emit_progress: Callable[[Any], None]) -> dict[str, Any]:
        source_root, partition_id, catalog_path = self._source_values(record.as_dict())
        expected_release = str(preview.source_snapshot.get("release_id") or "")
        output_root = self._output_root(record.as_dict(), source_root, partition_id)
        with ManagedCatalog(catalog_path) as catalog:
            release = catalog.release(partition_id, expected_release)
            if not release or stable_hash(release.get("payload") or {}) != str(preview.source_snapshot.get("release_hash") or ""):
                raise BridgeError("PREVIEW_STALE", "The managed release changed after review. Prepare a fresh review.")
            profile = catalog.profile(partition_id) or {}
            expected_profile_fingerprint = str(preview.representation.get("managed_profile_fingerprint") or "")
            current_profile_fingerprint = str(profile.get("profile_fingerprint") or "")
            if expected_profile_fingerprint and expected_profile_fingerprint != current_profile_fingerprint:
                raise BridgeError("PREVIEW_STALE", "The managed import profile changed after review. Prepare a fresh review.")
            config = resolve_managed_config(ImportConfig(), profile)
            result = run_managed_import(
                config,
                source_root,
                catalog,
                partition_id,
                upstream_release_id=expected_release,
                output_root=output_root,
                progress_callback=lambda message, current, total: emit_progress(type("Progress", (), {"message": message, "current": current, "total": total})()),
            )
        if result.get("status") not in {"completed", "reused"}:
            raise BridgeError("SOURCE_INVALID", f"Managed import ended with status {result.get('status') or 'unknown'}.")
        representation_id = str(preview.representation.get("representation_id") or "")
        existing_identity = dict(record.downstream_identity or {})
        downstream_database_id = str(result.get("corpus_id") or existing_identity.get("database_id") or "") or None
        collection_name = str(existing_identity.get("collection_name") or "rag_documents")
        return {
            "actual_writes": int((result.get("dedup") or {}).get("stored", preview.effects.writes) if result.get("status") == "completed" else 0),
            "imported_episodes": preview.effects.episodes_total,
            "skipped_episodes": 0,
            "retained_records": len(preview.effects.retained_missing_ids),
            "warnings": [],
            "output": str(result.get("export") or ""),
            "report_path": str(Path(str(result.get("export") or "")) / "import_manifest.json"),
            "active_database_state": "new_version_active",
            "operation_report": {"mode": preview.operation, "status": result.get("status"), "upstream_release_id": expected_release, "downstream_release_id": result.get("downstream_release_id"), "dedup": result.get("dedup") or {}},
            "downstream_identity": {"database_id": downstream_database_id, "collection_name": collection_name, "representation_id": representation_id, "profile": preview.representation.get("profile"), "upstream_release_id": expected_release, "downstream_release_id": result.get("downstream_release_id")},
        }

    def discover(self, catalog_path: Path, roots: list[Path]) -> dict[str, Any]:
        catalog = ManagedCatalog(catalog_path)
        try:
            return discover(catalog, roots)
        finally:
            catalog.connection.close()

    def start_action(self, source_root: Path, partition_id: str, action: str, callback: Callable[[str], None] | None = None) -> dict[str, Any]:
        adapter = PodcastRagSourceAdapter(source_root, partition_id)
        if action == "prepare":
            release = adapter.publish_release(callback)
            return {"action": action, "release": release, "catalog_refresh": self._refresh_catalog(source_root)}
        if action == "inspect":
            return {"action": action, "status": self.inspect(source_root, partition_id)}
        raise BridgeError("OPERATION_UNSUPPORTED", f"Unsupported source action: {action}")

    def _refresh_catalog(self, source_root: Path) -> dict[str, Any]:
        catalog_path = self._catalog_path(source_root)
        if catalog_path is None:
            return {"status": "not_registered"}
        with ManagedCatalog(catalog_path) as catalog:
            return discover(catalog, [source_root])
