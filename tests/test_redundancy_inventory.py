import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

from chroma_db_import.dedup_artifacts import write_dedup_artifacts
from chroma_db_import.deduplication import DedupInput, build_exact_plan
from chroma_db_import.representation import embedding_text
from chroma_db_import.redundancy_inventory import RedundancyInventoryError, load_inventory


class RedundancyInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _export(self) -> Path:
        export = self.root / "export"
        export.mkdir()
        release = {"release_contract_version": "chroma-export-release-v1", "release_id": "release-1", "upstream_release_id": "upstream-1", "partition_id": "partition-1", "corpus_id": "corpus-1", "representation_id": "representation-1"}
        (export / "release.json").write_text(json.dumps(release), encoding="utf-8")
        rows = []
        for item_id in ("occ-a", "occ-b"):
            metadata = {"stable_document_id": item_id, "node_id": item_id, "node_type": "leaf", "speaker": "Host", "source_span_id": "span"}
            rows.append(DedupInput(item_id, item_id, "Repeated text", metadata, "partition-1", "corpus-1", "partition-1:episode-1", "cache-1", embedding_text("Repeated text", metadata), "leaf", ("span",)))
        write_dedup_artifacts(export, build_exact_plan(rows, {"profile": "safe"}), release)
        return export

    def test_valid_v2_inventory_preserves_group_members_and_scope(self):
        inventory = load_inventory(self._export(), inspect_vectors=False)
        self.assertEqual("release-1", inventory.scope.base_release_id)
        self.assertEqual(1, len(inventory.units))
        self.assertEqual(("occ-a", "occ-b"), inventory.units[0].occurrence_ids)
        self.assertEqual({}, inventory.vectors)
        self.assertFalse(inventory.requires_v2_evidence)

    def test_vector_inspection_requires_actual_chroma_storage(self):
        with self.assertRaisesRegex(RedundancyInventoryError, "requires_chroma_evidence"):
            load_inventory(self._export(), inspect_vectors=True)

    def test_vector_inspection_uses_a_private_snapshot(self):
        export = self._export()
        (export / "chroma.sqlite3").write_bytes(b"fake-chroma")
        opened_paths = []

        class Collection:
            def get(self, *, ids=None, include=None):
                if ids is None:
                    return {"ids": ["occ-a"]}
                return {"ids": ["occ-a"], "embeddings": [[1.0, 0.0]]}

        class Client:
            def __init__(self, path):
                opened_paths.append(Path(path))

            def list_collections(self):
                return ["collection"]

            def get_collection(self, name):
                return Collection()

        previous = sys.modules.get("chromadb")
        sys.modules["chromadb"] = types.SimpleNamespace(PersistentClient=Client)
        try:
            inventory = load_inventory(export, inspect_vectors=True)
        finally:
            if previous is None:
                sys.modules.pop("chromadb", None)
            else:
                sys.modules["chromadb"] = previous
        self.assertEqual(["occ-a", "occ-b"], sorted(inventory.vectors))
        self.assertEqual(inventory.vectors["occ-a"], inventory.vectors["occ-b"])
        self.assertTrue(opened_paths)
        self.assertNotEqual(export.resolve(), opened_paths[0])

    def test_v1_or_missing_ledger_is_not_synthesized(self):
        legacy = {"release_contract_version": "chroma-export-release-v1", "release_id": "r", "partition_id": "p", "corpus_id": "c", "representation_id": "rep"}
        with self.assertRaisesRegex(RedundancyInventoryError, "requires_v2_evidence"):
            load_inventory({"release": legacy, "occurrences": [{"document_id": "doc", "text": "legacy"}]}, inspect_vectors=False)


if __name__ == "__main__":
    unittest.main()
