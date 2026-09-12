from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


INDEX_SCHEMA_VERSION = "1.0"
CONTEXT_HEADER_VERSION = "1.0"
REPRESENTATION_IMPLEMENTATION_VERSION = "stage-04-v2"

QWEN3_MODEL = "Qwen/Qwen3-Embedding-4B"
QWEN3_MODEL_REVISION = "5cf2132abc99cad020ac570b19d031efec650f2b"
QWEN3_PROFILE = "qwen3-embedding-4b-shadow"
QWEN3_EMBEDDING_4B_MODEL = QWEN3_MODEL
QWEN3_EMBEDDING_4B_PROFILE = QWEN3_PROFILE
QWEN3_EMBEDDING_4B_REVISION = QWEN3_MODEL_REVISION
PRIMARY_PROFILE = QWEN3_PROFILE

QWEN3_QUERY_INSTRUCTION_PROFILE = "podcast-retrieval-v1"
QWEN3_QUERY_INSTRUCTION = (
    "Instruct: Retrieve podcast passages that best answer the user’s question, "
    "preserving speaker, episode, and viewpoint relevance.\n"
    "Query: {query}"
)

# Qwen3 is the only supported representation. Model choice is deliberately
# not extensible: changing the vector space requires a new application build.
ACTIVE_PROFILE_CHOICES = (QWEN3_PROFILE,)
ALL_PROFILE_NAMES = (QWEN3_PROFILE,)


@dataclass(frozen=True)
class RepresentationProfile:
    name: str
    model_id: str
    model_revision: str
    dimension: int | None
    normalize_embeddings: bool
    distance_metric: str
    inference_dtype: str
    contextualization: str
    query_instruction_profile: str
    query_document_mode: str
    provider: str = "sentence_transformers"
    safe_cuda_batch_size: int = 2
    high_vram: bool = True
    deprecated: bool = False


PROFILE_DEFINITIONS: dict[str, RepresentationProfile] = {
    QWEN3_PROFILE: RepresentationProfile(
        name=QWEN3_PROFILE,
        model_id=QWEN3_MODEL,
        model_revision=QWEN3_MODEL_REVISION,
        dimension=2560,
        normalize_embeddings=True,
        distance_metric="cosine",
        inference_dtype="bfloat16",
        contextualization="minimal",
        query_instruction_profile=QWEN3_QUERY_INSTRUCTION_PROFILE,
        query_document_mode="separate-query-instruction",
        safe_cuda_batch_size=2,
        high_vram=True,
    ),
}


@dataclass(frozen=True)
class RepresentationSpec:
    """The complete identity of one vector space and encoder contract."""

    profile: str = PRIMARY_PROFILE
    provider: str = "sentence_transformers"
    model_id: str = QWEN3_MODEL
    model_revision: str = QWEN3_MODEL_REVISION
    dimension: int | None = 2560
    output_dimension: int | None = None
    normalize_embeddings: bool = True
    distance_metric: str = "cosine"
    inference_dtype: str = "bfloat16"
    contextualization: str = "minimal"
    context_header_version: str = CONTEXT_HEADER_VERSION
    query_instruction_profile: str = QWEN3_QUERY_INSTRUCTION_PROFILE
    query_document_mode: str = "separate-query-instruction"
    matryoshka_compatible: bool = False
    index_schema_version: str = INDEX_SCHEMA_VERSION
    pooling: str = "mean"
    implementation_version: str = REPRESENTATION_IMPLEMENTATION_VERSION

    def validate(self) -> None:
        if not self.profile.strip():
            raise ValueError("profile must be explicit")
        if not self.provider.strip() or not self.model_id.strip():
            raise ValueError("provider and model_id must be explicit")
        if not self.model_revision.strip():
            raise ValueError("model_revision must be pinned")
        if self.dimension is not None and self.dimension <= 0:
            raise ValueError("dimension must be positive")
        if self.contextualization not in {"none", "minimal", "full"}:
            raise ValueError("contextualization must be none, minimal, or full")
        if not self.context_header_version.strip():
            raise ValueError("context_header_version must be explicit")
        if not self.inference_dtype.strip():
            raise ValueError("inference_dtype must be explicit")
        if not self.pooling.strip():
            raise ValueError("pooling must be explicit")
        if not self.query_instruction_profile.strip():
            raise ValueError("query_instruction_profile must be explicit")
        if not self.query_document_mode.strip():
            raise ValueError("query_document_mode must be explicit")
        if not self.implementation_version.strip():
            raise ValueError("implementation_version must be explicit")
        if self.output_dimension is not None:
            if not self.matryoshka_compatible:
                raise ValueError("output_dimension requires a Matryoshka-compatible provider")
            if self.dimension and self.output_dimension > self.dimension:
                raise ValueError("output_dimension cannot exceed the model dimension")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["representation_id"] = self.representation_id
        return payload

    def fingerprint(self) -> str:
        self.validate()
        # Every field that can change the vector space or query contract is
        # included. This is deliberately not a model-only cache key.
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def representation_id(self) -> str:
        return self.fingerprint()


