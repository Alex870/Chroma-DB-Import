"""Contract-driven exact duplicate planning and bounded near-match reporting.

This module deliberately has no Chroma or embedding-provider dependency.  It is
the release planner's deterministic boundary: raw producer rows are validated,
then a complete eligible snapshot is planned before any model is constructed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .contract import content_fingerprint, has_text
from .lexical_index import tokenize
from .representation import RepresentationSpec, embedding_text


DEDUP_PROFILE_VERSION = "dedup-v1"
DEDUP_KEY_VERSION = "dedup-key-v1"
NEAR_ALGORITHM_VERSION = "lexical-near-v1"
DEDUP_ARTIFACT_CONTRACT = "chroma-export-dedup-v1"
DEDUP_CAPABILITY = "dedup-aliases-v1"
DEDUP_RESERVED_FIELDS = {
    "dedup_key_version",
    "normalized_text_hash",
    "source_span_hash",
    "duplicate_group_id",
    "duplicate_status",
    "duplicate_method",
    "duplicate_of",
    "dedup_policy_version",
}


class DeduplicationError(ValueError):
    """Raised when a deduplication policy or input inventory is unsafe."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_digest(value: Any) -> str:
    """Return the portable, explicit digest form used by dedup artifacts."""
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = _canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def normalize_text_v1(value: Any) -> str:
    """Normalize text without changing meaning-bearing characters."""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = re.sub(r"\s+", " ", text, flags=re.UNICODE)
    return text.strip()


def normalize_span_ids(metadata: Mapping[str, Any]) -> tuple[tuple[str, ...], str | None]:
    """Return sorted span IDs and a stable reason when the span is unusable.

    Numeric IDs are intentionally rejected.  A JSON-encoded list is accepted
    only as a defensive adapter for older producers; it is not normalized into
    a different identity scheme.
    """
    values: list[tuple[str, Any]] = []
    if "source_span_id" in metadata:
        values.append(("source_span_id", metadata.get("source_span_id")))
    if "source_span_ids" in metadata:
        values.append(("source_span_ids", metadata.get("source_span_ids")))
    if not values:
        return (), "missing_span"

    sets: list[set[str]] = []
    for name, raw in values:
        if name == "source_span_id":
            if not isinstance(raw, str) or not raw.strip():
                return (), "invalid_span"
            sets.append({raw.strip()})
            continue
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return (), "invalid_span"
        if not isinstance(raw, list) or not raw:
            return (), "invalid_span"
        if any(not isinstance(item, str) or not item.strip() for item in raw):
            return (), "invalid_span"
        sets.append({item.strip() for item in raw})
    if len(sets) == 2 and sets[0] != sets[1]:
        return (), "span_conflict"
    normalized = tuple(sorted(sets[0]))
    return normalized, None if normalized else "invalid_span"


