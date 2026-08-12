import tempfile
import unittest
from pathlib import Path

from chroma_db_import.release_jobs import ReleaseJobStore, backup_store, plan_job, restore_store, run_job
from chroma_db_import.releases import ReleaseStore, export_fingerprint, plan_release
from tests.test_releases import DELTA, ReleaseTests


class MilestoneOneReleaseJobTests(unittest.TestCase):
    def test_job_resumes_and_backup_restores(self):
        scratch = Path(__file__).parent.parent / ".test_tmp"; scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary); export = ReleaseTests().write_export(root)
            plan = plan_release(DELTA, parent_release_id=None, active_embedding=None,
                requested_embedding={"model": "a"}, selection_fingerprint="m1", removal_mode="reconcile_approved",
                export_bundle_fingerprint=export_fingerprint(export), vector_representation_id="repr-1")
            jobs = ReleaseJobStore(root / "jobs")
            job = plan_job(plan, export, root / "store", jobs)
            completed = run_job(jobs, job["job_id"], approved_plan_id=plan["plan_id"])
            self.assertEqual("completed", completed["status"])
            self.assertEqual(completed, run_job(jobs, job["job_id"], approved_plan_id=plan["plan_id"]))
            backup_store(root / "store", root / "backup")
            restore_store(root / "backup", root / "restored")
            self.assertEqual(plan["release_id"], ReleaseStore(root / "restored").active())

    def test_cancelled_job_never_stages(self):
        scratch = Path(__file__).parent.parent / ".test_tmp"; scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary); export = ReleaseTests().write_export(root)
            plan = plan_release(DELTA, parent_release_id=None, active_embedding=None,
                requested_embedding={"model": "a"}, selection_fingerprint="cancel", removal_mode="reconcile_approved",
                export_bundle_fingerprint=export_fingerprint(export), vector_representation_id="repr-1")
            jobs = ReleaseJobStore(root / "jobs"); job = plan_job(plan, export, root / "store", jobs)
            jobs.cancel(job["job_id"])
            self.assertEqual("cancelled", run_job(jobs, job["job_id"], approved_plan_id=plan["plan_id"])["status"])
            self.assertFalse((root / "store" / "staging").exists())


if __name__ == "__main__": unittest.main()
