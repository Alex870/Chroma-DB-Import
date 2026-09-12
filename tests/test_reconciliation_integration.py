import gc
import tempfile
import time
import unittest
from importlib.util import find_spec
from pathlib import Path

from chroma_db_import.config import ImportConfig
from chroma_db_import.importer import document_fingerprints, representation_spec
from chroma_db_import.ui_export import preview_ui_reconciliation
from chroma_db_import.ui_models import Episode, ImportPlan, ProcessedDocument
from chroma_db_import.representation import QWEN3_MODEL, resolved_collection_name


@unittest.skipUnless(find_spec("chromadb"), "chromadb is not installed in this environment")
class ReconciliationIntegrationTests(unittest.TestCase):
    def test_temporary_chroma_preview_classifies_every_outcome(self):
        import chromadb

        scratch = Path(__file__).parent.parent / ".test_tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as tmp:
            root = Path(tmp)
            source = root / "episode.processed_documents.json"
            source.write_text("{}", encoding="utf-8")
            output = root / "output"
            plan = ImportPlan(
                podcast_name="Fixture Podcast",
                database_id="fixture",
                processed_data_dir=root,
                output_root=output,
                collection_name="fixture",
                embedding_model=QWEN3_MODEL,
                embedding_device="cpu",
                episodes=[],
                included_speakers_by_episode={},
            )
            plan.export_dir.mkdir(parents=True)
            spec = representation_spec(
                ImportConfig(
                    representation_profile=plan.resolved_profile,
                    embedding_model=plan.embedding_model,
                    embedding_model_revision=plan.embedding_model_revision,
                    inference_dtype=plan.inference_dtype,
                    query_instruction_profile=plan.query_instruction_profile,
                    embedding_device=plan.embedding_device,
                    contextualization=plan.contextualization,
                )
            )

            def document(item_id, text, note):
                return ProcessedDocument(text, {
                    "node_id": item_id, "stable_document_id": item_id, "node_type": "leaf_chunk",
                    "speaker": "Host", "podcast": "Fixture Podcast", "episode_title": "Episode",
                    "episode_date": "2026-01-01", "private_note": note,
                })

            old = {
                "same": document("same", "same text", "same"),
                "meta": document("meta", "metadata text", "old"),
                "changed": document("changed", "old text", "same"),
                "removed": document("removed", "removed text", "same"),
            }
            current = [
                document("same", "same text", "same"),
                document("meta", "metadata text", "new"),
                document("changed", "new text", "same"),
                document("added", "added text", "same"),
            ]
            episode = Episode(source, "source-fingerprint", "Episode", "episode", "2026-01-01", current, ["Host"], {"leaf_chunk": 4})
            plan.episodes = [episode]
            plan.included_speakers_by_episode = {episode.fingerprint: {"Host"}}

            client = chromadb.PersistentClient(path=str(plan.export_dir))
            collection = client.create_collection(resolved_collection_name("fixture", plan.resolved_profile))
            ids, documents, metadatas, embeddings = [], [], [], []
            for index, (item_id, item) in enumerate(old.items()):
                metadata = dict(item.metadata)
                metadata.update(document_fingerprints(item, spec))
                metadata["import_source_cache"] = str(source.resolve())
                ids.append(item_id); documents.append(item.page_content); metadatas.append(metadata)
                embeddings.append([float(index + 1), 0.0, 0.0, 0.0])
            collection.add(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)

            preview = preview_ui_reconciliation(plan)
            self.assertEqual(preview.added, ["added"])
            self.assertEqual(preview.changed, ["changed"])
            self.assertEqual(preview.metadata_only, ["meta"])
            self.assertEqual(preview.unchanged, ["same"])
            self.assertEqual(preview.removed, ["removed"])
            client._system.stop()
            chromadb.api.client.SharedSystemClient.clear_system_cache()
            del collection
            del client
            gc.collect()
            time.sleep(0.1)


if __name__ == "__main__":
    unittest.main()