def _validate_number(value: Any, name: str, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DeduplicationError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise DeduplicationError(f"{name} must be between {minimum} and {maximum}")
    return result


def _validate_int(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeduplicationError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise DeduplicationError(f"{name} must be between {minimum} and {maximum}")
    return value


def resolve_dedup_policy(value: Mapping[str, Any] | None = None, *, default_profile: str = "off") -> dict[str, Any]:
    """Resolve and validate the complete v1 policy.

    The returned mapping is complete and JSON-safe.  Version, algorithm,
    minimum-token, oversampling, and candidate-cap settings are implementation
    controlled in v1; callers may only select the profile and the documented
    operational toggles/thresholds.
    """
    if value is not None and not isinstance(value, Mapping):
        raise DeduplicationError("dedup policy must be an object")
    raw = dict(value or {})
    allowed = {
        "profile", "policy_version", "key_version", "near_algorithm_version",
        "near_enabled", "near_jaccard_threshold", "near_length_ratio",
        "near_min_tokens", "near_max_block_records", "retrieval",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise DeduplicationError("unknown dedup policy field(s): " + ", ".join(unknown))
    profile = str(raw.get("profile", default_profile)).strip().lower()
    if profile not in {"off", "safe", "audit"}:
        raise DeduplicationError("dedup profile must be off, safe, or audit")

    policy_version = raw.get("policy_version", DEDUP_PROFILE_VERSION)
    key_version = raw.get("key_version", DEDUP_KEY_VERSION)
    near_algorithm_version = raw.get("near_algorithm_version", NEAR_ALGORITHM_VERSION)
    if policy_version != DEDUP_PROFILE_VERSION:
        raise DeduplicationError("unsupported dedup policy_version")
    if key_version != DEDUP_KEY_VERSION:
        raise DeduplicationError("unsupported dedup key_version")
    if near_algorithm_version != NEAR_ALGORITHM_VERSION:
        raise DeduplicationError("unsupported near_algorithm_version")

    near_enabled = raw.get("near_enabled", True)
    if not isinstance(near_enabled, bool):
        raise DeduplicationError("near_enabled must be boolean")
    near_jaccard = _validate_number(raw.get("near_jaccard_threshold", 0.90), "near_jaccard_threshold")
    near_length = _validate_number(raw.get("near_length_ratio", 0.90), "near_length_ratio")
    near_min_tokens = _validate_int(raw.get("near_min_tokens", 20), "near_min_tokens", minimum=1, maximum=2000)
    if near_min_tokens != 20:
        raise DeduplicationError("v1 near_min_tokens is implementation controlled at 20")
    near_max_block = _validate_int(raw.get("near_max_block_records", 2000), "near_max_block_records", minimum=1, maximum=2000)

    retrieval_raw = raw.get("retrieval", {})
    if not isinstance(retrieval_raw, Mapping):
        raise DeduplicationError("retrieval policy must be an object")
    unknown_retrieval = sorted(set(retrieval_raw) - {"enabled", "oversample_factor", "candidate_cap"})
    if unknown_retrieval:
        raise DeduplicationError("unknown retrieval policy field(s): " + ", ".join(unknown_retrieval))
    retrieval_enabled = retrieval_raw.get("enabled", True)
    if not isinstance(retrieval_enabled, bool):
        raise DeduplicationError("retrieval.enabled must be boolean")
    oversample = _validate_int(retrieval_raw.get("oversample_factor", 3), "retrieval.oversample_factor", minimum=1, maximum=3)
    candidate_cap = _validate_int(retrieval_raw.get("candidate_cap", 200), "retrieval.candidate_cap", minimum=1, maximum=200)
    if oversample != 3 or candidate_cap != 200:
        raise DeduplicationError("v1 retrieval oversample_factor=3 and candidate_cap=200 are implementation controlled")

    if profile == "off":
        near_enabled = False
        retrieval_enabled = False
    return {
        "profile": profile,
        "policy_version": DEDUP_PROFILE_VERSION,
        "key_version": DEDUP_KEY_VERSION,
        "near_algorithm_version": NEAR_ALGORITHM_VERSION,
        "near_enabled": near_enabled,
        "near_jaccard_threshold": near_jaccard,
        "near_length_ratio": near_length,
        "near_min_tokens": near_min_tokens,
        "near_max_block_records": near_max_block,
        "retrieval": {
            "enabled": retrieval_enabled,
            "oversample_factor": 3,
            "candidate_cap": 200,
        },
    }


def partition_dedup_policy_fingerprint(policy: Mapping[str, Any]) -> str:
    return sha256_digest(resolve_dedup_policy(policy))


def validate_reserved_metadata(metadata: Mapping[str, Any], *, enabled: bool) -> None:
    if not enabled:
        return
    collisions = sorted(
        key for key in metadata
        if key in DEDUP_RESERVED_FIELDS or str(key).startswith("dedup_")
    )
    if collisions:
        raise DeduplicationError("producer metadata collides with dedup output fields: " + ", ".join(collisions))


@dataclass(frozen=True)
class DedupInput:
    effective_id: str
    node_id: str
    page_content: str
    producer_metadata: Mapping[str, Any]
    partition_id: str
    corpus_id: str
    episode_uid: str
    verified_cache_fingerprint: str
    embedding_input: str = ""
    node_type: str = ""
    source_span_ids: tuple[str, ...] = ()
    source_span_reason: str | None = None
    cache_locator: str | None = field(default=None, repr=False, compare=False)

    @property
    def metadata(self) -> Mapping[str, Any]:
        return self.producer_metadata

    @property
    def document_id(self) -> str:
        return self.effective_id

    @property
    def original_node_id(self) -> str:
        return self.node_id

    @property
    def original_metadata(self) -> Mapping[str, Any]:
        return self.producer_metadata

    @property
    def normalized_text(self) -> str:
        return normalize_text_v1(self.page_content)

    @property
    def normalized_text_hash(self) -> str:
        return sha256_digest(self.normalized_text)

    @property
    def source_span_hash(self) -> str | None:
        if self.source_span_reason or not self.source_span_ids or any(not isinstance(value, str) or not value.strip() for value in self.source_span_ids) or tuple(sorted(set(self.source_span_ids))) != tuple(self.source_span_ids):
            return None
        return sha256_digest([
            self.partition_id,
            self.corpus_id,
            self.episode_uid,
            self.verified_cache_fingerprint,
            list(self.source_span_ids),
        ])

    @property
    def producer_metadata_hash(self) -> str:
        return sha256_digest(_producer_metadata_key(self.producer_metadata))

    @property
    def embedding_input_hash(self) -> str:
        return sha256_digest(self.embedding_input)


@dataclass
class DedupDecision:
    effective_id: str
    storage_status: str = "retained"
    duplicate_group_id: str | None = None
    preferred_canonical_id: str = ""
    alias_target_id: str | None = None
    method: str = "none"
    reason: str = "unique"

    @property
    def suppressed(self) -> bool:
        return self.storage_status == "suppressed_exact"

    @property
    def document_id(self) -> str:
        return self.effective_id


@dataclass(frozen=True)
class ExactGroup:
    duplicate_group_id: str
    canonical_id: str
    member_ids: tuple[str, ...]
    method: str = "exact_provenance_v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "duplicate_group_id": self.duplicate_group_id,
            "canonical_id": self.canonical_id,
            "member_ids": list(self.member_ids),
            "method": self.method,
        }


@dataclass(frozen=True)
class NearEdge:
    left_id: str
    right_id: str
    similarity: float
    length_ratio: float
    partition_id: str
    corpus_id: str
    node_type: str
    same_episode: bool
    method: str = "lexical_shingle_jaccard_v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "left_id": self.left_id,
            "right_id": self.right_id,
            "method": self.method,
            "similarity": self.similarity,
            "length_ratio": self.length_ratio,
            "partition_id": self.partition_id,
            "corpus_id": self.corpus_id,
            "node_type": self.node_type,
            "same_episode": self.same_episode,
        }


@dataclass
class DedupPlan:
    policy: dict[str, Any]
    decisions: dict[str, DedupDecision]
    inputs: dict[str, DedupInput]
    exact_groups: list[ExactGroup] = field(default_factory=list)
    near_edges: list[NearEdge] = field(default_factory=list)
    near_report: dict[str, Any] = field(default_factory=dict)
    cache_ids: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False)
    excluded_count: int = 0
    excluded_reasons: dict[str, int] = field(default_factory=dict)
    input_count: int = 0
    representation_id: str = field(default="", repr=False)

    @property
    def eligible_count(self) -> int:
        return len(self.inputs)

    @property
    def stored_ids(self) -> list[str]:
        return sorted(item_id for item_id, decision in self.decisions.items() if not decision.suppressed)

    @property
    def stored_count(self) -> int:
        return len(self.stored_ids)

    @property
    def suppressed_exact_count(self) -> int:
        return sum(decision.suppressed for decision in self.decisions.values())

    @property
    def would_suppress_exact(self) -> int:
        return sum(max(0, len(group.member_ids) - 1) for group in self.exact_groups)

    @property
    def plan_fingerprint(self) -> str:
        payload = {
            "policy": self.policy,
            "decisions": [
                {
                    "id": item_id,
                    "status": self.decisions[item_id].storage_status,
                    "group": self.decisions[item_id].duplicate_group_id,
                    "canonical": self.decisions[item_id].preferred_canonical_id,
                    "alias": self.decisions[item_id].alias_target_id,
                    "method": self.decisions[item_id].method,
                    "reason": self.decisions[item_id].reason,
                    "partition_id": self.inputs[item_id].partition_id,
                    "corpus_id": self.inputs[item_id].corpus_id,
                    "episode_uid": self.inputs[item_id].episode_uid,
                    "verified_cache_fingerprint": self.inputs[item_id].verified_cache_fingerprint,
                    "normalized_text_hash": self.inputs[item_id].normalized_text_hash,
                    "source_span_hash": self.inputs[item_id].source_span_hash,
                    "producer_metadata_hash": self.inputs[item_id].producer_metadata_hash,
                    "embedding_input_hash": self.inputs[item_id].embedding_input_hash,
                }
                for item_id in sorted(self.decisions)
            ],
            "groups": [group.as_dict() for group in sorted(self.exact_groups, key=lambda item: item.duplicate_group_id)],
            "near_edges": [edge.as_dict() for edge in sorted(self.near_edges, key=lambda item: (item.left_id, item.right_id))],
            "coverage": {
                "input": self.input_count,
                "excluded": self.excluded_count,
                "eligible": self.eligible_count,
                "stored": self.stored_count,
                "suppressed_exact": self.suppressed_exact_count,
                "would_suppress_exact": self.would_suppress_exact,
            },
            "near_report": {key: value for key, value in sorted(self.near_report.items()) if key != "generated_at"},
        }
        return sha256_digest(payload)

    @property
    def logical_fingerprint(self) -> str:
        return self.plan_fingerprint

    def as_counts(self) -> dict[str, Any]:
        return {
            "input": self.input_count,
            "excluded": self.excluded_count,
            "eligible": self.eligible_count,
            "stored": self.stored_count,
            "suppressed_exact": self.suppressed_exact_count,
            "would_suppress_exact": self.would_suppress_exact,
            "exact_group_count": len(self.exact_groups),
            "near": dict(self.near_report),
        }

    @property
    def counts(self) -> dict[str, Any]:
        return self.as_counts()


