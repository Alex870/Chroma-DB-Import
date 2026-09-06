"""Bounded lexical, source, and dense candidate discovery."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .redundancy_models import AnalysisUnit, CandidatePair, Coverage
from .redundancy_policy import resolve_redundancy_policy


LEXICAL_ALGORITHM_VERSION = "redundancy-minhash-v1"
NEGATION_MARKERS = {"no", "not", "never", "without", "may", "might", "could", "must", "only", "unless", "if"}


class RedundancyCandidateError(ValueError):
    pass


def _unit(value: AnalysisUnit | Mapping[str, Any]) -> AnalysisUnit:
    return value if isinstance(value, AnalysisUnit) else AnalysisUnit.from_mapping(value)


def _tokens(text: str) -> tuple[str, ...]:
    import re
    return tuple(re.findall(r"[^\W_]+(?:['-][^\W_]+)*", str(text or "").casefold(), flags=re.UNICODE))


def normalized_text(text: str) -> str:
    return " ".join(_tokens(text))


def token_shingles(text: str) -> frozenset[tuple[str, str, str]]:
    tokens = _tokens(text)
    return frozenset(tuple(tokens[index:index + 3]) for index in range(max(0, len(tokens) - 2)))


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def minhash_signature(shingles: Iterable[tuple[str, str, str]]) -> tuple[int, ...]:
    values = list(shingles)
    if not values:
        return tuple([2**64 - 1] * 64)
    signature: list[int] = []
    for seed in range(64):
        best = 2**64 - 1
        prefix = seed.to_bytes(4, "big") + b"\0"
        for shingle in values:
            digest = hashlib.sha256(prefix + _canonical_json(shingle)).digest()
            best = min(best, int.from_bytes(digest[:8], "big", signed=False))
        signature.append(best)
    return tuple(signature)


def _scope_key(unit: AnalysisUnit) -> tuple[str, str, str]:
    return str(unit.metadata.get("partition_id") or ""), str(unit.metadata.get("corpus_id") or ""), unit.node_type


def _representation_key(unit: AnalysisUnit) -> str:
    return str(unit.metadata.get("representation_id") or "")


def _pair_scope(left: AnalysisUnit, right: AnalysisUnit) -> bool:
    return _scope_key(left) == _scope_key(right) and _representation_key(left) == _representation_key(right)


def _numeric_tokens(text: str) -> frozenset[str]:
    import re
    return frozenset(re.findall(r"(?<![\w])(?:\d+(?:[.,]\d+)?%?|\$\d+(?:[.,]\d+)?)(?![\w])", text))


def _marker_tokens(text: str) -> frozenset[str]:
    return frozenset(token for token in _tokens(text) if token in NEGATION_MARKERS)


def material_guards(left: AnalysisUnit, right: AnalysisUnit) -> tuple[str, ...]:
    guards: list[str] = []
    if _numeric_tokens(left.text) != _numeric_tokens(right.text):
        guards.append("numeric_token_set_changed")
    if _marker_tokens(left.text) != _marker_tokens(right.text):
        guards.append("negation_modal_marker_changed")
    if left.episode_uid != right.episode_uid or left.cache_fingerprint != right.cache_fingerprint:
        guards.append("source_revision_or_episode_changed")
    left_meta, right_meta = left.metadata, right.metadata
    for fields, label in ((
        (("episode_uid", "episode_id", "episode_date"), "episode_or_date_changed"),
        (("speaker", "speakers"), "speaker_changed"),
    )):
        if any(left_meta.get(key) != right_meta.get(key) for key in fields if key in left_meta or key in right_meta):
            guards.append(label)
            break
    return tuple(sorted(set(guards)))


@dataclass
class LexicalIndex:
    path: Path
    policy: dict[str, Any]
    coverage: Coverage
    units: dict[str, AnalysisUnit] = field(default_factory=dict, repr=False)
    posting_size_stats: dict[str, dict[str, int]] = field(default_factory=dict, repr=False)

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)

    @property
    def sqlite_path(self) -> Path:
        return self.path

    def exists(self) -> bool:
        return self.path.exists()


def build_lexical_index(units: Iterable[AnalysisUnit | Mapping[str, Any]], sqlite_path: str | Path, policy: Mapping[str, Any] | None = None) -> LexicalIndex:
    resolved = resolve_redundancy_policy(policy)
    normalized = {_unit(item).document_id: _unit(item) for item in units}
    if not normalized:
        raise RedundancyCandidateError("cannot build an index with no analysis units")
    path = Path(sqlite_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
            DROP TABLE IF EXISTS metadata;
            DROP TABLE IF EXISTS bands;
            DROP TABLE IF EXISTS source_postings;
            DROP TABLE IF EXISTS exact_postings;
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE bands(band_key TEXT NOT NULL, document_id TEXT NOT NULL, PRIMARY KEY(band_key, document_id));
            CREATE INDEX bands_document_idx ON bands(document_id);
            CREATE TABLE source_postings(partition_id TEXT NOT NULL, corpus_id TEXT NOT NULL, node_type TEXT NOT NULL, episode_uid TEXT, cache_fingerprint TEXT, span_id TEXT, representation_id TEXT NOT NULL, document_id TEXT NOT NULL, PRIMARY KEY(partition_id, corpus_id, node_type, episode_uid, cache_fingerprint, span_id, representation_id, document_id));
            CREATE INDEX source_postings_document_idx ON source_postings(document_id);
            CREATE TABLE exact_postings(text_hash TEXT NOT NULL, normalized_text TEXT NOT NULL, partition_id TEXT NOT NULL, corpus_id TEXT NOT NULL, node_type TEXT NOT NULL, representation_id TEXT NOT NULL, document_id TEXT NOT NULL, PRIMARY KEY(text_hash, partition_id, corpus_id, node_type, representation_id, document_id));
            CREATE INDEX exact_postings_document_idx ON exact_postings(document_id);
        """)
        for unit in sorted(normalized.values(), key=lambda item: item.document_id):
            signature = minhash_signature(token_shingles(unit.text))
            partition, corpus, node_type = _scope_key(unit)
            for band in range(16):
                values = signature[band * 4:(band + 1) * 4]
                key = hashlib.sha256(_canonical_json([LEXICAL_ALGORITHM_VERSION, partition, corpus, node_type, _representation_key(unit), band, list(values)])).hexdigest()
                connection.execute("INSERT INTO bands VALUES(?,?)", (key, unit.document_id))
            for span_id in unit.span_ids:
                connection.execute("INSERT OR IGNORE INTO source_postings VALUES(?,?,?,?,?,?,?,?)", (partition, corpus, node_type, unit.episode_uid, unit.cache_fingerprint, span_id, _representation_key(unit), unit.document_id))
            text = normalized_text(unit.text)
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            connection.execute("INSERT INTO exact_postings VALUES(?,?,?,?,?,?,?)", (text_hash, text, partition, corpus, node_type, _representation_key(unit), unit.document_id))
        for key, value in (("algorithm", LEXICAL_ALGORITHM_VERSION), ("posting_cap", str(resolved["posting_cap"])), ("unit_count", str(len(normalized)))):
            connection.execute("INSERT INTO metadata VALUES(?,?)", (key, value))
        connection.commit()
    finally:
        connection.close()
    coverage = Coverage(len(normalized), {"lexical": True, "structural": bool(resolved["structural_enabled"])}, {}, {"lexical": resolved["lexical_neighbors"], "structural": resolved["structural_neighbors"]}, {})
    return LexicalIndex(path, resolved, coverage, normalized)


