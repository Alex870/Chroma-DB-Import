"""Read-only, source-aware inspection helpers for the Modern UI.

This module deliberately owns the active-export resolver.  Callers must never
guess a managed path from a display name or silently fall back to an older
release when the active pointer is missing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from chroma_db_import.releases import inspect_export_work
from chroma_db_import.ui_loader import EpisodeLoader

from .models import BridgeError, DatabaseRecord, SelectionPolicy, make_report, stable_hash, utc_now
from .selection import select_documents


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"invalid:{type(exc).__name__}"
    if not isinstance(payload, dict):
        return None, "invalid:object_required"
    return payload, None


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_active_export(record: Mapping[str, Any]) -> Path:
    """Resolve and validate the exact export represented by a database record."""
    target = Path(str((record.get("target") or {}).get("path") or "")).expanduser().resolve()
    if record.get("source_kind") != "managed":
        if not target.is_dir():
            raise BridgeError("ACTIVE_EXPORT_MISSING", "The registered database export is not available.", field="target.path")
        return target

    source_ref = dict(record.get("source_ref") or {})
    output_root = Path(str((record.get("target") or {}).get("managed_output_root") or "")).expanduser().resolve()
    partition_id = str(source_ref.get("partition_id") or "").strip()
    if not output_root or not partition_id:
        raise BridgeError("ACTIVE_EXPORT_INVALID", "This managed database has no complete active-export reference.")
    from chroma_db_import.managed import managed_paths

    partition_root = managed_paths(output_root, partition_id)["partition_root"]
    pointer = partition_root / "active-release.json"
    if not pointer.is_file():
        raise BridgeError("ACTIVE_EXPORT_MISSING", "No active managed export is recorded for this database.")
    pointer_payload, pointer_error = _read_json(pointer)
    if pointer_error or pointer_payload is None:
        raise BridgeError("ACTIVE_EXPORT_INVALID", "The active managed export pointer is malformed.", details_id=stable_hash(str(pointer))[:16])
    release_id = str(pointer_payload.get("release_id") or "").strip()
    if not release_id:
        raise BridgeError("ACTIVE_EXPORT_INVALID", "The active managed export pointer has no release ID.")
    if str(pointer_payload.get("partition_id") or partition_id) != partition_id:
        raise BridgeError("ACTIVE_EXPORT_INVALID", "The active managed export pointer belongs to another partition.")
    resolved = managed_paths(output_root, partition_id, release_id)["export_root"].resolve()
    if not _within(resolved, partition_root.resolve()) or not resolved.is_dir():
        raise BridgeError("ACTIVE_EXPORT_INVALID", "The active managed export path is outside the registered output or is unavailable.")
    if target.exists() and target.resolve() != resolved:
        # A managed record may retain its partition root as target for legacy
        # compatibility, but an explicitly conflicting target is unsafe.
        target_as_partition = target.resolve() == partition_root.resolve()
        if not target_as_partition:
            raise BridgeError("ACTIVE_EXPORT_INVALID", "The registered managed target disagrees with its active pointer.")
    return resolved


def _finding(severity: str, code: str, message: str, *, path: str | None = None) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, **({"path": path} if path else {})}


def inspect_database(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a durable-shaped report without scanning the current source."""
    scope = {"database_id": str(record.get("id") or "")}
    findings: list[dict[str, Any]] = []
    try:
        target = resolve_active_export(record)
    except BridgeError as exc:
        return make_report("database_details", scope, status="unavailable", findings=[_finding("error", exc.code, exc.message)], details={"registered_target": str((record.get("target") or {}).get("path") or ""), "active_export": None})

    manifest, manifest_error = _read_json(target / "import_manifest.json")
    metadata, metadata_error = _read_json(target / "podcast.json")
    if manifest_error:
        findings.append(_finding("error", "MANIFEST_MISSING" if manifest_error == "missing" else "MANIFEST_INVALID", "The active export has no readable import manifest.", path=str(target / "import_manifest.json")))
    if metadata_error:
        findings.append(_finding("error", "METADATA_MISSING" if metadata_error == "missing" else "METADATA_INVALID", "The active export has no readable podcast metadata.", path=str(target / "podcast.json")))
    manifest = manifest or {}
    metadata = metadata or {}
    identity = dict(record.get("downstream_identity") or {})
    manifest_identity = {
        "database_id": manifest.get("database_id") or metadata.get("database_id"),
        "collection_name": manifest.get("collection_name") or metadata.get("collection_name"),
        "representation_id": manifest.get("representation_id") or (manifest.get("representation") or {}).get("representation_id"),
        "profile": manifest.get("representation_profile") or (manifest.get("representation") or {}).get("profile"),
    }
    for key in ("database_id", "collection_name", "representation_id"):
        expected = str(identity.get(key) or "")
        actual = str(manifest_identity.get(key) or "")
        if expected and actual and expected != actual:
            findings.append(_finding("error", "IDENTITY_MISMATCH", f"Recorded {key} does not match the active export."))
    representation = dict(manifest.get("representation") or {})
    episodes = [item for item in metadata.get("episodes") or [] if isinstance(item, Mapping)]
    try:
        work = inspect_export_work(target) if manifest_error is None else {}
    except Exception as exc:
        work = {}
        findings.append(_finding("warning", "EXPORT_WORK_UNAVAILABLE", f"Importer work details are unavailable: {type(exc).__name__}."))
    if not findings:
        status = "pass"
    elif any(item["severity"] == "error" for item in findings):
        status = "failed"
    else:
        status = "warnings"
    return make_report(
        "database_details", scope, status=status,
        summary={"stored_episodes": len(episodes), "stored_documents": sum(int(item.get("document_count") or 0) for item in episodes), "metadata_valid": not bool(metadata_error), "manifest_valid": not bool(manifest_error)},
        findings=findings,
        details={
            "active_export": str(target), "database_id": manifest_identity.get("database_id") or identity.get("database_id") or record.get("id"),
            "resolved_collection": manifest_identity.get("collection_name") or identity.get("collection_name"),
            "manifest_version": manifest.get("manifest_version") or manifest.get("schema_version") or manifest.get("importer_version"),
            "importer_version": manifest.get("importer_version") or manifest.get("producer", {}).get("version"),
            "model": manifest.get("embedding_model") or representation.get("model_id"),
            "model_revision": representation.get("model_revision"), "dimensions": manifest.get("embedding_dimension") or representation.get("dimension"),
            "representation_id": manifest_identity.get("representation_id"), "representation": representation,
            "source_file_count": len(manifest.get("source_files") or manifest.get("files") or []), "source_episode_count": manifest.get("source_episode_count"),
            "managed_release_ids": {"upstream": manifest.get("upstream_release_id"), "downstream": manifest.get("downstream_release_id") or manifest.get("corpus_release_id")},
            "profile_fingerprint": manifest.get("import_profile_fingerprint") or manifest.get("profile_fingerprint"),
            "metadata_validity": {"manifest": not bool(manifest_error), "podcast": not bool(metadata_error), "checked_at": utc_now()},
            "export_work": work,
            "raw_manifest": manifest, "raw_metadata": metadata,
        },
    )


