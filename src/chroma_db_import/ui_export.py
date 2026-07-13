from __future__ import annotations

import datetime as dt
import json
import shutil
import time
import re
import hashlib
from pathlib import Path
from typing import Any

import chroma_db_import.runtime as runtime
from chroma_db_import.config import ImportConfig
from chroma_db_import.contract import IMPORTER_VERSION, build_import_manifest, content_fingerprint, has_text, sanitize_metadata, summarize_reports, validate_document_items, validate_podcast_metadata
from chroma_db_import.importer import cache_fingerprint, validate_documents, document_fingerprints, representation_spec
from chroma_db_import.providers import create_embedding_provider
from chroma_db_import.reconciliation import ReconciliationPlan, plan_reconciliation, require_delete_confirmation
from chroma_db_import.representation import embedding_text, recommended_batch_size
from chroma_db_import.ui_helpers import document_speakers, slugify, safe_folder_name
from chroma_db_import.ui_models import Episode, ImportPlan, ImportProgress, ImportSummary, ProcessedDocument
from chroma_db_import.ui_support import resolve_embedding_device

ALWAYS_INCLUDE_NODE_TYPES = {"episode_thesis"}
SUMMARY_NODE_TYPES = {"cluster_summary"}
TOPIC_INDEX_FILENAME = "topic_index.json"


def preview_ui_reconciliation(plan: ImportPlan) -> ReconciliationPlan:
    """Inspect an existing desktop export without loading or downloading an embedding model."""
    combined = ReconciliationPlan()
    config = ImportConfig(
        embedding_model=plan.embedding_model,
        contextualization=plan.contextualization,
        experimental_bge_m3=plan.experimental_bge_m3,
    )
    spec = representation_spec(config)
    collection = None
    if (plan.export_dir / "chroma.sqlite3").exists():
        import chromadb

        client = chromadb.PersistentClient(path=str(plan.export_dir))
        try:
            collection = client.get_collection(plan.collection_name)
        except Exception:
            collection = None
    for episode in plan.episodes:
        source_cache = str(episode.path.resolve())
        selected = [doc for doc in select_documents_for_episode(episode, plan.included_speakers_by_episode) if has_text(doc.page_content)]
        current = {}
        for doc in selected:
            item = DocumentLike(doc.page_content, sanitize_metadata(doc.metadata))
            current[str(doc.metadata.get("node_id"))] = document_fingerprints(item, spec)
        existing = {}
        if collection is not None:
            payloads = [collection.get(ids=list(current), include=["metadatas"])] if current else []
            try:
                payloads.append(collection.get(where={"import_source_cache": source_cache}, include=["metadatas"]))
            except Exception:
                pass
            for payload in payloads:
                for item_id, metadata in zip(payload.get("ids") or [], payload.get("metadatas") or []):
                    metadata = metadata or {}
                    existing[str(item_id)] = {
                        "embedding_fingerprint": str(metadata.get("embedding_fingerprint") or ""),
                        "metadata_fingerprint": str(metadata.get("metadata_fingerprint") or ""),
                    }
        episode_plan = plan_reconciliation(current, existing)
        for field_name in ("added", "changed", "metadata_only", "unchanged", "removed"):
            getattr(combined, field_name).extend(getattr(episode_plan, field_name))
    for field_name in ("added", "changed", "metadata_only", "unchanged", "removed"):
        setattr(combined, field_name, sorted(set(getattr(combined, field_name))))
    return combined