def _load_units(index: LexicalIndex | str | Path, units: Iterable[AnalysisUnit | Mapping[str, Any]] | None) -> dict[str, AnalysisUnit]:
    if isinstance(index, LexicalIndex) and index.units:
        return dict(index.units)
    return {_unit(item).document_id: _unit(item) for item in (units or [])}


def _band_key(unit: AnalysisUnit, band: int, signature: tuple[int, ...]) -> str:
    partition, corpus, node_type = _scope_key(unit)
    return hashlib.sha256(_canonical_json([LEXICAL_ALGORITHM_VERSION, partition, corpus, node_type, _representation_key(unit), band, list(signature[band * 4:(band + 1) * 4])])).hexdigest()


def _scores(left: AnalysisUnit, right: AnalysisUnit) -> tuple[dict[str, float], tuple[str, ...]]:
    left_tokens, right_tokens = set(_tokens(left.text)), set(_tokens(right.text))
    left_shingles, right_shingles = token_shingles(left.text), token_shingles(right.text)
    union = left_shingles | right_shingles
    intersection = left_shingles & right_shingles
    jaccard = len(intersection) / len(union) if union else 0.0
    text_left = len(intersection) / len(left_shingles) if left_shingles else 0.0
    text_right = len(intersection) / len(right_shingles) if right_shingles else 0.0
    scores = {"jaccard": jaccard, "text_containment_left": text_left, "text_containment_right": text_right}
    left_spans, right_spans = set(left.span_ids), set(right.span_ids)
    if left_spans and right_spans:
        span_intersection = left_spans & right_spans
        scores.update({"source_iou": len(span_intersection) / len(left_spans | right_spans) if left_spans | right_spans else 0.0, "source_containment_left": len(span_intersection) / len(left_spans), "source_containment_right": len(span_intersection) / len(right_spans)})
    return scores, material_guards(left, right)


