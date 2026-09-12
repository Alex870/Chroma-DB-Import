import unittest
from pathlib import Path

from chroma_db_import.config import ImportConfig
from chroma_db_import.importer import representation_spec
from chroma_db_import.providers import PINNED_MODEL_REVISIONS, RepresentationEmbeddingProvider
from chroma_db_import.representation import (
    QWEN3_MODEL,
    QWEN3_MODEL_REVISION,
    QWEN3_PROFILE,
    QWEN3_QUERY_INSTRUCTION_PROFILE,
    RepresentationSpec,
    query_text,
    recommended_batch_size,
    resolved_collection_name,
    resolved_profile_path,
    validate_representation_manifest,
)


class RecordingProvider:
    def __init__(self):
        self.queries = []
        self.documents = []

    def embed_query(self, text):
        self.queries.append(text)
        return [3.0, 4.0]

    def embed_documents(self, texts):
        self.documents.extend(texts)
        return [[3.0, 4.0] for _ in texts]


class Qwen3RepresentationTests(unittest.TestCase):
    def test_qwen_is_the_primary_pinned_profile(self):
        config = ImportConfig()
        spec = representation_spec(config)
        self.assertEqual(QWEN3_PROFILE, config.representation_profile)
        self.assertEqual(QWEN3_MODEL, spec.model_id)
        self.assertEqual(QWEN3_MODEL_REVISION, spec.model_revision)
        self.assertEqual(2560, spec.dimension)
        self.assertEqual("bfloat16", spec.inference_dtype)
        self.assertEqual(QWEN3_QUERY_INSTRUCTION_PROFILE, spec.query_instruction_profile)
        self.assertEqual(QWEN3_MODEL_REVISION, PINNED_MODEL_REVISIONS[QWEN3_MODEL])

    def test_query_instruction_is_separate_from_document_encoding(self):
        provider = RecordingProvider()
        spec = RepresentationSpec(dimension=2)
        adapter = RepresentationEmbeddingProvider(provider, spec)
        adapter.embed_documents(["Episode: Example\n\nDocument text"])
        adapter.embed_query("What happened?")
        self.assertNotIn("Instruct:", provider.documents[0])
        self.assertIn("Instruct:", provider.queries[0])
        self.assertIn("Query: What happened?", provider.queries[0])

    def test_query_instruction_and_dtype_are_part_of_identity(self):
        first = RepresentationSpec(dimension=2)
        changed_dtype = RepresentationSpec(dimension=2, inference_dtype="float32")
        changed_query = RepresentationSpec(dimension=2, query_instruction_profile="other", query_document_mode="other")
        self.assertNotEqual(first.representation_id, changed_dtype.representation_id)
        self.assertNotEqual(first.representation_id, changed_query.representation_id)
        self.assertEqual(query_text("question", first), "Instruct: Retrieve podcast passages that best answer the user’s question, preserving speaker, episode, and viewpoint relevance.\nQuery: question")

    def test_profile_selection_is_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            representation_spec(ImportConfig(representation_profile="custom"))
        with self.assertRaises(ValueError):
            representation_spec(ImportConfig(representation_profile="custom", embedding_model="local/model"))

    def test_qwen_batch_and_storage_are_isolated(self):
        self.assertEqual(2, recommended_batch_size("cuda", model_id=QWEN3_MODEL, profile=QWEN3_PROFILE))
        self.assertEqual(1, recommended_batch_size("cuda", 8 * 1024**3, model_id=QWEN3_MODEL, profile=QWEN3_PROFILE))
        self.assertNotEqual("collection", resolved_collection_name("collection", QWEN3_PROFILE))
        self.assertEqual(Path("exports") / QWEN3_PROFILE, resolved_profile_path(Path("exports"), QWEN3_PROFILE))

    def test_manifest_contract_rejects_query_provider_mismatch(self):
        spec = representation_spec(ImportConfig())
        manifest = {"representation": spec.as_dict(), "embedding_model": spec.model_id, "embedding_dimension": 2560}
        validate_representation_manifest(manifest, spec)
        manifest["representation"]["query_instruction_profile"] = "none"
        with self.assertRaises(ValueError):
            validate_representation_manifest(manifest, spec)


if __name__ == "__main__":
    unittest.main()