def _producer_metadata_key(metadata: Mapping[str, Any]) -> Any:
    return {
        str(key): value
        for key, value in metadata.items()
        if key not in {"stable_document_id", "node_id"}
    }


def _exact_key(item: DedupInput) -> dict[str, Any] | None:
    valid_spans = (
        bool(item.source_span_ids)
        and all(isinstance(value, str) and bool(value.strip()) for value in item.source_span_ids)
        and tuple(sorted(set(item.source_span_ids))) == tuple(item.source_span_ids)
    )
    if item.source_span_reason or not valid_spans:
        return None
    if not item.node_type.strip():
        return None
    return {
        "key_version": DEDUP_KEY_VERSION,
        "partition_id": item.partition_id,
        "corpus_id": item.corpus_id,
        "episode_uid": item.episode_uid,
        "verified_cache_fingerprint": item.verified_cache_fingerprint,
        "node_type": item.node_type,
        "source_span_ids": list(item.source_span_ids),
        "normalized_text": item.normalized_text,
        "embedding_input": item.embedding_input,
        "producer_metadata": _producer_metadata_key(item.producer_metadata),
    }


def _group_id(key: Mapping[str, Any]) -> str:
    return "dupgrp_" + sha256_digest(key)[7:]


def _reason_for_nonmatch(left: DedupInput, right: DedupInput) -> str:
    if left.source_span_reason or right.source_span_reason or not left.source_span_ids or not right.source_span_ids:
        return "missing_span" if (left.source_span_reason == "missing_span" or right.source_span_reason == "missing_span" or not left.source_span_ids or not right.source_span_ids) else "invalid_span"
    if not left.node_type.strip() or not right.node_type.strip():
        return "missing_node_type"
    if left.partition_id != right.partition_id or left.corpus_id != right.corpus_id:
        return "scope_conflict"
    if left.episode_uid != right.episode_uid or left.verified_cache_fingerprint != right.verified_cache_fingerprint:
        return "different_source_revision"
    if left.normalized_text != right.normalized_text:
        return "span_conflict"
    if left.embedding_input != right.embedding_input:
        return "different_embedding_input"
    if _producer_metadata_key(left.producer_metadata) != _producer_metadata_key(right.producer_metadata):
        return "metadata_conflict"
    return "unique"