def lexical_and_source_candidates(unit: AnalysisUnit | Mapping[str, Any], index: LexicalIndex | str | Path, policy: Mapping[str, Any] | None = None, *, units: Iterable[AnalysisUnit | Mapping[str, Any]] | None = None) -> list[CandidatePair]:
    resolved = resolve_redundancy_policy(policy or (index.policy if isinstance(index, LexicalIndex) else None))
    current = _unit(unit)
    inventory = _load_units(index, units)
    if current.document_id not in inventory:
        inventory[current.document_id] = current
    connection = sqlite3.connect(Path(index.path if isinstance(index, LexicalIndex) else index))
    candidates: dict[tuple[str, str], CandidatePair] = {}
    posting_truncations = 0
    source_posting_truncations = 0

    def record_posting(channel: str, size: int, truncated: bool) -> None:
        if not isinstance(index, LexicalIndex):
            return
        stats = index.posting_size_stats.setdefault(channel, {"lookups": 0, "total_postings": 0, "max_posting": 0, "truncated_lookups": 0})
        stats["lookups"] += 1
        stats["total_postings"] += int(size)
        stats["max_posting"] = max(stats["max_posting"], int(size))
        if truncated:
            stats["truncated_lookups"] += 1

    try:
        signature = minhash_signature(token_shingles(current.text))
        for band in range(16):
            key = _band_key(current, band, signature)
            rows = connection.execute("SELECT document_id FROM bands WHERE band_key=? ORDER BY document_id", (key,)).fetchall()
            truncated = len(rows) > resolved["posting_cap"]
            record_posting("minhash_band", len(rows), truncated)
            if truncated:
                posting_truncations += 1
            for (other_id,) in rows[:resolved["posting_cap"]]:
                if other_id == current.document_id or other_id not in inventory:
                    continue
                other = inventory[other_id]
                if not _pair_scope(current, other):
                    continue
                scores, guards = _scores(current, other)
                lexical_gate = scores["jaccard"] >= resolved["pair_jaccard_gate"] or max(scores["text_containment_left"], scores["text_containment_right"]) >= resolved["pair_containment_gate"]
                if resolved["lexical_enabled"] and lexical_gate:
                    key_pair = tuple(sorted((current.document_id, other_id)))
                    old = candidates.get(key_pair)
                    candidates[key_pair] = CandidatePair(key_pair[0], key_pair[1], tuple(set((old.channels if old else ()) + ("lexical",))), {**(old.scores if old else {}), **scores}, {**(old.channel_ranks if old else {}), "lexical": 1}, guards)
        text = normalized_text(current.text)
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        partition, corpus, node_type = _scope_key(current)
        representation_id = _representation_key(current)
        exact_scope = (text_hash, partition, corpus, node_type, representation_id)
        exact_count = int(connection.execute("SELECT COUNT(*) FROM exact_postings WHERE text_hash=? AND partition_id=? AND corpus_id=? AND node_type=? AND representation_id=?", exact_scope).fetchone()[0])
        record_posting("exact_text", exact_count, exact_count > resolved["posting_cap"])
        if exact_count > 1:
            star_row = connection.execute("SELECT document_id, normalized_text FROM exact_postings WHERE text_hash=? AND partition_id=? AND corpus_id=? AND node_type=? AND representation_id=? ORDER BY document_id LIMIT 1", exact_scope).fetchone()
            star = str(star_row[0]) if star_row and str(star_row[1]) == text else ""
            if star and star in inventory and _pair_scope(current, inventory[star]):
                exact_ids = [star]
                if current.document_id == star:
                    exact_ids = [str(item[0]) for item in connection.execute("SELECT document_id, normalized_text FROM exact_postings WHERE text_hash=? AND partition_id=? AND corpus_id=? AND node_type=? AND representation_id=? ORDER BY document_id", exact_scope).fetchall() if str(item[1]) == text and str(item[0]) in inventory]
                for other_id in exact_ids:
                    if other_id == current.document_id or current.document_id != star and other_id != star or not _pair_scope(current, inventory[other_id]):
                        continue
                    left, right = sorted((current.document_id, other_id))
                    scores, guards = _scores(inventory[left], inventory[right])
                    old = candidates.get((left, right))
                    candidates[(left, right)] = CandidatePair(left, right, tuple(set((old.channels if old else ()) + ("exact_text",))), {**(old.scores if old else {}), **scores, "exact_text": 1.0}, {**(old.channel_ranks if old else {}), "exact_text": 1}, guards)
        if resolved["structural_enabled"] and current.span_ids:
            span_ids = set(current.span_ids)
            placeholders = ",".join("?" for _ in span_ids)
            params = [partition, corpus, node_type, current.episode_uid, current.cache_fingerprint, representation_id, *sorted(span_ids)]
            rows = connection.execute(f"SELECT DISTINCT document_id FROM source_postings WHERE partition_id=? AND corpus_id=? AND node_type=? AND episode_uid=? AND cache_fingerprint=? AND representation_id=? AND span_id IN ({placeholders}) ORDER BY document_id", params).fetchall()
            truncated = len(rows) > resolved["posting_cap"]
            record_posting("source_span", len(rows), truncated)
            if truncated:
                source_posting_truncations += 1
                rows = rows[:resolved["posting_cap"]]
            for (other_id,) in rows:
                if other_id == current.document_id or other_id not in inventory:
                    continue
                other = inventory[other_id]
                if not _pair_scope(current, other):
                    continue
                scores, guards = _scores(current, other)
                if not any(scores.get(key, 0.0) > 0 for key in ("source_iou", "source_containment_left", "source_containment_right")):
                    continue
                left, right = sorted((current.document_id, other_id))
                old = candidates.get((left, right))
                candidates[(left, right)] = CandidatePair(left, right, tuple(set((old.channels if old else ()) + ("structural",))), {**(old.scores if old else {}), **scores}, {**(old.channel_ranks if old else {}), "structural": 1}, guards)
    finally:
        connection.close()
    if isinstance(index, LexicalIndex):
        if posting_truncations:
            index.coverage.posting_truncations["minhash_band"] = index.coverage.posting_truncations.get("minhash_band", 0) + posting_truncations
        if source_posting_truncations:
            index.coverage.posting_truncations["source_span"] = index.coverage.posting_truncations.get("source_span", 0) + source_posting_truncations
        lexical = [pair for pair in candidates.values() if "lexical" in pair.channels]
        structural = [pair for pair in candidates.values() if "structural" in pair.channels]
        lexical.sort(key=lambda pair: (-max(float(pair.scores.get("jaccard", 0.0)), float(pair.scores.get("text_containment_left", 0.0)), float(pair.scores.get("text_containment_right", 0.0))), pair.left_id, pair.right_id))
        structural.sort(key=lambda pair: (-max(float(pair.scores.get("source_containment_left", 0.0)), float(pair.scores.get("source_containment_right", 0.0))), pair.left_id, pair.right_id))
        allowed = {pair.candidate_id for pair in lexical[:resolved["lexical_neighbors"]]} | {pair.candidate_id for pair in structural[:resolved["structural_neighbors"]]} | {pair.candidate_id for pair in candidates.values() if "exact_text" in pair.channels}
        candidates = {key: pair for key, pair in candidates.items() if pair.candidate_id in allowed}
        index.coverage.capped_neighbors["lexical"] = max(index.coverage.capped_neighbors.get("lexical", 0), len(lexical) - len(lexical[:resolved["lexical_neighbors"]]))
        index.coverage.capped_neighbors["structural"] = max(index.coverage.capped_neighbors.get("structural", 0), len(structural) - len(structural[:resolved["structural_neighbors"]]))
        index.coverage = Coverage(
            index.coverage.unit_count,
            index.coverage.channel_available,
            index.coverage.posting_truncations,
            index.coverage.capped_neighbors,
            index.coverage.skipped_reasons,
            index.posting_size_stats,
        )
    return sorted(candidates.values(), key=lambda pair: (pair.left_id, pair.right_id))


