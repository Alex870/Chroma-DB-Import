from pathlib import Path
import unittest

pytest_import_error = None
try:
    import PySide6  # noqa: F401
except ModuleNotFoundError as exc:
    pytest_import_error = exc

if pytest_import_error:
    raise unittest.SkipTest("PySide6 is not installed in this environment.")

from chroma_db_import.ui import (
    Episode,
    ProcessedDocument,
    merge_episode_entries,
    update_should_skip_episode,
)


class UpdateImportTests(unittest.TestCase):
    def episode(self) -> Episode:
        return Episode(
            path=Path("show.processed_documents.json"),
            fingerprint="episode-fingerprint",
            title="Show",
            episode_id="show-1",
            episode_date="2026-01-01",
            documents=[
                ProcessedDocument("host text", {"node_id": "host", "speaker": "HostA"}),
                ProcessedDocument("guest text", {"node_id": "guest", "speaker": "GuestB"}),
            ],
            speakers=["GuestB", "HostA"],
            node_counts={},
        )

    def existing_entry(self) -> dict:
        episode = self.episode()
        return {
            "source_file": str(episode.path),
            "source_fingerprint": episode.fingerprint,
            "episode_date": episode.episode_date,
            "episode_title": episode.title,
            "document_count": 1,
            "speakers": [{"id": "hosta", "name": "HostA"}],
        }

    def test_update_skips_when_selected_speakers_are_already_imported(self) -> None:
        episode = self.episode()
        existing = self.existing_entry()

        should_skip = update_should_skip_episode(
            episode,
            {episode.fingerprint: {"HostA"}},
            {episode.fingerprint: existing},
            {str(episode.path): existing},
        )

        self.assertTrue(should_skip)

    def test_update_imports_when_selection_contains_new_speaker(self) -> None:
        episode = self.episode()
        existing = self.existing_entry()

        should_skip = update_should_skip_episode(
            episode,
            {episode.fingerprint: {"HostA", "GuestB"}},
            {episode.fingerprint: existing},
            {str(episode.path): existing},
        )

        self.assertFalse(should_skip)

    def test_episode_metadata_merges_speakers_and_document_counts(self) -> None:
        episode = self.episode()
        existing = self.existing_entry()
        imported = {
            "source_file": str(episode.path),
            "source_fingerprint": episode.fingerprint,
            "episode_date": episode.episode_date,
            "episode_title": episode.title,
            "document_count": 2,
            "speakers": [{"id": "guestb", "name": "GuestB"}],
        }

        merged = merge_episode_entries([existing], [imported])

        self.assertEqual(merged[0]["document_count"], 3)
        self.assertEqual(
            [speaker["name"] for speaker in merged[0]["speakers"]],
            ["GuestB", "HostA"],
        )


if __name__ == "__main__":
    unittest.main()
