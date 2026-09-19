from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chroma_db_import.asset_filters import DEFAULT_ASSET_FILTER
from chroma_db_import.ui_helpers import safe_folder_name
from chroma_db_import.representation import PRIMARY_PROFILE, QWEN3_PROFILE, profile_storage_suffix, resolved_collection_name

@dataclass(frozen=True)
class DeviceOption:
    label: str
    value: str

@dataclass
class ProcessedDocument:
    page_content: str
    metadata: dict[str, Any]

@dataclass
class Episode:
    path: Path
    fingerprint: str
    title: str
    episode_id: str
    episode_date: str
    documents: list[ProcessedDocument]
    speakers: list[str]
    node_counts: dict[str, int]
    source_content_fingerprint: str = ""
    schema_version: str = ""
    partition_identity: dict[str, str] = field(default_factory=dict)
    # Modern jobs may read an immutable copy while preserving original
    # provenance for manifests and user-facing metadata.
    source_file_path: Path | None = None

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.episode_date or "9999-99-99", self.title.lower())

@dataclass
class ImportPlan:
    podcast_name: str
    database_id: str
    processed_data_dir: Path
    output_root: Path
    collection_name: str
    embedding_model: str
    embedding_device: str
    contextualization: str = "minimal"
    asset_filter: str = DEFAULT_ASSET_FILTER
    asset_pattern: str = ""
    representation_profile: str = PRIMARY_PROFILE
    embedding_model_revision: str = ""
    inference_dtype: str = "bfloat16"
    query_instruction_profile: str = "podcast-retrieval-v1"
    allow_delete_missing: bool = False
    reconcile: bool = False
    episodes: list[Episode] = field(default_factory=list)
    included_speakers_by_episode: dict[str, set[str]] = field(default_factory=dict)
    # Modern workflow callers may bind an exact final destination. Legacy
    # callers continue to derive it from output_root/podcast_name.
    final_export_dir: Path | None = None
    temporal_validation_mode: str = "partial"

    @property
    def resolved_profile(self) -> str:
        return self.representation_profile or QWEN3_PROFILE

    @property
    def export_dir(self) -> Path:
        if self.final_export_dir is not None:
            return Path(self.final_export_dir)
        base = self.output_root / safe_folder_name(self.podcast_name)
        return base / profile_storage_suffix(self.resolved_profile)

    @property
    def resolved_collection_name(self) -> str:
        return resolved_collection_name(self.collection_name, self.resolved_profile)

@dataclass
class ImportProgress:
    message: str
    current: int = 0
    total: int = 0

@dataclass
class ImportSummary:
    inserted: int
    skipped_episodes: int
    imported_episodes: int
    export_dir: Path
    skipped_documents: int = 0
    elapsed_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    operation_report: dict[str, Any] = field(default_factory=dict)
