from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from chroma_db_import.asset_filters import DEFAULT_ASSET_FILTER
from chroma_db_import.representation import (
    PRIMARY_PROFILE,
    QWEN3_MODEL,
    QWEN3_MODEL_REVISION,
)

@dataclass
class ImportConfig:
    processed_data_dir: str = "processed_data"
    file_glob: str = "**/*.processed_documents.json"
    asset_filter: str = DEFAULT_ASSET_FILTER
    asset_pattern: str = ""
    state_path: str = "state/chroma_import_state.json"
    persist_dir: str = "chroma_db_raptor_v2"
    collection_name: str = "whisper_rag_v2"
    # Qwen3 is the sole supported representation. These fields are retained
    # because they are emitted in portable metadata and manifests, but they
    # are validated as immutable Qwen3 identity fields.
    representation_profile: str = PRIMARY_PROFILE
    embedding_model: str = QWEN3_MODEL
    embedding_provider: str = "sentence_transformers"
    embedding_model_revision: str = QWEN3_MODEL_REVISION
    embedding_dimension: int | None = 2560
    inference_dtype: str = "bfloat16"
    query_instruction_profile: str = "podcast-retrieval-v1"
    embedding_device: str = "auto"
    allow_model_download: bool = False
    normalize_embeddings: bool = True
    distance_metric: str = "cosine"
    contextualization: str = "minimal"
    output_dimension: int | None = None
    matryoshka_compatible: bool = False
    export_root: str = "exports"
    allow_delete_missing: bool = False
    reconcile: bool = False
    staging_collection_prefix: str = "__stage04__"
    import_batch_size: int = 0
    chroma_batch_size: int = 64
    skip_existing_ids: bool = True
    stop_file: str = "state/stop_after_current_import.txt"
    manifest_path: str = "import_manifest.json"
    preflight_report_path: str = "state/preflight_report.json"
    import_state_dir: str = "state/import_batches"
    embedding_cache_dir: str = "state/embedding_cache"
    cache_embeddings: bool = True
    dry_run: bool = False
    validation_only: bool = False
    rebuild: bool = False
    update: bool = False
    selected_speakers: list[str] | None = None
    troubleshooting_dir: str = "state/diagnostics"
    portable_artifacts: bool = False
    managed_partition_identity: dict[str, str] | None = None
    upstream_release_id: str = ""
    handoff_ids: list[str] | None = None
    # Resolved by the managed context catalog.  The importer accepts this
    # field for headless runs, but does not require users to edit JSON.
    dedup_policy: dict[str, Any] | None = None
    embedding_memory_safety_margin_bytes: int = 512 * 1024**2
    enable_embedding_memory_preflight: bool = True
    # Managed release staging already has an immutable per-release root, so it
    # can opt out of adding a second profile directory level.
    storage_path_isolated: bool = False
    # Partial keeps legacy exports importable; certified rejects records that
    # cannot support auditable temporal retrieval.
    temporal_validation_mode: str = "partial"

def resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path

def load_config(config_path: Path) -> ImportConfig:
    """Load import settings from JSON, falling back to defaults when absent."""
    if not config_path.exists():
        return ImportConfig()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if str(payload.get("temporal_validation_mode") or "partial") not in {"partial", "certified"}:
        raise ValueError("temporal_validation_mode must be 'partial' or 'certified'")
    if "experimental_bge_m3" in payload or "shadow_export_root" in payload:
        raise ValueError("Legacy embedding compatibility settings have been removed; use the pinned Qwen3 profile")
    configured_profile = str(payload.get("representation_profile") or PRIMARY_PROFILE)
    if configured_profile != PRIMARY_PROFILE:
        raise ValueError(f"Only the Qwen3 representation profile is supported; got {configured_profile!r}")
    configured_model = str(payload.get("embedding_model") or "")
    if configured_model and configured_model != QWEN3_MODEL:
        raise ValueError(f"Only {QWEN3_MODEL} is supported; got {configured_model!r}")
    configured_revision = str(payload.get("embedding_model_revision") or "")
    if configured_revision and configured_revision != QWEN3_MODEL_REVISION:
        raise ValueError(f"Qwen3 must use pinned revision {QWEN3_MODEL_REVISION}")
    if "embedding_provider" in payload and payload.get("embedding_provider") not in (None, "", "sentence_transformers"):
        raise ValueError("Only the sentence_transformers provider is supported")
    if "normalize_embeddings" in payload and payload.get("normalize_embeddings") is not True:
        raise ValueError("Qwen3 embeddings must be normalized")
    if "distance_metric" in payload and payload.get("distance_metric") not in (None, "", "cosine"):
        raise ValueError("Qwen3 uses cosine distance")
    if "query_instruction_profile" in payload and payload.get("query_instruction_profile") not in (None, "", "podcast-retrieval-v1"):
        raise ValueError("Qwen3 uses query instruction profile podcast-retrieval-v1")
    if "inference_dtype" in payload and payload.get("inference_dtype") not in (None, "", "bfloat16"):
        raise ValueError("Qwen3 imports must use bfloat16 inference")
    if "embedding_dimension" in payload and payload.get("embedding_dimension") != 2560:
        raise ValueError("Qwen3 imports must declare embedding_dimension=2560")
    allowed = {field.name for field in fields(ImportConfig)}
    return ImportConfig(**{key: value for key, value in payload.items() if key in allowed})
