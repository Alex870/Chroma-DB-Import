"""Selection rules for choosing RAG-produced transcript variants."""

from __future__ import annotations

import fnmatch
import json
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ASSET_FILTER_ALL = "all"
ASSET_FILTER_REVIEWED = "reviewed_speaker_transcript"
ASSET_FILTER_CLEANED = "cleaned_speaker_transcript"
ASSET_FILTER_RAW = "speaker_transcript"
ASSET_FILTER_CUSTOM = "custom"
DEFAULT_ASSET_FILTER = ASSET_FILTER_REVIEWED
ASSET_FILTER_CHOICES = (
    ASSET_FILTER_REVIEWED,
    ASSET_FILTER_CLEANED,
    ASSET_FILTER_RAW,
    ASSET_FILTER_ALL,
    ASSET_FILTER_CUSTOM,
)
ASSET_FILTER_LABELS = {
    ASSET_FILTER_REVIEWED: "Latest reviewed speaker transcripts",
    ASSET_FILTER_CLEANED: "Cleaned speaker transcripts",
    ASSET_FILTER_RAW: "Original speaker transcripts",
    ASSET_FILTER_ALL: "All processed assets",
    ASSET_FILTER_CUSTOM: "Custom filename pattern",
}


@dataclass(frozen=True)
class AssetSelection:
    """The result of applying a source-variant filter to discovered caches."""

    discovered_paths: tuple[Path, ...]
    selected_paths: tuple[Path, ...]
    excluded_paths: tuple[Path, ...]
    invalid_paths: tuple[Path, ...]

    @property
    def selected_count(self) -> int:
        return len(self.selected_paths)

    @property
    def discovered_count(self) -> int:
        return len(self.discovered_paths)


