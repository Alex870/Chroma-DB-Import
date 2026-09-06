"""The single provider-independent semantic MMR selector."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from .redundancy_models import SelectionResult


class SemanticSelectionError(ValueError):
    pass


def candidate_depth(top_k: int, eligible_count: int) -> int:
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 200:
        raise SemanticSelectionError("top_k must be an integer between 1 and 200")
    if isinstance(eligible_count, bool) or not isinstance(eligible_count, int) or eligible_count < 0:
        raise SemanticSelectionError("eligible_count must be a non-negative integer")
    return min(200, max(top_k, 5 * top_k), eligible_count)


def _hit_id(hit: Mapping[str, Any]) -> str:
    return str(hit.get("id") or hit.get("document_id") or "")


def _cosine(left: Iterable[float], right: Iterable[float]) -> float:
    a, b = tuple(float(value) for value in left), tuple(float(value) for value in right)
    if len(a) != len(b) or not a:
        raise SemanticSelectionError("selection vectors have incompatible shapes")
    if any(not math.isfinite(value) for value in (*a, *b)):
        raise SemanticSelectionError("selection vectors contain non-finite values")
    na, nb = math.sqrt(sum(value * value for value in a)), math.sqrt(sum(value * value for value in b))
    if not na or not nb:
        raise SemanticSelectionError("zero-norm selection vector is unavailable")
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _ranked(hits: list[Mapping[str, Any]], top_k: int, mode: str, *, fallback_reason: str | None = None, exact_collapse: bool = True) -> SelectionResult:
    selected: list[str] = []
    omitted: list[dict[str, Any]] = []
    seen_groups: set[str] = set()
    for index, hit in enumerate(hits):
        item_id = _hit_id(hit)
        if not item_id:
            continue
        group = str((hit.get("metadata") or {}).get("duplicate_group_id") or hit.get("duplicate_group_id") or "")
        if exact_collapse and mode == "ranked" and group:
            if group in seen_groups:
                omitted.append({"id": item_id, "reason": "exact_copy", "rank": index})
                continue
            seen_groups.add(group)
        if len(selected) < top_k:
            selected.append(item_id)
        else:
            omitted.append({"id": item_id, "reason": "top_k", "rank": index})
    return SelectionResult(tuple(selected), tuple(omitted), (), mode, fallback_reason, "candidate_pool_underfilled" if len(selected) < top_k else None)


def select_evidence(hits: Iterable[Mapping[str, Any]], vectors: Mapping[str, Iterable[float]] | None, *, top_k: int, mode: str = "ranked", mmr_lambda: float = 0.75) -> SelectionResult:
    materialized = [dict(hit) for hit in hits]
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1 or top_k > 200:
        raise SemanticSelectionError("top_k must be an integer between 1 and 200")
    if mode not in {"ranked", "semantic_mmr", "preserve_occurrences"}:
        raise SemanticSelectionError("selection mode is unknown")
    if isinstance(mmr_lambda, bool) or not isinstance(mmr_lambda, (int, float)) or not math.isfinite(float(mmr_lambda)) or not 0 <= float(mmr_lambda) <= 1:
        raise SemanticSelectionError("mmr_lambda must be finite and between 0 and 1")
    # Inputs are already relevance-ranked. Re-sort only to make missing rank
    # values deterministic and to prevent provider-dependent dictionary order.
    ranked = sorted(enumerate(materialized), key=lambda item: (int(item[1].get("rank", item[0])) if isinstance(item[1].get("rank", item[0]), int) and not isinstance(item[1].get("rank", item[0]), bool) else item[0], _hit_id(item[1])))
    hits_sorted = [item[1] for item in ranked]
    ids = [_hit_id(hit) for hit in hits_sorted]
    if any(not item_id for item_id in ids) or len(ids) != len(set(ids)):
        raise SemanticSelectionError("selection hits must have unique non-empty IDs")
    if mode != "semantic_mmr":
        return _ranked(hits_sorted, top_k, mode, exact_collapse=(mode == "ranked"))
    if not hits_sorted:
        return SelectionResult((), (), (), mode, None, "candidate_pool_empty")
    if vectors is None:
        return _ranked(hits_sorted, top_k, mode, fallback_reason="vectors_unavailable", exact_collapse=False)
    try:
        parsed = {str(key): tuple(float(value) for value in vector) for key, vector in vectors.items()}
        if any(item_id not in parsed for item_id in ids):
            raise SemanticSelectionError("a required candidate vector is missing")
        dimensions = {len(parsed[item_id]) for item_id in ids}
        if len(dimensions) != 1 or any(not parsed[item_id] or any(not math.isfinite(value) for value in parsed[item_id]) for item_id in ids):
            raise SemanticSelectionError("candidate vector shapes are invalid")
        for item_id in ids:
            if not any(abs(value) > 0 for value in parsed[item_id]):
                raise SemanticSelectionError("a required candidate vector has zero norm")
    except (TypeError, ValueError, SemanticSelectionError) as exc:
        reason = "vectors_unavailable" if "missing" in str(exc) else str(exc)
        return _ranked(hits_sorted, top_k, mode, fallback_reason=reason, exact_collapse=False)
    selected_indices: list[int] = [0]
    omitted: list[dict[str, Any]] = []
    while len(selected_indices) < min(top_k, len(hits_sorted)):
        candidates = [index for index in range(len(hits_sorted)) if index not in selected_indices]
        scored: list[tuple[float, int, str]] = []
        for index in candidates:
            relevance = 1.0 / (1.0 + index)
            maximum_similarity = 0.0
            for selected_index in selected_indices:
                try:
                    maximum_similarity = max(maximum_similarity, max(0.0, _cosine(parsed[ids[index]], parsed[ids[selected_index]])))
                except SemanticSelectionError:
                    return _ranked(hits_sorted, top_k, mode, fallback_reason="invalid_vector", exact_collapse=False)
            score = float(mmr_lambda) * relevance - (1.0 - float(mmr_lambda)) * maximum_similarity
            scored.append((score, index, ids[index]))
        if not scored:
            break
        # Highest score wins; tied scores use relevance rank and then ID.
        best_score = max(value[0] for value in scored)
        tied = [value for value in scored if abs(value[0] - best_score) <= 1e-12]
        chosen = min(tied, key=lambda value: (value[1], value[2]))[1]
        selected_indices.append(chosen)
    selected_set = set(selected_indices)
    for index, hit in enumerate(hits_sorted):
        if index not in selected_set:
            omitted.append({"id": ids[index], "reason": "mmr", "rank": index})
    return SelectionResult(tuple(ids[index] for index in selected_indices), tuple(omitted), (), mode, None, "candidate_pool_underfilled" if len(selected_indices) < top_k else None)


__all__ = ["SemanticSelectionError", "candidate_depth", "select_evidence"]
