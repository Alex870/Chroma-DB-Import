import tempfile
import unittest
from pathlib import Path

from chroma_db_import.redundancy_candidates import build_lexical_index, lexical_and_source_candidates
from chroma_db_import.redundancy_models import AnalysisUnit


class RedundancyCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_large_repeated_text_bucket_uses_a_star_and_reports_band_caps(self):
        units = [
            AnalysisUnit(f"doc-{index:04d}", (f"doc-{index:04d}",), "same repeated transcript text", {"partition_id": "p", "corpus_id": "c"}, "leaf", f"episode-{index}", f"cache-{index}")
            for index in range(2001)
        ]
        index = build_lexical_index(units, self.root / "index.sqlite3")
        pairs = lexical_and_source_candidates(units[0], index)
        self.assertEqual(2001, index.coverage.unit_count)
        self.assertGreater(index.coverage.posting_truncations.get("minhash_band", 0), 0)
        self.assertGreater(index.coverage.posting_size_stats["minhash_band"]["max_posting"], 512)
        self.assertIn("doc-0000::doc-2000", {pair.candidate_id for pair in pairs})

    def test_structural_posting_cap_is_per_lookup_and_keeps_bounded_candidates(self):
        units = [
            AnalysisUnit(f"doc-{index:04d}", (f"doc-{index:04d}",), f"short {index}", {"partition_id": "p", "corpus_id": "c"}, "leaf", "episode-1", "cache-1", ("shared-span",))
            for index in range(600)
        ]
        policy = {"lexical_enabled": False, "structural_enabled": True, "dense_enabled": False}
        index = build_lexical_index(units, self.root / "structural.sqlite3", policy)
        pairs = lexical_and_source_candidates(units[0], index, policy)
        self.assertEqual(20, len(pairs))
        self.assertGreater(index.coverage.posting_truncations.get("source_span", 0), 0)

    def test_candidate_index_does_not_mix_representation_spaces(self):
        units = [
            AnalysisUnit("rep-a", ("rep-a",), "same text for two spaces", {"partition_id": "p", "corpus_id": "c", "representation_id": "r-a"}, "leaf", "episode-a", "cache-a"),
            AnalysisUnit("rep-b", ("rep-b",), "same text for two spaces", {"partition_id": "p", "corpus_id": "c", "representation_id": "r-b"}, "leaf", "episode-b", "cache-b"),
        ]
        index = build_lexical_index(units, self.root / "representation.sqlite3")
        self.assertEqual([], lexical_and_source_candidates(units[0], index))

    def test_scope_is_applied_before_source_posting_cap(self):
        foreign = [
            AnalysisUnit(f"a-foreign-{index:04d}", (f"a-foreign-{index:04d}",), "foreign", {"partition_id": "other", "corpus_id": "c"}, "leaf", "episode-1", "cache-1", ("shared-span",))
            for index in range(600)
        ]
        target = AnalysisUnit("target", ("target",), "target", {"partition_id": "p", "corpus_id": "c"}, "leaf", "episode-1", "cache-1", ("shared-span",))
        match = AnalysisUnit("z-match", ("z-match",), "match", {"partition_id": "p", "corpus_id": "c"}, "leaf", "episode-1", "cache-1", ("shared-span",))
        index = build_lexical_index([*foreign, target, match], self.root / "scope-before-cap.sqlite3", {"lexical_enabled": False, "structural_enabled": True, "dense_enabled": False})
        pairs = lexical_and_source_candidates(target, index, {"lexical_enabled": False, "structural_enabled": True, "dense_enabled": False})
        self.assertIn("target::z-match", {pair.candidate_id for pair in pairs})


if __name__ == "__main__":
    unittest.main()