def export_chroma(plan: ImportPlan, mode: str, emit_progress) -> ImportSummary:  # type: ignore[no-untyped-def]
    """Create or update a self-contained Chroma export for the selected episodes."""
    emit_progress(ImportProgress("Loading runtime dependencies...", 0, 0))
    runtime.load_runtime_deps()
    from langchain_chroma import Chroma
    from langchain_core.documents import Document

    started_at = time.time()
    emit_progress(ImportProgress("Validating selected documents...", 0, 0))
    preflight = build_ui_validation_report(plan)
    if not preflight["valid"]:
        first_error = next((error for item in preflight["files"] for error in item["errors"]), "validation failed")
        raise ValueError(f"Validation failed before import: {first_error}")

    if mode == "generate" and plan.export_dir.exists():
        shutil.rmtree(plan.export_dir)
    plan.export_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = plan.export_dir / "podcast.json"
    existing_episode_entries: list[dict[str, Any]] = []
    existing_by_fingerprint: dict[str, dict[str, Any]] = {}
    existing_by_source_file: dict[str, dict[str, Any]] = {}
    if mode == "update" and metadata_path.exists():
        emit_progress(ImportProgress("Loading existing export metadata...", 0, 0))
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        existing_episode_entries = list(existing.get("episodes", []))
        existing_by_fingerprint = {
            str(item.get("source_fingerprint")): item
            for item in existing_episode_entries
            if item.get("source_fingerprint")
        }
        existing_by_source_file = {
            str(item.get("source_file")): item
            for item in existing_episode_entries
            if item.get("source_file")
        }

    embedding_device = resolve_embedding_device(plan.embedding_device)
    emit_progress(ImportProgress(f"Loading embedding model on {embedding_device}...", 0, 0))
    provider_config = ImportConfig(
        embedding_model=plan.embedding_model,
        embedding_device=plan.embedding_device,
        contextualization=plan.contextualization,
        experimental_bge_m3=plan.experimental_bge_m3,
    )
    spec = representation_spec(provider_config)
    embeddings, provider_diagnostics = create_embedding_provider(spec, embedding_device)
    emit_progress(ImportProgress("Opening Chroma collection...", 0, 0))
    vectorstore = Chroma(
        embedding_function=embeddings,
        persist_directory=str(plan.export_dir),
        collection_name=plan.collection_name,
    )

    episodes_to_import: list[Episode] = []
    for episode in plan.episodes:
        if mode == "update" and update_should_skip_episode(
            episode,
            plan.included_speakers_by_episode,
            existing_by_fingerprint,
            existing_by_source_file,
        ):
            continue
        episodes_to_import.append(episode)
    total_documents_to_import = sum(
        count_insertable_documents(select_documents_for_episode(episode, plan.included_speakers_by_episode))
        for episode in episodes_to_import
    )
    emit_progress(
        ImportProgress(
            f"Ready to embed {total_documents_to_import} document(s) across {len(episodes_to_import)} episode(s).",
            0,
            total_documents_to_import,
        )
    )

    imported_episodes = []
    inserted = 0
    skipped = 0
    processed_documents = 0
    for index, episode in enumerate(plan.episodes, 1):
        if mode == "update" and update_should_skip_episode(
            episode,
            plan.included_speakers_by_episode,
            existing_by_fingerprint,
            existing_by_source_file,
        ):
            skipped += 1
            emit_progress(
                ImportProgress(
                    f"Skipped already imported: {episode.title}",
                    processed_documents,
                    total_documents_to_import,
                )
            )
            continue

        selected = select_documents_for_episode(episode, plan.included_speakers_by_episode)
        validate_documents([Document(page_content=doc.page_content, metadata=doc.metadata) for doc in selected], str(episode.path))
        documents = []
        source_cache = str(episode.path.resolve())
        for doc in selected:
            if not has_text(doc.page_content):
                continue
            item = Document(page_content=doc.page_content, metadata=sanitize_metadata(doc.metadata))
            fingerprints = document_fingerprints(item, spec)
            item.metadata.update(fingerprints)
            item.metadata["import_source_cache"] = source_cache
            documents.append(item)
        ids = [str(doc.metadata["node_id"]) for doc in selected if has_text(doc.page_content)]
        emit_progress(
            ImportProgress(
                f"Preparing episode {index} of {len(plan.episodes)}: {episode.title}",
                processed_documents,
                total_documents_to_import,
            )
        )

        current = {doc_id: {"embedding_fingerprint": doc.metadata["embedding_fingerprint"], "metadata_fingerprint": doc.metadata["metadata_fingerprint"]} for doc, doc_id in zip(documents, ids)}
        existing = {}
        if mode == "update":
            payloads = [vectorstore._collection.get(ids=ids, include=["metadatas"])] if ids else []
            try:
                payloads.append(vectorstore._collection.get(where={"import_source_cache": source_cache}, include=["metadatas"]))
            except Exception:
                pass
            for payload in payloads:
                for item_id, metadata in zip(payload.get("ids") or [], payload.get("metadatas") or []):
                    metadata = metadata or {}
                    existing[str(item_id)] = {"embedding_fingerprint": str(metadata.get("embedding_fingerprint") or ""), "metadata_fingerprint": str(metadata.get("metadata_fingerprint") or "")}
        reconciliation = plan_reconciliation(current, existing)
        emit_progress(ImportProgress(f"Preview {episode.title}: added={len(reconciliation.added)}, changed={len(reconciliation.changed)}, metadata-only={len(reconciliation.metadata_only)}, unchanged={len(reconciliation.unchanged)}, removed={len(reconciliation.removed)}", processed_documents, total_documents_to_import))
        require_delete_confirmation(reconciliation, plan.allow_delete_missing)
        by_id = dict(zip(ids, documents))
        if reconciliation.metadata_only:
            vectorstore._collection.update(ids=reconciliation.metadata_only, documents=[by_id[item].page_content for item in reconciliation.metadata_only], metadatas=[by_id[item].metadata for item in reconciliation.metadata_only])
        if reconciliation.removed:
            vectorstore._collection.delete(ids=reconciliation.removed)
        ids = reconciliation.added + reconciliation.changed
        documents = [by_id[item] for item in ids]

        batch_size = recommended_batch_size(provider_diagnostics["device"])
        for start in range(0, len(documents), batch_size):
            batch_docs = documents[start : start + batch_size]
            batch_ids = ids[start : start + batch_size]
            if batch_docs:
                emit_progress(
                    ImportProgress(
                        f"Embedding {episode.title}: documents {start + 1}-{start + len(batch_docs)} of {len(documents)}",
                        processed_documents,
                        total_documents_to_import,
                    )
                )
                vectors = embeddings.embed_documents([embedding_text(doc.page_content, doc.metadata, spec.contextualization) for doc in batch_docs])
                vectorstore._collection.upsert(ids=batch_ids, documents=[doc.page_content for doc in batch_docs], metadatas=[doc.metadata for doc in batch_docs], embeddings=vectors)
                inserted += len(batch_docs)
                processed_documents += len(batch_docs)
                emit_progress(
                    ImportProgress(
                        f"Embedded {processed_documents} of {total_documents_to_import} document(s).",
                        processed_documents,
                        total_documents_to_import,
                    )
                )

        imported_episodes.append(episode_metadata_entry(episode, selected, len(documents)))
        emit_progress(
            ImportProgress(
                f"Imported {len(documents)} documents: {episode.title}",
                processed_documents,
                total_documents_to_import,
            )
        )

    all_episode_entries = merge_episode_entries(existing_episode_entries, imported_episodes)
    primary_host_name = infer_primary_host_name(plan, all_episode_entries)
    primary_host_id = slugify(primary_host_name) if primary_host_name else ""
    emit_progress(ImportProgress("Preparing topic index bundle...", processed_documents, total_documents_to_import))
    export_topic_index = build_export_topic_index(plan)
    topic_index_path = plan.export_dir / TOPIC_INDEX_FILENAME
    topic_profile_count = 0
    if export_topic_index is not None:
        topic_index_path.write_text(json.dumps(export_topic_index, indent=2, ensure_ascii=True), encoding="utf-8")
        topic_profile_count = write_topic_profile_documents(
            vectorstore,
            plan,
            export_topic_index,
            primary_host_name=primary_host_name,
        )
    total_documents = sum(int(item.get("document_count") or 0) for item in all_episode_entries)
    total_collection_documents = total_documents + topic_profile_count
    emit_progress(ImportProgress("Writing podcast metadata...", processed_documents, total_documents_to_import))
    write_podcast_metadata(
        plan,
        all_episode_entries,
        total_collection_documents,
        metadata_path,
        detect_embedding_dimension(embeddings),
        primary_host_name=primary_host_name,
        primary_host_id=primary_host_id,
        topic_index_present=topic_index_path.exists(),
        topic_count=int(export_topic_index.get("topic_count") or 0) if export_topic_index else 0,
        topic_profile_count=topic_profile_count,
    )
    metadata_payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata_report = validate_podcast_metadata(metadata_payload)
    if not metadata_report.valid:
        raise ValueError("Generated podcast.json failed validation: " + "; ".join(metadata_report.errors[:5]))
    emit_progress(ImportProgress("Writing import manifest...", processed_documents, total_documents_to_import))
    manifest = build_import_manifest(
        config={
            "database_id": plan.database_id,
            "collection_name": plan.collection_name,
            "embedding_model": plan.embedding_model,
            "embedding_device": plan.embedding_device,
            "mode": mode,
        },
        source_files=[
            {
                "path": str(episode.path),
                "fingerprint": episode.fingerprint,
                "content_fingerprint": content_fingerprint(episode.path),
                "document_count": len(select_documents_for_episode(episode, plan.included_speakers_by_episode)),
            }
            for episode in plan.episodes
        ],
        validation_results=[item["report"] for item in preflight["raw_reports"]],
        embedding_model=plan.embedding_model,
        embedding_dimension=detect_embedding_dimension(embeddings),
        collection_name=plan.collection_name,
        selected_speakers=sorted({speaker for speakers in plan.included_speakers_by_episode.values() for speaker in speakers}),
        compatibility_warnings=metadata_report.warnings,
    )
    manifest["representation"] = spec.as_dict()
    manifest["provider_diagnostics"] = provider_diagnostics
    manifest_path = plan.export_dir / "import_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True), encoding="utf-8")
    immutable_id = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    profile = "bge-m3-shadow" if plan.experimental_bge_m3 else "baseline"
    immutable_dir = plan.output_root / "exports" / profile / immutable_id
    if not immutable_dir.exists():
        immutable_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(plan.export_dir, immutable_dir)
        (immutable_dir / "BUILD_IMMUTABLE").write_text(immutable_id + "\n", encoding="ascii")
    return ImportSummary(
        inserted=inserted,
        skipped_episodes=skipped,
        imported_episodes=len(imported_episodes),
        export_dir=plan.export_dir,
        skipped_documents=max(0, total_documents_to_import - inserted),
        elapsed_seconds=round(time.time() - started_at, 2),
        warnings=preflight.get("warnings", []) + metadata_report.warnings + ([] if export_topic_index else ["No topic index bundle was found to export."]),
    )

