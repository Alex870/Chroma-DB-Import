from __future__ import annotations

from typing import Any

from chroma_db_import.ui_helpers import document_speakers
from chroma_db_import.ui_models import Episode, ProcessedDocument

from .models import SelectionPolicy


CONTEXT_NODE_TYPES = {"episode_thesis", "cluster_summary", "topic_profile", "episode_summary"}


def speakers_for_episode(episode: Episode, policy: SelectionPolicy) -> set[str]:
    policy.validate()
    if episode.episode_id in policy.episode_overrides:
        return set(policy.episode_overrides[episode.episode_id])
    available = {str(value) for value in episode.speakers if str(value).strip()}
    if policy.speaker_mode == "allowlist":
        return available.intersection(policy.allowlist_speakers)
    return available.difference(policy.excluded_speakers)


def include_document(episode: Episode, document: ProcessedDocument, policy: SelectionPolicy) -> bool:
    policy.validate()
    if episode.episode_id in policy.excluded_episode_ids:
        return False
    selected = speakers_for_episode(episode, policy)
    speakers = set(document_speakers(document.metadata))
    if not speakers:
        return True
    if str(document.metadata.get("node_type") or "") in CONTEXT_NODE_TYPES:
        # Shared context remains useful when at least one selected speaker is
        # represented, and is always retained for the default all-speakers view.
        return bool(selected) and (policy.speaker_mode == "all" or speakers.intersection(selected) or len(speakers) > 1)
    return bool(speakers.intersection(selected))


def select_documents(episode: Episode, policy: SelectionPolicy) -> list[ProcessedDocument]:
    return [document for document in episode.documents if include_document(episode, document, policy)]


def policy_summary(policy: SelectionPolicy) -> dict[str, Any]:
    policy.validate()
    return {
        "speaker_mode": policy.speaker_mode,
        "excluded_speakers": list(policy.excluded_speakers),
        "allowlist_speakers": list(policy.allowlist_speakers),
        "episode_override_count": len(policy.episode_overrides),
        "excluded_episode_count": len(policy.excluded_episode_ids),
        "asset_filter": policy.asset_filter,
        "asset_pattern": policy.asset_pattern,
        "fingerprint": policy.fingerprint,
    }
