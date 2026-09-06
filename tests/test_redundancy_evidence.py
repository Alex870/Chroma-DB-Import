import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from chroma_db_import.redundancy_evidence import RedundancyEvidenceError, build_occurrence_index, build_representative_map, write_evidence_inventory
from chroma_db_import.redundancy_models import Scope


class RedundancyEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp")
        self.root = Path(self.tmp.name)
        self.scope = Scope("partition", "corpus", "release", "representation")

    def tearDown(self):
        self.tmp.cleanup()

    def test_evidence_preserves_original_text_and_builds_filter_indexes(self):
        rows = [{
            "document_id": "occ-1",
            "text": "  Original  whitespace\n",
            "metadata": {"episode_uid": "partition:episode", "episode_date": "2025-01-01", "speaker": "Host", "node_type": "leaf"},
        }]
        evidence_path = self.root / "evidence.jsonl"
        write_evidence_inventory(rows, evidence_path, scope=self.scope)
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual("  Original  whitespace\n", payload["text"])
        index = self.root / "occurrences.sqlite3"
        build_occurrence_index(evidence_path, index)
        connection = sqlite3.connect(index)
        try:
            self.assertEqual(("occ-1",), connection.execute("SELECT document_id FROM occurrences").fetchone())
            self.assertEqual(("Host",), connection.execute("SELECT speaker FROM speakers").fetchone())
            self.assertEqual(("2025-01-01",), connection.execute("SELECT episode_date FROM occurrences").fetchone())
        finally:
            connection.close()

    def test_representatives_share_only_verified_actual_inputs(self):
        rows = [
            {"document_id": "a", "text": "A", "representation_id": "representation", "partition_id": "partition", "corpus_id": "corpus"},
            {"document_id": "b", "text": "B", "representation_id": "representation", "partition_id": "partition", "corpus_id": "corpus"},
            {"document_id": "c", "text": "C", "representation_id": "representation", "partition_id": "partition", "corpus_id": "corpus", "embedding_input_hash": "sha256:not-the-input"},
        ]
        result = build_representative_map(rows, embedding_inputs={"a": "same", "b": "same", "c": "different"}, scope=self.scope)
        by_id = {row["document_id"]: row for row in result}
        self.assertEqual("a", by_id["b"]["representative_id"])
        self.assertEqual("c", by_id["c"]["representative_id"])
        self.assertIsNone(by_id["a"]["reason"])

    def test_audit_and_alias_rows_do_not_displace_retained_representative(self):
        rows = [
            {"document_id": "canonical", "text": "same", "storage_status": "retained"},
            {"document_id": "audit-copy", "text": "same", "storage_status": "audit", "alias_target_id": "canonical"},
            {"document_id": "hierarchy-alias", "text": "same", "alias_target_id": "canonical"},
        ]
        mapping = build_representative_map(rows, embedding_inputs={item["document_id"]: "same" for item in rows}, scope=self.scope)
        by_id = {row["document_id"]: row["representative_id"] for row in mapping}
        self.assertEqual({"canonical"}, set(by_id.values()))

    def test_evidence_writer_rejects_foreign_scope_rows(self):
        with self.assertRaises(RedundancyEvidenceError):
            write_evidence_inventory([{"document_id": "foreign", "text": "x", "partition_id": "other"}], self.root / "foreign.jsonl", scope=self.scope)


if __name__ == "__main__":
    unittest.main()