def normalize_asset_filter(value: Any) -> str:
    normalized = str(value or DEFAULT_ASSET_FILTER).strip().casefold()
    aliases = {
        "reviewed": ASSET_FILTER_REVIEWED,
        "reviewed_llm": ASSET_FILTER_REVIEWED,
        "cleaned": ASSET_FILTER_CLEANED,
        "raw": ASSET_FILTER_RAW,
        "original": ASSET_FILTER_RAW,
        "all_assets": ASSET_FILTER_ALL,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in ASSET_FILTER_CHOICES:
        raise ValueError(
            f"unknown asset filter {value!r}; expected one of {', '.join(ASSET_FILTER_CHOICES)}"
        )
    return normalized


def asset_filter_label(value: Any) -> str:
    return ASSET_FILTER_LABELS[normalize_asset_filter(value)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_name(value: Any) -> str:
    return _text(value).replace("\\", "/").casefold()


def _payload_candidates(path: Path, payload: dict[str, Any]) -> tuple[str, ...]:
    candidates: list[str] = [path.name, str(path)]
    for key in (
        "source_path",
        "source_file",
        "selected_transcript_relative_path",
    ):
        value = _text(payload.get(key))
        if value:
            candidates.extend((value, Path(value).name))
    documents = payload.get("documents")
    if isinstance(documents, list) and documents and isinstance(documents[0], dict):
        metadata = documents[0].get("metadata")
        if isinstance(metadata, dict):
            for key in ("source", "source_path", "source_file"):
                value = _text(metadata.get(key))
                if value:
                    candidates.extend((value, Path(value).name))
    return tuple(_normalized_name(value) for value in candidates if _text(value))


def _variants(payload: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("selected_variant", "review_status", "variant"):
        value = _text(payload.get(key)).casefold()
        if value:
            values.add(value)
    documents = payload.get("documents")
    if isinstance(documents, list) and documents and isinstance(documents[0], dict):
        metadata = documents[0].get("metadata")
        if isinstance(metadata, dict):
            for key in ("selected_variant", "review_status", "variant"):
                value = _text(metadata.get(key)).casefold()
                if value:
                    values.add(value)
    return values


def _episode_identity(path: Path, payload: dict[str, Any]) -> str:
    """Return the producer episode identity used to collapse repeated caches."""
    documents = payload.get("documents")
    metadata: dict[str, Any] = {}
    if isinstance(documents, list) and documents and isinstance(documents[0], dict):
        first_metadata = documents[0].get("metadata")
        if isinstance(first_metadata, dict):
            metadata = first_metadata

    for key in ("episode_id", "episode_key", "episode_uuid"):
        value = _text(payload.get(key) or metadata.get(key))
        if value:
            return _normalized_name(value)

    for key in ("selected_transcript_relative_path", "source_path", "source_file"):
        value = _text(payload.get(key) or metadata.get(key) or "")
        if not value:
            continue
        normalized = _normalized_name(value)
        parts = [part for part in normalized.split("/") if part]
        if len(parts) > 1 and parts[-1] in {"reviewed.json", "cleaned.json", "raw.json"}:
            return "/".join(parts[:-1])
        for suffix in (
            "_reviewed_speaker_transcript.json",
            "_cleaned_speaker_transcript.json",
            "_speaker_transcript.json",
        ):
            if normalized.endswith(suffix):
                return normalized[: -len(suffix)]
        return normalized

    for key in ("episode_title", "title"):
        value = _text(payload.get(key) or metadata.get(key))
        if value:
            return _normalized_name(value)
    return ""


def _timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value)
    if not text:
        return 0.0
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def _recency_key(path: Path, payload: dict[str, Any]) -> tuple[float, int]:
    for key in ("updated_at", "created_at", "processed_at", "generated_at"):
        timestamp = _timestamp(payload.get(key))
        if timestamp:
            try:
                return (timestamp, path.stat().st_mtime_ns)
            except OSError:
                return (timestamp, 0)
    try:
        return (0.0, path.stat().st_mtime_ns)
    except OSError:
        return (0.0, 0)


def _has_suffix(candidates: Iterable[str], suffix: str) -> bool:
    normalized_suffix = suffix.casefold()
    return any(candidate.endswith(normalized_suffix) for candidate in candidates)


def _matches_custom(candidates: Iterable[str], pattern: str) -> bool:
    normalized_pattern = _normalized_name(pattern)
    if not normalized_pattern:
        return False
    return any(
        fnmatch.fnmatch(candidate, normalized_pattern)
        or fnmatch.fnmatch(Path(candidate).name, normalized_pattern)
        for candidate in candidates
    )


def matches_asset_filter(
    path: Path,
    payload: dict[str, Any],
    asset_filter: str = DEFAULT_ASSET_FILTER,
    asset_pattern: str = "",
) -> bool:
    selected_filter = normalize_asset_filter(asset_filter)
    if selected_filter == ASSET_FILTER_ALL:
        return True

    candidates = _payload_candidates(path, payload)
    variants = _variants(payload)
    if selected_filter == ASSET_FILTER_CUSTOM:
        return _matches_custom(candidates, asset_pattern)

    reviewed = (
        "reviewed_llm" in variants
        or "reviewed" in variants
        or "reviewed_speaker_transcript" in variants
        or _has_suffix(candidates, "_reviewed_speaker_transcript.json")
    )
    cleaned = (
        "cleaned" in variants
        or "cleaned_speaker_transcript" in variants
        or _has_suffix(candidates, "_cleaned_speaker_transcript.json")
    )
    if selected_filter == ASSET_FILTER_REVIEWED:
        return reviewed
    if selected_filter == ASSET_FILTER_CLEANED:
        return cleaned and not reviewed

    # The broad speaker-transcript suffix also matches reviewed and cleaned
    # filenames, so classify those more specific variants first.
    raw = (
        "raw" in variants
        or "original" in variants
        or _has_suffix(candidates, "_speaker_transcript.json")
    )
    return raw and not reviewed and not cleaned


def select_asset_files(
    paths: Iterable[Path],
    asset_filter: str = DEFAULT_ASSET_FILTER,
    asset_pattern: str = "",
) -> AssetSelection:
    discovered = tuple(sorted(Path(path) for path in paths))
    selected_filter = normalize_asset_filter(asset_filter)
    if selected_filter == ASSET_FILTER_ALL:
        return AssetSelection(discovered, discovered, (), ())

    selected: list[Path] = []
    excluded: list[Path] = []
    invalid: list[Path] = []
    matched_records: list[tuple[Path, dict[str, Any], str]] = []
    for path in discovered:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("cache payload must be a JSON object")
            matches = matches_asset_filter(path, payload, selected_filter, asset_pattern)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            invalid.append(path)
            # A custom pattern can still intentionally select a cache by its
            # own filename even when its payload is malformed; normal import
            # validation will report the malformed file afterward.
            matches = selected_filter == ASSET_FILTER_CUSTOM and _matches_custom(
                (_normalized_name(path.name),), asset_pattern
            )
        if matches:
            selected.append(path)
            if selected_filter == ASSET_FILTER_REVIEWED:
                matched_records.append((path, payload, _episode_identity(path, payload)))
        else:
            excluded.append(path)

    if selected_filter == ASSET_FILTER_REVIEWED:
        # A producer rerun can emit several reviewed caches for the same
        # episode. Keep the newest one in the default reviewed view so the UI
        # presents one tree entry per episode.
        newest_by_episode: dict[str, Path] = {}
        newest_keys: dict[str, tuple[float, int]] = {}
        for path, payload, identity in matched_records:
            if not identity:
                continue
            recency = _recency_key(path, payload)
            if identity not in newest_keys or recency > newest_keys[identity]:
                newest_by_episode[identity] = path
                newest_keys[identity] = recency
        deduplicated: list[Path] = []
        identity_by_path = {path: identity for path, _payload, identity in matched_records}
        for path in selected:
            identity = identity_by_path.get(path, "")
            if identity and newest_by_episode.get(identity) != path:
                excluded.append(path)
            else:
                deduplicated.append(path)
        selected = deduplicated

    return AssetSelection(
        tuple(discovered),
        tuple(selected),
        tuple(sorted(excluded)),
        tuple(invalid),
    )