def profile_definition(name: str) -> RepresentationProfile | None:
    return PROFILE_DEFINITIONS.get(str(name or "").strip())


def resolve_profile_name(config: Any) -> str:
    """Resolve the sole supported Qwen3 representation profile."""
    requested = str(getattr(config, "representation_profile", "") or PRIMARY_PROFILE).strip()
    model_id = str(getattr(config, "embedding_model", "") or "").strip()
    provider = str(getattr(config, "embedding_provider", "") or "").strip()
    distance = str(getattr(config, "distance_metric", "") or "").strip()
    query_profile = str(getattr(config, "query_instruction_profile", "") or "").strip()
    inference_dtype = str(getattr(config, "inference_dtype", "") or "").strip()
    if requested not in ALL_PROFILE_NAMES:
        raise ValueError(f"Only the Qwen3 representation profile is supported; got {requested!r}")
    if model_id and model_id != QWEN3_MODEL:
        raise ValueError(
            f"Only {QWEN3_MODEL} is supported; configured embedding model was {model_id!r}"
        )
    if provider and provider != "sentence_transformers":
        raise ValueError("Only the sentence_transformers provider is supported for Qwen3")
    if getattr(config, "normalize_embeddings", True) is not True:
        raise ValueError("Qwen3 embeddings must be normalized")
    if distance and distance != "cosine":
        raise ValueError("Qwen3 uses cosine distance")
    if query_profile and query_profile != QWEN3_QUERY_INSTRUCTION_PROFILE:
        raise ValueError("Qwen3 uses query instruction profile podcast-retrieval-v1")
    if inference_dtype and inference_dtype != "bfloat16":
        raise ValueError("Qwen3 imports must use bfloat16 inference")
    if getattr(config, "output_dimension", None) is not None:
        raise ValueError("Qwen3 imports must retain the full 2560-dimensional output")
    if getattr(config, "matryoshka_compatible", False):
        raise ValueError("Qwen3 output truncation is not supported")
    return requested


def resolve_representation_spec(config: Any, dimension: int | None = None) -> RepresentationSpec:
    profile_name = resolve_profile_name(config)
    profile = profile_definition(profile_name)
    if profile is None:
        raise ValueError(f"Unsupported representation profile: {profile_name}")
    configured_model = str(getattr(config, "embedding_model", "") or "").strip()
    configured_revision = str(getattr(config, "embedding_model_revision", "") or "").strip()
    if configured_model and configured_model != QWEN3_MODEL:
        raise ValueError(f"Only {QWEN3_MODEL} is supported")
    if configured_revision and configured_revision != QWEN3_MODEL_REVISION:
        raise ValueError(
            f"Qwen3 must use the pinned revision {QWEN3_MODEL_REVISION}; got {configured_revision!r}"
        )
    expected_dimension = profile.dimension
    configured_dimension = getattr(config, "embedding_dimension", None)
    if configured_dimension not in (None, expected_dimension):
        raise ValueError(f"Qwen3 requires dimension {expected_dimension}, got {configured_dimension}")
    if dimension is not None and dimension != expected_dimension:
        raise ValueError(
            f"Qwen3 requires dimension {expected_dimension}, got {dimension}"
        )

    spec = RepresentationSpec(
        profile=profile_name,
        provider=profile.provider,
        model_id=profile.model_id,
        model_revision=profile.model_revision,
        dimension=expected_dimension,
        output_dimension=None,
        normalize_embeddings=profile.normalize_embeddings,
        distance_metric=profile.distance_metric,
        inference_dtype=profile.inference_dtype,
        contextualization=str(getattr(config, "contextualization", profile.contextualization)),
        query_instruction_profile=profile.query_instruction_profile,
        query_document_mode=profile.query_document_mode,
        matryoshka_compatible=False,
    )
    spec.validate()
    return spec


def query_text(text: str, spec: RepresentationSpec) -> str:
    """Return the exact query input; documents never use this function."""
    value = str(text or "").strip()
    if spec.query_instruction_profile == QWEN3_QUERY_INSTRUCTION_PROFILE:
        return QWEN3_QUERY_INSTRUCTION.format(query=value)
    return value


