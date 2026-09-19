"""Contract-driven context discovery and isolated Chroma exports.

The producer's handoff/release manifests own partition identity.  This module
keeps only local operational state in SQLite and derives every Chroma target
from a validated upstream identity.
"""

from __future__ import annotations

import datetime as dt
import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from chroma_db_import.config import ImportConfig, load_config
from chroma_db_import.contract import (
    content_fingerprint,
    coerce_speakers,
    partition_identity,
    partition_identities,
    sanitize_metadata,
    temporal_coverage_stats,
    validate_podcast_metadata,
)
from chroma_db_import.importer import cache_fingerprint, load_processed_payload, representation_spec
from chroma_db_import.deduplication import (
    DEDUP_CAPABILITY,
    build_exact_plan,
    load_managed_dedup_inputs,
    resolve_dedup_policy,
)
from chroma_db_import.dedup_artifacts import validate_dedup_artifacts, write_dedup_artifacts
from chroma_db_import.managed_lock import ManagedPartitionBusy, ManagedPartitionLock
from chroma_db_import.lock_repair import repair_partition_lock
from chroma_db_import.redundancy_chroma import close_chroma_client
from chroma_db_import.redundancy_policy import policy_fingerprint, resolve_redundancy_policy
from chroma_db_import.representation import QWEN3_MODEL, QWEN3_MODEL_REVISION, QWEN3_PROFILE, resolved_collection_name


HANDOFF_CONTRACT = "podcast-rag-transcription-handoff-v1"
UPSTREAM_RELEASE_CONTRACT = "podcast-rag-corpus-release-v2"
LEGACY_UPSTREAM_RELEASE_CONTRACT = "podcast-rag-corpus-release-v1"
DOWNSTREAM_RELEASE_CONTRACT = "chroma-export-release-v1"
DOWNSTREAM_DEDUP_RELEASE_CONTRACT = "chroma-export-release-v2"
CATALOG_SCHEMA_VERSION = 3
COLLECTION_NAME = "rag_documents"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
CONTEXT_TYPES = {"podcast", "meeting", "custom"}


