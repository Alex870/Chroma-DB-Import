from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chroma_db_import.workflow.catalog import AppCatalog, CatalogError
from chroma_db_import.workflow.models import BridgeError, DatabaseRecord, SelectionPolicy, new_id


class GuiCatalogTests(unittest.TestCase):
    def record(self, target: Path, *, name: str = "Fixture") -> DatabaseRecord:
        return DatabaseRecord(
            id=new_id("db"), display_name=name, source_kind="folder", source_ref={"path": str(target.parent / "source")},
            target={"path": str(target), "collection_name": "fixture"}, selection_policy=SelectionPolicy().as_dict(),
        )

    def test_restart_preserves_rename_and_archive_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "export"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("fixture", encoding="utf-8")
            catalog = AppCatalog(root / "state" / "gui_catalog.sqlite3")
            created = catalog.create_database(self.record(target).as_dict())
            catalog.update_database(created["id"], {"display_name": "Renamed"})
            restarted = AppCatalog(root / "state" / "gui_catalog.sqlite3")
            self.assertEqual(restarted.get_database(created["id"])["display_name"], "Renamed")
            restarted.archive_database(created["id"])
            self.assertTrue(marker.is_file())
            self.assertEqual(restarted.list_databases(), [])
            self.assertEqual(len(restarted.list_databases(include_archived=True)), 1)

    def test_duplicate_normalized_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = AppCatalog(root / "catalog.sqlite3")
            target = root / "Export"
            catalog.create_database(self.record(target).as_dict())
            with self.assertRaises(BridgeError) as raised:
                catalog.create_database(self.record(root / "." / "export").as_dict())
            self.assertEqual(raised.exception.code, "TARGET_EXISTS")

    def test_newer_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "catalog.sqlite3"
            catalog = AppCatalog(path)
            with catalog._connection() as connection:
                connection.execute("UPDATE catalog_meta SET value='999' WHERE key='schema_version'")
                connection.commit()
            with self.assertRaises(CatalogError):
                AppCatalog(path)

    def test_duplicate_apply_for_preview_reuses_one_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            catalog = AppCatalog(Path(temp) / "catalog.sqlite3")
            first = catalog.create_or_get_job(kind="import", database_id="db", preview_id="preview", payload={"preview_id": "preview"})
            second = catalog.create_or_get_job(kind="import", database_id="db", preview_id="preview", payload={"preview_id": "preview"})
            self.assertEqual(first["id"], second["id"])

    def test_failed_import_can_be_requeued_for_a_safe_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            catalog = AppCatalog(Path(temp) / "catalog.sqlite3")
            first = catalog.create_or_get_job(kind="import", database_id="db", preview_id="preview", payload={"preview_id": "preview"})
            catalog.update_job(first["id"], state="failed", stage="complete", error={"code": "TARGET_EXISTS", "message": "fixture"})
            retried = catalog.create_or_get_job(kind="import", database_id="db", preview_id="preview", payload={"preview_id": "preview"}, retry_failed=True)
            self.assertEqual(first["id"], retried["id"])
            self.assertEqual("queued", retried["state"])
            self.assertEqual("Queued for retry", catalog.get_job_events(first["id"])[-1]["message"])

    def test_tracking_records_are_additive_and_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "catalog.sqlite3"
            catalog = AppCatalog(path)
            observed = catalog.save_source_observation({
                "connection_id": "connection-one", "partition_id": "partition-one", "upstream_release_id": "release-one",
                "source_root": "C:/source", "corpus_id": "corpus-one", "status": "discovered", "active": True,
                "snapshot": {"completed": 57},
            })
            link = catalog.save_database_link({
                "database_id": "db-one", "connection_id": "connection-one", "partition_id": "partition-one",
                "corpus_id": "corpus-one", "source_root": "C:/source", "target_key": "c:/exports/partition-one",
                "profile_fingerprint": "sha256:profile", "origin": "created", "state": "linked",
                "last_source_release_id": "release-one", "last_downstream_release_id": "downstream-one",
            })
            event = catalog.save_database_release_event({
                "database_id": "db-one", "connection_id": "connection-one", "partition_id": "partition-one",
                "upstream_release_id": "release-one", "downstream_release_id": "downstream-one",
                "operation": "create", "status": "succeeded", "detail": {"writes": 57},
            })
            restarted = AppCatalog(path)
            self.assertTrue(observed["active"])
            self.assertEqual("release-one", restarted.list_source_observations()[0]["upstream_release_id"])
            self.assertEqual("downstream-one", restarted.get_database_link("db-one")["last_downstream_release_id"])
            self.assertEqual(event["id"], restarted.list_database_release_history("db-one")[0]["id"])
            self.assertEqual(link["origin"], "created")


if __name__ == "__main__":
    unittest.main()
