from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chroma_db_import.workflow.folder_adapter import FolderAdapter
from chroma_db_import.workflow.models import BridgeError, DatabaseRecord, SelectionPolicy
from chroma_db_import.workflow.planning import create_folder_preview


class FixtureEmbeddings:
    @staticmethod
    def vector() -> list[float]:
        return [1.0] + [0.0] * 2559

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.vector() for _ in texts]

    def embed_query(self, _text: str) -> list[float]:
        return self.vector()


class CountingEmbeddings(FixtureEmbeddings):
    calls = 0

    def embed_query(self, text: str) -> list[float]:
        type(self).calls += 1
        return super().embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        type(self).calls += len(texts)
        return super().embed_documents(texts)


class GuiFolderExecutionTests(unittest.TestCase):
    def test_folder_preview_and_execution_use_real_chroma_with_fake_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "processed"
            source.mkdir()
            source_file = source / "episode-one.processed_documents.json"
            source_file.write_text(json.dumps({
                "schema_version": "2.1",
                "documents": [
                    {"page_content": "Host explains the topic.", "metadata": {
                        "node_id": "leaf-one", "node_type": "leaf_chunk", "source": "episode.json",
                        "source_type": "json_transcript", "episode_id": "episode-1", "episode_title": "Episode One",
                        "episode_date": "2026-01-01", "speaker": "Host", "speaker_scope": "single",
                    }},
                    {"page_content": "The episode thesis.", "metadata": {
                        "node_id": "thesis-one", "node_type": "episode_thesis", "source": "episode.json",
                        "source_type": "json_transcript", "episode_id": "episode-1", "episode_title": "Episode One",
                        "episode_date": "2026-01-01", "speaker_scope": "episode", "child_ids": ["leaf-one"],
                    }},
                ],
            }), encoding="utf-8")
            target = root / "export"
            policy = SelectionPolicy(asset_filter="all")
            draft = {
                "source_kind": "folder", "source_ref": {"path": str(source)}, "display_name": "Fixture Podcast",
                "target": {"path": str(target), "collection_name": "fixture", "representation_profile": "qwen3-embedding-4b-shadow"},
                "representation_profile": "qwen3-embedding-4b-shadow", "embedding_model": "Qwen/Qwen3-Embedding-4B", "embedding_model_revision": "5cf2132abc99cad020ac570b19d031efec650f2b",
                "selection_policy": policy.as_dict(),
            }
            preview = create_folder_preview(None, draft, operation="create")
            record = DatabaseRecord(
                id="db-folder", display_name="Fixture Podcast", source_kind="folder",
                source_ref={"path": str(source)}, target={**draft["target"]}, selection_policy=policy.as_dict(),
            )
            original_bytes = source_file.read_bytes()
            snapshot = FolderAdapter._capture_source_snapshot(record, preview, target)
            try:
                payload = json.loads(source_file.read_text(encoding="utf-8"))
                payload["documents"][0]["page_content"] = "Changed after review."
                source_file.write_text(json.dumps(payload), encoding="utf-8")
                frozen_plan = FolderAdapter().build_plan(record, preview, source_root=snapshot)
                self.assertEqual("Host explains the topic.", frozen_plan.episodes[0].documents[0].page_content)
                self.assertEqual(source_file, frozen_plan.episodes[0].source_file_path)
                accepted_entry = next(item for item in preview.source_snapshot["files"] if item["path"] == str(source_file.resolve()))
                self.assertEqual(accepted_entry["fingerprint"], frozen_plan.episodes[0].fingerprint)
            finally:
                source_file.write_bytes(original_bytes)
                shutil.rmtree(snapshot, ignore_errors=True)
            with patch("chroma_db_import.ui_export.create_embedding_provider", return_value=(FixtureEmbeddings(), {"device": "cpu", "profile": "qwen3-embedding-4b-shadow"})), \
                patch("chroma_db_import.ui_export.resolve_embedding_device", return_value="cpu"):
                result = FolderAdapter().execute(record, preview, lambda _progress: None)

            self.assertEqual("new_version_active", result["active_database_state"])
            self.assertEqual(2, result["actual_writes"])
            self.assertTrue((target / "chroma.sqlite3").is_file())
            self.assertTrue((target / "import_manifest.json").is_file())
            self.assertEqual("qwen3-embedding-4b-shadow", result["downstream_identity"]["profile"])

            update_draft = {**draft, "representation": json.loads((target / "import_manifest.json").read_text(encoding="utf-8"))["representation"]}
            update_preview = create_folder_preview(record, update_draft, operation="update")
            import chromadb
            from chroma_db_import.representation import resolved_collection_name

            client = chromadb.PersistentClient(path=str(target))
            try:
                collection = client.get_collection(resolved_collection_name("fixture", "qwen3-embedding-4b-shadow"))
                collection.add(ids=["foreign-record"], documents=["foreign content"], embeddings=[FixtureEmbeddings.vector()])
            finally:
                try:
                    client._system.stop()
                except Exception:
                    pass
                chromadb.api.client.SharedSystemClient.clear_system_cache()
            with self.assertRaises(BridgeError) as raised:
                FolderAdapter().execute(record, update_preview, lambda _progress: None)
            self.assertEqual("PREVIEW_STALE", raised.exception.code)

    def test_metadata_only_update_does_not_load_embedding_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "processed"
            source.mkdir()
            source_file = source / "episode-one.processed_documents.json"
            source_file.write_text(json.dumps({
                "schema_version": "2.1",
                "documents": [
                    {"page_content": "Stable text.", "metadata": {
                        "node_id": "stable-one", "node_type": "leaf_chunk", "source": "episode.json",
                        "source_type": "json_transcript", "episode_id": "episode-1", "episode_title": "Episode One",
                        "episode_date": "2026-01-01", "speaker": "Host", "speaker_scope": "single",
                    }},
                    {"page_content": "Episode thesis.", "metadata": {
                        "node_id": "thesis-one", "node_type": "episode_thesis", "source": "episode.json",
                        "source_type": "json_transcript", "episode_id": "episode-1", "episode_title": "Episode One",
                        "episode_date": "2026-01-01", "speaker_scope": "episode", "child_ids": ["stable-one"],
                    }},
                ],
            }), encoding="utf-8")
            target = root / "export"
            policy = SelectionPolicy(asset_filter="all")
            draft = {
                "source_kind": "folder", "source_ref": {"path": str(source)}, "display_name": "Fixture Podcast",
                "target": {"path": str(target), "collection_name": "fixture", "representation_profile": "qwen3-embedding-4b-shadow"},
                "representation_profile": "qwen3-embedding-4b-shadow", "embedding_model": "Qwen/Qwen3-Embedding-4B", "embedding_model_revision": "5cf2132abc99cad020ac570b19d031efec650f2b",
                "selection_policy": policy.as_dict(),
            }
            record = DatabaseRecord(
                id="db-folder", display_name="Fixture Podcast", source_kind="folder",
                source_ref={"path": str(source)}, target={**draft["target"]}, selection_policy=policy.as_dict(),
            )
            create_preview = create_folder_preview(None, draft, operation="create")
            with patch("chroma_db_import.ui_export.create_embedding_provider", return_value=(CountingEmbeddings(), {"device": "cpu", "profile": "qwen3-embedding-4b-shadow"})), \
                patch("chroma_db_import.ui_export.resolve_embedding_device", return_value="cpu"):
                FolderAdapter().execute(record, create_preview, lambda _progress: None)
            draft["representation"] = json.loads((target / "import_manifest.json").read_text(encoding="utf-8"))["representation"]

            payload = json.loads(source_file.read_text(encoding="utf-8"))
            payload["documents"][0]["metadata"]["private_note"] = "changed"
            source_file.write_text(json.dumps(payload), encoding="utf-8")
            update_preview = create_folder_preview(record, draft, operation="update")
            self.assertEqual(["stable-one"], update_preview.effects.metadata_only_ids)
            with patch("chroma_db_import.ui_export.create_embedding_provider", side_effect=AssertionError("embedding provider loaded")), \
                patch("chroma_db_import.ui_export.resolve_embedding_device", side_effect=AssertionError("embedding device resolved")):
                result = FolderAdapter().execute(record, update_preview, lambda _progress: None)
            self.assertEqual(1, result["actual_writes"])
            self.assertEqual("new_version_active", result["active_database_state"])


if __name__ == "__main__":
    unittest.main()
