"""Read-only adapter for already-published managed source artifacts.

The Chroma importer may inspect producer manifests, state, and published
release files, but it does not execute or control the producer pipeline.
The producer repository owns both processing and release publication; its
complete-partition menu action performs them as one operator workflow.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from chroma_db_import.managed import ContextIdentity, ManagedContextError, validate_upstream_release


UPSTREAM_RELEASE_CONTRACT = "podcast-rag-corpus-release-v2"
SAFE_RELEASE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class PodcastRagAdapterError(ManagedContextError):
    """Raised when the linked producer cannot be inspected or operated safely."""


@dataclass(frozen=True)
class PipelineStatus:
    source_root: str
    partition_root: str
    partition_id: str
    corpus_id: str
    display_name: str
    latest_handoff_id: str
    declared_episodes: int
    completed: int
    pending: int
    failed: int
    interrupted: int
    quarantined: int
    processed_cache_count: int
    current_handoff_cache_count: int
    release_count: int
    latest_release_id: str
    ready_to_publish: bool
    warnings: tuple[str, ...] = ()

    @property
    def blocking_count(self) -> int:
        return self.pending + self.failed + self.interrupted + self.quarantined

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_root": self.source_root,
            "partition_root": self.partition_root,
            "partition_id": self.partition_id,
            "corpus_id": self.corpus_id,
            "display_name": self.display_name,
            "latest_handoff_id": self.latest_handoff_id,
            "declared_episodes": self.declared_episodes,
            "completed": self.completed,
            "pending": self.pending,
            "failed": self.failed,
            "interrupted": self.interrupted,
            "quarantined": self.quarantined,
            "processed_cache_count": self.processed_cache_count,
            "current_handoff_cache_count": self.current_handoff_cache_count,
            "release_count": self.release_count,
            "latest_release_id": self.latest_release_id,
            "ready_to_publish": self.ready_to_publish,
            "blocking_count": self.blocking_count,
            "warnings": list(self.warnings),
        }


ProgressCallback = Callable[[str], None]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PodcastRagAdapterError(f"could not read producer file {path}: {exc}") from exc


def _text(value: Any) -> str:
    return str(value or "").strip()


def _record_timestamp(record: dict[str, Any]) -> str:
    return max(
        _text(record.get("completed_at")),
        _text(record.get("updated_at")),
        _text(record.get("run_id")),
    )


def _episode_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return _text(payload.get("episode_id") or metadata.get("episode_id"))


class PodcastRagSourceAdapter:
    """Inspect and operate one producer-defined partition."""

    def __init__(self, source_root: Path, partition_id: str) -> None:
        self.source_root = source_root.expanduser().resolve()
        self.partition_id = _text(partition_id)
        if not self.source_root.is_dir():
            raise PodcastRagAdapterError(f"producer source root does not exist: {self.source_root}")
        if not self.partition_id:
            raise PodcastRagAdapterError("producer partition ID is required")

    @property
    def partition_root(self) -> Path:
        candidates = []
        if self.source_root.name == self.partition_id:
            candidates.append(self.source_root)
        candidates.extend(
            [
                self.source_root / "partitions" / self.partition_id,
                self.source_root / self.partition_id,
            ]
        )
        for candidate in candidates:
            if (candidate / "partition.json").is_file():
                return candidate.resolve()
        raise PodcastRagAdapterError(
            f"could not locate partition {self.partition_id} below linked source {self.source_root}"
        )

    def _identity(self) -> ContextIdentity:
        payload = _read_json(self.partition_root / "partition.json")
        identity = ContextIdentity.from_mapping(payload, strict=True)
        if identity.partition_id != self.partition_id:
            raise PodcastRagAdapterError(
                f"partition manifest identifies {identity.partition_id}, not {self.partition_id}"
            )
        return identity

    def _latest_handoff(self) -> tuple[str, Path | None, list[Path]]:
        inbox = self.partition_root / "handoff_inbox"
        packages = [path for path in inbox.iterdir() if path.is_dir()] if inbox.is_dir() else []
        packages.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
        if not packages:
            return "", None, []
        package = packages[0]
        episode_files = sorted(package.glob("episodes/**/reviewed.json"))
        if not episode_files:
            episode_files = sorted(package.glob("episodes/**/*.json"))
        return package.name, package, episode_files

    def _state_records(self) -> list[dict[str, Any]]:
        state_path = self.partition_root / "state" / "podcast_rag_state.json"
        if not state_path.is_file():
            return []
        payload = _read_json(state_path)
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, dict):
            return []
        records: list[dict[str, Any]] = []
        for value in files.values():
            if isinstance(value, dict):
                records.append(dict(value))
        return records

    def _current_cache_fingerprints(self) -> tuple[set[str], set[str]]:
        fingerprints: set[str] = set()
        episode_uids: set[str] = set()
        cache_dir = self.partition_root / "processed_data"
        for path in sorted(cache_dir.glob("*.processed_documents.json")) if cache_dir.is_dir() else []:
            try:
                payload = _read_json(path)
            except PodcastRagAdapterError:
                continue
            fingerprint = _text(payload.get("source_fingerprint") or payload.get("cache_fingerprint"))
            episode_uid = _text(payload.get("episode_uid"))
            if fingerprint:
                fingerprints.add(fingerprint.removeprefix("sha256:"))
            if episode_uid:
                episode_uids.add(episode_uid)
        return fingerprints, episode_uids

    def inspect(self) -> PipelineStatus:
        identity = self._identity()
        handoff_id, _package, episode_files = self._latest_handoff()
        declared_ids: list[str] = []
        warnings: list[str] = []
        for path in episode_files:
            try:
                episode_id = _episode_id(_read_json(path))
            except PodcastRagAdapterError as exc:
                warnings.append(str(exc))
                continue
            if episode_id and episode_id not in declared_ids:
                declared_ids.append(episode_id)
        declared_set = set(declared_ids)
        if not declared_set:
            warnings.append("latest handoff package contains no declared episode records")

        records_by_episode: dict[str, list[dict[str, Any]]] = {}
        for record in self._state_records():
            episode_id = _text(record.get("episode_id"))
            if episode_id in declared_set:
                records_by_episode.setdefault(episode_id, []).append(record)

        completed = pending = failed = interrupted = quarantined = cache_count = 0
        for episode_id in declared_ids:
            records = sorted(records_by_episode.get(episode_id, []), key=_record_timestamp, reverse=True)
            valid_cache = next(
                (
                    record
                    for record in records
                    if _text(record.get("status")) == "completed"
                    and _text(record.get("cache_path"))
                    and Path(_text(record.get("cache_path"))).is_file()
                ),
                None,
            )
            if valid_cache:
                completed += 1
                cache_count += 1
                continue
            status = _text(records[0].get("status")) if records else "pending"
            if status == "failed":
                failed += 1
            elif status == "interrupted":
                interrupted += 1
            elif status == "quarantined":
                quarantined += 1
            else:
                pending += 1

        processed_cache_count = len(list((self.partition_root / "processed_data").glob("*.processed_documents.json"))) if (self.partition_root / "processed_data").is_dir() else 0
        release_paths = sorted((self.partition_root / "releases").glob("*.release.json")) if (self.partition_root / "releases").is_dir() else []
        latest_release_id = ""
        try:
            active_release = self._active_release()
        except PodcastRagAdapterError as exc:
            warnings.append(str(exc))
            active_release = None
        if active_release:
            latest_release_id = _text(active_release.get("release_id"))
        elif release_paths:
            release_candidates: list[tuple[str, str, int]] = []
            for path in release_paths:
                payload = _read_json(path)
                created_at = _text(payload.get("created_at")) if isinstance(payload, dict) else ""
                try:
                    modified_ns = path.stat().st_mtime_ns
                except OSError:
                    modified_ns = 0
                release_candidates.append((created_at, path.name, modified_ns))
            latest_release_id = max(release_candidates, key=lambda item: (item[0], item[2], item[1]))[1].removesuffix(".release.json")
        if processed_cache_count > cache_count:
            warnings.append(
                f"partition contains {processed_cache_count} processed caches; {cache_count} belong to the latest declared handoff"
            )
        if not episode_files:
            warnings.append("no latest handoff episode files were found")
        ready = bool(declared_ids) and pending == failed == interrupted == quarantined == 0 and cache_count == len(declared_ids)
        return PipelineStatus(
            source_root=str(self.source_root),
            partition_root=str(self.partition_root),
            partition_id=identity.partition_id,
            corpus_id=identity.corpus_id,
            display_name=identity.display_name,
            latest_handoff_id=handoff_id,
            declared_episodes=len(declared_ids),
            completed=completed,
            pending=pending,
            failed=failed,
            interrupted=interrupted,
            quarantined=quarantined,
            processed_cache_count=processed_cache_count,
            current_handoff_cache_count=cache_count,
            release_count=len(release_paths),
            latest_release_id=latest_release_id,
            ready_to_publish=ready,
            warnings=tuple(warnings),
        )

    def _release_id(self) -> str:
        cache_dir = self.partition_root / "processed_data"
        signature: list[dict[str, Any]] = []
        for path in sorted(cache_dir.glob("*.processed_documents.json")) if cache_dir.is_dir() else []:
            try:
                stat = path.stat()
                payload = _read_json(path)
                signature.append(
                    {
                        "name": path.name,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "source_fingerprint": payload.get("source_fingerprint") or payload.get("cache_fingerprint"),
                        "episode_uid": payload.get("episode_uid"),
                    }
                )
            except (OSError, PodcastRagAdapterError):
                signature.append({"name": path.name})
        digest = hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:10]
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        value = f"release_{stamp}_{digest}"
        if not SAFE_RELEASE_ID.fullmatch(value):
            raise PodcastRagAdapterError("generated producer release ID is invalid")
        return value

    def _existing_release_for_current_caches(self) -> dict[str, Any] | None:
        cache_fingerprints, episode_uids = self._current_cache_fingerprints()
        if not cache_fingerprints or not episode_uids:
            return None
        release_dir = self.partition_root / "releases"
        paths = sorted(release_dir.glob("*.release.json"), reverse=True) if release_dir.is_dir() else []
        for path in paths:
            try:
                payload = _read_json(path)
                validate_upstream_release(payload)
            except PodcastRagAdapterError:
                continue
            except ManagedContextError:
                continue
            release_fingerprints = {
                _text(value).removeprefix("sha256:")
                for value in payload.get("processed_cache_fingerprints") or []
                if _text(value)
            }
            release_episodes = {_text(value) for value in payload.get("episode_uids") or [] if _text(value)}
            if release_fingerprints == cache_fingerprints and release_episodes == episode_uids:
                return {"release_id": _text(payload.get("release_id")), "release_path": str(path), "payload": payload, "reused": True}
        return None

    def _active_release(self) -> dict[str, Any] | None:
        pointer_path = self.partition_root / "active-release.json"
        if not pointer_path.is_file():
            return None
        pointer = _read_json(pointer_path)
        if pointer.get("pointer_contract_version") != "podcast-rag-active-release-v1":
            raise PodcastRagAdapterError("producer active-release pointer uses an unsupported contract")
        release_path = (self.partition_root / _text(pointer.get("release_path"))).resolve()
        try:
            release_path.relative_to(self.partition_root.resolve())
        except ValueError as exc:
            raise PodcastRagAdapterError("producer active-release pointer escapes the partition root") from exc
        if not release_path.is_file():
            raise PodcastRagAdapterError("producer active-release pointer references a missing release manifest")
        payload = _read_json(release_path)
        identity = validate_upstream_release(payload)
        if _text(pointer.get("release_id")) != _text(payload.get("release_id")):
            raise PodcastRagAdapterError("producer active-release pointer does not match its release manifest")
        if _text(pointer.get("partition_id")) != identity.partition_id or _text(pointer.get("corpus_id")) != identity.corpus_id or identity.partition_id != self.partition_id:
            raise PodcastRagAdapterError("producer active-release pointer identity is invalid")
        expected_hash = _text(pointer.get("release_sha256")).removeprefix("sha256:")
        actual_hash = hashlib.sha256(release_path.read_bytes()).hexdigest()
        if not expected_hash or expected_hash != actual_hash:
            raise PodcastRagAdapterError("producer active-release pointer manifest hash does not match")
        return {"release_id": _text(payload.get("release_id")), "release_path": str(release_path), "payload": payload, "reused": True, "active": True}

    def publish_release(self, callback: ProgressCallback | None = None, *, release_id: str | None = None) -> dict[str, Any]:
        status = self.inspect()
        if not status.ready_to_publish:
            raise PodcastRagAdapterError(
                "producer partition is not ready to publish: "
                f"pending={status.pending}, failed={status.failed}, interrupted={status.interrupted}, "
                f"quarantined={status.quarantined}"
            )
        active = self._active_release()
        if active:
            current_fingerprints, current_episode_uids = self._current_cache_fingerprints()
            release_fingerprints = {
                _text(value).removeprefix("sha256:")
                for value in active["payload"].get("processed_cache_fingerprints") or []
                if _text(value)
            }
            release_episode_uids = {
                _text(value)
                for value in active["payload"].get("episode_uids") or []
                if _text(value)
            }
            if release_fingerprints == current_fingerprints and release_episode_uids == current_episode_uids:
                if callback:
                    callback(f"Using active producer release {active['release_id']}.")
                return active
        existing = self._existing_release_for_current_caches()
        if existing:
            if callback:
                callback(f"Reusing existing producer release {existing['release_id']}.")
            return existing
        raise PodcastRagAdapterError(
            "no matching published producer release was found; "
            "use the producer's Process / resume all pending work menu action before Chroma import"
        )


__all__ = ["PipelineStatus", "PodcastRagAdapterError", "PodcastRagSourceAdapter", "UPSTREAM_RELEASE_CONTRACT"]
