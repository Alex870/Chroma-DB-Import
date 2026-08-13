"""Immutable M5 graph and multi-vector sidecars stored outside Chroma."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

CONTRACT = "advanced-retrieval-sidecars-1.0"
MULTIVECTOR_CONTRACT = "multi-vector-index-1.0"


class AdvancedSidecarError(ValueError):
    pass


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _major(version: str) -> int:
    try:
        return int(version.rsplit("-", 1)[-1].split(".", 1)[0])
    except (TypeError, ValueError) as exc:
        raise AdvancedSidecarError(f"invalid contract version: {version}") from exc


def validate_inputs(
    graph: Mapping[str, Any], alignment: Mapping[str, Any], release_id: str
) -> None:
    if _major(str(graph.get("contract_version"))) != 1:
        raise AdvancedSidecarError(
            "unsupported evidence graph major version; upgrade Chroma DB Import"
        )
    if _major(str(alignment.get("contract_version"))) != 1:
        raise AdvancedSidecarError(
            "unsupported late-chunk major version; upgrade Chroma DB Import"
        )
    for name, value in (("graph", graph), ("alignment", alignment)):
        if value.get("parent_corpus_release_id") != release_id:
            raise AdvancedSidecarError(
                f"{name} parent release does not match {release_id}"
            )
        if value.get("disposition") != "prototype":
            raise AdvancedSidecarError(
                f"{name} must remain a prototype until promotion evaluation"
            )


def build_package(
    graph_path: str | Path,
    alignment_path: str | Path,
    vectors_path: str | Path,
    output_root: str | Path,
    *,
    release_id: str,
) -> Path:
    """Build a content-addressed package from explicitly produced token vectors."""
    started = time.monotonic()
    graph, alignment, vectors = (
        _load(graph_path),
        _load(alignment_path),
        _load(vectors_path),
    )
    validate_inputs(graph, alignment, release_id)
    encoder = alignment.get("encoder") or {}
    if (
        not encoder.get("model_revision")
        or encoder.get("model_revision") == "unresolved"
    ):
        raise AdvancedSidecarError("a resolved immutable encoder revision is required")
    rows = list(vectors.get("documents") or [])
    expected = {item["document_id"] for item in alignment.get("documents") or []}
    actual = {str(item.get("document_id") or "") for item in rows}
    if actual != expected:
        raise AdvancedSidecarError(
            "multi-vector document IDs do not exactly align with late-chunk documents"
        )
    dimension = int(vectors.get("dimension") or 0)
    if dimension <= 0:
        raise AdvancedSidecarError("multi-vector dimension must be positive")
    count = 0
    for row in rows:
        values = row.get("vectors") or []
        if any(len(vector) != dimension for vector in values):
            raise AdvancedSidecarError(
                f"vector dimension mismatch for {row.get('document_id')}"
            )
        count += len(values)
    index = {
        "contract_version": MULTIVECTOR_CONTRACT,
        "disposition": "prototype",
        "parent_corpus_release_id": release_id,
        "alignment_id": alignment.get("alignment_id"),
        "encoder": encoder,
        "dimension": dimension,
        "document_count": len(rows),
        "token_vector_count": count,
        "documents": sorted(rows, key=lambda item: item["document_id"]),
        "storage": {
            "format": "portable-json-float-v1",
            "chroma_resident": False,
            "source_payload_bytes": Path(vectors_path).stat().st_size,
            "estimated_float_bytes": count * dimension * 8,
        },
        "build_diagnostics": {
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "explicit_model_artifact_required": True,
        },
    }
    index["index_id"] = "multivector_" + _hash(
        {key: value for key, value in index.items() if key != "build_diagnostics"}
    )
    manifest = {
        "contract_version": CONTRACT,
        "disposition": "prototype",
        "parent_corpus_release_id": release_id,
        "graph_id": graph.get("graph_id"),
        "alignment_id": alignment.get("alignment_id"),
        "multi_vector_index_id": index["index_id"],
        "entry_gate_id": graph.get("entry_gate_id"),
        "adapters": {"graph": "evidence-graph-v1", "late_interaction": "maxsim-v1"},
        "rollback": "remove package and continue dense/hybrid baseline",
    }
    manifest["package_id"] = "advanced_sidecars_" + _hash(manifest)
    destination = Path(output_root) / release_id / manifest["package_id"]
    if destination.exists():
        existing = _load(destination / "manifest.json")
        if existing != manifest:
            raise FileExistsError(
                f"immutable package exists with different content: {destination}"
            )
        return destination
    destination.mkdir(parents=True)
    _atomic_write(destination / "evidence-graph.json", graph)
    _atomic_write(destination / "late-chunk-alignment.json", alignment)
    _atomic_write(destination / "multi-vector-index.json", index)
    _atomic_write(destination / "manifest.json", manifest)
    return destination


def validate_package(
    path: str | Path, *, release_id: str | None = None
) -> dict[str, Any]:
    root = Path(path)
    manifest = _load(root / "manifest.json")
    graph = _load(root / "evidence-graph.json")
    alignment = _load(root / "late-chunk-alignment.json")
    index = _load(root / "multi-vector-index.json")
    if manifest.get("contract_version") != CONTRACT:
        raise AdvancedSidecarError(f"unsupported package contract in {root}")
    bound_release = str(release_id or manifest.get("parent_corpus_release_id") or "")
    validate_inputs(graph, alignment, bound_release)
    if index.get("parent_corpus_release_id") != bound_release:
        raise AdvancedSidecarError("multi-vector release mismatch")
    if manifest.get("multi_vector_index_id") != index.get("index_id"):
        raise AdvancedSidecarError("multi-vector identity mismatch")
    return manifest


def maxsim_search(
    index: Mapping[str, Any],
    query_vectors: list[list[float]],
    *,
    limit: int = 8,
    candidate_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Score only a bounded candidate set using normalized token-vector MaxSim."""
    dimension = int(index.get("dimension") or 0)
    if not query_vectors or any(len(vector) != dimension for vector in query_vectors):
        raise AdvancedSidecarError("query vector dimension mismatch")

    def cosine(left: list[float], right: list[float]) -> float:
        denominator = math.sqrt(sum(v * v for v in left)) * math.sqrt(
            sum(v * v for v in right)
        )
        return (
            sum(a * b for a, b in zip(left, right)) / denominator
            if denominator
            else 0.0
        )

    results = []
    for row in index.get("documents") or []:
        document_id = str(row.get("document_id") or "")
        if candidate_ids is not None and document_id not in candidate_ids:
            continue
        vectors = row.get("vectors") or []
        score = sum(
            max((cosine(query, vector) for vector in vectors), default=0.0)
            for query in query_vectors
        )
        results.append({"document_id": document_id, "score": score})
    return sorted(results, key=lambda item: (-item["score"], item["document_id"]))[
        :limit
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build or validate explicit M5 sidecars; no models are downloaded."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--graph", required=True)
    build.add_argument("--alignment", required=True)
    build.add_argument("--vectors", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--release-id", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("path")
    validate.add_argument("--release-id")
    args = parser.parse_args(argv)
    if args.command == "build":
        print(
            build_package(
                args.graph,
                args.alignment,
                args.vectors,
                args.output,
                release_id=args.release_id,
            )
        )
        return 0
    print(
        json.dumps(
            validate_package(args.path, release_id=args.release_id), sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
