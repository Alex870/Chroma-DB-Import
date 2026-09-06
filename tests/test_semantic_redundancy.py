import json
import tempfile
import unittest
from pathlib import Path

from chroma_db_import.redundancy_artifacts import validate_bundle, write_bundle
from chroma_db_import.redundancy_candidates import build_dense_candidate_index, build_lexical_index, dense_candidates, lexical_and_source_candidates, minhash_signature, token_shingles
from chroma_db_import.redundancy_judge import build_judge_request, validate_judgment
from chroma_db_import.redundancy_models import AnalysisUnit, Scope
from chroma_db_import.redundancy_policy import RedundancyPolicyError, resolve_redundancy_policy
from chroma_db_import.redundancy_retrieval import eligible_occurrences, hydrate_occurrences, query_representatives, representatives_for
from chroma_db_import.semantic_selection import select_evidence


class SemanticRedundancyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp")
        self.root = Path(self.tmp.name)
        self.units = [
            AnalysisUnit("a", ("a",), "The host may grow the market.", {"partition_id": "p", "corpus_id": "c", "speaker": "Host", "episode_uid": "p:e1"}, "leaf", "p:e1", "f1", ("s1",)),
            AnalysisUnit("b", ("b",), "The host may grow the market quickly.", {"partition_id": "p", "corpus_id": "c", "speaker": "Host", "episode_uid": "p:e2"}, "leaf", "p:e2", "f2", ("s2",)),
            AnalysisUnit("c", ("c",), "The guest says the market will fall.", {"partition_id": "p", "corpus_id": "c", "speaker": "Guest", "episode_uid": "p:e3"}, "leaf", "p:e3", "f3", ("s3",)),
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def test_policy_rejects_unknown_and_bool_numbers(self):
        self.assertEqual("redundancy-policy-v1", resolve_redundancy_policy()["version"])
        with self.assertRaises(RedundancyPolicyError):
            resolve_redundancy_policy({"unknown": True})
        with self.assertRaises(RedundancyPolicyError):
            resolve_redundancy_policy({"lexical_neighbors": True})

    def test_fixed_minhash_and_bounded_channels(self):
        self.assertEqual(minhash_signature(token_shingles("a b c d")), minhash_signature(token_shingles("a b c d")))
        index = build_lexical_index(self.units, self.root / "index.sqlite3")
        pairs = lexical_and_source_candidates(self.units[0], index)
        self.assertTrue(all(pair.left_id < pair.right_id for pair in pairs))
        dense = build_dense_candidate_index(self.units, {"a": [1.0, 0.0], "b": [0.99, 0.1], "c": [-1.0, 0.0]})
        self.assertEqual("a::b", dense_candidates(self.units[0], dense)[0].candidate_id)

    def test_dense_inventory_reports_unavailable_vectors(self):
        dense = build_dense_candidate_index(
            self.units,
            {"a": [0.0, 0.0], "b": [1.0, 0.0, 0.0], "outside": [1.0, 0.0]},
        )
        self.assertEqual(1, dense.skipped_reasons["zero_norm_vector"])
        self.assertEqual(1, dense.skipped_reasons["dimension_mismatch"])
        self.assertEqual(1, dense.skipped_reasons["vector_without_analysis_unit"])
        self.assertEqual(3, dense.skipped_reasons["missing_vector"])

    def test_judge_requires_literal_evidence_and_preserves_uncertainty(self):
        request = build_judge_request(self.units[0], [self.units[1]])
        self.assertEqual(0, request["temperature"])
        self.assertNotIn("tools", request["messages"][0])
        raw = {"candidate_id": "a", "relation": "equivalent", "matched_ids": ["b"], "evidence": [{"candidate_quote": "The host may grow the market.", "matched_id": "b", "matched_quote": "The host may grow the market quickly."}], "novel_quotes": [], "conflict_quotes": [], "attribution_changed": False, "time_changed": False, "qualification_changed": False, "reason": "same claims"}
        judgment = validate_judgment(raw, candidate_id="a", supplied_units={unit.document_id: unit for unit in self.units})
        self.assertEqual("equivalent", judgment.relation)
        invalid = dict(raw, evidence=[{"candidate_quote": "fabricated", "matched_id": "b", "matched_quote": "fabricated"}])
        self.assertEqual("uncertain", validate_judgment(invalid, candidate_id="a", supplied_units={unit.document_id: unit for unit in self.units}).relation)
        self.assertEqual(
            "uncertain",
            validate_judgment(raw, candidate_id="a", supplied_units={unit.document_id: unit for unit in self.units}, allowed_matched_ids=["c"]).relation,
        )

    def test_judge_marks_single_neighbor_that_cannot_fit_as_context_limit(self):
        large_neighbor = AnalysisUnit("large", ("large",), "x" * 30000, {"episode_uid": "p:e-large"}, "leaf", "p:e-large", "f-large")
        request = build_judge_request(self.units[0], [large_neighbor])
        self.assertTrue(request["_context_limit"])
        self.assertEqual(0, len(json.loads(request["messages"][1]["content"])["comparisons"]))

    def test_shared_input_bundle_filters_before_representative_query(self):
        rows = [
            {"document_id": "a", "text": self.units[0].text, "metadata": self.units[0].metadata, "episode_uid": "p:e1", "node_type": "leaf"},
            {"document_id": "b", "text": self.units[1].text, "metadata": self.units[1].metadata, "episode_uid": "p:e2", "node_type": "leaf"},
            {"document_id": "c", "text": self.units[2].text, "metadata": self.units[2].metadata, "episode_uid": "p:e3", "node_type": "leaf"},
        ]
        bundle = self.root / "bundle"
        write_bundle(bundle, scope=Scope("p", "c", "r", "rep"), occurrences=rows, storage_mode="shared_input", vectors={"a": [1.0, 0.0], "c": [-1.0, 0.0]}, embedding_inputs={"a": "same", "b": "same", "c": "different"})
        self.assertTrue(validate_bundle(bundle)["valid"])
        self.assertEqual(["a", "b"], eligible_occurrences(bundle, {"speakers": ["Host"]}))
        self.assertEqual(["a"], representatives_for(bundle, ["b"]))
        hits = query_representatives(bundle, [1.0, 0.0], ["a"], 3)
        hydrated = hydrate_occurrences(bundle, hits, ["b"], "preserve_occurrences")
        self.assertEqual(["b"], [row["id"] for row in hydrated["hits"]])
        semantic_hydrated = hydrate_occurrences(bundle, hits, ["b"], "semantic_mmr")
        self.assertEqual(["b"], [row["id"] for row in semantic_hydrated["hits"]])

    def test_mmr_falls_back_as_a_whole_request_when_a_vector_is_missing(self):
        hits = [{"id": "a", "rank": 0}, {"id": "b", "rank": 1}]
        result = select_evidence(hits, {"a": [1.0, 0.0]}, top_k=2, mode="semantic_mmr")
        self.assertEqual(("a", "b"), result.selected_ids)
        self.assertEqual("vectors_unavailable", result.fallback_reason)

    def test_enabled_judge_can_be_explicitly_skipped(self):
        rows = [{"document_id": "a", "text": "A", "metadata": {"episode_uid": "e1"}, "episode_uid": "e1", "node_type": "leaf"}]
        bundle = self.root / "disabled-judge-bundle"
        write_bundle(bundle, scope=Scope("p", "c", "r", "rep"), occurrences=rows, policy={"judge_enabled": True}, coverage={"judge_status": "disabled"})
        self.assertTrue(validate_bundle(bundle)["valid"])


if __name__ == "__main__":
    unittest.main()
