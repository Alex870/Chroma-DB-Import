"""Small, dependency-free records shared by semantic redundancy modules.

The records intentionally keep the semantic layer separate from Chroma,
embedding providers, and UI code.  They are immutable at the boundary and
offer JSON-friendly helpers for portable artifacts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


@dataclass(frozen=True, order=True)
class Scope:
    partition_id: str
    corpus_id: str
    base_release_id: str
    representation_id: str

    def __post_init__(self) -> None:
        if any(not str(value).strip() for value in (self.partition_id, self.corpus_id, self.base_release_id, self.representation_id)):
            raise ValueError("scope fields must be non-empty")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Scope":
        partition = value.get("partition") if isinstance(value.get("partition"), Mapping) else value
        return cls(
            str(partition.get("partition_id") or ""),
            str(partition.get("corpus_id") or ""),
            str(value.get("base_release_id") or value.get("release_id") or partition.get("release_id") or ""),
            str(value.get("representation_id") or partition.get("representation_id") or ""),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "partition_id": self.partition_id,
            "corpus_id": self.corpus_id,
            "base_release_id": self.base_release_id,
            "representation_id": self.representation_id,
        }


@dataclass(frozen=True)
class AnalysisUnit:
    document_id: str
    occurrence_ids: tuple[str, ...]
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    node_type: str = ""
    episode_uid: str = ""
    cache_fingerprint: str = ""
    span_ids: tuple[str, ...] = ()
    embedding_fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.document_id:
            raise ValueError("analysis unit document_id is required")
        object.__setattr__(self, "occurrence_ids", _tuple(self.occurrence_ids) or (self.document_id,))
        object.__setattr__(self, "span_ids", tuple(sorted(set(_tuple(self.span_ids)))))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AnalysisUnit":
        metadata = dict(value.get("metadata") or value.get("producer_metadata") or {})
        for key in ("source_group_ids", "duplicate_group_id", "exact_group_id", "alias_target_id", "representation_id"):
            if key in value and key not in metadata:
                metadata[key] = value[key]
        return cls(
            str(value.get("document_id") or value.get("id") or ""),
            _tuple(value.get("occurrence_ids") or value.get("member_ids") or value.get("document_id")),
            str(value.get("text") if value.get("text") is not None else value.get("page_content") or ""),
            metadata,
            str(value.get("node_type") or metadata.get("node_type") or ""),
            str(value.get("episode_uid") or metadata.get("episode_uid") or ""),
            str(value.get("cache_fingerprint") or value.get("verified_cache_fingerprint") or ""),
            _tuple(value.get("span_ids") or value.get("source_span_ids") or value.get("source_span_id")),
            str(value.get("embedding_fingerprint") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "occurrence_ids": list(self.occurrence_ids),
            "text": self.text,
            "metadata": dict(self.metadata),
            "node_type": self.node_type,
            "episode_uid": self.episode_uid,
            "cache_fingerprint": self.cache_fingerprint,
            "span_ids": list(self.span_ids),
            "embedding_fingerprint": self.embedding_fingerprint,
        }


@dataclass(frozen=True)
class CandidatePair:
    left_id: str
    right_id: str
    channels: tuple[str, ...] = ()
    scores: Mapping[str, float] = field(default_factory=dict)
    channel_ranks: Mapping[str, int] = field(default_factory=dict)
    guards: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.left_id or not self.right_id or self.left_id == self.right_id:
            raise ValueError("candidate pair requires two distinct IDs")
        if self.left_id > self.right_id:
            left, right = self.left_id, self.right_id
            object.__setattr__(self, "left_id", right)
            object.__setattr__(self, "right_id", left)
        object.__setattr__(self, "channels", tuple(sorted(set(str(item) for item in self.channels))))
        scores: dict[str, float] = {}
        for key, value in dict(self.scores).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError("candidate scores must be finite numbers")
            scores[str(key)] = float(value)
        ranks: dict[str, int] = {}
        for key, value in dict(self.channel_ranks).items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("candidate channel ranks must be positive integers")
            ranks[str(key)] = value
        object.__setattr__(self, "scores", scores)
        object.__setattr__(self, "channel_ranks", ranks)
        object.__setattr__(self, "guards", tuple(sorted(set(str(item) for item in self.guards))))

    @property
    def candidate_id(self) -> str:
        return f"{self.left_id}::{self.right_id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "left_id": self.left_id,
            "right_id": self.right_id,
            "channels": list(self.channels),
            "scores": dict(sorted(self.scores.items())),
            "channel_ranks": dict(sorted(self.channel_ranks.items())),
            "guards": list(self.guards),
        }


@dataclass(frozen=True)
class Coverage:
    unit_count: int = 0
    channel_available: Mapping[str, bool] = field(default_factory=dict)
    posting_truncations: Mapping[str, int] = field(default_factory=dict)
    capped_neighbors: Mapping[str, int] = field(default_factory=dict)
    skipped_reasons: Mapping[str, int] = field(default_factory=dict)
    posting_size_stats: Mapping[str, Mapping[str, int]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit_count": self.unit_count,
            "channel_available": dict(sorted(self.channel_available.items())),
            "posting_truncations": dict(sorted(self.posting_truncations.items())),
            "capped_neighbors": dict(sorted(self.capped_neighbors.items())),
            "skipped_reasons": dict(sorted(self.skipped_reasons.items())),
            "posting_size_stats": {str(channel): dict(sorted(stats.items())) for channel, stats in sorted(self.posting_size_stats.items())},
        }


@dataclass(frozen=True)
class Judgment:
    candidate_id: str
    relation: str
    matched_ids: tuple[str, ...] = ()
    evidence: tuple[Mapping[str, Any], ...] = ()
    novel_quotes: tuple[str, ...] = ()
    conflict_quotes: tuple[str, ...] = ()
    change_flags: Mapping[str, bool] = field(default_factory=dict)
    status: str = "retain_evidence"
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "relation": self.relation,
            "matched_ids": list(self.matched_ids),
            "evidence": [dict(item) for item in self.evidence],
            "novel_quotes": list(self.novel_quotes),
            "conflict_quotes": list(self.conflict_quotes),
            "attribution_changed": bool(self.change_flags.get("attribution_changed", False)),
            "time_changed": bool(self.change_flags.get("time_changed", False)),
            "qualification_changed": bool(self.change_flags.get("qualification_changed", False)),
            "distinct_occurrence": bool(self.change_flags.get("distinct_occurrence", False)),
            "reason": self.reason,
            "status": self.status,
        }


@dataclass(frozen=True)
class SelectionResult:
    selected_ids: tuple[str, ...]
    omitted: tuple[Mapping[str, Any], ...] = ()
    related_occurrence_ids: tuple[str, ...] = ()
    mode: str = "ranked"
    fallback_reason: str | None = None
    underfill_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_ids": list(self.selected_ids),
            "omitted": [dict(item) for item in self.omitted],
            "related_occurrence_ids": list(self.related_occurrence_ids),
            "mode": self.mode,
            "fallback_reason": self.fallback_reason,
            "underfill_reason": self.underfill_reason,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


__all__ = ["Scope", "AnalysisUnit", "CandidatePair", "Coverage", "Judgment", "SelectionResult"]
