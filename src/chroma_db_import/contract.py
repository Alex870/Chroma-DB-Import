from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .representation import (
    QWEN3_MODEL,
    QWEN3_MODEL_REVISION,
    QWEN3_PROFILE,
    QWEN3_QUERY_INSTRUCTION_PROFILE,
)

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
    temporal_capability: str = "legacy"
    temporal_coverage: dict[str, Any] = field(default_factory=dict)

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
            "temporal_capability": self.temporal_capability,
            "temporal_coverage": self.temporal_coverage or {},
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
        if key == "episode_sort_key" and str(value or "").strip().isdigit() and len(str(value).strip()) == 8:
            clean[key] = int(str(value).strip())
            continue
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


def _strict_episode_date(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return ""
    try:
        parsed = dt.date.fromisoformat(text)
    except ValueError:
        return ""
    return text if parsed.isoformat() == text else ""


def _temporal_speakers(metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    speaker = metadata.get("speaker")
    if isinstance(speaker, str) and speaker.strip() and speaker.strip().casefold() not in {"unknown", "multiple", "mixed", "unattributed"}:
        values.append(speaker.strip())
    raw = metadata.get("speakers")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [part.strip() for part in raw.split(",")]
    if isinstance(raw, list):
        for value in raw:
            if isinstance(value, str) and value.strip() and value.strip().casefold() not in {"unknown", "multiple", "mixed", "unattributed"} and value.strip() not in values:
                values.append(value.strip())
    return values


def temporal_record_eligibility(metadata: dict[str, Any], *, episode_uid: str = "", require_direct_speaker: bool = False) -> dict[str, Any]:
    reasons: list[str] = []
    episode_date = _strict_episode_date(metadata.get("episode_date"))
    if not episode_date:
        reasons.append("missing_or_invalid_episode_date")
    sort_key = str(metadata.get("episode_sort_key") or "").strip()
    if not sort_key.isdigit() or len(sort_key) != 8 or (episode_date and sort_key != episode_date.replace("-", "")):
        reasons.append("missing_or_invalid_episode_sort_key")
    if str(metadata.get("episode_date_source") or "").casefold() in {"filename", "filename_inferred", "inferred"}:
        reasons.append("date_is_filename_inferred")
    identity = str(metadata.get("episode_uid") or metadata.get("episode_id") or episode_uid or "").strip()
    if not identity:
        reasons.append("missing_episode_identity")
    node_type = str(metadata.get("node_type") or "").strip().casefold()
    if node_type not in {"leaf_chunk", "transcript_segment", "position_card", "episode_thesis", "cluster_summary", "topic_profile"}:
        reasons.append("missing_or_invalid_node_type")
    speakers = _temporal_speakers(metadata)
    scope = str(metadata.get("speaker_scope") or "").strip().casefold()
    if not speakers or not scope or scope in {"unknown", "unattributed"} or (scope in {"multiple", "mixed"} and not speakers):
        reasons.append("missing_or_ambiguous_speaker")
    direct_speaker = bool(speakers) and scope not in {"", "multiple", "mixed", "all", "unknown", "unattributed"}
    if require_direct_speaker and not direct_speaker:
        reasons.append("evidence_is_not_direct_speaker_attribution")
    provenance = metadata.get("source_span_id") or metadata.get("source_span_ids") or metadata.get("source_segment_id") or metadata.get("source_segment_ids") or metadata.get("source_spans") or metadata.get("primary_evidence_id") or metadata.get("primary_evidence_ids") or metadata.get("primary_evidence_path") or metadata.get("child_ids")
    if not provenance:
        reasons.append("missing_source_provenance")
    return {
        "eligible": not reasons, "reasons": reasons, "date": episode_date,
        "sort_key": sort_key, "episode_uid": identity, "episode_id": str(metadata.get("episode_id") or ""),
        "speakers": speakers, "direct_speaker": direct_speaker, "node_type": node_type,
    }


def temporal_coverage_stats(items: list[Any], *, episode_uid: str = "") -> dict[str, Any]:
    rows = []
    for item in items:
        if isinstance(item, dict) and "metadata" in item:
            rows.append(dict(item.get("metadata") or {}))
        elif isinstance(item, dict):
            rows.append(dict(item))
        else:
            rows.append(dict(getattr(item, "metadata", {}) or {}))
    reason_counts: Counter[str] = Counter()
    dates: list[str] = []
    speakers: Counter[str] = Counter()
    speaker_details: dict[str, dict[str, Any]] = {}
    periods: dict[str, dict[str, Any]] = {}
    episodes: dict[str, dict[str, Any]] = {}
    primary = derived = eligible = missing_speakers = dated = sort_keyed = 0
    missing_dates = invalid_dates = 0
    for metadata in rows:
        check = temporal_record_eligibility(metadata, episode_uid=episode_uid)
        if check["date"]:
            dates.append(check["date"])
            dated += 1
        raw_date = str(metadata.get("episode_date") or "").strip()
        if not raw_date:
            missing_dates += 1
        elif not check["date"]:
            invalid_dates += 1
        if check["sort_key"] and "missing_or_invalid_episode_sort_key" not in check["reasons"]:
            sort_keyed += 1
        if not check["eligible"]:
            reason_counts.update(check["reasons"])
        else:
            eligible += 1
            if check["node_type"] in {"leaf_chunk", "transcript_segment"}:
                primary += 1
            else:
                derived += 1
        if not check["speakers"]:
            missing_speakers += 1
        identity = check["episode_uid"]
        if identity:
            row = episodes.setdefault(identity, {"episode_uid": identity, "episode_id": check["episode_id"], "episode_date": check["date"], "episode_sort_key": check["sort_key"], "document_count": 0, "eligible_document_count": 0, "speakers": set(), "primary_evidence_count": 0, "derived_evidence_count": 0, "status": "ineligible"})
            row["document_count"] += 1
            row["speakers"].update(check["speakers"])
            if check["eligible"]:
                row["eligible_document_count"] += 1
                row["primary_evidence_count"] += int(check["node_type"] in {"leaf_chunk", "transcript_segment"})
                row["derived_evidence_count"] += int(check["node_type"] not in {"leaf_chunk", "transcript_segment"})
            row["status"] = "eligible" if row["eligible_document_count"] == row["document_count"] else "ineligible"
        speakers.update(check["speakers"])
        for speaker in check["speakers"]:
            detail = speaker_details.setdefault(
                speaker,
                {
                    "document_count": 0,
                    "eligible_document_count": 0,
                    "episode_ids": set(),
                    "eligible_episode_ids": set(),
                    "date_min": "",
                    "date_max": "",
                    "primary_evidence_count": 0,
                    "derived_evidence_count": 0,
                },
            )
            detail["document_count"] += 1
            if check["eligible"]:
                detail["eligible_document_count"] += 1
                if identity:
                    detail["eligible_episode_ids"].add(identity)
                if check["node_type"] in {"leaf_chunk", "transcript_segment"}:
                    detail["primary_evidence_count"] += 1
                else:
                    detail["derived_evidence_count"] += 1
            if identity:
                detail["episode_ids"].add(identity)
            if check["date"]:
                detail["date_min"] = min(detail["date_min"] or check["date"], check["date"])
                detail["date_max"] = max(detail["date_max"] or check["date"], check["date"])
        if check["date"]:
            period = periods.setdefault(
                check["date"][:7],
                {
                    "document_count": 0,
                    "eligible_document_count": 0,
                    "episode_ids": set(),
                    "eligible_episode_ids": set(),
                    "primary_evidence_count": 0,
                    "derived_evidence_count": 0,
                },
            )
            period["document_count"] += 1
            if check["eligible"]:
                period["eligible_document_count"] += 1
                if check["node_type"] in {"leaf_chunk", "transcript_segment"}:
                    period["primary_evidence_count"] += 1
                else:
                    period["derived_evidence_count"] += 1
            if identity:
                period["episode_ids"].add(identity)
    episode_rows = []
    for key in sorted(episodes, key=lambda value: (episodes[value]["episode_date"], value)):
        row = dict(episodes[key]); row["speakers"] = sorted(row["speakers"]); episode_rows.append(row)
    by_speaker = {
        name: {
            **detail,
            "episode_ids": sorted(detail["episode_ids"]),
            "eligible_episode_ids": sorted(detail["eligible_episode_ids"]),
            "episode_count": len(detail["episode_ids"]),
            "eligible_episode_count": len(detail["eligible_episode_ids"]),
        }
        for name, detail in sorted(speaker_details.items())
    }
    by_period = {
        name: {
            **detail,
            "episode_ids": sorted(detail["episode_ids"]),
            "eligible_episode_ids": sorted(detail["eligible_episode_ids"]),
            "episode_count": len(detail["episode_ids"]),
            "eligible_episode_count": len(detail["eligible_episode_ids"]),
        }
        for name, detail in sorted(periods.items())
    }
    total = len(rows)
    capability = "certified" if total and eligible == total else "partial" if rows else "legacy"
    return {
        "date_min": min(dates) if dates else "", "date_max": max(dates) if dates else "",
        "eligible_episode_count": sum(row["status"] == "eligible" for row in episode_rows),
        "eligible_document_count": eligible, "missing_date_count": missing_dates,
        "invalid_date_count": invalid_dates, "dated_record_count": dated,
        "sort_key_record_count": sort_keyed, "missing_sort_key_count": total - sort_keyed,
        "missing_speaker_count": missing_speakers, "primary_evidence_count": primary, "derived_evidence_count": derived,
        "record_count": total, "ineligible_temporal_record_count": total - eligible,
        "ineligible_reasons": dict(sorted(reason_counts.items())),
        "by_speaker": by_speaker,
        "speaker_coverage": by_speaker,
        "by_period": by_period,
        "episodes": episode_rows,
        "temporally_eligible_record_count": eligible,
        "temporal_capability": capability,
    }


def validate_document_items(
    items: list[Any],
    label: str = "processed cache",
    *,
    require_temporal: bool = False,
    episode_uid: str = "",
    legacy_contract: bool = False,
) -> ValidationReport:
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

    temporal = temporal_coverage_stats(normalized, episode_uid=episode_uid)
    if legacy_contract:
        temporal = dict(temporal)
        temporal["temporal_capability"] = "legacy"
        warnings.append("temporal metadata is from a legacy contract")
    if temporal["temporal_capability"] != "certified":
        warnings.append("temporal metadata is partial or legacy")
    if require_temporal and temporal["temporal_capability"] != "certified":
        errors.append(f"{label} is not temporally certified")
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
        temporal_capability=temporal["temporal_capability"],
        temporal_coverage=temporal,
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
    temporal_reports = [report.temporal_coverage or {} for report in reports]
    temporal_capability = "certified" if temporal_reports and all(item.get("temporal_capability") == "certified" for item in temporal_reports) else "partial" if any(item.get("temporal_capability") == "partial" for item in temporal_reports) else "legacy"
    temporal_episodes: dict[str, dict[str, Any]] = {}
    for coverage in temporal_reports:
        for episode in coverage.get("episodes") or []:
            key = str(episode.get("episode_uid") or episode.get("episode_id") or "")
            if not key:
                continue
            current = temporal_episodes.setdefault(key, {"episode_uid": key, "episode_id": episode.get("episode_id", ""), "episode_date": episode.get("episode_date", ""), "episode_sort_key": episode.get("episode_sort_key", ""), "document_count": 0, "eligible_document_count": 0, "primary_evidence_count": 0, "derived_evidence_count": 0, "speakers": set(), "status": "ineligible"})
            for field in ("document_count", "eligible_document_count", "primary_evidence_count", "derived_evidence_count"):
                current[field] += int(episode.get(field) or 0)
            current["speakers"].update(episode.get("speakers") or [])
            current["status"] = "eligible" if current["eligible_document_count"] == current["document_count"] else "ineligible"
    temporal = {
        "date_min": min((item.get("date_min") for item in temporal_reports if item.get("date_min")), default=""),
        "date_max": max((item.get("date_max") for item in temporal_reports if item.get("date_max")), default=""),
        "eligible_episode_count": sum(item.get("status") == "eligible" for item in temporal_episodes.values()),
        "eligible_document_count": sum(int(item.get("eligible_document_count") or 0) for item in temporal_reports),
        "missing_date_count": sum(int(item.get("missing_date_count") or 0) for item in temporal_reports),
        "invalid_date_count": sum(int(item.get("invalid_date_count") or 0) for item in temporal_reports),
        "missing_sort_key_count": sum(int(item.get("missing_sort_key_count") or 0) for item in temporal_reports),
        "dated_record_count": sum(int(item.get("dated_record_count") or 0) for item in temporal_reports),
        "sort_key_record_count": sum(int(item.get("sort_key_record_count") or 0) for item in temporal_reports),
        "missing_speaker_count": sum(int(item.get("missing_speaker_count") or 0) for item in temporal_reports),
        "primary_evidence_count": sum(int(item.get("primary_evidence_count") or 0) for item in temporal_reports),
        "derived_evidence_count": sum(int(item.get("derived_evidence_count") or 0) for item in temporal_reports),
        "record_count": sum(int(item.get("record_count") or 0) for item in temporal_reports),
        "ineligible_temporal_record_count": sum(int(item.get("ineligible_temporal_record_count") or 0) for item in temporal_reports),
        "ineligible_reasons": dict(sorted((reason, sum(int(item.get("ineligible_reasons", {}).get(reason) or 0) for item in temporal_reports)) for reason in {reason for item in temporal_reports for reason in item.get("ineligible_reasons", {})})),
        "by_speaker": {}, "by_period": {},
        "episodes": [{**item, "speakers": sorted(item["speakers"])} for item in sorted(temporal_episodes.values(), key=lambda row: (row["episode_date"], row["episode_uid"]))],
        "temporal_capability": temporal_capability,
    }
    speaker_rows: dict[str, dict[str, Any]] = {}
    period_rows: dict[str, dict[str, Any]] = {}
    for coverage in temporal_reports:
        for name, row in (coverage.get("by_speaker") or {}).items():
            target = speaker_rows.setdefault(name, {
                "document_count": 0,
                "eligible_document_count": 0,
                "episode_ids": set(),
                "eligible_episode_ids": set(),
                "date_min": "",
                "date_max": "",
                "primary_evidence_count": 0,
                "derived_evidence_count": 0,
            })
            for field_name in ("document_count", "eligible_document_count", "primary_evidence_count", "derived_evidence_count"):
                target[field_name] += int(row.get(field_name) or 0)
            target["episode_ids"].update(str(value) for value in row.get("episode_ids") or [])
            target["eligible_episode_ids"].update(str(value) for value in row.get("eligible_episode_ids") or [])
            for field_name in ("date_min", "date_max"):
                value = str(row.get(field_name) or "")
                if value:
                    target[field_name] = min(target[field_name] or value, value) if field_name == "date_min" else max(target[field_name] or value, value)
        for period, row in (coverage.get("by_period") or {}).items():
            target = period_rows.setdefault(period, {
                "document_count": 0,
                "eligible_document_count": 0,
                "episode_ids": set(),
                "primary_evidence_count": 0,
                "derived_evidence_count": 0,
            })
            for field_name in ("document_count", "eligible_document_count", "primary_evidence_count", "derived_evidence_count"):
                target[field_name] += int(row.get(field_name) or 0)
            target["episode_ids"].update(str(value) for value in row.get("episode_ids") or [])
    temporal["by_speaker"] = {
        name: {
            **row,
            "episode_ids": sorted(row["episode_ids"]),
            "eligible_episode_ids": sorted(row["eligible_episode_ids"]),
            "episode_count": len(row["episode_ids"]),
            "eligible_episode_count": len(row["eligible_episode_ids"]),
        }
        for name, row in sorted(speaker_rows.items())
    }
    temporal["by_period"] = {
        period: {
            **row,
            "episode_ids": sorted(row["episode_ids"]),
            "episode_count": len(row["episode_ids"]),
        }
        for period, row in sorted(period_rows.items())
    }
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
        "temporal_capability": temporal_capability,
        "temporal_coverage": temporal,
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
    temporal_capability: str | None = None,
    temporal_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if embedding_model != QWEN3_MODEL:
        raise ValueError(
            f"Qwen3-only exports require embedding_model={QWEN3_MODEL!r}; got {embedding_model!r}"
        )
    if embedding_dimension != 2560:
        raise ValueError(
            f"Qwen3-only exports require embedding_dimension=2560; got {embedding_dimension!r}"
        )
    if not str(collection_name).endswith("__qwen3-embedding-4b-shadow"):
        raise ValueError("Qwen3-only exports require a Qwen3-scoped collection name")
    representation_payload = dict(representation or {})
    required_representation = {
        "model_id": QWEN3_MODEL,
        "model_revision": QWEN3_MODEL_REVISION,
        "dimension": 2560,
        "provider": "sentence_transformers",
        "normalize_embeddings": True,
        "distance_metric": "cosine",
        "profile": QWEN3_PROFILE,
        "query_instruction_profile": QWEN3_QUERY_INSTRUCTION_PROFILE,
        "query_document_mode": "separate-query-instruction",
    }
    for field, expected in required_representation.items():
        if representation_payload.get(field) != expected:
            raise ValueError(
                f"Qwen3-only exports require representation.{field}={expected!r}; "
                f"got {representation_payload.get(field)!r}"
            )
    if not representation_payload.get("representation_id"):
        raise ValueError("Qwen3-only exports require representation.representation_id")
    summary = summarize_reports(validation_results)
    coverage = dict(temporal_coverage or summary.get("temporal_coverage") or {})
    capability = str(temporal_capability or summary.get("temporal_capability") or "legacy")
    coverage["temporal_capability"] = capability
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
        "representation": representation_payload,
        "representation_id": representation_payload.get("representation_id") or "",
        "operation": operation or {},
        "staging": staging or {},
        "reconciliation": reconciliation or {},
        "embedding_cache": embedding_cache or {},
        "temporal_capability": capability,
        "temporal_coverage": coverage,
        "temporal_coverage_sha256": hashlib.sha256(json.dumps(coverage, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest(),
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
    elif not str(payload.get("collection_name")).endswith("__qwen3-embedding-4b-shadow"):
        errors.append("podcast.json collection_name is not Qwen3-scoped")
    if not payload.get("embedding_model"):
        errors.append("podcast.json missing embedding_model")
    elif payload.get("embedding_model") != QWEN3_MODEL:
        errors.append(
            f"podcast.json declares unsupported embedding_model={payload.get('embedding_model')!r}; "
            f"only {QWEN3_MODEL!r} is supported"
        )
    if payload.get("embedding_dimension") in (None, ""):
        errors.append("podcast.json missing embedding_dimension")
    elif payload.get("embedding_dimension") != 2560:
        errors.append(
            f"podcast.json must declare embedding_dimension=2560; got {payload.get('embedding_dimension')!r}"
        )
    if not payload.get("representation_id"):
        errors.append("podcast.json missing representation_id")
    declared_revision = payload.get("embedding_model_revision") or payload.get("model_revision")
    if declared_revision not in (None, "", QWEN3_MODEL_REVISION):
        errors.append("podcast.json declares an unsupported Qwen3 model revision")
    representation = payload.get("representation")
    if isinstance(representation, dict):
        if representation.get("representation_id") != payload.get("representation_id"):
            errors.append("podcast.json representation_id does not match representation.representation_id")
        checks = {
            "profile": QWEN3_PROFILE,
            "model_id": QWEN3_MODEL,
            "model_revision": QWEN3_MODEL_REVISION,
            "dimension": 2560,
            "provider": "sentence_transformers",
            "normalize_embeddings": True,
            "distance_metric": "cosine",
            "query_instruction_profile": QWEN3_QUERY_INSTRUCTION_PROFILE,
            "query_document_mode": "separate-query-instruction",
        }
        for field, expected in checks.items():
            if representation.get(field) != expected:
                errors.append(f"podcast.json representation.{field} does not match Qwen3")
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
    temporal_capability = str(payload.get("temporal_capability") or "legacy")
    temporal_coverage = payload.get("temporal_coverage") if isinstance(payload.get("temporal_coverage"), dict) else {}
    if temporal_capability not in {"certified", "partial", "legacy"}:
        errors.append("podcast.json temporal_capability is invalid")
    if temporal_capability == "certified":
        for idx, episode in enumerate(episodes):
            episode_date = _strict_episode_date(episode.get("episode_date")) if isinstance(episode, dict) else ""
            episode_sort_key = str(episode.get("episode_sort_key") or "") if isinstance(episode, dict) else ""
            if not episode_date or episode_sort_key != episode_date.replace("-", ""):
                errors.append(f"episode {idx} lacks a certified date/sort-key pair")
            if not str((episode or {}).get("episode_uid") or "").strip():
                errors.append(f"episode {idx} lacks episode_uid for certified temporal coverage")
    declared_temporal_hash = str(payload.get("temporal_coverage_sha256") or "")
    if temporal_coverage and declared_temporal_hash:
        actual_temporal_hash = hashlib.sha256(json.dumps(temporal_coverage, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
        if declared_temporal_hash.removeprefix("sha256:") != actual_temporal_hash:
            errors.append("podcast.json temporal coverage checksum does not match")
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
        temporal_capability=temporal_capability,
        temporal_coverage=temporal_coverage,
    )
