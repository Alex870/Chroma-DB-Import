from __future__ import annotations

import tempfile
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch

from chroma_db_import.managed import ManagedCatalog, discover
from chroma_db_import.workflow.models import DatabaseRecord, SelectionPolicy
from chroma_db_import.workflow.service import WorkflowService
from tests.test_managed_contexts import write_release


class GuiReconciliationTests(unittest.TestCase):
    @staticmethod
    def _status(release_id: str) -> dict[str, object]:
        return {
            "partition_id": "partition-one", "corpus_id": "partition-one", "display_name": "TFM Show",
            "ready_to_publish": True, "active_release_id": release_id, "completed": 57,
            "declared_episodes": 57, "pending": 0, "failed": 0, "interrupted": 0,
            "quarantined": 0, "status": "active", "warnings": [],
        }

    def _source(self, root: Path, release_id: str = "release-one") -> None:
        (root / "processed_data").mkdir(parents=True)
        release = write_release(root, "partition-one", "partition-one", release_id, "cache-one")
        payload = __import__("json").loads(release.read_text(encoding="utf-8"))
        payload["partition_display_name"] = "TFM Show"
        release.write_text(__import__("json").dumps(payload), encoding="utf-8")

    def test_ready_partition_is_actionable_and_new_release_becomes_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "pipeline"
            source.mkdir()
            self._source(source)
            service = WorkflowService(root / "state")
            try:
                connection = service.catalog.save_source_connection(str(source), str(root / "state" / "source.sqlite3"))
                with patch("chroma_db_import.workflow.reconciliation._source_status", return_value=self._status("release-one")):
                    first = service.reconcile_sources()
                context = first["contexts"][0]
                self.assertEqual("TFM Show", context["display_name"])
                self.assertEqual("ready_to_create", context["tracking"]["state"])
                self.assertEqual("create_database", context["suggested_next_action"]["action"])
                self.assertEqual(57, context["source_status"]["completed"])
                creation = context["creation_defaults"]
                self.assertEqual("TFM Show", creation["display_name"])
                self.assertEqual("release-one", creation["source_ref"]["upstream_release_id"])
                self.assertEqual(str((source / "exports" / "partitions" / "partition-one").resolve()), creation["target"]["path"])
                self.assertEqual(str((source / "exports").resolve()), creation["target"]["managed_output_root"])
                self.assertEqual("managed_fallback", creation["provenance"]["output"])
                self.assertEqual("auto", creation["execution_options"]["embedding_device"])

                (root / "exports").mkdir()
                database = DatabaseRecord(
                    id="db-one", display_name="TFM Show", source_kind="managed",
                    source_ref={"connection_id": connection["id"], "source_root": str(source), "partition_id": "partition-one", "corpus_id": "partition-one"},
                    target={"path": str(root / "exports"), "managed_output_root": str(root / "exports")},
                    selection_policy=SelectionPolicy().as_dict(),
                    downstream_identity={"upstream_release_id": "release-one", "downstream_release_id": "downstream-one", "profile": "qwen3-embedding-4b-shadow"},
                )
                service.catalog.create_database(database.as_dict())
                with patch("chroma_db_import.workflow.reconciliation._source_status", return_value=self._status("release-one")):
                    current = service.reconcile_sources()["contexts"][0]
                self.assertEqual("current", current["tracking"]["state"])
                self.assertEqual(["db-one"], current["matching_database_ids"])
                self.assertEqual("release-one", current["tracking"]["last_applied_release_id"])
                self.assertFalse(current["tracking"]["change_counts"]["requires_review"])

                write_release(source, "partition-one", "partition-one", "release-two", "cache-one")
                with patch("chroma_db_import.workflow.reconciliation._source_status", return_value=self._status("release-two")):
                    updated = service.reconcile_sources()["contexts"][0]
                self.assertEqual("update_available", updated["tracking"]["state"])
                self.assertEqual("release-two", updated["tracking"]["latest_release"]["upstream_release_id"])
                self.assertTrue(updated["tracking"]["change_counts"]["requires_review"])
                self.assertGreaterEqual(len(updated["tracking"]["history"]), 2)
                with patch("chroma_db_import.workflow.reconciliation._source_status", return_value=self._status("release-two")):
                    service.reconcile_sources()
                observations = service.catalog.list_source_observations(connection_id=connection["id"], partition_id="partition-one")
                self.assertEqual(2, len(observations))
            finally:
                service.shutdown()

    def test_contextual_output_defaults_follow_partition_application_and_fallback_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            from chroma_db_import.workflow.reconciliation import _default_output_root

            service = WorkflowService(root / "state")
            try:
                partition_root = root / "partition-exports"
                application_root = root / "application-exports"
                context = {"context_settings": {"settings": {"output_root": str(partition_root)}}}
                resolved, source = _default_output_root(service.catalog, context, {}, str(root / "pipeline"))
                self.assertEqual(str(partition_root.resolve()), resolved)
                self.assertEqual("partition", source)

                service.catalog.save_app_setting("creation_defaults", {"output_parent": str(application_root)})
                resolved, source = _default_output_root(service.catalog, {}, {}, str(root / "pipeline"))
                self.assertEqual(str(application_root.resolve()), resolved)
                self.assertEqual("application", source)

                service.catalog.save_app_setting("creation_defaults", {"output_parent": ""})
                resolved, source = _default_output_root(service.catalog, {}, {}, str(root / "pipeline"))
                self.assertEqual(str((root / "pipeline" / "exports").resolve()), resolved)
                self.assertEqual("managed_fallback", source)

                creation = __import__("chroma_db_import.workflow.reconciliation", fromlist=["_creation_defaults"])._creation_defaults(
                    service.catalog,
                    {"partition_id": "partition-two", "display_name": "", "corpus_id": "", "context_settings": {"revision": 1, "settings": {"output_root": str(partition_root), "execution_options": {"embedding_device": "cuda:0"}}}},
                    {"id": "connection-two", "root": str(root / "pipeline")},
                    {"profile": {"representation_profile": "profile-custom", "selection_policy": {"speaker_mode": "allowlist", "allowlist_speakers": ["Host"], "asset_filter": "all"}}, "profile_fingerprint": "profile-fingerprint"},
                    {"upstream_release_id": "release-two"},
                )
                self.assertEqual("partition-two", creation["display_name"])
                self.assertEqual("profile-custom", creation["representation"]["profile"])
                self.assertEqual("cuda:0", creation["execution_options"]["embedding_device"])
                self.assertEqual("allowlist", creation["selection_policy"]["speaker_mode"])
                self.assertEqual(str(partition_root / "partitions" / "partition-two"), creation["target"]["path"])
            finally:
                service.shutdown()

    def test_reconciliation_does_not_modify_pipeline_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "pipeline"
            source.mkdir()
            self._source(source)
            with ManagedCatalog(source / "producer-catalog.sqlite3") as catalog:
                discover(catalog, [source])
            producer_catalog = source / "producer-catalog.sqlite3"
            before = producer_catalog.read_bytes()
            service = WorkflowService(root / "state")
            try:
                service.catalog.save_source_connection(str(source), str(producer_catalog))
                with patch("chroma_db_import.workflow.reconciliation._source_status", return_value=self._status("release-one")):
                    service.reconcile_sources()
                self.assertEqual(before, producer_catalog.read_bytes())
            finally:
                service.shutdown()

    def test_explicit_link_requires_strict_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = __import__("chroma_db_import.workflow.catalog", fromlist=["AppCatalog"]).AppCatalog(root / "catalog.sqlite3")
            catalog.save_source_connection(str(root), str(root / "source.sqlite3"), connection_id="connection-one")
            catalog.save_database_link({
                "database_id": "db-one", "connection_id": "connection-one", "partition_id": "partition-one",
                "corpus_id": "other-corpus", "source_root": str(root), "target_key": "target",
                "state": "linked", "origin": "created",
            })
            from chroma_db_import.workflow.reconciliation import _explicit_link_matches

            self.assertFalse(_explicit_link_matches(catalog.get_database_link("db-one"), {"id": "connection-one", "root": str(root)}, {"partition_id": "partition-one", "corpus_id": "partition-one"}))

    def test_multiple_links_require_and_honor_persisted_destination_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "pipeline"
            source.mkdir()
            self._source(source)
            service = WorkflowService(root / "state")
            try:
                connection = service.catalog.save_source_connection(str(source), str(root / "producer.sqlite3"))
                for database_id in ("db-one", "db-two"):
                    target = root / "exports" / database_id
                    target.mkdir(parents=True)
                    service.catalog.create_database(DatabaseRecord(
                        id=database_id, display_name=database_id, source_kind="managed",
                        source_ref={"connection_id": connection["id"], "source_root": str(source), "partition_id": "partition-one", "corpus_id": "partition-one"},
                        target={"path": str(target), "managed_output_root": str(root / "exports")},
                        selection_policy=SelectionPolicy().as_dict(),
                        downstream_identity={"upstream_release_id": "release-one", "downstream_release_id": f"downstream-{database_id}"},
                    ).as_dict())
                write_release(source, "partition-one", "partition-one", "release-two", "cache-one")

                with patch("chroma_db_import.workflow.reconciliation._source_status", return_value=self._status("release-two")):
                    ambiguous = service.reconcile_sources()["contexts"][0]
                self.assertEqual("ambiguous_match", ambiguous["tracking"]["state"])
                self.assertIn("Multiple databases", ambiguous["tracking"]["reason"])
                self.assertEqual([], [item for item in ambiguous["tracking"]["matching_databases"] if item.get("selected")])

                service.catalog.archive_database("db-one")
                with patch("chroma_db_import.workflow.reconciliation._source_status", return_value=self._status("release-two")):
                    active_only = service.reconcile_sources()["contexts"][0]
                self.assertEqual("update_available", active_only["tracking"]["state"])
                self.assertEqual("release-one", active_only["tracking"]["last_applied_release_id"])

                selected = service.select_database_link({
                    "database_id": "db-two", "connection_id": connection["id"], "partition_id": "partition-one",
                })
                self.assertTrue(selected["selected"])
                with patch("chroma_db_import.workflow.reconciliation._source_status", return_value=self._status("release-two")):
                    resolved = service.reconcile_sources()["contexts"][0]
                self.assertEqual("update_available", resolved["tracking"]["state"])
                self.assertEqual("release-one", resolved["tracking"]["last_applied_release_id"])
                chosen = [item for item in resolved["tracking"]["matching_databases"] if item.get("selected")]
                self.assertEqual(["db-two"], [item["database_id"] for item in chosen])
            finally:
                service.shutdown()

    def test_unavailable_root_keeps_cached_context_and_history_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "pipeline"
            source.mkdir()
            self._source(source)
            service = WorkflowService(root / "state")
            try:
                connection = service.catalog.save_source_connection(str(source), str(root / "producer.sqlite3"))
                with patch("chroma_db_import.workflow.reconciliation._source_status", return_value=self._status("release-one")):
                    service.reconcile_sources()
                shutil.rmtree(source)
                unavailable = service.reconcile_sources()
                context = unavailable["contexts"][0]
                self.assertEqual("source_unavailable", context["tracking"]["state"])
                self.assertIn("unavailable", context["tracking"]["reason"].lower())
                self.assertTrue(service.catalog.list_source_observations(connection_id=connection["id"], partition_id="partition-one"))
            finally:
                service.shutdown()

    def test_archiving_partition_is_reversible_local_state_and_preserves_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "pipeline"
            source.mkdir()
            self._source(source)
            sentinel = source / "producer-input.txt"
            sentinel.write_bytes(b"producer-owned content")
            database_sentinel = root / "exports" / "partition-one" / "chroma.sqlite3"
            database_sentinel.parent.mkdir(parents=True)
            database_sentinel.write_bytes(b"database-owned content")
            service = WorkflowService(root / "state")
            try:
                connection = service.catalog.save_source_connection(str(source), str(root / "producer.sqlite3"))
                with patch("chroma_db_import.workflow.reconciliation._source_status", return_value=self._status("release-one")):
                    service.reconcile_sources()
                before = sentinel.read_bytes()
                database_before = database_sentinel.read_bytes()
                ref = {"connection_id": connection["id"], "partition_id": "partition-one"}

                archived = service.set_context_archived(ref, True)
                self.assertEqual("archived", archived["local_status"])
                self.assertEqual(before, sentinel.read_bytes())
                self.assertEqual(database_before, database_sentinel.read_bytes())
                self.assertEqual([], service.list_contexts())
                self.assertEqual("archived", service.list_contexts(include_archived=True)[0]["local_status"])

                restored = service.set_context_archived(ref, False)
                self.assertEqual("active", restored["local_status"])
                self.assertEqual(1, len(service.list_contexts()))
                self.assertEqual(before, sentinel.read_bytes())
                self.assertEqual(database_before, database_sentinel.read_bytes())
            finally:
                service.shutdown()

    def test_archived_database_cannot_be_adopted_as_an_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            service = WorkflowService(root / "state")
            try:
                database = DatabaseRecord(
                    id="db-archived", display_name="Archived", source_kind="managed",
                    source_ref={"connection_id": "connection-one", "source_root": str(root), "partition_id": "partition-one", "corpus_id": "partition-one"},
                    target={"path": str(root / "exports" / "partition-one")}, archived=True,
                    selection_policy=SelectionPolicy().as_dict(),
                )
                service.catalog.create_database(database.as_dict())
                with self.assertRaisesRegex(ValueError, "Archived databases are not eligible"):
                    service.adopt_database_link({"database_id": "db-archived", "connection_id": "connection-one", "partition_id": "partition-one"})
            finally:
                service.shutdown()


if __name__ == "__main__":
    unittest.main()
