from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from chroma_db_import.workflow.catalog import AppCatalog
from chroma_db_import.workflow.folder_adapter import FolderAdapter
from chroma_db_import.workflow.jobs import JobService, TargetLock
from chroma_db_import.workflow.models import BridgeError, DatabaseRecord, FrozenPreview, SelectionPolicy
from chroma_db_import.workflow.service import WorkflowService
from chroma_db_import.ui_export import export_chroma
from chroma_db_import.ui_models import ImportPlan


FIXTURES = Path(__file__).parent / "fixtures" / "gui"


def _hold_target_lock(target: str, ready: Any, release: Any) -> None:
    lock = TargetLock(Path(target), "child-process")
    lock.acquire()
    ready.set()
    release.wait(10)
    lock.release()


class GuiJobTests(unittest.TestCase):
    def test_target_lock_is_stable_and_excludes_a_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "database"
            first = TargetLock(target, "first")
            second = TargetLock(target, "second")
            first.acquire()
            try:
                self.assertEqual(first.path, target.parent / ".database.gui.lock")
                with self.assertRaises(BridgeError) as raised:
                    second.acquire()
                self.assertEqual(raised.exception.code, "TARGET_BUSY")
            finally:
                first.release()

    def test_target_lock_excludes_a_separate_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "database"
            context = multiprocessing.get_context("spawn")
            ready = context.Event()
            release = context.Event()
            process = context.Process(target=_hold_target_lock, args=(str(target), ready, release))
            process.start()
            try:
                self.assertTrue(ready.wait(10), "child process did not acquire the target lock")
                with self.assertRaises(BridgeError) as raised:
                    TargetLock(target, "parent-process").acquire()
                self.assertEqual("TARGET_BUSY", raised.exception.code)
            finally:
                release.set()
                process.join(10)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            self.assertEqual(0, process.exitcode)

    def test_legacy_exporter_cooperates_with_the_target_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "database"
            plan = ImportPlan(
                podcast_name="Fixture", database_id="db", processed_data_dir=Path(temp) / "source",
                output_root=Path(temp), collection_name="fixture", embedding_model="fixture",
                embedding_device="cpu", final_export_dir=target,
            )
            lock = TargetLock(target, "modern-job")
            lock.acquire()
            try:
                with self.assertRaises(BridgeError) as raised:
                    export_chroma(plan, "create", lambda _progress: None)
                self.assertEqual("TARGET_BUSY", raised.exception.code)
            finally:
                lock.release()

    def test_restart_marks_running_work_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            catalog = AppCatalog(Path(temp) / "catalog.sqlite3")
            job = catalog.create_or_get_job(kind="import", database_id="db", preview_id="preview", payload={})
            catalog.update_job(job["id"], state="running", stage="embedding")
            recovered = catalog.mark_running_interrupted()
            self.assertEqual(recovered[0]["state"], "interrupted")
            self.assertEqual(recovered[0]["error"]["active_database_state"], "unknown")

    def test_cancelled_queued_scan_does_not_start_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            catalog = AppCatalog(Path(temp) / "catalog.sqlite3")
            job = catalog.create_or_get_job(kind="scan", database_id=None, preview_id=None, payload={}, can_cancel=True)
            catalog.cancel_queued_job(job["id"])
            service = JobService(catalog, FolderAdapter(), object())
            try:
                service._run_scan(job["id"], {})
            finally:
                service.shutdown()
            self.assertEqual("cancelled", catalog.get_job(job["id"])["state"])

    def test_synthetic_progress_job_persists_bounded_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            catalog = AppCatalog(Path(temp) / "catalog.sqlite3")
            service = JobService(catalog, FolderAdapter(), object())
            try:
                job = service.submit_synthetic_progress(1)
                service.shutdown(wait=True)
            finally:
                service.shutdown(wait=False)
            completed = catalog.get_job(job["id"])
            self.assertEqual("succeeded", completed["state"])
            self.assertEqual("unchanged", completed["result"]["active_database_state"])
            events = catalog.get_job_events(job["id"])
            self.assertTrue(any(event["stage"] == "preparing" for event in events))

    def test_import_revalidates_database_inside_writer_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "episode-one.processed_documents.json").write_bytes(
                (FIXTURES / "episode-one.processed_documents.json").read_bytes()
            )
            service = WorkflowService(root / "state")
            try:
                record = service.catalog.create_database(DatabaseRecord(
                    id="db-lock-review", display_name="Fixture Podcast", source_kind="folder",
                    source_ref={"path": str(source)}, target={"path": str(root / "export")},
                    selection_policy=SelectionPolicy().as_dict(),
                ).as_dict())
                preview = service.create_preview({"database_id": record["id"], "operation": "update"})
                original = service.get_database(record["id"])
                service.update_database_settings(record["id"], {
                    "selection_policy": {**SelectionPolicy().as_dict(), "asset_filter": "all"},
                })
                changed = service.get_database(record["id"])
                job = service.catalog.create_or_get_job(
                    kind="import", database_id=record["id"], preview_id=preview["preview_id"],
                    payload={"preview_id": preview["preview_id"]}, can_cancel=False,
                )
                with patch.object(service, "get_database", side_effect=[original, changed]):
                    service.jobs._run_import(job["id"], record["id"], FrozenPreview.from_mapping(preview))
                failed = service.catalog.get_job(job["id"])
                self.assertEqual("failed", failed["state"])
                self.assertEqual("PREVIEW_STALE", failed["error"]["code"])
                self.assertFalse(any(event["stage"] == "embedding" for event in service.catalog.get_job_events(job["id"])))
            finally:
                service.shutdown()

    def test_retry_failed_import_creates_a_fresh_preview_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "episode-one.processed_documents.json").write_bytes(
                (FIXTURES / "episode-one.processed_documents.json").read_bytes()
            )
            service = WorkflowService(root / "state")
            try:
                record = service.catalog.create_database(DatabaseRecord(
                    id="db-retry", display_name="Retry fixture", source_kind="folder",
                    source_ref={"path": str(source)}, target={"path": str(root / "export")},
                    selection_policy=SelectionPolicy().as_dict(),
                ).as_dict())
                preview = service.create_preview({"database_id": record["id"], "operation": "update"})
                failed = service.catalog.create_or_get_job(
                    kind="import", database_id=record["id"], preview_id=preview["preview_id"],
                    payload={"preview_id": preview["preview_id"]}, can_cancel=False,
                )
                service.catalog.update_job(failed["id"], state="failed", stage="complete", error={"code": "JOB_FAILED", "message": "fixture failure"})
                with patch.object(service.jobs, "submit_preview", return_value={"id": "fresh-review", "kind": "preview"}) as submit:
                    result = service.retry_job(failed["id"])
                self.assertEqual("fresh-review", result["id"])
                payload = submit.call_args.args[0]
                self.assertEqual("update", payload["operation"])
                self.assertEqual(record["id"], payload["database_id"])
                self.assertNotIn("preview_id", payload)
            finally:
                service.shutdown()


if __name__ == "__main__":
    unittest.main()
