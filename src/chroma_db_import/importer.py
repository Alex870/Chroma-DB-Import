from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import chroma_db_import.runtime as runtime
from chroma_db_import.config import ImportConfig, resolve_path
from chroma_db_import.contract import content_fingerprint, file_fingerprint, has_text, sanitize_metadata, validate_document_items
from chroma_db_import.state import write_json
from chroma_db_import.representation import RepresentationSpec, embedding_content_hash, embedding_fingerprint, embedding_text, metadata_fingerprint
from chroma_db_import.reconciliation import plan_reconciliation, require_delete_confirmation, source_identity
from chroma_db_import.providers import EmbeddingCompatibilityError, create_embedding_provider, probe_embedding_provider
from chroma_db_import.providers import pinned_revision
from chroma_db_import.representation import recommended_batch_size
from chroma_db_import.staging import operation_id, staging_collection_name, validate_staged_records

def cache_fingerprint(path: Path) -> str:
    return file_fingerprint(path)

def load_processed_payload(cache_path: Path) -> dict[str, Any]:
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {"documents": []}

def load_processed_documents(cache_path: Path) -> list[Document]:
    runtime.load_runtime_deps()
    Document = runtime.Document
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    docs = []
    for item in payload.get("documents", []):
        if not isinstance(item, dict):
            continue
        docs.append(
            Document(
                page_content=str(item.get("page_content", "")),
                metadata=dict(item.get("metadata") or {}),
            )
        )
    return docs

def validate_documents(docs: list[Document], label: str) -> None:
    validate_document_items(docs, label).raise_for_errors(label)

def document_id(doc: Document) -> str:
    metadata = doc.metadata or {}
    return str(metadata.get("stable_document_id") or metadata.get("node_id"))

