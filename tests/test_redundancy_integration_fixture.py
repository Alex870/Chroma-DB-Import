import hashlib
import json
import sqlite3
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chroma_db_import.dedup_artifacts import write_dedup_artifacts
from chroma_db_import.deduplication import DedupInput, build_exact_plan
from chroma_db_import.redundancy_artifacts import RedundancyArtifactError, publish_bundle, validate_bundle, write_bundle
from chroma_db_import.redundancy_evidence import build_representative_map
from chroma_db_import.redundancy_models import Scope
from chroma_db_import.redundancy_retrieval import eligible_occurrences, hydrate_occurrences, query_representatives, representatives_for
from chroma_db_import.representation import embedding_text
from chroma_db_import.semantic_selection import select_evidence


class RedundancyIntegrationFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp")
        self.root = Path(self.tmp.name)
        self.scope = Scope("partition-1", "corpus-1", "release-1", "representation-1")
        self.rows = [
            {"document_id": "occ-a", "text": "The host says the market is growing.", "metadata": {"episode_uid": "episode-1", "speaker": "Host", "episode_date": "2025-01-01"}, "episode_uid": "episode-1", "node_type": "leaf", "source_span_ids": ["span-1"]},
            {"document_id": "occ-b", "text": "The host says the market is growing.", "metadata": {"episode_uid": "episode-2", "speaker": "Host", "episode_date": "2025-01-02"}, "episode_uid": "episode-2", "node_type": "leaf", "source_span_ids": ["span-2"]},
            {"document_id": "occ-c", "text": "The host says the market may be growing.", "metadata": {"episode_uid": "episode-3", "speaker": "Guest", "episode_date": "2025-01-03"}, "episode_uid": "episode-3", "node_type": "leaf", "source_span_ids": ["span-3"]},
            {"document_id": "occ-d", "text": "The host says the market is not growing.", "metadata": {"episode_uid": "episode-4", "speaker": "Host", "episode_date": "2025-01-04"}, "episode_uid": "episode-4", "node_type": "leaf", "source_span_ids": ["span-4"]},
            {"document_id": "occ-e", "text": "The host says the market is growing.", "metadata": {"episode_uid": "episode-1", "speaker": "Host", "episode_date": "2025-01-01"}, "episode_uid": "episode-1", "node_type": "leaf", "source_span_ids": ["span-1"], "alias_target_id": "occ-a", "storage_status": "suppressed"},
        ]
        self.embedding_inputs = {"occ-a": "The host says the market is growing.", "occ-b": "The host says the market is growing.", "occ-c": "The host says the market may be growing.", "occ-d": "The host says the market is not growing."}
        self.vectors = {"occ-a": [1.0, 0.0], "occ-c": [0.8, 0.2], "occ-d": [-1.0, 0.0]}

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, mode: str) -> Path:
        path = self.root / mode
        write_bundle(path, scope=self.scope, occurrences=self.rows, vectors=self.vectors, embedding_inputs=self.embedding_inputs, storage_mode=mode, coverage={"judge_status": "disabled"})
        self.assertTrue(validate_bundle(path)["valid"])
        return path

    def _base_export(self) -> Path:
        export = self.root / "base-export"
        export.mkdir()
        release = {
            "release_contract_version": "chroma-export-release-v1",
            "release_id": self.scope.base_release_id,
            "upstream_release_id": "upstream-1",
            "partition_id": self.scope.partition_id,
            "corpus_id": self.scope.corpus_id,
            "representation_id": self.scope.representation_id,
        }
        (export / "release.json").write_text(json.dumps(release), encoding="utf-8")
        rows = []
        for item_id in ("occ-a", "occ-b"):
            metadata = {"stable_document_id": item_id, "node_id": item_id, "node_type": "leaf", "speaker": "Host", "source_span_id": "span"}
            rows.append(DedupInput(item_id, item_id, "Repeated text", metadata, self.scope.partition_id, self.scope.corpus_id, f"{self.scope.partition_id}:episode-1", "cache-1", embedding_text("Repeated text", metadata), "leaf", ("span",)))
        write_dedup_artifacts(export, build_exact_plan(rows, {"profile": "safe"}), release)
        (export / "vectors.json").write_text(json.dumps({"vectors": {"occ-a": [1.0, 0.0], "occ-b": [1.0, 0.0], "occ-c": [0.8, 0.2], "occ-d": [-1.0, 0.0], "occ-e": [1.0, 0.0]}}), encoding="utf-8")
        return export

    def test_full_and_shared_bundles_preserve_occurrences_and_filter_before_query(self):
        full = self._write("full")
        compact = self._write("shared_input")
        full_vectors = (full / "vectors.json").read_text(encoding="utf-8")
        compact_vectors = (compact / "vectors" / "vectors.json").read_text(encoding="utf-8")
        self.assertGreater(len(full_vectors), 0)
        self.assertGreater(len(compact_vectors), 0)
        self.assertEqual(["occ-a", "occ-b", "occ-e"], eligible_occurrences(compact, {"speakers": ["Host"], "episodes": ["episode-1", "episode-2"]}))
        self.assertEqual(["occ-a"], representatives_for(compact, ["occ-b"]))
        hits = query_representatives(compact, [1.0, 0.0], ["occ-a"], 2)
        hydrated = hydrate_occurrences(compact, hits, ["occ-b"], "preserve_occurrences")
        self.assertEqual(["occ-b"], [row["id"] for row in hydrated["hits"]])
        self.assertEqual(["occ-b"], hydrated["related_occurrence_ids"]["occ-a"])
        paged = hydrate_occurrences(compact, hits, ["occ-a", "occ-b", "occ-e"], "preserve_occurrences", occurrence_limit=1)
        self.assertEqual(["occ-a"], [row["id"] for row in paged["hits"]])
        self.assertEqual(3, paged["related_occurrence_counts"]["occ-a"])
        self.assertEqual(1, paged["next_occurrence_offsets"]["occ-a"])
        selected = select_evidence(hits, {"occ-a": [1.0, 0.0]}, top_k=1, mode="semantic_mmr")
        self.assertEqual(("occ-a",), selected.selected_ids)

    def test_evidence_bytes_are_stable_and_corruption_is_rejected(self):
        bundle = self._write("shared_input")
        evidence = bundle / "evidence.jsonl"
        original_bytes = evidence.read_bytes()
        before = hashlib.sha256(original_bytes).hexdigest()
        eligible_occurrences(bundle, {})
        self.assertEqual(before, hashlib.sha256(evidence.read_bytes()).hexdigest())
        (bundle / "evidence.jsonl").write_text(evidence.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(RedundancyArtifactError):
            validate_bundle(bundle)
        evidence.write_bytes(original_bytes)
        self.assertTrue(validate_bundle(bundle)["valid"])
        index = bundle / "occurrences.sqlite3"
        original_index = index.read_bytes()
        connection = sqlite3.connect(index)
        connection.execute("UPDATE occurrences SET text='tampered' WHERE document_id='occ-a'")
        connection.commit()
        connection.close()
        with self.assertRaises(RedundancyArtifactError):
            validate_bundle(bundle)
        index.write_bytes(original_index)
        self.assertTrue(validate_bundle(bundle)["valid"])

    def test_shared_input_reconstructs_only_hash_verified_inputs(self):
        rows = [dict(row) for row in self.rows]
        for row in rows[:4]:
            value = row["text"].strip()
            row["embedding_input_hash"] = "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
        bundle = self.root / "reconstructed"
        write_bundle(bundle, scope=self.scope, occurrences=rows, vectors=self.vectors, storage_mode="shared_input", input_spec={"contextualization": "none"}, coverage={"judge_status": "disabled"})
        manifest = validate_bundle(bundle)["manifest"]
        vector_payload = json.loads((bundle / "vectors" / "vectors.json").read_text(encoding="utf-8"))
        self.assertEqual({"occ-a", "occ-c", "occ-d"}, set(vector_payload["vectors"]))
        self.assertEqual("shared_input", manifest["storage_mode"])

    def test_judge_model_identity_changes_bundle_id(self):
        first = self.root / "model-a"
        second = self.root / "model-b"
        write_bundle(first, scope=self.scope, occurrences=self.rows, vectors=self.vectors, embedding_inputs=self.embedding_inputs, storage_mode="full", coverage={"judge_status": "disabled"}, judgment_status={"model": "model-a", "model_artifact_id": "artifact-a"})
        write_bundle(second, scope=self.scope, occurrences=self.rows, vectors=self.vectors, embedding_inputs=self.embedding_inputs, storage_mode="full", coverage={"judge_status": "disabled"}, judgment_status={"model": "model-b", "model_artifact_id": "artifact-b"})
        self.assertNotEqual(validate_bundle(first)["manifest"]["bundle_id"], validate_bundle(second)["manifest"]["bundle_id"])

    def test_bundle_storage_mode_cannot_conflict_with_policy(self):
        with self.assertRaises(RedundancyArtifactError):
            write_bundle(self.root / "conflicting-storage", scope=self.scope, occurrences=self.rows, policy={"vector_storage": "full"}, storage_mode="shared_input", coverage={"judge_status": "disabled"})

    def test_bundle_creation_and_publication_recheck_base_hashes(self):
        base = self._base_export()
        private = self.root / "private-with-base"
        write_bundle(private, scope=self.scope, occurrences=self.rows, storage_mode="full", base_export=base, coverage={"judge_status": "disabled"})
        self.assertTrue(validate_bundle(private, base_export=base)["valid"])
        published_root = self.root / "published"
        self.assertEqual("published", publish_bundle(private, published_root, base_export=base)["status"])
        release_path = base / "release.json"
        release_path.write_text(release_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(RedundancyArtifactError):
            publish_bundle(private, self.root / "published-after-change", base_export=base)

    def test_verified_input_strings_are_compared_after_hashing(self):
        rows = [
            {"document_id": "left", "text": "left", "embedding_input_hash": "sha256:collision"},
            {"document_id": "right", "text": "right", "embedding_input_hash": "sha256:collision"},
        ]
        mapping = build_representative_map(
            rows,
            embedding_inputs={"left": "left", "right": "right"},
            scope=self.scope,
        )
        self.assertEqual({"left", "right"}, {row["representative_id"] for row in mapping})

    def test_shared_input_never_crosses_partition_or_representation_scope(self):
        rows = [
            {"document_id": "p1", "text": "same", "partition_id": "p1", "corpus_id": "c", "representation_id": "r", "metadata": {}},
            {"document_id": "p2", "text": "same", "partition_id": "p2", "corpus_id": "c", "representation_id": "r", "metadata": {}},
            {"document_id": "r2", "text": "same", "partition_id": "p1", "corpus_id": "c", "representation_id": "other", "metadata": {}},
        ]
        mapping = build_representative_map(rows, embedding_inputs={"p1": "same", "p2": "same", "r2": "same"})
        by_id = {row["document_id"]: row["representative_id"] for row in mapping}
        self.assertEqual({"p1", "p2", "r2"}, set(by_id.values()))

    def test_compact_chroma_backend_is_queryable_in_batches(self):
        class FakeCollection:
            def __init__(self, vectors):
                self.vectors = vectors
                self.metadata = {}
                self.row_metadatas = {}

            def add(self, *, ids, embeddings, metadatas):
                self.vectors.update({str(identifier): list(vector) for identifier, vector in zip(ids, embeddings)})
                self.row_metadatas.update({str(identifier): dict(metadata) for identifier, metadata in zip(ids, metadatas)})

            def get(self, include=None):
                ids = sorted(self.vectors)
                return {"ids": ids, "embeddings": [self.vectors[item] for item in ids], "metadatas": [self.row_metadatas[item] for item in ids]}

            def query(self, *, query_embeddings, n_results, where, include):
                allowed = set(where["representative_id"]["$in"])
                query = query_embeddings[0]
                scored = []
                for identifier in sorted(allowed & set(self.vectors)):
                    vector = self.vectors[identifier]
                    score = sum(left * right for left, right in zip(query, vector)) / ((sum(value * value for value in query) ** 0.5) * (sum(value * value for value in vector) ** 0.5))
                    scored.append((1.0 - score, identifier))
                scored.sort()
                return {"ids": [[identifier for _, identifier in scored[:n_results]]], "distances": [[distance for distance, _ in scored[:n_results]]]}

        class FakeClient:
            collections = {}

            def __init__(self, path):
                self.path = str(path)
                self.collections.setdefault(self.path, {})

            def delete_collection(self, name):
                if name not in self.collections[self.path]:
                    raise KeyError(name)
                del self.collections[self.path][name]

            def create_collection(self, *, name, metadata, embedding_function=None):
                collection = FakeCollection({})
                collection.metadata = dict(metadata)
                self.collections[self.path][name] = collection
                return collection

            def get_collection(self, *, name):
                return self.collections[self.path][name]

        fake_chromadb = types.SimpleNamespace(PersistentClient=FakeClient)
        bundle = self.root / "fake-chroma"
        with patch.dict(sys.modules, {"chromadb": fake_chromadb}):
            write_bundle(bundle, scope=self.scope, occurrences=self.rows, vectors=self.vectors, embedding_inputs=self.embedding_inputs, storage_mode="shared_input", coverage={"judge_status": "disabled"})
            self.assertEqual("chroma", validate_bundle(bundle)["manifest"]["vector_backend"])
            hits = query_representatives(bundle, [1.0, 0.0], ["occ-a", "occ-c", "occ-d"], 2, batch_size=2)
        self.assertEqual(["occ-a", "occ-c"], [row["id"] for row in hits])


if __name__ == "__main__":
    unittest.main()
