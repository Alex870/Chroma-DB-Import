import json
import tempfile
import unittest
from pathlib import Path

from chroma_db_import.dedup_artifacts import DedupArtifactError, validate_dedup_artifacts, write_dedup_artifacts
from chroma_db_import.deduplication import DedupInput, build_exact_plan
from chroma_db_import.representation import embedding_text


class DedupArtifactTests(unittest.TestCase):
    def make_plan(self, profile="safe"):
        rows = []
        for item_id in ("a", "b"):
            metadata = {"stable_document_id": item_id, "node_id": "node", "node_type": "leaf_chunk", "speaker": "Host", "source_span_id": "span"}
            rows.append(DedupInput(item_id, "node", "Repeated", metadata, "p", "c", "p:e", "cache", embedding_text("Repeated", metadata), "leaf_chunk", ("span",)))
        return build_exact_plan(rows, {"profile": profile})

    def test_round_trip_and_hash_failure_fail_closed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp" / "dedup") as directory:
            export = Path(directory)
            release = {"release_contract_version": "chroma-export-release-v1", "release_id": "d", "upstream_release_id": "u", "partition_id": "p", "corpus_id": "c", "representation_id": "r"}
            (export / "release.json").write_text(json.dumps(release), encoding="utf-8")
            write_dedup_artifacts(export, self.make_plan(), release)
            published = json.loads((export / "release.json").read_text(encoding="utf-8"))
            self.assertTrue(validate_dedup_artifacts(export, published)["valid"])
            (export / "dedup_occurrences.jsonl").write_text((export / "dedup_occurrences.jsonl").read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaises(DedupArtifactError):
                validate_dedup_artifacts(export, published)

    def test_audit_retains_every_occurrence(self):
        plan = self.make_plan("audit")
        self.assertEqual(["a", "b"], plan.stored_ids)
        self.assertEqual(1, plan.would_suppress_exact)


if __name__ == "__main__":
    unittest.main()
