import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chroma_db_import.config import ImportConfig
from chroma_db_import.importer import representation_spec
from chroma_db_import.representation import QWEN3_MODEL, QWEN3_MODEL_REVISION
from chroma_db_import.managed import (
    ManagedCatalog,
    ManagedContextError,
    discover,
    derive_downstream_release_id,
    managed_paths,
    resolve_release_cache_files,
    validate_handoff_manifest,
)


def write_release(root: Path, partition_id: str, corpus_id: str, release_id: str, cache_fingerprint: str) -> Path:
    release_dir = root / "partitions" / partition_id / "releases"
    release_dir.mkdir(parents=True, exist_ok=True)
    path = release_dir / f"{release_id}.release.json"
    path.write_text(
        json.dumps(
            {
                "release_contract_version": "podcast-rag-corpus-release-v1",
                "release_id": release_id,
                "partition_id": partition_id,
                "corpus_id": corpus_id,
                "partition_display_name": partition_id.title(),
                "context_type": "podcast",
                "workflow_profile": "podcast",
                "handoff_ids": [f"handoff-{partition_id}"],
                "episode_uids": [f"{partition_id}:episode-01"],
                "cache_schema_version": "2.1",
                "embedding_model": QWEN3_MODEL,
                "embedding_model_revision": QWEN3_MODEL_REVISION,
                "embedding_dimension": 2560,
                "processed_cache_fingerprints": [cache_fingerprint],
            }
        ),
        encoding="utf-8",
    )
    return path


