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
from chroma_db_import.contract import (
    IMPORTER_VERSION,
    build_import_manifest,
    content_fingerprint,
    partition_identities,
    summarize_reports,
    validate_document_items,
)
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
from chroma_db_import.representation import (
    INDEX_SCHEMA_VERSION,
    QWEN3_MODEL,
    QWEN3_MODEL_REVISION,
    QWEN3_QUERY_INSTRUCTION_PROFILE,
    recommended_batch_size,
    resolve_representation_spec,
    resolved_collection_name,
    resolved_profile_path,
)
from chroma_db_import.deduplication import DedupPlan

def config_payload(config: ImportConfig) -> dict[str, Any]:
    return {field.name: getattr(config, field.name) for field in fields(ImportConfig)}

def existing_manifest(config: ImportConfig, project_dir: Path) -> dict[str, Any] | None:
    spec = resolve_representation_spec(config)
    configured_persist_dir = resolve_path(project_dir, config.persist_dir)
    persist_dir = configured_persist_dir if config.storage_path_isolated else resolved_profile_path(configured_persist_dir, spec.profile)
    path = persist_dir / config.manifest_path
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
    requested_spec = resolve_representation_spec(config)
    requested_collection = resolved_collection_name(config.collection_name, requested_spec.profile)
    if manifest.get("embedding_model") and manifest.get("embedding_model") != config.embedding_model:
        warnings.append(
            f"Existing manifest embedding_model={manifest.get('embedding_model')} differs from requested {config.embedding_model}."
        )
    if manifest.get("collection_name") and manifest.get("collection_name") != requested_collection:
        warnings.append(
            f"Existing manifest collection_name={manifest.get('collection_name')} differs from requested {requested_collection}."
        )
    previous_sources = {item.get("content_fingerprint") for item in manifest.get("source_files", []) if isinstance(item, dict)}
    current_sources = {content_fingerprint(path) for path in files}
    if previous_sources and previous_sources != current_sources:
        warnings.append("Selected source cache files differ from the existing manifest.")
    return warnings

