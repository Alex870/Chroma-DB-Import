"""M6 release scale, constrained-resource, and clean-machine diagnostics."""

from __future__ import annotations
import argparse, hashlib, json, shutil, time
from pathlib import Path
from typing import Any
from .m6_preflight import build_preflight, write_report
from .releases import inspect_export_work


def scale_plan(
    export_dir: Path,
    workspace: Path,
    *,
    available_bytes: int | None = None,
    memory_limit_bytes: int | None = None,
) -> dict[str, Any]:
    work = inspect_export_work(export_dir)
    storage = int((work.get("storage") or {}).get("export_bytes") or 0)
    database = int((work.get("storage") or {}).get("database_bytes") or 0)
    available = int(
        available_bytes
        if available_bytes is not None
        else shutil.disk_usage(workspace).free
    )
    peak = max(storage + database, storage * 2 + database)
    memory = int(memory_limit_bytes or 0)
    document_work = work.get("vector_work") or {}
    changed = int(document_work.get("add") or 0) + int(document_work.get("update") or 0)
    batch = 32
    if memory and memory < 2_000_000_000:
        batch = 8
    elif memory and memory < 4_000_000_000:
        batch = 16
    elif changed > 100_000:
        batch = 64
    blockers = []
    if available < peak:
        blockers.append("insufficient_disk_for_atomic_stage_and_rollback")
    value = {
        "contract_version": "chroma-release-scale-plan-1.0",
        "source": {
            "export_bytes": storage,
            "database_bytes": database,
            "changed_documents": changed,
        },
        "capacity": {
            "available_bytes": available,
            "estimated_peak_bytes": peak,
            "headroom_bytes": available - peak,
            "memory_limit_bytes": memory or None,
        },
        "recommendation": {
            "embedding_batch_size": batch,
            "collection_write_batch_size": min(1000, max(100, batch * 8)),
            "retain_active_release_during_stage": True,
            "shadow_release_isolated": True,
        },
        "blockers": blockers,
        "runnable": not blockers,
    }
    return value


def benchmark_collection_writes(
    corpus_path: Path, workspace: Path, batch_sizes: list[int]
) -> dict[str, Any]:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    documents = list(corpus.get("documents") or corpus)
    if not documents:
        raise ValueError("benchmark corpus has no documents")
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError(
            "chromadb is required for collection-write benchmark"
        ) from exc
    rows = []
    for batch in sorted(set(max(1, int(x)) for x in batch_sizes)):
        root = workspace / f"batch-{batch}"
        client = chromadb.PersistentClient(path=str(root))
        collection = client.create_collection("m6_benchmark")
        started = time.perf_counter()
        for offset in range(0, len(documents), batch):
            group = documents[offset : offset + batch]
            ids = [
                str(item.get("id") or item.get("document_id") or offset + i)
                for i, item in enumerate(group)
            ]
            texts = [
                str(
                    item.get("text")
                    or item.get("page_content")
                    or item.get("dense_text")
                    or ""
                )
                for item in group
            ]
            vectors = [
                [
                    float(
                        (
                            int(hashlib.sha256(identifier.encode()).hexdigest()[:8], 16)
                            + axis
                        )
                        % 97
                    )
                    / 97
                    for axis in range(8)
                ]
                for identifier in ids
            ]
            collection.add(ids=ids, documents=texts, embeddings=vectors)
        elapsed = time.perf_counter() - started
        rows.append(
            {
                "batch_size": batch,
                "documents": len(documents),
                "elapsed_seconds": round(elapsed, 6),
                "documents_per_second": len(documents) / max(elapsed, 0.000001),
                "storage_bytes": sum(
                    path.stat().st_size for path in root.rglob("*") if path.is_file()
                ),
            }
        )
        client._system.stop()
        chromadb.api.client.SharedSystemClient.clear_system_cache()
        shutil.rmtree(root, ignore_errors=True)
    return {
        "contract_version": "chroma-write-benchmark-1.0",
        "synthetic_vectors": True,
        "dimension": 8,
        "results": rows,
        "best_batch_size": max(rows, key=lambda item: item["documents_per_second"])[
            "batch_size"
        ],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="M6 importer diagnostics and scale tests; no model downloads."
    )
    c = p.add_subparsers(dest="command", required=True)
    pre = c.add_parser("preflight")
    pre.add_argument("--workspace", required=True)
    pre.add_argument("--output")
    scale = c.add_parser("scale-plan")
    scale.add_argument("--export", required=True)
    scale.add_argument("--workspace", required=True)
    scale.add_argument("--available-bytes", type=int)
    scale.add_argument("--memory-limit-bytes", type=int)
    bench = c.add_parser("benchmark-writes")
    bench.add_argument("--corpus", required=True)
    bench.add_argument("--workspace", required=True)
    bench.add_argument("--batch-sizes", type=int, nargs="+", default=[100, 500, 1000])
    a = p.parse_args(argv)
    if a.command == "preflight":
        value = build_preflight(
            "Chroma DB Import",
            Path(a.workspace),
            required_modules=("chromadb",),
            optional_modules=("sentence_transformers",),
        )
        write_report(Path(a.output), value) if a.output else None
    elif a.command == "scale-plan":
        value = scale_plan(
            Path(a.export),
            Path(a.workspace),
            available_bytes=a.available_bytes,
            memory_limit_bytes=a.memory_limit_bytes,
        )
    else:
        value = benchmark_collection_writes(
            Path(a.corpus), Path(a.workspace), a.batch_sizes
        )
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
