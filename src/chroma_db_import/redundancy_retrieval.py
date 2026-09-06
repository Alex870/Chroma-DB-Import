"""Filter-first read-only retrieval adapter for redundancy bundles."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .redundancy_chroma import close_chroma_client


class RedundancyRetrievalError(ValueError):
    pass


FILTER_FIELDS = {"episodes", "speakers", "node_types", "date_start", "date_end", "allowed_occurrence_ids"}
MODES = {"factual", "ranked", "semantic_mmr", "recurrence", "timeline", "speaker_comparison", "preserve_occurrences"}


def _bundle_root(bundle: Any) -> Path:
    if isinstance(bundle, (str, Path)):
        return Path(bundle).expanduser().resolve()
    if isinstance(bundle, Mapping) and bundle.get("root"):
        return Path(str(bundle["root"])).expanduser().resolve()
    raise RedundancyRetrievalError("bundle root is required")


def _manifest(root: Path) -> dict[str, Any]:
    try:
        value = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RedundancyRetrievalError("bundle manifest is unavailable") from exc
    if not isinstance(value, dict):
        raise RedundancyRetrievalError("bundle manifest must be an object")
    return value


def _validate_filter(value: Mapping[str, Any] | None) -> dict[str, Any]:
    filters = dict(value or {})
    unknown = sorted(set(filters) - FILTER_FIELDS)
    if unknown:
        raise RedundancyRetrievalError("unknown retrieval filter field(s): " + ", ".join(unknown))
    for key in ("episodes", "speakers", "node_types", "allowed_occurrence_ids"):
        if key in filters and filters[key] is not None and not isinstance(filters[key], (list, tuple, set, frozenset)):
            raise RedundancyRetrievalError(f"{key} filter must be a collection")
    if "date_start" in filters and filters["date_start"] is not None and not isinstance(filters["date_start"], str):
        raise RedundancyRetrievalError("date_start must be a string")
    if "date_end" in filters and filters["date_end"] is not None and not isinstance(filters["date_end"], str):
        raise RedundancyRetrievalError("date_end must be a string")
    return filters


def _rows(root: Path) -> list[dict[str, Any]]:
    path = root / "evidence.jsonl"
    if not path.is_file():
        raise RedundancyRetrievalError("bundle evidence is unavailable")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _speakers(row: Mapping[str, Any]) -> set[str]:
    metadata = row.get("metadata") or {}
    values = metadata.get("speakers") if isinstance(metadata, Mapping) else []
    if isinstance(values, str):
        values = [values]
    result = {str(item) for item in values or []}
    if isinstance(metadata, Mapping) and metadata.get("speaker"):
        result.add(str(metadata["speaker"]))
    return result


def eligible_occurrences(bundle: Any, validated_filter: Mapping[str, Any] | None) -> list[str]:
    filters = _validate_filter(validated_filter)
    root = _bundle_root(bundle)
    rows = _rows(root)
    allowed = filters.get("allowed_occurrence_ids")
    allowed_set = None if allowed is None else {str(item) for item in allowed}
    episodes = {str(item) for item in filters.get("episodes") or []}
    speakers = {str(item) for item in filters.get("speakers") or []}
    node_types = {str(item) for item in filters.get("node_types") or []}
    result: list[str] = []
    for row in rows:
        document_id = str(row.get("document_id") or "")
        metadata = row.get("metadata") or {}
        episode = str(row.get("episode_uid") or metadata.get("episode_uid") or metadata.get("episode_id") or "")
        node_type = str(row.get("node_type") or metadata.get("node_type") or "")
        date = str(metadata.get("episode_date") or "")
        if allowed_set is not None and document_id not in allowed_set:
            continue
        if episodes and episode not in episodes and str(metadata.get("episode_id") or "") not in episodes:
            continue
        if speakers and not (_speakers(row) & speakers):
            continue
        if node_types and node_type not in node_types:
            continue
        if filters.get("date_start") and (not date or date < str(filters["date_start"])):
            continue
        if filters.get("date_end") and (not date or date > str(filters["date_end"])):
            continue
        result.append(document_id)
    return sorted(result)


def representatives_for(bundle: Any, eligible_ids: Iterable[str]) -> list[str]:
    root = _bundle_root(bundle)
    rows = {str(row.get("document_id")): row for row in _rows(root)}
    requested = {str(item) for item in eligible_ids}
    if not requested <= set(rows):
        raise RedundancyRetrievalError("eligible occurrence set contains an unknown ID")
    representatives: set[str] = set()
    for item_id in requested:
        row = rows[item_id]
        representatives.add(str(row.get("representative_id") or row.get("alias_target_id") or item_id))
    return sorted(representatives)


def _load_vectors(root: Path) -> dict[str, tuple[float, ...]]:
    path = root / "vectors.json"
    if not path.is_file():
        path = root / "vectors" / "vectors.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("vectors") if isinstance(payload, Mapping) else payload
    result: dict[str, tuple[float, ...]] = {}
    for key, values in (raw or {}).items():
        vector = tuple(float(value) for value in values)
        if not vector or any(not math.isfinite(value) for value in vector) or not any(value != 0.0 for value in vector):
            raise RedundancyRetrievalError("bundle vector storage contains an invalid cosine vector")
        result[str(key)] = vector
    return result


def _compact_chroma_manifest(root: Path) -> dict[str, Any] | None:
    path = root / "vectors" / "chroma_manifest.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RedundancyRetrievalError("compact vector backend manifest is invalid") from exc
    if not isinstance(value, Mapping) or value.get("backend") != "chroma" or not value.get("collection"):
        return None
    relative = value.get("path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or "\\" in relative:
        raise RedundancyRetrievalError("compact Chroma path is invalid")
    chroma_root = (root / "vectors" / relative).resolve()
    try:
        chroma_root.relative_to(root.resolve())
    except ValueError as exc:
        raise RedundancyRetrievalError("compact Chroma path escapes the bundle") from exc
    if not chroma_root.is_dir():
        raise RedundancyRetrievalError("compact Chroma collection is unavailable")
    return {**dict(value), "root": chroma_root}


def _query_compact_chroma(root: Path, query: tuple[float, ...], allowed: list[str], depth: int, batch_size: int) -> list[dict[str, Any]]:
    manifest = _compact_chroma_manifest(root)
    if manifest is None:
        return []
    client = None
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(manifest["root"]))
        collection = client.get_collection(name=str(manifest["collection"]))
    except ModuleNotFoundError as exc:
        raise RedundancyRetrievalError("compact retrieval requires chromadb") from exc
    except Exception as exc:
        raise RedundancyRetrievalError(f"could not open compact Chroma collection: {type(exc).__name__}") from exc
    try:
        hits: list[dict[str, Any]] = []
        vectors = _load_vectors(root)
        for start in range(0, len(allowed), batch_size):
            batch = allowed[start:start + batch_size]
            try:
                result = collection.query(
                    query_embeddings=[list(query)],
                    n_results=min(depth, len(batch)),
                    where={"representative_id": {"$in": batch}},
                    include=["distances"],
                )
            except Exception as exc:
                raise RedundancyRetrievalError(f"compact Chroma query failed: {type(exc).__name__}") from exc
            ids = ((result.get("ids") or [[]])[0] if isinstance(result, Mapping) else []) or []
            distances = ((result.get("distances") or [[]])[0] if isinstance(result, Mapping) else []) or []
            for index, raw_id in enumerate(ids):
                representative_id = str(raw_id)
                if representative_id not in batch:
                    raise RedundancyRetrievalError("compact Chroma returned an ID outside the allowed batch")
                if representative_id in vectors:
                    score = _cosine(query, vectors[representative_id])
                else:
                    if index >= len(distances):
                        raise RedundancyRetrievalError("compact Chroma omitted a returned distance")
                    distance = float(distances[index])
                    if not math.isfinite(distance):
                        raise RedundancyRetrievalError("compact Chroma returned a non-finite distance")
                    score = 1.0 - distance
                hits.append({"id": representative_id, "representative_id": representative_id, "score": score})
        hits.sort(key=lambda item: (-item["score"], item["id"]))
        for rank, hit in enumerate(hits[:depth]):
            hit["rank"] = rank
        return hits[:depth]
    finally:
        close_chroma_client(client)


def _cosine(left: Iterable[float], right: Iterable[float]) -> float:
    a, b = tuple(left), tuple(right)
    if len(a) != len(b) or not a:
        raise RedundancyRetrievalError("query/vector dimensions do not match")
    na, nb = math.sqrt(sum(value * value for value in a)), math.sqrt(sum(value * value for value in b))
    if not na or not nb:
        raise RedundancyRetrievalError("zero-norm vector is unavailable")
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def query_representatives(bundle: Any, query_vector: Iterable[float], allowed_ids: Iterable[str], depth: int, *, batch_size: int = 1000) -> list[dict[str, Any]]:
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:
        raise RedundancyRetrievalError("depth must be a positive integer")
    allowed = sorted({str(item) for item in allowed_ids})
    if not allowed:
        return []
    if batch_size > 1000 or batch_size < 1:
        raise RedundancyRetrievalError("batch_size must be between 1 and 1000")
    root = _bundle_root(bundle)
    try:
        query = tuple(float(value) for value in query_vector)
    except (TypeError, ValueError) as exc:
        raise RedundancyRetrievalError("query vector is invalid") from exc
    if not query or any(not math.isfinite(value) for value in query):
        raise RedundancyRetrievalError("query vector must be finite and non-empty")
    compact_manifest = _compact_chroma_manifest(root)
    if compact_manifest is not None:
        return _query_compact_chroma(root, query, allowed, depth, batch_size)
    vectors = _load_vectors(root)
    hits: list[dict[str, Any]] = []
    started = time.perf_counter()
    for start in range(0, len(allowed), batch_size):
        for representative_id in allowed[start:start + batch_size]:
            if representative_id not in vectors:
                continue
            hits.append({"id": representative_id, "representative_id": representative_id, "score": _cosine(query, vectors[representative_id])})
    hits.sort(key=lambda item: (-item["score"], item["id"]))
    for rank, hit in enumerate(hits[:depth]):
        hit["rank"] = rank
    return hits[:depth]


def hydrate_occurrences(bundle: Any, hits: Iterable[Mapping[str, Any]], eligible_ids: Iterable[str], mode: str = "factual", *, occurrence_offset: int = 0, occurrence_limit: int | None = None) -> dict[str, Any]:
    if mode not in MODES:
        raise RedundancyRetrievalError("unknown retrieval mode")
    if isinstance(occurrence_offset, bool) or not isinstance(occurrence_offset, int) or occurrence_offset < 0:
        raise RedundancyRetrievalError("occurrence_offset must be a non-negative integer")
    if occurrence_limit is not None and (isinstance(occurrence_limit, bool) or not isinstance(occurrence_limit, int) or occurrence_limit < 1 or occurrence_limit > 1000):
        raise RedundancyRetrievalError("occurrence_limit must be between 1 and 1000")
    root = _bundle_root(bundle)
    rows = {str(row.get("document_id")): row for row in _rows(root)}
    eligible = {str(item) for item in eligible_ids}
    if not eligible <= set(rows):
        raise RedundancyRetrievalError("eligible occurrence set contains an unknown ID")
    by_rep: dict[str, list[dict[str, Any]]] = {}
    for item_id in sorted(eligible):
        row = rows[item_id]
        representative_id = str(row.get("representative_id") or row.get("alias_target_id") or item_id)
        by_rep.setdefault(representative_id, []).append(row)
    selected: list[dict[str, Any]] = []
    related: dict[str, list[str]] = {}
    related_counts: dict[str, int] = {}
    next_offsets: dict[str, int | None] = {}
    preserve = mode in {"recurrence", "timeline", "speaker_comparison", "preserve_occurrences"}
    for hit in hits:
        representative_id = str(hit.get("representative_id") or hit.get("id") or "")
        members = sorted(by_rep.get(representative_id, []), key=lambda row: (int(row.get("original_rank") or (row.get("metadata") or {}).get("original_rank") or 10**9), str(row.get("document_id"))))
        if not members:
            continue
        if preserve:
            chosen = members[occurrence_offset:occurrence_offset + occurrence_limit] if occurrence_limit is not None else members[occurrence_offset:]
        else:
            chosen = members[:1]
        selected.extend({**dict(hit), "id": str(row.get("document_id")), "document_id": str(row.get("document_id")), "text": row.get("text") or row.get("page_content") or "", "metadata": row.get("metadata") or {}, "representative_id": representative_id} for row in chosen)
        related[representative_id] = [str(row.get("document_id")) for row in members]
        related_counts[representative_id] = len(members)
        if preserve and occurrence_limit is not None:
            next_value = occurrence_offset + occurrence_limit
            next_offsets[representative_id] = next_value if next_value < len(members) else None
        else:
            next_offsets[representative_id] = None
    return {"hits": selected, "related_occurrence_ids": related, "related_occurrence_counts": related_counts, "next_occurrence_offsets": next_offsets, "total_eligible_occurrences": len(eligible), "mode": mode}


class RedundancyRetrievalAdapter:
    def __init__(self, bundle: Any):
        self.bundle = bundle
        self.last_query_report: dict[str, Any] = {}

    def eligible_occurrences(self, validated_filter: Mapping[str, Any] | None) -> list[str]:
        return eligible_occurrences(self.bundle, validated_filter)

    def representatives_for(self, eligible_ids: Iterable[str]) -> list[str]:
        return representatives_for(self.bundle, eligible_ids)

    def query_representatives(self, query_vector: Iterable[float], allowed_ids: Iterable[str], depth: int) -> list[dict[str, Any]]:
        started = time.perf_counter()
        allowed = sorted({str(item) for item in allowed_ids})
        result = query_representatives(self.bundle, query_vector, allowed, depth)
        self.last_query_report = {"batch_count": (len(allowed) + 999) // 1000, "latency_ms": round((time.perf_counter() - started) * 1000, 3), "allowed_count": len(allowed)}
        return result

    def hydrate_occurrences(self, hits: Iterable[Mapping[str, Any]], eligible_ids: Iterable[str], mode: str = "factual", *, occurrence_offset: int = 0, occurrence_limit: int | None = None) -> dict[str, Any]:
        return hydrate_occurrences(self.bundle, hits, eligible_ids, mode, occurrence_offset=occurrence_offset, occurrence_limit=occurrence_limit)


__all__ = ["RedundancyRetrievalAdapter", "RedundancyRetrievalError", "eligible_occurrences", "hydrate_occurrences", "query_representatives", "representatives_for"]
