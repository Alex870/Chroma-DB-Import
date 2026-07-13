from __future__ import annotations

import datetime as dt
import json
import shutil
import time
from dataclasses import fields
from pathlib import Path
from typing import Any

import chroma_db_import.runtime as runtime
from chroma_db_import.config import ImportConfig, resolve_path
from chroma_db_import.contract import IMPORTER_VERSION, build_import_manifest, content_fingerprint, summarize_reports, validate_document_items
from chroma_db_import.importer import (
    ChromaImporter,
    cache_fingerprint,
    detect_embedding_dimension,
    load_processed_documents,
    load_processed_payload,
    selected_speaker_set,
    should_include_document,
)
from chroma_db_import.state import write_json
from chroma_db_import.contracts import require_compatible_contract
from chroma_db_import.representation import INDEX_SCHEMA_VERSION

def config_payload(config: ImportConfig) -> dict[str, Any]:
    return {field.name: getattr(config, field.name) for field in fields(ImportConfig)}

def existing_manifest(config: ImportConfig, project_dir: Path) -> dict[str, Any] | None:
    path = resolve_path(project_dir, config.persist_dir) / config.manifest_path
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            require_compatible_contract(
                str(payload.get("manifest_version") or "1.0"),
                "2.0",
                artifact_path=path,
                component="Chroma DB Import",
            )
            return payload
        return None
    except json.JSONDecodeError:
        return None

def rebuild_safety_warnings(config: ImportConfig, project_dir: Path, files: list[Path]) -> list[str]:
    warnings = []
    manifest = existing_manifest(config, project_dir)
    if not manifest:
        return warnings
    if manifest.get("embedding_model") and manifest.get("embedding_model") != config.embedding_model:
        warnings.append(
            f"Existing manifest embedding_model={manifest.get('embedding_model')} differs from requested {config.embedding_model}."
        )
    if manifest.get("collection_name") and manifest.get("collection_name") != config.collection_name:
        warnings.append(
            f"Existing manifest collection_name={manifest.get('collection_name')} differs from requested {config.collection_name}."
        )
    previous_sources = {item.get("content_fingerprint") for item in manifest.get("source_files", []) if isinstance(item, dict)}
    current_sources = {content_fingerprint(path) for path in files}
    if previous_sources and previous_sources != current_sources:
        warnings.append("Selected source cache files differ from the existing manifest.")
    if config.expected_embedding_model and config.expected_embedding_model != config.embedding_model:
        warnings.append(
            f"Chat compatibility warning: expected embedding model {config.expected_embedding_model}, import config uses {config.embedding_model}."
        )
    return warnings

def preflight_files(config: ImportConfig, project_dir: Path, files: list[Path]) -> dict[str, Any]:
    """Validate selected source caches and summarize compatibility risks before import."""
    reports = []
    source_files = []
    for path in files:
        payload = load_processed_payload(path)
        docs = load_processed_documents(path)
        docs = [doc for doc in docs if should_include_document(doc, config)]
        report = validate_document_items(docs, str(path))
        reports.append(report)
        source_files.append(
            {
                "path": str(path),
                "fingerprint": cache_fingerprint(path),
                "content_fingerprint": content_fingerprint(path),
                "schema_version": payload.get("schema_version"),
                "pipeline_version": payload.get("pipeline_version"),
                "prompt_version": payload.get("prompt_version"),
                "document_count": len(docs),
                "validation": report.as_dict(),
            }
        )
    summary = summarize_reports(reports)
    safety_warnings = rebuild_safety_warnings(config, project_dir, files)
    report = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "importer_version": IMPORTER_VERSION,
        "processed_data_dir": str(resolve_path(project_dir, config.processed_data_dir)),
        "file_count": len(files),
        "valid": all(item.valid for item in reports),
        "summary": summary,
        "source_files": source_files,
        "safety_warnings": safety_warnings,
    }
    return report

def write_preflight_report(config: ImportConfig, project_dir: Path, report: dict[str, Any]) -> Path:
    path = resolve_path(project_dir, config.preflight_report_path)
    write_json(path, report)
    return path

def write_manifest(config: ImportConfig, project_dir: Path, importer: ChromaImporter, files: list[Path], preflight: dict[str, Any]) -> Path:
    """Write the downstream import manifest consumed by chat and inspection tools."""
    validation_results = [
        validate_document_items(load_processed_documents(path), str(path))
        for path in files
    ]
    source_files = [
        {
            "path": str(path),
            "fingerprint": cache_fingerprint(path),
            "content_fingerprint": content_fingerprint(path),
            "schema_version": load_processed_payload(path).get("schema_version"),
            "pipeline_version": load_processed_payload(path).get("pipeline_version"),
        }
        for path in files
    ]
    manifest = build_import_manifest(
        config=config_payload(config),
        source_files=source_files,
        validation_results=validation_results,
        embedding_model=config.embedding_model,
        embedding_dimension=importer.embedding_dimension,
        collection_name=config.collection_name,
        selected_speakers=sorted(selected_speaker_set(config)),
        compatibility_warnings=preflight.get("safety_warnings") or [],
    )
    manifest["index_schema_version"] = INDEX_SCHEMA_VERSION
    manifest["representation"] = importer.spec.as_dict()
    path = importer.persist_dir / config.manifest_path
    write_json(path, manifest)
    return path

