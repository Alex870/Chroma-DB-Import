import json
import tempfile
import unittest
from pathlib import Path

from chroma_db_import.redundancy_artifacts import RedundancyArtifactError, validate_bundle, write_bundle
from chroma_db_import.redundancy_models import Scope


class RedundancyArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp")
        self.root = Path(self.tmp.name)
        self.scope = Scope("partition", "corpus", "release", "representation")
        self.rows = [{"document_id": "a", "text": "same text", "metadata": {"episode_uid": "partition:e1", "node_type": "leaf"}, "episode_uid": "partition:e1", "node_type": "leaf"}, {"document_id": "b", "text": "same text", "metadata": {"episode_uid": "partition:e2", "node_type": "leaf"}, "episode_uid": "partition:e2", "node_type": "leaf"}]

    def tearDown(self):
        self.tmp.cleanup()

    def test_shared_bundle_manifest_and_hashes_are_self_validating(self):
        bundle = self.root / "bundle"
        write_bundle(bundle, scope=self.scope, occurrences=self.rows, vectors={"a": [1.0, 0.0]}, embedding_inputs={"a": "same", "b": "same"}, storage_mode="shared_input", coverage={"judge_status": "disabled"})
        report = validate_bundle(bundle)
        self.assertTrue(report["valid"])
        self.assertEqual("shared_input", report["manifest"]["storage_mode"])
        self.assertEqual("a", json.loads((bundle / "representative_map.jsonl").read_text(encoding="utf-8").splitlines()[1])["representative_id"])

    def test_artifact_hash_corruption_is_rejected(self):
        bundle = self.root / "bundle"
        write_bundle(bundle, scope=self.scope, occurrences=self.rows, storage_mode="full", coverage={"judge_status": "disabled"})
        evidence = bundle / "evidence.jsonl"
        evidence.write_text(evidence.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
        with self.assertRaises(RedundancyArtifactError):
            validate_bundle(bundle)

    def test_shared_logical_vector_digest_covers_compact_representatives_only(self):
        bundle = self.root / "compact-digest"
        write_bundle(
            bundle,
            scope=self.scope,
            occurrences=self.rows,
            vectors={"a": [1.0, 0.0], "b": [1.0, 0.0]},
            embedding_inputs={"a": "same", "b": "same"},
            storage_mode="shared_input",
            coverage={"judge_status": "disabled"},
        )
        report = validate_bundle(bundle)
        self.assertTrue(report["valid"])
        compact = json.loads((bundle / "vectors" / "vectors.json").read_text(encoding="utf-8"))
        self.assertEqual(["a"], sorted(compact["vectors"]))


if __name__ == "__main__":
    unittest.main()
