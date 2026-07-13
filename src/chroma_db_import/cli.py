from __future__ import annotations

import argparse
import datetime as dt
import time
from pathlib import Path

import chroma_db_import.runtime as runtime
from chroma_db_import.config import ImportConfig, load_config
from chroma_db_import.diagnostics import (
    benchmark_embeddings,
    delete_collection,
    inspect_collection,
    list_collections,
    preflight_files,
    release_build,
    write_diagnostic_bundle,
    write_manifest,
    write_preflight_report,
)
from chroma_db_import.importer import ChromaImporter, cache_fingerprint, iter_cache_files
from chroma_db_import.state import load_state, save_state


def run_import(config: ImportConfig, project_dir: Path, one_file: bool) -> int:
    """Import processed cache files into a Chroma collection with resumable state."""
    runtime.load_runtime_deps()
    from chroma_db_import.config import resolve_path

    processed_data_dir = resolve_path(project_dir, config.processed_data_dir)
    state_path = resolve_path(project_dir, config.state_path)
    stop_file = resolve_path(project_dir, config.stop_file)
    state = load_state(state_path)

    files = iter_cache_files(processed_data_dir, config.file_glob)
    preflight = preflight_files(config, project_dir, files)
    preflight_path = write_preflight_report(config, project_dir, preflight)
    print(f"Preflight report written: {preflight_path}")
    print(
        f"Preflight: files={preflight['file_count']}, documents={preflight['summary']['document_count']}, "
        f"errors={preflight['summary']['error_count']}, warnings={preflight['summary']['warning_count']}"
    )
    for warning in preflight.get("safety_warnings") or []:
        print(f"  safety warning: {warning}")
    if not preflight["valid"]:
        for source in preflight["source_files"]:
            for error in source["validation"]["errors"][:5]:
                print(f"  validation error in {source['path']}: {error}")
        if config.validation_only or config.dry_run:
            return 1
        raise ValueError("Preflight validation failed; no Chroma writes were performed.")
    if config.validation_only:
        print("Validation-only mode complete; no Chroma writes performed.")
        return 0
    if config.dry_run:
        print("Dry-run mode: no Chroma writes will be performed.")
        for source in preflight["source_files"]:
            print(
                f"  {Path(source['path']).name}: docs={source['document_count']} "
                f"node_types={source['validation']['counts_by_node_type']}"
            )
        return 0

    importer = ChromaImporter(config, project_dir)
    pending = []
    for path in files:
        fingerprint = cache_fingerprint(path)
        entry = state.get("files", {}).get(fingerprint)
        if not entry or entry.get("status") != "completed":
            pending.append((path, fingerprint))

    print(f"Found {len(files)} processed cache file(s); {len(pending)} pending import.")
    batch_started = time.time()
    total_inserted = 0
    total_skipped = 0
    total_embedding_hits = 0
    for idx, (path, fingerprint) in enumerate(pending, 1):
        if stop_file.exists():
            print("Stop file detected before starting next import.")
            break

        print(f"\nFile {idx}/{len(pending)}: {path}")
        try:
            started = time.time()
            result = importer.import_cache(path, dry_run=False)
            elapsed = max(0.001, time.time() - started)
            total_inserted += int(result.get("inserted") or 0)
            total_skipped += int(result.get("skipped_existing") or 0)
            total_embedding_hits += int(result.get("embedding_cache_hits") or 0)
            state.setdefault("files", {})[fingerprint] = {
                "path": str(path),
                "status": "completed",
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "elapsed_seconds": round(elapsed, 3),
                **result,
            }
            save_state(state_path, state)
            throughput = int(result.get("inserted") or 0) / elapsed
            print(
                f"  completed: inserted={result['inserted']}, skipped_existing={result['skipped_existing']}, "
                f"embedding_cache_hits={result.get('embedding_cache_hits', 0)}, docs_per_second={throughput:.2f}"
            )
        except Exception as exc:
            state.setdefault("files", {})[fingerprint] = {
                "path": str(path),
                "status": "failed",
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
            }
            save_state(state_path, state)
            raise

        if one_file:
            print("Imported one file; stopping because --one-file was set.")
            break

    manifest_path = write_manifest(config, project_dir, importer, files, preflight)
    elapsed_total = max(0.001, time.time() - batch_started)
    print(
        f"Import summary: inserted={total_inserted}, skipped_existing={total_skipped}, "
        f"embedding_cache_hits={total_embedding_hits}, elapsed={elapsed_total:.1f}s, "
        f"docs_per_second={total_inserted / elapsed_total:.2f}"
    )
    print(f"Import manifest written: {manifest_path}")
    print("\nImport complete.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import processed podcast RAG documents into Chroma.")
    parser.add_argument("--config", default="chroma_db_import_config.json", help="Path to the JSON config file.")
    parser.add_argument("--processed-data-dir", help="Override config processed_data_dir.")
    parser.add_argument("--persist-dir", help="Override config persist_dir.")
    parser.add_argument("--collection-name", help="Override config collection_name.")
    parser.add_argument("--one-file", action="store_true", help="Import only one pending cache file.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and summarize expected changes without writing Chroma.")
    parser.add_argument("--validation-only", action="store_true", help="Run preflight validation only.")
    parser.add_argument("--rebuild", action="store_true", help="Treat this run as a rebuild; safety warnings are included in reports.")
    parser.add_argument("--update", action="store_true", help="Treat this run as an update append.")
    parser.add_argument("--embedding-benchmark", action="store_true", help="Benchmark embedding throughput and exit.")
    parser.add_argument("--diagnostic-bundle", action="store_true", help="Write troubleshooting diagnostics and exit.")
    parser.add_argument("--release-build", action="store_true", help="Write local release metadata and print release build steps.")
    parser.add_argument("--list-collections", action="store_true", help="List Chroma collections in persist_dir.")
    parser.add_argument("--inspect-collection", action="store_true", help="Inspect the configured Chroma collection.")
    parser.add_argument("--delete-collection", action="store_true", help="Delete the configured Chroma collection.")
    parser.add_argument("--allow-delete-missing", action="store_true", help="After dry-run review, allow update reconciliation to delete missing IDs.")
    parser.add_argument("--contextualization", choices=("none", "minimal", "full"), help="Embedding-only contextual header profile.")
    parser.add_argument("--experimental-bge-m3", action="store_true", help="Build with the pinned experimental BGE-M3 dense provider profile.")
    return parser.parse_args()


