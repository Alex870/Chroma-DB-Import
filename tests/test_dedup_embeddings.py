import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from chroma_db_import.config import ImportConfig
from chroma_db_import.importer import ChromaImporter
from chroma_db_import.providers import EmbeddingCompatibilityError
from chroma_db_import.representation import RepresentationSpec, embedding_text


class CountingEmbeddings:
    def __init__(self, dimension=3, wrong_count=False):
        self.dimension = dimension
        self.wrong_count = wrong_count
        self.calls = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        count = len(texts) - 1 if self.wrong_count else len(texts)
        return [[float(index + axis) for axis in range(self.dimension)] for index in range(count)]


class DedupEmbeddingTests(unittest.TestCase):
    def importer(self, embeddings, directory):
        importer = object.__new__(ChromaImporter)
        importer.config = ImportConfig(cache_embeddings=False)
        importer.spec = RepresentationSpec(dimension=3)
        importer.embedding_dimension = 3
        importer.embeddings = embeddings
        importer.embedding_cache_dir = Path(directory) / "embedding-cache"
        importer.last_cache_stats = {"hits": 0, "misses": 0, "invalidations": 0, "cache_size": 0}
        return importer

    def test_uncached_equal_provider_inputs_are_coalesced(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp" / "dedup") as directory:
            provider = CountingEmbeddings()
            importer = self.importer(provider, directory)
            first = SimpleNamespace(page_content="Same", metadata={"node_type": "leaf_chunk"})
            second = SimpleNamespace(page_content="Same", metadata={"node_type": "leaf_chunk"})
            vectors, _ = importer.embed_documents_cached([first, second])
            self.assertEqual(1, len(provider.calls[0]))
            self.assertEqual(vectors[0], vectors[1])
            self.assertEqual(2, importer.last_cache_stats["shared_vector_records"])

    def test_provider_count_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp" / "dedup") as directory:
            importer = self.importer(CountingEmbeddings(wrong_count=True), directory)
            doc = SimpleNamespace(page_content="One", metadata={"node_type": "leaf_chunk"})
            with self.assertRaises(EmbeddingCompatibilityError):
                importer.embed_documents_cached([doc])


if __name__ == "__main__":
    unittest.main()
