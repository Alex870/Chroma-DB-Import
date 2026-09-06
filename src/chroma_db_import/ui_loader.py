from __future__ import annotations

import json
from pathlib import Path

from chroma_db_import.contract import content_fingerprint, partition_identity
from chroma_db_import.importer import cache_fingerprint
from chroma_db_import.ui_helpers import document_speakers, first_present
from chroma_db_import.ui_models import Episode, ProcessedDocument

class EpisodeLoader:
    """Load processed-document caches and normalize them into episode view models."""

    def load_folder(self, folder: Path) -> list[Episode]:
        files = sorted(folder.rglob("*.processed_documents.json"))
        episodes = [self.load_file(path) for path in files]
        return sorted(episodes, key=lambda episode: episode.sort_key)

    def load_file(self, path: Path) -> Episode:
        payload = json.loads(path.read_text(encoding="utf-8"))
        documents = [
            ProcessedDocument(
                page_content=str(item.get("page_content", "")),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in payload.get("documents", [])
            if isinstance(item, dict)
        ]
        speakers = sorted({speaker for doc in documents for speaker in document_speakers(doc.metadata)})
        node_counts: dict[str, int] = {}
        for doc in documents:
            node_type = str(doc.metadata.get("node_type") or "unknown")
            node_counts[node_type] = node_counts.get(node_type, 0) + 1

        first_meta = next((doc.metadata for doc in documents if doc.metadata), {})
        episode_date = str(first_present(doc.metadata.get("episode_date") for doc in documents) or "")
        title = str(
            first_present(doc.metadata.get("episode_title") for doc in documents)
            or payload.get("episode_title")
            or path.stem.replace(".processed_documents", "")
        )
        episode_id = str(first_meta.get("episode_id") or path.stem)

        return Episode(
            path=path,
            fingerprint=cache_fingerprint(path),
            title=title,
            episode_id=episode_id,
            episode_date=episode_date,
            documents=documents,
            speakers=speakers,
            node_counts=node_counts,
            source_content_fingerprint=content_fingerprint(path),
            schema_version=str(payload.get("schema_version") or ""),
            partition_identity=partition_identity(payload),
        )
