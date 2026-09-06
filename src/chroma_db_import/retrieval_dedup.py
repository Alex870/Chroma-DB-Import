"""Provider-independent retrieval repetition-control reference helper."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from .deduplication import DeduplicationError, resolve_dedup_policy


class RetrievalDedupError(DeduplicationError):
    pass


def candidate_limit(top_k: int, collection_count: int, policy: Mapping[str, Any] | None = None) -> int:
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1 or top_k > 200:
        raise RetrievalDedupError("top_k must be an integer between 1 and 200")
    if isinstance(collection_count, bool) or not isinstance(collection_count, int) or collection_count < 0:
        raise RetrievalDedupError("collection_count must be a non-negative integer")
    resolved = resolve_dedup_policy(policy, default_profile="off")
    if not resolved["retrieval"]["enabled"]:
        return min(collection_count, top_k)
    return min(collection_count, resolved["retrieval"]["candidate_cap"], resolved["retrieval"]["oversample_factor"] * top_k)


def _hit_identity(hit: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), Mapping) else hit
    return (
        str(metadata.get("partition_id") or ""),
        str(metadata.get("corpus_id") or ""),
        str(metadata.get("release_id") or metadata.get("downstream_release_id") or ""),
        str(metadata.get("representation_id") or ""),
        str(hit.get("id") or hit.get("document_id") or ""),
    )


def _manifest_identity(manifest: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(manifest.get("partition_id") or ""),
        str(manifest.get("corpus_id") or ""),
        str(manifest.get("release_id") or ""),
        str(manifest.get("representation_id") or ""),
    )


def _occurrence_map(occurrences: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in occurrences:
        item_id = str(row.get("document_id") or "")
        if not item_id or item_id in result:
            raise RetrievalDedupError("occurrence inventory contains an empty or duplicate ID")
        result[item_id] = row
    return result


def collapse_ranked_hits(
    hits: Iterable[Mapping[str, Any]],
    validated_manifest: Mapping[str, Any],
    occurrences: Iterable[Mapping[str, Any]],
    *,
    top_k: int,
    eligible_occurrence_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Collapse only validated exact groups while preserving supplied rank."""
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1 or top_k > 200:
        raise RetrievalDedupError("top_k must be an integer between 1 and 200")
    expected = _manifest_identity(validated_manifest)
    if any(not value for value in expected):
        raise RetrievalDedupError("validated manifest lacks retrieval identity")
    inventory = _occurrence_map(occurrences)
    eligible = set(inventory) if eligible_occurrence_ids is None else {str(item) for item in eligible_occurrence_ids}
    if not eligible <= set(inventory):
        raise RetrievalDedupError("eligible occurrence set contains an unknown ID")
    ranked: list[tuple[int, str, Mapping[str, Any]]] = []
    for sequence, raw_hit in enumerate(hits):
        if not isinstance(raw_hit, Mapping):
            raise RetrievalDedupError("retrieval hit must be an object")
        partition, corpus, release_id, representation, item_id = _hit_identity(raw_hit)
        if (partition, corpus, release_id, representation) != expected:
            raise RetrievalDedupError(f"retrieval hit {item_id or sequence} has foreign release identity")
        if item_id not in inventory:
            raise RetrievalDedupError(f"retrieval hit {item_id} is absent from the occurrence ledger")
        rank = raw_hit.get("rank", sequence)
        if isinstance(rank, bool) or not isinstance(rank, int):
            raise RetrievalDedupError("retrieval hit rank must be an integer")
        ranked.append((rank, item_id, raw_hit))
    ranked.sort(key=lambda value: (value[0], value[1]))
    retrieval_policy = validated_manifest.get("policy") if isinstance(validated_manifest.get("policy"), Mapping) else {}
    collapse_enabled = bool((retrieval_policy.get("retrieval") or {}).get("enabled", True))
    seen_groups: set[str] = set()
    selected: list[Mapping[str, Any]] = []
    related: dict[str, set[str]] = defaultdict(set)
    for _, item_id, hit in ranked:
        if item_id not in eligible:
            continue
        row = inventory[item_id]
        if row.get("storage_status") == "suppressed_exact":
            raise RetrievalDedupError(f"suppressed occurrence {item_id} is present in retrieval results")
        group_id = str(row.get("duplicate_group_id") or "")
        group = next((value for value in validated_manifest.get("exact_groups") or [] if str(value.get("duplicate_group_id") or "") == group_id), None) if group_id else None
        if group_id and group is None:
            raise RetrievalDedupError(f"occurrence {item_id} references an unvalidated duplicate group")
        if group_id:
            group_members = {str(value) for value in group.get("member_ids") or []}
            if not group_members <= set(inventory) or str(group.get("canonical_id") or "") != min(group_members) or any(str(inventory[member].get("duplicate_group_id") or "") != group_id for member in group_members):
                raise RetrievalDedupError("duplicate group has a foreign member")
            related[group_id].update(group_members & eligible)
            if not collapse_enabled:
                selected.append(hit)
                if len(selected) >= top_k:
                    break
                continue
            if group_id in seen_groups:
                continue
            seen_groups.add(group_id)
        elif not collapse_enabled:
            selected.append(hit)
            if len(selected) >= top_k:
                break
            continue
        selected.append(hit)
        if len(selected) >= top_k:
            break
    selected_ids = {str(hit.get("id") or hit.get("document_id") or "") for hit in selected}
    related_rows = {
        group_id: sorted(values - selected_ids)
        for group_id, values in related.items()
    }
    return {
        "hits": list(selected),
        "related_occurrence_ids": related_rows,
        "underfill": max(0, top_k - len(selected)),
        "candidate_count": len(ranked),
    }


def resolve_alias(document_id: str, validated_occurrences: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    inventory = _occurrence_map(validated_occurrences)
    item_id = str(document_id)
    row = inventory.get(item_id)
    if row is None:
        return {"available": False, "document_id": item_id}
    target = row.get("alias_target_id")
    if target in (None, ""):
        return {"available": True, "document_id": item_id, "original_document_id": item_id, "row": row}
    target = str(target)
    if target not in inventory:
        raise RetrievalDedupError("alias target is absent from the occurrence ledger")
    if target == item_id:
        raise RetrievalDedupError("alias self-cycle detected")
    if inventory[target].get("alias_target_id") not in (None, ""):
        raise RetrievalDedupError("alias chains are not permitted")
    return {"available": True, "document_id": target, "original_document_id": item_id, "row": inventory[target], "occurrence": row}


__all__ = ["RetrievalDedupError", "candidate_limit", "collapse_ranked_hits", "resolve_alias"]
