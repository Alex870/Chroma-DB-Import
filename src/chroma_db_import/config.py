from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

@dataclass
class ImportConfig:
    processed_data_dir: str = "processed_data"
    file_glob: str = "**/*.processed_documents.json"
    state_path: str = "state/chroma_import_state.json"
    persist_dir: str = "chroma_db_raptor_v2"
    collection_name: str = "whisper_rag_v2"
    embedding_model: str = "BAAI/bge-large-en-v1.5"
    embedding_provider: str = "sentence_transformers"
    embedding_model_revision: str = "d4aa6901d3a41ba39fb536a557fa166f842b0e09"
    embedding_device: str = "auto"
    allow_model_download: bool = False
    normalize_embeddings: bool = True
    distance_metric: str = "cosine"
    contextualization: str = "minimal"
    output_dimension: int | None = None
    matryoshka_compatible: bool = False
    experimental_bge_m3: bool = False
    shadow_export_root: str = "exports"
    allow_delete_missing: bool = False
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
    expected_embedding_model: str = ""
    selected_speakers: list[str] | None = None
    troubleshooting_dir: str = "state/diagnostics"

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
    allowed = {field.name for field in fields(ImportConfig)}
    return ImportConfig(**{key: value for key, value in payload.items() if key in allowed})
