from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


IMPORTER_VERSION = "0.3.0"
IMPORT_MANIFEST_VERSION = "2.0"
PODCAST_JSON_SCHEMA_VERSION = "2.0"
REQUIRED_NODE_TYPES = {"leaf_chunk", "episode_thesis"}
SUMMARY_NODE_TYPES = {"cluster_summary", "episode_thesis", "position_card"}
REQUIRED_METADATA_FIELDS = {
    "node_id",
    "node_type",
    "source",
    "episode_id",
    "episode_title",
    "source_type",
    "speaker_scope",
}


@dataclass
class ValidationReport:
    """Normalized validation result shared by the CLI, UI, and downstream tools."""

    valid: bool
    errors: list[str]
    warnings: list[str]
    document_count: int
    counts_by_node_type: dict[str, int]
    counts_by_speaker: dict[str, int]
    counts_by_episode: dict[str, int]
    date_min: str | None
    date_max: str | None
    duplicate_node_ids: list[str]
    malformed_position_cards: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "document_count": self.document_count,
            "counts_by_node_type": self.counts_by_node_type,
            "counts_by_speaker": self.counts_by_speaker,
            "counts_by_episode": self.counts_by_episode,
            "date_min": self.date_min,
            "date_max": self.date_max,
            "duplicate_node_ids": self.duplicate_node_ids,
            "malformed_position_cards": self.malformed_position_cards,
        }

    def raise_for_errors(self, label: str) -> None:
        if self.errors:
            preview = "; ".join(self.errors[:10])
            if len(self.errors) > 10:
                preview += f"; and {len(self.errors) - 10} more"
            raise ValueError(f"{label} contains invalid processed documents: {preview}")


def stable_hash_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def file_fingerprint(path: Path) -> str:
    stat = path.stat()
    hasher = hashlib.sha1()
    hasher.update(str(path.resolve()).encode("utf-8"))
    hasher.update(str(stat.st_size).encode("ascii"))
    hasher.update(str(stat.st_mtime_ns).encode("ascii"))
    return hasher.hexdigest()


def content_fingerprint(path: Path) -> str:
    hasher = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def has_text(text: str) -> bool:
    return bool(re.sub(r"\s+", " ", text or "").strip())


PARTITION_IDENTITY_FIELDS = (
    "partition_id",
    "corpus_id",
    "partition_display_name",
    "context_type",
    "workflow_profile",
    "partition_config_fingerprint",
)


def _partition_identity_from_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    nested = value.get("partition") if isinstance(value.get("partition"), dict) else {}
    identity: dict[str, str] = {}
    for field in PARTITION_IDENTITY_FIELDS:
        candidate = value.get(field)
        if candidate in (None, ""):
            candidate = nested.get(field)
        if candidate not in (None, ""):
            identity[field] = str(candidate)
    return identity


def partition_identities(payload: Any) -> list[dict[str, str]]:
    """Extract partition identities from a processed cache or exported payload.

    Partition metadata was added additively, so old caches simply return an empty
    list.  The document scan catches malformed caches that contain more than one
    identity even when their top-level metadata is incomplete.
    """
    if not isinstance(payload, dict):
        return []
    candidates: list[dict[str, str]] = []
    for mapping in (payload, payload.get("metadata")):
        identity = _partition_identity_from_mapping(mapping)
        if identity:
            candidates.append(identity)
    documents = payload.get("documents")
    if isinstance(documents, list):
        for item in documents:
            if not isinstance(item, dict):
                continue
            for mapping in (item.get("metadata"), item):
                identity = _partition_identity_from_mapping(mapping)
                if identity:
                    candidates.append(identity)

    unique: dict[str, dict[str, str]] = {}
    for identity in candidates:
        key = json.dumps(
            {
                "partition_id": identity.get("partition_id", ""),
                "corpus_id": identity.get("corpus_id", ""),
            },
            sort_keys=True,
        )
        unique.setdefault(key, identity)
    return list(unique.values())


def partition_identity(payload: Any) -> dict[str, str]:
    """Return the single partition identity represented by a payload, if any."""
    identities = partition_identities(payload)
    if not identities:
        return {}
    return dict(identities[0])


def is_missing_context_response(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text or "").strip().lower()
    if not compact:
        return False
    asks_for_missing_input = (
        ("please provide" in compact or "please share" in compact or "send me" in compact)
        and any(term in compact for term in ["transcript", "source text", "source material", "podcast text", "material"])
    )
    deferred_until_shared = any(pattern in compact for pattern in ["once shared", "once you share", "when you provide"])
    return asks_for_missing_input or deferred_until_shared


