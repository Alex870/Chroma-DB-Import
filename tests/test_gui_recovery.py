from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chroma_db_import.workflow.catalog import AppCatalog
from chroma_db_import.workflow.folder_adapter import FolderAdapter
from chroma_db_import.workflow.jobs import JobService
from chroma_db_import.workflow.recovery import inspect_activation, recover_interrupted


class GuiRecoveryTests(unittest.TestCase):
    def test_journal_evidence_never_assumes_an_active_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = AppCatalog(root / "catalog.sqlite3")
            target = root / "database"
            stage = root / ".database.staging"
            backup = root / ".database.previous"
            catalog.save_activation_journal("op", str(target), str(stage), str(backup), "accepted")
            evidence = recover_interrupted(catalog)
            self.assertFalse(evidence[0]["evidence"]["target_present"])
            self.assertEqual(evidence[0]["evidence"]["active_database_state"], "unknown")

    def test_restart_attaches_journal_state_to_interrupted_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = AppCatalog(root / "catalog.sqlite3")
            job = catalog.create_or_get_job(kind="import", database_id="db", preview_id="preview", payload={})
            catalog.update_job(job["id"], state="running", stage="activating")
            target = root / "database"
            target.mkdir()
            stage = root / ".database.staging"
            backup = root / ".database.previous"
            catalog.save_activation_journal(job["id"], str(target), str(stage), str(backup), "accepted")
            service = JobService(catalog, FolderAdapter(), object())
            try:
                recovered = service.recover()
            finally:
                service.shutdown()
            self.assertEqual(recovered[0]["state"], "interrupted")
            self.assertEqual(recovered[0]["error"]["active_database_state"], "new_version_active")
            self.assertTrue(recovered[0]["error"]["recovery_evidence"]["target_present"])

    def test_activation_evidence_distinguishes_transition_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "database"
            stage = root / ".database.staging"
            backup = root / ".database.previous"
            stage.mkdir()
            backup.mkdir()
            evidence = inspect_activation(AppCatalog(root / "catalog.sqlite3"), target, stage, backup)
            self.assertEqual("target_moved_to_backup", evidence["transition"])
            self.assertEqual("unchanged", evidence["active_database_state"])
            target.mkdir()
            evidence = inspect_activation(AppCatalog(root / "catalog.sqlite3"), target, stage, backup)
            self.assertEqual("ambiguous_multiple_copies", evidence["transition"])
            self.assertEqual("unknown", evidence["active_database_state"])


if __name__ == "__main__":
    unittest.main()
