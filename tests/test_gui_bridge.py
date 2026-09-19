from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chroma_db_import.desktop.bridge import ApplicationBridge
from chroma_db_import.workflow.service import WorkflowService


class GuiBridgeTests(unittest.TestCase):
    def test_malformed_ids_are_safe_error_envelopes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = WorkflowService(Path(temp))
            bridge = ApplicationBridge(service)
            response = bridge.get_database({})
            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.handshake({})["data"]["api_version"], "gui-api-v1")
            service.shutdown()

    def test_production_bridge_does_not_fall_back_to_fixture_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = WorkflowService(Path(temp))
            bridge = ApplicationBridge(service)
            self.assertEqual(bridge.list_databases()["data"], [])
            self.assertEqual(bridge.echo({"round_trip": "ok"}), {"ok": True, "data": {"round_trip": "ok"}})
            self.assertEqual(bridge.synthetic_progress({"seconds": 0})["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.synthetic_progress([])["error"]["code"], "VALIDATION_FAILED")
            service.shutdown()

    def test_migration_candidates_are_read_only_and_field_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "state" / "ui_state.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"podcast_name":"Legacy Show","processed_data_dir":"processed","output_root":"exports","embedding_model":"legacy-model"}', encoding="utf-8")
            service = WorkflowService(root / "state" / "gui")
            proposal = service.migration_candidates()
            self.assertTrue(proposal["originals_unchanged"])
            self.assertEqual(proposal["candidates"][0]["kind"], "legacy_ui_state")
            self.assertEqual(proposal["candidates"][0]["changes"]["display_name"], "Legacy Show")
            self.assertEqual(legacy.read_text(encoding="utf-8").count("Legacy Show"), 1)
            service.shutdown()

    def test_malformed_paging_and_acknowledgment_payloads_are_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = WorkflowService(Path(temp))
            bridge = ApplicationBridge(service)
            self.assertEqual(bridge.list_jobs({"limit": "not-an-int"})["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.apply_preview({"preview_id": "preview_x", "acknowledgments": "REMOVE_OUTDATED_RECORDS"})["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.get_job(None)["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.get_job({"job_id": []})["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.scan_source({"source_kind": [], "path": "fixture"})["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.create_preview({"source_kind": [], "operation": "create"})["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.start_source_action({"action": "inspect", "source_root": [], "partition_id": "part"})["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.create_maintenance_preview({"database_id": "db", "delete_ids": [1]})["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.inspect_existing({})["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.list_jobs([])["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.scan_source([])["error"]["code"], "VALIDATION_FAILED")
            self.assertEqual(bridge.create_preview([])["error"]["code"], "VALIDATION_FAILED")
            service.shutdown()

    def test_register_existing_keeps_catalog_id_separate_from_downstream_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            service = WorkflowService(root / "state")
            record = service.register_existing({
                "display_name": "Inspected export",
                "source_kind": "folder",
                "source_ref": {"path": str(root / "processed")},
                "target": {"path": str(root / "export")},
                "downstream_identity": {
                    "database_id": "legacy-downstream-id",
                    "collection_name": "rag_documents",
                },
            })
            self.assertTrue(record["id"].startswith("db_"))
            self.assertNotEqual(record["id"], "legacy-downstream-id")
            self.assertEqual(record["downstream_identity"]["database_id"], "legacy-downstream-id")
            service.shutdown()


if __name__ == "__main__":
    unittest.main()