def update_should_skip_episode(
    episode: Episode,
    included_speakers_by_episode: dict[str, set[str]],
    existing_by_fingerprint: dict[str, dict[str, Any]],
    existing_by_source_file: dict[str, dict[str, Any]],
) -> bool:
    existing_entry = existing_entry_for_episode(episode, existing_by_fingerprint, existing_by_source_file)
    if existing_entry is None:
        return False

    selected_speakers = selected_speakers_for_episode(episode, included_speakers_by_episode)
    if not selected_speakers:
        return True

    existing_speakers = episode_speakers_from_entry(existing_entry)
    return selected_speakers.issubset(existing_speakers)

def build_ui_validation_report(plan: ImportPlan) -> dict[str, Any]:
    raw_reports = []
    files = []
    warnings = []
    for episode in plan.episodes:
        selected = select_documents_for_episode(episode, plan.included_speakers_by_episode)
        docs = [DocumentLike(doc.page_content, doc.metadata) for doc in selected]
        report = validate_document_items(docs, str(episode.path))
        raw_reports.append({"path": str(episode.path), "report": report})
        files.append({"path": str(episode.path), **report.as_dict()})
        warnings.extend(report.warnings)
    summary = summarize_reports([item["report"] for item in raw_reports])
    return {
        "valid": all(item["report"].valid for item in raw_reports),
        "summary": summary,
        "files": files,
        "warnings": warnings,
        "raw_reports": raw_reports,
    }