def preflight_files(
    config: ImportConfig,
    project_dir: Path,
    files: list[Path],
    *,
    allow_mixed_partitions: bool = False,
) -> dict[str, Any]:
    """Validate selected source caches and summarize compatibility risks before import."""
    reports = []
    source_files = []
    partition_records: list[dict[str, Any]] = []
    for path in files:
        payload = load_processed_payload(path)
        docs = load_processed_documents(path)
        docs = [doc for doc in docs if should_include_document(doc, config)]
        report = validate_document_items(docs, str(path))
        payload_representation = payload.get("representation") if isinstance(payload.get("representation"), dict) else {}
        declared_model = str(payload.get("embedding_model") or payload_representation.get("model_id") or "").strip()
        if declared_model and declared_model != QWEN3_MODEL:
            report.errors.append(
                f"{path} declares unsupported embedding_model={declared_model!r}; only {QWEN3_MODEL!r} is supported"
            )
            report.valid = False
        declared_revision = str(payload.get("embedding_model_revision") or payload.get("model_revision") or payload_representation.get("model_revision") or "").strip()
        if declared_revision and declared_revision != QWEN3_MODEL_REVISION:
            report.errors.append(f"{path} declares unsupported Qwen3 model revision")
            report.valid = False
        declared_dimension = payload.get("embedding_dimension") or payload_representation.get("dimension")
        if declared_dimension not in (None, "", 2560):
            report.errors.append(f"{path} declares unsupported embedding_dimension={declared_dimension!r}; Qwen3 requires 2560")
            report.valid = False
        declared_query_profile = str(payload.get("query_instruction_profile") or payload_representation.get("query_instruction_profile") or "").strip()
        if declared_query_profile and declared_query_profile != QWEN3_QUERY_INSTRUCTION_PROFILE:
            report.errors.append(f"{path} declares unsupported query_instruction_profile={declared_query_profile!r}")
            report.valid = False
        reports.append(report)
        source_files.append(
            {
                "path": str(path),
                "fingerprint": cache_fingerprint(path),
                "content_fingerprint": content_fingerprint(path),
                "schema_version": payload.get("schema_version"),
                "pipeline_version": payload.get("pipeline_version"),
                "prompt_version": payload.get("prompt_version"),
                "embedding_model": declared_model,
                "embedding_model_revision": declared_revision,
                "embedding_dimension": declared_dimension,
                "query_instruction_profile": declared_query_profile,
                "document_count": len(docs),
                "validation": report.as_dict(),
            }
        )
        identities = partition_identities(payload)
        partition_records.append({"path": str(path), "identities": identities})

    partition_ids = sorted(
        {
            str(identity.get("partition_id"))
            for record in partition_records
            for identity in record["identities"]
            if identity.get("partition_id")
        }
    )
    corpus_ids = sorted(
        {
            str(identity.get("corpus_id"))
            for record in partition_records
            for identity in record["identities"]
            if identity.get("corpus_id")
        }
    )
    identity_keys = sorted(
        {
            str(identity.get("partition_id") or json.dumps(identity, sort_keys=True))
            for record in partition_records
            for identity in record["identities"]
        }
    )
    legacy_files = [record["path"] for record in partition_records if not record["identities"]]
    partition_errors: list[str] = []
    if len(identity_keys) > 1 and not allow_mixed_partitions:
        partition_errors.append(
            "Selected processed caches belong to multiple processing spaces: "
            + ", ".join(identity_keys)
        )
    if len(corpus_ids) > 1 and not allow_mixed_partitions:
        partition_errors.append(
            "Selected processed caches belong to multiple corpus IDs: "
            + ", ".join(corpus_ids)
        )
    if identity_keys and legacy_files and not allow_mixed_partitions:
        partition_errors.append(
            "Selected processed caches mix partition-aware files with legacy files without a partition identity: "
            + ", ".join(legacy_files[:5])
        )
    selected_partition = next(
        (
            identity
            for record in partition_records
            for identity in record["identities"]
            if identity.get("partition_id")
        ),
        {},
    )
    summary = summarize_reports(reports)
    safety_warnings = rebuild_safety_warnings(config, project_dir, files)
    representation = resolve_representation_spec(config)
    report = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "importer_version": IMPORTER_VERSION,
        "processed_data_dir": str(resolve_path(project_dir, config.processed_data_dir)),
        "file_count": len(files),
        "valid": all(item.valid for item in reports) and not partition_errors,
        "summary": summary,
        "source_files": source_files,
        "safety_warnings": safety_warnings,
        "representation": representation.as_dict(),
        "representation_storage": {
            "collection_name": resolved_collection_name(config.collection_name, representation.profile),
            "profile": representation.profile,
        },
        "partition_isolation": {
            "valid": not partition_errors,
            "partition": selected_partition,
            "partition_ids": partition_ids,
            "corpus_ids": corpus_ids,
            "legacy_file_count": len(legacy_files),
            "errors": partition_errors,
            "policy": "allow-mixed" if allow_mixed_partitions else "single-partition",
        },
    }
    return report

def write_preflight_report(config: ImportConfig, project_dir: Path, report: dict[str, Any]) -> Path:
    spec = resolve_representation_spec(config)
    path = resolved_profile_path(resolve_path(project_dir, config.preflight_report_path), spec.profile)
    write_json(path, report)
    return path

def write_manifest(config: ImportConfig, project_dir: Path, importer: ChromaImporter, files: list[Path], preflight: dict[str, Any], *, dedup_plan: DedupPlan | None = None) -> Path:
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
    partition_info = preflight.get("partition_isolation") or {}
    manifest = build_import_manifest(
        config=config_payload(config),
        source_files=source_files,
        validation_results=validation_results,
        embedding_model=importer.spec.model_id,
        embedding_dimension=importer.embedding_dimension,
        collection_name=importer.collection_name,
        selected_speakers=sorted(selected_speaker_set(config)),
        compatibility_warnings=preflight.get("safety_warnings") or [],
        representation={**importer.spec.as_dict(), "representation_id": importer.spec.representation_id},
        operation={"mode": "reconcile" if config.reconcile else "update" if config.update else "import"},
        embedding_cache=getattr(importer, "last_cache_stats", {}),
        partition_identity=(
            partition_info.get("partition")
            if len(partition_info.get("partition_ids") or []) == 1
            and not partition_info.get("legacy_file_count")
            else None
        ),
        dedup=(dedup_plan.as_counts() if dedup_plan is not None else None),
    )
    if partition_info.get("partition_ids"):
        manifest["partition_ids"] = list(partition_info["partition_ids"])
    manifest["index_schema_version"] = INDEX_SCHEMA_VERSION
    manifest["representation"] = importer.spec.as_dict()
    manifest["provider_diagnostics"] = importer.provider_diagnostics
    manifest["resource_measurements"] = {
        "memory_preflight": importer.memory_preflight,
        "batch_size": max(
            1,
            int(
                config.import_batch_size
                or recommended_batch_size(
                    importer.provider_diagnostics.get("device", "cpu"),
                    model_id=importer.spec.model_id,
                    profile=importer.spec.profile,
                )
            ),
        ),
        "device": importer.provider_diagnostics.get("device"),
    }
    if dedup_plan is not None:
        manifest["dedup"] = {
            "policy": dedup_plan.policy,
            "plan_fingerprint": dedup_plan.plan_fingerprint,
            "counts": dedup_plan.as_counts(),
        }
    path = importer.persist_dir / config.manifest_path
    write_json(path, manifest)
    return path

