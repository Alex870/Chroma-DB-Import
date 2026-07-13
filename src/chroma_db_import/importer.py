from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import chroma_db_import.runtime as runtime
from chroma_db_import.config import ImportConfig, resolve_path
from chroma_db_import.contract import content_fingerprint, file_fingerprint, has_text, sanitize_metadata, validate_document_items
from chroma_db_import.state import write_json
from chroma_db_import.representation import RepresentationSpec, embedding_fingerprint, embedding_text, metadata_fingerprint

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
        model_revision=config.embedding_model_revision,
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
        HuggingFaceEmbeddings = runtime.HuggingFaceEmbeddings
        self.config = config
        self.project_dir = project_dir
        self.spec = representation_spec(config)
        self.embeddings = HuggingFaceEmbeddings(model_name=self.spec.model_id)
        self.persist_dir = resolve_path(project_dir, config.persist_dir)
        self.vectorstore = Chroma(
            embedding_function=self.embeddings,
            persist_directory=str(self.persist_dir),
            collection_name=config.collection_name,
        )
        self.embedding_dimension = detect_embedding_dimension(self.embeddings)
        self.spec = representation_spec(config, self.embedding_dimension)
        self.embedding_cache_dir = resolve_path(project_dir, config.embedding_cache_dir)
        self.import_state_dir = resolve_path(project_dir, config.import_state_dir)

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
        key = embedding_fingerprint(doc.page_content, dict(doc.metadata or {}), self.spec)
        return self.embedding_cache_dir / f"{key}.json"

    def cached_embeddings(self, docs: list[Document]) -> tuple[list[list[float] | None], int]:
        if not self.config.cache_embeddings:
            return [None] * len(docs), 0
        cached: list[list[float] | None] = []
        hits = 0
        for doc in docs:
            path = self.embedding_cache_path(doc)
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    vector = payload.get("embedding")
                    if isinstance(vector, list):
                        cached.append([float(value) for value in vector])
                        hits += 1
                        continue
                except Exception:
                    pass
            cached.append(None)
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
                            "embedding_fingerprint": embedding_fingerprint(docs[idx].page_content, dict(docs[idx].metadata or {}), self.spec),
                            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                            "embedding": vector,
                        },
                    )
        return [vector or [] for vector in cached], hits

    def import_cache(self, cache_path: Path, dry_run: bool = False) -> dict[str, Any]:
        """Import one processed cache file, resuming batch progress when available."""
        docs = load_processed_documents(cache_path)
        validate_documents(docs, str(cache_path))
        docs = [doc for doc in docs if has_text(doc.page_content) and should_include_document(doc, self.config)]
        ids = [document_id(doc) for doc in docs]
        existing = self.existing_ids(ids)
        pending = [
            Document(page_content=doc.page_content, metadata=sanitize_metadata(doc.metadata))
            for doc in docs
            if document_id(doc) not in existing
        ]
        pending_ids = [document_id(doc) for doc in docs if document_id(doc) not in existing]
        summary = summarize_documents_for_plan(docs)
        if dry_run:
            return {
                "documents": len(docs),
                "inserted": 0,
                "skipped_existing": len(existing),
                "new_ids": pending_ids,
                "summary": summary,
                "dry_run": True,
            }

        inserted = 0
        embedding_hits = 0
        batch_state = self.load_batch_state(cache_path)
        completed_batches = set(batch_state.get("completed_batches") or [])
        batch_size = max(1, int(self.config.import_batch_size or 64))
        for start in range(0, len(pending), batch_size):
            batch_docs = pending[start : start + batch_size]
            batch_ids = pending_ids[start : start + batch_size]
            batch_key = hashlib.sha1("|".join(batch_ids).encode("utf-8")).hexdigest()
            if batch_key in completed_batches:
                inserted += len(batch_docs)
                continue
            if not batch_docs:
                continue
            embeddings, hits = self.embed_documents_cached(batch_docs)
            embedding_hits += hits
            self.vectorstore._collection.add(
                ids=batch_ids,
                documents=[doc.page_content for doc in batch_docs],
                metadatas=[doc.metadata for doc in batch_docs],
                embeddings=embeddings,
            )
            inserted += len(batch_docs)
            completed_batches.add(batch_key)
            batch_state["completed_batches"] = sorted(completed_batches)
            self.save_batch_state(cache_path, batch_state)
            print(f"    inserted {inserted}/{len(pending)} documents")

        return {
            "documents": len(docs),
            "inserted": inserted,
            "skipped_existing": len(existing),
            "embedding_cache_hits": embedding_hits,
            "summary": summary,
        }

def iter_cache_files(processed_data_dir: Path, file_glob: str) -> list[Path]:
    pattern = str(processed_data_dir / file_glob)
    return [Path(path) for path in sorted(glob.glob(pattern, recursive=True)) if Path(path).is_file()]

def detect_embedding_dimension(embeddings: Any) -> int | None:
    try:
        vector = embeddings.embed_query("dimension probe")
        return len(vector) if vector else None
    except Exception:
        return None
