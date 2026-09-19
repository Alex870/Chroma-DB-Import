"""Explicit source connection and managed-context operations for the GUI."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from chroma_db_import.managed import ManagedCatalog, discover, managed_paths

from .inspection import resolve_active_export
from .models import BridgeError, ContextRef, DatabaseRecord, make_report, stable_hash, utc_now
from .reconciliation import catalog_cache_path, reconcile_sources as reconcile_source_roots, summarize_context


def _connection(catalog: Any, connection_id: str) -> dict[str, Any]:
    rows = [item for item in catalog.list_source_connections(include_archived=True) if item["id"] == connection_id]
    if not rows:
        raise BridgeError("IDENTITY_UNRESOLVED", f"Source connection {connection_id} is not registered.")
    return rows[0]


def _context(catalog_path: Path, partition_id: str) -> dict[str, Any]:
    with ManagedCatalog(catalog_path, read_only=True) as catalog:
        context = catalog.context(partition_id)
        if not context:
            raise BridgeError("IDENTITY_UNRESOLVED", f"Managed context {partition_id} is not available.")
        context["releases"] = catalog.releases(partition_id)
        context["profile"] = catalog.profile(partition_id)
        context["context_settings"] = catalog.context_settings(partition_id)
        context["latest_run"] = catalog.latest_run(partition_id)
        from .redundancy_adapter import get_settings

        context["redundancy_policy"] = get_settings(str(catalog_path), partition_id)
        context["judge_config"] = catalog.get_redundancy_judge_config(partition_id)
        return context


def _release_status(release: Mapping[str, Any], databases: list[Mapping[str, Any]], partition_id: str) -> dict[str, Any]:
    upstream_id = str(release.get("upstream_release_id") or "")
    downstream_id = str(release.get("downstream_release_id") or "")
    importable = str(release.get("status") or "discovered") not in {"quarantined", "failed", "invalid"} and Path(str(release.get("source_path") or "")).exists()
    export: Path | None = None
    reasons: list[str] = []
    for database in databases:
        source = dict(database.get("source_ref") or {})
        if str(source.get("partition_id") or "") != partition_id:
            continue
        target = dict(database.get("target") or {})
        root = str(target.get("managed_output_root") or "")
        if root and downstream_id:
            candidate = managed_paths(Path(root), partition_id, downstream_id)["export_root"]
            if candidate.is_dir():
                export = candidate
                break
    analyzable = False
    if export:
        required = [export / "podcast.json", export / "import_manifest.json", export / "chroma.sqlite3"]
        analyzable = all(path.is_file() for path in required)
        if not analyzable:
            reasons.append("The downstream analysis export is incomplete.")
    else:
        reasons.append("Import required before analysis; no resolved downstream export is registered.")
    if not importable:
        reasons.append("The discovered source release is not importable.")
    return {"upstream_release_id": upstream_id, "downstream_release_id": downstream_id or None, "importable": importable, "analyzable": analyzable, "export_path": str(export) if export else None, "reasons": reasons, "status": release.get("status"), "discovered_at": release.get("discovered_at"), "source_path": release.get("source_path")}


def _context_summary(context: Mapping[str, Any], connection: Mapping[str, Any], databases: list[Mapping[str, Any]], app_catalog: Any | None = None) -> dict[str, Any]:
    partition_id = str(context.get("partition_id") or "")
    releases = [_release_status(item, databases, partition_id) for item in context.get("releases") or []]
    valid = [item for item in releases if item["importable"]]
    analyzable = [item for item in releases if item["analyzable"]]
    matches = [str(db.get("id")) for db in databases if str((db.get("source_ref") or {}).get("partition_id") or "") == partition_id and str((db.get("source_ref") or {}).get("connection_id") or connection.get("id")) == str(connection.get("id"))]
    local_status = str(context.get("local_status") or "active")
    producer_status = str(context.get("producer_status") or "unknown")
    if local_status in {"archived", "hidden"}:
        action = "restore"
    elif matches and analyzable:
        action = "review_latest_release"
    elif valid:
        action = "create_database" if not matches else "review_latest_release"
    elif producer_status in {"complete", "ready", "active"}:
        action = "prepare_update"
    elif producer_status in {"pending", "failed", "interrupted"}:
        action = "review_source_issues"
    else:
        action = "refresh_status"
    summary = {
        "ref": {"connection_id": str(connection["id"]), "partition_id": partition_id},
        "display_name": context.get("display_name"), "context_type": context.get("context_type"), "corpus_id": context.get("corpus_id"), "workflow_profile": context.get("workflow_profile"),
        "local_status": local_status, "producer_status": producer_status, "source_root": connection.get("root"), "catalog_path": str(catalog_cache_path(app_catalog, connection)),
        "source_status": {"status": producer_status, "checked_at": context.get("updated_at"), "completed": context.get("completed"), "pending": context.get("pending"), "failed": context.get("failed"), "interrupted": context.get("interrupted"), "warnings": context.get("warnings") or []},
        "active_database": {"database_ids": matches, "status": "linked" if matches else "not_linked"},
        "release_inventory": releases, "matching_database_ids": matches,
        "profile_fingerprint": (context.get("profile") or {}).get("profile_fingerprint"), "capabilities": {"import": bool(valid), "analyze": bool(analyzable), "archive": True},
        "suggested_next_action": {"action": action, "reason": "Derived from the current source, release, and linked-database facts."},
    }
    if app_catalog is not None:
        tracked = summarize_context(
            app_catalog,
            context,
            connection,
            databases=databases,
            releases=list(context.get("releases") or []),
            profile=dict(context.get("profile") or {}),
        )
        summary["tracking"] = tracked["tracking"]
        summary["suggested_next_action"] = tracked["suggested_next_action"]
        summary["source_status"] = tracked["source_status"]
        summary["matching_database_ids"] = tracked["matching_database_ids"]
        summary["active_database"] = tracked["active_database"]
        summary["creation_defaults"] = tracked["creation_defaults"]
        by_release = {str(item.get("upstream_release_id")): item for item in tracked.get("tracking", {}).get("history", [])}
        for release in summary["release_inventory"]:
            observed = by_release.get(str(release.get("upstream_release_id")))
            if observed:
                release["observed_at"] = observed.get("observed_at")
                release["observed_status"] = observed.get("status")
    return summary


def link_source(app_catalog: Any, state_dir: Path, source_root: str) -> dict[str, Any]:
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise BridgeError("SOURCE_UNAVAILABLE", f"The source root is unavailable: {root}", field="source_root")
    owned_catalog = (state_dir / "managed" / "context_catalog.sqlite3").resolve()
    connection = app_catalog.save_source_connection(str(root), str(owned_catalog))
    cache_path = catalog_cache_path(app_catalog, connection)
    if Path(str(connection.get("catalog_path") or "")).resolve() != cache_path:
        connection = app_catalog.save_source_connection(str(root), str(cache_path), connection_id=str(connection["id"]))
    discovery = discover_contexts(app_catalog, connection["id"])
    return {"connection": connection, "discovery": discovery, "report": make_report("source_link", {"connection": {"connection_id": connection["id"]}}, summary={"contexts": len(discovery["contexts"]), "releases": len(discovery["releases"]), "rejected": len(discovery["rejected"])} , findings=discovery["rejected"])}


def discover_contexts(app_catalog: Any, connection_id: str) -> dict[str, Any]:
    connection = _connection(app_catalog, connection_id)
    root = Path(str(connection["root"])).resolve()
    catalog_path = catalog_cache_path(app_catalog, connection)
    with ManagedCatalog(catalog_path) as managed_catalog:
        report = discover(managed_catalog, [root])
        contexts = managed_catalog.contexts()
        for item in contexts:
            item["releases"] = managed_catalog.releases(str(item["partition_id"]))
            item["profile"] = managed_catalog.profile(str(item["partition_id"]))
    databases = app_catalog.list_databases(include_archived=True)
    summaries = [_context_summary(item, connection, databases, app_catalog) for item in contexts]
    rejected = [{"severity": "warning", "code": "DISCOVERY_REJECTED", "message": str(item.get("error") or "Candidate was rejected."), "path": item.get("path")} for item in report.get("invalid") or []]
    return {"connection": connection, "contexts": summaries, "releases": [release for item in summaries for release in item["release_inventory"]], "rejected": rejected, "checked_at": utc_now()}


def list_contexts(app_catalog: Any, *, connection_id: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]:
    connections = [_connection(app_catalog, connection_id)] if connection_id else app_catalog.list_source_connections(include_archived=False)
    databases = app_catalog.list_databases(include_archived=True)
    result: list[dict[str, Any]] = []
    for connection in connections:
        path = catalog_cache_path(app_catalog, connection)
        if not path.is_file():
            continue
        with ManagedCatalog(path, read_only=True) as catalog:
            for raw in catalog.contexts():
                if not include_archived and str(raw.get("local_status") or "active") in {"archived", "hidden"}:
                    continue
                raw["releases"] = catalog.releases(str(raw["partition_id"]))
                raw["profile"] = catalog.profile(str(raw["partition_id"]))
                result.append(_context_summary(raw, connection, databases, app_catalog))
    return result


def get_context(app_catalog: Any, ref: Mapping[str, Any]) -> dict[str, Any]:
    context_ref = ContextRef.from_mapping(ref)
    connection = _connection(app_catalog, context_ref.connection_id)
    context = _context(catalog_cache_path(app_catalog, connection), context_ref.partition_id)
    return _context_summary(context, connection, app_catalog.list_databases(include_archived=True), app_catalog) | {"detail": context}


def reconcile_sources(app_catalog: Any, state_dir: Path) -> dict[str, Any]:
    return reconcile_source_roots(app_catalog, state_dir)


def set_context_archived(app_catalog: Any, ref: Mapping[str, Any], archived: bool) -> dict[str, Any]:
    context_ref = ContextRef.from_mapping(ref)
    connection = _connection(app_catalog, context_ref.connection_id)
    with ManagedCatalog(catalog_cache_path(app_catalog, connection)) as catalog:
        catalog.set_local_status(context_ref.partition_id, "archived" if archived else "active")
    return get_context(app_catalog, ref)


def open_context_folder(app_catalog: Any, ref: Mapping[str, Any]) -> str:
    detail = get_context(app_catalog, ref)
    database_ids = list(detail.get("matching_database_ids") or [])
    if len(database_ids) != 1:
        raise BridgeError("ACTIVE_EXPORT_MISSING", "This context has no single linked active database export to open.")
    database = app_catalog.get_database(database_ids[0])
    return str(resolve_active_export(database))
