import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from chroma_db_import.podcast_rag_adapter import PodcastRagSourceAdapter, UPSTREAM_RELEASE_CONTRACT
from chroma_db_import.representation import QWEN3_MODEL


PARTITION_ID = "partition-one"


def write_source(root: Path, *, statuses: list[str] | None = None, cache_count: int = 2) -> Path:
    partition_root = root / "partitions" / PARTITION_ID
    partition_root.mkdir(parents=True)
    (partition_root / "partition.json").write_text(
        json.dumps(
            {
                "contract_version": "podcast-rag-partition-1.0",
                "partition": {
                    "partition_id": PARTITION_ID,
                    "corpus_id": PARTITION_ID,
                    "display_name": "Podcast One",
                    "context_type": "podcast",
                    "workflow_profile": "podcast",
                    "config_fingerprint": "sha256:" + "1" * 64,
                },
            }
        ),
        encoding="utf-8",
    )
    package = partition_root / "handoff_inbox" / "handoff-one" / "episodes"
    package.mkdir(parents=True)
    statuses = statuses or ["completed"] * cache_count
    state_files = {}
    for index, status in enumerate(statuses, start=1):
        episode_id = f"episode-{index:02d}"
        episode_path = package / episode_id / "reviewed.json"
        episode_path.parent.mkdir(parents=True, exist_ok=True)
        episode_path.write_text(json.dumps({"episode_id": episode_id}), encoding="utf-8")
        cache_path = partition_root / "processed_data" / f"cache-{index:02d}.processed_documents.json"
        if status == "completed":
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "schema_version": "2.1",
                        "source_fingerprint": f"fingerprint-{index}",
                        "partition_id": PARTITION_ID,
                        "corpus_id": PARTITION_ID,
                        "partition_display_name": "Podcast One",
                        "context_type": "podcast",
                        "workflow_profile": "podcast",
                        "episode_uid": f"{PARTITION_ID}:{episode_id}",
                        "embedding_model": QWEN3_MODEL,
                        "documents": [],
                    }
                ),
                encoding="utf-8",
            )
        state_files[f"state-{index}"] = {
            "episode_id": episode_id,
            "status": status,
            "handoff_id": "handoff-one",
            "cache_path": str(cache_path),
        }
    state_dir = partition_root / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "podcast_rag_state.json").write_text(json.dumps({"version": 1, "files": state_files}), encoding="utf-8")
    return partition_root


class PodcastRagAdapterTests(unittest.TestCase):
    def test_inspect_prefers_the_producer_active_release_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_source(root)
            adapter = PodcastRagSourceAdapter(root, PARTITION_ID)
            with patch.object(adapter, "_active_release", return_value={"release_id": "release-active"}):
                status = adapter.inspect()
            self.assertEqual("release-active", status.latest_release_id)

    def test_inspect_reports_current_handoff_and_pending_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_source(root, statuses=["completed", "interrupted"])
            status = PodcastRagSourceAdapter(root, PARTITION_ID).inspect()
            self.assertEqual(2, status.declared_episodes)
            self.assertEqual(1, status.completed)
            self.assertEqual(0, status.pending)
            self.assertEqual(1, status.interrupted)
            self.assertFalse(status.ready_to_publish)

    def test_publish_requires_an_existing_producer_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_source(root)
            adapter = PodcastRagSourceAdapter(root, PARTITION_ID)
            with self.assertRaisesRegex(Exception, "Process / resume all pending work"):
                adapter.publish_release()

    def test_existing_matching_release_is_reused_without_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partition_root = write_source(root)
            release_path = partition_root / "releases" / "release-existing.release.json"
            release_path.parent.mkdir(parents=True, exist_ok=True)
            release_path.write_text(
                json.dumps(
                    {
                        "release_contract_version": UPSTREAM_RELEASE_CONTRACT,
                        "release_id": "release-existing",
                        "created_at": "2026-09-14T00:00:00+00:00",
                        "partition_id": PARTITION_ID,
                        "corpus_id": PARTITION_ID,
                        "partition_display_name": "Podcast One",
                        "context_type": "podcast",
                        "workflow_profile": "podcast",
                        "handoff_ids": ["handoff-one"],
                        "embedding_model": QWEN3_MODEL,
                        "cache_schema_version": "2.1",
                        "representation_profile": "baseline-v1",
                        "episode_ids": ["episode-01", "episode-02"],
                        "episode_uids": [f"{PARTITION_ID}:episode-01", f"{PARTITION_ID}:episode-02"],
                        "processed_cache_fingerprints": ["fingerprint-1", "fingerprint-2"],
                        "processed_cache_artifacts": [
                            {
                                "episode_id": f"episode-{index:02d}",
                                "episode_uid": f"{PARTITION_ID}:episode-{index:02d}",
                                "handoff_id": "handoff-one",
                                "relative_path": f"processed_data/cache-{index:02d}.processed_documents.json",
                                "cache_fingerprint": f"fingerprint-{index}",
                                "content_sha256": hashlib.sha256((partition_root / "processed_data" / f"cache-{index:02d}.processed_documents.json").read_bytes()).hexdigest(),
                                "validation": {"status": "passed", "counts": {}, "warnings": [], "errors": []},
                            }
                            for index in (1, 2)
                        ],
                        "validation_evidence": {
                            "status": "passed",
                            "validator": "test",
                            "validator_version": "test",
                            "evidence_closure": True,
                            "cache_count": 2,
                            "episode_count": 2,
                            "document_count": 0,
                            "counts": {},
                            "warnings": [],
                            "errors": [],
                        },
                        "release_identity_fingerprint": "sha256:" + "3" * 64,
                    }
                ),
                encoding="utf-8",
            )
            adapter = PodcastRagSourceAdapter(root, PARTITION_ID)
            result = adapter.publish_release()
            self.assertTrue(result["reused"])
            self.assertEqual("release-existing", result["release_id"])

    def test_upstream_embedding_metadata_does_not_block_source_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partition_root = write_source(root)
            cache_path = partition_root / "processed_data" / "cache-01.processed_documents.json"
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            payload["embedding_model"] = "BAAI/bge-large-en-v1.5"
            cache_path.write_text(json.dumps(payload), encoding="utf-8")

            status = PodcastRagSourceAdapter(root, PARTITION_ID).inspect()

            self.assertEqual(2, status.completed)
            self.assertEqual(0, status.pending)
            self.assertTrue(status.ready_to_publish)


if __name__ == "__main__":
    unittest.main()