class ManagedContextError(ValueError):
    """Raised when a managed source cannot be safely isolated."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def producer_active_release_id(source_root: Path, partition_id: str) -> str:
    """Return the producer-selected release without scanning or changing its files."""
    pointer = source_root.expanduser().resolve() / "partitions" / partition_id / "active-release.json"
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict) or payload.get("pointer_contract_version") != "podcast-rag-active-release-v1":
        return ""
    if str(payload.get("partition_id") or "") != str(partition_id):
        return ""
    return str(payload.get("release_id") or "").strip()


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _temporal_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _safe_id(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not ID_RE.fullmatch(result):
        raise ManagedContextError(f"{label} must be a safe identifier")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _normalize_hash(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        raise ManagedContextError(f"{label} is missing")
    if text.startswith("sha256:"):
        digest = text[7:]
    else:
        digest = text
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ManagedContextError(f"{label} must be a sha256 hash")
    return "sha256:" + digest


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class ContextIdentity:
    partition_id: str
    corpus_id: str
    display_name: str
    context_type: str
    workflow_profile: str
    partition_config_fingerprint: str = ""

    @classmethod
    def from_mapping(cls, value: Any, *, strict: bool = True) -> "ContextIdentity":
        if not isinstance(value, dict):
            raise ManagedContextError("partition identity must be an object")
        nested = value.get("partition") if isinstance(value.get("partition"), dict) else value

        def pick(*names: str) -> Any:
            for name in names:
                candidate = value.get(name)
                if candidate not in (None, ""):
                    return candidate
                candidate = nested.get(name)
                if candidate not in (None, ""):
                    return candidate
            return None

        partition_id = _safe_id(pick("partition_id"), "partition_id")
        corpus_value = pick("corpus_id")
        if corpus_value in (None, "") and not strict:
            corpus_value = partition_id
        corpus_id = _safe_id(corpus_value, "corpus_id")
        display_name = str(pick("partition_display_name", "display_name") or partition_id).strip()
        context_type = str(pick("context_type") or "").strip().lower()
        workflow_profile = str(pick("workflow_profile") or "").strip()
        if not display_name:
            raise ManagedContextError("partition display name cannot be empty")
        if context_type not in CONTEXT_TYPES:
            raise ManagedContextError(f"context_type must be one of {sorted(CONTEXT_TYPES)}")
        if not workflow_profile:
            raise ManagedContextError("workflow_profile cannot be empty")
        fingerprint = str(pick("partition_config_fingerprint", "config_fingerprint") or "")
        return cls(partition_id, corpus_id, display_name, context_type, workflow_profile, fingerprint)

    def as_dict(self) -> dict[str, str]:
        return {
            "partition_id": self.partition_id,
            "corpus_id": self.corpus_id,
            "partition_display_name": self.display_name,
            "context_type": self.context_type,
            "workflow_profile": self.workflow_profile,
            "partition_config_fingerprint": self.partition_config_fingerprint,
        }


def validate_handoff_manifest(payload: Any, package_root: Path | None = None) -> ContextIdentity:
    if not isinstance(payload, dict) or payload.get("contract_version") != HANDOFF_CONTRACT:
        raise ManagedContextError("unsupported or invalid transcription handoff contract")
    for field in ("handoff_id", "created_at", "producer", "partition", "episodes", "integrity"):
        if field not in payload:
            raise ManagedContextError(f"handoff is missing {field}")
    producer = payload.get("producer")
    if not isinstance(producer, dict) or not str(producer.get("name") or "").strip() or producer.get("contract_version") not in {"episode-contract-v2", "episode-contract-v2.0"}:
        raise ManagedContextError("handoff producer contract identity is invalid")
    episodes = payload.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ManagedContextError("handoff episodes must be a non-empty array")
    identity = ContextIdentity.from_mapping(payload, strict=True)
    integrity = payload.get("integrity") if isinstance(payload.get("integrity"), dict) else {}
    if str(integrity.get("algorithm") or "").lower() != "sha256":
        raise ManagedContextError("handoff integrity must declare sha256")
    if integrity.get("manifest_hash_excludes_field") != "integrity.manifest_sha256":
        raise ManagedContextError("handoff integrity must declare the manifest hash exclusion field")
    expected_manifest_hash = _normalize_hash(integrity.get("manifest_sha256"), "handoff manifest_sha256")
    if expected_manifest_hash:
        manifest_copy = json.loads(json.dumps(payload))
        manifest_copy.setdefault("integrity", {}).pop("manifest_sha256", None)
        actual_manifest_hash = "sha256:" + hashlib.sha256(_json(manifest_copy).encode("utf-8")).hexdigest()
        if actual_manifest_hash.lower() != expected_manifest_hash.lower():
            raise ManagedContextError("handoff manifest hash mismatch")
    episode_ids: set[str] = set()
    episode_uids: set[str] = set()
    for index, episode in enumerate(episodes):
        if not isinstance(episode, dict):
            raise ManagedContextError(f"handoff episode {index} must be an object")
        episode_id = str(episode.get("episode_id") or "").strip()
        episode_uid = str(episode.get("episode_uid") or "").strip()
        expected_uid = f"{identity.partition_id}:{episode_id}"
        if not episode_id or episode_uid != expected_uid:
            raise ManagedContextError(f"handoff episode {index} has an invalid episode_uid")
        if episode_id in episode_ids or episode_uid in episode_uids:
            raise ManagedContextError(f"handoff contains duplicate episode identity: {episode_uid}")
        episode_ids.add(episode_id)
        episode_uids.add(episode_uid)
    if package_root is not None:
        package_root = package_root.resolve()
        for index, episode in enumerate(episodes):
            episode_id = str(episode.get("episode_id") or "").strip()
            selected = episode.get("selected_transcript")
            if not isinstance(selected, dict):
                raise ManagedContextError(f"handoff episode {episode_id} is missing selected_transcript")
            relative = str(selected.get("path") or "").strip()
            relative_path = Path(relative)
            candidate = (package_root / relative_path).resolve()
            try:
                candidate.relative_to(package_root)
            except ValueError:
                raise ManagedContextError(f"handoff references an unsafe or missing transcript: {relative}")
            if not relative or relative_path.is_absolute() or "\\" in relative or "." in relative_path.parts or ".." in relative_path.parts or not candidate.is_file():
                raise ManagedContextError(f"handoff references an unsafe or missing transcript: {relative}")
            expected = _normalize_hash(selected.get("artifact_sha256"), f"handoff transcript artifact_sha256: {relative}")
            if _sha256(candidate).lower() != expected:
                raise ManagedContextError(f"handoff transcript hash mismatch: {relative}")
            try:
                transcript = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ManagedContextError(f"handoff transcript is unreadable: {relative}") from exc
            if not isinstance(transcript, dict) or transcript.get("contract_version") not in {"episode-contract-v2", "episode-contract-v2.0"}:
                raise ManagedContextError(f"handoff transcript contract is invalid: {relative}")
            if str(transcript.get("episode_id") or "") != episode_id:
                raise ManagedContextError(f"handoff transcript episode_id disagrees with manifest: {relative}")
            transcript_metadata = transcript.get("metadata") if isinstance(transcript.get("metadata"), dict) else {}
            transcript_partition = str(transcript.get("partition_id") or transcript_metadata.get("partition_id") or "")
            transcript_corpus = str(transcript.get("corpus_id") or transcript_metadata.get("corpus_id") or "")
            if transcript_partition and transcript_partition != identity.partition_id or transcript_corpus and transcript_corpus != identity.corpus_id:
                raise ManagedContextError(f"handoff transcript partition/corpus disagrees with manifest: {relative}")
            _normalize_hash(selected.get("canonical_payload_sha256"), f"handoff transcript canonical_payload_sha256: {relative}")
            canonical_hash = "sha256:" + hashlib.sha256(_json(transcript).encode("utf-8")).hexdigest()
            if canonical_hash != _normalize_hash(selected.get("canonical_payload_sha256"), f"handoff transcript canonical_payload_sha256: {relative}"):
                raise ManagedContextError(f"handoff transcript canonical hash mismatch: {relative}")
            source_audio = episode.get("source_audio")
            if not isinstance(source_audio, dict) or not str(source_audio.get("fingerprint") or "").strip():
                raise ManagedContextError(f"handoff episode {episode_id} is missing source_audio fingerprint")
            if episode.get("stable") is not True:
                raise ManagedContextError(f"handoff episode {episode_id} is not stable")
    return identity


def validate_upstream_release(payload: Any) -> ContextIdentity:
    if not isinstance(payload, dict) or payload.get("release_contract_version") != UPSTREAM_RELEASE_CONTRACT:
        raise ManagedContextError("unsupported or invalid Podcast-RAG release contract")
    for field in (
        "release_id",
        "created_at",
        "partition_id",
        "corpus_id",
        "handoff_ids",
        "episode_ids",
        "episode_uids",
        "cache_schema_version",
        "representation_profile",
        "embedding_model",
        "processed_cache_fingerprints",
        "processed_cache_artifacts",
        "validation_evidence",
        "release_identity_fingerprint",
    ):
        if field not in payload:
            raise ManagedContextError(f"release is missing {field}")
    identity = ContextIdentity.from_mapping(payload, strict=True)
    if not isinstance(payload.get("handoff_ids"), list) or not isinstance(payload.get("episode_uids"), list):
        raise ManagedContextError("release handoff_ids and episode_uids must be arrays")
    episode_ids = payload.get("episode_ids")
    if not isinstance(episode_ids, list) or len(episode_ids) != len(set(str(item) for item in episode_ids)):
        raise ManagedContextError("release episode_ids must be a unique array")
    episode_uids = [str(item) for item in payload["episode_uids"]]
    if len(episode_uids) != len(set(episode_uids)):
        raise ManagedContextError("release contains duplicate episode_uids")
    if len(episode_ids) != len(episode_uids):
        raise ManagedContextError("release episode_ids and episode_uids must have equal cardinality")
    prefix = identity.partition_id + ":"
    if any(not item.startswith(prefix) for item in episode_uids):
        raise ManagedContextError("release contains an episode_uid from another partition")
    fingerprints = payload.get("processed_cache_fingerprints")
    artifacts = payload.get("processed_cache_artifacts")
    if not isinstance(fingerprints, list) or not fingerprints or len(fingerprints) != len(set(str(item) for item in fingerprints)):
        raise ManagedContextError("release processed_cache_fingerprints must be a non-empty unique array")
    if not isinstance(artifacts, list) or len(artifacts) != len(episode_uids):
        raise ManagedContextError("release processed_cache_artifacts must bind every declared episode")
    artifact_uids: set[str] = set()
    artifact_episode_ids: set[str] = set()
    artifact_fingerprints: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ManagedContextError("release cache artifacts must be objects")
        for field in ("episode_id", "episode_uid", "handoff_id", "relative_path", "cache_fingerprint", "content_sha256", "validation"):
            if field not in artifact:
                raise ManagedContextError(f"release cache artifact is missing {field}")
        artifact_uid = str(artifact["episode_uid"])
        artifact_episode_ids.add(str(artifact["episode_id"]))
        if artifact_uid in artifact_uids:
            raise ManagedContextError("release cache artifacts contain duplicate episode_uids")
        artifact_uids.add(artifact_uid)
        artifact_fingerprints.add(str(artifact["cache_fingerprint"]))
        _normalize_hash(artifact["content_sha256"], "release cache artifact content_sha256")
        relative_path = str(artifact["relative_path"])
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ManagedContextError("release cache artifact relative_path must remain inside the partition")
        validation = artifact["validation"]
        if not isinstance(validation, dict) or validation.get("status") not in {"passed", "passed_with_warnings"} or validation.get("errors"):
            raise ManagedContextError("release cache artifact validation evidence is not passing")
    if artifact_uids != set(episode_uids) or artifact_episode_ids != {str(item) for item in episode_ids} or artifact_fingerprints != {str(item) for item in fingerprints}:
        raise ManagedContextError("release cache artifacts do not bind to the declared episodes and fingerprints")
    evidence = payload.get("validation_evidence")
    if not isinstance(evidence, dict) or evidence.get("evidence_closure") is not True:
        raise ManagedContextError("release validation evidence is not closed")
    if evidence.get("status") not in {"passed", "passed_with_warnings"} or evidence.get("errors"):
        raise ManagedContextError("release validation evidence is not passing")
    if evidence.get("cache_count") != len(artifacts) or evidence.get("episode_count") != len(episode_uids):
        raise ManagedContextError("release validation evidence counts do not match the release artifacts")
    _normalize_hash(payload.get("release_identity_fingerprint"), "release_identity_fingerprint")
    capability = str(payload.get("temporal_capability") or "legacy")
    if capability not in {"certified", "partial", "legacy"}:
        raise ManagedContextError("release temporal_capability is invalid")
    coverage = payload.get("temporal_coverage")
    coverage_hash = str(payload.get("temporal_coverage_sha256") or "")
    if capability != "legacy" or coverage is not None or coverage_hash:
        if not isinstance(coverage, dict):
            raise ManagedContextError("release temporal_coverage must be an object when declared")
        expected_hash = "sha256:" + _temporal_hash(coverage)
        normalized_hash = coverage_hash if coverage_hash.startswith("sha256:") else "sha256:" + coverage_hash
        if normalized_hash != expected_hash:
            raise ManagedContextError("release temporal coverage checksum mismatch")
        if str(coverage.get("temporal_capability") or capability) != capability:
            raise ManagedContextError("release temporal capability does not match coverage")
    return identity


class ManagedCatalog:
    """SQLite-backed local catalog; portable identity remains in producer JSON."""

    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = path.expanduser().resolve()
        self.read_only = bool(read_only)
        if self.read_only:
            if not self.path.is_file():
                raise ManagedContextError(f"catalog does not exist: {self.path}")
            self.connection = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=30)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(str(self.path), timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        if not self.read_only:
            self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS catalog_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_roots (
                root TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'podcast-rag',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_scan_at TEXT
            );
            CREATE TABLE IF NOT EXISTS contexts (
                partition_id TEXT PRIMARY KEY,
                corpus_id TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                context_type TEXT NOT NULL,
                workflow_profile TEXT NOT NULL,
                partition_config_fingerprint TEXT NOT NULL DEFAULT '',
                producer_status TEXT NOT NULL DEFAULT 'active',
                local_status TEXT NOT NULL DEFAULT 'active',
                legacy INTEGER NOT NULL DEFAULT 0,
                legacy_source TEXT,
                source_root TEXT,
                source_manifest TEXT,
                local_alias TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS releases (
                partition_id TEXT NOT NULL REFERENCES contexts(partition_id),
                upstream_release_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'discovered',
                downstream_release_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                discovered_at TEXT NOT NULL,
                imported_at TEXT,
                PRIMARY KEY (partition_id, upstream_release_id)
            );
            CREATE TABLE IF NOT EXISTS import_profiles (
                partition_id TEXT PRIMARY KEY REFERENCES contexts(partition_id),
                profile_json TEXT NOT NULL,
                profile_fingerprint TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS context_settings (
                partition_id TEXT PRIMARY KEY REFERENCES contexts(partition_id),
                settings_json TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                partition_id TEXT NOT NULL REFERENCES contexts(partition_id),
                upstream_release_id TEXT NOT NULL,
                downstream_release_id TEXT NOT NULL,
                status TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS import_profile_revisions (
                partition_id TEXT NOT NULL REFERENCES contexts(partition_id),
                profile_fingerprint TEXT NOT NULL,
                profile_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (partition_id, profile_fingerprint)
            );
            CREATE TABLE IF NOT EXISTS downstream_exports (
                partition_id TEXT NOT NULL REFERENCES contexts(partition_id),
                downstream_release_id TEXT NOT NULL,
                upstream_release_id TEXT NOT NULL,
                profile_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (partition_id, downstream_release_id)
            );
            CREATE TABLE IF NOT EXISTS redundancy_profiles (
                partition_id TEXT PRIMARY KEY REFERENCES contexts(partition_id),
                policy_json TEXT NOT NULL,
                policy_fingerprint TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS redundancy_profile_revisions (
                partition_id TEXT NOT NULL REFERENCES contexts(partition_id),
                policy_fingerprint TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (partition_id, policy_fingerprint)
            );
            CREATE TABLE IF NOT EXISTS redundancy_jobs (
                job_id TEXT PRIMARY KEY,
                partition_id TEXT NOT NULL REFERENCES contexts(partition_id),
                base_release_id TEXT NOT NULL,
                policy_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            """
        )
        row = self.connection.execute("SELECT value FROM catalog_meta WHERE key='schema_version'").fetchone()
        had_schema_row = bool(row)
        current = int(row[0]) if row and str(row[0]).isdigit() else 1
        if current > CATALOG_SCHEMA_VERSION:
            raise ManagedContextError(f"catalog schema {current} is newer than supported schema {CATALOG_SCHEMA_VERSION}")
        # Tables are created idempotently above. Migration is marked only after
        # the legacy profile backfill and redundancy defaults have completed in
        # one transaction. A hand-built schema-v1 catalog from the original
        # release keeps its visible version-2 marker for old tooling while the
        # private redundancy_schema_version records that the new tables exist.
        if current < CATALOG_SCHEMA_VERSION and not self.get_setting("redundancy_schema_version"):
            self.connection.execute("BEGIN")
            try:
                legacy_contexts = self.connection.execute("SELECT partition_id FROM contexts").fetchall()
                for legacy in legacy_contexts:
                    partition_id = str(legacy[0])
                    existing_profile = self.connection.execute("SELECT * FROM import_profiles WHERE partition_id=?", (partition_id,)).fetchone()
                    if existing_profile:
                        old_profile = json.loads(existing_profile["profile_json"])
                        if "dedup_policy" in old_profile:
                            self.connection.execute(
                                "INSERT OR IGNORE INTO import_profile_revisions(partition_id, profile_fingerprint, profile_json, created_at) VALUES(?,?,?,?)",
                                (partition_id, existing_profile["profile_fingerprint"], existing_profile["profile_json"], _now()),
                            )
                            continue
                        self.connection.execute(
                            "INSERT OR IGNORE INTO import_profile_revisions(partition_id, profile_fingerprint, profile_json, created_at) VALUES(?,?,?,?)",
                            (partition_id, existing_profile["profile_fingerprint"], existing_profile["profile_json"], _now()),
                        )
                        off = dict(old_profile)
                        off["dedup_policy"] = resolve_dedup_policy(None, default_profile="off")
                        fingerprint = _fingerprint(off)
                        self.connection.execute(
                            "UPDATE import_profiles SET profile_json=?, profile_fingerprint=?, updated_at=? WHERE partition_id=?",
                            (_json(off), fingerprint, _now(), partition_id),
                        )
                        self.connection.execute(
                            "INSERT OR IGNORE INTO import_profile_revisions(partition_id, profile_fingerprint, profile_json, created_at) VALUES(?,?,?,?)",
                            (partition_id, fingerprint, _json(off), _now()),
                        )
                    else:
                        off = import_profile_payload(ImportConfig(dedup_policy={"profile": "off"}))
                        fingerprint = _fingerprint(off)
                        self.connection.execute(
                            "INSERT INTO import_profiles(partition_id, profile_json, profile_fingerprint, updated_at) VALUES(?,?,?,?)",
                            (partition_id, _json(off), fingerprint, _now()),
                        )
                        self.connection.execute(
                            "INSERT OR IGNORE INTO import_profile_revisions(partition_id, profile_fingerprint, profile_json, created_at) VALUES(?,?,?,?)",
                            (partition_id, fingerprint, _json(off), _now()),
                        )
                contexts = self.connection.execute("SELECT partition_id FROM contexts").fetchall()
                default_policy = resolve_redundancy_policy()
                default_json = _json(default_policy)
                default_fp = policy_fingerprint(default_policy)
                for context in contexts:
                    partition_id = str(context[0])
                    self.connection.execute(
                        "INSERT OR IGNORE INTO redundancy_profiles(partition_id, policy_json, policy_fingerprint, updated_at) VALUES(?,?,?,?)",
                        (partition_id, default_json, default_fp, _now()),
                    )
                    self.connection.execute(
                        "INSERT OR IGNORE INTO redundancy_profile_revisions(partition_id, policy_fingerprint, policy_json, created_at) VALUES(?,?,?,?)",
                        (partition_id, default_fp, default_json, _now()),
                    )
                visible_version = "2" if (had_schema_row and current == 1) else str(CATALOG_SCHEMA_VERSION)
                self.connection.execute("INSERT OR REPLACE INTO catalog_meta(key,value) VALUES('schema_version',?)", (visible_version,))
                self.connection.execute("INSERT OR REPLACE INTO catalog_meta(key,value) VALUES('redundancy_schema_version',?)", (str(CATALOG_SCHEMA_VERSION),))
                if visible_version == "2":
                    self.connection.execute("INSERT OR REPLACE INTO catalog_meta(key,value) VALUES('legacy_schema_compat_v1','1')")
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        else:
            if not self.get_setting("redundancy_schema_version"):
                self.connection.execute("INSERT OR REPLACE INTO catalog_meta(key,value) VALUES('redundancy_schema_version',?)", (str(CATALOG_SCHEMA_VERSION),))
            if not had_schema_row:
                self.connection.execute("INSERT OR REPLACE INTO catalog_meta(key,value) VALUES('schema_version',?)", (str(CATALOG_SCHEMA_VERSION),))
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ManagedCatalog":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.connection.execute("SELECT value FROM catalog_meta WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.connection.execute("INSERT OR REPLACE INTO catalog_meta(key, value) VALUES(?, ?)", (key, value))
        self.connection.commit()

    def _ensure_redundancy_profile(self, partition_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT policy_json, policy_fingerprint FROM redundancy_profiles WHERE partition_id=?",
            (partition_id,),
        ).fetchone()
        if row:
            policy = json.loads(row[0])
            return {**policy, "policy": policy, "policy_fingerprint": str(row[1])}
        if not self.context(partition_id):
            raise ManagedContextError(f"unknown partition: {partition_id}")
        policy = resolve_redundancy_policy()
        fingerprint = policy_fingerprint(policy)
        self.connection.execute(
            "INSERT INTO redundancy_profiles(partition_id, policy_json, policy_fingerprint, updated_at) VALUES(?,?,?,?)",
            (partition_id, _json(policy), fingerprint, _now()),
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO redundancy_profile_revisions(partition_id, policy_fingerprint, policy_json, created_at) VALUES(?,?,?,?)",
            (partition_id, fingerprint, _json(policy), _now()),
        )
        self.connection.commit()
        return {**policy, "policy": policy, "policy_fingerprint": fingerprint}

    def get_redundancy_policy(self, partition_id: str) -> dict[str, Any]:
        """Return the current immutable-policy revision for one partition."""
        return self._ensure_redundancy_profile(partition_id)

    def save_redundancy_policy(self, partition_id: str, policy: dict[str, Any]) -> str:
        if not self.context(partition_id):
            raise ManagedContextError(f"unknown partition: {partition_id}")
        if isinstance(policy.get("policy"), dict):
            policy = dict(policy["policy"])
        resolved = resolve_redundancy_policy(policy)
        fingerprint = policy_fingerprint(resolved)
        payload = _json(resolved)
        self.connection.execute("BEGIN")
        try:
            self.connection.execute(
                "INSERT OR IGNORE INTO redundancy_profile_revisions(partition_id, policy_fingerprint, policy_json, created_at) VALUES(?,?,?,?)",
                (partition_id, fingerprint, payload, _now()),
            )
            self.connection.execute(
                "INSERT OR REPLACE INTO redundancy_profiles(partition_id, policy_json, policy_fingerprint, updated_at) VALUES(?,?,?,?)",
                (partition_id, payload, fingerprint, _now()),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return fingerprint

    def create_redundancy_job(
        self,
        partition_id: str,
        base_release_id: str,
        *,
        job_id: str | None = None,
        spec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.context(partition_id):
            raise ManagedContextError(f"unknown partition: {partition_id}")
        if not str(base_release_id).strip():
            raise ManagedContextError("base_release_id is required")
        current = self.get_redundancy_policy(partition_id)
        frozen = dict(spec or {})
        frozen.setdefault("base_release_id", str(base_release_id))
        frozen.setdefault("policy", current["policy"])
        frozen.setdefault("policy_fingerprint", current["policy_fingerprint"])
        frozen.setdefault("channels", [name for name, enabled in (("lexical", current["policy"].get("lexical_enabled")), ("structural", current["policy"].get("structural_enabled")), ("dense", current["policy"].get("dense_enabled"))) if enabled])
        frozen.setdefault("prompt_version", "redundancy_judge_v1")
        frozen.setdefault("schema_version", "redundancy-job-v1")
        job_id = str(job_id or f"redundancy_{uuid.uuid4().hex}")
        now = _now()
        self.connection.execute(
            "INSERT INTO redundancy_jobs(job_id, partition_id, base_release_id, policy_fingerprint, status, detail_json, created_at, completed_at) VALUES(?,?,?,?,?,?,?,NULL)",
            (job_id, partition_id, str(base_release_id), current["policy_fingerprint"], "planned", _json({"spec": frozen}), now),
        )
        self.connection.commit()
        return {"job_id": job_id, "partition_id": partition_id, "base_release_id": str(base_release_id), "policy_fingerprint": current["policy_fingerprint"], "status": "planned", "spec": frozen, "created_at": now}

    def update_redundancy_job(self, job_id: str, *, status: str | None = None, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        allowed = {"planned", "running", "cancelled", "failed", "completed", "completed_partial"}
        row = self.connection.execute("SELECT * FROM redundancy_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise ManagedContextError(f"unknown redundancy job: {job_id}")
        if status is not None and status not in allowed:
            raise ManagedContextError(f"invalid redundancy job status: {status}")
        existing = json.loads(row["detail_json"] or "{}")
        if detail:
            existing.update(detail)
        effective = status or str(row["status"])
        completed_at = _now() if effective in {"cancelled", "failed", "completed", "completed_partial"} else row["completed_at"]
        self.connection.execute(
            "UPDATE redundancy_jobs SET status=?, detail_json=?, completed_at=? WHERE job_id=?",
            (effective, _json(existing), completed_at, job_id),
        )
        self.connection.commit()
        return self.get_redundancy_job(job_id) or {}

    def get_redundancy_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM redundancy_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return None
        detail = json.loads(row["detail_json"] or "{}")
        return {"job_id": row["job_id"], "partition_id": row["partition_id"], "base_release_id": row["base_release_id"], "policy_fingerprint": row["policy_fingerprint"], "status": row["status"], "spec": detail.get("spec", {}), "detail": detail, "created_at": row["created_at"], "completed_at": row["completed_at"]}

    def set_redundancy_judge_config(self, partition_id: str, config: dict[str, Any]) -> None:
        if not self.context(partition_id):
            raise ManagedContextError(f"unknown partition: {partition_id}")
        safe = {key: config[key] for key in ("base_url", "model", "model_fingerprint", "timeout_seconds", "auth_ref") if key in config}
        if not safe.get("model"):
            raise ManagedContextError("a selected judge model is required")
        self.set_setting(f"redundancy_judge:{partition_id}", _json(safe))

    def get_redundancy_judge_config(self, partition_id: str) -> dict[str, Any] | None:
        raw = self.get_setting(f"redundancy_judge:{partition_id}")
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ManagedContextError("stored judge configuration is invalid") from exc
        return value if isinstance(value, dict) else None

    def add_source_root(self, root: Path, *, kind: str = "podcast-rag") -> Path:
        resolved = root.expanduser().resolve()
        if not resolved.is_dir():
            raise ManagedContextError(f"source root does not exist: {resolved}")
        self.connection.execute(
            "INSERT OR REPLACE INTO source_roots(root, kind, enabled, created_at) VALUES(?, ?, 1, COALESCE((SELECT created_at FROM source_roots WHERE root = ?), ?))",
            (str(resolved), kind, str(resolved), _now()),
        )
        self.connection.commit()
        return resolved

    def source_roots(self) -> list[Path]:
        rows = self.connection.execute("SELECT root FROM source_roots WHERE enabled = 1 ORDER BY root").fetchall()
        return [Path(str(row[0])) for row in rows]

    def upsert_context(
        self,
        identity: ContextIdentity,
        *,
        source_root: Path | None = None,
        source_manifest: Path | None = None,
        producer_status: str = "active",
        legacy: bool = False,
        legacy_source: Path | None = None,
    ) -> dict[str, Any]:
        now = _now()
        existing = self.connection.execute(
            "SELECT * FROM contexts WHERE partition_id = ?", (identity.partition_id,)
        ).fetchone()
        if existing and str(existing["corpus_id"]) != identity.corpus_id:
            raise ManagedContextError(
                f"partition {identity.partition_id} is already bound to corpus {existing['corpus_id']}"
            )
        if existing:
            fingerprint = identity.partition_config_fingerprint or str(existing["partition_config_fingerprint"] or "")
            self.connection.execute(
                """UPDATE contexts SET corpus_id=?, display_name=?, context_type=?, workflow_profile=?,
                   partition_config_fingerprint=?, producer_status=?, source_root=COALESCE(?, source_root),
                   source_manifest=COALESCE(?, source_manifest), updated_at=? WHERE partition_id=?""",
                (
                    identity.corpus_id,
                    identity.display_name,
                    identity.context_type,
                    identity.workflow_profile,
                    fingerprint,
                    producer_status,
                    str(source_root.resolve()) if source_root else None,
                    str(source_manifest.resolve()) if source_manifest else None,
                    now,
                    identity.partition_id,
                ),
            )
        else:
            self.connection.execute(
                """INSERT INTO contexts(partition_id, corpus_id, display_name, context_type, workflow_profile,
                   partition_config_fingerprint, producer_status, legacy, legacy_source, source_root,
                   source_manifest, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identity.partition_id,
                    identity.corpus_id,
                    identity.display_name,
                    identity.context_type,
                    identity.workflow_profile,
                    identity.partition_config_fingerprint,
                    producer_status,
                    int(legacy),
                    str(legacy_source.resolve()) if legacy_source else None,
                    str(source_root.resolve()) if source_root else None,
                    str(source_manifest.resolve()) if source_manifest else None,
                    now,
                    now,
                ),
            )
            # New producer contexts default to Safe; the first explicit save
            # still records a revision and can select Off or Audit.
            default_profile = import_profile_payload(ImportConfig(dedup_policy={"profile": "safe"}))
            self.connection.execute(
                "INSERT OR IGNORE INTO import_profiles(partition_id, profile_json, profile_fingerprint, updated_at) VALUES(?,?,?,?)",
                (identity.partition_id, _json(default_profile), _fingerprint(default_profile), now),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO import_profile_revisions(partition_id, profile_fingerprint, profile_json, created_at) VALUES(?,?,?,?)",
                (identity.partition_id, _fingerprint(default_profile), _json(default_profile), now),
            )
        redundancy_default = resolve_redundancy_policy()
        redundancy_json = _json(redundancy_default)
        redundancy_fp = policy_fingerprint(redundancy_default)
        self.connection.execute(
            "INSERT OR IGNORE INTO redundancy_profiles(partition_id, policy_json, policy_fingerprint, updated_at) VALUES(?,?,?,?)",
            (identity.partition_id, redundancy_json, redundancy_fp, now),
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO redundancy_profile_revisions(partition_id, policy_fingerprint, policy_json, created_at) VALUES(?,?,?,?)",
            (identity.partition_id, redundancy_fp, redundancy_json, now),
        )
        self.connection.commit()
        return self.context(identity.partition_id) or {}

    def context(self, partition_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM contexts WHERE partition_id = ?", (partition_id,)).fetchone()
        return dict(row) if row else None

    def contexts(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM contexts ORDER BY display_name, partition_id")]

    def set_local_status(self, partition_id: str, status: str) -> None:
        if status not in {"active", "archived", "hidden"}:
            raise ManagedContextError("local context status must be active, archived, or hidden")
        if not self.context(partition_id):
            raise ManagedContextError(f"unknown partition: {partition_id}")
        self.connection.execute("UPDATE contexts SET local_status=?, updated_at=? WHERE partition_id=?", (status, _now(), partition_id))
        self.connection.commit()

    def record_release(self, identity: ContextIdentity, payload: dict[str, Any], source_path: Path) -> dict[str, Any]:
        release_id = _safe_id(payload.get("release_id"), "release_id")
        source_path = source_path.expanduser().resolve()
        self.connection.execute(
            """INSERT OR REPLACE INTO releases(partition_id, upstream_release_id, source_path, source_fingerprint,
               status, downstream_release_id, payload_json, discovered_at, imported_at) VALUES(?,?,?,?,COALESCE((SELECT status FROM releases WHERE partition_id=? AND upstream_release_id=?), 'discovered'), COALESCE((SELECT downstream_release_id FROM releases WHERE partition_id=? AND upstream_release_id=?), ''), ?, COALESCE((SELECT discovered_at FROM releases WHERE partition_id=? AND upstream_release_id=?), ?), (SELECT imported_at FROM releases WHERE partition_id=? AND upstream_release_id=?))""",
            (
                identity.partition_id,
                release_id,
                str(source_path),
                _sha256(source_path),
                identity.partition_id,
                release_id,
                identity.partition_id,
                release_id,
                _json(payload),
                identity.partition_id,
                release_id,
                _now(),
                identity.partition_id,
                release_id,
            ),
        )
        self.connection.commit()
        return self.release(identity.partition_id, release_id) or {}

    def release(self, partition_id: str, upstream_release_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM releases WHERE partition_id=? AND upstream_release_id=?",
            (partition_id, upstream_release_id),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def releases(self, partition_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM releases WHERE partition_id=? ORDER BY discovered_at DESC, upstream_release_id DESC",
            (partition_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def update_release(self, partition_id: str, upstream_release_id: str, *, status: str | None = None, downstream_release_id: str | None = None, imported_at: str | None = None) -> None:
        fields: list[str] = []
        values: list[Any] = []
        for name, value in (("status", status), ("downstream_release_id", downstream_release_id), ("imported_at", imported_at)):
            if value is not None:
                fields.append(f"{name}=?")
                values.append(value)
        if not fields:
            return
        values.extend((partition_id, upstream_release_id))
        self.connection.execute(f"UPDATE releases SET {', '.join(fields)} WHERE partition_id=? AND upstream_release_id=?", values)
        self.connection.commit()

    def save_profile(self, partition_id: str, profile: dict[str, Any]) -> str:
        if not self.context(partition_id):
            raise ManagedContextError(f"unknown partition: {partition_id}")
        profile = dict(profile)
        profile["dedup_policy"] = resolve_dedup_policy(profile.get("dedup_policy"), default_profile="safe")
        fingerprint = _fingerprint(profile)
        self.connection.execute(
            "INSERT OR REPLACE INTO import_profiles(partition_id, profile_json, profile_fingerprint, updated_at) VALUES(?,?,?,?)",
            (partition_id, _json(profile), fingerprint, _now()),
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO import_profile_revisions(partition_id, profile_fingerprint, profile_json, created_at) VALUES(?,?,?,?)",
            (partition_id, fingerprint, _json(profile), _now()),
        )
        self.connection.commit()
        return fingerprint

    def profile(self, partition_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM import_profiles WHERE partition_id=?", (partition_id,)).fetchone()
        if not row:
            return None
        return {"profile": json.loads(row["profile_json"]), "profile_fingerprint": row["profile_fingerprint"]}

    def context_settings(self, partition_id: str) -> dict[str, Any]:
        if not self.context(partition_id):
            raise ManagedContextError(f"unknown partition: {partition_id}")
        try:
            row = self.connection.execute("SELECT settings_json, revision, updated_at FROM context_settings WHERE partition_id=?", (partition_id,)).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            row = None
        if not row:
            return {"partition_id": partition_id, "revision": 0, "settings": {"execution_options": {"embedding_device": "auto"}}, "updated_at": None}
        return {"partition_id": partition_id, "revision": int(row[1]), "settings": json.loads(row[0]), "updated_at": row[2]}

    def save_context_settings_bundle(self, partition_id: str, *, profile: dict[str, Any] | None = None, settings: dict[str, Any] | None = None, redundancy_policy: dict[str, Any] | None = None, base_revision: int | None = None, base_profile_fingerprint: str | None = None) -> dict[str, Any]:
        if not self.context(partition_id):
            raise ManagedContextError(f"unknown partition: {partition_id}")
        current_settings = self.context_settings(partition_id)
        current_profile = self.profile(partition_id)
        if base_revision is not None and int(base_revision) != int(current_settings["revision"]):
            raise ManagedContextError("context settings changed in another window")
        if base_profile_fingerprint and str((current_profile or {}).get("profile_fingerprint") or "") != str(base_profile_fingerprint):
            raise ManagedContextError("context import profile changed in another window")
        profile_fingerprint = str((current_profile or {}).get("profile_fingerprint") or "")
        normalized_profile = None
        if profile is not None:
            normalized_profile = dict(profile)
            normalized_profile["dedup_policy"] = resolve_dedup_policy(normalized_profile.get("dedup_policy"), default_profile="safe")
            profile_fingerprint = _fingerprint(normalized_profile)
        resolved_redundancy = None
        redundancy_fingerprint = ""
        if redundancy_policy is not None:
            resolved_redundancy = resolve_redundancy_policy(redundancy_policy)
            redundancy_fingerprint = policy_fingerprint(resolved_redundancy)
        next_revision = int(current_settings["revision"]) + (1 if settings is not None else 0)
        now = _now()
        self.connection.execute("BEGIN")
        try:
            if normalized_profile is not None:
                payload = _json(normalized_profile)
                self.connection.execute("INSERT OR REPLACE INTO import_profiles(partition_id, profile_json, profile_fingerprint, updated_at) VALUES(?,?,?,?)", (partition_id, payload, profile_fingerprint, now))
                self.connection.execute("INSERT OR IGNORE INTO import_profile_revisions(partition_id, profile_fingerprint, profile_json, created_at) VALUES(?,?,?,?)", (partition_id, profile_fingerprint, payload, now))
            if settings is not None:
                self.connection.execute("INSERT OR REPLACE INTO context_settings(partition_id, settings_json, revision, updated_at) VALUES(?,?,?,?)", (partition_id, _json(settings), next_revision, now))
            if resolved_redundancy is not None:
                payload = _json(resolved_redundancy)
                self.connection.execute("INSERT OR IGNORE INTO redundancy_profile_revisions(partition_id, policy_fingerprint, policy_json, created_at) VALUES(?,?,?,?)", (partition_id, redundancy_fingerprint, payload, now))
                self.connection.execute("INSERT OR REPLACE INTO redundancy_profiles(partition_id, policy_json, policy_fingerprint, updated_at) VALUES(?,?,?,?)", (partition_id, payload, redundancy_fingerprint, now))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {"partition_id": partition_id, "revision": next_revision, "settings": dict(settings or current_settings["settings"]), "profile_fingerprint": profile_fingerprint, "policy_fingerprint": redundancy_fingerprint, "updated_at": now}

    def record_run(self, run_id: str, partition_id: str, upstream_release_id: str, downstream_release_id: str, status: str, detail: dict[str, Any], *, completed: bool = False) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO runs(run_id, partition_id, upstream_release_id, downstream_release_id, status, detail_json, started_at, completed_at) VALUES(?,?,?,?,?,?,COALESCE((SELECT started_at FROM runs WHERE run_id=?),?),?)",
            (run_id, partition_id, upstream_release_id, downstream_release_id, status, _json(detail), run_id, _now(), _now() if completed else None),
        )
        self.connection.commit()

    def latest_run(self, partition_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM runs WHERE partition_id=? ORDER BY started_at DESC LIMIT 1",
            (partition_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["detail"] = json.loads(result.pop("detail_json"))
        return result

    def record_downstream_export(
        self,
        partition_id: str,
        downstream_release_id: str,
        upstream_release_id: str,
        profile_fingerprint: str,
        status: str,
        detail: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO downstream_exports(
               partition_id, downstream_release_id, upstream_release_id,
               profile_fingerprint, status, detail_json, created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (partition_id, downstream_release_id, upstream_release_id, profile_fingerprint, status, _json(detail), _now()),
        )
        self.connection.commit()

    def set_active_release(self, output_root: Path, partition_id: str, release_id: str, *, corpus_id: str) -> Path:
        path = managed_paths(output_root, partition_id)["partition_root"] / "active-release.json"
        _atomic_json(path, {"partition_id": partition_id, "corpus_id": corpus_id, "release_id": release_id, "updated_at": _now()})
        return path


def managed_paths(output_root: Path, partition_id: str, release_id: str | None = None) -> dict[str, Path]:
    partition_id = _safe_id(partition_id, "partition_id")
    root = output_root.expanduser().resolve() / "partitions" / partition_id
    result = {"partition_root": root, "staging_root": root / "staging", "releases_root": root / "releases"}
    if release_id:
        release_id = _safe_id(release_id, "release_id")
        result["release_root"] = root / "releases" / release_id
        result["export_root"] = result["release_root"] / "export"
    return result


def derive_downstream_release_id(upstream_release_id: str, profile_fingerprint: str) -> str:
    upstream = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(upstream_release_id)).strip("-.") or "release"
    return _safe_id(f"chroma_{upstream}_{profile_fingerprint.replace('sha256:', '')[:12]}", "downstream release ID")


def import_profile_payload(config: ImportConfig) -> dict[str, Any]:
    return {
        "representation_profile": config.representation_profile,
        "embedding_provider": config.embedding_provider,
        "embedding_model": config.embedding_model,
        "embedding_model_revision": config.embedding_model_revision,
        "embedding_dimension": config.embedding_dimension,
        "inference_dtype": config.inference_dtype,
        "query_instruction_profile": config.query_instruction_profile,
        "normalize_embeddings": config.normalize_embeddings,
        "distance_metric": config.distance_metric,
        "contextualization": config.contextualization,
        "output_dimension": config.output_dimension,
        "matryoshka_compatible": config.matryoshka_compatible,
        "selected_speakers": sorted(config.selected_speakers or []),
        "dedup_policy": resolve_dedup_policy(config.dedup_policy, default_profile="safe"),
        "collection_name": COLLECTION_NAME,
    }


def resolve_managed_config(base_config: ImportConfig, saved_profile: dict[str, Any] | None) -> ImportConfig:
    """Apply a saved context profile without replacing local paths."""
    if not saved_profile:
        return replace(base_config, dedup_policy=resolve_dedup_policy(base_config.dedup_policy, default_profile="safe"))
    payload = saved_profile.get("profile") if isinstance(saved_profile.get("profile"), dict) else saved_profile
    config = replace(base_config)
    for field_name in (
        "representation_profile", "embedding_provider", "embedding_model", "embedding_model_revision",
        "embedding_dimension", "inference_dtype", "query_instruction_profile",
        "normalize_embeddings", "distance_metric", "contextualization",
        "output_dimension", "matryoshka_compatible",
        "selected_speakers", "collection_name",
    ):
        if field_name in payload:
            setattr(config, field_name, payload[field_name])
    config.dedup_policy = resolve_dedup_policy(payload.get("dedup_policy"), default_profile="off")
    return config


def _candidate_cache_files(release_path: Path, source_roots: Iterable[Path]) -> list[Path]:
    roots: set[Path] = set()
    release_path = release_path.resolve()
    roots.update({release_path.parent, release_path.parent.parent, release_path.parent.parent.parent})
    roots.update(path.expanduser().resolve() for path in source_roots)
    candidates: set[Path] = set()
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for base in (root, root / "processed_data", root / "processed"):
            if base.exists() and base.is_dir():
                candidates.update(
                    path
                    for path in base.rglob("*.processed_documents.json")
                    if path.is_file()
                    and not any(part.casefold() == "reprocess_backups" for part in path.parts)
                )
    return sorted(candidates)


def _cache_episode_uids(payload: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    top_level = payload.get("episode_uid")
    if top_level not in (None, ""):
        values.add(str(top_level))
    for value in payload.get("episode_uids") or []:
        if value not in (None, ""):
            values.add(str(value))
    for item in payload.get("documents") or []:
        if not isinstance(item, dict):
            continue
        for mapping in (item.get("metadata"), item):
            if isinstance(mapping, dict) and mapping.get("episode_uid") not in (None, ""):
                values.add(str(mapping["episode_uid"]))
    return values


def resolve_release_cache_files(release: dict[str, Any], source_roots: Iterable[Path]) -> list[Path]:
    payload = release.get("payload") if "payload" in release else release
    release_path = Path(str(release.get("source_path") or ""))
    if not release_path.is_file():
        raise ManagedContextError("release manifest path is unavailable")
    if payload.get("release_contract_version") != UPSTREAM_RELEASE_CONTRACT:
        raise ManagedContextError("unsupported or invalid Podcast-RAG release contract")
    validate_upstream_release(payload)
    if payload.get("release_contract_version") == UPSTREAM_RELEASE_CONTRACT:
        partition_root = release_path.parent.parent.resolve()
        resolved_paths: list[Path] = []
        resolved_episode_uids: set[str] = set()
        resolved_fingerprints: set[str] = set()
        for artifact in payload.get("processed_cache_artifacts") or []:
            relative_path = Path(str(artifact.get("relative_path") or ""))
            try:
                cache_path = (partition_root / relative_path).resolve()
                cache_path.relative_to(partition_root)
            except (OSError, ValueError) as exc:
                raise ManagedContextError("release cache artifact path escapes the partition root") from exc
            if not cache_path.is_file():
                raise ManagedContextError(f"release cache artifact is unavailable: {relative_path.as_posix()}")
            expected_content_hash = _normalize_hash(artifact.get("content_sha256"), "release cache artifact content_sha256")
            if _sha256(cache_path) != expected_content_hash:
                raise ManagedContextError(f"release cache artifact content hash mismatch: {cache_path.name}")
            cache_payload = load_processed_payload(cache_path)
            identity_values = partition_identities(cache_payload)
            if len({(item.get("partition_id"), item.get("corpus_id")) for item in identity_values}) != 1:
                raise ManagedContextError(f"managed release references a cache with mixed partition/corpus identity: {cache_path.name}")
            cache_identity = identity_values[0] if identity_values else {}
            if (
                cache_identity.get("partition_id") != payload.get("partition_id")
                or cache_identity.get("corpus_id") != payload.get("corpus_id")
            ):
                raise ManagedContextError(f"cache partition/corpus mismatch: {cache_path.name}")
            cache_episode_uids = _cache_episode_uids(cache_payload)
            expected_episode_uid = str(artifact.get("episode_uid") or "")
            if cache_episode_uids != {expected_episode_uid}:
                raise ManagedContextError(f"cache episode_uid binding disagrees with release: {cache_path.name}")
            if str(cache_payload.get("episode_id") or "") != str(artifact.get("episode_id") or ""):
                raise ManagedContextError(f"cache episode_id binding disagrees with release: {cache_path.name}")
            actual_fingerprint = str(cache_payload.get("source_fingerprint") or cache_payload.get("cache_fingerprint") or "").strip().removeprefix("sha256:")
            expected_fingerprint = str(artifact.get("cache_fingerprint") or "").strip().removeprefix("sha256:")
            if not actual_fingerprint or actual_fingerprint != expected_fingerprint:
                raise ManagedContextError(f"cache fingerprint mismatch: {cache_path.name}")
            resolved_paths.append(cache_path)
            resolved_episode_uids.add(expected_episode_uid)
            resolved_fingerprints.add(expected_fingerprint)
        expected_episode_uids = {str(item).strip() for item in payload.get("episode_uids") or [] if str(item).strip()}
        expected_fingerprints = {str(item).strip().removeprefix("sha256:") for item in payload.get("processed_cache_fingerprints") or [] if str(item).strip()}
        if resolved_episode_uids != expected_episode_uids or resolved_fingerprints != expected_fingerprints:
            raise ManagedContextError("release cache artifacts do not completely bind to the release declarations")
        return sorted(set(resolved_paths))
    expected = {str(item) for item in (payload.get("processed_cache_fingerprints") or []) if str(item)}
    if not expected:
        raise ManagedContextError("release does not declare processed_cache_fingerprints")
    matches: dict[str, Path] = {}
    match_paths: dict[str, set[Path]] = {}
    for path in _candidate_cache_files(release_path, source_roots):
        try:
            cache_payload = load_processed_payload(path)
        except Exception:
            continue
        values = {
            str(cache_payload.get("source_fingerprint") or ""),
            str(cache_payload.get("cache_fingerprint") or ""),
            cache_fingerprint(path),
            content_fingerprint(path),
        }
        for value in values - {""}:
            if value in expected:
                matches[value] = path
                match_paths.setdefault(value, set()).add(path)
    missing = sorted(expected - set(matches))
    if missing:
        raise ManagedContextError("release caches are unavailable: " + ", ".join(missing[:5]))
    paths = sorted(set(matches.values()))
    if len(paths) != len(expected) or any(len(value) != 1 for value in match_paths.values()):
        raise ManagedContextError("release cache fingerprints do not bind one-to-one to cache artifacts")
    identity = ContextIdentity.from_mapping(payload, strict=True)
    expected_episode_uids = {str(item) for item in (payload.get("episode_uids") or []) if str(item)}
    resolved_episode_uids: set[str] = set()
    for path in paths:
        cache_payload = load_processed_payload(path)
        cache_identity_values = partition_identities(cache_payload)
        cache_keys = {(item.get("partition_id"), item.get("corpus_id")) for item in cache_identity_values}
        if len(cache_keys) != 1:
            raise ManagedContextError(f"managed release references a cache with mixed partition/corpus identity: {path.name}")
        cache_identity = cache_identity_values[0] if cache_identity_values else {}
        if not cache_identity:
            raise ManagedContextError(f"managed release references a cache without partition identity: {path.name}")
        if cache_identity.get("partition_id") != identity.partition_id or cache_identity.get("corpus_id") != identity.corpus_id:
            raise ManagedContextError(f"cache partition/corpus mismatch: {path.name}")
        cache_episode_uids = _cache_episode_uids(cache_payload)
        if not cache_episode_uids:
            raise ManagedContextError(f"managed release references a cache without episode_uid binding: {path.name}")
        if not cache_episode_uids.issubset(expected_episode_uids):
            raise ManagedContextError(f"cache episode_uid binding disagrees with release: {path.name}")
        resolved_episode_uids.update(cache_episode_uids)
    if expected_episode_uids and resolved_episode_uids != expected_episode_uids:
        raise ManagedContextError("release episode_uids are not completely bound to the declared cache artifacts")
    return paths


def _known_discovery_dirs(root: Path) -> list[Path]:
    """Return the shallow producer layout locations that can contain metadata.

    Source roots also contain processed caches, transcripts, audio, and other
    large working trees.  Discovery metadata has a small, producer-defined
    layout, so walking only these directories avoids inspecting unrelated files.
    """
    directories: set[Path] = {root.resolve()}
    try:
        directories.update(path.resolve() for path in root.iterdir() if path.is_dir())
    except OSError:
        return sorted(directories)
    partitions_root = root / "partitions"
    try:
        if partitions_root.is_dir():
            directories.update(path.resolve() for path in partitions_root.iterdir() if path.is_dir())
    except OSError:
        pass
    return sorted(directories)


def _discovery_manifest_candidates(root: Path) -> list[Path]:
    """Find metadata in the supported shallow producer layout only."""
    directories = _known_discovery_dirs(root)
    candidates: set[Path] = set()
    for directory in directories:
        for filename in ("partition.json", "manifest.json", "release.json"):
            path = directory / filename
            if path.is_file():
                candidates.add(path.resolve())
        try:
            candidates.update(path.resolve() for path in directory.glob("*.release.json") if path.is_file())
        except OSError:
            pass
        releases_dir = directory / "releases"
        try:
            candidates.update(path.resolve() for path in releases_dir.glob("*.release.json") if path.is_file())
        except OSError:
            pass
        for inbox_name in ("handoff_inbox", "handoff"):
            inbox = directory / inbox_name
            try:
                packages = [path for path in inbox.iterdir() if path.is_dir()] if inbox.is_dir() else []
                for package in packages:
                    for filename in ("manifest.json", "release.json"):
                        path = package / filename
                        if path.is_file():
                            candidates.add(path.resolve())
                    candidates.update(path.resolve() for path in package.glob("*.release.json") if path.is_file())
            except OSError:
                continue
    return sorted(candidates)


def _discovery_pointer_candidates(root: Path) -> list[Path]:
    pointers: list[Path] = []
    for directory in _known_discovery_dirs(root):
        path = directory / "active-release.json"
        if path.is_file():
            pointers.append(path.resolve())
    return sorted(set(pointers))


def _discovery_signature(path: Path) -> dict[str, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def _handoff_dependency_signatures(payload: Mapping[str, Any], package_root: Path) -> dict[str, dict[str, int] | None]:
    dependencies: dict[str, dict[str, int] | None] = {}
    for episode in payload.get("episodes") or []:
        if not isinstance(episode, Mapping):
            continue
        selected = episode.get("selected_transcript")
        if not isinstance(selected, Mapping):
            continue
        relative = str(selected.get("path") or "").strip()
        if not relative:
            continue
        path = (package_root / relative).resolve()
        dependencies[str(path)] = _discovery_signature(path)
    return dependencies


def _discovery_dependencies_unchanged(entry: Mapping[str, Any]) -> bool:
    dependencies = entry.get("dependencies")
    if not isinstance(dependencies, Mapping):
        return True
    return all(_discovery_signature(Path(str(path))) == signature for path, signature in dependencies.items())


def _discovery_cache_key(root: Path) -> str:
    return "discovery_cache:" + hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()


def _load_discovery_cache(catalog: ManagedCatalog, root: Path) -> dict[str, dict[str, Any]]:
    raw = catalog.get_setting(_discovery_cache_key(root), "{}")
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _append_discovered_context(report: dict[str, Any], context: Mapping[str, Any] | None) -> None:
    if not context:
        return
    partition_id = str(context.get("partition_id") or "")
    if not any(str(item.get("partition_id") or "") == partition_id for item in report["contexts"]):
        report["contexts"].append(dict(context))


def _append_discovered_release(report: dict[str, Any], partition_id: str, release_id: str, path: Path) -> None:
    if not any(item.get("partition_id") == partition_id and item.get("release_id") == release_id for item in report["releases"]):
        report["releases"].append({"partition_id": partition_id, "release_id": release_id, "path": str(path)})


def discover(catalog: ManagedCatalog, roots: Iterable[Path] | None = None) -> dict[str, Any]:
    if roots:
        for root in roots:
            catalog.add_source_root(root)
    source_roots = catalog.source_roots()
    report: dict[str, Any] = {"roots": [str(path) for path in source_roots], "contexts": [], "releases": [], "invalid": []}
    for root in source_roots:
        root = root.resolve()
        cache = _load_discovery_cache(catalog, root)
        next_cache: dict[str, dict[str, Any]] = {}

        for path in _discovery_manifest_candidates(root):
            key = str(path)
            signature = _discovery_signature(path)
            if signature is None:
                continue
            cached = cache.get(key)
            if cached and cached.get("signature") == signature and _discovery_dependencies_unchanged(cached):
                kind = str(cached.get("kind") or "")
                partition_id = str(cached.get("partition_id") or "")
                release_id = str(cached.get("release_id") or "")
                context = catalog.context(partition_id) if partition_id else None
                release = catalog.release(partition_id, release_id) if partition_id and release_id else None
                if kind == "release" and context and release and Path(str(release.get("source_path") or "")).resolve() == path:
                    _append_discovered_context(report, context)
                    _append_discovered_release(report, partition_id, release_id, path)
                    next_cache[key] = cached
                    continue
                if kind in {"handoff", "partition"} and context:
                    _append_discovered_context(report, context)
                    next_cache[key] = cached
                    continue
                if kind == "ignored":
                    next_cache[key] = cached
                    continue
                if kind == "invalid":
                    report["invalid"].append({"path": key, "error": str(cached.get("error") or "Cached metadata validation failed.")})
                    next_cache[key] = cached
                    continue

            entry: dict[str, Any] = {"signature": signature}
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ManagedContextError("metadata must be a JSON object")
                if payload.get("release_contract_version") == UPSTREAM_RELEASE_CONTRACT:
                    identity = validate_upstream_release(payload)
                    _require_registry_mapping(identity, root)
                    partition_payload = payload.get("partition") if isinstance(payload.get("partition"), dict) else payload
                    context = catalog.upsert_context(identity, source_root=root, source_manifest=path, producer_status=str(partition_payload.get("status") or "active"))
                    release = catalog.record_release(identity, payload, path)
                    entry.update({"kind": "release", "partition_id": identity.partition_id, "release_id": str(release["upstream_release_id"])})
                    _append_discovered_context(report, context)
                    _append_discovered_release(report, identity.partition_id, str(release["upstream_release_id"]), path)
                elif payload.get("contract_version") == HANDOFF_CONTRACT:
                    entry["dependencies"] = _handoff_dependency_signatures(payload, path.parent)
                    identity = validate_handoff_manifest(payload, path.parent)
                    _require_registry_mapping(identity, root)
                    partition_payload = payload.get("partition") if isinstance(payload.get("partition"), dict) else payload
                    context = catalog.upsert_context(identity, source_root=root, source_manifest=path, producer_status=str(partition_payload.get("status") or "active"))
                    entry.update({"kind": "handoff", "partition_id": identity.partition_id})
                    _append_discovered_context(report, context)
                elif str(payload.get("release_contract_version") or "") == LEGACY_UPSTREAM_RELEASE_CONTRACT:
                    raise ManagedContextError("legacy Podcast-RAG release manifests are historical and not importable")
                elif path.name == "partition.json" and str(payload.get("contract_version") or "").startswith("podcast-rag-partition-"):
                    identity = ContextIdentity.from_mapping(payload, strict=True)
                    _require_registry_mapping(identity, root)
                    partition_payload = payload.get("partition") if isinstance(payload.get("partition"), dict) else payload
                    context = catalog.upsert_context(identity, source_root=root, source_manifest=path, producer_status=str(partition_payload.get("status") or "active"))
                    entry.update({"kind": "partition", "partition_id": identity.partition_id})
                    _append_discovered_context(report, context)
                else:
                    entry["kind"] = "ignored"
            except (OSError, json.JSONDecodeError, ManagedContextError) as exc:
                entry.update({"kind": "invalid", "error": f"{type(exc).__name__}: {exc}"})
                report["invalid"].append({"path": key, "error": str(entry["error"])})
            next_cache[key] = entry

        for pointer_path in _discovery_pointer_candidates(root):
            try:
                pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
                if pointer.get("pointer_contract_version") != "podcast-rag-active-release-v1":
                    raise ManagedContextError("unsupported active release pointer contract")
                relative_release_path = Path(str(pointer.get("release_path") or ""))
                partition_root = pointer_path.parent.resolve()
                release_path = (partition_root / relative_release_path).resolve()
                release_path.relative_to(partition_root)
                if not release_path.is_file():
                    raise ManagedContextError("active release pointer references a missing manifest")
                release_payload = json.loads(release_path.read_text(encoding="utf-8"))
                identity = validate_upstream_release(release_payload)
                if str(pointer.get("partition_id") or "") != identity.partition_id or str(pointer.get("corpus_id") or "") != identity.corpus_id:
                    raise ManagedContextError("active release pointer identity does not match its manifest")
                if str(pointer.get("release_id") or "") != str(release_payload.get("release_id") or ""):
                    raise ManagedContextError("active release pointer release_id does not match its manifest")
                expected_hash = _normalize_hash(pointer.get("release_sha256"), "active release pointer release_sha256")
                if _sha256(release_path) != expected_hash:
                    raise ManagedContextError("active release pointer manifest hash does not match")
                _require_registry_mapping(identity, root)
                partition_payload = release_payload.get("partition") if isinstance(release_payload.get("partition"), dict) else release_payload
                context = catalog.upsert_context(identity, source_root=root, source_manifest=release_path, producer_status=str(partition_payload.get("status") or "active"))
                _append_discovered_context(report, context)
                report.setdefault("active_releases", []).append(
                    {
                        "partition_id": identity.partition_id,
                        "release_id": str(release_payload.get("release_id") or ""),
                        "pointer": str(pointer_path),
                        "path": str(release_path),
                    }
                )
            except (OSError, json.JSONDecodeError, ManagedContextError, ValueError) as exc:
                report["invalid"].append({"path": str(pointer_path), "error": f"{type(exc).__name__}: {exc}"})
        catalog.set_setting(_discovery_cache_key(root), json.dumps(next_cache, ensure_ascii=False, sort_keys=True))
    return report


def _require_registry_mapping(identity: ContextIdentity, source_root: Path) -> None:
    """Require producer registry evidence for a non-default corpus mapping."""
    if identity.partition_id == identity.corpus_id:
        return
    registry_path = next(
        (
            candidate / "partitions" / "registry.json"
            for candidate in (source_root, *source_root.parents)
            if (candidate / "partitions" / "registry.json").is_file()
        ),
        None,
    )
    if registry_path is None:
        raise ManagedContextError(
            f"partition {identity.partition_id} maps to corpus {identity.corpus_id}; producer registry evidence is required"
        )
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagedContextError(f"could not read producer partition registry: {exc}") from exc
    entries = payload.get("partitions") if isinstance(payload, dict) else None
    match = next((item for item in entries or [] if isinstance(item, dict) and item.get("partition_id") == identity.partition_id), None)
    if not match or str(match.get("corpus_id") or identity.partition_id) != identity.corpus_id:
        raise ManagedContextError(
            f"producer registry does not prove corpus mapping for partition {identity.partition_id}"
        )


def adopt_legacy(catalog: ManagedCatalog, partition_id: str, source_dir: Path, display_name: str | None = None) -> dict[str, Any]:
    source_dir = source_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise ManagedContextError(f"legacy source directory does not exist: {source_dir}")
    root_fingerprint = hashlib.sha256(str(source_dir).encode("utf-8")).hexdigest()[:16]
    identity = ContextIdentity(
        _safe_id(partition_id, "partition_id"),
        _safe_id(f"legacy-{root_fingerprint}", "corpus_id"),
        display_name or partition_id,
        "custom",
        "legacy",
        _fingerprint({"legacy_source": str(source_dir)}),
    )
    return catalog.upsert_context(identity, legacy=True, legacy_source=source_dir, source_root=source_dir)


def _portable_config(config: dict[str, Any]) -> dict[str, Any]:
    path_fields = {"processed_data_dir", "persist_dir", "state_path", "stop_file", "preflight_report_path", "import_state_dir", "embedding_cache_dir", "troubleshooting_dir"}
    return {key: ("<local>" if key in path_fields else value) for key, value in config.items()}


def _write_managed_metadata(export_root: Path, identity: ContextIdentity, upstream: dict[str, Any], profile_fingerprint: str, cache_paths: list[Path], manifest: dict[str, Any], *, upstream_release_id: str, downstream_release_id: str, dedup_plan: Any | None = None) -> None:
    episodes: dict[str, dict[str, Any]] = {}
    speakers: set[str] = set()
    temporal_metadata: list[dict[str, Any]] = []
    for path in cache_paths:
        payload = load_processed_payload(path)
        docs = payload.get("documents") if isinstance(payload.get("documents"), list) else []
        metadata = [dict(item.get("metadata") or {}) for item in docs if isinstance(item, dict)]
        if payload.get("episode_uid"):
            for item in metadata:
                item.setdefault("episode_uid", str(payload["episode_uid"]))
        temporal_metadata.extend(metadata)
        first = next((item for item in metadata if item), {})
        declared_uid = str(payload.get("episode_uid") or "")
        declared_episode_id = declared_uid.split(":", 1)[1] if ":" in declared_uid else ""
        episode_id = str(first.get("episode_id") or payload.get("episode_id") or declared_episode_id or path.stem)
        episode_speakers = sorted({speaker for item in metadata for speaker in coerce_speakers(item)})
        speakers.update(episode_speakers)
        stored_count = len(docs)
        if dedup_plan is not None:
            stored_count = sum(
                1 for item_id in dedup_plan.cache_ids.get(str(path), tuple())
                if item_id in dedup_plan.stored_ids
                if dedup_plan.inputs.get(item_id) is not None
                and dedup_plan.inputs[item_id].episode_uid == str(first.get("episode_uid") or f"{identity.partition_id}:{episode_id}")
            )
        episode_uid = str(first.get("episode_uid") or f"{identity.partition_id}:{episode_id}")
        previous = episodes.get(episode_id)
        if previous:
            previous["document_count"] = int(previous.get("document_count") or 0) + stored_count
            previous["raw_document_count"] = int(previous.get("raw_document_count") or 0) + len(docs)
            previous["speakers"] = [{"id": re.sub(r"[^a-z0-9]+", "-", speaker.lower()).strip("-") or "speaker", "name": speaker} for speaker in sorted(set(episode_speakers) | {str(item.get("name")) for item in previous.get("speakers") or []})]
        else:
            episodes[episode_id] = {
                "source_file": path.name,
                "source_fingerprint": str(payload.get("source_fingerprint") or content_fingerprint(path)),
                "episode_uid": episode_uid,
                "episode_id": episode_id,
                "episode_title": str(first.get("episode_title") or payload.get("episode_title") or episode_id),
                "episode_date": first.get("episode_date") or payload.get("episode_date"),
                "episode_sort_key": first.get("episode_sort_key") or payload.get("episode_sort_key"),
                "document_count": stored_count,
                "raw_document_count": len(docs),
                "speakers": [{"id": re.sub(r"[^a-z0-9]+", "-", speaker.lower()).strip("-") or "speaker", "name": speaker} for speaker in episode_speakers],
                "imported_at": _now(),
                **identity.as_dict(),
            }
    temporal_coverage = temporal_coverage_stats(temporal_metadata)
    upstream_temporal_capability = str(upstream.get("temporal_capability") or "legacy")
    temporal_capability = "legacy" if upstream_temporal_capability == "legacy" else temporal_coverage.get("temporal_capability", "legacy")
    temporal_coverage = dict(temporal_coverage)
    temporal_coverage["temporal_capability"] = temporal_capability
    representation = manifest.get("representation") or {}
    managed_collection_name = resolved_collection_name(
        str(manifest.get("collection_name") or COLLECTION_NAME),
        str(representation.get("profile") or QWEN3_PROFILE),
    )
    podcast = {
        "podcast_name": identity.display_name,
        "database_id": identity.corpus_id,
        "collection_name": managed_collection_name,
        "release_contract_version": DOWNSTREAM_RELEASE_CONTRACT,
        "release_id": downstream_release_id,
        "downstream_release_id": downstream_release_id,
        "representation_id": manifest.get("representation_id") or (manifest.get("representation") or {}).get("representation_id"),
        "embedding_model": manifest.get("embedding_model"),
        "embedding_dimension": manifest.get("embedding_dimension"),
        "representation_profile": representation.get("profile"),
        "embedding_provider": representation.get("provider"),
        "model_revision": representation.get("model_revision"),
        "embedding_model_revision": representation.get("model_revision"),
        "inference_dtype": representation.get("inference_dtype"),
        "query_instruction_profile": representation.get("query_instruction_profile"),
        "query_document_mode": representation.get("query_document_mode"),
        "contextualization": representation.get("contextualization"),
        "context_header_version": representation.get("context_header_version"),
        "pooling": representation.get("pooling"),
        "index_schema_version": representation.get("index_schema_version"),
        "implementation_version": representation.get("implementation_version"),
        "distance_metric": representation.get("distance_metric"),
        "normalize_embeddings": representation.get("normalize_embeddings"),
        "representation": dict(representation),
        "description": f"Managed {identity.context_type} context: {identity.display_name}",
        "episode_count": len(episodes),
        "document_count": sum(int(item.get("document_count") or 0) for item in episodes.values()),
        "speakers": [{"id": re.sub(r"[^a-z0-9]+", "-", speaker.lower()).strip("-") or "speaker", "name": speaker} for speaker in sorted(speakers)],
        "episodes": list(episodes.values()),
        "generated_at": _now(),
        "generated_by": "Chroma DB Import managed context",
        **identity.as_dict(),
        "upstream_release_id": upstream_release_id,
        "import_profile_fingerprint": profile_fingerprint,
        "temporal_capability": temporal_capability,
        "temporal_coverage": temporal_coverage,
        "temporal_coverage_sha256": _temporal_hash(temporal_coverage),
    }
    podcast_report = validate_podcast_metadata(podcast)
    if not podcast_report.valid:
        raise ManagedContextError("generated podcast metadata is invalid: " + "; ".join(podcast_report.errors[:3]))
    manifest["config"] = _portable_config(dict(manifest.get("config") or {}))
    manifest["collection_name"] = managed_collection_name
    manifest["source_files"] = [{**item, "path": f"processed_data/{Path(str(item.get('path') or '')).name}"} for item in manifest.get("source_files") or []]
    manifest.update(identity.as_dict())
    manifest["partition"] = identity.as_dict()
    manifest["upstream_release_id"] = upstream_release_id
    manifest["release_contract_version"] = DOWNSTREAM_RELEASE_CONTRACT
    manifest["release_id"] = downstream_release_id
    manifest["downstream_release_id"] = downstream_release_id
    manifest["handoff_ids"] = list(upstream.get("handoff_ids") or [])
    manifest["import_profile_fingerprint"] = profile_fingerprint
    manifest["portable"] = True
    manifest["temporal_capability"] = temporal_capability
    manifest["temporal_coverage"] = temporal_coverage
    manifest["temporal_coverage_sha256"] = _temporal_hash(temporal_coverage)
    if dedup_plan is not None:
        manifest["dedup_counts"] = dedup_plan.as_counts()
    downstream = {
        "release_contract_version": DOWNSTREAM_RELEASE_CONTRACT,
        "release_id": downstream_release_id,
        "upstream_release_contract_version": UPSTREAM_RELEASE_CONTRACT,
        "upstream_release_id": upstream_release_id,
        "partition": identity.as_dict(),
        **identity.as_dict(),
        "handoff_ids": list(upstream.get("handoff_ids") or []),
        "episode_uids": sorted(item["episode_uid"] for item in episodes.values()),
        "processed_cache_fingerprints": sorted(str(item) for item in upstream.get("processed_cache_fingerprints") or []),
        "representation_id": manifest.get("representation_id") or (manifest.get("representation") or {}).get("representation_id"),
        "embedding_model": manifest.get("embedding_model"),
        "embedding_dimension": manifest.get("embedding_dimension"),
        "representation_profile": representation.get("profile"),
        "embedding_provider": representation.get("provider"),
        "model_revision": representation.get("model_revision"),
        "embedding_model_revision": representation.get("model_revision"),
        "inference_dtype": representation.get("inference_dtype"),
        "query_instruction_profile": representation.get("query_instruction_profile"),
        "query_document_mode": representation.get("query_document_mode"),
        "contextualization": representation.get("contextualization"),
        "context_header_version": representation.get("context_header_version"),
        "pooling": representation.get("pooling"),
        "index_schema_version": representation.get("index_schema_version"),
        "implementation_version": representation.get("implementation_version"),
        "distance_metric": representation.get("distance_metric"),
        "normalize_embeddings": representation.get("normalize_embeddings"),
        "representation": dict(representation),
        "import_profile_fingerprint": profile_fingerprint,
        "created_at": _now(),
        "temporal_capability": temporal_capability,
        "temporal_coverage": temporal_coverage,
        "temporal_coverage_sha256": _temporal_hash(temporal_coverage),
    }
    _atomic_json(export_root / "podcast.json", podcast)
    _atomic_json(export_root / "import_manifest.json", manifest)
    _atomic_json(export_root / "release.json", downstream)


def _stamp_collection_identity(export_root: Path, identity: ContextIdentity, upstream_release_id: str, downstream_release_id: str, profile_fingerprint: str, dedup_plan: Any | None = None) -> None:
    """Add the same scope identity to Chroma's collection metadata."""
    database_path = export_root / "chroma.sqlite3"
    # Test/fake importers and interrupted staging can leave a placeholder
    # file. There is no collection to stamp until the importer has produced a
    # real SQLite-backed Chroma store; do not reinterpret that placeholder as
    # a live database during this optional metadata step.
    try:
        if not database_path.is_file() or database_path.read_bytes()[:16] != b"SQLite format 3\x00":
            return
    except OSError:
        return
    try:
        import chromadb
    except ModuleNotFoundError:
        return
    client = None
    try:
        client = chromadb.PersistentClient(path=str(export_root))
        collection = client.get_collection(resolved_collection_name(COLLECTION_NAME, QWEN3_PROFILE))
        metadata = dict(getattr(collection, "metadata", None) or {})
        temporal_payload = {}
        try:
            temporal_payload = json.loads((export_root / "podcast.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            temporal_payload = {}
        metadata.update(
            {
                "partition_id": identity.partition_id,
                "corpus_id": identity.corpus_id,
                "partition_display_name": identity.display_name,
                "context_type": identity.context_type,
                "workflow_profile": identity.workflow_profile,
                "partition_config_fingerprint": identity.partition_config_fingerprint,
                "upstream_release_id": upstream_release_id,
                "chroma_release_id": downstream_release_id,
                "import_profile_fingerprint": profile_fingerprint,
                "embedding_model": QWEN3_MODEL,
                "model_revision": QWEN3_MODEL_REVISION,
                "profile": QWEN3_PROFILE,
                "embedding_dimension": 2560,
                "distance_metric": "cosine",
                "normalize_embeddings": True,
                "temporal_capability": temporal_payload.get("temporal_capability", "legacy"),
                "temporal_coverage_sha256": temporal_payload.get("temporal_coverage_sha256", ""),
            }
        )
        if dedup_plan is not None:
            metadata.update({
                "dedup_policy_version": dedup_plan.policy.get("policy_version", ""),
                "dedup_policy_fingerprint": _fingerprint(dedup_plan.policy),
                "dedup_plan_fingerprint": dedup_plan.plan_fingerprint,
            })
        collection.modify(
            metadata=metadata
        )
    finally:
        close_chroma_client(client)


def _managed_release_is_reusable(
    export_root: Path,
    identity: ContextIdentity,
    upstream: dict[str, Any],
    upstream_release_id: str,
    downstream_release_id: str,
    profile_fingerprint: str,
    dedup_plan: Any | None = None,
) -> bool:
    required = ("chroma.sqlite3", "podcast.json", "import_manifest.json", "release.json")
    if not export_root.is_dir() or any(not (export_root / name).is_file() for name in required):
        return False
    try:
        release = json.loads((export_root / "release.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    identity_ok = (
        release.get("release_contract_version") in {DOWNSTREAM_RELEASE_CONTRACT, DOWNSTREAM_DEDUP_RELEASE_CONTRACT}
        and release.get("release_id") == downstream_release_id
        and release.get("upstream_release_id") == upstream_release_id
        and release.get("partition_id") == identity.partition_id
        and release.get("corpus_id") == identity.corpus_id
        and release.get("import_profile_fingerprint") == profile_fingerprint
        and set(str(item) for item in release.get("episode_uids") or []) == set(str(item) for item in upstream.get("episode_uids") or [])
        and set(str(item) for item in release.get("processed_cache_fingerprints") or []) == set(str(item) for item in upstream.get("processed_cache_fingerprints") or [])
    )
    if not identity_ok:
        return False
    if release.get("release_contract_version") == DOWNSTREAM_DEDUP_RELEASE_CONTRACT or release.get("required_capabilities") or release.get("dedup"):
        # A v2 export is reusable only after validating the complete portable
        # ledger and its references.  If it has no dedup reference it is not a
        # valid v2 export, so a profile collision cannot overwrite it.
        if release.get("release_contract_version") != DOWNSTREAM_DEDUP_RELEASE_CONTRACT or DEDUP_CAPABILITY not in (release.get("required_capabilities") or []):
            return False
        try:
            validated = validate_dedup_artifacts(export_root, release)
            if dedup_plan is not None and validated["manifest"].get("plan_fingerprint") != dedup_plan.plan_fingerprint:
                return False
        except Exception:
            return False
    return True


def run_managed_import(
    config: ImportConfig,
    project_dir: Path,
    catalog: ManagedCatalog,
    partition_id: str,
    *,
    upstream_release_id: str | None = None,
    output_root: Path | None = None,
    dry_run: bool = False,
    validation_only: bool = False,
    selection_policy: Mapping[str, Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    context = catalog.context(partition_id)
    if not context:
        raise ManagedContextError(f"unknown partition: {partition_id}")
    if context.get("local_status") in {"archived", "hidden"} or context.get("producer_status") == "archived":
        raise ManagedContextError(f"partition is not active: {partition_id}")
    releases = catalog.releases(partition_id)
    if not releases:
        raise ManagedContextError(f"partition has no discovered Podcast-RAG release: {partition_id}")
    preferred_release_id = upstream_release_id or producer_active_release_id(Path(str(context.get("source_root") or project_dir)), partition_id)
    release = next((item for item in releases if item["upstream_release_id"] == preferred_release_id), releases[0]) if releases else None
    if release is None:
        raise ManagedContextError(f"partition has no discovered Podcast-RAG release: {partition_id}")
    if upstream_release_id and release["upstream_release_id"] != upstream_release_id:
        raise ManagedContextError(f"unknown upstream release {upstream_release_id} for {partition_id}")
    upstream = release["payload"]
    identity = validate_upstream_release(upstream)
    if identity.partition_id != partition_id or identity.corpus_id != context["corpus_id"]:
        raise ManagedContextError("release identity does not match the catalog partition")
    try:
        cache_paths = resolve_release_cache_files(release, catalog.source_roots())
    except ManagedContextError:
        if not (dry_run or validation_only):
            catalog.update_release(partition_id, release["upstream_release_id"], status="quarantined")
        raise
    from chroma_db_import.diagnostics import preflight_files

    # The catalog profile is authoritative.  A caller-supplied base config is
    # only used for local paths and for contexts that have not been cataloged.
    effective_config = resolve_managed_config(config, catalog.profile(partition_id))
    if str(upstream.get("temporal_capability") or "legacy") == "certified":
        effective_config.temporal_validation_mode = "certified"

    if progress_callback is not None:
        progress_callback(f"Validating {len(cache_paths)} producer cache(s)...", 0, len(cache_paths))
    preflight = preflight_files(effective_config, project_dir, cache_paths)
    if progress_callback is not None:
        progress_callback("Producer cache validation complete", len(cache_paths), len(cache_paths))
    if not preflight["valid"]:
        if not (dry_run or validation_only):
            catalog.update_release(partition_id, release["upstream_release_id"], status="quarantined")
        return {"status": "quarantined", "partition_id": partition_id, "release_id": release["upstream_release_id"], "preflight": preflight}
    saved_profile = catalog.profile(partition_id)
    if saved_profile is None:
        profile = import_profile_payload(effective_config)
        profile_fingerprint = _fingerprint(profile)
        # A one-run modern selection changes only this release derivation; it
        # must not create or overwrite the saved context profile.
        if not (dry_run or validation_only or selection_policy is not None):
            profile_fingerprint = catalog.save_profile(partition_id, profile)
    else:
        profile = saved_profile["profile"]
        profile_fingerprint = str(saved_profile["profile_fingerprint"])
    modern_selection_policy = selection_policy
    if modern_selection_policy is None and isinstance(profile.get("selection_policy"), Mapping):
        modern_selection_policy = dict(profile["selection_policy"])
    if modern_selection_policy is not None:
        from chroma_db_import.workflow.models import SelectionPolicy

        modern_selection_policy = SelectionPolicy.from_mapping(modern_selection_policy).as_dict()
        if selection_policy is not None:
            profile_for_run = dict(profile)
            profile_for_run["selection_policy"] = modern_selection_policy
            profile_fingerprint = _fingerprint(profile_for_run)
    effective_config = resolve_managed_config(effective_config, {"profile": profile, "profile_fingerprint": profile_fingerprint})
    dedup_policy = resolve_dedup_policy(effective_config.dedup_policy, default_profile="off")
    if progress_callback is not None:
        progress_callback("Checking duplicate and partition identities...", 0, len(cache_paths))
    dedup_inputs = load_managed_dedup_inputs(
        cache_paths,
        identity,
        upstream,
        representation_spec(effective_config),
        selected_speakers=effective_config.selected_speakers,
        selection_policy=modern_selection_policy,
        progress_callback=progress_callback,
    )
    if progress_callback is not None:
        progress_callback("Building the release deduplication plan...", 0, 0)
    dedup_plan = build_exact_plan(
        dedup_inputs.inputs,
        dedup_policy,
        representation_spec(effective_config),
        excluded_count=dedup_inputs.excluded_count,
        excluded_reasons=dedup_inputs.excluded_reasons,
        input_count=dedup_inputs.excluded_count + len(dedup_inputs.inputs),
    )
    downstream_release_id = derive_downstream_release_id(release["upstream_release_id"], profile_fingerprint)
    output_root = output_root or Path(catalog.get_setting("managed_output_root", str(project_dir / "exports")))
    paths = managed_paths(output_root, partition_id, downstream_release_id)
    if dry_run or validation_only:
        if progress_callback is not None:
            progress_callback("Import preview ready", 1, 1)
        return {
            "status": "validated",
            "partition_id": partition_id,
            "corpus_id": identity.corpus_id,
            "upstream_release_id": release["upstream_release_id"],
            "downstream_release_id": downstream_release_id,
            "cache_files": [str(path) for path in cache_paths],
            "preflight": preflight,
            "import_profile_fingerprint": profile_fingerprint,
            "dedup": dedup_plan.as_counts(),
            "record_ids": dedup_plan.stored_ids,
            "plan_fingerprint": dedup_plan.plan_fingerprint,
        }
    output_root = output_root or Path(catalog.get_setting("managed_output_root", str(project_dir / "exports")))
    run_id = f"managed_{uuid.uuid4().hex}"
    try:
        lock = ManagedPartitionLock(paths["partition_root"], run_id=run_id).acquire()
    except ManagedPartitionBusy as exc:
        catalog.record_downstream_export(partition_id, downstream_release_id, release["upstream_release_id"], profile_fingerprint, "busy", {"error": str(exc)})
        raise ManagedContextError(str(exc)) from exc
    try:
        if paths["export_root"].exists():
            if _managed_release_is_reusable(paths["export_root"], identity, upstream, release["upstream_release_id"], downstream_release_id, profile_fingerprint, dedup_plan):
                catalog.update_release(partition_id, release["upstream_release_id"], status="imported", downstream_release_id=downstream_release_id, imported_at=_now())
                catalog.set_active_release(output_root, partition_id, downstream_release_id, corpus_id=identity.corpus_id)
                catalog.record_run(run_id, partition_id, release["upstream_release_id"], downstream_release_id, "reused", {"export": str(paths["export_root"]), "embedding_work": 0}, completed=True)
                catalog.record_downstream_export(partition_id, downstream_release_id, release["upstream_release_id"], profile_fingerprint, "reused", {"export": str(paths["export_root"]), "embedding_work": 0, "dedup": dedup_plan.as_counts()})
                return {"status": "reused", "partition_id": partition_id, "corpus_id": identity.corpus_id, "upstream_release_id": release["upstream_release_id"], "downstream_release_id": downstream_release_id, "export": str(paths["export_root"]), "preflight": preflight, "embedding_work": 0, "dedup": dedup_plan.as_counts()}
            raise ManagedContextError(f"managed release already exists but failed identity validation: {downstream_release_id}")
        paths["staging_root"].mkdir(parents=True, exist_ok=True)
        stage = paths["staging_root"] / f"{downstream_release_id}-{uuid.uuid4().hex[:8]}"
        stage_export = stage / "export"
        managed_config = replace(effective_config)
        managed_config.processed_data_dir = str(cache_paths[0].parent)
        managed_config.persist_dir = str(stage_export)
        managed_config.storage_path_isolated = True
        managed_config.collection_name = COLLECTION_NAME
        managed_config.portable_artifacts = True
        managed_config.managed_partition_identity = identity.as_dict()
        managed_config.upstream_release_id = release["upstream_release_id"]
        managed_config.handoff_ids = [str(item) for item in upstream.get("handoff_ids") or []]
        managed_config.state_path = str(stage / "state.json")
        managed_config.preflight_report_path = str(stage / "preflight_report.json")
        managed_config.import_state_dir = str(stage / "import_batches")
        managed_config.embedding_cache_dir = str(paths["partition_root"] / "embedding_cache")
        managed_config.manifest_path = "import_manifest.json"
        catalog.record_run(run_id, partition_id, release["upstream_release_id"], downstream_release_id, "processing", {"cache_files": [str(path) for path in cache_paths]})
        dedup_integration_supported = True
        try:
            from chroma_db_import.cli import run_import
            source_fingerprints_before = {path: content_fingerprint(path) for path in cache_paths}

            if progress_callback is not None:
                progress_callback("Opening the embedding model and staged database...", 0, len(cache_paths))
            import_kwargs = {
                "selected_files": cache_paths,
                "write_snapshot": False,
                "dedup_plan": dedup_plan,
            }
            if progress_callback is not None:
                import_kwargs["progress_callback"] = progress_callback
            import_status = run_import(managed_config, project_dir, False, **import_kwargs)
            if import_status not in (0, None):
                raise ManagedContextError(f"managed import stopped before complete coverage (status {import_status})")
            if any(content_fingerprint(path) != fingerprint for path, fingerprint in source_fingerprints_before.items()):
                raise ManagedContextError("producer cache changed during managed import; staged release was not promoted")
            manifest_path = stage_export / "import_manifest.json"
            if progress_callback is not None:
                progress_callback("Validating the staged Chroma export...", len(cache_paths), len(cache_paths))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            _stamp_collection_identity(stage_export, identity, release["upstream_release_id"], downstream_release_id, profile_fingerprint, dedup_plan)
            _write_managed_metadata(stage_export, identity, upstream, profile_fingerprint, cache_paths, manifest, upstream_release_id=release["upstream_release_id"], downstream_release_id=downstream_release_id, dedup_plan=dedup_plan if dedup_integration_supported else None)
            _stamp_collection_identity(stage_export, identity, release["upstream_release_id"], downstream_release_id, profile_fingerprint, dedup_plan)
            dedup_reference = write_dedup_artifacts(
                stage_export,
                dedup_plan,
                {
                    "release_id": downstream_release_id,
                    "upstream_release_id": release["upstream_release_id"],
                    "partition_id": identity.partition_id,
                    "corpus_id": identity.corpus_id,
                    "partition_display_name": identity.display_name,
                    "context_type": identity.context_type,
                    "workflow_profile": identity.workflow_profile,
                    "partition_config_fingerprint": identity.partition_config_fingerprint,
                    "handoff_ids": list(upstream.get("handoff_ids") or []),
                    "representation_id": manifest.get("representation_id") or (manifest.get("representation") or {}).get("representation_id") or representation_spec(managed_config).representation_id,
                },
                {"dedup_plan": dedup_plan.as_counts()},
            ) if dedup_integration_supported else {"enabled": False}
            if dedup_reference.get("enabled"):
                staged_release = json.loads((stage_export / "release.json").read_text(encoding="utf-8"))
                validate_dedup_artifacts(stage_export, staged_release)
            target = paths["export_root"]
            if target.exists():
                existing_release = target / "release.json"
                if existing_release.is_file() and existing_release.read_bytes() == (stage_export / "release.json").read_bytes():
                    shutil.rmtree(stage, ignore_errors=True)
                else:
                    raise ManagedContextError(f"managed release already exists with different content: {downstream_release_id}")
            else:
                if progress_callback is not None:
                    progress_callback("Promoting the validated database release...", 1, 1)
                temporary_target = paths["release_root"].with_name(paths["release_root"].name + f".{uuid.uuid4().hex}.tmp")
                temporary_target.parent.mkdir(parents=True, exist_ok=True)
                (temporary_target / "export").mkdir(parents=True, exist_ok=True)
                shutil.copytree(stage_export, temporary_target / "export", dirs_exist_ok=True)
                temporary_target.replace(paths["release_root"])
            catalog.update_release(partition_id, release["upstream_release_id"], status="imported", downstream_release_id=downstream_release_id, imported_at=_now())
            catalog.set_active_release(output_root, partition_id, downstream_release_id, corpus_id=identity.corpus_id)
            catalog.record_downstream_export(partition_id, downstream_release_id, release["upstream_release_id"], profile_fingerprint, "completed", {"export": str(paths["export_root"]), "dedup": dedup_plan.as_counts(), "dedup_reference": dedup_reference})
            catalog.record_run(run_id, partition_id, release["upstream_release_id"], downstream_release_id, "completed", {"export": str(paths["export_root"])}, completed=True)
            shutil.rmtree(stage, ignore_errors=True)
            return {"status": "completed", "partition_id": partition_id, "corpus_id": identity.corpus_id, "upstream_release_id": release["upstream_release_id"], "downstream_release_id": downstream_release_id, "export": str(paths["export_root"]), "preflight": preflight, "dedup": dedup_plan.as_counts(), "plan_fingerprint": dedup_plan.plan_fingerprint}
        except Exception as exc:
            catalog.update_release(partition_id, release["upstream_release_id"], status="failed")
            catalog.record_run(run_id, partition_id, release["upstream_release_id"], downstream_release_id, "failed", {"error": f"{type(exc).__name__}: {exc}"}, completed=True)
            raise
    finally:
        lock.release()


def _catalog_path(value: str | None, project_dir: Path) -> Path:
    return Path(value).expanduser() if value else project_dir / "state" / "context_catalog.sqlite3"


def _project_dir_from_config(value: str | None) -> tuple[Path, ImportConfig]:
    if not value:
        return Path.cwd(), ImportConfig()
    config_path = Path(value).expanduser()
    return (config_path.resolve().parent if config_path.exists() else Path.cwd()), load_config(config_path)


def managed_main(argv: list[str]) -> int:
    """CLI for the no-JSON-edit managed context workflow."""
    parser = argparse.ArgumentParser(prog="chroma-db-import")
    subparsers = parser.add_subparsers(dest="command", required=True)

    contexts = subparsers.add_parser("contexts", help="Discover and manage independent retrieval contexts")
    context_commands = contexts.add_subparsers(dest="context_command", required=True)

    def add_catalog(command: argparse.ArgumentParser) -> None:
        command.add_argument("--catalog", help="Private local catalog path")

    link = context_commands.add_parser("link-source", help="Link a Podcast-RAG project or release root")
    link.add_argument("--root", required=True)
    link.add_argument("--output-root")
    add_catalog(link)

    discover_command = context_commands.add_parser("discover", help="Discover contract-valid handoffs and releases")
    discover_command.add_argument("--root", action="append", help="Additional source root; may be repeated")
    add_catalog(discover_command)

    list_command = context_commands.add_parser("list", help="List discovered contexts")
    add_catalog(list_command)

    show_command = context_commands.add_parser("show", help="Show a context and its releases")
    show_command.add_argument("--partition", required=True)
    add_catalog(show_command)

    archive = context_commands.add_parser("archive", help="Hide a context locally without deleting its database")
    archive.add_argument("--partition", required=True)
    add_catalog(archive)

    unarchive = context_commands.add_parser("unarchive", help="Restore a locally archived context")
    unarchive.add_argument("--partition", required=True)
    add_catalog(unarchive)

    legacy = context_commands.add_parser("adopt-legacy", help="Explicitly register a legacy folder")
    legacy.add_argument("--id", required=True)
    legacy.add_argument("--source", required=True)
    legacy.add_argument("--name")
    add_catalog(legacy)

    set_dedup = context_commands.add_parser("set-dedup", help="Save the deduplication profile for one context")
    set_dedup.add_argument("--partition", required=True)
    set_dedup.add_argument("--profile", choices=("off", "safe", "audit"))
    set_dedup.add_argument("--near-enabled", choices=("true", "false"), default=None)
    set_dedup.add_argument("--near-threshold", "--near-jaccard-threshold", dest="near_jaccard_threshold", type=float)
    set_dedup.add_argument("--length-ratio", "--near-length-ratio", dest="near_length_ratio", type=float)
    set_dedup.add_argument("--near-max-block-records", type=int)
    set_dedup.add_argument("--retrieval-enabled", choices=("true", "false"), default=None)
    add_catalog(set_dedup)

    review_dedup = context_commands.add_parser("review-dedup", help="Review the validated dedup ledger for one context")
    review_dedup.add_argument("--partition", required=True)
    review_dedup.add_argument("--downstream-release")
    review_dedup.add_argument("--output-root")
    add_catalog(review_dedup)

    repair_lock = context_commands.add_parser("repair-lock", help="Recover a stuck partition lock without importing")
    repair_lock.add_argument("--partition", required=True)
    repair_lock.add_argument("--output-root", required=True)
    repair_lock.add_argument("--confirm", action="store_true", help="Permit stopping the verified stale importer owner")
    repair_lock.add_argument("--grace-timeout", type=float, default=10.0)

    import_command = subparsers.add_parser("import", help="Import the newest valid release for one context")
    import_command.add_argument("--partition", required=True)
    import_command.add_argument("--release")
    import_command.add_argument("--config")
    import_command.add_argument("--catalog")
    import_command.add_argument("--output-root")
    import_command.add_argument("--dry-run", action="store_true")
    import_command.add_argument("--validation-only", action="store_true")

    status = subparsers.add_parser("status", help="Show context, release, and active database status")
    status.add_argument("--partition", required=True)
    status.add_argument("--catalog")

    args = parser.parse_args(argv)
    if args.command == "contexts" and args.context_command == "repair-lock":
        partition_id = _safe_id(args.partition, "partition")
        partition_root = Path(args.output_root).expanduser().resolve() / "partitions" / partition_id
        result = repair_partition_lock(partition_root, confirm=args.confirm, grace_timeout=args.grace_timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") in {"already_available", "released", "owner_terminated"} else 1
    project_dir, config = _project_dir_from_config(getattr(args, "config", None))
    catalog_path = _catalog_path(getattr(args, "catalog", None), project_dir)
    with ManagedCatalog(catalog_path) as catalog:
        if args.command == "contexts":
            if args.context_command == "link-source":
                root = catalog.add_source_root(Path(args.root))
                if args.output_root:
                    catalog.set_setting("managed_output_root", str(Path(args.output_root).expanduser().resolve()))
                print(json.dumps({"linked_root": str(root), "catalog": str(catalog.path)}, indent=2))
                return 0
            if args.context_command == "discover":
                report = discover(catalog, [Path(root) for root in (args.root or [])])
                for root in catalog.source_roots():
                    catalog.connection.execute("UPDATE source_roots SET last_scan_at=? WHERE root=?", (_now(), str(root)))
                catalog.connection.commit()
                print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
                return 0 if not report["invalid"] else 1
            if args.context_command == "list":
                print(json.dumps({"catalog": str(catalog.path), "contexts": catalog.contexts()}, ensure_ascii=False, indent=2, default=str))
                return 0
            if args.context_command == "show":
                context = catalog.context(args.partition)
                if not context:
                    raise ManagedContextError(f"unknown partition: {args.partition}")
                print(json.dumps({"context": context, "releases": catalog.releases(args.partition)}, ensure_ascii=False, indent=2, default=str))
                return 0
            if args.context_command in {"archive", "unarchive"}:
                catalog.set_local_status(args.partition, "archived" if args.context_command == "archive" else "active")
                print(json.dumps(catalog.context(args.partition), ensure_ascii=False, indent=2, default=str))
                return 0
            if args.context_command == "adopt-legacy":
                print(json.dumps(adopt_legacy(catalog, args.id, Path(args.source), args.name), ensure_ascii=False, indent=2, default=str))
                return 0
            if args.context_command == "set-dedup":
                context = catalog.context(args.partition)
                if not context:
                    raise ManagedContextError(f"unknown partition: {args.partition}")
                saved = catalog.profile(args.partition)
                profile = dict((saved or {}).get("profile") or import_profile_payload(ImportConfig()))
                policy = dict(resolve_dedup_policy(profile.get("dedup_policy"), default_profile="safe"))
                if args.profile is not None:
                    policy["profile"] = args.profile
                for argument, field_name in ((None if args.near_enabled is None else args.near_enabled == "true", "near_enabled"), (args.near_jaccard_threshold, "near_jaccard_threshold"), (args.near_length_ratio, "near_length_ratio"), (args.near_max_block_records, "near_max_block_records")):
                    if argument is not None:
                        policy[field_name] = argument
                if args.retrieval_enabled is not None:
                    policy["retrieval"] = {**dict(policy.get("retrieval") or {}), "enabled": args.retrieval_enabled == "true"}
                policy = resolve_dedup_policy(policy, default_profile="safe")
                profile["dedup_policy"] = policy
                fingerprint = catalog.save_profile(args.partition, profile)
                print(json.dumps({"partition_id": args.partition, "dedup_policy": policy, "profile_fingerprint": fingerprint}, ensure_ascii=False, indent=2))
                return 0
            if args.context_command == "review-dedup":
                context = catalog.context(args.partition)
                if not context:
                    raise ManagedContextError(f"unknown partition: {args.partition}")
                profile = catalog.profile(args.partition)
                policy = resolve_dedup_policy((profile or {}).get("profile", {}).get("dedup_policy"), default_profile="safe")
                if policy["profile"] == "off":
                    print(json.dumps({"partition_id": args.partition, "enabled": False, "reason": "dedup profile is off"}, indent=2))
                    return 0
                output = Path(args.output_root).expanduser().resolve() if args.output_root else Path(catalog.get_setting("managed_output_root", str(project_dir / "exports")))
                partition_root = managed_paths(output, args.partition)["partition_root"]
                pointer_path = partition_root / "active-release.json"
                release_id = args.downstream_release
                if not release_id:
                    if not pointer_path.is_file():
                        raise ManagedContextError("context has no active release")
                    release_id = json.loads(pointer_path.read_text(encoding="utf-8")).get("release_id")
                paths = managed_paths(output, args.partition, str(release_id))
                release_path = paths["export_root"] / "release.json"
                if not release_path.is_file():
                    raise ManagedContextError("requested downstream release is unavailable")
                release = json.loads(release_path.read_text(encoding="utf-8"))
                if not release.get("dedup"):
                    print(json.dumps({"partition_id": args.partition, "release_id": release_id, "enabled": False, "reason": "release has no dedup artifacts"}, indent=2))
                    return 0
                result = validate_dedup_artifacts(paths["export_root"], release)
                print(json.dumps({"partition_id": args.partition, "release_id": release_id, "enabled": True, "counts": result["manifest"].get("counts"), "exact_groups": result["manifest"].get("exact_groups"), "near": result["manifest"].get("near"), "coverage": result["manifest"].get("coverage")}, ensure_ascii=False, indent=2, default=str))
                return 0

        if args.command == "status":
            context = catalog.context(args.partition)
            if not context:
                raise ManagedContextError(f"unknown partition: {args.partition}")
            print(json.dumps({"context": context, "releases": catalog.releases(args.partition), "active_release": (managed_paths(Path(catalog.get_setting("managed_output_root", str(project_dir / "exports"))), args.partition)["partition_root"] / "active-release.json").is_file()}, ensure_ascii=False, indent=2, default=str))
            return 0

        if args.command == "import":
            result = run_managed_import(
                config,
                project_dir,
                catalog,
                args.partition,
                upstream_release_id=args.release,
                output_root=Path(args.output_root).expanduser().resolve() if args.output_root else None,
                dry_run=args.dry_run,
                validation_only=args.validation_only,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0 if result.get("status") in {"validated", "completed", "reused"} else 1

    raise ManagedContextError("unsupported managed command")
