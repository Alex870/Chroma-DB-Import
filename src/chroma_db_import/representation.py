from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


INDEX_SCHEMA_VERSION = "1.0"
CONTEXT_HEADER_VERSION = "1.0"
REPRESENTATION_IMPLEMENTATION_VERSION = "stage-04-v1"
BASELINE_MODEL = "BAAI/bge-large-en-v1.5"
BGE_M3_MODEL = "BAAI/bge-m3"


@dataclass(frozen=True)
class RepresentationSpec:
    provider: str = "sentence_transformers"
    model_id: str = BASELINE_MODEL
    model_revision: str = ""
    dimension: int | None = None
    normalize_embeddings: bool = True
    distance_metric: str = "cosine"
    contextualization: str = "minimal"
    context_header_version: str = CONTEXT_HEADER_VERSION
    output_dimension: int | None = None
    matryoshka_compatible: bool = False
    index_schema_version: str = INDEX_SCHEMA_VERSION
    pooling: str = "mean"
    query_document_mode: str = "document"
    implementation_version: str = REPRESENTATION_IMPLEMENTATION_VERSION

    def validate(self) -> None:
        if self.contextualization not in {"none", "minimal", "full"}:
            raise ValueError("contextualization must be none, minimal, or full")
        if not self.pooling.strip():
            raise ValueError("pooling must be explicit")
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
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def representation_id(self) -> str:
        """Stable identity for the complete embedding/index space."""
        return self.fingerprint()


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
    return stable_fingerprint({"representation": spec.fingerprint(), "text": embedding_text(page_content, metadata, spec.contextualization)})


def embedding_content_hash(page_content: str, metadata: dict[str, Any], spec: RepresentationSpec) -> str:
    """Hash the exact text presented to the embedding provider."""
    return stable_fingerprint({"text": embedding_text(page_content, metadata, spec.contextualization)})


def metadata_fingerprint(metadata: dict[str, Any]) -> str:
    return stable_fingerprint(metadata)


def recommended_batch_size(device: str, available_memory_bytes: int | None = None) -> int:
    if str(device).startswith("cuda"):
        if available_memory_bytes and available_memory_bytes < 8 * 1024**3:
            return 32
        return 64
    if available_memory_bytes and available_memory_bytes < 4 * 1024**3:
        return 8
    return 16
