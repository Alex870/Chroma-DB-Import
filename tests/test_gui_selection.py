from __future__ import annotations

import unittest
from pathlib import Path

from chroma_db_import.ui_models import Episode, ProcessedDocument
from chroma_db_import.workflow.models import SelectionPolicy
from chroma_db_import.workflow.selection import select_documents, speakers_for_episode


class GuiSelectionTests(unittest.TestCase):
    def episode(self) -> Episode:
        return Episode(
            path=Path("fixture.json"), fingerprint="fixture", title="Fixture", episode_id="episode-1", episode_date="2026-01-01",
            documents=[
                ProcessedDocument("host", {"node_id": "host", "speaker": "Host", "node_type": "transcript"}),
                ProcessedDocument("shared", {"node_id": "shared", "speakers": ["Host", "Guest"], "node_type": "episode_thesis"}),
                ProcessedDocument("guest", {"node_id": "guest", "speaker": "Guest", "node_type": "transcript"}),
            ], speakers=["Host", "Guest"], node_counts={},
        )

    def test_allowlist_keeps_shared_context_and_selected_speaker(self) -> None:
        episode = self.episode()
        policy = SelectionPolicy(speaker_mode="allowlist", allowlist_speakers=["Guest"])
        self.assertEqual(speakers_for_episode(episode, policy), {"Guest"})
        self.assertEqual([item.metadata["node_id"] for item in select_documents(episode, policy)], ["shared", "guest"])

    def test_excluded_speaker_does_not_exclude_new_episode_by_default(self) -> None:
        episode = self.episode()
        policy = SelectionPolicy(excluded_speakers=["Guest"])
        self.assertEqual([item.metadata["node_id"] for item in select_documents(episode, policy)], ["host", "shared"])
