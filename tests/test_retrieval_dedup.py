import unittest

from chroma_db_import.deduplication import DeduplicationError
from chroma_db_import.retrieval_dedup import RetrievalDedupError, candidate_limit, collapse_ranked_hits, resolve_alias


class RetrievalDedupTests(unittest.TestCase):
    def manifest(self):
        return {"partition_id": "p", "corpus_id": "c", "release_id": "r", "representation_id": "rep", "exact_groups": [{"duplicate_group_id": "g", "canonical_id": "a", "member_ids": ["a", "b"]}]}

    def rows(self):
        return [
            {"document_id": "a", "duplicate_group_id": "g", "preferred_canonical_id": "a", "storage_status": "retained", "alias_target_id": None},
            {"document_id": "b", "duplicate_group_id": "g", "preferred_canonical_id": "a", "storage_status": "suppressed_exact", "alias_target_id": "a"},
            {"document_id": "c", "duplicate_group_id": None, "preferred_canonical_id": "c", "storage_status": "retained", "alias_target_id": None},
        ]

    def hit(self, item_id, rank):
        return {"id": item_id, "rank": rank, "metadata": {"partition_id": "p", "corpus_id": "c", "release_id": "r", "representation_id": "rep"}}

    def test_filter_first_eligible_collapse_and_underfill(self):
        rows = self.rows()
        rows[1] = {**rows[1], "storage_status": "retained", "alias_target_id": None}
        result = collapse_ranked_hits([self.hit("b", 0), self.hit("c", 1), self.hit("a", 2)], self.manifest(), rows, top_k=3, eligible_occurrence_ids=["a", "b", "c"])
        self.assertEqual(["b", "c"], [item["id"] for item in result["hits"]])
        self.assertEqual(1, result["underfill"])

    def test_foreign_hit_is_rejected_before_filtering(self):
        with self.assertRaises(RetrievalDedupError):
            collapse_ranked_hits([self.hit("x", 0) | {"metadata": {"partition_id": "other", "corpus_id": "c", "release_id": "r", "representation_id": "rep"}}], self.manifest(), self.rows(), top_k=1, eligible_occurrence_ids=[])

    def test_alias_chain_is_rejected(self):
        rows = self.rows()
        rows[0]["alias_target_id"] = "b"
        with self.assertRaises(RetrievalDedupError):
            resolve_alias("a", rows)

    def test_candidate_limits(self):
        self.assertEqual(6, candidate_limit(2, 100, {"profile": "safe"}))
        self.assertEqual(2, candidate_limit(2, 100, {"profile": "off"}))
        with self.assertRaises(RetrievalDedupError):
            candidate_limit(True, 3, {"profile": "safe"})

    def test_disabled_retrieval_preserves_ranked_occurrences(self):
        manifest = {**self.manifest(), "policy": {"retrieval": {"enabled": False}}}
        rows = self.rows()
        rows[1] = {**rows[1], "storage_status": "retained", "alias_target_id": None}
        result = collapse_ranked_hits([self.hit("b", 0), self.hit("a", 1)], manifest, rows, top_k=2, eligible_occurrence_ids=["a", "b"])
        self.assertEqual(["b", "a"], [item["id"] for item in result["hits"]])


if __name__ == "__main__":
    unittest.main()
