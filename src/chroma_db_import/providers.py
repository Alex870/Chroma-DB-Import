from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable

import chroma_db_import.runtime as runtime
from chroma_db_import.representation import (
    QWEN3_MODEL,
    QWEN3_MODEL_REVISION,
    RepresentationSpec,
    query_text,
)


PINNED_MODEL_REVISIONS = {
    QWEN3_MODEL: QWEN3_MODEL_REVISION,
}


class EmbeddingCompatibilityError(RuntimeError):
    """Raised when a provider cannot safely read or write the target space."""


class EmbeddingMemoryError(EmbeddingCompatibilityError):
    """Raised when the selected model/batch cannot fit the available device memory."""


def pinned_revision(model_id: str, configured: str = "") -> str:
    if model_id != QWEN3_MODEL:
        raise EmbeddingCompatibilityError(
            f"Only {QWEN3_MODEL} is supported; got {model_id!r}"
        )
    if configured and configured != QWEN3_MODEL_REVISION:
        raise EmbeddingCompatibilityError(
            f"Qwen3 must use pinned revision {QWEN3_MODEL_REVISION}; got {configured!r}"
        )
    return QWEN3_MODEL_REVISION


def resolve_device(requested: str) -> str:
    requested = (requested or "auto").lower()
    if requested != "auto":
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _normalize(vector: Iterable[Any]) -> list[float]:
    values = [float(value) for value in vector]
    if not values or any(not math.isfinite(value) for value in values):
        raise EmbeddingCompatibilityError("Embedding provider returned an empty or non-finite vector")
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise EmbeddingCompatibilityError("Embedding provider returned a zero vector")
    return [value / norm for value in values]


class RepresentationEmbeddingProvider:
    """Small adapter enforcing document/query encoding for a representation."""

    def __init__(self, provider: Any, spec: RepresentationSpec):
        self.provider = provider
        self.spec = spec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # Documents receive the deterministic contextualized text from the
        # importer. They must never receive the Qwen query instruction.
        vectors = self.provider.embed_documents(list(texts))
        return [_normalize(vector) if self.spec.normalize_embeddings else [float(value) for value in vector] for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        # Qwen3's instruction is a compatibility field, not a UI-only string.
        vector = self.provider.embed_query(query_text(text, self.spec))
        return _normalize(vector) if self.spec.normalize_embeddings else [float(value) for value in vector]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)


# A descriptive alias for callers that want to make the adapter's purpose
# explicit without coupling to the Qwen model name.
InstructedEmbeddingProvider = RepresentationEmbeddingProvider


def _model_kwargs(spec: RepresentationSpec, device: str, revision: str) -> dict[str, Any]:
    values: dict[str, Any] = {"device": device, "local_files_only": True}
    if revision:
        values["revision"] = revision
    if spec.inference_dtype == "bfloat16":
        try:
            import torch

            values["model_kwargs"] = {"torch_dtype": torch.bfloat16}
        except Exception:
            # SentenceTransformers can still report a clear load error if the
            # CUDA/PyTorch runtime is unavailable.
            pass
    return values


def _actual_dtype(provider: Any, requested: str) -> str:
    for candidate in (
        getattr(getattr(provider, "client", None), "dtype", None),
        getattr(getattr(getattr(provider, "client", None), "_first_module", lambda: None)(), "auto_model", None),
    ):
        value = getattr(candidate, "dtype", candidate)
        if value is not None:
            return str(value).replace("torch.", "")
    return requested


def create_embedding_provider(spec: RepresentationSpec, requested_device: str) -> tuple[Any, dict[str, Any]]:
    """Load a pinned, already-cached provider without triggering a download."""
    _require_qwen_spec(spec)
    spec.validate()
    runtime.load_runtime_deps()
    device = resolve_device(requested_device)
    revision = pinned_revision(spec.model_id, spec.model_revision)
    model_kwargs = _model_kwargs(spec, device, revision)
    encode_kwargs = {"normalize_embeddings": spec.normalize_embeddings}
    try:
        base_provider = runtime.HuggingFaceEmbeddings(
            model_name=spec.model_id,
            model_kwargs=model_kwargs,
            encode_kwargs=encode_kwargs,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Embedding model {spec.model_id!r} at revision {revision or 'default'} is not cached. "
            "Use the explicit --download-model action before importing."
        ) from exc
    provider = RepresentationEmbeddingProvider(base_provider, spec)
    return provider, {
        "provider": spec.provider,
        "profile": spec.profile,
        "model_id": spec.model_id,
        "revision": revision,
        "resolved_revision": revision,
        "inference_dtype": spec.inference_dtype,
        "actual_dtype": _actual_dtype(base_provider, spec.inference_dtype),
        "query_instruction_profile": spec.query_instruction_profile,
        "device": device,
        "representation_id": spec.representation_id,
        "dimension": spec.dimension,
    }


