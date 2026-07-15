import json
import unittest
from pathlib import Path

from chroma_db_import.providers import EmbeddingCompatibilityError, probe_embedding_provider
from chroma_db_import.reconciliation import classify_source_records, source_identity
from chroma_db_import.representation import RepresentationSpec
from chroma_db_import.staging import validate_staged_records
from chroma_db_import.ui_export import embed_ui_documents_cached
from chroma_db_import.ui_export import update_should_skip_episode
from chroma_db_import.ui_models import Episode


class FakeProvider:
    def __init__(self, vector):
        self.vector = vector
        self.calls = 0

    def embed_query(self, _text):
        return self.vector

    def embed_documents(self, texts):
        self.calls += 1
        return [self.vector for _ in texts]


class FakeDocument:
    def __init__(self, text):
        self.page_content = text
        self.metadata = {"node_id": "node-1", "source": "episode.json", "speaker": "Host", "episode_date": "2026-01-01"}


class Stage4ContractTests(unittest.TestCase):
    def test_representation_fingerprint_contains_full_embedding_governance(self):
        first = RepresentationSpec(pooling="mean", query_document_mode="document", implementation_version="v1")
        second = RepresentationSpec(pooling="cls", query_document_mode="document", implementation_version="v1")
        self.assertNotEqual(first.representation_id, second.representation_id)
        payload = first.as_dict()
        self.assertEqual(payload["pooling"], "mean")
        self.assertEqual(payload["query_document_mode"], "document")
        self.assertEqual(payload["implementation_version"], "v1")
        self.assertEqual(payload["representation_id"], first.representation_id)

    def test_source_classification_uses_identity_and_speaker_eligibility(self):
        identity = source_identity(
            episode_id="episode-1",
            source_fingerprint="source-a",
            schema_version="2.0",
            representation_id="repr-a",
            content_hash="content-a",
        )
        current = {
            "new": {"episode_id": "e2", "source_fingerprint": "b", "schema_version": "2.0", "representation_id": "repr-a", "content_hash": "b"},
            "eligible": {"episode_id": "e1", "source_fingerprint": "a", "schema_version": "2.0", "representation_id": "repr-a", "content_hash": "a", "source_identity": identity, "eligible_speakers": {"Host", "Guest"}},
            "invalid": {"episode_id": "e3"},
        }
        existing = {
            "eligible": {"source_identity": identity, "eligible_speakers": {"Host"}},
            "removed": {"source_identity": "old"},
        }
        result = classify_source_records(current, existing)
        self.assertEqual(result["new"], "new")
        self.assertEqual(result["eligible"], "newly-eligible-by-speaker-selection")
        self.assertEqual(result["invalid"], "invalid")
        self.assertEqual(result["removed"], "removed-from-source")

    def test_pinned_probe_rejects_nonfinite_and_wrong_dimension(self):
        with self.assertRaises(EmbeddingCompatibilityError):
            probe_embedding_provider(FakeProvider([1.0, float("nan")]))
        with self.assertRaises(EmbeddingCompatibilityError):
            probe_embedding_provider(FakeProvider([1.0, 2.0]), expected_dimension=3)
        self.assertEqual(probe_embedding_provider(FakeProvider([1.0, 2.0]))["dimension"], 2)

    def test_staging_validation_checks_scalars_links_dimensions_and_finiteness(self):
        result = validate_staged_records(
            ["node-1"], ["text"], [{"node_id": "node-1", "source": "episode.json", "bad": {"nested": True}}], [[1.0, float("inf")]], expected_dimension=2,
        )
        self.assertFalse(result.valid)
        self.assertTrue(any("non-finite" in error for error in result.errors))
        self.assertTrue(any("not a Chroma scalar" in error for error in result.errors))

    def test_embedding_cache_reuses_exact_representation_matches(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            provider = FakeProvider([1.0, 2.0])
            spec = RepresentationSpec(dimension=2)
            stats = {"hits": 0, "misses": 0, "invalidations": 0, "cache_size": 0}
            documents = [FakeDocument("same text")]
            embed_ui_documents_cached(documents, provider, spec, Path(tmp), 2, stats)
            embed_ui_documents_cached(documents, provider, spec, Path(tmp), 2, stats)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(stats["hits"], 1)
            self.assertEqual(stats["misses"], 1)

    def test_changed_source_is_not_skipped_by_speaker_selection(self):
        episode = Episode(Path("episode.json"), "path-fingerprint", "Episode", "episode-1", "2026-01-01", [], ["Host"], {}, "new-content", "2.0")
        existing = {"source_file": "episode.json", "source_fingerprint": "path-fingerprint", "source_content_fingerprint": "old-content", "speakers": [{"name": "Host"}]}
        self.assertFalse(update_should_skip_episode(episode, {episode.fingerprint: {"Host"}}, {episode.fingerprint: existing}, {"episode.json": existing}))

    def test_golden_export_fixture_has_downstream_identity(self):
        root = Path(__file__).parent / "fixtures" / "golden_export"
        manifest = json.loads((root / "import_manifest.json").read_text(encoding="utf-8"))
        podcast = json.loads((root / "podcast.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], "2.0")
        self.assertTrue(manifest["representation_id"])
        self.assertEqual(manifest["representation"]["representation_id"], manifest["representation_id"])
        self.assertEqual(podcast["representation_id"], manifest["representation_id"])
        self.assertEqual(podcast["episodes"][0]["source_identity"], manifest["source_files"][0]["source_identity"])


if __name__ == "__main__":
    unittest.main()