def build_exact_plan(
    inputs: Iterable[DedupInput],
    policy: Mapping[str, Any] | None = None,
    spec: RepresentationSpec | None = None,
    *,
    excluded_count: int = 0,
    excluded_reasons: Mapping[str, int] | None = None,
    input_count: int | None = None,
) -> DedupPlan:
    """Build a deterministic release-wide exact plan."""
    resolved = resolve_dedup_policy(policy, default_profile="off")
    items = sorted(list(inputs), key=lambda item: item.effective_id)
    if spec is not None:
        items = [
            replace(item, embedding_input=embedding_text(item.page_content, dict(item.producer_metadata), spec.contextualization))
            if not item.embedding_input else item
            for item in items
        ]
    if input_count is None:
        input_count = len(items) + excluded_count
    for item in items:
        if not item.effective_id:
            raise DeduplicationError("dedup input has an empty effective ID")
        validate_reserved_metadata(item.producer_metadata, enabled=resolved["profile"] != "off")
    by_id: dict[str, DedupInput] = {}
    for item in items:
        if item.effective_id in by_id:
            raise DeduplicationError(f"duplicate effective ID across dedup inputs: {item.effective_id}")
        by_id[item.effective_id] = item
    decisions = {item_id: DedupDecision(item_id, reason="unique", preferred_canonical_id=item_id) for item_id in by_id}
    exact_groups: list[ExactGroup] = []
    buckets: dict[str, list[DedupInput]] = defaultdict(list)
    key_by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        key = _exact_key(item)
        if key is None:
            if item.source_span_reason:
                decisions[item.effective_id].reason = item.source_span_reason
            elif not item.source_span_ids:
                decisions[item.effective_id].reason = "missing_span"
            elif not item.node_type.strip():
                decisions[item.effective_id].reason = "missing_node_type"
            else:
                decisions[item.effective_id].reason = "invalid_span"
            continue
        digest = sha256_digest(key)
        buckets[digest].append(item)
        key_by_id[item.effective_id] = key
    for digest, bucket in sorted(buckets.items()):
        # Never allow a digest collision to authorize suppression.
        groups: list[list[DedupInput]] = []
        for item in sorted(bucket, key=lambda candidate: candidate.effective_id):
            key = key_by_id[item.effective_id]
            match = next((group for group in groups if _exact_key(group[0]) == key), None)
            if match is None:
                groups.append([item])
            else:
                match.append(item)
        for members in groups:
            if len(members) < 2 or resolved["profile"] == "off":
                continue
            member_ids = tuple(sorted(item.effective_id for item in members))
            canonical = member_ids[0]
            group_id = _group_id(key_by_id[canonical])
            exact_groups.append(ExactGroup(group_id, canonical, member_ids))
            for item_id in member_ids:
                decision = decisions[item_id]
                decision.duplicate_group_id = group_id
                decision.preferred_canonical_id = canonical
                decision.method = "exact_provenance_v1"
                if resolved["profile"] == "safe" and item_id != canonical:
                    decision.storage_status = "suppressed_exact"
                    decision.alias_target_id = canonical
                    decision.reason = "exact_duplicate"
                elif item_id == canonical:
                    decision.reason = "canonical"
                else:
                    decision.reason = "would_suppress_exact"
    # Retain bounded conflict diagnostics.  A representative comparison per
    # normalized-text/span bucket is enough to assign a deterministic primary
    # reason; reporting every equal-text pair would be quadratic for a noisy
    # release and adds no suppression authority.
    normalized_buckets: dict[str, list[DedupInput]] = defaultdict(list)
    span_buckets: dict[str, list[DedupInput]] = defaultdict(list)
    for item in items:
        if item.effective_id in key_by_id:
            normalized_buckets[item.normalized_text_hash].append(item)
            for span in item.source_span_ids:
                span_buckets[span].append(item)
    for bucket in list(normalized_buckets.values()) + list(span_buckets.values()):
        bucket.sort(key=lambda candidate: candidate.effective_id)
        for item in bucket:
            decision = decisions[item.effective_id]
            if decision.reason != "unique":
                continue
            candidate = next(
                (other for other in bucket if other.effective_id != item.effective_id and _exact_key(other) != _exact_key(item)),
                None,
            )
            if candidate is not None:
                decision.reason = _reason_for_nonmatch(item, candidate)
    representatives = [by_id[item_id] for item_id in sorted(by_id) if not decisions[item_id].suppressed]
    near_edges, near_report = detect_near_edges(representatives, resolved)
    cache_ids: dict[str, tuple[str, ...]] = {}
    cache_buckets: dict[str, list[str]] = defaultdict(list)
    for item in items:
        if item.cache_locator:
            cache_buckets[item.cache_locator].append(item.effective_id)
    cache_ids = {key: tuple(sorted(value)) for key, value in sorted(cache_buckets.items())}
    return DedupPlan(
        policy=resolved,
        decisions=decisions,
        inputs=by_id,
        exact_groups=sorted(exact_groups, key=lambda group: group.duplicate_group_id),
        near_edges=near_edges,
        near_report=near_report,
        cache_ids=cache_ids,
        excluded_count=excluded_count,
        excluded_reasons=dict(excluded_reasons or {}),
        input_count=input_count,
        representation_id=spec.representation_id if spec is not None else "",
    )


