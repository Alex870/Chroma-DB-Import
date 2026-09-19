import unittest

from chroma_db_import.contract import validate_document_items, temporal_coverage_stats


def metadata(node_id="leaf", *, date_value="2026-03-15", sort_key="20260315", scope="single", speaker="Host", speakers=None, node_type="leaf_chunk", child_ids=None):
    return {
        "node_id": node_id, "node_type": node_type, "source": "episode.json", "episode_id": "episode-1",
        "episode_title": "Episode", "source_type": "json_transcript", "speaker_scope": scope,
        "speaker": speaker, "speakers": speakers or ([speaker] if speaker else []),
        "episode_date": date_value, "episode_sort_key": sort_key, "source_segment_id": "span-1",
        "child_ids": child_ids or [],
    }


class TemporalContractTests(unittest.TestCase):
    def test_temporal_coverage_matches_current_producer_schema(self):
        docs = [
            {"page_content": "Host said this.", "metadata": metadata("leaf")},
            {"page_content": "A cluster summary.", "metadata": metadata("cluster", node_type="cluster_summary", child_ids=["leaf"])},
            {"page_content": "The episode thesis.", "metadata": metadata("thesis", node_type="episode_thesis", child_ids=["leaf"])},
            {"page_content": "A position claim.", "metadata": metadata("position", node_type="position_card", child_ids=["leaf"]) | {"claim": "A claim"}},
        ]
        coverage = temporal_coverage_stats(docs)
        self.assertEqual(coverage["by_speaker"], coverage["speaker_coverage"])
        self.assertEqual(coverage["temporally_eligible_record_count"], 4)
        self.assertIn("primary_evidence_count", coverage["by_speaker"]["Host"])
        self.assertIn("derived_evidence_count", coverage["by_period"]["2026-03"])

    def test_certified_and_strict_rejection(self):
        report = validate_document_items([
            {"page_content": "Host said this.", "metadata": metadata()},
            {"page_content": "The episode thesis.", "metadata": metadata("thesis", node_type="episode_thesis", child_ids=["leaf"])},
        ], require_temporal=True)
        self.assertTrue(report.valid, report.errors)
        bad = validate_document_items([
            {"page_content": "Host said this.", "metadata": metadata(sort_key="20260201")},
            {"page_content": "The episode thesis.", "metadata": metadata("thesis", node_type="episode_thesis", child_ids=["leaf"])},
        ], require_temporal=True)
        self.assertFalse(bad.valid)
        self.assertEqual("partial", bad.temporal_capability)

    def test_filename_date_and_mixed_orientation(self):
        inferred = temporal_coverage_stats([metadata() | {"episode_date_source": "filename_inferred"}])
        self.assertEqual("partial", inferred["temporal_capability"])
        mixed = temporal_coverage_stats([metadata(scope="mixed", speaker="multiple", speakers=["Host", "Guest"])])
        self.assertEqual("certified", mixed["temporal_capability"])

    def test_older_import_contract_is_legacy(self):
        report = validate_document_items([{"page_content": "Host said this.", "metadata": metadata()}], legacy_contract=True)
        self.assertEqual("legacy", report.temporal_capability)


if __name__ == "__main__":
    unittest.main()
