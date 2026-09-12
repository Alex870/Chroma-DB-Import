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


if __name__ == "__main__":
    unittest.main()