def _shingles(text: str) -> tuple[list[str], set[tuple[str, str, str]]]:
    terms = tokenize(text)
    return terms, {tuple(terms[index:index + 3]) for index in range(max(0, len(terms) - 2))}


def detect_near_edges(
    representatives: Iterable[DedupInput],
    policy: Mapping[str, Any] | None = None,
) -> tuple[list[NearEdge], dict[str, Any]]:
    resolved = resolve_dedup_policy(policy, default_profile="off")
    if not resolved["near_enabled"]:
        return [], {
            "status": "disabled", "candidate_pairs": 0, "edges": 0,
            "unique_flagged_records": 0, "skipped_short_records": 0,
            "skipped_block_records": 0,
        }
    records = sorted(list(representatives), key=lambda item: item.effective_id)
    blocks: dict[tuple[str, str, str], list[tuple[DedupInput, list[str], set[tuple[str, str, str]]]]] = defaultdict(list)
    skipped_short = 0
    for item in records:
        if not item.node_type.strip():
            continue
        terms, shingles = _shingles(item.page_content)
        if len(terms) < resolved["near_min_tokens"]:
            skipped_short += 1
            continue
        blocks[(item.partition_id, item.corpus_id, item.node_type)].append((item, terms, shingles))
    edges: list[NearEdge] = []
    skipped_block = 0
    candidate_pairs = 0
    for block_key in sorted(blocks):
        rows = blocks[block_key]
        if len(rows) > resolved["near_max_block_records"]:
            skipped_block += len(rows)
            continue
        for index, (left, left_terms, left_shingles) in enumerate(rows):
            for right, right_terms, right_shingles in rows[index + 1:]:
                candidate_pairs += 1
                ratio = min(len(left_terms), len(right_terms)) / max(len(left_terms), len(right_terms))
                if ratio < resolved["near_length_ratio"]:
                    continue
                union = left_shingles | right_shingles
                similarity = len(left_shingles & right_shingles) / len(union) if union else 0.0
                if similarity < resolved["near_jaccard_threshold"]:
                    continue
                edges.append(NearEdge(
                    left.effective_id, right.effective_id, float(similarity), float(ratio),
                    left.partition_id, left.corpus_id, left.node_type,
                    left.episode_uid == right.episode_uid,
                ))
    edges.sort(key=lambda edge: (edge.left_id, edge.right_id))
    flagged = {value for edge in edges for value in (edge.left_id, edge.right_id)}
    return edges, {
        "status": "incomplete" if skipped_block else "complete",
        "candidate_pairs": candidate_pairs,
        "edges": len(edges),
        "unique_flagged_records": len(flagged),
        "skipped_short_records": skipped_short,
        "skipped_block_records": skipped_block,
    }