@dataclass
class DenseIndex:
    vectors: dict[str, tuple[float, ...]]
    units: dict[str, AnalysisUnit]
    policy: dict[str, Any]
    backend: str = "in_memory"
    collection: Any | None = None
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)


def _cosine(left: Iterable[float], right: Iterable[float]) -> float:
    left_values, right_values = tuple(left), tuple(right)
    if len(left_values) != len(right_values):
        raise RedundancyCandidateError("vectors have different dimensions")
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if not left_norm or not right_norm:
        raise RedundancyCandidateError("zero-norm vector is unavailable for cosine")
    return sum(a * b for a, b in zip(left_values, right_values)) / (left_norm * right_norm)


def build_dense_candidate_index(units: Iterable[AnalysisUnit | Mapping[str, Any]], vectors: Mapping[str, Iterable[float]], policy: Mapping[str, Any] | None = None, *, require_chroma: bool = False) -> DenseIndex:
    resolved = resolve_redundancy_policy(policy)
    inventory = {_unit(item).document_id: _unit(item) for item in units}
    parsed: dict[str, tuple[float, ...]] = {}
    dimension: int | None = None
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for key, value in vectors.items():
        try:
            vector = tuple(float(item) for item in value)
        except (TypeError, ValueError):
            skip("invalid_vector")
            continue
        if not vector:
            skip("empty_vector")
            continue
        if any(not math.isfinite(item) for item in vector):
            skip("nonfinite_vector")
            continue
        if dimension is None:
            dimension = len(vector)
        if len(vector) != dimension:
            skip("dimension_mismatch")
            continue
        if not any(abs(item) > 0 for item in vector):
            skip("zero_norm_vector")
            continue
        if str(key) not in inventory:
            skip("vector_without_analysis_unit")
            continue
        parsed[str(key)] = vector
    collection = None
    backend = "in_memory"
    if require_chroma:
        try:
            import chromadb
        except ModuleNotFoundError as exc:
            raise RedundancyCandidateError("dense assessment requested but chromadb is not installed") from exc
        try:
            client_factory = getattr(chromadb, "EphemeralClient", None) or getattr(chromadb, "Client", None)
            if client_factory is None:
                raise RedundancyCandidateError("installed chromadb has no private client")
            client = client_factory()
            collection = client.get_or_create_collection(name="redundancy_candidate_index", embedding_function=None)
            ids = sorted(key for key in parsed if key in inventory)
            if ids:
                collection.add(
                    ids=ids,
                    embeddings=[list(parsed[key]) for key in ids],
                    metadatas=[{"partition_id": _scope_key(inventory[key])[0], "corpus_id": _scope_key(inventory[key])[1], "node_type": _scope_key(inventory[key])[2], "representation_id": _representation_key(inventory[key])} for key in ids],
                )
            backend = "chroma"
        except Exception as exc:
            raise RedundancyCandidateError(f"could not build private dense candidate index: {type(exc).__name__}") from exc
    missing = set(inventory) - set(parsed)
    if missing:
        skipped["missing_vector"] = len(missing)
    configuration = {
        "backend": backend,
        "metric": "cosine",
        "query_overfetch": int(resolved["dense_neighbors"]) + 1,
        "neighbor_cap": int(resolved["dense_neighbors"]),
        "embedding_function": None,
    }
    return DenseIndex(parsed, inventory, resolved, backend, collection, skipped, configuration)


