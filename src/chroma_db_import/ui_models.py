from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chroma_db_import.ui_helpers import safe_folder_name

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
    episodes: list[Episode]
    included_speakers_by_episode: dict[str, set[str]]

    @property
    def export_dir(self) -> Path:
        return self.output_root / safe_folder_name(self.podcast_name)

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