def benchmark_embeddings(config: ImportConfig, project_dir: Path) -> int:
    runtime.load_runtime_deps()
    embeddings = runtime.HuggingFaceEmbeddings(model_name=config.embedding_model)
    samples = ["benchmark sample text for chroma import"] * max(1, min(128, config.import_batch_size))
    started = time.time()
    vectors = embeddings.embed_documents(samples)
    elapsed = max(0.001, time.time() - started)
    dim = len(vectors[0]) if vectors else None
    print("Embedding benchmark")
    print(f"  model: {config.embedding_model}")
    print(f"  batch_size: {len(samples)}")
    print(f"  embedding_dimension: {dim}")
    print(f"  docs_per_second: {len(samples) / elapsed:.2f}")
    print("  recommendation: use CUDA when available; reduce import_batch_size if memory warnings appear.")
    return 0

def memory_snapshot() -> dict[str, Any]:
    payload: dict[str, Any] = {}
    try:
        import psutil

        mem = psutil.virtual_memory()
        payload.update({"system_memory_total": mem.total, "system_memory_available": mem.available, "system_memory_percent": mem.percent})
    except Exception:
        payload["system_memory"] = "psutil unavailable"
    try:
        import torch

        payload["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            payload["cuda_device"] = torch.cuda.get_device_name(0)
            payload["cuda_memory_allocated"] = int(torch.cuda.memory_allocated())
            payload["cuda_memory_reserved"] = int(torch.cuda.memory_reserved())
    except Exception:
        payload["cuda"] = "torch unavailable"
    return payload

def write_diagnostic_bundle(config: ImportConfig, project_dir: Path) -> int:
    """Capture runtime, package, and state snapshots for troubleshooting support."""
    diagnostics_dir = resolve_path(project_dir, config.troubleshooting_dir)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_dir = diagnostics_dir / stamp
    bundle_dir.mkdir(parents=True, exist_ok=True)
    write_json(bundle_dir / "config.json", config_payload(config))
    write_json(bundle_dir / "memory.json", memory_snapshot())
    packages = {}
    for package in ("chromadb", "langchain_chroma", "langchain_huggingface", "sentence_transformers"):
        try:
            module = __import__(package)
            packages[package] = getattr(module, "__version__", "unknown")
        except Exception as exc:
            packages[package] = f"unavailable: {type(exc).__name__}: {exc}"
    write_json(bundle_dir / "packages.json", packages)
    state_path = resolve_path(project_dir, config.state_path)
    if state_path.exists():
        shutil.copy2(state_path, bundle_dir / state_path.name)
    print(f"Diagnostic bundle written: {bundle_dir}")
    return 0

def chroma_client(config: ImportConfig, project_dir: Path) -> Any:
    import chromadb

    return chromadb.PersistentClient(path=str(resolve_path(project_dir, config.persist_dir)))

def list_collections(config: ImportConfig, project_dir: Path) -> int:
    runtime.load_runtime_deps()
    client = chroma_client(config, project_dir)
    collections = client.list_collections()
    print(f"Collections in {resolve_path(project_dir, config.persist_dir)}:")
    for collection in collections:
        name = getattr(collection, "name", str(collection))
        print(f"- {name}")
    return 0

def inspect_collection(config: ImportConfig, project_dir: Path) -> int:
    runtime.load_runtime_deps()
    client = chroma_client(config, project_dir)
    collection = client.get_collection(config.collection_name)
    print(f"Collection: {config.collection_name}")
    print(f"  count: {collection.count()}")
    print(f"  metadata: {getattr(collection, 'metadata', None)}")
    manifest = existing_manifest(config, project_dir)
    if manifest:
        print(f"  manifest_embedding_model: {manifest.get('embedding_model')}")
        print(f"  manifest_embedding_dimension: {manifest.get('embedding_dimension')}")
        print(f"  manifest_source_files: {len(manifest.get('source_files', []))}")
    return 0

def delete_collection(config: ImportConfig, project_dir: Path) -> int:
    runtime.load_runtime_deps()
    client = chroma_client(config, project_dir)
    client.delete_collection(config.collection_name)
    print(f"Deleted collection: {config.collection_name}")
    return 0

def release_build(project_dir: Path) -> int:
    version_path = project_dir / "VERSION.json"
    write_json(version_path, {"importer_version": IMPORTER_VERSION, "created_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    print(f"Release metadata written: {version_path}")
    print("Create a clean environment with: python -m venv .release_venv; .release_venv\\Scripts\\pip install -r chroma_db_import_requirements.txt")
    return 0
