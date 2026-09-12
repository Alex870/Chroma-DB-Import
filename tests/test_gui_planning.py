from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chroma_db_import.contract import sanitize_metadata
from chroma_db_import.representation import RepresentationSpec, embedding_fingerprint, metadata_fingerprint
from chroma_db_import.ui_loader import EpisodeLoader
from chroma_db_import.workflow.models import DatabaseRecord, FrozenPreview, SelectionPolicy, BridgeError
from chroma_db_import.workflow.planning import create_folder_preview, inspect_existing_records, scan_folder, verify_source_snapshot
from chroma_db_import.workflow.service import WorkflowService


FIXTURES = Path(__file__).parent / "fixtures" / "gui"


class GuiPlanningTests(unittest.TestCase):
    def draft(self, source: Path, target: Path) -> dict:
        return {
            "source_kind": "folder", "source_ref": {"path": str(source)}, "display_name": "Fixture Podcast",
            "target": {"path": str(target), "collection_name": "fixture"}, "selection_policy": SelectionPolicy().as_dict(),
        }

    def test_create_preview_has_separate_effect_categories_and_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "processed"
            source.mkdir()
            for fixture in FIXTURES.glob("*.processed_documents.json"):
                (source / fixture.name).write_bytes(fixture.read_bytes())
            preview = create_folder_preview(None, self.draft(source, Path(temp) / "export"), operation="create")
            self.assertEqual(preview.effects.episodes_total, 2)
            self.assertEqual(preview.effects.records_total, 5)
            self.assertEqual(len(preview.effects.insert_ids), 5)
            self.assertTrue(preview.source_snapshot["hash"])
            scan = scan_folder(source, SelectionPolicy())
            self.assertEqual(2, scan["episodes"])
            self.assertEqual(2, len(scan["episode_inventory"]))
            self.assertEqual({"start": "2026-01-01", "end": "2026-01-08"}, scan["date_range"])
            self.assertIn("excluded_files", scan)

            (source / "episode-one.processed_documents.json").write_text("changed", encoding="utf-8")
            with self.assertRaises(BridgeError) as raised:
                verify_source_snapshot(preview.source_snapshot)
            self.assertEqual(raised.exception.code, "PREVIEW_STALE")

    def test_create_preview_refuses_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            (source / "one.processed_documents.json").write_bytes((FIXTURES / "episode-one.processed_documents.json").read_bytes())
            target = Path(temp) / "existing"
            target.mkdir()
            with self.assertRaises(BridgeError) as raised:
                create_folder_preview(None, self.draft(source, target), operation="create")
            self.assertEqual(raised.exception.code, "TARGET_EXISTS")

    def test_apply_rejects_database_settings_changed_after_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "episode-one.processed_documents.json").write_bytes((FIXTURES / "episode-one.processed_documents.json").read_bytes())
            service = WorkflowService(root / "state")
            record = service.catalog.create_database(DatabaseRecord(
                id="db-settings", display_name="Fixture Podcast", source_kind="folder",
                source_ref={"path": str(source)}, target={"path": str(root / "export"), "collection_name": "fixture"},
            ).as_dict())
            preview = service.create_preview({"database_id": record["id"], "operation": "update"})
            service.update_database_settings(record["id"], {"selection_policy": {**SelectionPolicy().as_dict(), "asset_filter": "all"}})
            with self.assertRaises(BridgeError) as raised:
                service.apply_preview(preview["preview_id"])
            self.assertEqual(raised.exception.code, "PREVIEW_STALE")
            service.shutdown()

    def test_one_run_selection_uses_base_settings_without_persisting_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "episode-one.processed_documents.json").write_bytes((FIXTURES / "episode-one.processed_documents.json").read_bytes())
            service = WorkflowService(root / "state")
            record = service.catalog.create_database(DatabaseRecord(
                id="db-one-run", display_name="Fixture Podcast", source_kind="folder",
                source_ref={"path": str(source)}, target={"path": str(root / "export"), "collection_name": "fixture"},
            ).as_dict())
            requested = {**SelectionPolicy.from_mapping(record["selection_policy"]).as_dict(), "excluded_speakers": ["Guest"]}
            preview = service.create_preview({"database_id": record["id"], "operation": "update", "selection_policy": requested})
            current = DatabaseRecord.from_mapping(service.get_database(record["id"]))
            self.assertNotEqual(requested, current.selection_policy)
            self.assertTrue(preview["base_settings_hash"])
            WorkflowService._validate_preview_current(current, FrozenPreview.from_mapping(preview))
            service.shutdown()

    def test_update_preview_blocks_representation_profile_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "episode-one.processed_documents.json").write_bytes((FIXTURES / "episode-one.processed_documents.json").read_bytes())
            service = WorkflowService(root / "state")
            record = service.catalog.create_database(DatabaseRecord(
                id="db-profile-mismatch", display_name="Profile Fixture", source_kind="folder",
                source_ref={"path": str(source)}, target={"path": str(root / "export"), "representation_profile": "qwen3-embedding-4b-shadow"},
                downstream_identity={"profile": "qwen3-embedding-4b-shadow"},
            ).as_dict())
            with self.assertRaisesRegex(ValueError, "Qwen3"):
                service.create_preview({"database_id": record["id"], "operation": "update", "representation": {"profile": "legacy-embedding"}})
            service.shutdown()

    def test_content_inventory_exposes_episode_selection_without_document_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "export"
            target.mkdir()
            (target / "podcast.json").write_text(json.dumps({
                "speakers": [{"id": "host", "name": "Host"}, {"id": "guest", "name": "Guest"}],
                "episodes": [{"episode_id": "episode-1", "episode_title": "First", "episode_date": "2026-01-01", "document_count": 3, "speakers": [{"name": "Host"}]}],
            }), encoding="utf-8")
            service = WorkflowService(root / "state")
            record = service.catalog.create_database(DatabaseRecord(
                id="db-content", display_name="Content Fixture", source_kind="folder",
                source_ref={"path": str(root / "processed")}, target={"path": str(target)},
                selection_policy=SelectionPolicy(excluded_episode_ids=["episode-1"], episode_overrides={"episode-1": ["Host"]}).as_dict(),
            ).as_dict())
            content = service.get_database_content(record["id"])
            self.assertEqual(["Guest", "Host"], content["speakers"])
            self.assertEqual("episode-1", content["episodes"][0]["episode_id"])
            self.assertTrue(content["episodes"][0]["excluded"])
            self.assertEqual(["Host"], content["episodes"][0]["override_speakers"])
            self.assertNotIn("page_content", json.dumps(content))
            service.shutdown()

    def test_existing_importer_fingerprints_are_used_by_preview_inventory(self) -> None:
        import gc
        import chromadb

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "processed"
            source.mkdir()
            (source / "episode-one.processed_documents.json").write_bytes((FIXTURES / "episode-one.processed_documents.json").read_bytes())
            episode = EpisodeLoader().load_folder(source, "reviewed_speaker_transcript", "")[0]
            document = episode.documents[0]
            metadata = sanitize_metadata(dict(document.metadata))
            target = root / "export"
            client = chromadb.PersistentClient(path=str(target))
            collection = client.create_collection("fixture")
            collection.add(ids=[str(metadata["node_id"])], documents=[document.page_content], metadatas=[{
                **metadata,
                "embedding_fingerprint": embedding_fingerprint(document.page_content, metadata, RepresentationSpec()),
                "metadata_fingerprint": metadata_fingerprint(metadata),
            }], embeddings=[[0.1, 0.2, 0.3]])
            inventory = inspect_existing_records(target, "fixture")
            self.assertEqual(inventory[str(metadata["node_id"])]["fingerprint"], embedding_fingerprint(document.page_content, metadata, RepresentationSpec()))
            preview = create_folder_preview(None, self.draft(source, target), operation="update")
            self.assertIn(str(metadata["node_id"]), preview.effects.unchanged_ids)
            payload = json.loads((source / "episode-one.processed_documents.json").read_text(encoding="utf-8"))
            payload["documents"][0]["metadata"]["private_note"] = "changed without changing the embedded representation"
            (source / "episode-one.processed_documents.json").write_text(json.dumps(payload), encoding="utf-8")
            metadata_preview = create_folder_preview(None, self.draft(source, target), operation="update")
            self.assertIn(str(metadata["node_id"]), metadata_preview.effects.metadata_only_ids)
            self.assertNotIn(str(metadata["node_id"]), metadata_preview.effects.unchanged_ids)
            existing_inventory = inspect_existing_records(target, "fixture")
            self.assertEqual(
                len(set(existing_inventory) | set(metadata_preview.effects.insert_ids) | set(metadata_preview.effects.metadata_only_ids)),
                metadata_preview.effects.records_total,
            )
            try:
                client._system.stop()
            except Exception:
                pass
            try:
                chromadb.api.client.SharedSystemClient.clear_system_cache()
            except Exception:
                pass
            del collection
            del client
            gc.collect()

    def test_removal_preview_lists_exact_episode_scope_and_reasons(self) -> None:
        import gc
        import chromadb

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "processed"
            source.mkdir()
            (source / "episode-one.processed_documents.json").write_bytes((FIXTURES / "episode-one.processed_documents.json").read_bytes())
            target = root / "export"
            client = chromadb.PersistentClient(path=str(target))
            collection = client.create_collection("fixture")
            collection.add(
                ids=["record-old"], documents=["old content"], embeddings=[[0.1, 0.2, 0.3]],
                metadatas=[{"episode_id": "episode-old", "episode_title": "Old episode"}],
            )
            draft = self.draft(source, target)
            draft["delete_ids"] = ["record-old"]
            preview = create_folder_preview(None, draft, operation="remove_outdated")
            self.assertEqual(["record-old"], preview.effects.delete_ids)
            self.assertEqual("episode-old", preview.effects.delete_episodes[0]["episode_id"])
            self.assertEqual(["record-old"], preview.effects.delete_episodes[0]["record_ids"])
            self.assertIn("Old episode", preview.effects.reasons["record-old"])
            try:
                client._system.stop()
            except Exception:
                pass
            try:
                chromadb.api.client.SharedSystemClient.clear_system_cache()
            except Exception:
                pass
            del collection
            del client
            gc.collect()


if __name__ == "__main__":
    unittest.main()
