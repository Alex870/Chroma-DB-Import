import unittest
from pathlib import Path

from chroma_db_import import ImportConfig, should_include_document
from chroma_db_import.contract import (
    build_import_manifest,
    partition_identities,
    validate_document_items,
    validate_podcast_metadata,
)
from chroma_db_import.importer import representation_spec
from chroma_db_import.representation import QWEN3_MODEL


class Doc:
    def __init__(self, text, metadata):
        self.page_content = text
        self.metadata = metadata


def base_metadata(node_id="leaf_1", node_type="leaf_chunk"):
    return {
        "node_id": node_id,
        "node_type": node_type,
        "source": "episode.json",
        "episode_id": "ep1",
        "episode_title": "Episode",
        "source_type": "json_transcript",
        "speaker_scope": "single",
        "speaker": "Host",
        "episode_date": "2026-01-01",
    }


class ImportContractTests(unittest.TestCase):
    def test_validation_rejects_duplicate_ids_and_bad_position_card(self):
        docs = [
            Doc("leaf text", base_metadata("same", "leaf_chunk")),
            Doc("thesis text", {**base_metadata("thesis", "episode_thesis"), "child_ids": ["same"]}),
            Doc("other text", base_metadata("same", "cluster_summary")),
            Doc("position text", {**base_metadata("position", "position_card"), "claim": "", "child_ids": []}),
        ]

        report = validate_document_items(docs, "fixture")

        self.assertFalse(report.valid)
        self.assertIn("same", report.duplicate_node_ids)
        self.assertEqual(report.malformed_position_cards, 1)

    def test_speaker_filter_keeps_multi_speaker_episode_summary(self):
        config = ImportConfig(selected_speakers=["Host"])
        doc = Doc("summary", {**base_metadata("thesis", "episode_thesis"), "speaker_scope": "multi", "speakers": ["Guest"]})

        self.assertTrue(should_include_document(doc, config))

    def test_speaker_filter_excludes_unselected_single_speaker_doc(self):
        config = ImportConfig(selected_speakers=["Host"])
        doc = Doc("guest", {**base_metadata("guest", "leaf_chunk"), "speaker": "Guest", "speakers": ["Guest"]})

        self.assertFalse(should_include_document(doc, config))

    def test_import_manifest_contains_version_and_counts(self):
        report = validate_document_items(
            [
                Doc("leaf text", base_metadata("leaf", "leaf_chunk")),
                Doc("thesis text", {**base_metadata("thesis", "episode_thesis"), "child_ids": ["leaf"]}),
            ],
            "fixture",
        )

        manifest = build_import_manifest(
            config={"collection_name": "test"},
            source_files=[{"path": "fixture.json", "fingerprint": "abc"}],
            validation_results=[report],
            embedding_model=QWEN3_MODEL,
            embedding_dimension=2560,
            collection_name="collection__qwen3-embedding-4b-shadow",
            representation=representation_spec(ImportConfig()).as_dict(),
        )

        self.assertEqual(manifest["manifest_version"], "2.0")
        self.assertEqual(manifest["document_counts"]["document_count"], 2)

    def test_partition_identity_is_preserved_in_manifest(self):
        payload = {
            "partition": {
                "partition_id": "podcast",
                "partition_display_name": "Podcast",
                "context_type": "podcast",
                "workflow_profile": "podcast",
            },
            "documents": [{"metadata": {"partition_id": "podcast"}}],
        }

        identities = partition_identities(payload)
        manifest = build_import_manifest(
            config={"collection_name": "test"},
            source_files=[{"path": "fixture.json", "fingerprint": "abc"}],
            validation_results=[],
            embedding_model=QWEN3_MODEL,
            embedding_dimension=2560,
            collection_name="collection__qwen3-embedding-4b-shadow",
            representation=representation_spec(ImportConfig()).as_dict(),
            partition_identity=identities[0],
        )

        self.assertEqual([item["partition_id"] for item in identities], ["podcast"])
        self.assertEqual(manifest["partition_id"], "podcast")
        self.assertEqual(manifest["corpus_id"], "podcast")
        self.assertEqual(manifest["partition"]["partition_display_name"], "Podcast")

    def test_podcast_metadata_validation(self):
        report = validate_podcast_metadata(
            {
                "database_id": "db",
                "collection_name": "collection__qwen3-embedding-4b-shadow",
                "embedding_model": QWEN3_MODEL,
                "embedding_dimension": 2560,
                "representation_id": representation_spec(ImportConfig()).representation_id,
                "speakers": [{"id": "host", "name": "Host"}],
                "episodes": [{"source_fingerprint": "abc", "episode_date": "2026-01-01", "speakers": [{"name": "Host"}]}],
                "document_count": 2,
            }
        )

        self.assertTrue(report.valid)


if __name__ == "__main__":
    unittest.main()