def validate_representation_manifest(manifest: dict[str, Any], expected: RepresentationSpec | dict[str, Any]) -> None:
    """Require an exact producer/consumer representation contract match."""
    actual = dict(manifest.get("representation") or {})
    if not actual:
        actual = {
            "model_id": manifest.get("embedding_model"),
            "dimension": manifest.get("embedding_dimension"),
            "representation_id": manifest.get("representation_id"),
        }
    expected_payload = expected.as_dict() if isinstance(expected, RepresentationSpec) else dict(expected)
    fields = (
        "profile",
        "provider",
        "model_id",
        "model_revision",
        "dimension",
        "output_dimension",
        "normalize_embeddings",
        "distance_metric",
        "contextualization",
        "context_header_version",
        "query_instruction_profile",
        "query_document_mode",
        "implementation_version",
        "representation_id",
    )
    mismatches = []
    for field in fields:
        expected_value = expected_payload.get(field)
        if expected_value in (None, ""):
            continue
        actual_value = actual.get(field)
        if field == "dimension" and actual_value in (None, ""):
            actual_value = manifest.get("embedding_dimension")
        if field == "model_id" and actual_value in (None, ""):
            actual_value = manifest.get("embedding_model")
        if actual_value != expected_value:
            mismatches.append(f"{field}: expected {expected_value!r}, got {actual_value!r}")
    if mismatches:
        raise ValueError("representation contract mismatch: " + "; ".join(mismatches))


def profile_storage_suffix(profile: str) -> str:
    normalized = str(profile or PRIMARY_PROFILE).strip()
    if normalized not in ALL_PROFILE_NAMES:
        raise ValueError(f"Only the Qwen3 representation profile is supported; got {normalized!r}")
    return normalized.replace("/", "-")


def resolved_collection_name(base_name: str, profile: str) -> str:
    """Keep the Qwen3 collection identity explicit in storage names."""
    base = str(base_name or "whisper_rag_v2").strip()
    suffix = f"__{profile_storage_suffix(profile)}"
    if base.endswith(suffix):
        return base
    return f"{base}{suffix}"


def resolved_profile_path(base_path: Any, profile: str) -> Any:
    """Return the profile-scoped state/export path."""
    from pathlib import Path

    path = Path(base_path)
    suffix = profile_storage_suffix(profile)
    if path.name == suffix:
        return path
    return path / suffix


def contextual_header(metadata: dict[str, Any], profile: str = "minimal") -> str:
    if profile == "none":
        return ""
    if profile not in {"minimal", "full"}:
        raise ValueError(f"Unsupported contextualization profile: {profile}")
    fields = [
        ("Podcast", metadata.get("podcast_name") or metadata.get("podcast")),
        ("Episode", metadata.get("episode_title")),
        ("Date", metadata.get("episode_date")),
        ("Speaker", metadata.get("speaker")),
        ("Node type", metadata.get("node_type")),
    ]
    if profile == "full":
        fields.extend(
            [
                ("Topic", metadata.get("topic_label") or metadata.get("topic")),
                ("Hierarchy", metadata.get("hierarchy_path") or metadata.get("level")),
                ("Parent", metadata.get("parent_label") or metadata.get("parent_id")),
            ]
        )
    rendered = [f"{label}: {str(value).strip()}" for label, value in fields if value not in (None, "")]
    return "\n".join(rendered)


def embedding_text(page_content: str, metadata: dict[str, Any], profile: str = "minimal") -> str:
    header = contextual_header(metadata, profile)
    text = str(page_content or "").strip()
    return f"{header}\n\n{text}" if header else text


def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def embedding_fingerprint(page_content: str, metadata: dict[str, Any], spec: RepresentationSpec) -> str:
    return stable_fingerprint(
        {"representation": spec.fingerprint(), "text": embedding_text(page_content, metadata, spec.contextualization)}
    )


def embedding_content_hash(page_content: str, metadata: dict[str, Any], spec: RepresentationSpec) -> str:
    return stable_fingerprint({"text": embedding_text(page_content, metadata, spec.contextualization)})


def metadata_fingerprint(metadata: dict[str, Any]) -> str:
    return stable_fingerprint(metadata)


def recommended_batch_size(
    device: str,
    available_memory_bytes: int | None = None,
    *,
    model_id: str = "",
    profile: str = "",
) -> int:
    """Recommend an embedding batch, accounting for Qwen3's VRAM envelope."""
    is_qwen = profile == QWEN3_PROFILE or model_id == QWEN3_MODEL
    if is_qwen:
        if str(device).startswith("cuda"):
            if available_memory_bytes is not None and available_memory_bytes < 12 * 1024**3:
                return 1
            return 2
        return 1
    if str(device).startswith("cuda"):
        if available_memory_bytes and available_memory_bytes < 8 * 1024**3:
            return 32
        return 64
    if available_memory_bytes and available_memory_bytes < 4 * 1024**3:
        return 8
    return 16
