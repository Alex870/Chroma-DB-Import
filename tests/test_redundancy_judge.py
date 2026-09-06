import unittest

from chroma_db_import.redundancy_judge import build_judge_request, select_judge_units, validate_judgment
from chroma_db_import.redundancy_models import AnalysisUnit, CandidatePair


class RedundancyJudgeTests(unittest.TestCase):
    def setUp(self):
        self.units = [
            AnalysisUnit("a", ("a",), "The host may grow the market.", {"episode_uid": "e1", "speaker": "Host"}, "leaf", "e1", "cache-a"),
            AnalysisUnit("b", ("b",), "The host may grow the market quickly.", {"episode_uid": "e2", "speaker": "Host"}, "leaf", "e2", "cache-b"),
        ]
        self.pair = CandidatePair("a", "b", channels=("lexical",), scores={"jaccard": 1.0}, channel_ranks={"lexical": 1})

    def test_shortlist_and_request_are_deterministic_and_tool_free(self):
        selected = select_judge_units([self.pair], {unit.document_id: unit for unit in self.units}, {"judge_record_fraction": 1.0})
        self.assertEqual(["a::b"], selected[0]["pair_ids"])
        request = build_judge_request(selected[0]["candidate"], selected[0]["neighbors"])
        self.assertEqual(0, request["temperature"])
        self.assertFalse(any(message.get("role") == "tool" for message in request["messages"]))

    def test_unknown_ids_and_fabricated_quotes_become_uncertain(self):
        raw = {"candidate_id": "a", "relation": "equivalent", "matched_ids": ["b"], "evidence": [{"candidate_quote": "made up", "matched_id": "b", "matched_quote": "made up"}], "novel_quotes": [], "conflict_quotes": [], "attribution_changed": False, "time_changed": False, "qualification_changed": False, "reason": "same"}
        judgment = validate_judgment(raw, candidate_id="a", supplied_units={unit.document_id: unit for unit in self.units}, pair=self.pair, allowed_matched_ids=["b"])
        self.assertEqual("uncertain", judgment.relation)
        foreign = dict(raw, matched_ids=["foreign"], evidence=[])
        self.assertEqual("uncertain", validate_judgment(foreign, candidate_id="a", supplied_units={unit.document_id: unit for unit in self.units}, allowed_matched_ids=["b"]).relation)


if __name__ == "__main__":
    unittest.main()