def probe_embedding_provider(
    provider: Any,
    *,
    expected_dimension: int | None = None,
    probe_text: str = "podcast import pinned embedding probe",
) -> dict[str, Any]:
    """Run the deterministic probe used before staging or promotion."""
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


def available_cuda_memory_bytes(device: str = "cuda") -> int | None:
    if not str(device).startswith("cuda"):
        return None
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        index = torch.device(device).index
        free, _total = torch.cuda.mem_get_info(index)
        return int(free)
    except Exception:
        return None


def preflight_embedding_memory(
    provider: Any,
    spec: RepresentationSpec,
    *,
    device: str,
    batch_size: int,
    safety_margin_bytes: int = 512 * 1024**2,
) -> dict[str, Any]:
    """Measure a representative batch before any collection writes."""
    result: dict[str, Any] = {
        "status": "not_applicable",
        "device": device,
        "profile": spec.profile,
        "batch_size": int(batch_size),
        "safety_margin_bytes": int(safety_margin_bytes),
        "available_before_bytes": available_cuda_memory_bytes(device),
    }
    if not str(device).startswith("cuda"):
        result["reason"] = "CPU fallback; CUDA memory probe not applicable"
        return result
    try:
        import torch

        if not torch.cuda.is_available():
            result["reason"] = "CUDA is unavailable; CPU fallback selected"
            return result
        baseline_allocated = int(torch.cuda.memory_allocated())
        torch.cuda.reset_peak_memory_stats()
        provider.embed_documents(["podcast import representative memory probe"] * max(1, int(batch_size)))
        allocated = int(torch.cuda.max_memory_allocated())
        reserved = int(torch.cuda.max_memory_reserved())
        additional = max(0, allocated - baseline_allocated)
        result.update(
            {
                "status": "passed",
                "baseline_allocated_bytes": baseline_allocated,
                "peak_allocated_bytes": allocated,
                "peak_reserved_bytes": reserved,
                "additional_peak_allocated_bytes": additional,
                "required_with_safety_margin_bytes": additional + int(safety_margin_bytes),
            }
        )
        available = result.get("available_before_bytes")
        if available is not None and available < additional + int(safety_margin_bytes):
            result["status"] = "warning"
            result["fits_safety_margin"] = False
            result["warning"] = "available VRAM is below the measured peak plus safety margin"
        else:
            result["fits_safety_margin"] = True
        return result
    except Exception as exc:
        if is_cuda_oom(exc):
            result.update(
                {
                    "status": "failed",
                    "fits_safety_margin": False,
                    "error": "CUDA out of memory during representative preflight",
                    "guidance": "Lower the embedding batch size to 1 or select the CPU device.",
                }
            )
            raise EmbeddingMemoryError(
                "Qwen3 memory preflight ran out of CUDA memory. Lower the embedding batch size to 1 "
                "or select the CPU device; no collection writes were made."
            ) from exc
        result.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        raise EmbeddingCompatibilityError(f"Embedding memory preflight failed: {exc}") from exc


def is_cuda_oom(exc: BaseException) -> bool:
    text = str(exc).lower()
    if "out of memory" in text and ("cuda" in text or "cublas" in text or "gpu" in text):
        return True
    try:
        import torch

        return isinstance(exc, torch.cuda.OutOfMemoryError)
    except Exception:
        return False


def download_model(spec: RepresentationSpec, cache_dir: Path | None = None) -> Path:
    """Explicit network-enabled artifact acquisition entrypoint."""
    from huggingface_hub import snapshot_download

    _require_qwen_spec(spec)
    revision = pinned_revision(spec.model_id, spec.model_revision)
    path = snapshot_download(
        repo_id=spec.model_id,
        revision=revision or None,
        cache_dir=str(cache_dir) if cache_dir else None,
    )
    return Path(path)


def _require_qwen_spec(spec: RepresentationSpec) -> None:
    checks = {
        "model_id": (spec.model_id, QWEN3_MODEL),
        "model_revision": (spec.model_revision, QWEN3_MODEL_REVISION),
        "dimension": (spec.dimension, 2560),
        "provider": (spec.provider, "sentence_transformers"),
        "normalize_embeddings": (spec.normalize_embeddings, True),
        "distance_metric": (spec.distance_metric, "cosine"),
        "query_instruction_profile": (spec.query_instruction_profile, "podcast-retrieval-v1"),
        "query_document_mode": (spec.query_document_mode, "separate-query-instruction"),
    }
    for field, (actual, expected) in checks.items():
        if actual != expected:
            raise EmbeddingCompatibilityError(
                f"Qwen3 provider contract mismatch for {field}: expected {expected!r}, got {actual!r}"
            )