def _stored_episode_rows(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in metadata.get("episodes") or []:
        if not isinstance(item, Mapping):
            continue
        episode_id = str(item.get("episode_id") or item.get("episode_uid") or "").strip()
        if not episode_id:
            continue
        rows.append({
            "episode_id": episode_id, "title": str(item.get("episode_title") or item.get("title") or episode_id),
            "date": str(item.get("episode_date") or "") or None, "source_file": item.get("source_file") or item.get("source"),
            "stored_document_count": int(item.get("document_count") or 0), "stored_fingerprint": str(item.get("source_fingerprint") or item.get("fingerprint") or item.get("content_fingerprint") or ""),
            "speakers": sorted({str(value.get("name") or value.get("id") or "").strip() for value in item.get("speakers") or [] if isinstance(value, Mapping) and str(value.get("name") or value.get("id") or "").strip()}),
            "node_counts": dict(item.get("node_counts") or {}),
        })
    return rows


def inventory_database(record: Mapping[str, Any], *, policy: Mapping[str, Any] | None = None, offset: int = 0, limit: int = 100, search: str = "") -> dict[str, Any]:
    """Join source and stored episode inventories while preserving unavailable values."""
    if offset < 0 or limit < 1 or limit > 500:
        raise BridgeError("VALIDATION_FAILED", "offset must be non-negative and limit must be between 1 and 500.")
    database_id = str(record.get("id") or "")
    selected_policy = SelectionPolicy.from_mapping(policy or record.get("selection_policy"))
    findings: list[dict[str, Any]] = []
    target: Path | None = None
    metadata: dict[str, Any] = {}
    try:
        target = resolve_active_export(record)
        metadata, metadata_error = _read_json(target / "podcast.json")
        if metadata_error:
            findings.append(_finding("error", "METADATA_UNAVAILABLE", "Stored episode metadata is unavailable from the active export."))
            metadata = {}
    except BridgeError as exc:
        findings.append(_finding("error", exc.code, exc.message))
    stored = _stored_episode_rows(metadata)
    stored_by_id = {row["episode_id"]: row for row in stored}
    source_rows: list[dict[str, Any]] = []
    source_error: str | None = None
    source_ref = dict(record.get("source_ref") or {})
    source_path = str(source_ref.get("path") or "")
    if record.get("source_kind") == "managed":
        source_root = str(source_ref.get("path") or "")
        if not source_path and source_root and source_ref.get("partition_id"):
            root = Path(str(source_ref.get("source_root") or ""))
            partition = str(source_ref.get("partition_id"))
            source_path = str(root / "partitions" / partition / "processed_data")
            if not Path(source_path).is_dir():
                source_path = str(root / partition / "processed_data")
    if source_path:
        try:
            loader = EpisodeLoader()
            episodes = loader.load_folder(Path(source_path).expanduser().resolve(), selected_policy.asset_filter, selected_policy.asset_pattern)
            for episode in episodes:
                included = select_documents(episode, selected_policy)
                stored_row = stored_by_id.get(episode.episode_id)
                old_fingerprint = str((stored_row or {}).get("stored_fingerprint") or "")
                comparison = "new" if stored_row is None else ("imported" if old_fingerprint and old_fingerprint in {episode.fingerprint, episode.source_content_fingerprint} else "changed")
                source_rows.append({
                    "episode_id": episode.episode_id, "title": episode.title, "date": episode.episode_date or None, "source_file": str(episode.path),
                    "source_document_count": len(episode.documents), "stored_document_count": (stored_row or {}).get("stored_document_count"), "included_document_count": len(included),
                    "node_counts": {key: episode.node_counts.get(key) for key in ("leaf_chunk", "position_card", "cluster_summary", "episode_thesis")},
                    "speakers": sorted(episode.speakers), "override_mode": "custom" if episode.episode_id in selected_policy.episode_overrides else "inherit",
                    "override_speakers": list(selected_policy.episode_overrides.get(episode.episode_id) or []), "comparison": comparison, "checked_at": utc_now(),
                    "document_count": len(included), "excluded": episode.episode_id in selected_policy.excluded_episode_ids,
                })
        except (OSError, ValueError, BridgeError) as exc:
            source_error = f"{type(exc).__name__}: {exc}"
    if source_error:
        findings.append(_finding("warning", "SOURCE_UNAVAILABLE", "Current source inventory is unavailable; stored episodes remain available."))
    source_ids = {row["episode_id"] for row in source_rows}
    for row in stored:
        if row["episode_id"] in source_ids:
            continue
        source_rows.append({
            "episode_id": row["episode_id"], "title": row["title"], "date": row["date"], "source_file": row.get("source_file"),
            "source_document_count": None, "stored_document_count": row["stored_document_count"], "included_document_count": None,
            "node_counts": {key: row.get("node_counts", {}).get(key) for key in ("leaf_chunk", "position_card", "cluster_summary", "episode_thesis")},
            "speakers": row.get("speakers", []), "override_mode": "custom" if row["episode_id"] in selected_policy.episode_overrides else "inherit",
            "override_speakers": list(selected_policy.episode_overrides.get(row["episode_id"]) or []), "comparison": "missing_source", "checked_at": utc_now(),
            "document_count": row["stored_document_count"], "excluded": row["episode_id"] in selected_policy.excluded_episode_ids,
        })
    term = search.strip().casefold()
    filtered = [row for row in source_rows if not term or term in f"{row['title']} {row['episode_id']}".casefold()]
    status = "failed" if any(item["severity"] == "error" for item in findings) and not source_rows else ("warnings" if findings else "pass")
    report = make_report("content_inspection", {"database_id": database_id}, status=status,
        summary={"total": len(filtered), "source_available": not bool(source_error), "stored_available": bool(stored), "policy_fingerprint": selected_policy.fingerprint},
        findings=findings, details={"target": str(target) if target else None, "source_error": source_error, "policy_fingerprint": selected_policy.fingerprint})
    metadata_speakers = {
        str(item.get("name") or item.get("id") or "").strip()
        for item in metadata.get("speakers") or []
        if isinstance(item, Mapping) and str(item.get("name") or item.get("id") or "").strip()
    }
    return {"database_id": database_id, "episodes": filtered[offset:offset + limit], "speakers": sorted(metadata_speakers | {speaker for row in source_rows for speaker in row["speakers"]}), "total": len(filtered), "offset": offset, "limit": limit, "report": report, "policy_fingerprint": selected_policy.fingerprint, "source_available": not bool(source_error), "stored_available": bool(stored)}
