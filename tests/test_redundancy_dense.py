import unittest
import sys
import types
from unittest.mock import patch

from chroma_db_import.redundancy_candidates import RedundancyCandidateError, build_dense_candidate_index, dense_candidates
from chroma_db_import.redundancy_models import AnalysisUnit


class RedundancyDenseTests(unittest.TestCase):
    def setUp(self):
        self.units = [
            AnalysisUnit("a", ("a",), "alpha", {"partition_id": "p", "corpus_id": "c", "representation_id": "r"}, "leaf", "e1", "c1"),
            AnalysisUnit("b", ("b",), "beta", {"partition_id": "p", "corpus_id": "c", "representation_id": "r"}, "leaf", "e2", "c2"),
        ]

    def test_supplied_vectors_are_scored_without_embedding_calls(self):
        index = build_dense_candidate_index(self.units, {"a": [1.0, 0.0], "b": [0.99, 0.1]})
        self.assertEqual("a::b", dense_candidates(self.units[0], index)[0].candidate_id)
        self.assertIsNone(index.configuration["embedding_function"])

    def test_invalid_vectors_are_reported_and_requested_chroma_fails_clearly(self):
        index = build_dense_candidate_index(self.units, {"a": [0.0, 0.0], "b": [1.0, 0.0, 0.0]})
        self.assertEqual(1, index.skipped_reasons["zero_norm_vector"])
        self.assertEqual(1, index.skipped_reasons["dimension_mismatch"])
        with patch.dict(sys.modules, {"chromadb": types.SimpleNamespace()}):
            with self.assertRaises(RedundancyCandidateError):
                build_dense_candidate_index(self.units, {"a": [1.0, 0.0], "b": [1.0, 0.0]}, require_chroma=True)


if __name__ == "__main__":
    unittest.main()
