import json
import tempfile
import unittest
from pathlib import Path

from chroma_db_import.asset_filters import (
    ASSET_FILTER_ALL,
    ASSET_FILTER_CLEANED,
    ASSET_FILTER_CUSTOM,
    ASSET_FILTER_RAW,
    ASSET_FILTER_REVIEWED,
    select_asset_files,
)


class AssetFilterTests(unittest.TestCase):
    def write_cache(self, root: Path, name: str, **payload) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_reviewed_filter_matches_new_metadata_and_legacy_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = self.write_cache(root, "reviewed.cache.processed_documents.json", selected_variant="reviewed_llm")
            legacy = self.write_cache(
                root,
                "legacy.processed_documents.json",
                source_path="D:/transcripts/TFM_20260103_reviewed_speaker_transcript.json",
            )
            cleaned = self.write_cache(
                root,
                "cleaned.processed_documents.json",
                source_path="D:/transcripts/TFM_20260103_cleaned_speaker_transcript.json",
            )

            selection = select_asset_files([metadata, legacy, cleaned], ASSET_FILTER_REVIEWED)

            self.assertEqual({metadata, legacy}, set(selection.selected_paths))
            self.assertEqual((cleaned,), selection.excluded_paths)

    def test_raw_and_cleaned_are_not_confused_with_reviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = self.write_cache(root, "raw.processed_documents.json", source_path="episode_speaker_transcript.json")
            cleaned = self.write_cache(root, "cleaned.processed_documents.json", source_path="episode_cleaned_speaker_transcript.json")
            reviewed = self.write_cache(root, "reviewed.processed_documents.json", source_path="episode_reviewed_speaker_transcript.json")

            self.assertEqual((cleaned,), select_asset_files([raw, cleaned, reviewed], ASSET_FILTER_CLEANED).selected_paths)
            self.assertEqual((raw,), select_asset_files([raw, cleaned, reviewed], ASSET_FILTER_RAW).selected_paths)

    def test_reviewed_filter_keeps_newest_cache_per_episode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = self.write_cache(
                root,
                "reviewed.older.processed_documents.json",
                episode_id="TFM 20260103",
                selected_variant="reviewed_llm",
                created_at="09/05/2026 17:14:36",
            )
            newest = self.write_cache(
                root,
                "reviewed.newest.processed_documents.json",
                episode_id="TFM 20260103",
                selected_variant="reviewed_llm",
                created_at="09/06/2026 19:54:59",
            )
            other = self.write_cache(
                root,
                "reviewed.other.processed_documents.json",
                episode_id="TFM 20260107",
                selected_variant="reviewed_llm",
                created_at="09/06/2026 19:55:00",
            )

            selection = select_asset_files([older, newest, other], ASSET_FILTER_REVIEWED)

            self.assertEqual({newest, other}, set(selection.selected_paths))
            self.assertEqual((older,), selection.excluded_paths)

    def test_all_and_custom_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_cache(root, "one.processed_documents.json", source_path="episode_reviewed_speaker_transcript.json")
            second = self.write_cache(root, "two.processed_documents.json", source_path="episode_cleaned_speaker_transcript.json")

            self.assertEqual((first, second), select_asset_files([first, second], ASSET_FILTER_ALL).selected_paths)
            self.assertEqual(
                (first,),
                select_asset_files(
                    [first, second],
                    ASSET_FILTER_CUSTOM,
                    "*_reviewed_speaker_transcript.json",
                ).selected_paths,
            )
            self.assertEqual(
                (),
                select_asset_files([first, second], ASSET_FILTER_CUSTOM).selected_paths,
            )


if __name__ == "__main__":
    unittest.main()
