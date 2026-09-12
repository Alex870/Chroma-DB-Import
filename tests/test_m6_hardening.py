import json, tempfile, unittest
from importlib.util import find_spec
from pathlib import Path
from chroma_db_import.hardening import benchmark_collection_writes, scale_plan
from chroma_db_import.m6_preflight import build_preflight


class M6HardeningTests(unittest.TestCase):
    def export(self, root):
        export = root / "export"
        export.mkdir()
        (export / "import_manifest.json").write_text(
            json.dumps(
                {
                    "reconciliation": {
                        "added": ["a"],
                        "changed": [],
                        "metadata_only": [],
                        "removed": [],
                        "unchanged": [],
                    },
                    "embedding_cache": {"hits": 0, "misses": 1},
                    "operation": {"elapsed_seconds": 1},
                }
            )
        )
        (export / "chroma.sqlite3").write_bytes(b"db")
        (export / "podcast.json").write_text("{}")
        return export

    def test_scale_plan_blocks_low_disk_and_bounds_batches(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as raw:
            root = Path(raw)
            value = scale_plan(
                self.export(root),
                root,
                available_bytes=1,
                memory_limit_bytes=1_000_000_000,
            )
            self.assertFalse(value["runnable"])
            self.assertEqual(8, value["recommendation"]["embedding_batch_size"])
            self.assertTrue(value["recommendation"]["release_isolated"])
            pre = build_preflight(
                "import",
                root,
                required_modules=("m6_missing_dependency",),
                minimum_free_bytes=1,
            )
            self.assertNotIn(str(root.resolve()), json.dumps(pre))
            self.assertGreater(pre["profile"]["total_memory_bytes"], 0)
            missing = next(
                item
                for item in pre["capabilities"]
                if item["capability"] == "m6_missing_dependency"
            )
            self.assertEqual(
                "conda run -n chroma-db-import python -m pip install -r chroma_db_import_requirements.txt",
                missing["remediation_command"],
            )

    @unittest.skipUnless(find_spec("chromadb"), "chromadb is not installed")
    def test_collection_write_benchmark_is_real_and_cleans_workspace(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as raw:
            root = Path(raw)
            corpus = root / "corpus.json"
            corpus.write_text(
                json.dumps(
                    {
                        "documents": [
                            {"document_id": "a", "text": "A"},
                            {"document_id": "b", "text": "B"},
                        ]
                    }
                )
            )
            workspace = root / "bench"
            workspace.mkdir()
            value = benchmark_collection_writes(corpus, workspace, [1, 2])
            self.assertEqual(2, len(value["results"]))
            self.assertTrue(value["synthetic_vectors"])
            self.assertEqual([], list(workspace.iterdir()))


if __name__ == "__main__":
    unittest.main()
