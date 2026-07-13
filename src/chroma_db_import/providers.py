from __future__ import annotations

from pathlib import Path
from typing import Any

import chroma_db_import.runtime as runtime
from chroma_db_import.representation import RepresentationSpec


PINNED_MODEL_REVISIONS = {
    "BAAI/bge-large-en-v1.5": "d4aa6901d3a41ba39fb536a557fa166f842b0e09",
    "BAAI/bge-m3": "5617a9f61b028005a4858fdac845db406aefb181",
}


def pinned_revision(model_id: str, configured: str = "") -> str:
    return configured or PINNED_MODEL_REVISIONS.get(model_id, "")


def resolve_device(requested: str) -> str:
    requested = (requested or "auto").lower()
    if requested != "auto":
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def create_embedding_provider(spec: RepresentationSpec, requested_device: str) -> tuple[Any, dict[str, Any]]:
    """Load a pinned, already-cached provider without triggering a network download."""
    runtime.load_runtime_deps()
    device = resolve_device(requested_device)
    model_kwargs: dict[str, Any] = {"device": device, "local_files_only": True}
    revision = pinned_revision(spec.model_id, spec.model_revision)
    if revision:
        model_kwargs["revision"] = revision
    encode_kwargs = {"normalize_embeddings": spec.normalize_embeddings}
    try:
        provider = runtime.HuggingFaceEmbeddings(
            model_name=spec.model_id,
            model_kwargs=model_kwargs,
            encode_kwargs=encode_kwargs,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Embedding model {spec.model_id!r} at revision {spec.model_revision or 'default'} is not cached. "
            "Use the explicit --download-model action before importing."
        ) from exc
    return provider, {"provider": spec.provider, "model_id": spec.model_id, "revision": revision, "resolved_revision": revision, "device": device}


def download_model(spec: RepresentationSpec, cache_dir: Path | None = None) -> Path:
    """Explicit network-enabled artifact acquisition entrypoint."""
    from huggingface_hub import snapshot_download

    revision = pinned_revision(spec.model_id, spec.model_revision)
    path = snapshot_download(repo_id=spec.model_id, revision=revision or None, cache_dir=str(cache_dir) if cache_dir else None)
    return Path(path)