@dataclass
class DedupInventory:
    inputs: list[DedupInput]
    excluded_count: int = 0
    excluded_reasons: dict[str, int] = field(default_factory=dict)

    def __iter__(self):
        return iter(self.inputs)

    def __len__(self) -> int:
        return len(self.inputs)


def _scope_value(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if value in (None, "") and isinstance(mapping.get("partition"), Mapping):
        value = mapping["partition"].get(key)
    return str(value or "").strip()


def _scope_values(mapping: Mapping[str, Any], key: str) -> list[str]:
    values: list[str] = []
    direct = mapping.get(key)
    if direct not in (None, ""):
        values.append(str(direct).strip())
    nested = mapping.get("partition")
    if isinstance(nested, Mapping) and nested.get(key) not in (None, ""):
        values.append(str(nested.get(key)).strip())
    return [value for value in values if value]


def _consistent_scope(mappings: Iterable[Mapping[str, Any]], key: str, fallback: str) -> str:
    values: set[str] = set()
    for mapping in mappings:
        values.update(_scope_values(mapping, key))
    if len(values) > 1:
        raise DeduplicationError(f"managed input contains conflicting {key} values")
    return next(iter(values), fallback)


def load_managed_dedup_inputs(
    cache_paths: Iterable[Path],
    identity: Any,
    upstream: Mapping[str, Any],
    spec: RepresentationSpec | None = None,
    *,
    selected_speakers: Iterable[str] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> DedupInventory:
    """Validate raw producer rows across the complete release.

    This runs before ``load_processed_documents`` so malformed rows cannot be
    silently skipped by the legacy document adapter.
    """
    expected_partition = str(getattr(identity, "partition_id", None) or identity.get("partition_id"))
    expected_corpus = str(getattr(identity, "corpus_id", None) or identity.get("corpus_id"))
    expected_episodes = {str(item) for item in upstream.get("episode_uids") or []}
    chosen_speakers = {str(value).strip() for value in (selected_speakers or []) if str(value).strip()}
    representation = spec or RepresentationSpec()
    rows: list[tuple[Path, Mapping[str, Any], Mapping[str, Any], str]] = []
    seen_ids: set[str] = set()
    excluded_reasons: dict[str, int] = defaultdict(int)
    total = 0
    sorted_paths = sorted(Path(value) for value in cache_paths)
    total_paths = len(sorted_paths)
    for path_index, path in enumerate(sorted_paths, 1):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeduplicationError(f"cannot read managed cache {path}: {exc}") from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("documents"), list):
            raise DeduplicationError(f"managed cache {path.name} must contain a documents array")
        actual_fingerprint = content_fingerprint(path)
        declared = str(payload.get("cache_fingerprint") or payload.get("source_fingerprint") or actual_fingerprint)
        expected_fingerprints = {str(item) for item in upstream.get("processed_cache_fingerprints") or []}
        if expected_fingerprints and not ({actual_fingerprint, declared} & expected_fingerprints):
            raise DeduplicationError(f"cache fingerprint is not bound to upstream release: {path.name}")
        # Prefer the bytes we verified.  A producer-declared logical
        # fingerprint is accepted only when it is the release's explicit
        # binding (older packages may not expose our content hash format).
        verified_fingerprint = actual_fingerprint if actual_fingerprint in expected_fingerprints or not expected_fingerprints else declared
        enclosing_partition = _consistent_scope((payload,), "partition_id", expected_partition)
        enclosing_corpus = _consistent_scope((payload,), "corpus_id", expected_corpus)
        for index, raw in enumerate(payload["documents"]):
            total += 1
            if not isinstance(raw, Mapping):
                raise DeduplicationError(f"{path.name} row {index} is not an object")
            metadata = raw.get("metadata")
            if not isinstance(metadata, Mapping):
                raise DeduplicationError(f"{path.name} row {index} metadata is not an object")
            node_id = metadata.get("node_id", raw.get("node_id"))
            stable_id = metadata.get("stable_document_id", raw.get("stable_document_id"))
            effective_id = stable_id or node_id
            if not isinstance(effective_id, str) or not effective_id.strip():
                raise DeduplicationError(f"{path.name} row {index} is missing stable_document_id/node_id")
            effective_id = effective_id.strip()
            if effective_id in seen_ids:
                raise DeduplicationError(f"duplicate effective ID across managed caches: {effective_id}")
            seen_ids.add(effective_id)
            row_partition = _consistent_scope((metadata, raw), "partition_id", enclosing_partition)
            row_corpus = _consistent_scope((metadata, raw), "corpus_id", enclosing_corpus)
            if row_partition != expected_partition or row_corpus != expected_corpus:
                raise DeduplicationError(f"managed row {effective_id} has a partition/corpus mismatch")
            episode_id_values = {str(mapping.get("episode_id") or "").strip() for mapping in (metadata, raw, payload) if mapping.get("episode_id") not in (None, "")}
            if len(episode_id_values) > 1:
                raise DeduplicationError(f"managed row {effective_id} contains conflicting episode_id values")
            episode_id = next(iter(episode_id_values), "")
            episode_uid = _consistent_scope((metadata, raw, payload), "episode_uid", f"{expected_partition}:{episode_id}" if episode_id else "")
            if not episode_uid or episode_uid not in expected_episodes or episode_uid != f"{expected_partition}:{episode_uid.split(':', 1)[-1]}":
                raise DeduplicationError(f"managed row {effective_id} has an invalid or foreign episode_uid")
            if episode_id and episode_uid != f"{expected_partition}:{episode_id}":
                raise DeduplicationError(f"managed row {effective_id} episode_id disagrees with episode_uid")
            text = raw.get("page_content", "")
            if not isinstance(text, str):
                text = str(text or "")
            copied_metadata = dict(metadata)
            copied_metadata.setdefault("partition_id", expected_partition)
            copied_metadata.setdefault("corpus_id", expected_corpus)
            copied_metadata.setdefault("episode_uid", episode_uid)
            node_type = str(copied_metadata.get("node_type") or "").strip()
            source_span_ids, span_reason = normalize_span_ids(copied_metadata)
            include = has_text(text)
            if chosen_speakers:
                speakers = set()
                speaker = copied_metadata.get("speaker")
                if isinstance(speaker, str) and speaker.strip() and speaker.lower() not in {"unknown", "multiple", "mixed"}:
                    speakers.add(speaker.strip())
                values = copied_metadata.get("speakers")
                if isinstance(values, str):
                    try:
                        values = json.loads(values)
                    except json.JSONDecodeError:
                        values = [part.strip() for part in values.split(",")]
                if isinstance(values, list):
                    speakers.update(str(value).strip() for value in values if str(value).strip())
                if node_type in {"episode_thesis", "cluster_summary"} and copied_metadata.get("speaker_scope") != "single":
                    include = include
                else:
                    include = include and bool(speakers & chosen_speakers)
            if not include:
                reason = "empty_text" if not has_text(text) else "speaker_filtered"
                excluded_reasons[reason] += 1
                continue
            rows.append((path, raw, copied_metadata, verified_fingerprint))
        if progress_callback is not None:
            progress_callback(f"Validated producer cache {path_index}/{total_paths}: {path.name}", path_index, total_paths)
    inputs: list[DedupInput] = []
    for path, raw, metadata, fingerprint in rows:
        effective_id = str(metadata.get("stable_document_id") or metadata.get("node_id") or raw.get("stable_document_id") or raw.get("node_id") or "").strip()
        node_id = str(metadata.get("node_id") or raw.get("node_id") or effective_id)
        text = str(raw.get("page_content", "") or "")
        spans, reason = normalize_span_ids(metadata)
        inputs.append(DedupInput(
            effective_id=effective_id,
            node_id=node_id,
            page_content=text,
            producer_metadata=dict(metadata),
            partition_id=expected_partition,
            corpus_id=expected_corpus,
            episode_uid=str(metadata["episode_uid"]),
            verified_cache_fingerprint=fingerprint,
            embedding_input=embedding_text(text, dict(metadata), representation.contextualization),
            node_type=str(metadata.get("node_type") or ""),
            source_span_ids=spans,
            source_span_reason=reason,
            cache_locator=str(path),
        ))
    return DedupInventory(inputs, total - len(inputs), dict(sorted(excluded_reasons.items())))


__all__ = [
    "DEDUP_ARTIFACT_CONTRACT", "DEDUP_CAPABILITY", "DEDUP_KEY_VERSION", "DEDUP_PROFILE_VERSION",
    "DedupDecision", "DedupInput", "DedupInventory", "DedupPlan", "DeduplicationError",
    "ExactGroup", "NearEdge", "build_exact_plan", "detect_near_edges", "load_managed_dedup_inputs",
    "normalize_span_ids", "normalize_text_v1", "partition_dedup_policy_fingerprint",
    "resolve_dedup_policy", "sha256_digest", "validate_reserved_metadata",
]
