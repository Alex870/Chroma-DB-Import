from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Mapping

from chroma_db_import.contract import has_text, sanitize_metadata
from chroma_db_import.importer import document_fingerprints
from chroma_db_import.representation import resolved_collection_name
from chroma_db_import.ui_export import DocumentLike, export_chroma
from chroma_db_import.ui_models import Episode, ImportPlan

from .models import BridgeError, DatabaseRecord, FrozenPreview, SelectionPolicy, stable_hash
from .planning import _spec_from_representation, inspect_existing_records, load_source, verify_source_snapshot
from .selection import speakers_for_episode
from .target_lock import target_lock_path


class FolderAdapter:
    """Adapter around the existing folder importer; no UI dependencies."""

    def build_plan(self, record: DatabaseRecord, preview: FrozenPreview, *, source_root: Path | None = None) -> ImportPlan:
        original_folder = Path(str(record.source_ref.get("path") or "")).expanduser().resolve()
        folder = (source_root or original_folder).expanduser().resolve()
        policy = SelectionPolicy.from_mapping(preview.selection_policy)
        episodes, _snapshot, _scan = load_source(folder, policy)
        if source_root is not None:
            accepted_fingerprints: dict[str, str] = {}
            for entry in list(preview.source_snapshot.get("files") or []):
                source_path = Path(str(entry.get("path") or "")).expanduser().resolve()
                try:
                    relative = source_path.relative_to(original_folder)
                except ValueError:
                    continue
                accepted_fingerprints[os.fspath(relative)] = str(entry.get("fingerprint") or "")
            for episode in episodes:
                relative = episode.path.relative_to(folder)
                original_path = original_folder / relative
                episode.fingerprint = accepted_fingerprints.get(os.fspath(relative), "") or episode.fingerprint
                episode.source_file_path = original_path
        included = {episode.fingerprint: speakers_for_episode(episode, policy) for episode in episodes}
        representation = dict(preview.representation)
        return ImportPlan(
            podcast_name=record.display_name,
            database_id=record.id,
            processed_data_dir=folder,
            output_root=Path(str(record.target["path"])).parent,
            collection_name=str(record.target.get("collection_name") or "whisper_rag_v2"),
            embedding_model=str(representation.get("model_id") or ""),
            embedding_device=str(preview.execution_options.get("embedding_device") or record.execution_options.get("embedding_device") or record.target.get("embedding_device") or "auto"),
            representation_profile=str(representation.get("profile") or record.target.get("representation_profile") or "qwen3-embedding-4b-shadow"),
            embedding_model_revision=str(representation.get("model_revision") or ""),
            final_export_dir=Path(str(record.target["path"])),
            episodes=episodes,
            included_speakers_by_episode=included,
        )

    def execute(
        self,
        record: DatabaseRecord,
        preview: FrozenPreview,
        emit_progress: Callable[[Any], None],
        *,
        operation_id_override: str | None = None,
        journal_callback: Callable[[str, dict[str, Any]], None] | None = None,
        lock_held: bool = False,
    ) -> dict[str, Any]:
        verify_source_snapshot(preview.source_snapshot)
        target = Path(str(record.target["path"])).expanduser().resolve()
        if str(preview.target_identity.get("path") or target) != str(target):
            raise BridgeError("PREVIEW_STALE", "The database destination changed after this review. Prepare a fresh review.")
        if preview.operation == "create" and target.exists():
            raise BridgeError("TARGET_EXISTS", "The destination appeared after review; creation was stopped and existing data was preserved.")
        self._verify_target_snapshot(record, preview, target)
        if preview.effects.writes == 0 and preview.operation == "update":
            return {
                "actual_writes": 0,
                "imported_episodes": 0,
                "skipped_episodes": preview.effects.episodes_total,
                "retained_records": len(preview.effects.retained_missing_ids),
                "warnings": [],
                "output": str(target),
                "report_path": str(target / "import_manifest.json"),
                "active_database_state": "unchanged",
                "operation_report": {"mode": "update", "status": "no_op", "preview_id": preview.preview_id},
            }
        if preview.operation == "remove_outdated":
            deleted = self._remove_outdated(record, preview)
            return {
                "actual_writes": 0,
                "deleted_records": deleted,
                "retained_records": 0,
                "warnings": [],
                "output": str(target),
                "report_path": str(target / "state" / "gui-removal-report.json"),
                "active_database_state": "new_version_active",
                "operation_report": {"mode": "remove_outdated", "status": "promoted", "preview_id": preview.preview_id},
            }
        source_root = self._capture_source_snapshot(record, preview, target)
        try:
            self._verify_target_snapshot(record, preview, target)
            if (
                preview.operation == "update"
                and preview.effects.metadata_only_ids
                and not preview.effects.insert_ids
                and not preview.effects.replace_ids
            ):
                return self._update_metadata_only(record, preview, source_root=source_root)
            plan = self.build_plan(record, preview, source_root=source_root)
            summary = export_chroma(
                plan,
                "update" if preview.operation == "update" else "create",
                emit_progress,
                operation_id_override=operation_id_override,
                journal_callback=journal_callback,
                lock_held=lock_held,
            )
            downstream_identity = self._read_downstream_identity(Path(summary.export_dir), record, preview)
            return {
                "actual_writes": summary.inserted,
                "imported_episodes": summary.imported_episodes,
                "skipped_episodes": summary.skipped_episodes,
                "retained_records": len(preview.effects.retained_missing_ids),
                "warnings": list(summary.warnings),
                "output": str(summary.export_dir),
                "report_path": str(summary.export_dir / "import_manifest.json"),
                "active_database_state": "new_version_active",
                "operation_report": summary.operation_report,
                "downstream_identity": downstream_identity,
            }
        finally:
            shutil.rmtree(source_root, ignore_errors=True)

    @staticmethod
    def _verify_target_snapshot(record: DatabaseRecord, preview: FrozenPreview, target: Path) -> None:
        expected = str(preview.target_identity.get("content_hash") or "")
        if not expected:
            return
        spec = _spec_from_representation(preview.representation)
        base_collection_name = str(record.target.get("collection_name") or "whisper_rag_v2")
        collection_name = resolved_collection_name(base_collection_name, spec.profile)
        existing = inspect_existing_records(
            target,
            collection_name,
            fallback_collection_name=base_collection_name if collection_name != base_collection_name else None,
        )
        if stable_hash(existing) != expected:
            raise BridgeError("PREVIEW_STALE", "The target database changed after this review. Prepare a fresh review.")

    @staticmethod
    def _update_metadata_only(record: DatabaseRecord, preview: FrozenPreview, *, source_root: Path) -> dict[str, Any]:
        """Apply stable-ID metadata changes without loading an embedding provider."""
        target = Path(str(record.target["path"])).expanduser().resolve()
        folder = source_root.expanduser().resolve()
        policy = SelectionPolicy.from_mapping(preview.selection_policy)
        episodes, _snapshot, _scan = load_source(folder, policy)
        original_folder = Path(str(record.source_ref.get("path") or "")).expanduser().resolve()
        for episode in episodes:
            episode.source_file_path = original_folder / episode.path.relative_to(folder)
        spec = _spec_from_representation(preview.representation)
        desired: dict[str, dict[str, Any]] = {}
        for episode in episodes:
            for document in episode.documents:
                if not has_text(document.page_content):
                    continue
                # Use the same sanitized metadata and fingerprints as the importer.
                item = DocumentLike(document.page_content, sanitize_metadata(dict(document.metadata)))
                fingerprints = document_fingerprints(item, spec)
                item.metadata.update(fingerprints)
                item.metadata["import_source_cache"] = str((episode.source_file_path or episode.path).resolve())
                desired[str(item.metadata.get("node_id"))] = item.metadata

        ids = list(preview.effects.metadata_only_ids)
        missing = [identifier for identifier in ids if identifier not in desired]
        if missing:
            raise BridgeError("PREVIEW_STALE", "The source selection changed after this review. Prepare a fresh review.")

        try:
            import chromadb
            from chromadb.api.client import SharedSystemClient

            existing_systems = list(SharedSystemClient._identifier_to_system.values())
            client = chromadb.PersistentClient(path=str(target))
            owns_chroma_system = not any(system is getattr(client, "_system", None) for system in existing_systems)
            try:
                collection = client.get_collection(
                    resolved_collection_name(
                        str(record.target.get("collection_name") or "whisper_rag_v2"),
                        spec.profile,
                    )
                )
                collection.update(ids=ids, metadatas=[desired[identifier] for identifier in ids])
            finally:
                if owns_chroma_system:
                    try:
                        client._system.stop()
                    finally:
                        SharedSystemClient.clear_system_cache()
        except BridgeError:
            raise
        except Exception as exc:
            raise BridgeError("JOB_FAILED", f"Metadata-only changes could not be applied: {exc}") from exc

        return {
            "actual_writes": len(ids),
            "metadata_only_records": len(ids),
            "imported_episodes": 0,
            "skipped_episodes": preview.effects.episodes_total,
            "retained_records": len(preview.effects.retained_missing_ids),
            "warnings": [],
            "output": str(target),
            "report_path": str(target / "import_manifest.json"),
            "active_database_state": "new_version_active",
            "operation_report": {
                "mode": "update",
                "status": "metadata_only",
                "preview_id": preview.preview_id,
                "updated_ids": ids,
            },
        }

    @staticmethod
    def _capture_source_snapshot(record: DatabaseRecord, preview: FrozenPreview, target: Path) -> Path:
        """Copy the accepted source files into an immutable job-owned workspace."""
        source_folder = Path(str(record.source_ref.get("path") or "")).expanduser().resolve()
        snapshot_root = target.parent / f".{target.name}.source-{preview.preview_id}"
        if snapshot_root.exists():
            raise BridgeError("TARGET_BUSY", "A source snapshot for this review already exists.")
        try:
            snapshot_root.mkdir(parents=True, exist_ok=False)
            for entry in list(preview.source_snapshot.get("files") or []):
                source_path = Path(str(entry.get("path") or "")).expanduser().resolve()
                try:
                    relative = source_path.relative_to(source_folder)
                except ValueError as exc:
                    raise BridgeError("PREVIEW_STALE", "The accepted source snapshot is no longer valid. Prepare a fresh review.") from exc
                destination = snapshot_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination)
                digest = hashlib.sha256(destination.read_bytes()).hexdigest()
                if digest != str(entry.get("sha256") or ""):
                    raise BridgeError("PREVIEW_STALE", "The source changed while the accepted snapshot was being captured. Prepare a fresh review.")

            topic_candidates = [
                source_folder / "topic_index.json",
                source_folder / "state" / "topic_index.json",
                source_folder / "processed_data" / "topic_index.json",
                source_folder.parent / "state" / "topic_index.json",
            ]
            for candidate in topic_candidates:
                if not candidate.is_file():
                    continue
                if candidate.parent == source_folder:
                    relative = candidate.name
                else:
                    relative = Path(candidate.name)
                destination = snapshot_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, destination)
                break

            # Re-check the originals after the copy. The importer then reads
            # only the immutable snapshot, so later source mutations cannot
            # alter the accepted job midway through embedding.
            verify_source_snapshot(preview.source_snapshot)
            return snapshot_root
        except Exception:
            shutil.rmtree(snapshot_root, ignore_errors=True)
            raise

    @staticmethod
    def _read_downstream_identity(export_dir: Path, record: DatabaseRecord, preview: FrozenPreview) -> dict[str, Any]:
        manifest: dict[str, Any] = {}
        metadata: dict[str, Any] = {}
        for filename in ("import_manifest.json", "podcast.json"):
            path = export_dir / filename
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if filename == "import_manifest.json":
                manifest = payload if isinstance(payload, dict) else {}
            else:
                metadata = payload if isinstance(payload, dict) else {}
        representation = dict(manifest.get("representation") or {})
        return {
            "database_id": metadata.get("database_id") or manifest.get("database_id") or record.id,
            "collection_name": manifest.get("collection_name") or preview.target_identity.get("collection_name") or record.target.get("collection_name"),
            "representation_id": manifest.get("representation_id") or representation.get("representation_id") or preview.representation.get("representation_id"),
            "profile": manifest.get("representation_profile") or representation.get("profile") or preview.representation.get("profile"),
        }

    @staticmethod
    def _remove_outdated(record: DatabaseRecord, preview: FrozenPreview) -> int:
        target = Path(str(record.target["path"])).expanduser().resolve()
        ids = sorted(set(preview.effects.delete_ids))
        if not ids:
            return 0
        try:
            import chromadb
            from chromadb.api.client import SharedSystemClient

            spec = _spec_from_representation(preview.representation)
            base_collection_name = str(record.target.get("collection_name") or "whisper_rag_v2")
            collection_name = resolved_collection_name(base_collection_name, spec.profile)
            existing_systems = list(SharedSystemClient._identifier_to_system.values())
            client = chromadb.PersistentClient(path=str(target))
            owns_chroma_system = not any(system is getattr(client, "_system", None) for system in existing_systems)
            try:
                try:
                    collection = client.get_collection(collection_name)
                except Exception:
                    if collection_name == base_collection_name:
                        raise
                    collection = client.get_collection(base_collection_name)
                collection.delete(ids=ids)
            finally:
                if owns_chroma_system:
                    try:
                        client._system.stop()
                    finally:
                        SharedSystemClient.clear_system_cache()
        except Exception as exc:
            raise BridgeError("JOB_FAILED", f"Outdated records could not be removed: {exc}") from exc
        report = target / "state" / "gui-removal-report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"preview_id": preview.preview_id, "deleted_ids": ids}, indent=2), encoding="utf-8")
        return len(ids)
