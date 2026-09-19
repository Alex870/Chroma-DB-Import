from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .models import DatabaseRecord, ExecutionOptions, FrozenPreview, JobRecord, BridgeError, new_id, stable_hash, utc_now


class CatalogError(RuntimeError):
    pass


SCHEMA_VERSION = 5


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_target(value: str | Path) -> str:
    path = Path(value).expanduser()
    try:
        path = path.resolve(strict=False)
    except OSError:
        path = Path(os.path.abspath(os.fspath(path)))
    return os.path.normcase(os.path.normpath(os.fspath(path))).casefold()


class AppCatalog:
    """Thread-safe SQLite catalog; connections are owned by each operation."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS databases (
                    id TEXT PRIMARY KEY, display_name TEXT NOT NULL, source_kind TEXT NOT NULL,
                    source_ref_json TEXT NOT NULL, target_json TEXT NOT NULL,
                    downstream_identity_json TEXT, selection_policy_json TEXT NOT NULL,
                    execution_options_json TEXT NOT NULL DEFAULT '{"embedding_device":"auto"}',
                    settings_revision INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
                    last_check_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    target_key TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY, database_id TEXT, revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS previews (
                    id TEXT PRIMARY KEY, database_id TEXT, draft_id TEXT, operation TEXT NOT NULL,
                    status TEXT NOT NULL, settings_revision INTEGER NOT NULL, preview_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL,
                    database_id TEXT, preview_id TEXT, payload_json TEXT NOT NULL,
                    result_json TEXT, error_json TEXT, progress_json TEXT NOT NULL DEFAULT '{}', can_cancel INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS jobs_preview_unique
                    ON jobs(preview_id) WHERE preview_id IS NOT NULL AND kind='import';
                CREATE TABLE IF NOT EXISTS job_events (
                    job_id TEXT NOT NULL, sequence INTEGER NOT NULL, stage TEXT NOT NULL,
                    message TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS activation_journal (
                    operation_id TEXT PRIMARY KEY, target_path TEXT NOT NULL, stage_path TEXT NOT NULL,
                    backup_path TEXT, state TEXT NOT NULL, updated_at TEXT NOT NULL, detail_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY, revision INTEGER NOT NULL, value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_connections (
                    id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE, catalog_path TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS source_observations (
                    connection_id TEXT NOT NULL, partition_id TEXT NOT NULL, upstream_release_id TEXT NOT NULL,
                    source_root TEXT NOT NULL, corpus_id TEXT NOT NULL, status TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0, snapshot_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL, PRIMARY KEY(connection_id, partition_id, upstream_release_id)
                );
                CREATE INDEX IF NOT EXISTS source_observations_context_idx
                    ON source_observations(connection_id, partition_id, observed_at DESC);
                CREATE TABLE IF NOT EXISTS database_links (
                    database_id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, partition_id TEXT NOT NULL,
                    corpus_id TEXT NOT NULL, source_root TEXT NOT NULL, target_key TEXT NOT NULL,
                    profile_fingerprint TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL,
                    state TEXT NOT NULL, last_source_release_id TEXT,
                    last_downstream_release_id TEXT, selected INTEGER NOT NULL DEFAULT 0,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS database_links_context_idx
                    ON database_links(connection_id, partition_id, state);
                CREATE TABLE IF NOT EXISTS database_release_history (
                    id TEXT PRIMARY KEY, database_id TEXT NOT NULL, connection_id TEXT NOT NULL,
                    partition_id TEXT NOT NULL, upstream_release_id TEXT,
                    downstream_release_id TEXT, operation TEXT NOT NULL, status TEXT NOT NULL,
                    detail_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS database_release_history_db_idx
                    ON database_release_history(database_id, created_at DESC);
                """
            )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(databases)").fetchall()}
            if "execution_options_json" not in columns:
                connection.execute("ALTER TABLE databases ADD COLUMN execution_options_json TEXT NOT NULL DEFAULT '{\"embedding_device\":\"auto\"}'")
            job_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
            if "progress_json" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN progress_json TEXT NOT NULL DEFAULT '{}'")
            database_link_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(database_links)").fetchall()}
            if "selected" not in database_link_columns:
                connection.execute("ALTER TABLE database_links ADD COLUMN selected INTEGER NOT NULL DEFAULT 0")
            row = connection.execute("SELECT value FROM catalog_meta WHERE key='schema_version'").fetchone()
            current = int(row[0]) if row and str(row[0]).isdigit() else 0
            if current > SCHEMA_VERSION:
                raise CatalogError(f"GUI catalog schema {current} is newer than supported schema {SCHEMA_VERSION}")
            connection.execute("INSERT OR REPLACE INTO catalog_meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
            connection.commit()

    @staticmethod
    def _record(row: sqlite3.Row) -> DatabaseRecord:
        return DatabaseRecord.from_mapping({
            "id": row["id"], "display_name": row["display_name"], "source_kind": row["source_kind"],
            "source_ref": json.loads(row["source_ref_json"]), "target": json.loads(row["target_json"]),
            "downstream_identity": json.loads(row["downstream_identity_json"]) if row["downstream_identity_json"] else None,
            "selection_policy": json.loads(row["selection_policy_json"]), "settings_revision": row["settings_revision"],
            "execution_options": json.loads(row["execution_options_json"]) if row["execution_options_json"] else ExecutionOptions().as_dict(),
            "archived": bool(row["archived"]), "last_check": json.loads(row["last_check_json"]) if row["last_check_json"] else None,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })

    def list_databases(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        with self._connection() as connection:
            query = "SELECT * FROM databases" + ("" if include_archived else " WHERE archived=0") + " ORDER BY lower(display_name), id"
            return [self._record(row).as_dict() for row in connection.execute(query).fetchall()]

    def get_database(self, database_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM databases WHERE id=?", (str(database_id),)).fetchone()
        if not row:
            raise BridgeError("IDENTITY_UNRESOLVED", f"Database {database_id} is not registered.")
        return self._record(row).as_dict()

    def get_database_by_target(self, target: str, *, include_archived: bool = True) -> dict[str, Any] | None:
        """Return the library entry that owns a normalized destination, if any."""
        target_key = normalize_target(target)
        with self._connection() as connection:
            query = "SELECT * FROM databases WHERE target_key=?"
            values: tuple[Any, ...] = (target_key,)
            if not include_archived:
                query += " AND archived=0"
            row = connection.execute(query, values).fetchone()
        return self._record(row).as_dict() if row else None

    def create_database(self, value: Mapping[str, Any]) -> dict[str, Any]:
        record = DatabaseRecord.from_mapping(value)
        record.created_at = record.updated_at = utc_now()
        target_key = normalize_target(record.target["path"])
        with self._lock, self._connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO databases(id,display_name,source_kind,source_ref_json,target_json,downstream_identity_json,selection_policy_json,execution_options_json,settings_revision,archived,last_check_json,created_at,updated_at,target_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (record.id, record.display_name, record.source_kind, _json(record.source_ref), _json(record.target),
                     _json(record.downstream_identity) if record.downstream_identity else None, _json(record.selection_policy), _json(record.execution_options),
                     record.settings_revision, int(record.archived), _json(record.last_check) if record.last_check else None,
                     record.created_at, record.updated_at, target_key),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                raise BridgeError("TARGET_EXISTS", "Another library entry already uses this database destination.", details_id=target_key) from exc
        return record.as_dict()

    def update_database(self, database_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        current = self.get_database(database_id)
        merged = dict(current)
        for key, value in changes.items():
            if key in {"id", "created_at"}:
                continue
            merged[key] = value
        merged["updated_at"] = utc_now()
        record = DatabaseRecord.from_mapping(merged)
        target_key = normalize_target(record.target["path"])
        with self._lock, self._connection() as connection:
            try:
                connection.execute(
                    "UPDATE databases SET display_name=?,source_kind=?,source_ref_json=?,target_json=?,downstream_identity_json=?,selection_policy_json=?,execution_options_json=?,settings_revision=?,archived=?,last_check_json=?,updated_at=?,target_key=? WHERE id=?",
                    (record.display_name, record.source_kind, _json(record.source_ref), _json(record.target),
                     _json(record.downstream_identity) if record.downstream_identity else None, _json(record.selection_policy), _json(record.execution_options),
                     record.settings_revision, int(record.archived), _json(record.last_check) if record.last_check else None,
                     record.updated_at, target_key, record.id),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                raise BridgeError("TARGET_EXISTS", "Another library entry already uses this database destination.") from exc
        return record.as_dict()

    def archive_database(self, database_id: str) -> dict[str, Any]:
        return self.update_database(database_id, {"archived": True})

    def restore_database(self, database_id: str) -> dict[str, Any]:
        """Restore local visibility only; never touch the registered target."""
        return self.update_database(database_id, {"archived": False})

    def get_app_setting(self, key: str, default: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM app_settings WHERE key=?", (key,)).fetchone()
        if not row:
            return {"key": key, "revision": 0, "value": dict(default or {}), "updated_at": None}
        return {"key": row["key"], "revision": int(row["revision"]), "value": json.loads(row["value_json"]), "updated_at": row["updated_at"]}

    def save_app_setting(self, key: str, value: Mapping[str, Any], *, base_revision: int | None = None) -> dict[str, Any]:
        current = self.get_app_setting(key)
        if base_revision is not None and int(base_revision) != int(current["revision"]):
            raise BridgeError("STALE_SETTINGS", "Application defaults changed in another window. Reload before saving.", field="base_revision")
        revision = int(current["revision"]) + 1
        updated_at = utc_now()
        with self._lock, self._connection() as connection:
            connection.execute("INSERT OR REPLACE INTO app_settings(key,revision,value_json,updated_at) VALUES(?,?,?,?)", (key, revision, _json(dict(value)), updated_at))
            connection.commit()
        return {"key": key, "revision": revision, "value": dict(value), "updated_at": updated_at}

    def list_source_connections(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        with self._connection() as connection:
            query = "SELECT * FROM source_connections" + ("" if include_archived else " WHERE archived=0") + " ORDER BY lower(root), id"
            rows = connection.execute(query).fetchall()
        return [dict(row) for row in rows]

    def save_source_connection(self, root: str, catalog_path: str, *, connection_id: str | None = None) -> dict[str, Any]:
        resolved_root = str(Path(root).expanduser().resolve())
        resolved_catalog = str(Path(catalog_path).expanduser().resolve())
        now = utc_now()
        current = next((item for item in self.list_source_connections(include_archived=True) if normalize_target(item["root"]) == normalize_target(resolved_root)), None)
        identifier = connection_id or str(current["id"] if current else new_id("connection"))
        with self._lock, self._connection() as connection:
            try:
                connection.execute("INSERT INTO source_connections(id,root,catalog_path,created_at,updated_at,archived) VALUES(?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET root=excluded.root,catalog_path=excluded.catalog_path,updated_at=excluded.updated_at,archived=0", (identifier, resolved_root, resolved_catalog, str(current["created_at"] if current else now), now))
                connection.commit()
            except sqlite3.IntegrityError as exc:
                raise BridgeError("SOURCE_CONFLICT", "Another source connection already owns this root.") from exc
        return next(item for item in self.list_source_connections(include_archived=True) if item["id"] == identifier)

    def archive_source_connection(self, connection_id: str, archived: bool = True) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM source_connections WHERE id=?", (connection_id,)).fetchone()
            if not row:
                raise BridgeError("IDENTITY_UNRESOLVED", f"Source connection {connection_id} is not registered.")
            connection.execute("UPDATE source_connections SET archived=?,updated_at=? WHERE id=?", (int(archived), utc_now(), connection_id))
            connection.commit()
        return next(item for item in self.list_source_connections(include_archived=True) if item["id"] == connection_id)

    def save_source_observation(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Persist the latest read-only observation of one upstream release."""
        connection_id = str(value.get("connection_id") or "").strip()
        partition_id = str(value.get("partition_id") or "").strip()
        release_id = str(value.get("upstream_release_id") or "").strip()
        if not connection_id or not partition_id or not release_id:
            raise BridgeError("VALIDATION_FAILED", "Source observation identity is incomplete.")
        observed_at = str(value.get("observed_at") or utc_now())
        snapshot = dict(value.get("snapshot") or {})
        if value.get("error") and "error" not in snapshot:
            snapshot["error"] = str(value.get("error"))
        with self._lock, self._connection() as connection:
            connection.execute(
                """INSERT INTO source_observations(
                    connection_id, partition_id, upstream_release_id, source_root, corpus_id,
                    status, active, snapshot_json, observed_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(connection_id, partition_id, upstream_release_id) DO UPDATE SET
                    source_root=excluded.source_root, corpus_id=excluded.corpus_id,
                    status=excluded.status, active=excluded.active,
                    snapshot_json=excluded.snapshot_json, observed_at=excluded.observed_at""",
                (
                    connection_id, partition_id, release_id,
                    str(value.get("source_root") or ""), str(value.get("corpus_id") or ""),
                    str(value.get("status") or "unknown"), int(bool(value.get("active"))),
                    _json(snapshot), observed_at,
                ),
            )
            connection.commit()
        return {
            "connection_id": connection_id,
            "partition_id": partition_id,
            "upstream_release_id": release_id,
            "source_root": str(value.get("source_root") or ""),
            "corpus_id": str(value.get("corpus_id") or ""),
            "status": str(value.get("status") or "unknown"),
            "active": bool(value.get("active")),
            "source_fingerprint": snapshot.get("source_fingerprint"),
            "readiness": snapshot.get("context_status") or {},
            "error": snapshot.get("error"),
            "snapshot": snapshot,
            "observed_at": observed_at,
        }

    def list_source_observations(self, *, connection_id: str | None = None, partition_id: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if connection_id:
            clauses.append("connection_id=?")
            values.append(str(connection_id))
        if partition_id:
            clauses.append("partition_id=?")
            values.append(str(partition_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM source_observations{where} ORDER BY observed_at DESC, upstream_release_id DESC",
                values,
            ).fetchall()
        return [
            {
                "connection_id": row["connection_id"], "partition_id": row["partition_id"],
                "upstream_release_id": row["upstream_release_id"], "source_root": row["source_root"],
                "corpus_id": row["corpus_id"], "status": row["status"], "active": bool(row["active"]),
                "source_fingerprint": json.loads(row["snapshot_json"]).get("source_fingerprint"),
                "readiness": json.loads(row["snapshot_json"]).get("context_status") or {},
                "error": json.loads(row["snapshot_json"]).get("error"),
                "snapshot": json.loads(row["snapshot_json"]), "observed_at": row["observed_at"],
            }
            for row in rows
        ]

    def save_database_link(self, value: Mapping[str, Any]) -> dict[str, Any]:
        database_id = str(value.get("database_id") or "").strip()
        connection_id = str(value.get("connection_id") or "").strip()
        partition_id = str(value.get("partition_id") or "").strip()
        if not database_id or not connection_id or not partition_id:
            raise BridgeError("VALIDATION_FAILED", "Database link identity is incomplete.")
        existing = self.get_database_link(database_id)
        created_at = str(existing.get("created_at") if existing else value.get("created_at") or utc_now())
        updated_at = str(value.get("updated_at") or utc_now())
        detail = dict(value.get("detail") or {})
        selected = bool(value.get("selected", existing.get("selected", False) if existing else False))
        with self._lock, self._connection() as connection:
            connection.execute(
                """INSERT INTO database_links(
                    database_id, connection_id, partition_id, corpus_id, source_root, target_key,
                    profile_fingerprint, origin, state, last_source_release_id,
                    last_downstream_release_id, selected, detail_json, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(database_id) DO UPDATE SET
                    connection_id=excluded.connection_id, partition_id=excluded.partition_id,
                    corpus_id=excluded.corpus_id, source_root=excluded.source_root,
                    target_key=excluded.target_key, profile_fingerprint=excluded.profile_fingerprint,
                    origin=excluded.origin, state=excluded.state,
                    last_source_release_id=excluded.last_source_release_id,
                    last_downstream_release_id=excluded.last_downstream_release_id,
                    selected=excluded.selected, detail_json=excluded.detail_json, updated_at=excluded.updated_at""",
                (
                    database_id, connection_id, partition_id, str(value.get("corpus_id") or ""),
                    str(value.get("source_root") or ""), str(value.get("target_key") or ""),
                    str(value.get("profile_fingerprint") or ""), str(value.get("origin") or "existing"),
                    str(value.get("state") or "linked"),
                    str(value.get("last_source_release_id") or "") or None,
                    str(value.get("last_downstream_release_id") or "") or None,
                    int(selected), _json(detail), created_at, updated_at,
                ),
            )
            connection.commit()
        return self.get_database_link(database_id) or {}

    def select_database_link(self, *, database_id: str, connection_id: str, partition_id: str) -> dict[str, Any]:
        """Select exactly one linked database as the update destination.

        A partition may retain more than one explicit link for discovery and
        history, but supplementing must have one persisted destination. The
        selection is catalog state; it never changes the database files.
        """
        database_id = str(database_id or "").strip()
        connection_id = str(connection_id or "").strip()
        partition_id = str(partition_id or "").strip()
        if not database_id or not connection_id or not partition_id:
            raise BridgeError("VALIDATION_FAILED", "database_id, connection_id, and partition_id are required.")
        now = utc_now()
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT connection_id, partition_id FROM database_links WHERE database_id=?",
                (database_id,),
            ).fetchone()
            if not row:
                raise BridgeError("IDENTITY_UNRESOLVED", "The selected database is not explicitly linked to this partition.")
            if str(row["connection_id"]) != connection_id or str(row["partition_id"]) != partition_id:
                raise BridgeError("IDENTITY_UNRESOLVED", "The selected database is linked to a different source partition.")
            connection.execute(
                "UPDATE database_links SET selected=0, updated_at=? WHERE connection_id=? AND partition_id=?",
                (now, connection_id, partition_id),
            )
            connection.execute(
                "UPDATE database_links SET selected=1, updated_at=? WHERE database_id=?",
                (now, database_id),
            )
            connection.commit()
        return self.get_database_link(database_id) or {}

    def get_database_link(self, database_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM database_links WHERE database_id=?", (str(database_id),)).fetchone()
        if not row:
            return None
        return self._database_link_from_row(row)

    @staticmethod
    def _database_link_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "database_id": row["database_id"], "connection_id": row["connection_id"],
            "partition_id": row["partition_id"], "corpus_id": row["corpus_id"],
            "source_root": row["source_root"], "target_key": row["target_key"],
            "profile_fingerprint": row["profile_fingerprint"], "origin": row["origin"],
            "state": row["state"], "last_source_release_id": row["last_source_release_id"],
            "last_downstream_release_id": row["last_downstream_release_id"],
            "selected": bool(row["selected"]),
            "detail": json.loads(row["detail_json"]), "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_database_links(self, *, connection_id: str | None = None, partition_id: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if connection_id:
            clauses.append("connection_id=?")
            values.append(str(connection_id))
        if partition_id:
            clauses.append("partition_id=?")
            values.append(str(partition_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as connection:
            rows = connection.execute(f"SELECT * FROM database_links{where} ORDER BY updated_at DESC, database_id", values).fetchall()
        return [self._database_link_from_row(row) for row in rows]

    def save_database_release_event(self, value: Mapping[str, Any]) -> dict[str, Any]:
        event = {
            "id": str(value.get("id") or new_id("release_event")),
            "database_id": str(value.get("database_id") or ""),
            "connection_id": str(value.get("connection_id") or ""),
            "partition_id": str(value.get("partition_id") or ""),
            "upstream_release_id": str(value.get("upstream_release_id") or "") or None,
            "downstream_release_id": str(value.get("downstream_release_id") or "") or None,
            "operation": str(value.get("operation") or "unknown"),
            "status": str(value.get("status") or "unknown"),
            "detail": dict(value.get("detail") or {}),
            "created_at": str(value.get("created_at") or utc_now()),
        }
        if not event["database_id"] or not event["connection_id"] or not event["partition_id"]:
            raise BridgeError("VALIDATION_FAILED", "Database release history identity is incomplete.")
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO database_release_history(id,database_id,connection_id,partition_id,upstream_release_id,downstream_release_id,operation,status,detail_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (event["id"], event["database_id"], event["connection_id"], event["partition_id"], event["upstream_release_id"], event["downstream_release_id"], event["operation"], event["status"], _json(event["detail"]), event["created_at"]),
            )
            connection.commit()
        return event

    def list_database_release_history(self, database_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM database_release_history WHERE database_id=? ORDER BY created_at DESC LIMIT ?",
                (str(database_id), max(1, min(int(limit), 500))),
            ).fetchall()
        return [
            {
                "id": row["id"], "database_id": row["database_id"], "connection_id": row["connection_id"],
                "partition_id": row["partition_id"], "upstream_release_id": row["upstream_release_id"],
                "downstream_release_id": row["downstream_release_id"], "operation": row["operation"],
                "status": row["status"], "detail": json.loads(row["detail_json"]), "created_at": row["created_at"],
            }
            for row in rows
        ]

    def save_draft(self, payload: Mapping[str, Any], *, draft_id: str | None = None, database_id: str | None = None) -> dict[str, Any]:
        identifier = draft_id or new_id("draft")
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT revision FROM drafts WHERE id=?", (identifier,)).fetchone()
            revision = int(row[0]) + 1 if row else 1
            connection.execute(
                "INSERT INTO drafts(id,database_id,revision,payload_json,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET database_id=excluded.database_id,revision=excluded.revision,payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                (identifier, database_id, revision, _json(dict(payload)), utc_now()),
            )
            connection.commit()
        return {"id": identifier, "database_id": database_id, "revision": revision, "payload": dict(payload)}

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if not row:
            raise BridgeError("IDENTITY_UNRESOLVED", f"Draft {draft_id} is not available.")
        return {"id": row["id"], "database_id": row["database_id"], "revision": row["revision"], "payload": json.loads(row["payload_json"]), "updated_at": row["updated_at"]}

    def save_preview(self, preview: FrozenPreview) -> dict[str, Any]:
        payload = preview.as_dict()
        preview_hash = payload["preview_hash"]
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO previews(id,database_id,draft_id,operation,status,settings_revision,preview_hash,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (preview.preview_id, preview.database_id, preview.draft_id, preview.operation, preview.status,
                 preview.settings_revision, preview_hash, _json(payload), preview.created_at),
            )
            connection.commit()
        return payload

    def get_preview(self, preview_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT payload_json FROM previews WHERE id=?", (preview_id,)).fetchone()
        if not row:
            raise BridgeError("IDENTITY_UNRESOLVED", f"Preview {preview_id} is not available.")
        return json.loads(row[0])

    def create_or_get_job(self, *, kind: str, database_id: str | None, preview_id: str | None, payload: Mapping[str, Any], can_cancel: bool = False, retry_failed: bool = False) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            if preview_id:
                existing = connection.execute("SELECT * FROM jobs WHERE preview_id=? AND kind='import'", (preview_id,)).fetchone()
                if existing:
                    if retry_failed and str(existing["state"]) == "failed":
                        now = utc_now()
                        connection.execute(
                            "UPDATE jobs SET state='queued',stage='checking',result_json=NULL,error_json=NULL,progress_json=?,started_at=NULL,completed_at=NULL WHERE id=?",
                            (_json({"stage": "checking", "message": "Queued for retry", "percent": 0}), existing["id"]),
                        )
                        connection.execute(
                            "INSERT INTO job_events VALUES(?,?,?,?,?,?)",
                            (existing["id"], self._next_event_sequence(connection, str(existing["id"])), "checking", "Queued for retry", "{}", now),
                        )
                        connection.commit()
                        existing = connection.execute("SELECT * FROM jobs WHERE id=?", (existing["id"],)).fetchone()
                    return self._job_from_row(existing)
            identifier = new_id("job")
            now = utc_now()
            connection.execute(
                "INSERT INTO jobs(id,kind,state,stage,database_id,preview_id,payload_json,result_json,error_json,progress_json,can_cancel,created_at,started_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, kind, "queued", "checking", database_id, preview_id, _json(dict(payload)), None, None, _json({"stage": "checking", "message": "Queued", "percent": 0}), int(can_cancel), now, None, None),
            )
            connection.execute("INSERT INTO job_events VALUES(?,?,?,?,?,?)", (identifier, 1, "checking", "Queued", "{}", now))
            connection.commit()
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            return self._job_from_row(row)

    @staticmethod
    def _next_event_sequence(connection: sqlite3.Connection, job_id: str) -> int:
        row = connection.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM job_events WHERE job_id=?", (job_id,)).fetchone()
        return int(row[0] or 1)

    def get_job_for_preview(self, preview_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE preview_id=? AND kind='import'", (preview_id,)).fetchone()
        return self._job_from_row(row) if row else None

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return JobRecord(id=row["id"], kind=row["kind"], state=row["state"], stage=row["stage"], database_id=row["database_id"], preview_id=row["preview_id"], created_at=row["created_at"], started_at=row["started_at"], completed_at=row["completed_at"], can_cancel=bool(row["can_cancel"]), result=json.loads(row["result_json"]) if row["result_json"] else None, error=json.loads(row["error_json"]) if row["error_json"] else None, progress=json.loads(row["progress_json"]) if row["progress_json"] else None).as_dict()

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise BridgeError("IDENTITY_UNRESOLVED", f"Job {job_id} is not available.")
        return self._job_from_row(row)

    def get_job_payload(self, job_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT payload_json FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise BridgeError("IDENTITY_UNRESOLVED", f"Job {job_id} is not available.")
        payload = json.loads(row[0])
        if not isinstance(payload, dict):
            raise BridgeError("VALIDATION_FAILED", f"Job {job_id} has an invalid persisted payload.")
        return payload

    def list_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
        return [self._job_from_row(row) for row in rows]

    def update_job(self, job_id: str, *, state: str | None = None, stage: str | None = None, result: Mapping[str, Any] | None = None, error: Mapping[str, Any] | None = None, can_cancel: bool | None = None, progress: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise BridgeError("IDENTITY_UNRESOLVED", f"Job {job_id} is not available.")
            next_state = state or row["state"]
            next_stage = stage or row["stage"]
            started = row["started_at"] or (utc_now() if next_state == "running" else None)
            completed = row["completed_at"] or (utc_now() if next_state in JobRecord.STATES - {"queued", "running"} else None)
            connection.execute("UPDATE jobs SET state=?,stage=?,started_at=?,completed_at=?,result_json=?,error_json=?,progress_json=?,can_cancel=? WHERE id=?", (next_state, next_stage, started, completed, _json(result) if result is not None else row["result_json"], _json(error) if error is not None else row["error_json"], _json(progress) if progress is not None else row["progress_json"], int(can_cancel if can_cancel is not None else bool(row["can_cancel"])), job_id))
            connection.commit()
            return self.get_job(job_id)

    def cancel_queued_job(self, job_id: str) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise BridgeError("IDENTITY_UNRESOLVED", f"Job {job_id} is not available.")
            if row[0] != "queued":
                raise BridgeError("OPERATION_UNSUPPORTED", "This operation is already active; safe cancellation is not available yet.")
            connection.execute("UPDATE jobs SET state='cancelled',completed_at=?,can_cancel=0 WHERE id=?", (utc_now(), job_id))
            connection.commit()
        return self.get_job(job_id)

    def add_event(self, job_id: str, stage: str, message: str, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM job_events WHERE job_id=?", (job_id,)).fetchone()
            sequence = int(row[0])
            connection.execute("INSERT INTO job_events VALUES(?,?,?,?,?,?)", (job_id, sequence, stage, message, _json(dict(data or {})), utc_now()))
            detail = dict(data or {})
            current = detail.get("current")
            total = detail.get("total")
            percent: float | None = None
            try:
                if total is not None and float(total) > 0 and current is not None:
                    percent = round(max(0.0, min(100.0, float(current) / float(total) * 100.0)), 1)
            except (TypeError, ValueError):
                percent = None
            progress = {"stage": stage, "message": message, "current": current, "total": total, "percent": percent, "updated_at": utc_now()}
            connection.execute("UPDATE jobs SET stage=?,progress_json=? WHERE id=?", (stage, _json(progress), job_id))
            connection.commit()
        return {"job_id": job_id, "sequence": sequence, "stage": stage, "message": message, "data": dict(data or {})}

    def get_job_events(self, job_id: str, *, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM job_events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT ?", (job_id, int(after), max(1, min(int(limit), 1000)))).fetchall()
        return [{"job_id": row["job_id"], "sequence": row["sequence"], "stage": row["stage"], "message": row["message"], "data": json.loads(row["data_json"]), "created_at": row["created_at"]} for row in rows]

    def mark_running_interrupted(self) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute("SELECT id FROM jobs WHERE state IN ('running','queued')").fetchall()
            ids = [row[0] for row in rows]
            for job_id in ids:
                connection.execute("UPDATE jobs SET state='interrupted',completed_at=?,error_json=? WHERE id=?", (utc_now(), _json({"code": "JOB_INTERRUPTED", "message": "The application stopped before this job completed.", "active_database_state": "unknown"}), job_id))
            connection.commit()
        return [self.get_job(job_id) for job_id in ids]

    def save_activation_journal(self, operation_id: str, target_path: str, stage_path: str, backup_path: str | None, state: str, detail: Mapping[str, Any] | None = None) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("INSERT OR REPLACE INTO activation_journal VALUES(?,?,?,?,?,?,?)", (operation_id, target_path, stage_path, backup_path, state, utc_now(), _json(dict(detail or {}))))
            connection.commit()

    def list_activation_journals(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM activation_journal ORDER BY updated_at DESC").fetchall()
        return [{"operation_id": row["operation_id"], "target_path": row["target_path"], "stage_path": row["stage_path"], "backup_path": row["backup_path"], "state": row["state"], "updated_at": row["updated_at"], "detail": json.loads(row["detail_json"])} for row in rows]
