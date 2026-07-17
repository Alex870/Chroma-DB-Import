import hashlib
import json
import unittest
from pathlib import Path

import chroma_db_import.runtime as runtime
from chroma_db_import.importer import load_processed_documents, load_processed_payload


class Document:
    def __init__(self, page_content, metadata):
        self.page_content = page_content
        self.metadata = metadata


class ContractFixtureTests(unittest.TestCase):
    def test_rag_fixture_checksum_and_parser(self):
        runtime.RUNTIME_DEPS_LOADED = True
        runtime.Document = Document
        root = Path(__file__).parent / "fixtures" / "contracts" / "podcast-rag" / "2.1"
        origin = json.loads((root / "origin.json").read_text(encoding="utf-8"))
        fixture = root / "processed_cache.json"
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(), origin["files"][fixture.name])
        self.assertEqual("2.1", load_processed_payload(fixture)["schema_version"])
        docs = load_processed_documents(fixture)
        self.assertEqual(["leaf_1", "thesis_1"], [doc.metadata["node_id"] for doc in docs])


if __name__ == "__main__":
    unittest.main()
