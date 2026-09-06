"""Portable evidence ledger and exact embedding-input representative maps."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

from .representation import embedding_text
from .redundancy_inventory import Inventory
from .redundancy_models import AnalysisUnit, Scope
from .redundancy_chroma import close_chroma_client


class RedundancyEvidenceError(ValueError):
    pass


def _sha256(value: bytes | str) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _unit(value: AnalysisUnit | Mapping[str, Any]) -> AnalysisUnit:
    return value if isinstance(value, AnalysisUnit) else AnalysisUnit.from_mapping(value)


def write_evidence_inventory(occurrences: Iterable[Mapping[str, Any]], output_path: str | Path, *, scope: Scope | Mapping[str, Any], base_fingerprint: str = "") -> dict[str, Any]:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved_scope = scope if isinstance(scope, Scope) else Scope.from_mapping(scope)
    rows = [dict(row) for row in occurrences]
    seen: set[str] = set()
    encoded: list[bytes] = []
    for row in sorted(rows, key=lambda item: str(item.get("document_id") or "")):
        document_id = str(row.get("document_id") or "")
        if not document_id or document_id in seen:
            raise RedundancyEvidenceError("evidence occurrence IDs must be unique and non-empty")
        seen.add(document_id)
        metadata = dict(row.get("metadata") or row.get("producer_metadata") or {})
        row_scope = {
            "partition_id": str(row.get("partition_id") or metadata.get("partition_id") or resolved_scope.partition_id),
            "corpus_id": str(row.get("corpus_id") or metadata.get("corpus_id") or resolved_scope.corpus_id),
            "representation_id": str(row.get("representation_id") or metadata.get("representation_id") or resolved_scope.representation_id),
        }
        if row_scope != {key: resolved_scope.as_dict()[key] for key in row_scope}:
            raise RedundancyEvidenceError("evidence occurrence is outside the requested scope")
        portable = {
            "document_id": document_id,
            "node_id": str(row.get("node_id") or ""),
            "page_content": str(row.get("page_content") if row.get("page_content") is not None else row.get("text") or ""),
            "text": str(row.get("text") if row.get("text") is not None else row.get("page_content") or ""),
            "metadata": metadata,
            "partition_id": str(row.get("partition_id") or resolved_scope.partition_id),
            "corpus_id": str(row.get("corpus_id") or resolved_scope.corpus_id),
            "representation_id": str(row.get("representation_id") or resolved_scope.representation_id),
            "episode_uid": str(row.get("episode_uid") or ""),
            "verified_cache_fingerprint": str(row.get("verified_cache_fingerprint") or row.get("cache_fingerprint") or ""),
            "node_type": str(row.get("node_type") or (row.get("metadata") or {}).get("node_type") or ""),
            "source_span_ids": sorted(str(item) for item in (row.get("source_span_ids") or []) if str(item)),
            "source_span_hash": row.get("source_span_hash"),
            "embedding_input_hash": row.get("embedding_input_hash"),
            "embedding_fingerprint": row.get("embedding_fingerprint") or "",
            "duplicate_group_id": row.get("duplicate_group_id"),
            "alias_target_id": row.get("alias_target_id"),
            "representative_id": row.get("representative_id"),
            "preferred_canonical_id": row.get("preferred_canonical_id"),
            "decision_method": row.get("decision_method"),
            "decision_reason": row.get("decision_reason"),
            "storage_status": row.get("storage_status", "retained"),
            "scope": resolved_scope.as_dict(),
        }
        encoded.append((json.dumps(portable, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    path.write_bytes(b"".join(encoded))
    return {"path": str(path), "row_count": len(encoded), "sha256": _sha256(path.read_bytes()), "base_fingerprint": base_fingerprint, "scope": resolved_scope.as_dict()}


def _speaker_values(metadata: Mapping[str, Any]) -> list[str]:
    raw = metadata.get("speakers")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [part.strip() for part in raw.split(",")]
    values = []
    if isinstance(raw, list):
        values.extend(str(item).strip() for item in raw if str(item).strip())
    if metadata.get("speaker"):
        values.append(str(metadata["speaker"]).strip())
    return sorted(set(item for item in values if item))


def build_occurrence_index(evidence_path: str | Path, sqlite_path: str | Path) -> dict[str, Any]:
    evidence = Path(evidence_path).expanduser().resolve()
    database = Path(sqlite_path).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    rows: list[dict[str, Any]] = []
    try:
        for line in evidence.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise RedundancyEvidenceError("evidence row is not an object")
                rows.append(row)
        connection.executescript("""
            PRAGMA foreign_keys=ON;
            DROP TABLE IF EXISTS speakers;
            DROP TABLE IF EXISTS representatives;
            DROP TABLE IF EXISTS occurrences;
            CREATE TABLE occurrences(document_id TEXT PRIMARY KEY, episode_uid TEXT NOT NULL, node_type TEXT NOT NULL, episode_date TEXT, metadata_json TEXT NOT NULL, text TEXT NOT NULL, representative_id TEXT, embedding_fingerprint TEXT, embedding_input_hash TEXT);
            CREATE TABLE speakers(document_id TEXT NOT NULL, speaker TEXT NOT NULL, PRIMARY KEY(document_id, speaker), FOREIGN KEY(document_id) REFERENCES occurrences(document_id));
            CREATE TABLE representatives(representative_id TEXT PRIMARY KEY, embedding_fingerprint TEXT, embedding_input_hash TEXT, member_count INTEGER NOT NULL);
            CREATE INDEX occurrences_episode_idx ON occurrences(episode_uid);
            CREATE INDEX occurrences_node_idx ON occurrences(node_type);
            CREATE INDEX occurrences_rep_idx ON occurrences(representative_id);
            CREATE INDEX occurrences_date_idx ON occurrences(episode_date);
            CREATE INDEX speakers_speaker_idx ON speakers(speaker);
        """)
        for row in rows:
            metadata = dict(row.get("metadata") or {})
            document_id = str(row.get("document_id") or "")
            connection.execute("INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?)", (document_id, str(row.get("episode_uid") or metadata.get("episode_uid") or ""), str(row.get("node_type") or metadata.get("node_type") or ""), str(metadata.get("episode_date") or ""), json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")), str(row.get("text") if row.get("text") is not None else row.get("page_content") or ""), row.get("representative_id"), row.get("embedding_fingerprint"), row.get("embedding_input_hash")))
            for speaker in _speaker_values(metadata):
                connection.execute("INSERT OR IGNORE INTO speakers VALUES(?,?)", (document_id, speaker))
        representatives: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            representative_id = str(row.get("representative_id") or row.get("alias_target_id") or row.get("document_id") or "")
            representatives.setdefault(representative_id, []).append(row)
        for representative_id, members in sorted(representatives.items()):
            first = members[0]
            connection.execute("INSERT INTO representatives VALUES(?,?,?,?)", (representative_id, first.get("embedding_fingerprint"), first.get("embedding_input_hash"), len(members)))
        connection.commit()
    except sqlite3.IntegrityError as exc:
        raise RedundancyEvidenceError("evidence rows violate occurrence index uniqueness") from exc
    finally:
        connection.close()
    return {"path": str(database), "row_count": len(rows), "sha256": _sha256(database.read_bytes())}


def build_occurrence_index_from_rows(rows: Iterable[Mapping[str, Any]], sqlite_path: str | Path) -> dict[str, Any]:
    temporary = Path(sqlite_path).expanduser().resolve().with_name(Path(sqlite_path).name + ".input.jsonl")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    try:
        return build_occurrence_index(temporary, sqlite_path)
    finally:
        temporary.unlink(missing_ok=True)


def build_representative_map(occurrences: Iterable[Mapping[str, Any]], *, vectors: Mapping[str, Iterable[float]] | None = None, embedding_inputs: Mapping[str, str] | None = None, scope: Scope | Mapping[str, Any] | None = None, contextualization: str | None = None) -> list[dict[str, Any]]:
    rows = [dict(row) for row in occurrences]
    resolved_scope = scope if isinstance(scope, Scope) else (Scope.from_mapping(scope) if scope else None)
    groups: dict[tuple[str, str, str, str, str], list[str]] = {}
    for row in rows:
        document_id = str(row.get("document_id") or "")
        if not document_id:
            raise RedundancyEvidenceError("representative map has an empty occurrence ID")
        metadata = dict(row.get("metadata") or row.get("producer_metadata") or {})
        partition_id = str(row.get("partition_id") or metadata.get("partition_id") or (resolved_scope.partition_id if resolved_scope else ""))
        corpus_id = str(row.get("corpus_id") or metadata.get("corpus_id") or (resolved_scope.corpus_id if resolved_scope else ""))
        representation = str(row.get("representation_id") or metadata.get("representation_id") or (resolved_scope.representation_id if resolved_scope else ""))
        if resolved_scope and any((partition_id, corpus_id, representation)[index] != (resolved_scope.partition_id, resolved_scope.corpus_id, resolved_scope.representation_id)[index] for index in range(3)):
            raise RedundancyEvidenceError("representative map contains an occurrence outside the requested scope")
        input_value = (embedding_inputs or {}).get(document_id)
        if input_value is None and isinstance(row.get("embedding_input"), str):
            input_value = str(row["embedding_input"])
        if input_value is None and contextualization in {"none", "minimal", "full"}:
            input_value = embedding_text(str(row.get("text") if row.get("text") is not None else row.get("page_content") or ""), dict(row.get("metadata") or row.get("producer_metadata") or {}), contextualization)
        provided_hash = str(row.get("embedding_input_hash") or "")
        actual_hash = _sha256(input_value) if input_value is not None else ""
        input_hash = actual_hash or provided_hash
        verified = bool(input_value is not None and (not provided_hash or provided_hash == actual_hash))
        if not verified:
            key = (partition_id, corpus_id, representation, "unshareable:" + document_id, document_id)
        else:
            # Hashes are an index, not the equality proof. Keep the actual
            # verified input in the grouping key so a pathological collision
            # or a producer hash mistake cannot merge different inputs.
            key = (partition_id, corpus_id, representation, input_hash, str(input_value))
        groups.setdefault(key, []).append(document_id)
    row_by_id = {str(row.get("document_id")): row for row in rows}
    representatives: dict[str, str] = {}
    for key, member_ids in groups.items():
        # Prefer a retained, non-alias occurrence as the physical
        # representative.  Audit/suppressed rows remain in the occurrence
        # ledger but must not displace the canonical vector row merely because
        # their document ID sorts first.
        representative = min(
            member_ids,
            key=lambda item: (
                1 if str(row_by_id[item].get("storage_status") or "retained") in {"audit", "suppressed_exact"} else 0,
                1 if str(row_by_id[item].get("alias_target_id") or "") else 0,
                item,
            ),
        )
        for member_id in member_ids:
            representatives[member_id] = representative
    for document_id, row in row_by_id.items():
        alias_target = str(row.get("alias_target_id") or "")
        if alias_target and alias_target in representatives:
            representatives[document_id] = representatives[alias_target]
    result: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: str(item.get("document_id") or "")):
        document_id = str(row["document_id"])
        representative_id = representatives[document_id]
        input_value = (embedding_inputs or {}).get(document_id)
        if input_value is None and isinstance(row.get("embedding_input"), str):
            input_value = str(row["embedding_input"])
        if input_value is None and contextualization in {"none", "minimal", "full"}:
            input_value = embedding_text(str(row.get("text") if row.get("text") is not None else row.get("page_content") or ""), dict(row.get("metadata") or row.get("producer_metadata") or {}), contextualization)
        provided_hash = str(row.get("embedding_input_hash") or "")
        actual_hash = _sha256(input_value) if input_value is not None else ""
        verified = bool(input_value is not None and (not provided_hash or provided_hash == actual_hash))
        input_hash = actual_hash or provided_hash
        item = {"document_id": document_id, "representative_id": representative_id, "embedding_input_hash": input_hash or None, "embedding_fingerprint": row.get("embedding_fingerprint") or None, "reason": None if verified else "unverifiable_embedding_input"}
        if resolved_scope:
            item["scope"] = resolved_scope.as_dict()
        result.append(item)
    return result


def build_compact_vectors(representative_map: Iterable[Mapping[str, Any]], vectors: Mapping[str, Iterable[float]], output_dir: str | Path, *, scope: Scope | Mapping[str, Any]) -> dict[str, Any]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    resolved_scope = scope if isinstance(scope, Scope) else Scope.from_mapping(scope)
    rows = [dict(row) for row in representative_map]
    representative_ids = sorted({str(row.get("representative_id") or "") for row in rows if str(row.get("representative_id") or "")})
    compact: dict[str, list[float]] = {}
    dimensions: set[int] = set()
    for representative_id in representative_ids:
        if representative_id not in vectors:
            raise RedundancyEvidenceError(f"missing vector for representative {representative_id}")
        vector = [float(item) for item in vectors[representative_id]]
        if not vector:
            raise RedundancyEvidenceError("compact vector cannot be empty")
        if any(not math.isfinite(item) for item in vector):
            raise RedundancyEvidenceError("compact vector contains a non-finite value")
        if not any(abs(item) > 0 for item in vector):
            raise RedundancyEvidenceError("compact vector has zero norm and is unavailable for cosine")
        compact[representative_id] = vector
        dimensions.add(len(vector))
    if len(dimensions) > 1:
        raise RedundancyEvidenceError("compact vectors have inconsistent dimensions")
    vector_path = root / "vectors.json"
    vector_path.write_text(json.dumps({"scope": resolved_scope.as_dict(), "vectors": compact}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    chroma_status: dict[str, Any]
    chroma_root = root / "chroma"
    try:
        import chromadb
    except ModuleNotFoundError:
        chroma_status = {"backend": "json", "status": "unavailable", "reason": "chromadb_not_installed"}
    else:
        try:
            chroma_root.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(chroma_root))
            collection_name = "redundancy_compact_v1"
            try:
                client.delete_collection(collection_name)
            except Exception:
                pass
            collection = client.create_collection(
                name=collection_name,
                metadata={
                    "contract": "chroma-redundancy-bundle-v1",
                    "hnsw:space": "cosine",
                    "partition_id": resolved_scope.partition_id,
                    "corpus_id": resolved_scope.corpus_id,
                    "representation_id": resolved_scope.representation_id,
                },
                embedding_function=None,
            )
            ids = sorted(compact)
            collection.add(
                ids=ids,
                embeddings=[compact[item] for item in ids],
                metadatas=[
                    {
                        "representative_id": item,
                        "partition_id": resolved_scope.partition_id,
                        "corpus_id": resolved_scope.corpus_id,
                        "representation_id": resolved_scope.representation_id,
                    }
                    for item in ids
                ],
            )
            persist = getattr(client, "persist", None)
            if callable(persist):
                persist()
            chroma_status = {
                "backend": "chroma",
                "status": "created",
                "path": "chroma",
                "collection": collection_name,
                "representative_count": len(ids),
                "dimension": next(iter(dimensions), 0),
            }
        except Exception as exc:
            raise RedundancyEvidenceError(f"compact Chroma materialization failed: {type(exc).__name__}") from exc
        finally:
            close_chroma_client(locals().get("client"))
            collection = None
            client = None
    chroma_manifest = root / "chroma_manifest.json"
    chroma_manifest.write_text(json.dumps(chroma_status, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "path": str(vector_path),
        "representative_count": len(compact),
        "dimension": next(iter(dimensions), 0),
        "sha256": _sha256(vector_path.read_bytes()),
        "scope": resolved_scope.as_dict(),
        "backend": chroma_status["backend"],
        "chroma_manifest_path": str(chroma_manifest),
        "chroma_manifest_sha256": _sha256(chroma_manifest.read_bytes()),
        "chroma": chroma_status,
    }


__all__ = ["RedundancyEvidenceError", "build_compact_vectors", "build_occurrence_index", "build_occurrence_index_from_rows", "build_representative_map", "write_evidence_inventory"]
