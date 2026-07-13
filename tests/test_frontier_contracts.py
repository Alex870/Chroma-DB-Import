import unittest

from chroma_db_import.contracts import ContractCompatibilityError, require_compatible_contract
from chroma_db_import.reconciliation import plan_reconciliation, require_delete_confirmation
from chroma_db_import.representation import RepresentationSpec, contextual_header, embedding_fingerprint


class FrontierContractTests(unittest.TestCase):
    def test_contract_accepts_new_minor_and_rejects_new_major(self):
        require_compatible_contract("2.9", "2.0", artifact_path="manifest.json", component="Importer")
        with self.assertRaises(ContractCompatibilityError):
            require_compatible_contract("3.0", "2.9", artifact_path="manifest.json", component="Importer")

    def test_context_headers_are_deterministic_and_profiled(self):
        metadata = {"podcast_name": "Show", "episode_title": "Episode", "episode_date": "2026-01-01", "speaker": "Host", "node_type": "leaf_chunk", "topic": "AI"}
        minimal = contextual_header(metadata, "minimal")
        full = contextual_header(metadata, "full")
        self.assertNotIn("Topic: AI", minimal)
        self.assertIn("Topic: AI", full)
        self.assertEqual(minimal, contextual_header(metadata, "minimal"))

    def test_embedding_fingerprint_ignores_nonembedded_metadata(self):
        spec = RepresentationSpec(contextualization="minimal")
        first = embedding_fingerprint("text", {"speaker": "Host", "private_note": "one"}, spec)
        second = embedding_fingerprint("text", {"speaker": "Host", "private_note": "two"}, spec)
        self.assertEqual(first, second)
        changed = embedding_fingerprint("text", {"speaker": "Guest", "private_note": "two"}, spec)
        self.assertNotEqual(first, changed)

    def test_reconciliation_categories_and_delete_confirmation(self):
        old = {
            "same": {"embedding_fingerprint": "a", "metadata_fingerprint": "a"},
            "meta": {"embedding_fingerprint": "b", "metadata_fingerprint": "b"},
            "changed": {"embedding_fingerprint": "c", "metadata_fingerprint": "c"},
            "removed": {"embedding_fingerprint": "d", "metadata_fingerprint": "d"},
        }
        current = {
            "same": {"embedding_fingerprint": "a", "metadata_fingerprint": "a"},
            "meta": {"embedding_fingerprint": "b", "metadata_fingerprint": "new"},
            "changed": {"embedding_fingerprint": "new", "metadata_fingerprint": "new"},
            "added": {"embedding_fingerprint": "e", "metadata_fingerprint": "e"},
        }
        plan = plan_reconciliation(current, old)
        self.assertEqual(plan.added, ["added"])
        self.assertEqual(plan.metadata_only, ["meta"])
        self.assertEqual(plan.changed, ["changed"])
        self.assertEqual(plan.removed, ["removed"])
        with self.assertRaises(PermissionError):
            require_delete_confirmation(plan, False)
        require_delete_confirmation(plan, True)

    def test_dimension_truncation_requires_compatible_provider(self):
        with self.assertRaises(ValueError):
            RepresentationSpec(dimension=1024, output_dimension=512).validate()


if __name__ == "__main__":
    unittest.main()
