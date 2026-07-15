from __future__ import annotations

from pathlib import Path
from typing import Any
import math

import chroma_db_import.runtime as runtime
from chroma_db_import.representation import RepresentationSpec


PINNED_MODEL_REVISIONS = {
    "BAAI/bge-large-en-v1.5": "d4aa6901d3a41ba39fb536a557fa166f842b0e09",
    "BAAI/bge-m3": "5617a9f61b028005a4858fdac845db406aefb181",
}


class EmbeddingCompatibilityError(RuntimeError):
    """Raised when a provider cannot safely read or write the target space."""


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
    return provider, {
        "provider": spec.provider,
        "model_id": spec.model_id,
        "revision": revision,
        "resolved_revision": revision,
        "device": device,
        "representation_id": spec.representation_id,
    }


def probe_embedding_provider(
    provider: Any,
    *,
    expected_dimension: int | None = None,
    probe_text: str = "podcast import pinned embedding probe",
) -> dict[str, Any]:
    """Run the deterministic probe used before any staging or promotion."""
    try:
        vector = provider.embed_query(probe_text)
    except Exception as exc:
        raise EmbeddingCompatibilityError(f"Pinned embedding probe failed: {exc}") from exc
    if not vector:
        raise EmbeddingCompatibilityError("Pinned embedding probe returned an empty vector")
    try:
        values = [float(value) for value in vector]
    except (TypeError, ValueError) as exc:
        raise EmbeddingCompatibilityError("Pinned embedding probe returned non-numeric values") from exc
    if not all(math.isfinite(value) for value in values):
        raise EmbeddingCompatibilityError("Pinned embedding probe returned non-finite values")
    dimension = len(values)
    if expected_dimension is not None and dimension != expected_dimension:
        raise EmbeddingCompatibilityError(
            f"Embedding dimension mismatch: provider={dimension}, target={expected_dimension}. "
            "Use a new export or an explicit migration path."
        )
    return {"probe": probe_text, "dimension": dimension, "finite": True}


def download_model(spec: RepresentationSpec, cache_dir: Path | None = None) -> Path:
    """Explicit network-enabled artifact acquisition entrypoint."""
    from huggingface_hub import snapshot_download

    revision = pinned_revision(spec.model_id, spec.model_revision)
    path = snapshot_download(repo_id=spec.model_id, revision=revision or None, cache_dir=str(cache_dir) if cache_dir else None)
    return Path(path)