def benchmark_embeddings(config: ImportConfig, project_dir: Path) -> int:
    runtime.load_runtime_deps()
    from chroma_db_import.providers import create_embedding_provider, probe_embedding_provider

    spec = resolve_representation_spec(config)
    embeddings, diagnostics = create_embedding_provider(spec, config.embedding_device)
    samples = ["benchmark sample text for chroma import"] * max(1, min(128, config.import_batch_size))
    started = time.time()
    vectors = embeddings.embed_documents(samples)
    elapsed = max(0.001, time.time() - started)
    dim = len(vectors[0]) if vectors else None
    print("Embedding benchmark")
    print(f"  profile: {spec.profile}")
    print(f"  model: {spec.model_id}@{spec.model_revision}")
    print(f"  batch_size: {len(samples)}")
    print(f"  embedding_dimension: {dim}")
    print(f"  docs_per_second: {len(samples) / elapsed:.2f}")
    print(f"  dtype: {diagnostics.get('actual_dtype', spec.inference_dtype)}")
    print(f"  representation_id: {spec.representation_id}")
    print("  recommendation: use CUDA when available; reduce import_batch_size to 1 if memory is constrained.")
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

    spec = resolve_representation_spec(config)
    configured_persist_dir = resolve_path(project_dir, config.persist_dir)
    persist_dir = configured_persist_dir if config.storage_path_isolated else resolved_profile_path(configured_persist_dir, spec.profile)
    return chromadb.PersistentClient(path=str(persist_dir))

def list_collections(config: ImportConfig, project_dir: Path) -> int:
    runtime.load_runtime_deps()
    spec = resolve_representation_spec(config)
    client = chroma_client(config, project_dir)
    collections = client.list_collections()
    print(f"Collections in {resolve_path(project_dir, config.persist_dir)}:")
    for collection in collections:
        name = getattr(collection, "name", str(collection))
        print(f"- {name}")
    return 0

def inspect_collection(config: ImportConfig, project_dir: Path) -> int:
    runtime.load_runtime_deps()
    spec = resolve_representation_spec(config)
    collection_name = resolved_collection_name(config.collection_name, spec.profile)
    client = chroma_client(config, project_dir)
    collection = client.get_collection(collection_name)
    print(f"Collection: {collection_name}")
    print(f"  count: {collection.count()}")
    print(f"  metadata: {getattr(collection, 'metadata', None)}")
    manifest = existing_manifest(config, project_dir)
    if manifest:
        print(f"  manifest_profile: {(manifest.get('representation') or {}).get('profile', spec.profile)}")
        print(f"  manifest_embedding_model: {manifest.get('embedding_model')}")
        print(f"  manifest_embedding_dimension: {manifest.get('embedding_dimension')}")
        print(f"  manifest_source_files: {len(manifest.get('source_files', []))}")
    return 0

def delete_collection(config: ImportConfig, project_dir: Path) -> int:
    runtime.load_runtime_deps()
    spec = resolve_representation_spec(config)
    collection_name = resolved_collection_name(config.collection_name, spec.profile)
    client = chroma_client(config, project_dir)
    client.delete_collection(collection_name)
    print(f"Deleted collection: {collection_name}")
    return 0

def release_build(project_dir: Path) -> int:
    version_path = project_dir / "VERSION.json"
    write_json(version_path, {"importer_version": IMPORTER_VERSION, "created_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    print(f"Release metadata written: {version_path}")
    print("Install the pinned dependencies into the managed environment with: conda run -n chroma-db-import python -m pip install -r chroma_db_import_requirements.txt")
    return 0