def coerce_speakers(metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    speaker = metadata.get("speaker")
    if isinstance(speaker, str) and speaker.strip() and speaker.lower() not in {"unknown", "multiple", "mixed"}:
        values.append(speaker.strip())
    speakers = metadata.get("speakers")
    if isinstance(speakers, str):
        try:
            speakers = json.loads(speakers)
        except json.JSONDecodeError:
            speakers = [part.strip() for part in speakers.split(",")]
    if isinstance(speakers, list):
        for item in speakers:
            if isinstance(item, str) and item.strip() and item.strip() not in values:
                values.append(item.strip())
    return values


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    clean = {}
    for key, value in metadata.items():
        if value is None:
            clean[key] = ""
        elif isinstance(value, (str, int, float, bool)):
            clean[key] = value
        else:
            clean[key] = json.dumps(value, ensure_ascii=True)
    return clean


def document_to_item(doc: Any) -> dict[str, Any]:
    return {
        "page_content": str(getattr(doc, "page_content", "") or ""),
        "metadata": dict(getattr(doc, "metadata", {}) or {}),
    }


def validate_document_items(items: list[Any], label: str = "processed cache") -> ValidationReport:
    """Validate processed-document payloads against the shared import contract."""
    errors: list[str] = []
    warnings: list[str] = []
    node_ids: Counter[str] = Counter()
    node_types: Counter[str] = Counter()
    speakers: Counter[str] = Counter()
    episodes: Counter[str] = Counter()
    dates: list[str] = []
    child_refs: list[tuple[str, str]] = []
    malformed_position_cards = 0

    normalized = []
    for item in items:
        if isinstance(item, dict) and "metadata" in item:
            normalized.append({"page_content": str(item.get("page_content", "") or ""), "metadata": dict(item.get("metadata") or {})})
        else:
            normalized.append(document_to_item(item))

    for index, item in enumerate(normalized):
        text = item["page_content"]
        metadata = item["metadata"]
        node_id = str(metadata.get("node_id") or f"index_{index}")
        node_type = str(metadata.get("node_type") or "unknown")
        label_text = f"{node_id} ({node_type})"
        node_ids[node_id] += 1
        node_types[node_type] += 1
        episodes[str(metadata.get("episode_id") or "unknown")] += 1
        if metadata.get("episode_date"):
            dates.append(str(metadata["episode_date"]))
        for speaker in coerce_speakers(metadata) or ["unknown"]:
            speakers[speaker] += 1

        if not has_text(text):
            errors.append(f"{label_text} has empty page_content")
        elif node_type in SUMMARY_NODE_TYPES and is_missing_context_response(text):
            errors.append(f"{label_text} has a missing-context response")
        for field in REQUIRED_METADATA_FIELDS:
            if metadata.get(field) in (None, ""):
                errors.append(f"{label_text} is missing metadata.{field}")
        if not metadata.get("episode_date"):
            warnings.append(f"{label_text} is missing episode_date")
        for child_id in metadata.get("child_ids") or []:
            if isinstance(child_id, str) and child_id:
                child_refs.append((node_id, child_id))
        if node_type == "position_card":
            position_errors = []
            if metadata.get("speaker_scope") != "single" or not coerce_speakers(metadata):
                position_errors.append("speaker is not attributable")
            if not metadata.get("claim"):
                position_errors.append("claim is missing")
            if not metadata.get("child_ids"):
                position_errors.append("evidence child_ids are missing")
            if position_errors:
                malformed_position_cards += 1
                errors.append(f"{label_text} malformed position_card: {', '.join(position_errors)}")

    duplicate_node_ids = sorted(node_id for node_id, count in node_ids.items() if count > 1)
    for node_id in duplicate_node_ids:
        errors.append(f"duplicate node_id {node_id}")
    for required_type in sorted(REQUIRED_NODE_TYPES.difference(node_types)):
        errors.append(f"{label} is missing required node_type={required_type}")
    known_ids = set(node_ids)
    for parent_id, child_id in child_refs:
        if child_id not in known_ids:
            errors.append(f"{parent_id} references missing child_id {child_id}")

    return ValidationReport(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        document_count=len(normalized),
        counts_by_node_type=dict(node_types),
        counts_by_speaker=dict(speakers),
        counts_by_episode=dict(episodes),
        date_min=min(dates) if dates else None,
        date_max=max(dates) if dates else None,
        duplicate_node_ids=duplicate_node_ids,
        malformed_position_cards=malformed_position_cards,
    )


def summarize_reports(reports: list[ValidationReport]) -> dict[str, Any]:
    node_types: Counter[str] = Counter()
    speakers: Counter[str] = Counter()
    episodes: Counter[str] = Counter()
    dates: list[str] = []
    errors = 0
    warnings = 0
    malformed_positions = 0
    documents = 0
    for report in reports:
        node_types.update(report.counts_by_node_type)
        speakers.update(report.counts_by_speaker)
        episodes.update(report.counts_by_episode)
        if report.date_min:
            dates.append(report.date_min)
        if report.date_max:
            dates.append(report.date_max)
        errors += len(report.errors)
        warnings += len(report.warnings)
        malformed_positions += report.malformed_position_cards
        documents += report.document_count
    return {
        "document_count": documents,
        "counts_by_node_type": dict(node_types),
        "counts_by_speaker": dict(speakers),
        "counts_by_episode": dict(episodes),
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "error_count": errors,
        "warning_count": warnings,
        "malformed_position_cards": malformed_positions,
    }


def build_import_manifest(
    *,
    config: dict[str, Any],
    source_files: list[dict[str, Any]],
    validation_results: list[ValidationReport],
    embedding_model: str,
    embedding_dimension: int | None,
    collection_name: str,
    selected_speakers: list[str] | None = None,
    compatibility_warnings: list[str] | None = None,
    representation: dict[str, Any] | None = None,
    operation: dict[str, Any] | None = None,
    staging: dict[str, Any] | None = None,
    reconciliation: dict[str, Any] | None = None,
    embedding_cache: dict[str, Any] | None = None,
    partition_identity: dict[str, Any] | None = None,
    dedup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = summarize_reports(validation_results)
    manifest = {
        "manifest_version": IMPORT_MANIFEST_VERSION,
        "importer_version": IMPORTER_VERSION,
        "imported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config": config,
        "collection_name": collection_name,
        "embedding_model": embedding_model,
        "embedding_dimension": embedding_dimension,
        "selected_speakers": selected_speakers or [],
        "source_files": source_files,
        "document_counts": summary,
        "validation": {
            "valid": all(report.valid for report in validation_results),
            "files": [report.as_dict() for report in validation_results],
        },
        "compatibility_warnings": compatibility_warnings or [],
        "representation": representation or {},
        "representation_id": (representation or {}).get("representation_id") or "",
        "operation": operation or {},
        "staging": staging or {},
        "reconciliation": reconciliation or {},
        "embedding_cache": embedding_cache or {},
    }
    if dedup is not None:
        manifest["dedup"] = dict(dedup)
    if partition_identity:
        manifest["partition"] = dict(partition_identity)
        if partition_identity.get("partition_id"):
            manifest["partition_id"] = str(partition_identity["partition_id"])
        corpus_id = partition_identity.get("corpus_id") or partition_identity.get("partition_id")
        if corpus_id:
            manifest["corpus_id"] = str(corpus_id)
    return manifest


def validate_podcast_metadata(payload: dict[str, Any]) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    episodes = payload.get("episodes")
    speakers = payload.get("speakers")
    if not payload.get("database_id"):
        errors.append("podcast.json missing database_id")
    if not payload.get("collection_name"):
        errors.append("podcast.json missing collection_name")
    if not payload.get("embedding_model"):
        errors.append("podcast.json missing embedding_model")
    if payload.get("embedding_dimension") in (None, ""):
        warnings.append("podcast.json missing embedding_dimension")
    if not isinstance(episodes, list):
        errors.append("podcast.json episodes must be a list")
        episodes = []
    if not isinstance(speakers, list):
        errors.append("podcast.json speakers must be a list")
        speakers = []
    speaker_names = {speaker.get("name") for speaker in speakers if isinstance(speaker, dict)}
    dates = []
    for idx, episode in enumerate(episodes):
        if not isinstance(episode, dict):
            errors.append(f"episode {idx} is not an object")
            continue
        if not episode.get("source_fingerprint"):
            errors.append(f"episode {idx} missing source_fingerprint")
        if episode.get("episode_date"):
            dates.append(str(episode["episode_date"]))
        for speaker in episode.get("speakers") or []:
            name = speaker.get("name") if isinstance(speaker, dict) else None
            if name and name not in speaker_names:
                warnings.append(f"episode {idx} speaker {name} missing from top-level speakers")
    return ValidationReport(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        document_count=int(payload.get("document_count") or 0),
        counts_by_node_type={},
        counts_by_speaker={name: 1 for name in speaker_names if name},
        counts_by_episode={str(item.get("source_fingerprint") or idx): 1 for idx, item in enumerate(episodes) if isinstance(item, dict)},
        date_min=min(dates) if dates else None,
        date_max=max(dates) if dates else None,
        duplicate_node_ids=[],
        malformed_position_cards=0,
    )