def dense_candidates(unit: AnalysisUnit | Mapping[str, Any], index: DenseIndex) -> list[CandidatePair]:
    current = _unit(unit)
    if current.document_id not in index.vectors:
        return []
    candidate_ids: list[str]
    if index.collection is not None:
        partition, corpus, node_type = _scope_key(current)
        representation_id = _representation_key(current)
        try:
            result = index.collection.query(
                query_embeddings=[list(index.vectors[current.document_id])],
                n_results=min(len(index.vectors), int(index.policy["dense_neighbors"]) + 1),
                where={"$and": [{"partition_id": partition}, {"corpus_id": corpus}, {"node_type": node_type}, {"representation_id": representation_id}]},
                include=["distances"],
            )
            candidate_ids = [str(value) for value in ((result.get("ids") or [[]])[0] or [])]
        except Exception as exc:
            raise RedundancyCandidateError(f"private dense candidate query failed: {type(exc).__name__}") from exc
    else:
        candidate_ids = sorted(index.vectors)
    values: list[tuple[float, str]] = []
    for other_id in candidate_ids:
        if other_id == current.document_id or other_id not in index.units:
            continue
        other_vector = index.vectors.get(other_id)
        if other_vector is None:
            continue
        other = index.units[other_id]
        if not _pair_scope(current, other):
            continue
        try:
            score = _cosine(index.vectors[current.document_id], other_vector)
        except RedundancyCandidateError:
            continue
        if score >= index.policy["pair_cosine_gate"]:
            values.append((score, other_id))
    values.sort(key=lambda item: (-item[0], item[1]))
    return [CandidatePair(*sorted((current.document_id, other_id)), channels=("dense",), scores={"cosine": score}, channel_ranks={"dense": rank + 1}, guards=material_guards(current, index.units[other_id])) for rank, (score, other_id) in enumerate(values[:index.policy["dense_neighbors"]])]


