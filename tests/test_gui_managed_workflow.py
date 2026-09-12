from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from chroma_db_import.desktop.bridge import ApplicationBridge
from chroma_db_import.managed import ManagedCatalog, discover
from chroma_db_import.workflow.managed_adapter import ManagedAdapter
from chroma_db_import.workflow.models import BridgeError, DatabaseRecord, SelectionPolicy
from chroma_db_import.workflow.service import WorkflowService
from tests.test_managed_contexts import write_release


class GuiManagedWorkflowTests(unittest.TestCase):
    @staticmethod
    def _wait_for_job(service: WorkflowService, job_id: str) -> dict[str, object]:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = service.get_job(job_id)
            if job["state"] in {"succeeded", "succeeded_with_warnings", "failed", "interrupted", "cancelled"}:
                return job
            time.sleep(0.01)
        raise AssertionError(f"Timed out waiting for managed GUI job {job_id}")

    def test_service_inspects_managed_registration_and_normalizes_partition_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "managed-source"
            source_root.mkdir()
            target = root / "exports" / "partitions" / "podcast-one"
            service = WorkflowService(root / "state")
            try:
                status = {
                    "partition_id": "podcast-one",
                    "corpus_id": "corpus-one",
                    "display_name": "Podcast One",
                    "warnings": [],
                    "catalog_path": str(source_root / "catalog.sqlite3"),
                }
                with patch.object(service.managed_adapter, "inspect", return_value=status):
                    proposal = service.inspect_existing({
                        "source_kind": "managed",
                        "target_path": str(target),
                        "source_root": str(source_root),
                        "partition_id": "podcast-one",
                    })
                self.assertEqual("managed", proposal["source_kind"])
                self.assertEqual(str(root / "exports"), proposal["target"]["managed_output_root"])
                self.assertEqual(str(target), proposal["target"]["path"])
                self.assertEqual("podcast-one", proposal["source_ref"]["partition_id"])
                self.assertEqual("unresolved", proposal["identity_status"])
            finally:
                service.shutdown()

    def test_real_managed_dry_run_returns_pinned_release_and_record_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "processed_data"
            cache_dir.mkdir()
            (cache_dir / "episode.processed_documents.json").write_text(json.dumps({
                "source_fingerprint": "cache-one", "partition_id": "podcast-one", "corpus_id": "podcast-one",
                "partition_display_name": "Podcast One", "context_type": "podcast", "workflow_profile": "podcast",
                "episode_uid": "podcast-one:episode-01", "episode_id": "episode-01",
                "documents": [
                    {"page_content": "managed text", "metadata": {
                        "node_id": "record-one", "stable_document_id": "record-one", "node_type": "leaf_chunk",
                        "episode_id": "episode-01", "episode_uid": "podcast-one:episode-01", "episode_title": "Episode 01", "source_span_id": "span-one",
                        "source": "episode.json", "source_type": "json_transcript", "speaker_scope": "single",
                        "speaker": "Host", "partition_id": "podcast-one", "corpus_id": "podcast-one",
                    }},
                    {"page_content": "episode thesis", "metadata": {
                        "node_id": "thesis-one", "stable_document_id": "thesis-one", "node_type": "episode_thesis",
                        "episode_id": "episode-01", "episode_uid": "podcast-one:episode-01", "episode_title": "Episode 01",
                        "source": "episode.json", "source_type": "json_transcript", "speaker_scope": "episode",
                        "partition_id": "podcast-one", "corpus_id": "podcast-one",
                    }},
                ],
            }), encoding="utf-8")
            write_release(root, "podcast-one", "podcast-one", "release-01", "cache-one")
            with ManagedCatalog(root / "catalog.sqlite3") as catalog:
                discover(catalog, [root])
            draft = {
                "source_kind": "managed",
                "source_ref": {"source_root": str(root), "partition_id": "podcast-one", "catalog_path": str(root / "catalog.sqlite3")},
                "target": {"path": str(root / "exports")}, "display_name": "Podcast One",
                "selection_policy": SelectionPolicy().as_dict(),
            }
            preview = ManagedAdapter().create_preview(None, draft, operation="create")
            self.assertEqual("release-01", preview.source_snapshot["release_id"])
            self.assertEqual(2, preview.effects.records_total)
            self.assertEqual(["record-one", "thesis-one"], preview.effects.insert_ids)

    def test_managed_creation_runs_through_real_bridge_and_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "processed_data"
            cache_dir.mkdir()
            (cache_dir / "episode.processed_documents.json").write_text(
                '{"source_fingerprint":"cache-one","partition_id":"podcast-one","corpus_id":"podcast-one","episode_uid":"podcast-one:episode-01","documents":[]}',
                encoding="utf-8",
            )
            write_release(root, "podcast-one", "podcast-one", "release-01", "cache-one")
            with ManagedCatalog(root / "catalog.sqlite3") as catalog:
                discover(catalog, [root])
                profile_fingerprint = str((catalog.profile("podcast-one") or {}).get("profile_fingerprint") or "")

            service = WorkflowService(root / "state")
            bridge = ApplicationBridge(service)
            try:
                draft = {
                    "source_kind": "managed",
                    "source_ref": {
                        "source_root": str(root),
                        "partition_id": "podcast-one",
                        "catalog_path": str(root / "catalog.sqlite3"),
                    },
                    "target": {"path": str(root / "exports")},
                    "display_name": "Podcast One",
                    "selection_policy": SelectionPolicy().as_dict(),
                }
                saved = bridge.save_draft({"payload": draft})
                self.assertTrue(saved["ok"])
                draft_id = str(saved["data"]["id"])
                dry_run = {
                    "status": "validated",
                    "upstream_release_id": "release-01",
                    "downstream_release_id": "chroma-release-01",
                    "cache_files": [],
                    "import_profile_fingerprint": profile_fingerprint,
                    "record_ids": ["record-one"],
                    "dedup": {"stored": 1},
                }
                completed = {
                    "status": "completed",
                    "export": str(root / "exports" / "partitions" / "podcast-one" / "releases" / "release-01"),
                    "corpus_id": "podcast-one",
                    "downstream_release_id": "chroma-release-01",
                    "dedup": {"stored": 1},
                }
                with patch(
                    "chroma_db_import.workflow.managed_adapter.run_managed_import",
                    side_effect=[dry_run, completed],
                ):
                    preview_response = bridge.create_preview({"draft_id": draft_id, "operation": "create"})
                    self.assertTrue(preview_response["ok"])
                    preview_job = self._wait_for_job(service, str(preview_response["data"]["id"]))
                    self.assertEqual("succeeded", preview_job["state"])
                    preview_id = str((preview_job["result"] or {})["preview_id"])
                    preview_response = bridge.get_preview({"preview_id": preview_id})
                    self.assertTrue(preview_response["ok"])
                    self.assertEqual("managed", preview_response["data"]["source_snapshot"]["kind"])
                    self.assertEqual("release-01", preview_response["data"]["source_snapshot"]["release_id"])

                    import_response = bridge.apply_preview({
                        "preview_id": preview_id,
                        "acknowledgments": ["MANAGED_RELEASE_REVIEW"],
                    })
                    self.assertTrue(import_response["ok"])
                    import_job = self._wait_for_job(service, str(import_response["data"]["id"]))

                self.assertEqual("succeeded", import_job["state"])
                self.assertEqual("podcast-one", (import_job["result"] or {})["downstream_identity"]["database_id"])
                database = service.get_database(str(import_job["database_id"]))
                self.assertNotEqual(database["id"], database["downstream_identity"]["database_id"])
                self.assertEqual("podcast-one", database["downstream_identity"]["database_id"])
            finally:
                service.shutdown()

    def test_preview_pins_release_and_execution_uses_same_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "processed_data"
            cache_dir.mkdir()
            (cache_dir / "episode.processed_documents.json").write_text(
                '{"source_fingerprint":"cache-one","partition_id":"podcast-one","corpus_id":"podcast-one","episode_uid":"podcast-one:episode-01","documents":[]}',
                encoding="utf-8",
            )
            write_release(root, "podcast-one", "podcast-one", "release-01", "cache-one")
            with ManagedCatalog(root / "catalog.sqlite3") as catalog:
                discover(catalog, [root])
                profile_fingerprint = str((catalog.profile("podcast-one") or {}).get("profile_fingerprint") or "")
            draft = {
                "source_kind": "managed",
                "source_ref": {"source_root": str(root), "partition_id": "podcast-one", "catalog_path": str(root / "catalog.sqlite3")},
                "target": {"path": str(root / "exports")},
                "display_name": "Podcast One",
                "selection_policy": SelectionPolicy().as_dict(),
            }
            dry_run = {
                "status": "validated", "upstream_release_id": "release-01", "downstream_release_id": "chroma-release-01",
                "cache_files": [], "import_profile_fingerprint": profile_fingerprint, "record_ids": ["a", "b"],
                "dedup": {"stored": 2}, "plan_fingerprint": "sha256:plan",
            }
            adapter = ManagedAdapter()
            with patch("chroma_db_import.workflow.managed_adapter.run_managed_import", return_value=dry_run) as dry:
                preview = adapter.create_preview(None, draft, operation="create")
            self.assertEqual("release-01", preview.source_snapshot["release_id"])
            self.assertEqual(["a", "b"], preview.effects.insert_ids)
            self.assertIn("MANAGED_RELEASE_REVIEW", preview.required_acknowledgments)
            self.assertEqual("release-01", dry.call_args.kwargs["upstream_release_id"])

            record = DatabaseRecord(
                id="db-managed", display_name="Podcast One", source_kind="managed",
                source_ref=draft["source_ref"], target=preview.target_identity,
                selection_policy=SelectionPolicy().as_dict(), downstream_identity=None,
            )
            completed = {"status": "completed", "export": str(root / "exports" / "active"), "corpus_id": "podcast-one", "downstream_release_id": "chroma-release-01", "dedup": {"stored": 2}}
            with patch("chroma_db_import.workflow.managed_adapter.run_managed_import", return_value=completed) as run:
                result = adapter.execute(record, preview, lambda _progress: None)
            self.assertEqual("new_version_active", result["active_database_state"])
            self.assertEqual("podcast-one", result["downstream_identity"]["database_id"])
            self.assertNotEqual(record.id, result["downstream_identity"]["database_id"])
            self.assertEqual("release-01", run.call_args.kwargs["upstream_release_id"])

    def test_managed_execution_rejects_profile_change_after_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "processed_data"
            cache_dir.mkdir()
            (cache_dir / "episode.processed_documents.json").write_text(
                '{"source_fingerprint":"cache-one","partition_id":"podcast-one","corpus_id":"podcast-one","episode_uid":"podcast-one:episode-01","documents":[]}',
                encoding="utf-8",
            )
            write_release(root, "podcast-one", "podcast-one", "release-01", "cache-one")
            with ManagedCatalog(root / "catalog.sqlite3") as catalog:
                discover(catalog, [root])
                profile_fingerprint = str((catalog.profile("podcast-one") or {}).get("profile_fingerprint") or "")
            draft = {
                "source_kind": "managed",
                "source_ref": {"source_root": str(root), "partition_id": "podcast-one", "catalog_path": str(root / "catalog.sqlite3")},
                "target": {"path": str(root / "exports")}, "display_name": "Podcast One",
                "selection_policy": SelectionPolicy().as_dict(),
            }
            dry_run = {
                "status": "validated", "upstream_release_id": "release-01", "downstream_release_id": "chroma-release-01",
                "cache_files": [], "import_profile_fingerprint": profile_fingerprint, "record_ids": [],
            }
            adapter = ManagedAdapter()
            with patch("chroma_db_import.workflow.managed_adapter.run_managed_import", return_value=dry_run):
                preview = adapter.create_preview(None, draft, operation="create")
            record = DatabaseRecord(
                id="db-managed-profile", display_name="Podcast One", source_kind="managed",
                source_ref=draft["source_ref"], target=preview.target_identity,
                selection_policy=SelectionPolicy().as_dict(), downstream_identity=None,
            )
            with patch.object(ManagedCatalog, "profile", return_value={"profile": {}, "profile_fingerprint": "sha256:changed"}), \
                patch("chroma_db_import.workflow.managed_adapter.run_managed_import") as run:
                with self.assertRaises(BridgeError) as raised:
                    adapter.execute(record, preview, lambda _progress: None)
            self.assertEqual("PREVIEW_STALE", raised.exception.code)
            self.assertIn("profile changed", raised.exception.message)
            run.assert_not_called()

    def test_managed_update_blocks_active_records_missing_from_prospective_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "processed_data"
            cache_dir.mkdir()
            (cache_dir / "episode.processed_documents.json").write_text(
                '{"source_fingerprint":"cache-one","partition_id":"podcast-one","corpus_id":"podcast-one","episode_uid":"podcast-one:episode-01","documents":[]}',
                encoding="utf-8",
            )
            write_release(root, "podcast-one", "podcast-one", "release-01", "cache-one")
            with ManagedCatalog(root / "catalog.sqlite3") as catalog:
                discover(catalog, [root])
            source_ref = {"source_root": str(root), "partition_id": "podcast-one", "catalog_path": str(root / "catalog.sqlite3")}
            target = {"path": str(root / "exports"), "managed_output_root": str(root / "exports")}
            record = DatabaseRecord(
                id="managed-db", display_name="Podcast One", source_kind="managed", source_ref=source_ref,
                target=target, selection_policy=SelectionPolicy().as_dict(), downstream_identity=None,
            )
            dry_run = {
                "status": "validated", "upstream_release_id": "release-01", "downstream_release_id": "chroma-new",
                "cache_files": [], "import_profile_fingerprint": "sha256:profile", "record_ids": ["new"],
            }
            adapter = ManagedAdapter()
            with patch("chroma_db_import.workflow.managed_adapter.run_managed_import", return_value=dry_run), \
                patch.object(adapter, "_active_export", return_value=("chroma-old", root / "active")), \
                patch("chroma_db_import.workflow.managed_adapter.inspect_existing_records", return_value={"old": {"id": "old"}}):
                preview = adapter.create_preview(record, {**record.as_dict()}, operation="update")
            self.assertEqual(["old"], preview.effects.retained_missing_ids)
            self.assertEqual("MANAGED_RETENTION_UNSUPPORTED", preview.validation_findings[0]["code"])


if __name__ == "__main__":
    unittest.main()