def write_handoff(root: Path, *, partition_id: str = "podcast-one", corpus_id: str = "podcast-one", transcript_path: str = "episodes/episode-01.json", duplicate: bool = False) -> Path:
    transcript = {
        "contract_version": "episode-contract-v2",
        "episode_id": "episode-01",
        "metadata": {"partition_id": partition_id, "corpus_id": corpus_id},
        "segments": [],
    }
    package_root = root / "handoff"
    transcript_file = package_root / transcript_path
    if not Path(transcript_path).is_absolute() and ".." not in Path(transcript_path).parts:
        transcript_file.parent.mkdir(parents=True, exist_ok=True)
        transcript_file.write_text(json.dumps(transcript, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    artifact_hash = "sha256:" + hashlib.sha256(transcript_file.read_bytes()).hexdigest() if transcript_file.is_file() else "sha256:" + "0" * 64
    episode = {
        "episode_id": "episode-01",
        "episode_uid": f"{partition_id}:episode-01",
        "selected_transcript": {
            "path": transcript_path,
            "artifact_sha256": artifact_hash,
            "canonical_payload_sha256": "sha256:" + hashlib.sha256(json.dumps(transcript, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        },
        "source_audio": {"fingerprint": "sha256:" + "1" * 64},
        "stable": True,
    }
    episodes = [episode, dict(episode)] if duplicate else [episode]
    payload = {
        "contract_version": "podcast-rag-transcription-handoff-v1",
        "handoff_id": "handoff-01",
        "created_at": "2026-09-05T00:00:00Z",
        "producer": {"name": "fixture", "contract_version": "episode-contract-v2"},
        "partition": {
            "partition_id": partition_id,
            "corpus_id": corpus_id,
            "display_name": "Podcast One",
            "context_type": "podcast",
            "workflow_profile": "podcast",
            "config_fingerprint": "sha256:" + "2" * 64,
        },
        "episodes": episodes,
        "integrity": {"algorithm": "sha256", "manifest_hash_excludes_field": "integrity.manifest_sha256"},
    }
    manifest_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["integrity"]["manifest_sha256"] = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    manifest = package_root / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest


class ManagedContextTests(unittest.TestCase):
    def test_discovery_registers_release_less_partition_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partition_root = root / "partitions" / "podcast-one"
            partition_root.mkdir(parents=True)
            (partition_root / "partition.json").write_text(
                json.dumps(
                    {
                        "contract_version": "podcast-rag-partition-1.0",
                        "partition": {
                            "partition_id": "podcast-one",
                            "corpus_id": "podcast-one",
                            "display_name": "Podcast One",
                            "context_type": "podcast",
                            "workflow_profile": "podcast",
                            "config_fingerprint": "sha256:" + "1" * 64,
                            "status": "active",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with ManagedCatalog(root / "catalog.sqlite3") as catalog:
                report = discover(catalog, [root])
                self.assertFalse(report["invalid"])
                self.assertEqual(1, len(report["contexts"]))
                self.assertEqual([], catalog.releases("podcast-one"))

    def test_valid_handoff_discovery_checks_identity_and_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_handoff(root)
            with ManagedCatalog(root / "catalog.sqlite3") as catalog:
                report = discover(catalog, [root])
                self.assertFalse(report["invalid"])
                self.assertEqual("podcast-one", catalog.context("podcast-one")["partition_id"])
            self.assertEqual("manifest.json", manifest.name)

    def test_invalid_handoff_and_non_default_mapping_are_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_handoff(root, transcript_path="missing/episode.json")
            (root / "handoff" / "missing" / "episode.json").unlink()
            release = write_release(root, "mapped", "different-corpus", "release-01", "cache-missing")
            with ManagedCatalog(root / "catalog.sqlite3") as catalog:
                report = discover(catalog, [root])
                self.assertGreaterEqual(len(report["invalid"]), 2)
                self.assertIsNone(catalog.context("mapped"))
            self.assertTrue(release.exists())

    def test_duplicate_handoff_episode_uids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_handoff(root, duplicate=True)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ManagedContextError, "duplicate episode identity"):
                validate_handoff_manifest(payload, manifest.parent)

    def test_discovery_auto_registers_partition_and_release_without_path_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "partitions" / "podcast-one" / "processed_data"
            cache_dir.mkdir(parents=True)
            (cache_dir / "same-name.processed_documents.json").write_text(
                json.dumps(
                    {
                        "source_fingerprint": "cache-one",
                        "partition_id": "podcast-one",
                        "corpus_id": "corpus-one",
                        "episode_uid": "podcast-one:episode-01",
                        "partition_display_name": "Podcast One",
                        "context_type": "podcast",
                        "workflow_profile": "podcast",
                        "embedding_model": QWEN3_MODEL,
                        "documents": [],
                    }
                ),
                encoding="utf-8",
            )
            backup_dir = root / "partitions" / "podcast-one" / "state" / "reprocess_backups" / "backup-01"
            backup_dir.mkdir(parents=True)
            shutil.copy2(cache_dir / "same-name.processed_documents.json", backup_dir / "same-name.processed_documents.json")
            release_path = write_release(root, "podcast-one", "corpus-one", "release-01", "cache-one")
            registry_dir = root / "partitions"
            (registry_dir / "registry.json").write_text(
                json.dumps({"partitions": [{"partition_id": "podcast-one", "corpus_id": "corpus-one"}]}),
                encoding="utf-8",
            )
            (root / "podcast-two").mkdir()
            (root / "podcast-two" / "same-name.processed_documents.json").write_text("{}", encoding="utf-8")

            with ManagedCatalog(root / "catalog.sqlite3") as catalog:
                report = discover(catalog, [root])
                self.assertFalse(report["invalid"])
                context = catalog.context("podcast-one")
                self.assertEqual("corpus-one", context["corpus_id"])
                self.assertEqual("release-01", catalog.releases("podcast-one")[0]["upstream_release_id"])
                caches = resolve_release_cache_files(catalog.releases("podcast-one")[0], catalog.source_roots())
                self.assertEqual("same-name.processed_documents.json", caches[0].name)
                self.assertIsNone(catalog.context("podcast-two"))
                self.assertEqual(release_path.name, catalog.releases("podcast-one")[0]["source_path"].split("\\")[-1])

    def test_mixed_cache_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "processed_data"
            cache_dir.mkdir()
            for name, partition in (("one", "one"), ("two", "two")):
                (cache_dir / f"{name}.processed_documents.json").write_text(
                    json.dumps(
                        {
                            "source_fingerprint": f"cache-{name}",
                            "partition_id": partition,
                            "corpus_id": partition,
                            "episode_uid": f"{partition}:episode",
                            "partition_display_name": partition,
                            "context_type": "custom",
                            "workflow_profile": "custom",
                            "embedding_model": QWEN3_MODEL,
                            "documents": [],
                        }
                    ),
                    encoding="utf-8",
                )
            release = {
                "source_path": str(root / "release.json"),
                "payload": {
                    "release_contract_version": "podcast-rag-corpus-release-v1",
                    "release_id": "release-01",
                    "partition_id": "one",
                    "corpus_id": "one",
                    "partition_display_name": "One",
                    "context_type": "custom",
                    "workflow_profile": "custom",
                    "handoff_ids": ["h"],
                    "episode_uids": ["one:episode", "two:episode"],
                    "processed_cache_fingerprints": ["cache-one", "cache-two"],
                },
            }
            (root / "release.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ManagedContextError, "partition/corpus"):
                resolve_release_cache_files(release, [root])

    def test_destination_and_release_ids_are_stable_and_partition_specific(self):
        self.assertNotEqual(
            derive_downstream_release_id("release-01", "sha256:one"),
            derive_downstream_release_id("release-01", "sha256:two"),
        )
        one = managed_paths(Path("exports"), "podcast-one", "release-01")
        two = managed_paths(Path("exports"), "podcast-two", "release-01")
        self.assertNotEqual(one["export_root"], two["export_root"])

    def test_managed_import_promotes_portable_identity_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "processed_data"
            cache_dir.mkdir()
            cache = cache_dir / "episode.processed_documents.json"
            cache.write_text(
                json.dumps(
                    {
                        "source_fingerprint": "cache-one",
                        "partition_id": "podcast-one",
                        "corpus_id": "podcast-one",
                        "partition_display_name": "Podcast One",
                        "context_type": "podcast",
                        "workflow_profile": "podcast",
                        "embedding_model": QWEN3_MODEL,
                        "episode_id": "episode-01",
                        "documents": [
                            {"page_content": "evidence", "metadata": {"node_id": "leaf", "node_type": "leaf_chunk", "source": "episode.json", "source_type": "json_transcript", "episode_id": "episode-01", "episode_uid": "podcast-one:episode-01", "episode_title": "Episode", "partition_id": "podcast-one", "corpus_id": "podcast-one", "speaker_scope": "single", "speaker": "Host"}},
                            {"page_content": "thesis", "metadata": {"node_id": "thesis", "node_type": "episode_thesis", "source": "episode.json", "source_type": "json_transcript", "episode_id": "episode-01", "episode_uid": "podcast-one:episode-01", "episode_title": "Episode", "partition_id": "podcast-one", "corpus_id": "podcast-one", "speaker_scope": "mixed", "child_ids": ["leaf"]}},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            release_path = write_release(root, "podcast-one", "podcast-one", "release-01", "cache-one")

            with ManagedCatalog(root / "catalog.sqlite3") as catalog:
                discover(catalog, [root])
                calls = []

                def fake_run(config, _project_dir, _one_file, *, selected_files=None, write_snapshot=True, dedup_plan=None):
                    calls.append(list(selected_files or []))
                    export = Path(config.persist_dir)
                    export.mkdir(parents=True, exist_ok=True)
                    (export / "chroma.sqlite3").write_bytes(b"fixture")
                    representation = representation_spec(config).as_dict()
                    (export / "import_manifest.json").write_text(
                        json.dumps(
                            {
                                "config": {"persist_dir": str(export), "processed_data_dir": str(cache_dir)},
                                "embedding_model": representation["model_id"],
                                "embedding_dimension": representation["dimension"],
                                "representation_id": representation["representation_id"],
                                "representation": representation,
                                "document_counts": {"document_count": 0},
                                "source_files": [{"path": str(cache), "fingerprint": "cache-one"}],
                            }
                        ),
                        encoding="utf-8",
                    )

                with patch("chroma_db_import.cli.run_import", fake_run):
                    from chroma_db_import.managed import run_managed_import

                    result = run_managed_import(ImportConfig(), root, catalog, "podcast-one", output_root=root / "exports")
                    reused = run_managed_import(ImportConfig(), root, catalog, "podcast-one", output_root=root / "exports")

            self.assertEqual("completed", result["status"])
            self.assertEqual("reused", reused["status"])
            self.assertEqual(1, len(calls))
            export = Path(result["export"])
            self.assertTrue((export / "chroma.sqlite3").exists())
            self.assertEqual("podcast-one", json.loads((export / "podcast.json").read_text(encoding="utf-8"))["corpus_id"])
            release = json.loads((export / "release.json").read_text(encoding="utf-8"))
            self.assertEqual("chroma-export-release-v2", release["release_contract_version"])
            manifest_text = (export / "import_manifest.json").read_text(encoding="utf-8")
            self.assertNotIn(str(root), manifest_text)
            self.assertEqual(release_path.name, "release-01.release.json")


if __name__ == "__main__":
    unittest.main()
