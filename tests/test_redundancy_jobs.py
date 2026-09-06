import tempfile
import unittest
from pathlib import Path

from chroma_db_import.managed import ContextIdentity, ManagedCatalog
from chroma_db_import.managed import ManagedContextError
from chroma_db_import.redundancy_jobs import create_redundancy_job, update_redundancy_job


class RedundancyJobTests(unittest.TestCase):
    def test_job_freezes_scope_hashes_channels_and_partial_reasons(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp") as directory:
            with ManagedCatalog(Path(directory) / "catalog.sqlite3") as catalog:
                catalog.upsert_context(ContextIdentity("partition-a", "corpus-a", "A", "podcast", "podcast"))
                job = create_redundancy_job(
                    catalog,
                    "partition-a",
                    "release-1",
                    channels=["lexical", "structural"],
                    model_id="local-model",
                    judge_requested=True,
                    base_scope={"partition_id": "partition-a", "corpus_id": "corpus-a", "base_release_id": "release-1", "representation_id": "rep-1"},
                    base_hashes={"release.json": "sha256:release"},
                )
                self.assertEqual(["lexical", "structural"], job["spec"]["channels"])
                self.assertTrue(job["spec"]["judge_requested"])
                self.assertEqual("local-model", job["spec"]["model_id"])
                self.assertEqual("rep-1", job["spec"]["base_scope"]["representation_id"])
                updated = update_redundancy_job(catalog, job["job_id"], status="completed_partial", detail={"partial_reasons": ["channel_not_requested:dense"]})
                self.assertEqual(["channel_not_requested:dense"], updated["detail"]["partial_reasons"])

    def test_newer_job_schema_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp") as directory:
            with ManagedCatalog(Path(directory) / "catalog.sqlite3") as catalog:
                catalog.upsert_context(ContextIdentity("partition-a", "corpus-a", "A", "podcast", "podcast"))
                with self.assertRaises(ManagedContextError):
                    create_redundancy_job(catalog, "partition-a", "release-1", schema_version="redundancy-job-v2")


if __name__ == "__main__":
    unittest.main()