def document_content_hash(doc: Document) -> str:
    metadata = doc.metadata or {}
    payload = json.dumps({"page_content": doc.page_content, "metadata": metadata}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()

def representation_spec(config: ImportConfig, dimension: int | None = None) -> RepresentationSpec:
    model_id = "BAAI/bge-m3" if config.experimental_bge_m3 else config.embedding_model
    spec = RepresentationSpec(
        provider=config.embedding_provider,
        model_id=model_id,
        model_revision=pinned_revision(model_id, "" if config.experimental_bge_m3 else config.embedding_model_revision),
        dimension=dimension,
        normalize_embeddings=config.normalize_embeddings,
        distance_metric=config.distance_metric,
        contextualization=config.contextualization,
        output_dimension=config.output_dimension,
        matryoshka_compatible=config.matryoshka_compatible,
    )
    spec.validate()
    return spec

def document_fingerprints(doc: Document, spec: RepresentationSpec) -> dict[str, str]:
    return {
        "embedding_fingerprint": embedding_fingerprint(doc.page_content, dict(doc.metadata or {}), spec),
        "metadata_fingerprint": metadata_fingerprint(dict(doc.metadata or {})),
        "content_hash": embedding_content_hash(doc.page_content, dict(doc.metadata or {}), spec),
        "representation_id": spec.representation_id,
    }

def selected_speaker_set(config: ImportConfig) -> set[str]:
    return {speaker for speaker in (config.selected_speakers or []) if isinstance(speaker, str) and speaker.strip()}

def document_speakers(metadata: dict[str, Any]) -> set[str]:
    values = set()
    speaker = metadata.get("speaker")
    if isinstance(speaker, str) and speaker.strip() and speaker.lower() not in {"unknown", "multiple", "mixed"}:
        values.add(speaker.strip())
    speakers = metadata.get("speakers")
    if isinstance(speakers, str):
        try:
            speakers = json.loads(speakers)
        except json.JSONDecodeError:
            speakers = [part.strip() for part in speakers.split(",")]
    if isinstance(speakers, list):
        values.update(str(item).strip() for item in speakers if str(item).strip())
    return values

def should_include_document(doc: Document, config: ImportConfig) -> bool:
    selected = selected_speaker_set(config)
    if not selected:
        return True
    metadata = doc.metadata or {}
    speakers = document_speakers(metadata)
    node_type = str(metadata.get("node_type") or "")
    speaker_scope = str(metadata.get("speaker_scope") or "")
    if node_type in {"episode_thesis", "cluster_summary"} and speaker_scope != "single":
        return True
    return bool(speakers & selected)

def summarize_documents_for_plan(docs: list[Document]) -> dict[str, Any]:
    node_types = Counter(str(doc.metadata.get("node_type") or "unknown") for doc in docs)
    speakers = Counter(speaker for doc in docs for speaker in (document_speakers(doc.metadata) or {"unknown"}))
    episodes = Counter(str(doc.metadata.get("episode_id") or "unknown") for doc in docs)
    dates = [str(doc.metadata.get("episode_date")) for doc in docs if doc.metadata.get("episode_date")]
    return {
        "document_count": len(docs),
        "counts_by_node_type": dict(node_types),
        "counts_by_speaker": dict(speakers),
        "counts_by_episode": dict(episodes),
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
    }

class ChromaImporter:
    """Import validated processed documents into Chroma with resumable batches."""

    def __init__(self, config: ImportConfig, project_dir: Path):
        runtime.load_runtime_deps()
        Chroma = runtime.Chroma
        self.config = config
        self.project_dir = project_dir
        self.spec = representation_spec(config)
        self.embeddings, self.provider_diagnostics = create_embedding_provider(self.spec, config.embedding_device)
        self.persist_dir = resolve_path(project_dir, config.persist_dir)
        self.vectorstore = Chroma(
            embedding_function=self.embeddings,
            persist_directory=str(self.persist_dir),
            collection_name=config.collection_name,
        )
        existing_metadata = getattr(self.vectorstore._collection, "metadata", None) or {}
        existing_representation = str(existing_metadata.get("representation_id") or "")
        if existing_representation and existing_representation != self.spec.representation_id:
            raise EmbeddingCompatibilityError(
                "Target collection embedding representation is incompatible with the requested import. "
                "Create a new export or run an explicit migration; existing vectors were not changed."
            )
        self.embedding_dimension = detect_embedding_dimension(self.embeddings)
        self.spec = representation_spec(config, self.embedding_dimension)
        self.embedding_probe = probe_embedding_provider(self.embeddings, expected_dimension=self.embedding_dimension)
        if existing_metadata.get("embedding_dimension") not in (None, "", self.embedding_dimension):
            raise EmbeddingCompatibilityError(
                f"Target collection dimension {existing_metadata.get('embedding_dimension')} does not match provider {self.embedding_dimension}."
            )
        self.embedding_cache_dir = resolve_path(project_dir, config.embedding_cache_dir)
        self.import_state_dir = resolve_path(project_dir, config.import_state_dir)
        self.last_cache_stats = {"hits": 0, "misses": 0, "invalidations": 0, "cache_size": 0}

    def existing_ids(self, ids: list[str]) -> set[str]:
        if not self.config.skip_existing_ids or not ids:
            return set()
        existing: set[str] = set()
        batch_size = max(1, int(self.config.chroma_batch_size or self.config.import_batch_size))
        for start in range(0, len(ids), batch_size):
            batch_ids = ids[start : start + batch_size]
            try:
                payload = self.vectorstore.get(ids=batch_ids, include=[])
            except Exception:
                return set()
            existing.update(payload.get("ids") or [])
        return existing

    def existing_records(self, ids: list[str], source_cache: str) -> dict[str, dict[str, str]]:
        """Read persisted reconciliation fingerprints for current and removed source records."""
        records: dict[str, dict[str, str]] = {}
        payloads = []
        if ids:
            payloads.append(self.vectorstore._collection.get(ids=ids, include=["metadatas"]))
        try:
            payloads.append(
                self.vectorstore._collection.get(
                    where={"import_source_cache": source_cache}, include=["metadatas"]
                )
            )
        except Exception:
            # Older collections have no source-cache tag; current IDs still reconcile safely.
            pass
        for payload in payloads:
            for item_id, metadata in zip(payload.get("ids") or [], payload.get("metadatas") or []):
                metadata = metadata or {}
                records[str(item_id)] = {
                    "embedding_fingerprint": str(metadata.get("embedding_fingerprint") or ""),
                    "metadata_fingerprint": str(metadata.get("metadata_fingerprint") or ""),
                    "source_identity": str(metadata.get("source_identity") or ""),
                    "eligible_speakers": set(str(item) for item in (metadata.get("eligible_speakers") or []) if str(item)),
                }
        return records

    def batch_state_path(self, cache_path: Path) -> Path:
        return self.import_state_dir / f"{content_fingerprint(cache_path)}.batches.json"

    def load_batch_state(self, cache_path: Path) -> dict[str, Any]:
        path = self.batch_state_path(cache_path)
        if not path.exists():
            return {"completed_batches": []}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"completed_batches": []}

    def save_batch_state(self, cache_path: Path, state: dict[str, Any]) -> None:
        write_json(self.batch_state_path(cache_path), state)

    def embedding_cache_path(self, doc: Document) -> Path:
        key = hashlib.sha256(json.dumps({
            "content_hash": document_content_hash(doc),
            "representation_id": self.spec.representation_id,
        }, sort_keys=True).encode("utf-8")).hexdigest()
        return self.embedding_cache_dir / f"{key}.json"

    def cached_embeddings(self, docs: list[Document]) -> tuple[list[list[float] | None], int]:
        if not self.config.cache_embeddings:
            return [None] * len(docs), 0
        cached: list[list[float] | None] = []
        hits = 0
        invalidations = 0
        for doc in docs:
            path = self.embedding_cache_path(doc)
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    vector = payload.get("embedding")
                    expected_content_hash = document_content_hash(doc)
                    if (
                        payload.get("content_hash") == expected_content_hash
                        and payload.get("representation_id") == self.spec.representation_id
                        and isinstance(vector, list)
                        and len(vector) == self.embedding_dimension
                        and all(math.isfinite(float(value)) for value in vector)
                    ):
                        cached.append([float(value) for value in vector])
                        hits += 1
                        continue
                except Exception:
                    pass
                invalidations += 1
            cached.append(None)
        self.last_cache_stats = {"hits": hits, "misses": len(docs) - hits, "invalidations": invalidations, "cache_size": len(list(self.embedding_cache_dir.glob("*.json"))) if self.embedding_cache_dir.exists() else 0}
        return cached, hits

    def embed_documents_cached(self, docs: list[Document]) -> tuple[list[list[float]], int]:
        cached, hits = self.cached_embeddings(docs)
        missing_indexes = [idx for idx, vector in enumerate(cached) if vector is None]
        if missing_indexes:
            missing_texts = [embedding_text(docs[idx].page_content, dict(docs[idx].metadata or {}), self.spec.contextualization) for idx in missing_indexes]
            embedded = self.embeddings.embed_documents(missing_texts)
            for idx, vector in zip(missing_indexes, embedded):
                cached[idx] = vector
                if self.config.cache_embeddings:
                    write_json(
                        self.embedding_cache_path(docs[idx]),
                        {
                            "representation": self.spec.as_dict(),
                            "representation_id": self.spec.representation_id,
                            "content_hash": document_content_hash(docs[idx]),
                            "embedding_fingerprint": embedding_fingerprint(docs[idx].page_content, dict(docs[idx].metadata or {}), self.spec),
                            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                            "embedding": vector,
                        },
                    )
        return [vector or [] for vector in cached], hits

    def import_cache(self, cache_path: Path, dry_run: bool = False) -> dict[str, Any]:
        """Import one processed cache file, resuming batch progress when available."""
        docs = load_processed_documents(cache_path)
        payload = load_processed_payload(cache_path)
        validate_documents(docs, str(cache_path))
        docs = [doc for doc in docs if has_text(doc.page_content) and should_include_document(doc, self.config)]
        source_cache = str(cache_path.resolve())
        episode_id = str((docs[0].metadata if docs else {}).get("episode_id") or payload.get("episode_id") or cache_path.stem)
        source_content_fingerprint = content_fingerprint(cache_path)
        source_record_identity = source_identity(
            episode_id=episode_id,
            source_fingerprint=source_content_fingerprint,
            schema_version=str(payload.get("schema_version") or ""),
            representation_id=self.spec.representation_id,
            content_hash=source_content_fingerprint,
        )
        prepared: dict[str, Document] = {}
        current: dict[str, dict[str, str]] = {}
        for doc in docs:
            item_id = document_id(doc)
            fingerprints = document_fingerprints(doc, self.spec)
            metadata = sanitize_metadata(doc.metadata)
            metadata.update(fingerprints)
            metadata["import_source_cache"] = source_cache
            metadata["source_content_fingerprint"] = source_content_fingerprint
            metadata["source_identity"] = source_record_identity
            metadata["schema_version"] = str(payload.get("schema_version") or "")
            prepared[item_id] = Document(page_content=doc.page_content, metadata=metadata)
            current[item_id] = {**fingerprints, "source_identity": source_record_identity}
        existing = self.existing_records(list(prepared), source_cache)
        reconciliation = plan_reconciliation(current, existing)
        summary = summarize_documents_for_plan(docs)
        if dry_run:
            return {
                "documents": len(docs),
                "inserted": 0,
                "skipped_existing": len(reconciliation.unchanged),
                "reconciliation": reconciliation.as_dict(),
                "summary": summary,
                "dry_run": True,
            }

        if self.config.reconcile:
            require_delete_confirmation(reconciliation, self.config.allow_delete_missing, reconcile=True)
        pending_ids = reconciliation.added + reconciliation.changed
        pending = [prepared[item_id] for item_id in pending_ids]
        operation = operation_id("cache")
        failure_path = self.import_state_dir / "import_reports" / f"{operation}.json"
        inserted = 0
        embedding_hits = 0
        batch_state = self.load_batch_state(cache_path)
        completed_batches = set(batch_state.get("completed_batches") or [])
        batch_size = max(1, int(self.config.import_batch_size or recommended_batch_size(self.provider_diagnostics["device"])))
        staged_ids: list[str] = []
        staged_docs: list[Document] = []
        staged_embeddings: list[list[float]] = []
        for start in range(0, len(pending), batch_size):
            batch_docs = pending[start : start + batch_size]
            batch_ids = pending_ids[start : start + batch_size]
            batch_identity = "|".join(
                f"{item_id}:{current[item_id]['embedding_fingerprint']}" for item_id in batch_ids
            )
            batch_key = hashlib.sha1(batch_identity.encode("utf-8")).hexdigest()
            if not batch_docs:
                continue
            embeddings, hits = self.embed_documents_cached(batch_docs)
            embedding_hits += hits
            staged_ids.extend(batch_ids)
            staged_docs.extend(batch_docs)
            staged_embeddings.extend(embeddings)
            inserted += len(batch_docs)
            completed_batches.add(batch_key)
            batch_state["completed_batches"] = sorted(completed_batches)
            self.save_batch_state(cache_path, batch_state)
            print(f"    staged {inserted}/{len(pending)} documents")

        validation = validate_staged_records(
            staged_ids,
            [doc.page_content for doc in staged_docs],
            [doc.metadata for doc in staged_docs],
            staged_embeddings,
            expected_dimension=self.embedding_dimension,
            retrieval_ids=staged_ids[:1],
        )
        report = {
            "operation_id": operation,
            "status": "validated" if validation.valid else "failed",
            "source_cache": source_cache,
            "source_identity": source_record_identity,
            "representation_id": self.spec.representation_id,
            "embedding_probe": self.embedding_probe,
            "reconciliation": reconciliation.as_dict(),
            "embedding_cache": getattr(self, "last_cache_stats", {}),
            "staging": validation.as_dict(),
        }
        if not validation.valid:
            write_json(failure_path, report)
            raise ValueError("Staging validation failed: " + "; ".join(validation.errors[:5]))

        staging_name = staging_collection_name(self.config.collection_name, operation)
        client = getattr(self.vectorstore, "_client", None)
        stage_collection = None
        try:
            if client is not None:
                stage_collection = client.get_or_create_collection(staging_name, metadata={"representation_id": self.spec.representation_id})
                if staged_ids:
                    stage_collection.upsert(
                        ids=staged_ids,
                        documents=[doc.page_content for doc in staged_docs],
                        metadatas=[doc.metadata for doc in staged_docs],
                        embeddings=staged_embeddings,
                    )
                    smoke = stage_collection.query(query_embeddings=[staged_embeddings[0]], n_results=1, include=[])
                    validation.smoke_query_ids = list((smoke.get("ids") or [[]])[0] or [])
                    if not validation.smoke_query_ids:
                        raise ValueError("Pinned retrieval smoke query returned no staged IDs")
            removed_ids = reconciliation.removed if self.config.reconcile else []
            affected = sorted(set(reconciliation.metadata_only + removed_ids + staged_ids))
            snapshot = self.vectorstore._collection.get(ids=affected, include=["documents", "metadatas", "embeddings"]) if affected else {"ids": []}
            if reconciliation.metadata_only:
                self.vectorstore._collection.update(ids=reconciliation.metadata_only, documents=[prepared[item].page_content for item in reconciliation.metadata_only], metadatas=[prepared[item].metadata for item in reconciliation.metadata_only])
            if removed_ids:
                self.vectorstore._collection.delete(ids=removed_ids)
            if staged_ids:
                self.vectorstore._collection.upsert(ids=staged_ids, documents=[doc.page_content for doc in staged_docs], metadatas=[doc.metadata for doc in staged_docs], embeddings=staged_embeddings)
            try:
                self.vectorstore._collection.modify(metadata={"representation_id": self.spec.representation_id, "embedding_dimension": self.embedding_dimension, "index_schema_version": self.spec.index_schema_version})
            except Exception:
                pass
            report["status"] = "promoted"
            report["promoted_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            write_json(failure_path, report)
        except Exception as exc:
            try:
                self.vectorstore._collection.delete(ids=affected)
                if snapshot.get("ids"):
                    self.vectorstore._collection.upsert(
                        ids=snapshot.get("ids"),
                        documents=snapshot.get("documents"),
                        metadatas=snapshot.get("metadatas"),
                        embeddings=snapshot.get("embeddings"),
                    )
            except Exception:
                report["rollback_warning"] = "rollback could not be fully verified"
            report["status"] = "failed"
            report["error"] = f"{type(exc).__name__}: {exc}"
            write_json(failure_path, report)
            raise
        finally:
            if client is not None and stage_collection is not None:
                try:
                    client.delete_collection(staging_name)
                except Exception:
                    pass

        return {
            "documents": len(docs),
            "inserted": inserted,
            "updated_metadata": len(reconciliation.metadata_only),
            "removed": len(reconciliation.removed) if self.config.reconcile else 0,
            "proposed_removed": len(reconciliation.removed),
            "skipped_existing": len(reconciliation.unchanged),
            "embedding_cache_hits": embedding_hits,
            "embedding_cache": getattr(self, "last_cache_stats", {}),
            "operation_id": operation,
            "staging_validation": validation.as_dict(),
            "source_classification": reconciliation.source_classification,
            "reconciliation": reconciliation.as_dict(),
            "summary": summary,
        }

def iter_cache_files(processed_data_dir: Path, file_glob: str) -> list[Path]:
    pattern = str(processed_data_dir / file_glob)
    return [Path(path) for path in sorted(glob.glob(pattern, recursive=True)) if Path(path).is_file()]

def detect_embedding_dimension(embeddings: Any) -> int | None:
    try:
        vector = embeddings.embed_query("dimension probe")
        values = [float(value) for value in vector]
        if not values or not all(math.isfinite(value) for value in values):
            raise EmbeddingCompatibilityError("Dimension probe returned an empty or non-finite vector")
        return len(values)
    except EmbeddingCompatibilityError:
        raise
    except Exception:
        return None
