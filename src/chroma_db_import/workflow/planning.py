from __future__ import annotations

import hashlib
import json
import os
from dataclasses import fields
from pathlib import Path
from typing import Any, Iterable, Mapping

from chroma_db_import.contract import file_fingerprint, sanitize_metadata
from chroma_db_import.representation import RepresentationSpec, embedding_fingerprint, metadata_fingerprint, resolve_representation_spec, resolved_collection_name
from chroma_db_import.ui_helpers import safe_folder_name
from chroma_db_import.ui_loader import EpisodeLoader
from chroma_db_import.ui_models import Episode, ProcessedDocument

from .models import BridgeError, DatabaseRecord, ExecutionOptions, FrozenPreview, PreviewEffects, SelectionPolicy, new_id, stable_hash, utc_now
from .selection import select_documents, speakers_for_episode


def source_snapshot(paths: Iterable[Path]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted({Path(item).expanduser().resolve() for item in paths}, key=os.fspath):
        if not path.is_file():
            raise BridgeError("SOURCE_UNAVAILABLE", f"Source file is unavailable: {path}", details_id=stable_hash(str(path))[:16])
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append({"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest, "fingerprint": file_fingerprint(path)})
    return {"files": entries, "hash": stable_hash(entries)}


def verify_source_snapshot(snapshot: Mapping[str, Any]) -> None:
    for entry in list(snapshot.get("files") or []):
        path = Path(str(entry.get("path")))
        if not path.is_file():
            raise BridgeError("PREVIEW_STALE", f"The source changed after this preview: {path} is unavailable.")
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        if current != str(entry.get("sha256") or ""):
            raise BridgeError("PREVIEW_STALE", "The source changed after this preview. Review the latest changes.")


def load_source(folder: Path, policy: SelectionPolicy) -> tuple[list[Episode], dict[str, Any], dict[str, Any]]:
    if not folder.is_dir():
        raise BridgeError("SOURCE_UNAVAILABLE", f"The source folder is unavailable: {folder}")
    loader = EpisodeLoader()
    episodes = loader.load_folder(folder, policy.asset_filter, policy.asset_pattern)
    selected_paths = list(loader.last_selection.selected_paths)
    snapshot = source_snapshot(selected_paths)
    scan = {
        "folder": str(folder.resolve()),
        "episodes": len(episodes),
        "eligible_records": sum(len(select_documents(episode, policy)) for episode in episodes),
        "excluded_files": [str(path) for path in loader.last_selection.excluded_paths],
        "speakers": sorted({speaker for episode in episodes for speaker in episode.speakers}),
        "date_range": {"start": min((episode.episode_date for episode in episodes if episode.episode_date), default=""), "end": max((episode.episode_date for episode in episodes if episode.episode_date), default="")},
        "episode_inventory": [
            {
                "episode_id": episode.episode_id,
                "title": episode.title,
                "date": episode.episode_date,
                "document_count": len(select_documents(episode, policy)),
                "speakers": sorted(episode.speakers),
            }
            for episode in episodes
        ],
    }
    return episodes, snapshot, scan


def _record_id(document: ProcessedDocument) -> str:
    return str(document.metadata.get("node_id") or document.metadata.get("stable_document_id") or stable_hash({"text": document.page_content, "metadata": document.metadata})[:24])


def _record_fingerprint(document: ProcessedDocument, spec: RepresentationSpec) -> tuple[str, str]:
    """Use the same sanitized metadata and representation identity as import."""
    metadata = sanitize_metadata(dict(document.metadata))
    return (
        embedding_fingerprint(document.page_content, metadata, spec),
        metadata_fingerprint(metadata),
    )


def _spec_from_representation(value: Mapping[str, Any]) -> RepresentationSpec:
    allowed = {item.name for item in fields(RepresentationSpec)}
    payload = {key: value[key] for key in allowed if key in value}
    try:
        spec = RepresentationSpec(**payload)
        spec.validate()
    except Exception as exc:
        raise ValueError(f"invalid Qwen3 representation metadata: {exc}") from exc
    if spec.model_id != "Qwen/Qwen3-Embedding-4B":
        raise ValueError(f"only Qwen/Qwen3-Embedding-4B representations are supported, got {spec.model_id!r}")
    if spec.model_revision != "5cf2132abc99cad020ac570b19d031efec650f2b":
        raise ValueError("Qwen3 representation metadata has an unsupported model revision")
    if spec.dimension != 2560:
        raise ValueError(f"Qwen3 representation metadata must declare dimension 2560, got {spec.dimension!r}")
    if spec.provider != "sentence_transformers" or not spec.normalize_embeddings or spec.distance_metric != "cosine":
        raise ValueError("Qwen3 representation metadata has an incompatible provider, normalization, or distance metric")
    if spec.query_instruction_profile != "podcast-retrieval-v1" or spec.query_document_mode != "separate-query-instruction":
        raise ValueError("Qwen3 representation metadata has an incompatible query encoding contract")
    declared_id = str(value.get("representation_id") or "")
    if declared_id and declared_id != spec.representation_id:
        raise ValueError("Qwen3 representation_id does not match its representation fields")
    return spec


def inspect_existing_records(
    target: Path,
    collection_name: str,
    *,
    fallback_collection_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    if not target.is_dir() or not (target / "chroma.sqlite3").is_file():
        return {}
    try:
        import chromadb
        from chromadb.api.client import SharedSystemClient
        existing_systems = list(SharedSystemClient._identifier_to_system.values())
        client = chromadb.PersistentClient(path=str(target))
        owns_chroma_system = not any(system is getattr(client, "_system", None) for system in existing_systems)
        try:
            payload = None
            for candidate_name in (collection_name, fallback_collection_name):
                if not candidate_name:
                    continue
                try:
                    collection = client.get_collection(candidate_name)
                    payload = collection.get(include=["metadatas", "documents"])
                    break
                except Exception:
                    continue
            if payload is None:
                return {}
        except Exception:
            return {}
        finally:
            if owns_chroma_system:
                try:
                    client._system.stop()
                    SharedSystemClient.clear_system_cache()
                except Exception:
                    pass
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    ids = list(payload.get("ids") or [])
    metadatas = list(payload.get("metadatas") or [])
    documents = list(payload.get("documents") or [])
    for index, identifier in enumerate(ids):
        metadata = dict(metadatas[index] or {}) if index < len(metadatas) else {}
        result[str(identifier)] = {
            "id": str(identifier),
            "metadata": metadata,
            "document": documents[index] if index < len(documents) else None,
            "fingerprint": str(metadata.get("gui_record_fingerprint") or metadata.get("embedding_fingerprint") or ""),
            "metadata_fingerprint": str(metadata.get("gui_metadata_fingerprint") or metadata.get("metadata_fingerprint") or ""),
        }
    return result


def _existing_manifest(target: Path) -> dict[str, Any]:
    for name in ("import_manifest.json", "podcast.json"):
        path = target / name
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def _representation(record: DatabaseRecord, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(payload or {})
    representation = dict(raw.get("representation") or {})
    if representation:
        return representation
    config = type("Config", (), {
        "representation_profile": raw.get("representation_profile", record.target.get("representation_profile", "qwen3-embedding-4b-shadow")),
        "embedding_model": raw.get("embedding_model", ""),
        "embedding_model_revision": raw.get("embedding_model_revision", ""),
        "inference_dtype": raw.get("inference_dtype", "bfloat16"),
        "query_instruction_profile": raw.get("query_instruction_profile", "podcast-retrieval-v1"),
        "contextualization": raw.get("contextualization", "minimal"),
        "embedding_provider": raw.get("embedding_provider", "sentence_transformers"),
        "normalize_embeddings": True,
        "distance_metric": "cosine",
        "output_dimension": None,
        "matryoshka_compatible": False,
        "embedding_dimension": None,
    })
    try:
        return resolve_representation_spec(config).as_dict()
    except Exception:
        return {"profile": str(raw.get("representation_profile") or "unknown"), "representation_id": str(raw.get("representation_id") or "")}


def create_folder_preview(record: DatabaseRecord | None, draft: Mapping[str, Any], *, operation: str) -> FrozenPreview:
    policy = SelectionPolicy.from_mapping(draft.get("selection_policy") or {})
    source_raw = str((draft.get("source_ref") or {}).get("path") or "").strip()
    if not source_raw:
        raise BridgeError("SOURCE_UNAVAILABLE", "A processed source folder is required.", field="source_ref.path")
    folder = Path(source_raw).expanduser().resolve()
    episodes, snapshot, scan = load_source(folder, policy)
    target_raw = str((draft.get("target") or {}).get("path") or "").strip()
    if not target_raw:
        raise BridgeError("VALIDATION_FAILED", "A database destination is required.", field="target.path")
    target = Path(target_raw).expanduser().resolve()
    if operation == "create" and target.exists():
        raise BridgeError("TARGET_EXISTS", "Creation cannot overwrite an existing database. Choose another location or use Add existing.")
    if record:
        registered_target = Path(str(record.target.get("path") or "")).expanduser().resolve()
        if registered_target != target:
            raise BridgeError("VALIDATION_FAILED", "An existing database update must use its registered destination.", field="target.path")
        target_identity = dict(record.target)
        settings_revision = record.settings_revision
        database_id = record.id
    else:
        target_identity = {"path": str(target), "normalized": os.path.normcase(str(target)).casefold()}
        settings_revision = int(draft.get("settings_revision") or 1)
        database_id = None
    representation = _representation(record or DatabaseRecord(
        id="draft", display_name=str(draft.get("display_name") or "Draft"), source_kind="folder",
        source_ref={"path": str(folder)}, target={"path": str(target)},
    ), draft)
    profile_findings: list[dict[str, Any]] = []
    if record is not None and operation in {"update", "rebuild"}:
        stored_profile = str(record.downstream_identity.get("profile") if record.downstream_identity else record.target.get("representation_profile") or "")
        requested_profile = str(representation.get("profile") or "")
        if stored_profile and requested_profile and stored_profile != requested_profile:
            profile_findings.append({
                "severity": "error",
                "code": "PROFILE_MISMATCH",
                "message": f"The selected representation profile ({requested_profile}) does not match the registered database profile ({stored_profile}). Create a separate database for this representation.",
            })
    spec = _spec_from_representation(representation)
    target_payload = dict(draft.get("target") or {})
    base_collection_name = str(draft.get("collection_name") or target_payload.get("collection_name") or (record.target.get("collection_name") if record else "") or "whisper_rag_v2")
    collection_name = resolved_collection_name(base_collection_name, spec.profile)
    existing = inspect_existing_records(
        target,
        collection_name,
        fallback_collection_name=base_collection_name if collection_name != base_collection_name else None,
    )
    current: dict[str, dict[str, Any]] = {}
    episode_changes: list[dict[str, Any]] = []
    for episode in episodes:
        selected = select_documents(episode, policy)
        added = changed = metadata_only = unchanged = 0
        for document in selected:
            identifier = _record_id(document)
            fp, mfp = _record_fingerprint(document, spec)
            current[identifier] = {"id": identifier, "fingerprint": fp, "metadata_fingerprint": mfp, "document": document.page_content, "episode_id": episode.episode_id, "title": episode.title}
            old = existing.get(identifier)
            if old is None:
                added += 1
            elif old.get("fingerprint") == fp or (not old.get("fingerprint") and old.get("document") == document.page_content):
                if old.get("metadata_fingerprint") == mfp:
                    unchanged += 1
                else:
                    metadata_only += 1
            elif old.get("metadata_fingerprint") == mfp:
                changed += 1
            else:
                changed += 1
        old_episode_ids = {str(item.get("metadata", {}).get("episode_id") or "") for item in existing.values() if item.get("metadata", {}).get("episode_id")}
        episode_changes.append({"episode_id": episode.episode_id, "title": episode.title, "records": len(selected), "added": added, "changed": changed, "metadata_only": metadata_only, "unchanged": unchanged, "source_fingerprint": episode.source_content_fingerprint or episode.fingerprint})
    insert_ids: list[str] = []
    replace_ids: list[str] = []
    metadata_ids: list[str] = []
    unchanged_ids: list[str] = []
    for identifier, item in current.items():
        old = existing.get(identifier)
        if old is None:
            insert_ids.append(identifier)
        elif old.get("fingerprint") == item["fingerprint"] or (not old.get("fingerprint") and old.get("document") == item.get("document")):
            if old.get("metadata_fingerprint") == item["metadata_fingerprint"]:
                unchanged_ids.append(identifier)
            else:
                metadata_ids.append(identifier)
        else:
            # A changed embedding input needs replacement even if metadata
            # changed at the same time. Only a stable embedding with changed
            # metadata can use the metadata-only path.
            replace_ids.append(identifier)
    retained = sorted(set(existing).difference(current))
    resulting_ids = (set(existing) | set(current))
    findings: list[dict[str, Any]] = list(profile_findings)
    if not episodes:
        findings.append({"severity": "warning", "code": "NO_ELIGIBLE_CONTENT", "message": "No eligible processed files matched the selected asset policy."})
    if not target.parent.exists():
        findings.append({"severity": "warning", "code": "PARENT_MISSING", "message": "The destination parent will be created during activation."})
    requested_delete_ids = sorted({str(item).strip() for item in (draft.get("delete_ids") or []) if str(item).strip()})
    if operation == "remove_outdated":
        unsupported_ids = sorted(set(requested_delete_ids).difference(retained))
        if unsupported_ids:
            raise BridgeError(
                "VALIDATION_FAILED",
                "Maintenance removal is limited to records retained as missing by ordinary Update.",
                details_id=stable_hash(unsupported_ids)[:16],
            )
        resulting_ids.difference_update(requested_delete_ids)
    delete_reasons: dict[str, str] = {}
    delete_episode_rows: dict[str, dict[str, Any]] = {}
    if operation == "remove_outdated":
        for identifier in requested_delete_ids:
            old = existing.get(identifier) or {}
            metadata = dict(old.get("metadata") or {})
            episode_id = str(metadata.get("episode_uid") or metadata.get("episode_id") or "unknown episode")
            title = str(metadata.get("episode_title") or metadata.get("title") or episode_id)
            delete_reasons[identifier] = f'Explicitly accepted outdated record from episode "{title}" ({episode_id}).'
            row = delete_episode_rows.setdefault(episode_id, {"episode_id": episode_id, "title": title, "record_ids": []})
            row["record_ids"].append(identifier)
    effects = PreviewEffects(
        episodes_total=len(episodes), records_total=len(resulting_ids), insert_ids=sorted(insert_ids), replace_ids=sorted(replace_ids),
        metadata_only_ids=sorted(metadata_ids), unchanged_ids=sorted(unchanged_ids), retained_missing_ids=retained,
        episode_changes=episode_changes, delete_episodes=list(delete_episode_rows.values()),
        reasons={identifier: "retained by ordinary update" for identifier in retained} | delete_reasons,
    )
    if operation == "remove_outdated":
        effects.delete_ids = requested_delete_ids
    required = ["REMOVE_OUTDATED_RECORDS"] if operation == "remove_outdated" and effects.delete_ids else []
    execution_options = ExecutionOptions.from_mapping(draft.get("execution_options") or (record.execution_options if record else None)).as_dict()
    return FrozenPreview(
        preview_id=new_id("preview"), operation=operation, database_id=database_id, draft_id=str(draft.get("draft_id") or "") or None,
        created_at=utc_now(), settings_revision=settings_revision,
        settings_hash=stable_hash({
            "source_kind": str(draft.get("source_kind") or (record.source_kind if record else "folder")),
            "source_path": str((draft.get("source_ref") or {}).get("path") or folder),
            "target_path": str(target),
            "selection_policy": policy.as_dict(),
            "execution_options": execution_options,
        }),
        source_snapshot=snapshot, target_identity={**target_identity, "path": str(target), "source_hash": snapshot["hash"], "content_hash": stable_hash(existing)},
        selection_policy=policy.as_dict(), representation=representation, validation_findings=findings, effects=effects,
        required_acknowledgments=required, execution_options=execution_options,
    )


def scan_folder(folder: Path, policy: SelectionPolicy) -> dict[str, Any]:
    _episodes, _snapshot, scan = load_source(folder, policy)
    return scan
