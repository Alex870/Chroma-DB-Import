"""Validated v2 release inventory for semantic redundancy analysis."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .dedup_artifacts import validate_dedup_artifacts
from .redundancy_chroma import close_chroma_client
from .redundancy_models import AnalysisUnit, Scope


class RedundancyInventoryError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _vector(value: Any, dimension: int | None = None) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise RedundancyInventoryError("stored vector is missing or not a non-empty array")
    result = tuple(float(item) for item in value)
    if any(not math.isfinite(item) for item in result):
        raise RedundancyInventoryError("stored vector contains a non-finite value")
    if dimension is not None and len(result) != dimension:
        raise RedundancyInventoryError("stored vectors have inconsistent dimensions")
    return result


@dataclass
class Inventory:
    scope: Scope
    units: list[AnalysisUnit]
    occurrences: list[dict[str, Any]]
    vectors: dict[str, tuple[float, ...]]
    hashes: dict[str, str]
    release: dict[str, Any]
    requires_v2_evidence: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope.as_dict(),
            "units": [unit.as_dict() for unit in self.units],
            "occurrences": list(self.occurrences),
            "vectors": {key: list(value) for key, value in self.vectors.items()},
            "hashes": dict(self.hashes),
            "release": self.release,
            "requires_v2_evidence": self.requires_v2_evidence,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


def _load_source(base_export: Any) -> tuple[Path | None, dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    if isinstance(base_export, Mapping):
        release = dict(base_export.get("release") or base_export.get("release_manifest") or {})
        rows = [dict(row) for row in (base_export.get("occurrences") or base_export.get("rows") or [])]
        raw_vectors = base_export.get("vectors") or {}
        vectors = dict(raw_vectors.get("vectors") or {}) if isinstance(raw_vectors, Mapping) and isinstance(raw_vectors.get("vectors"), Mapping) else dict(raw_vectors)
        return None, release, rows, vectors
    root = Path(base_export).expanduser().resolve()
    release_path = root / "release.json"
    if not release_path.is_file():
        raise RedundancyInventoryError("base export is missing release.json")
    try:
        release = json.loads(release_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RedundancyInventoryError("base release metadata is invalid") from exc
    if not isinstance(release, dict):
        raise RedundancyInventoryError("base release metadata must be an object")
    import_manifest_path = root / "import_manifest.json"
    if import_manifest_path.is_file():
        try:
            import_manifest = json.loads(import_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RedundancyInventoryError("base import manifest is invalid") from exc
        if isinstance(import_manifest, dict):
            representation = import_manifest.get("representation")
            config = import_manifest.get("config")
            if isinstance(representation, Mapping):
                release["_analysis_representation"] = dict(representation)
            elif isinstance(config, Mapping):
                release["_analysis_representation"] = {"contextualization": config.get("contextualization")}
    reference = release.get("dedup") if isinstance(release.get("dedup"), Mapping) else {}
    ledger_name = reference.get("ledger_path") or "dedup_occurrences.jsonl"
    ledger_path = root / str(ledger_name)
    if not ledger_path.is_file():
        return root, release, [], {}
    rows: list[dict[str, Any]] = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise RedundancyInventoryError("v2 ledger row is not an object")
            rows.append(row)
    vectors_path = root / "vectors.json"
    vectors = json.loads(vectors_path.read_text(encoding="utf-8")) if vectors_path.is_file() else {}
    if isinstance(vectors, Mapping) and isinstance(vectors.get("vectors"), Mapping):
        vectors = dict(vectors["vectors"])
    return root, release, rows, vectors if isinstance(vectors, dict) else {}


def load_inventory(base_export: Any, *, inspect_vectors: bool = True) -> Inventory:
    root, release, rows, supplied_vectors = _load_source(base_export)
    tracked_names = ("release.json", "dedup_manifest.json", "dedup_occurrences.jsonl", "import_manifest.json")
    initial_hashes = {name: _sha256_file(root / name) for name in tracked_names if root is not None and (root / name).is_file()}
    if release.get("release_contract_version") != "chroma-export-release-v2" or not isinstance(release.get("dedup"), Mapping):
        raise RedundancyInventoryError("requires_v2_evidence: complete managed v2 dedup ledger is required")
    if root is not None:
        try:
            validated = validate_dedup_artifacts(root, release)
        except Exception as exc:
            raise RedundancyInventoryError(f"invalid managed v2 evidence: {exc}") from exc
        rows = [dict(row) for row in validated["rows"]]
    if not rows:
        raise RedundancyInventoryError("managed v2 ledger contains no eligible occurrences")
    if root is not None and inspect_vectors and not supplied_vectors and not (root / "chroma.sqlite3").is_file():
        raise RedundancyInventoryError("requires_chroma_evidence: base export has no inspectable stored vectors")
    if root is not None and inspect_vectors and (root / "chroma.sqlite3").is_file():
        client = None
        try:
            import chromadb
            # Chroma may migrate its SQLite schema when opened. Read a
            # verified private snapshot so a supposedly read-only assessment
            # cannot mutate the historical release in place.
            with tempfile.TemporaryDirectory(dir=str(root.parent), prefix=".redundancy-inventory-") as snapshot_dir:
                snapshot_root = Path(snapshot_dir) / "export"
                shutil.copytree(root, snapshot_root)
                client = chromadb.PersistentClient(path=str(snapshot_root))
                collection_name = str(release.get("collection_name") or "")
                if collection_name:
                    collection = client.get_collection(collection_name)
                else:
                    names = [str(getattr(item, "name", item)) for item in client.list_collections()]
                    if not names:
                        raise RedundancyInventoryError("Chroma export has no collection")
                    collection = client.get_collection(names[0])
                id_payload = collection.get(include=[])
                stored_ids = {str(item) for item in (id_payload.get("ids") or [])}
                expected_ids = {str(row.get("document_id")) for row in rows if row.get("storage_status") == "retained"}
                if stored_ids != expected_ids:
                    raise RedundancyInventoryError("actual Chroma stored IDs disagree with the validated v2 ledger")
                supplied_vectors = {}
                ordered_ids = sorted(expected_ids)
                for start in range(0, len(ordered_ids), 1000):
                    page = collection.get(ids=ordered_ids[start:start + 1000], include=["embeddings"])
                    supplied_vectors.update({str(key): value for key, value in zip(page.get("ids") or [], page.get("embeddings") or [])})
        except RedundancyInventoryError:
            raise
        except ModuleNotFoundError as exc:
            raise RedundancyInventoryError("requires_chroma_evidence: Chroma is required to inspect stored vectors") from exc
        except Exception as exc:
            raise RedundancyInventoryError(f"could not inspect actual Chroma stored IDs: {type(exc).__name__}") from exc
        finally:
            close_chroma_client(client)
    try:
        scope = Scope(
            str(release.get("partition_id") or (release.get("partition") or {}).get("partition_id") or ""),
            str(release.get("corpus_id") or (release.get("partition") or {}).get("corpus_id") or ""),
            str(release.get("release_id") or release.get("downstream_release_id") or ""),
            str(release.get("representation_id") or ""),
        )
    except (TypeError, ValueError) as exc:
        raise RedundancyInventoryError("base release scope is incomplete") from exc
    occurrences: list[dict[str, Any]] = []
    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda item: str(item.get("document_id") or "")):
        document_id = str(row.get("document_id") or "")
        if not document_id:
            raise RedundancyInventoryError("v2 ledger contains an empty document ID")
        metadata = dict(row.get("producer_metadata") or row.get("metadata") or {})
        occurrence = {
            "document_id": document_id,
            "page_content": str(row.get("page_content") or row.get("text") or ""),
            "text": str(row.get("page_content") or row.get("text") or ""),
            "metadata": metadata,
            "producer_metadata": metadata,
            "partition_id": str(row.get("partition_id") or scope.partition_id),
            "corpus_id": str(row.get("corpus_id") or scope.corpus_id),
            "representation_id": str(row.get("representation_id") or scope.representation_id),
            "episode_uid": str(row.get("episode_uid") or metadata.get("episode_uid") or ""),
            "verified_cache_fingerprint": str(row.get("verified_cache_fingerprint") or ""),
            "node_type": str(metadata.get("node_type") or row.get("node_type") or ""),
            "source_span_ids": list(row.get("source_span_ids") or metadata.get("source_span_ids") or ([metadata.get("source_span_id")] if metadata.get("source_span_id") else [])),
            "embedding_input_hash": row.get("embedding_input_hash"),
            "embedding_input": row.get("embedding_input") if isinstance(row.get("embedding_input"), str) else None,
            "embedding_fingerprint": row.get("embedding_fingerprint") or "",
            "normalized_text_hash": row.get("normalized_text_hash") or "",
            "duplicate_group_id": row.get("duplicate_group_id"),
            "alias_target_id": row.get("alias_target_id"),
            "storage_status": row.get("storage_status", "retained"),
        }
        if occurrence["partition_id"] != scope.partition_id or occurrence["corpus_id"] != scope.corpus_id or occurrence["representation_id"] != scope.representation_id:
            raise RedundancyInventoryError("v2 ledger row is outside the release scope")
        occurrences.append(occurrence)
        group = str(occurrence.get("duplicate_group_id") or "")
        by_group.setdefault(group or document_id, []).append(occurrence)
    units: list[AnalysisUnit] = []
    for group_id, members in sorted(by_group.items()):
        members = sorted(members, key=lambda row: str(row["document_id"]))
        canonical = next((row for row in members if row.get("storage_status") == "retained"), members[0])
        unit_metadata = dict(canonical["metadata"])
        unit_metadata.setdefault("partition_id", scope.partition_id)
        unit_metadata.setdefault("corpus_id", scope.corpus_id)
        unit_metadata.setdefault("representation_id", scope.representation_id)
        unit_metadata.setdefault("episode_uid", canonical.get("episode_uid") or "")
        units.append(AnalysisUnit(
            document_id=str(canonical["document_id"]),
            occurrence_ids=tuple(str(row["document_id"]) for row in members),
            text=str(canonical["text"]),
            metadata=unit_metadata,
            node_type=str(canonical["node_type"]),
            episode_uid=str(canonical["episode_uid"]),
            cache_fingerprint=str(canonical["verified_cache_fingerprint"]),
            span_ids=tuple(str(item) for item in canonical["source_span_ids"] if str(item)),
            embedding_fingerprint=str(canonical.get("embedding_fingerprint") or ""),
        ))
    vectors: dict[str, tuple[float, ...]] = {}
    if inspect_vectors:
        # Accept either direct document IDs or a Chroma-like payload mapping.
        raw_vectors = supplied_vectors
        if isinstance(raw_vectors.get("ids"), list):
            raw_vectors = {str(key): value for key, value in zip(raw_vectors.get("ids") or [], raw_vectors.get("embeddings") or [])}
        dimension: int | None = None
        for key, value in raw_vectors.items():
            try:
                parsed = _vector(value, dimension)
            except (TypeError, ValueError) as exc:
                raise RedundancyInventoryError(f"invalid vector for {key}: {exc}") from exc
            dimension = dimension or len(parsed)
            vectors[str(key)] = parsed
        for row in occurrences:
            if row["storage_status"] == "retained" and row["document_id"] not in vectors:
                raise RedundancyInventoryError(f"stored vector is missing for retained occurrence {row['document_id']}")
        # Aliases resolve to their validated retained canonical only after all
        # vector IDs have been checked.
        by_id = {row["document_id"]: row for row in occurrences}
        for row in occurrences:
            target = str(row.get("alias_target_id") or "")
            if target and target in vectors:
                vectors[row["document_id"]] = vectors[target]
            elif row["storage_status"] == "retained" and row["document_id"] not in vectors:
                raise RedundancyInventoryError(f"retained vector inventory is incomplete for {row['document_id']}")
    hashes = {}
    if root is not None:
        after = {name: _sha256_file(root / name) for name in initial_hashes if (root / name).is_file()}
        if initial_hashes != after:
            raise RedundancyInventoryError("base export changed while it was being analyzed")
        hashes = after
    return Inventory(scope, units, occurrences, vectors, hashes, release)


__all__ = ["Inventory", "RedundancyInventoryError", "load_inventory"]
