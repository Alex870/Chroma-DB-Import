import json
import unittest
from pathlib import Path

from chroma_db_import.semantic_selection import candidate_depth, select_evidence


class SemanticSelectionTests(unittest.TestCase):
    def test_portable_conformance_fixture(self):
        fixture = Path(__file__).resolve().parent / "fixtures" / "contracts" / "semantic-selection-v1" / "conformance.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        self.assertEqual("semantic-selection-v1", payload["contract_version"])
        for case in payload["cases"]:
            result = select_evidence(case["hits"], case["vectors"], top_k=case["top_k"], mode=case["mode"], mmr_lambda=case["mmr_lambda"])
            self.assertEqual(tuple(case["selected_ids"]), result.selected_ids, case["name"])
            self.assertEqual(case["fallback_reason"], result.fallback_reason, case["name"])

    def test_candidate_depth_is_bounded(self):
        self.assertEqual(15, candidate_depth(3, 100))
        self.assertEqual(100, candidate_depth(30, 100))

    def test_mmr_uses_rank_relevance_and_keeps_ids(self):
        hits = [{"id": "a", "rank": 0}, {"id": "b", "rank": 1}, {"id": "c", "rank": 2}]
        result = select_evidence(hits, {"a": [1, 0], "b": [1, 0], "c": [0, 1]}, top_k=2, mode="semantic_mmr", mmr_lambda=0.5)
        self.assertEqual("a", result.selected_ids[0])
        self.assertEqual(2, len(result.selected_ids))

    def test_missing_vector_falls_back_for_the_whole_request(self):
        result = select_evidence([{"id": "a"}, {"id": "b"}], {"a": [1, 0]}, top_k=2, mode="semantic_mmr")
        self.assertEqual("vectors_unavailable", result.fallback_reason)
        self.assertEqual(("a", "b"), result.selected_ids)


if __name__ == "__main__":
    unittest.main()
