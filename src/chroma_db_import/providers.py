from __future__ import annotations

from pathlib import Path
from typing import Any

import chroma_db_import.runtime as runtime
from chroma_db_import.representation import RepresentationSpec


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
    if spec.model_revision:
        model_kwargs["revision"] = spec.model_revision
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
    return provider, {"provider": spec.provider, "model_id": spec.model_id, "revision": spec.model_revision, "device": device}


def download_model(spec: RepresentationSpec, cache_dir: Path | None = None) -> Path:
    """Explicit network-enabled artifact acquisition entrypoint."""
    from huggingface_hub import snapshot_download

    path = snapshot_download(repo_id=spec.model_id, revision=spec.model_revision or None, cache_dir=str(cache_dir) if cache_dir else None)
    return Path(path)