def prune_document_graph(documents: list[ProcessedDocument]) -> list[ProcessedDocument]:
    selected_ids = {
        str(doc.metadata.get("node_id"))
        for doc in documents
        if doc.metadata.get("node_id")
    }
    pruned: list[ProcessedDocument] = []
    for doc in documents:
        metadata = dict(doc.metadata)
        child_ids = metadata.get("child_ids")
        if isinstance(child_ids, list):
            metadata["child_ids"] = [
                child_id for child_id in child_ids
                if isinstance(child_id, str) and child_id in selected_ids
            ]
        parent_id = metadata.get("parent_id")
        if isinstance(parent_id, str) and parent_id and parent_id not in selected_ids:
            metadata["parent_id"] = None
        pruned.append(ProcessedDocument(page_content=doc.page_content, metadata=metadata))
    return pruned

class DocumentLike:
    def __init__(self, page_content: str, metadata: dict[str, Any]):
        self.page_content = page_content
        self.metadata = metadata

def existing_entry_for_episode(
    episode: Episode,
    existing_by_fingerprint: dict[str, dict[str, Any]],
    existing_by_source_file: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    return existing_by_fingerprint.get(episode.fingerprint) or existing_by_source_file.get(str(episode.path))

def selected_speakers_for_episode(
    episode: Episode,
    included_speakers_by_episode: dict[str, set[str]],
) -> set[str]:
    included_speakers = included_speakers_by_episode.get(episode.fingerprint, set())
    return {speaker for speaker in episode.speakers if speaker in included_speakers}

def episode_speakers_from_entry(entry: dict[str, Any]) -> set[str]:
    speakers: set[str] = set()
    for item in entry.get("speakers", []):
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            speakers.add(name)
    return speakers

def existing_vector_ids(vectorstore: Any, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    existing: set[str] = set()
    for start in range(0, len(ids), 64):
        batch_ids = ids[start : start + 64]
        result = vectorstore.get(ids=batch_ids, include=[])
        existing.update(str(item) for item in result.get("ids", []) if item)
    return existing

def detect_embedding_dimension(embeddings: Any) -> int | None:
    try:
        probe = embeddings.embed_query("embedding dimension probe")
    except Exception:
        return None
    try:
        return len(probe)
    except TypeError:
        return None

def merge_episode_entries(
    existing_episode_entries: list[dict[str, Any]],
    imported_episodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_source_file = {
        str(entry.get("source_file")): entry
        for entry in existing_episode_entries
        if entry.get("source_file")
    }
    for entry in imported_episodes:
        source_file = str(entry.get("source_file"))
        if source_file in by_source_file:
            by_source_file[source_file] = merge_episode_entry(by_source_file[source_file], entry)
        else:
            by_source_file[source_file] = entry
    return sorted(
        by_source_file.values(),
        key=lambda item: (str(item.get("episode_date") or "9999-99-99"), str(item.get("episode_title") or "")),
    )

def merge_episode_entry(existing: dict[str, Any], imported: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    merged.update(imported)
    speaker_names = sorted(episode_speakers_from_entry(existing) | episode_speakers_from_entry(imported))
    merged["speakers"] = [{"id": slugify(speaker), "name": speaker} for speaker in speaker_names]
    merged["document_count"] = int(existing.get("document_count") or 0) + int(imported.get("document_count") or 0)
    return merged

def select_documents_for_episode(
    episode: Episode,
    included_speakers_by_episode: dict[str, set[str]],
) -> list[ProcessedDocument]:
    included_speakers = included_speakers_by_episode.get(episode.fingerprint, set())
    selected = [doc for doc in episode.documents if should_include_document(doc, included_speakers)]
    return prune_document_graph(selected)

def count_insertable_documents(documents: list[ProcessedDocument]) -> int:
    return sum(1 for doc in documents if has_text(doc.page_content))

def should_include_document(doc: ProcessedDocument, included_speakers: set[str]) -> bool:
    metadata = doc.metadata
    node_type = str(metadata.get("node_type") or "")
    speaker_scope = str(metadata.get("speaker_scope") or "")
    speakers = set(document_speakers(metadata))

    if node_type in ALWAYS_INCLUDE_NODE_TYPES:
        return True
    if node_type in SUMMARY_NODE_TYPES and speaker_scope != "single":
        return True
    if not speakers:
        return True
    return bool(speakers & included_speakers)

def episode_metadata_entry(
    episode: Episode,
    selected: list[ProcessedDocument],
    document_count: int | None = None,
) -> dict[str, Any]:
    speakers = sorted({speaker for doc in selected for speaker in document_speakers(doc.metadata)})
    return {
        "source_file": str(episode.path),
        "source_fingerprint": episode.fingerprint,
        "episode_id": episode.episode_id,
        "episode_title": episode.title,
        "episode_date": episode.episode_date,
        "document_count": len(selected) if document_count is None else document_count,
        "speakers": [{"id": slugify(speaker), "name": speaker} for speaker in speakers],
        "imported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def infer_primary_host_name(plan: ImportPlan, imported_episodes: list[dict[str, Any]]) -> str:
    speaker_counts: dict[str, int] = {}
    for episode in imported_episodes:
        for speaker in episode.get("speakers", []):
            if not isinstance(speaker, dict):
                continue
            name = str(speaker.get("name") or "").strip()
            if not name:
                continue
            speaker_counts[name] = speaker_counts.get(name, 0) + 1

    if not speaker_counts:
        return ""

    def score(name: str) -> tuple[int, int, str]:
        points = speaker_counts.get(name, 0) * 10
        name_fold = name.casefold()
        if name_fold == plan.database_id.strip().casefold():
            points += 100
        if name_fold == plan.podcast_name.strip().casefold():
            points += 90
        compact_name = re.sub(r"[^a-z0-9]+", "", name_fold)
        compact_db = re.sub(r"[^a-z0-9]+", "", plan.database_id.strip().casefold())
        compact_podcast = re.sub(r"[^a-z0-9]+", "", plan.podcast_name.strip().casefold())
        if compact_name and compact_name in {compact_db, compact_podcast}:
            points += 80
        if "host" in name_fold:
            points += 30
        return (points, speaker_counts.get(name, 0), name_fold)

    return sorted(speaker_counts, key=score, reverse=True)[0]


def locate_source_topic_index(plan: ImportPlan) -> Path | None:
    roots = [plan.processed_data_dir, *list(plan.processed_data_dir.parents[:3])]
    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        for candidate in (
            root / "state" / TOPIC_INDEX_FILENAME,
            root / "processed_data" / TOPIC_INDEX_FILENAME,
            root / TOPIC_INDEX_FILENAME,
        ):
            key = str(candidate.resolve()).casefold() if candidate.exists() else str(candidate).casefold()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def build_export_topic_index(plan: ImportPlan) -> dict[str, Any] | None:
    source_path = locate_source_topic_index(plan)
    if source_path is None:
        return None
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None

    topics = [
        dict(topic)
        for topic in payload.get("topics") or []
        if isinstance(topic, dict)
        and (
            str(topic.get("podcast_id") or "").strip().casefold() == plan.database_id.strip().casefold()
            or str(topic.get("podcast_name") or "").strip().casefold() == plan.podcast_name.strip().casefold()
        )
    ]
    podcasts = [
        dict(item)
        for item in payload.get("podcasts") or []
        if isinstance(item, dict)
        and (
            str(item.get("podcast_id") or "").strip().casefold() == plan.database_id.strip().casefold()
            or str(item.get("podcast_name") or "").strip().casefold() == plan.podcast_name.strip().casefold()
        )
    ]
    if not topics and not podcasts:
        return None
    for topic in topics:
        topic["podcast_id"] = plan.database_id
        topic["podcast_name"] = plan.podcast_name
    for item in podcasts:
        item["podcast_id"] = plan.database_id
        item["podcast_name"] = plan.podcast_name
    return {
        "schema_version": payload.get("schema_version") or "1.0",
        "logic_version": payload.get("logic_version") or "",
        "generated_at": payload.get("generated_at") or "",
        "podcasts": podcasts or [
            {
                "podcast_id": plan.database_id,
                "podcast_name": plan.podcast_name,
                "topic_count": len(topics),
                "episode_count": len(plan.episodes),
            }
        ],
        "topic_count": len(topics),
        "topics": topics,
    }


def build_topic_profile_payload(topic: dict[str, Any]) -> str:
    label = str(topic.get("label") or topic.get("topic_key") or "Topic")
    summary = str(topic.get("summary") or "").strip()
    question_templates = [str(item).strip() for item in topic.get("question_templates") or topic.get("sample_questions") or [] if str(item).strip()]
    query_hints = [str(item).strip() for item in topic.get("query_hints") or [] if str(item).strip()]
    aliases = [str(item).strip() for item in topic.get("aliases") or [] if str(item).strip()]
    keywords = [str(item).strip() for item in topic.get("top_keywords") or [] if str(item).strip()]
    evidence = [item for item in topic.get("evidence") or [] if isinstance(item, dict)][:3]
    evidence_lines = []
    for item in evidence:
        excerpt = str(item.get("excerpt") or "").strip()
        episode_title = str(item.get("episode_title") or "").strip()
        if excerpt:
            prefix = f"{episode_title}: " if episode_title else ""
            evidence_lines.append(f"- {prefix}{excerpt}")
    sections = [
        f"Topic: {label}",
        f"Kind: {str(topic.get('topic_kind') or 'subject')}",
    ]
    if summary:
        sections.append(f"Summary: {summary}")
    if aliases:
        sections.append("Aliases: " + ", ".join(aliases[:6]))
    if keywords:
        sections.append("Keywords: " + ", ".join(keywords[:8]))
    if query_hints:
        sections.append("Query hints: " + " | ".join(query_hints[:8]))
    if question_templates:
        sections.append("Follow-up questions: " + " | ".join(question_templates[:4]))
    if evidence_lines:
        sections.append("Evidence:\n" + "\n".join(evidence_lines))
    return "\n\n".join(sections)


def write_topic_profile_documents(
    vectorstore: Any,
    plan: ImportPlan,
    export_topic_index: dict[str, Any],
    *,
    primary_host_name: str,
) -> int:
    topics = [item for item in export_topic_index.get("topics") or [] if isinstance(item, dict)]
    if not topics:
        return 0

    from langchain_core.documents import Document

    speaker_name = primary_host_name or plan.podcast_name
    speaker_id = slugify(speaker_name)
    ids: list[str] = []
    documents: list[Document] = []
    seen_ids: set[str] = set()
    for topic in topics:
        topic_key = str(topic.get("topic_key") or topic.get("topic_id") or slugify(str(topic.get("label") or "topic")))
        doc_id = f"topic-profile:{plan.database_id.casefold()}:{topic_key}"
        if doc_id in seen_ids:
            continue
        seen_ids.add(doc_id)
        ids.append(doc_id)
        documents.append(
            Document(
                page_content=build_topic_profile_payload(topic),
                metadata=sanitize_metadata(
                    {
                        "node_id": doc_id,
                        "stable_document_id": doc_id,
                        "node_type": "topic_profile",
                        "source": f"{plan.export_dir / TOPIC_INDEX_FILENAME}#{topic_key}",
                        "episode_id": f"topic-profile:{topic_key}",
                        "episode_title": str(topic.get("label") or topic_key),
                        "episode_date": str(topic.get("latest_episode_date") or topic.get("first_episode_date") or ""),
                        "episode_sort_key": int(topic.get("latest_episode_date", "").replace("-", "") or 0),
                        "source_type": "topic_profile",
                        "speaker_scope": "single",
                        "speaker": speaker_name,
                        "speakers": [speaker_name],
                        "speaker_id": speaker_id,
                        "podcast": plan.podcast_name,
                        "podcast_id": plan.database_id,
                        "topic_key": topic_key,
                        "topic_label": str(topic.get("label") or topic_key),
                        "topic_kind": str(topic.get("topic_kind") or "subject"),
                        "topic_aliases": topic.get("aliases") or [],
                        "query_hints": topic.get("query_hints") or [],
                    }
                ),
            )
        )

    try:
        vectorstore.delete(ids=ids)
    except Exception:
        pass
    for start in range(0, len(documents), 64):
        batch_docs = documents[start : start + 64]
        batch_ids = ids[start : start + 64]
        if batch_docs:
            vectorstore.add_documents(batch_docs, ids=batch_ids)
    return len(documents)

def write_podcast_metadata(
    plan: ImportPlan,
    imported_episodes: list[dict[str, Any]],
    inserted: int,
    metadata_path: Path,
    embedding_dimension: int | None = None,
    *,
    primary_host_name: str = "",
    primary_host_id: str = "",
    topic_index_present: bool = False,
    topic_count: int = 0,
    topic_profile_count: int = 0,
) -> None:
    all_speakers = sorted(
        {
            str(speaker["name"])
            for episode in imported_episodes
            for speaker in episode.get("speakers", [])
            if speaker.get("name")
        }
    )
    speaker_entries = []
    for speaker in all_speakers:
        entry = {"id": slugify(speaker), "name": speaker}
        if primary_host_name and speaker.casefold() == primary_host_name.casefold():
            entry["description"] = "Primary host"
        speaker_entries.append(entry)
    dates = [episode["episode_date"] for episode in imported_episodes if episode.get("episode_date")]
    payload = {
        "podcast_name": plan.podcast_name,
        "database_id": plan.database_id,
        "collection_name": plan.collection_name,
        "embedding_model": plan.embedding_model,
        "embedding_dimension": embedding_dimension,
        "embedding_device": plan.embedding_device,
        "description": f"Generated from processed RAG output in {plan.processed_data_dir}",
        "date_range": {
            "start": min(dates) if dates else "",
            "end": max(dates) if dates else "",
        },
        "primary_host_id": primary_host_id,
        "primary_host_name": primary_host_name,
        "episode_count": len(imported_episodes),
        "chunk_count": inserted,
        "topic_index_file": TOPIC_INDEX_FILENAME if topic_index_present else "",
        "topic_count": topic_count,
        "topic_profile_count": topic_profile_count,
        "speakers": speaker_entries,
        "episodes": imported_episodes,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "generated_by": "Chroma DB Import UI",
    }
    metadata_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
