import sqlite3
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from chroma_db_import.config import ImportConfig
from chroma_db_import.managed import ContextIdentity, ManagedCatalog, discover, managed_paths, run_managed_import
from tests.test_managed_contexts import write_release


class ManagedDedupTests(unittest.TestCase):
    def test_schema_v1_profiles_migrate_once_to_explicit_off(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp" / "dedup") as directory:
            path = Path(directory) / "catalog.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE catalog_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE contexts(
                    partition_id TEXT PRIMARY KEY, corpus_id TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL, context_type TEXT NOT NULL,
                    workflow_profile TEXT NOT NULL, partition_config_fingerprint TEXT NOT NULL DEFAULT '',
                    producer_status TEXT NOT NULL DEFAULT 'active', local_status TEXT NOT NULL DEFAULT 'active',
                    legacy INTEGER NOT NULL DEFAULT 0, legacy_source TEXT, source_root TEXT,
                    source_manifest TEXT, local_alias TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE import_profiles(
                    partition_id TEXT PRIMARY KEY, profile_json TEXT NOT NULL,
                    profile_fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                INSERT INTO catalog_meta VALUES('schema_version', '1');
                INSERT INTO contexts VALUES('legacy-context', 'legacy-corpus', 'Legacy', 'custom', 'legacy', '', 'active', 'active', 0, NULL, NULL, NULL, '', '2026-01-01', '2026-01-01');
                INSERT INTO import_profiles VALUES('legacy-context', '{"embedding_model":"old-model"}', 'old-profile-fingerprint', '2026-01-01');
                """
            )
            connection.commit()
            connection.close()

            with ManagedCatalog(path) as catalog:
                profile = catalog.profile("legacy-context")
                self.assertEqual("off", profile["profile"]["dedup_policy"]["profile"])
                revisions = catalog.connection.execute(
                    "SELECT profile_fingerprint, profile_json FROM import_profile_revisions WHERE partition_id='legacy-context' ORDER BY created_at"
                ).fetchall()
                self.assertEqual(2, len(revisions))
                self.assertEqual("old-profile-fingerprint", revisions[0][0])
                self.assertNotIn("dedup_policy", json.loads(revisions[0][1]))
                self.assertEqual(2, int(catalog.get_setting("schema_version")))

            with ManagedCatalog(path) as catalog:
                self.assertEqual(2, catalog.connection.execute(
                    "SELECT COUNT(*) FROM import_profile_revisions WHERE partition_id='legacy-context'"
                ).fetchone()[0])

    def test_new_profiles_are_safe_and_revisions_are_append_only(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp" / "dedup") as directory:
            root = Path(directory)
            with ManagedCatalog(root / "catalog.sqlite3") as catalog:
                identity = ContextIdentity("context", "corpus", "Context", "podcast", "podcast")
                catalog.upsert_context(identity)
                self.assertEqual("safe", catalog.profile("context")["profile"]["dedup_policy"]["profile"])
                catalog.save_profile("context", {"dedup_policy": {"profile": "audit"}, "embedding_model": "model"})
                self.assertEqual("audit", catalog.profile("context")["profile"]["dedup_policy"]["profile"])
                revisions = catalog.connection.execute("SELECT COUNT(*) FROM import_profile_revisions WHERE partition_id='context'").fetchone()[0]
                self.assertEqual(2, revisions)

    def test_display_name_does_not_change_storage_identity(self):
        one = managed_paths(Path("exports"), "context", "release")
        two = managed_paths(Path("exports"), "context", "release")
        self.assertEqual(one["export_root"], two["export_root"])

    def test_managed_safe_export_round_trips_and_reuses(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp" / "dedup") as directory:
            root = Path(directory)
            cache_dir = root / "processed_data"
            cache_dir.mkdir()
            cache = cache_dir / "episode.processed_documents.json"
            cache.write_text(json.dumps({
                "source_fingerprint": "cache-one",
                "partition_id": "p",
                "corpus_id": "p",
                "episode_uid": "p:episode-01",
                "episode_id": "episode-01",
                "documents": [
                    {"page_content": "text", "metadata": {"stable_document_id": "a", "node_id": "a", "node_type": "leaf_chunk", "source": "episode.json", "source_type": "json_transcript", "episode_id": "episode-01", "episode_uid": "p:episode-01", "episode_title": "E", "speaker_scope": "single", "source_span_id": "s"}},
                    {"page_content": "thesis", "metadata": {"stable_document_id": "t", "node_id": "t", "node_type": "episode_thesis", "source": "episode.json", "source_type": "json_transcript", "episode_id": "episode-01", "episode_uid": "p:episode-01", "episode_title": "E", "speaker_scope": "mixed", "source_span_id": "t"}},
                ],
            }), encoding="utf-8")
            write_release(root, "p", "p", "upstream", "cache-one")
            with ManagedCatalog(root / "catalog.sqlite3") as catalog:
                discover(catalog, [root])
                calls = []
                def fake_run(config, _project_dir, _one_file, *, selected_files=None, write_snapshot=True, dedup_plan=None):
                    calls.append(dedup_plan.plan_fingerprint if dedup_plan else None)
                    export = Path(config.persist_dir)
                    export.mkdir(parents=True, exist_ok=True)
                    (export / "chroma.sqlite3").write_bytes(b"fixture")
                    (export / "import_manifest.json").write_text(json.dumps({"embedding_model": "fixture", "embedding_dimension": 3, "representation_id": "rep", "document_counts": {"document_count": 1}, "source_files": []}), encoding="utf-8")
                with patch("chroma_db_import.cli.run_import", fake_run):
                    first = run_managed_import(ImportConfig(), root, catalog, "p", output_root=root / "exports")
                    second = run_managed_import(ImportConfig(), root, catalog, "p", output_root=root / "exports")
            self.assertEqual("completed", first["status"])
            self.assertEqual("reused", second["status"])
            self.assertEqual(1, len(calls))
            release = json.loads((Path(first["export"]) / "release.json").read_text(encoding="utf-8"))
            self.assertEqual("chroma-export-release-v2", release["release_contract_version"])


if __name__ == "__main__":
    unittest.main()