def main() -> int:
    """CLI entry point for Chroma import, diagnostics, and collection inspection."""
    args = parse_args()
    config_path = Path(args.config).expanduser()
    project_dir = config_path.resolve().parent if config_path.exists() else Path.cwd()
    config = load_config(config_path)
    if args.processed_data_dir:
        config.processed_data_dir = args.processed_data_dir
    if args.persist_dir:
        config.persist_dir = args.persist_dir
    if args.collection_name:
        config.collection_name = args.collection_name
    if args.dry_run:
        config.dry_run = True
    if args.validation_only:
        config.validation_only = True
    if args.rebuild:
        config.rebuild = True
    if args.update:
        config.update = True
    if args.allow_delete_missing:
        config.allow_delete_missing = True
    if args.contextualization:
        config.contextualization = args.contextualization
    if args.experimental_bge_m3:
        config.experimental_bge_m3 = True

    if args.embedding_benchmark:
        return benchmark_embeddings(config, project_dir)
    if args.diagnostic_bundle:
        return write_diagnostic_bundle(config, project_dir)
    if args.release_build:
        return release_build(project_dir)
    if args.list_collections:
        return list_collections(config, project_dir)
    if args.inspect_collection:
        return inspect_collection(config, project_dir)
    if args.delete_collection:
        return delete_collection(config, project_dir)
    return run_import(config, project_dir, args.one_file)
