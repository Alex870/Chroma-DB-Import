import json
import tempfile
import unittest
from pathlib import Path

from chroma_db_import.dedup_artifacts import validate_dedup_artifacts, write_dedup_artifacts
from chroma_db_import.deduplication import (
    DedupInput,
    DeduplicationError,
    build_exact_plan,
    detect_near_edges,
    load_managed_dedup_inputs,
    normalize_span_ids,
    normalize_text_v1,
    resolve_dedup_policy,
)
from chroma_db_import.representation import RepresentationSpec, embedding_text
from chroma_db_import.retrieval_dedup import candidate_limit, collapse_ranked_hits, resolve_alias


class DeduplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(__file__).resolve().parents[1] / ".test_tmp" / "dedup"
        cls.scratch.mkdir(parents=True, exist_ok=True)

    def item(self, item_id, *, partition="podcast", corpus="podcast", episode="episode", text="same text", span="span-1", metadata=None):
        producer = {
            "stable_document_id": item_id,
            "node_id": "node-" + item_id,
            "node_type": "leaf_chunk",
            "source": "episode.json",
            "source_type": "json_transcript",
            "episode_id": episode,
            "episode_uid": f"{partition}:{episode}",
            "speaker_scope": "single",
            "speaker": "Host",
            "source_span_id": span,
        }
        producer.update(metadata or {})
        return DedupInput(
            item_id,
            str(producer["node_id"]),
            text,
            producer,
            partition,
            corpus,
            f"{partition}:{episode}",
            "cache-revision-1",
            embedding_text(text, producer, "minimal"),
            "leaf_chunk",
            (span,),
        )

    def test_policy_is_complete_and_off_disables_runtime_features(self):
        off = resolve_dedup_policy({"profile": "off"})
        self.assertFalse(off["near_enabled"])
        self.assertFalse(off["retrieval"]["enabled"])
        self.assertEqual("dedup-v1", off["policy_version"])
        with self.assertRaises(DeduplicationError):
            resolve_dedup_policy({"profile": "safe", "unknown": True})
        with self.assertRaises(DeduplicationError):
            resolve_dedup_policy({"profile": "safe", "near_jaccard_threshold": True})

    def test_normalization_and_spans_are_conservative(self):
        self.assertEqual("Café, NO! 12", normalize_text_v1("  Café,\u00a0NO!\n12  "))
        self.assertEqual(("a", "b"), normalize_span_ids({"source_span_ids": ["b", "a", "a"]})[0])
        self.assertEqual("span_conflict", normalize_span_ids({"source_span_id": "a", "source_span_ids": ["b"]})[1])
        self.assertEqual("invalid_span", normalize_span_ids({"source_span_id": 123})[1])

    def test_safe_exact_plan_is_release_wide_and_canonical(self):
        plan = build_exact_plan([self.item("z"), self.item("a")], {"profile": "safe"})
        self.assertEqual(["a"], plan.stored_ids)
        self.assertEqual(1, plan.suppressed_exact_count)
        self.assertEqual("a", plan.exact_groups[0].canonical_id)
        self.assertEqual("a", plan.decisions["z"].alias_target_id)

    def test_audit_retains_all_and_span_or_metadata_conflicts_are_not_suppressed(self):
        duplicate = self.item("d")
        changed = self.item("b", text="different")
        conflicting = self.item("c", metadata={"speaker": "Guest"})
        plan = build_exact_plan([self.item("a"), duplicate, changed, conflicting], {"profile": "audit"})
        self.assertEqual(["a", "b", "c", "d"], plan.stored_ids)
        self.assertEqual(0, plan.suppressed_exact_count)
        self.assertEqual(1, plan.would_suppress_exact)
        self.assertEqual("span_conflict", plan.decisions["b"].reason)
        self.assertEqual("different_embedding_input", plan.decisions["c"].reason)

    def test_near_detection_is_bounded_and_nontransitive(self):
        base = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty twentyone twentytwo twentythree twentyfour"
        similar = base.replace("twentythree", "twentythree")
        different = base.replace("three", "threeX").replace("four", "fourX")
        edges, report = detect_near_edges([self.item("a", text=base), self.item("b", text=similar), self.item("c", text=different)], {"profile": "safe"})
        self.assertEqual("complete", report["status"])
        self.assertEqual(3, report["candidate_pairs"])
        self.assertTrue(any(edge.left_id == "a" and edge.right_id == "b" for edge in edges))

    def test_portable_ledger_and_retrieval_collapse(self):
        first, second = self.item("a"), self.item("b")
        plan = build_exact_plan([first, second], {"profile": "safe"})
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            export = Path(directory)
            (export / "release.json").write_text(json.dumps({
                "release_contract_version": "chroma-export-release-v1",
                "release_id": "downstream-1",
                "upstream_release_id": "upstream-1",
                "partition_id": "podcast",
                "corpus_id": "podcast",
                "representation_id": "representation-1",
            }), encoding="utf-8")
            result = write_dedup_artifacts(export, plan, {
                "release_id": "downstream-1", "upstream_release_id": "upstream-1",
                "partition_id": "podcast", "corpus_id": "podcast", "representation_id": "representation-1",
            })
            release = json.loads((export / "release.json").read_text(encoding="utf-8"))
            validated_safe = validate_dedup_artifacts(export, release)
            self.assertEqual(["a"], validated_safe["stored_ids"])
            audit_plan = build_exact_plan([first, second], {"profile": "audit"})
            write_dedup_artifacts(export, audit_plan, {
                "release_id": "downstream-1", "upstream_release_id": "upstream-1",
                "partition_id": "podcast", "corpus_id": "podcast", "representation_id": "representation-1",
            })
            release = json.loads((export / "release.json").read_text(encoding="utf-8"))
            validated = validate_dedup_artifacts(export, release)
            hits = [
                {"id": "b", "rank": 0, "metadata": {"partition_id": "podcast", "corpus_id": "podcast", "release_id": "downstream-1", "representation_id": "representation-1"}},
                {"id": "a", "rank": 1, "metadata": {"partition_id": "podcast", "corpus_id": "podcast", "release_id": "downstream-1", "representation_id": "representation-1"}},
            ]
            collapsed = collapse_ranked_hits(hits, validated["manifest"], validated["rows"], top_k=2)
            self.assertEqual(["b"], [hit["id"] for hit in collapsed["hits"]])
            self.assertEqual("a", resolve_alias("b", validated_safe["rows"])["document_id"])
            self.assertEqual(6, candidate_limit(2, 20, {"profile": "safe"}))

    def test_loader_rejects_global_duplicate_ids_and_keeps_exclusion_counts(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            cache = Path(directory) / "episode.processed_documents.json"
            cache.write_text(json.dumps({
                "episode_id": "episode",
                "episode_uid": "podcast:episode",
                "documents": [
                    {"page_content": "", "metadata": {"stable_document_id": "empty", "node_id": "empty-node", "node_type": "leaf_chunk", "episode_id": "episode", "episode_uid": "podcast:episode"}},
                    {"page_content": "text", "metadata": {"stable_document_id": "kept", "node_id": "kept-node", "node_type": "leaf_chunk", "episode_id": "episode", "episode_uid": "podcast:episode", "source_span_id": "span"}},
                ],
            }), encoding="utf-8")
            inventory = load_managed_dedup_inputs([cache], {"partition_id": "podcast", "corpus_id": "podcast"}, {"episode_uids": ["podcast:episode"]})
            self.assertEqual(1, len(inventory))
            self.assertEqual(1, inventory.excluded_count)


if __name__ == "__main__":
    unittest.main()
