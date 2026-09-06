import json
import tempfile
import unittest
from pathlib import Path

from chroma_db_import.redundancy_evaluation import (
    RedundancyEvaluationError,
    bootstrap_query_metric,
    candidate_recall_metrics,
    evaluate_equivalence,
    evaluate_frozen_snapshot,
    evaluate_query_results,
    export_labels,
    export_queries,
    select_label_pairs,
    select_query_sample,
    validate_labels,
    validate_queries,
)
from chroma_db_import.redundancy_models import AnalysisUnit, CandidatePair


class RedundancyEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.units = {}
        for index in range(8):
            episode = f"episode-{index // 2}"
            unit = AnalysisUnit(
                f"doc-{index}",
                (f"doc-{index}",),
                f"A distinct transcript {index}.",
                {"episode_uid": episode, "partition_id": "p", "corpus_id": "c"},
                "leaf",
                episode,
                f"cache-{index}",
            )
            self.units[unit.document_id] = unit

    def _pairs(self):
        return [CandidatePair(f"doc-{index}", f"doc-{index + 1}", channels=("lexical",), scores={"jaccard": 1.0, "text_containment_left": 1.0, "text_containment_right": 1.0}) for index in range(0, 8, 2)]

    def _labels(self):
        rows = []
        for index, pair in enumerate(self._pairs()):
            rows.append({
                "left_id": pair.left_id,
                "right_id": pair.right_id,
                "left_text_hash": f"sha256:left-{index}",
                "right_text_hash": f"sha256:right-{index}",
                "left_text": f"left-{index}",
                "right_text": f"right-{index}",
                "relation": "equivalent" if index == 0 else "novel",
                "material_difference": False,
                "distinct_occurrence": True,
                "reviewer": "reviewer",
                "split": "development" if index < 2 else "held_out",
                "source_group_ids": [f"episode-{index // 2}"],
            })
        return {"contract_version": "redundancy-labels-v1", "base_scope": {"partition_id": "p", "corpus_id": "c", "base_release_id": "r", "representation_id": "rep"}, "base_fingerprint": "sha256:base", "pairs": rows}

    def test_sample_is_stable_and_keeps_groups_out_of_both_splits(self):
        first = select_label_pairs(self._pairs(), self.units, limit=4)
        second = select_label_pairs(list(reversed(self._pairs())), self.units, limit=4)
        self.assertEqual(first, second)
        self.assertEqual({"development", "held_out"}, {row["split"] for row in first})
        development = {group for row in first if row["split"] == "development" for group in row["source_group_ids"]}
        held_out = {group for row in first if row["split"] == "held_out" for group in row["source_group_ids"]}
        self.assertFalse(development & held_out)

    def test_candidate_edges_do_not_connect_unrelated_source_groups(self):
        pairs = [
            CandidatePair("doc-0", "doc-2", channels=("lexical",)),
            CandidatePair("doc-2", "doc-4", channels=("lexical",)),
            CandidatePair("doc-0", "doc-4", channels=("lexical",)),
        ]
        selected = select_label_pairs(pairs, self.units, limit=10)
        # Three independent episode groups are split independently. Only the
        # pair whose endpoints happen to share a split survives; candidate
        # similarity must not merge all three episodes into one component.
        self.assertEqual(1, len(selected))

    def test_validation_rejects_leakage_and_bad_query_contract(self):
        labels = self._labels()
        self.assertEqual(4, validate_labels(labels)["pair_count"])
        leaked = json.loads(json.dumps(labels))
        leaked["pairs"][2]["source_group_ids"] = leaked["pairs"][0]["source_group_ids"]
        with self.assertRaises(RedundancyEvaluationError):
            validate_labels(leaked)
        queries = {"contract_version": "redundancy-queries-v1", "base_scope": labels["base_scope"], "base_fingerprint": labels["base_fingerprint"], "queries": [{"query_id": "q1", "query": "What happened?", "mode": "factual", "filters": {}, "relevance": {"doc-0": 3}, "reviewer": "reviewer", "split": "development", "source_group_ids": ["episode-0"]}]}
        self.assertTrue(validate_queries(queries)["valid"])

    def test_bound_label_hashes_and_excerpts_are_checked(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp") as directory:
            output = Path(directory) / "labels.json"
            export_labels(self._pairs(), self.units, output, base_scope={}, base_fingerprint="sha256:base")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(4, validate_labels(payload, units=self.units)["pair_count"])
            payload["pairs"][0]["left_text"] = "tampered"
            with self.assertRaises(RedundancyEvaluationError):
                validate_labels(payload, units=self.units)

    def test_frozen_evaluation_writes_unknown_query_measurements(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp") as directory:
            result = evaluate_frozen_snapshot(self._labels(), {"A": {"doc-0::doc-1": "equivalent"}, "B": {}, "C": {}, "D": {}}, output_dir=directory)
            self.assertEqual("insufficient_evidence", result["gates"]["overall"])
            self.assertTrue((Path(directory) / "evaluation.json").is_file())
            self.assertEqual("insufficient_evidence", result["queries"]["status"])

    def test_query_export_adds_filter_object(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp") as directory:
            output = Path(directory) / "queries.json"
            export_queries([{"query_id": "q1", "query": "What happened?", "mode": "factual", "relevance": {"doc-0": 3}}], output, base_scope={}, base_fingerprint="sha256:base")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual({}, payload["queries"][0]["filters"])

    def test_query_sample_is_stable_and_balances_provenance_groups(self):
        queries = [
            {"query_id": f"q-{index}", "query": f"Question {index}", "mode": "factual", "filters": {}, "relevance": {f"doc-{index * 2}": 3}}
            for index in range(4)
        ]
        first = select_query_sample(queries, self.units, limit=4)
        second = select_query_sample(list(reversed(queries)), self.units, limit=4)
        self.assertEqual(first, second)
        self.assertEqual({"development", "held_out"}, {row["split"] for row in first})
        development = {group for row in first if row["split"] == "development" for group in row["source_group_ids"]}
        held_out = {group for row in first if row["split"] == "held_out" for group in row["source_group_ids"]}
        self.assertFalse(development & held_out)

    def test_equivalence_metrics_do_not_count_unknown_pairs(self):
        metrics = evaluate_equivalence({"doc-0::doc-1": "equivalent", "unknown::pair": "equivalent"}, self._labels()["pairs"])
        self.assertEqual(1, metrics["true_positive"])
        self.assertEqual(2, metrics["predicted_equivalent"])
        self.assertEqual(0.5, metrics["equivalence_precision"])

    def test_material_change_error_counts_any_predicted_equivalent(self):
        labels = self._labels()
        labels["pairs"][0]["material_difference"] = True
        metrics = evaluate_equivalence({"doc-0::doc-1": "equivalent"}, labels["pairs"])
        self.assertEqual(1, metrics["material_change_errors"])
        self.assertEqual(1.0, metrics["material_change_error_rate"])

    def test_candidate_recall_uses_an_independent_positive_denominator(self):
        audit = [{"left_id": "doc-0", "right_id": "doc-1", "relation": "equivalent"}, {"left_id": "doc-2", "right_id": "doc-3", "relation": "partial_overlap"}, {"left_id": "doc-4", "right_id": "doc-5", "relation": "novel"}]
        result = candidate_recall_metrics(audit, [self._pairs()[0]])
        self.assertEqual(2, result["positive_count"])
        self.assertEqual(0.5, result["candidate_recall"])

    def test_grouped_bootstrap_is_seeded_and_uses_all_group_members(self):
        first = bootstrap_query_metric([1.0, 0.0, 1.0], groups=["a", "a", "b"], seed=0, replicates=20)
        second = bootstrap_query_metric([1.0, 0.0, 1.0], groups=["a", "a", "b"], seed=0, replicates=20)
        self.assertEqual(first, second)
        self.assertTrue(first["grouped"])
        self.assertEqual(2, first["group_count"])

    def test_query_results_report_grouped_recall_and_ndcg(self):
        queries = {"contract_version": "redundancy-queries-v1", "base_scope": {}, "base_fingerprint": "sha256:base", "queries": [{"query_id": "q1", "query": "What?", "mode": "factual", "filters": {}, "relevance": {"doc-0": 3}, "reviewer": "reviewer", "split": "held_out", "source_group_ids": ["episode-0"]}]}
        result = evaluate_query_results(queries, {"A": {"q1": ["doc-0", "doc-0"]}}, replicates=20)
        self.assertEqual("complete", result["status"])
        self.assertEqual(1, result["arms"]["A"]["query_count"])
        self.assertEqual(1.0, result["arms"]["A"]["recall_at_k"]["estimate"])

    def test_query_results_report_missing_queries_without_inflating_coverage(self):
        queries = {"contract_version": "redundancy-queries-v1", "base_scope": {}, "base_fingerprint": "sha256:base", "queries": [
            {"query_id": "q1", "query": "What?", "mode": "factual", "filters": {}, "relevance": {"doc-0": 3}, "reviewer": "reviewer", "split": "held_out", "source_group_ids": ["episode-0"]},
            {"query_id": "q2", "query": "Why?", "mode": "factual", "filters": {}, "relevance": {"doc-2": 3}, "reviewer": "reviewer", "split": "held_out", "source_group_ids": ["episode-1"]},
        ]}
        result = evaluate_query_results(queries, {"A": {"q1": ["doc-0"]}}, replicates=20)
        self.assertEqual(1, result["arms"]["A"]["missing_query_count"])
        self.assertEqual(0.5, result["arms"]["A"]["coverage"])


if __name__ == "__main__":
    unittest.main()
