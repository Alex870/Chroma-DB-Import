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
from chroma_db_import.representation import (
    RepresentationSpec,
    embedding_content_hash,
    embedding_fingerprint,
    embedding_text,
    metadata_fingerprint,
    recommended_batch_size,
    resolve_representation_spec,
    resolved_collection_name,
    resolved_profile_path,
)
from chroma_db_import.reconciliation import plan_reconciliation, require_delete_confirmation, source_identity
from chroma_db_import.providers import (
    EmbeddingCompatibilityError,
    EmbeddingMemoryError,
    create_embedding_provider,
    preflight_embedding_memory,
    probe_embedding_provider,
)
from chroma_db_import.staging import operation_id, staging_collection_name, validate_staged_records
from chroma_db_import.deduplication import (
    DEDUP_KEY_VERSION,
    DedupPlan,
    DeduplicationError,
    resolve_dedup_policy,
)

def cache_fingerprint(path: Path) -> str:
    return file_fingerprint(path)

def load_processed_payload(cache_path: Path) -> dict[str, Any]:
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {"documents": []}

def load_processed_documents(cache_path: Path) -> list[Document]:
    try:
        runtime.load_runtime_deps()
        Document = runtime.Document
    except ModuleNotFoundError:
        class Document:  # type: ignore[no-redef]
            def __init__(self, page_content: str, metadata: dict[str, Any]):
                self.page_content = page_content
                self.metadata = metadata
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
    return resolve_representation_spec(config, dimension)

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
        if resolve_dedup_policy(config.dedup_policy, default_profile="off")["profile"] != "off" and not config.managed_partition_identity:
            raise DeduplicationError("deduplication is managed-only; provide validated partition identity")
        self.project_dir = project_dir
        self.spec = representation_spec(config)
        self.embeddings, self.provider_diagnostics = create_embedding_provider(self.spec, config.embedding_device)
        self.embedding_dimension = detect_embedding_dimension(self.embeddings) or self.spec.dimension
        self.spec = representation_spec(config, self.embedding_dimension)
        self.provider_diagnostics.update(
            {
                "dimension": self.embedding_dimension,
                "representation_id": self.spec.representation_id,
            }
        )
        self.collection_name = resolved_collection_name(config.collection_name, self.spec.profile)
        configured_persist_dir = resolve_path(project_dir, config.persist_dir)
        self.persist_dir = (
            configured_persist_dir
            if config.storage_path_isolated
            else resolved_profile_path(configured_persist_dir, self.spec.profile)
        )
        batch_size = max(
            1,
            int(
                config.import_batch_size
                or recommended_batch_size(
                    self.provider_diagnostics["device"],
                    model_id=self.spec.model_id,
                    profile=self.spec.profile,
                )
            ),
        )
        self.memory_preflight = (
            preflight_embedding_memory(
                self.embeddings,
                self.spec,
                device=self.provider_diagnostics["device"],
                batch_size=batch_size,
                safety_margin_bytes=config.embedding_memory_safety_margin_bytes,
            )
            if config.enable_embedding_memory_preflight
            else {"status": "disabled", "batch_size": batch_size}
        )
        self.vectorstore = Chroma(
            embedding_function=self.embeddings,
            persist_directory=str(self.persist_dir),
            collection_name=self.collection_name,
        )
        existing_metadata = getattr(self.vectorstore._collection, "metadata", None) or {}
        existing_model = str(existing_metadata.get("model_id") or existing_metadata.get("embedding_model") or "")
        if existing_model and existing_model != self.spec.model_id:
            raise EmbeddingCompatibilityError(
                f"Target collection embedding model {existing_model!r} is incompatible with Qwen3-only imports. "
                "Create a fresh export; existing vectors were not changed."
            )
        existing_representation = str(existing_metadata.get("representation_id") or "")
        if existing_representation and existing_representation != self.spec.representation_id:
            raise EmbeddingCompatibilityError(
                "Target collection embedding representation is incompatible with the requested import. "
                "Create a new export or run an explicit migration; existing vectors were not changed."
            )
        self.embedding_probe = probe_embedding_provider(self.embeddings, expected_dimension=self.embedding_dimension)
        if existing_metadata.get("embedding_dimension") not in (None, "", self.embedding_dimension):
            raise EmbeddingCompatibilityError(
                f"Target collection dimension {existing_metadata.get('embedding_dimension')} does not match provider {self.embedding_dimension}."
            )
        self.embedding_cache_dir = resolved_profile_path(resolve_path(project_dir, config.embedding_cache_dir), self.spec.profile)
        self.import_state_dir = resolved_profile_path(resolve_path(project_dir, config.import_state_dir), self.spec.profile)
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
        # The cache identity is the exact provider input plus representation,
        # never the post-import metadata or a normalized-text dedup key.
        key = embedding_fingerprint(doc.page_content, dict(doc.metadata or {}), self.spec)
        key = hashlib.sha256(key.encode("utf-8")).hexdigest()
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
                    expected_embedding_fingerprint = embedding_fingerprint(
                        doc.page_content, dict(doc.metadata or {}), self.spec
                    )
                    if (
                        payload.get("cache_format_version") == 2
                        and payload.get("embedding_fingerprint") == expected_embedding_fingerprint
                        and payload.get("representation_id") == self.spec.representation_id
                        and isinstance(vector, list)
                        and len(vector) == self.embedding_dimension
                        and all(not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value)) for value in vector)
                    ):
                        cached.append([float(value) for value in vector])
                        hits += 1
                        continue
                except Exception:
                    pass
                invalidations += 1
            cached.append(None)
        self.last_cache_stats = {"hits": hits, "disk_hit_records": hits, "misses": len(docs) - hits, "invalidations": invalidations, "cache_size": len(list(self.embedding_cache_dir.glob("*.json"))) if self.embedding_cache_dir.exists() else 0}
        return cached, hits

    def embed_documents_cached(self, docs: list[Document]) -> tuple[list[list[float]], int]:
        cached, hits = self.cached_embeddings(docs)
        missing_indexes = [idx for idx, vector in enumerate(cached) if vector is None]
        provider_inputs = 0
        shared_vector_records = 0
        if missing_indexes:
            by_fingerprint: dict[str, list[int]] = {}
            for idx in missing_indexes:
                fingerprint = embedding_fingerprint(docs[idx].page_content, dict(docs[idx].metadata or {}), self.spec)
                by_fingerprint.setdefault(fingerprint, []).append(idx)
            fingerprints = sorted(by_fingerprint)
            missing_texts = [
                embedding_text(docs[by_fingerprint[fingerprint][0]].page_content, dict(docs[by_fingerprint[fingerprint][0]].metadata or {}), self.spec.contextualization)
                for fingerprint in fingerprints
            ]
            provider_inputs = len(missing_texts)
            try:
                embedded = self.embeddings.embed_documents(missing_texts)
            except Exception as exc:
                from chroma_db_import.providers import is_cuda_oom

                if is_cuda_oom(exc):
                    raise EmbeddingMemoryError(
                        f"Embedding batch of {len(missing_texts)} ran out of CUDA memory. "
                        "Lower import_batch_size to 1 or select the CPU device; no candidate was promoted."
                    ) from exc
                raise
            if len(embedded) != provider_inputs:
                raise EmbeddingCompatibilityError(
                    f"Embedding provider returned {len(embedded)} vectors for {provider_inputs} inputs"
                )
            for fingerprint, vector in zip(fingerprints, embedded):
                if not isinstance(vector, list) or len(vector) != self.embedding_dimension or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in vector):
                    raise EmbeddingCompatibilityError("Embedding provider returned an invalid vector")
                for idx in by_fingerprint[fingerprint]:
                    if len(by_fingerprint[fingerprint]) > 1:
                        shared_vector_records += 1
                    cached[idx] = [float(value) for value in vector]
                idx = by_fingerprint[fingerprint][0]
                cached[idx] = vector
                if self.config.cache_embeddings:
                    write_json(
                        self.embedding_cache_path(docs[idx]),
                        {
                            "cache_format_version": 2,
                            "representation": self.spec.as_dict(),
                            "representation_id": self.spec.representation_id,
                            "embedding_fingerprint": fingerprint,
                            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                            "embedding": [float(value) for value in vector],
                        },
                    )
        self.last_cache_stats.update({"provider_document_inputs": provider_inputs, "shared_vector_records": shared_vector_records, "disk_hit_records": hits})
        return [vector or [] for vector in cached], hits

    def import_cache(self, cache_path: Path, dry_run: bool = False, *, dedup_plan: DedupPlan | None = None) -> dict[str, Any]:
        """Import one processed cache file, resuming batch progress when available."""
        # ``Document`` is intentionally loaded lazily with the other heavy
        # runtime dependencies.  Keep a local constructor for the normalized
        # records created below; the loader's local fallback is not visible
        # here when the runtime package is unavailable.
        runtime.load_runtime_deps()
        document_class = runtime.Document
        if dedup_plan is not None:
            resolved_policy = resolve_dedup_policy(self.config.dedup_policy, default_profile="off")
            if resolved_policy != dedup_plan.policy:
                raise DeduplicationError("dedup plan policy does not match effective import configuration")
            if dedup_plan.representation_id and dedup_plan.representation_id != self.spec.representation_id:
                raise DeduplicationError("dedup plan representation does not match effective import configuration")
        docs = load_processed_documents(cache_path)
        payload = load_processed_payload(cache_path)
        validate_documents(docs, str(cache_path))
        docs = [doc for doc in docs if has_text(doc.page_content) and should_include_document(doc, self.config)]
        source_cache = cache_path.name if self.config.portable_artifacts else str(cache_path.resolve())
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
            if dedup_plan is not None:
                planned = dedup_plan.inputs.get(item_id)
                if planned is None:
                    raise DeduplicationError(f"cache document {item_id} is absent from the release dedup plan")
                if planned.cache_locator and Path(planned.cache_locator).resolve() != cache_path.resolve():
                    raise DeduplicationError(f"dedup plan cache binding mismatch for {item_id}")
                if planned.partition_id and self.config.managed_partition_identity and planned.partition_id != self.config.managed_partition_identity.get("partition_id"):
                    raise DeduplicationError(f"dedup plan partition mismatch for {item_id}")
                if planned.verified_cache_fingerprint not in {source_content_fingerprint, str(payload.get("cache_fingerprint") or ""), str(payload.get("source_fingerprint") or "")}:
                    raise DeduplicationError(f"dedup plan cache fingerprint mismatch for {item_id}")
                decision = dedup_plan.decisions.get(item_id)
                if decision is None:
                    raise DeduplicationError(f"dedup plan is missing decision for {item_id}")
                if decision.suppressed:
                    continue
            fingerprints = document_fingerprints(doc, self.spec)
            metadata = sanitize_metadata(doc.metadata)
            metadata.update(fingerprints)
            if self.config.managed_partition_identity:
                for key, value in self.config.managed_partition_identity.items():
                    if value not in (None, ""):
                        metadata.setdefault(key, value)
                if not metadata.get("episode_uid") and metadata.get("episode_id"):
                    metadata["episode_uid"] = (
                        f"{self.config.managed_partition_identity.get('partition_id')}:{metadata['episode_id']}"
                    )
            if self.config.upstream_release_id:
                metadata["upstream_release_id"] = self.config.upstream_release_id
            if self.config.handoff_ids:
                metadata["handoff_ids"] = json.dumps(sorted(set(self.config.handoff_ids)), ensure_ascii=True)
            metadata["import_source_cache"] = source_cache
            metadata["source_content_fingerprint"] = source_content_fingerprint
            metadata["source_identity"] = source_record_identity
            metadata["schema_version"] = str(payload.get("schema_version") or "")
            if dedup_plan is not None:
                planned = dedup_plan.inputs[item_id]
                decision = dedup_plan.decisions[item_id]
                metadata.update({
                    "dedup_key_version": DEDUP_KEY_VERSION,
                    "normalized_text_hash": planned.normalized_text_hash,
                    "duplicate_status": (
                        "canonical" if decision.duplicate_group_id and decision.preferred_canonical_id == item_id
                        else "retained_exact" if decision.duplicate_group_id
                        else "unique"
                    ),
                    "duplicate_method": decision.method if decision.method != "none" else "none",
                    "duplicate_of": decision.alias_target_id or (decision.preferred_canonical_id if decision.duplicate_group_id and decision.preferred_canonical_id != item_id else ""),
                    "dedup_policy_version": dedup_plan.policy["policy_version"],
                })
                if planned.source_span_hash:
                    metadata["source_span_hash"] = planned.source_span_hash
                if decision.duplicate_group_id:
                    metadata["duplicate_group_id"] = decision.duplicate_group_id
            prepared[item_id] = document_class(page_content=doc.page_content, metadata=metadata)
            current[item_id] = {**fingerprints, "source_identity": source_record_identity}
        existing = self.existing_records(list(prepared), source_cache)
        reconciliation = plan_reconciliation(current, existing)
        summary = summarize_documents_for_plan(docs)
        if dedup_plan is not None:
            planned_stored = set(dedup_plan.stored_ids)
            if set(prepared) - planned_stored:
                raise DeduplicationError("prepared cache contains IDs that are not planned for storage")
        if dry_run:
            return {
                "documents": len(docs),
                "inserted": 0,
                "skipped_existing": len(reconciliation.unchanged),
                "reconciliation": reconciliation.as_dict(),
            "summary": summary,
                "dedup": dedup_plan.as_counts() if dedup_plan is not None else None,
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
        batch_size = max(
            1,
            int(
                self.config.import_batch_size
                or recommended_batch_size(
                    self.provider_diagnostics["device"],
                    model_id=self.spec.model_id,
                    profile=self.spec.profile,
                )
            ),
        )
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
            "dedup": dedup_plan.as_counts() if dedup_plan is not None else None,
            "staging": validation.as_dict(),
            "resource_measurements": {
                "provider": self.provider_diagnostics,
                "memory_preflight": self.memory_preflight,
                "batch_size": batch_size,
                "device": self.provider_diagnostics.get("device"),
            },
        }
        if not validation.valid:
            write_json(failure_path, report)
            raise ValueError("Staging validation failed: " + "; ".join(validation.errors[:5]))

        staging_name = staging_collection_name(self.collection_name, operation)
        client = getattr(self.vectorstore, "_client", None)
        stage_collection = None
        try:
            if client is not None:
                stage_collection = client.get_or_create_collection(
                    staging_name,
                    metadata={
                        "representation_id": self.spec.representation_id,
                        "embedding_dimension": self.embedding_dimension,
                        "profile": self.spec.profile,
                        "embedding_model": self.spec.model_id,
                        "model_revision": self.spec.model_revision,
                        "provider": self.spec.provider,
                        "normalize_embeddings": self.spec.normalize_embeddings,
                        "distance_metric": self.spec.distance_metric,
                        "query_instruction_profile": self.spec.query_instruction_profile,
                        "query_document_mode": self.spec.query_document_mode,
                    },
                )
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
                self.vectorstore._collection.modify(
                    metadata={
                        "representation_id": self.spec.representation_id,
                        "embedding_dimension": self.embedding_dimension,
                        "index_schema_version": self.spec.index_schema_version,
                        "profile": self.spec.profile,
                        "embedding_model": self.spec.model_id,
                        "model_revision": self.spec.model_revision,
                        "provider": self.spec.provider,
                        "normalize_embeddings": self.spec.normalize_embeddings,
                        "distance_metric": self.spec.distance_metric,
                        "query_instruction_profile": self.spec.query_instruction_profile,
                        "query_document_mode": self.spec.query_document_mode,
                    }
                )
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
            "resource_measurements": report.get("resource_measurements", {}),
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