def generate_candidate_snapshot(units: Iterable[AnalysisUnit | Mapping[str, Any]], policy: Mapping[str, Any] | None, output_path: str | Path, *, vectors: Mapping[str, Iterable[float]] | None = None, sqlite_path: str | Path | None = None, require_chroma: bool | None = None) -> dict[str, Any]:
    resolved = resolve_redundancy_policy(policy)
    materialized = [_unit(item) for item in units]
    db_path = Path(sqlite_path or (Path(output_path).with_suffix(".sqlite3"))).resolve()
    index = build_lexical_index(materialized, db_path, resolved)
    merged: dict[tuple[str, str], CandidatePair] = {}
    for unit in materialized:
        for pair in lexical_and_source_candidates(unit, index, resolved):
            key = (pair.left_id, pair.right_id)
            prior = merged.get(key)
            if not prior:
                merged[key] = pair
            else:
                merged[key] = CandidatePair(pair.left_id, pair.right_id, tuple(set(prior.channels) | set(pair.channels)), {**prior.scores, **pair.scores}, {**prior.channel_ranks, **pair.channel_ranks}, tuple(set(prior.guards) | set(pair.guards)))
    channel_available = {"lexical": bool(resolved["lexical_enabled"]), "structural": bool(resolved["structural_enabled"]), "dense": False}
    skipped_reasons = dict(index.coverage.skipped_reasons)
    backend_configuration: dict[str, Any] = {"lexical_algorithm": LEXICAL_ALGORITHM_VERSION, "posting_cap": int(resolved["posting_cap"])}
    candidate_backend = "lexical_sqlite"
    if resolved["dense_enabled"] and vectors is not None:
        dense_requires_chroma = bool(require_chroma) if require_chroma is not None else True
        dense_index = build_dense_candidate_index(materialized, vectors, resolved, require_chroma=dense_requires_chroma)
        channel_available["dense"] = True
        candidate_backend = dense_index.backend
        for reason, count in dense_index.skipped_reasons.items():
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + count
        backend_configuration["dense"] = dict(dense_index.configuration)
        for unit in materialized:
            for pair in dense_candidates(unit, dense_index):
                key = (pair.left_id, pair.right_id)
                prior = merged.get(key)
                merged[key] = pair if not prior else CandidatePair(pair.left_id, pair.right_id, tuple(set(prior.channels) | set(pair.channels)), {**prior.scores, **pair.scores}, {**prior.channel_ranks, **pair.channel_ranks}, tuple(set(prior.guards) | set(pair.guards)))
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [pair.as_dict() for pair in sorted(merged.values(), key=lambda item: (item.left_id, item.right_id))]
    output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest()
    coverage = Coverage(len(materialized), channel_available, dict(index.coverage.posting_truncations), dict(index.coverage.capped_neighbors), skipped_reasons, dict(index.coverage.posting_size_stats))
    return {"path": str(output), "candidate_count": len(rows), "candidates": rows, "coverage": coverage.as_dict(), "snapshot_hash": digest, "backend": candidate_backend, "configuration": backend_configuration}


__all__ = ["DenseIndex", "LexicalIndex", "RedundancyCandidateError", "build_dense_candidate_index", "build_lexical_index", "dense_candidates", "generate_candidate_snapshot", "lexical_and_source_candidates", "material_guards", "minhash_signature", "normalized_text", "token_shingles"]
