"""Read-only reconciliation between producer partitions and GUI databases."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from chroma_db_import.config import ImportConfig
from chroma_db_import.managed import ManagedCatalog, discover, managed_paths
from chroma_db_import.podcast_rag_adapter import PodcastRagSourceAdapter

from .catalog import normalize_target
from .models import BridgeError, ExecutionOptions, SelectionPolicy, utc_now


ACTION_STATES = {
    "ready_to_create",
    "update_available",
    "current",
    "source_not_ready",
    "source_unavailable",
    "profile_mismatch",
    "ambiguous_match",
    "legacy_adoption_available",
    "target_unavailable",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _root(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    try:
        return normalize_target(Path(raw))
    except OSError:
        return os.path.normcase(os.path.normpath(raw)).casefold()


def _managed_output_root(database: Mapping[str, Any], partition_id: str) -> Path | None:
    target = dict(database.get("target") or {})
    raw = _text(target.get("managed_output_root") or target.get("path"))
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if path.name == partition_id and path.parent.name.casefold() == "partitions":
        return path.parent.parent
    return path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _export_identity(path: Path) -> dict[str, Any]:
    manifest = _read_json(path / "import_manifest.json")
    metadata = _read_json(path / "podcast.json")
    representation = dict(manifest.get("representation") or {})
    return {
        "partition_id": _text(manifest.get("partition_id") or metadata.get("partition_id")),
        "corpus_id": _text(manifest.get("corpus_id") or metadata.get("corpus_id") or metadata.get("database_id")),
        "database_id": _text(manifest.get("database_id") or metadata.get("database_id")),
        "collection_name": _text(manifest.get("collection_name") or metadata.get("collection_name")),
        "profile": _text(manifest.get("representation_profile") or representation.get("profile") or metadata.get("representation_profile")),
        "representation_id": _text(manifest.get("representation_id") or representation.get("representation_id") or metadata.get("representation_id")),
        "upstream_release_id": _text(manifest.get("upstream_release_id") or metadata.get("upstream_release_id")),
        "downstream_release_id": _text(manifest.get("downstream_release_id") or manifest.get("release_id") or metadata.get("downstream_release_id") or metadata.get("release_id")),
        "manifest": manifest,
        "metadata": metadata,
    }


def _active_export(output_root: Path, partition_id: str) -> tuple[str, Path | None]:
    partition_root = managed_paths(output_root, partition_id)["partition_root"]
    pointer = partition_root / "active-release.json"
    if not pointer.is_file():
        return "", None
    payload = _read_json(pointer)
    release_id = _text(payload.get("release_id"))
    if not release_id:
        return "", None
    return release_id, managed_paths(output_root, partition_id, release_id)["export_root"]


def catalog_cache_path(app_catalog: Any, connection: Mapping[str, Any]) -> Path:
    """Return an app-owned catalog path, never a producer catalog path."""
    identifier = _text(connection.get("id")) or "source"
    return (Path(app_catalog.path).parent / "managed" / f"context_catalog_{identifier}.sqlite3").resolve()


def _source_status(source_root: Path, partition_id: str) -> dict[str, Any]:
    try:
        return PodcastRagSourceAdapter(source_root, partition_id).inspect().as_dict()
    except Exception as exc:
        return {
            "partition_id": partition_id,
            "ready_to_publish": False,
            "status": "unavailable",
            "warnings": [f"The source could not be inspected: {type(exc).__name__}: {exc}"],
            "error": str(exc),
        }


def _default_output_root(app_catalog: Any, context: Mapping[str, Any], profile: Mapping[str, Any], source_root: str = "") -> tuple[str, str]:
    settings = dict((context.get("context_settings") or {}).get("settings") or {})
    configured = _text(settings.get("output_root") or settings.get("managed_output_root"))
    if configured:
        return str(Path(configured).expanduser().resolve()), "partition"
    defaults = app_catalog.get_app_setting("creation_defaults", {"output_parent": ""})
    parent = _text((defaults.get("value") or {}).get("output_parent"))
    if parent:
        return str(Path(parent).expanduser().resolve()), "application"
    if _text(source_root):
        return str((Path(source_root).expanduser().resolve() / "exports").resolve()), "managed_fallback"
    return "", "unavailable"


def _creation_defaults(
    app_catalog: Any,
    context: Mapping[str, Any],
    connection: Mapping[str, Any],
    profile: Mapping[str, Any],
    latest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return complete defaults for a partition-launched managed create flow."""
    partition_id = _text(context.get("partition_id"))
    source_root = _text(connection.get("root"))
    saved_profile = dict(profile.get("profile") or {}) if isinstance(profile.get("profile"), Mapping) else {}
    context_settings_record = dict(context.get("context_settings") or {})
    saved_settings = dict(context_settings_record.get("settings") or {})
    app_defaults = dict((app_catalog.get_app_setting("creation_defaults", {"output_parent": "", "execution_options": {"embedding_device": "auto"}}).get("value") or {}))
    saved_selection = saved_profile.get("selection_policy") if isinstance(saved_profile.get("selection_policy"), Mapping) else {}
    selection_input = {}
    if _text(app_defaults.get("asset_filter")):
        selection_input["asset_filter"] = app_defaults["asset_filter"]
    if "asset_pattern" in app_defaults:
        selection_input["asset_pattern"] = app_defaults.get("asset_pattern")
    selection_input.update(dict(saved_selection))
    selection_policy = SelectionPolicy.from_mapping(selection_input).as_dict()
    context_revision = int(context_settings_record.get("revision") or 0)
    context_options = saved_settings.get("execution_options") if context_revision > 0 and isinstance(saved_settings.get("execution_options"), Mapping) else None
    application_options = app_defaults.get("execution_options") if isinstance(app_defaults.get("execution_options"), Mapping) else None
    execution_options = ExecutionOptions.from_mapping(context_options or application_options).as_dict()
    profile_name = _text(saved_profile.get("representation_profile")) or ImportConfig().representation_profile
    output_root, output_source = _default_output_root(app_catalog, context, profile, source_root)
    target_path = str(managed_paths(Path(output_root), partition_id)["partition_root"]) if output_root and partition_id else ""
    display_name = _text(context.get("display_name")) or partition_id
    release_id = _text((latest or {}).get("upstream_release_id"))
    return {
        "source_kind": "managed",
        "source_ref": {
            "connection_id": _text(connection.get("id")),
            "source_root": source_root,
            "partition_id": partition_id,
            "corpus_id": _text(context.get("corpus_id")) or partition_id,
            "catalog_path": str(catalog_cache_path(app_catalog, connection)),
            "upstream_release_id": release_id,
        },
        "display_name": display_name,
        "target": {"path": target_path, "managed_output_root": output_root},
        "selection_policy": selection_policy,
        "execution_options": execution_options,
        "representation": {
            "profile": profile_name,
            "profile_fingerprint": _text(profile.get("profile_fingerprint")),
            "contextualization": _text(saved_profile.get("contextualization")) or ImportConfig().contextualization,
            "embedding_model": _text(saved_profile.get("embedding_model")) or ImportConfig().embedding_model,
            "embedding_dimension": saved_profile.get("embedding_dimension") or ImportConfig().embedding_dimension,
            "collection_name": _text(saved_profile.get("collection_name")) or ImportConfig().collection_name,
        },
        "provenance": {
            "display_name": "partition" if _text(context.get("display_name")) else "partition_id",
            "output": output_source,
            "selection_policy": "partition" if saved_selection else "application" if selection_input else "builtin",
            "execution_options": "partition" if context_options else "application" if application_options else "builtin",
            "representation": "partition" if saved_profile else "builtin",
            "release": "latest_valid_release" if release_id else "unavailable",
        },
    }


def _database_profile(database: Mapping[str, Any]) -> str:
    identity = dict(database.get("downstream_identity") or {})
    target = dict(database.get("target") or {})
    return _text(identity.get("profile") or target.get("representation_profile"))


def _is_failed_creation_placeholder(database: Mapping[str, Any], link: Mapping[str, Any]) -> bool:
    """Exclude an unactivated create reservation from eligible matching DBs."""
    return (
        _text(link.get("origin")) == "created"
        and _text(link.get("state")) == "update_failed"
        and not database.get("downstream_identity")
        and not _text(link.get("last_source_release_id"))
        and not _text(link.get("last_downstream_release_id"))
    )


def _explicit_link_matches(link: Mapping[str, Any], connection: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    return (
        _text(link.get("connection_id")) == _text(connection.get("id"))
        and _text(link.get("partition_id")) == _text(context.get("partition_id"))
        and _text(link.get("corpus_id")) == _text(context.get("corpus_id"))
        and _root(link.get("source_root")) == _root(connection.get("root"))
    )


def _legacy_candidates(
    context: Mapping[str, Any],
    connection: Mapping[str, Any],
    databases: list[Mapping[str, Any]],
    output_root: str,
) -> list[dict[str, Any]]:
    partition_id = _text(context.get("partition_id"))
    candidates: list[dict[str, Any]] = []
    target_paths: dict[str, dict[str, Any]] = {}
    database_ids_by_target: dict[str, str] = {}
    if output_root:
        partition_root = managed_paths(Path(output_root), partition_id)["partition_root"]
        target_paths[_root(partition_root)] = {"path": str(partition_root), "managed_output_root": output_root}
    for database in databases:
        if _text(database.get("source_kind")) != "managed":
            continue
        source = dict(database.get("source_ref") or {})
        if _text(source.get("partition_id")) and _text(source.get("partition_id")) != partition_id:
            continue
        if _root(source.get("source_root")) and _root(source.get("source_root")) != _root(connection.get("root")):
            continue
        target = dict(database.get("target") or {})
        path = _text(target.get("path"))
        if path:
            output_root_for_database = _managed_output_root(database, partition_id)
            partition_path = managed_paths(output_root_for_database, partition_id)["partition_root"] if output_root_for_database else Path(path).expanduser().resolve()
            normalized_partition_path = _root(partition_path)
            target_paths.setdefault(normalized_partition_path, {"path": str(partition_path), "managed_output_root": str(output_root_for_database or "")})
            if database.get("id"):
                database_ids_by_target.setdefault(normalized_partition_path, str(database["id"]))
    for normalized, target in target_paths.items():
        path = Path(str(target["path"])).expanduser().resolve()
        if not path.is_dir():
            continue
        output_root_for_target = Path(str(target.get("managed_output_root") or "")).expanduser().resolve() if target.get("managed_output_root") else None
        if output_root_for_target is None:
            output_root_for_target = path.parent.parent if path.name == partition_id and path.parent.name.casefold() == "partitions" else path
        active_release_id, export = _active_export(output_root_for_target, partition_id)
        if not active_release_id or export is None or not export.is_dir():
            continue
        if not all((export / name).is_file() for name in ("import_manifest.json", "podcast.json", "chroma.sqlite3")):
            continue
        identity = _export_identity(export)
        database_id = database_ids_by_target.get(normalized)
        identity_complete = bool(identity.get("partition_id") and identity.get("corpus_id") and identity.get("upstream_release_id"))
        identity_matches_context = identity_complete and identity.get("partition_id") == partition_id and identity.get("corpus_id") == _text(context.get("corpus_id"))
        candidates.append({
            "database_id": database_id,
            "target": {**target, "path": str(path)},
            "active_release_id": active_release_id,
            "downstream_identity": {key: value for key, value in identity.items() if key not in {"manifest", "metadata"}},
            "status": "adoption_available" if identity_matches_context else "ambiguous",
            "reason": "The export contains validated managed metadata but has not been explicitly linked for tracking." if identity_matches_context else "The export is present, but its partition, corpus, or upstream release identity is incomplete or disagrees with this source.",
        })
    return candidates


def summarize_context(
    app_catalog: Any,
    context: Mapping[str, Any],
    connection: Mapping[str, Any],
    *,
    databases: list[Mapping[str, Any]] | None = None,
    source_status: Mapping[str, Any] | None = None,
    releases: list[Mapping[str, Any]] | None = None,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    databases = list(databases if databases is not None else app_catalog.list_databases(include_archived=True))
    partition_id = _text(context.get("partition_id"))
    profile = dict(profile or {})
    source_status = dict(source_status or _source_status(Path(str(connection.get("root") or "")), partition_id))
    releases = list(releases or [])
    profile_fingerprint = _text(profile.get("profile_fingerprint"))
    links = app_catalog.list_database_links(connection_id=str(connection.get("id") or ""), partition_id=partition_id)
    links = [link for link in links if _explicit_link_matches(link, connection, context)]

    # The producer adapter exposes the active pointer as latest_release_id for
    # compatibility with older status payloads. Prefer an explicit active ID,
    # then fall back to that pointer-derived value before considering catalog
    # ordering.
    active_release_id = _text(source_status.get("active_release_id") or source_status.get("latest_release_id"))
    release_views: list[dict[str, Any]] = []
    for release in releases:
        release_id = _text(release.get("upstream_release_id"))
        payload = dict(release.get("payload") or {})
        importable = str(release.get("status") or "discovered") not in {"quarantined", "failed", "invalid"} and Path(_text(release.get("source_path"))).is_file()
        is_active = release_id == active_release_id
        release_views.append({
            "upstream_release_id": release_id,
            "source_fingerprint": _text(release.get("source_fingerprint")),
            "status": _text(release.get("status") or "discovered"),
            "importable": importable,
            "active": is_active,
            "discovered_at": release.get("discovered_at"),
            "episode_count": len(payload.get("episode_uids") or []),
        })
    valid_releases = [release for release in release_views if release["importable"]]
    latest = next((release for release in valid_releases if release["active"]), None) or (valid_releases[0] if valid_releases else None)
    output_root, _output_source = _default_output_root(app_catalog, {**dict(context), "context_settings": context.get("context_settings")}, profile, str(connection.get("root") or ""))
    creation_defaults = _creation_defaults(app_catalog, context, connection, profile, latest)
    legacy = _legacy_candidates(context, connection, databases, output_root)

    matching: list[dict[str, Any]] = []
    for link in links:
        database = next((item for item in databases if str(item.get("id")) == str(link.get("database_id"))), None)
        if not database:
            continue
        if _is_failed_creation_placeholder(database, link):
            continue
        source_profile = dict(profile.get("profile") or {}) if isinstance(profile.get("profile"), Mapping) else {}
        source_profile_name = _text(source_profile.get("representation_profile") or source_profile.get("profile") or source_profile.get("name"))
        database_profile = _database_profile(database)
        compatible = (
            (not link.get("profile_fingerprint") or not profile_fingerprint or str(link.get("profile_fingerprint")) == profile_fingerprint)
            and (not database_profile or not source_profile_name or database_profile == source_profile_name)
        )
        matching.append({
            "database_id": str(database["id"]), "display_name": database.get("display_name"),
            "target": dict(database.get("target") or {}), "archived": bool(database.get("archived")),
            "compatible": compatible, "profile": _database_profile(database),
            "selected": bool(link.get("selected")),
            "last_source_release_id": link.get("last_source_release_id"),
            "last_downstream_release_id": link.get("last_downstream_release_id"),
            "link_origin": link.get("origin"),
        })

    linked_database_ids = {str(item.get("database_id")) for item in matching}
    legacy = [candidate for candidate in legacy if not candidate.get("database_id") or str(candidate.get("database_id")) not in linked_database_ids]

    state = "source_unavailable"
    reason = "The configured source could not be inspected."
    action = "refresh_status"
    selected_matches = [candidate for candidate in matching if candidate.get("selected")]
    active_matches = [candidate for candidate in matching if not candidate.get("archived")]
    active_match = (
        selected_matches[0] if len(selected_matches) == 1
        else active_matches[0] if len(active_matches) == 1
        else matching[0] if len(matching) == 1
        else None
    )
    if source_status.get("error"):
        state = "source_unavailable"
        reason = _text(source_status.get("error")) or "The configured source is unavailable."
    elif not source_status.get("ready_to_publish") or not latest:
        state = "source_not_ready"
        reason = "The partition does not yet have a ready validated release."
        action = "review_source_issues"
    elif not active_match and len(matching) > 1:
        state = "ambiguous_match"
        reason = "Multiple databases are linked to this partition. Choose one under Matching databases to set the supplement destination."
        action = "select_database"
    elif active_match:
        candidate = active_match
        if candidate["archived"]:
            state = "ambiguous_match"
            reason = "The matching database is archived; restore it or create a separate database."
            action = "restore_database"
        elif not _text((candidate.get("target") or {}).get("path")) or not Path(_text((candidate.get("target") or {}).get("path"))).expanduser().is_dir():
            state = "target_unavailable"
            reason = "The linked database destination is unavailable. Resolve it or create a separate database."
            action = "resolve_target"
        elif not candidate["compatible"]:
            state = "profile_mismatch"
            reason = "The source representation profile is not compatible with the linked database."
            action = "create_database"
        elif str(candidate.get("last_source_release_id") or "") == str(latest["upstream_release_id"]):
            state = "current"
            reason = "The linked database already contains the active source release."
            action = "view_history"
        else:
            state = "update_available"
            reason = "A newer ready source release is available for this linked database."
            action = "supplement_database"
    elif any(candidate.get("status") == "ambiguous" for candidate in legacy):
        state = "ambiguous_match"
        reason = "A known managed export could not be paired safely because its identity metadata is incomplete."
        action = "select_database"
    elif legacy:
        state = "legacy_adoption_available"
        reason = "A managed export was found, but it needs one-time confirmation before tracking begins."
        action = "link_existing"
    else:
        state = "ready_to_create"
        reason = "A ready source release is available and no linked database exists."
        action = "create_database"

    tracking = {
        "state": state if state in ACTION_STATES else "source_unavailable",
        "recommended_action": action,
        "reason": reason,
        "checked_at": utc_now(),
        "active_release_id": active_release_id or None,
        "latest_release": latest,
        "last_applied_release_id": active_match.get("last_source_release_id") if active_match else None,
        "matching_databases": matching,
        "legacy_candidates": legacy,
        "suggested_target": output_root,
        "profile_fingerprint": profile_fingerprint or None,
        "history": app_catalog.list_source_observations(connection_id=str(connection.get("id") or ""), partition_id=partition_id),
        "change_counts": {
            "episodes_total": int((latest or {}).get("episode_count") or 0),
            "records_changed": None,
            "requires_review": state in {"ready_to_create", "update_available", "profile_mismatch"},
        },
    }
    status = _text(context.get("producer_status") or source_status.get("status") or "unknown")
    return {
        "ref": {"connection_id": str(connection["id"]), "partition_id": partition_id},
        "display_name": context.get("display_name"), "context_type": context.get("context_type"),
        "corpus_id": context.get("corpus_id"), "workflow_profile": context.get("workflow_profile"),
        "local_status": context.get("local_status") or "active", "producer_status": status,
        "source_root": connection.get("root"), "catalog_path": str(catalog_cache_path(app_catalog, connection)),
        "source_status": dict(source_status), "active_database": {"database_ids": [item["database_id"] for item in matching], "status": "linked" if matching else "not_linked"},
        "release_inventory": release_views, "matching_database_ids": [item["database_id"] for item in matching],
        "profile_fingerprint": profile_fingerprint or None,
        "capabilities": {"import": bool(valid_releases), "analyze": False, "archive": True},
        "suggested_next_action": {"action": action, "reason": reason},
        "tracking": tracking,
        "creation_defaults": creation_defaults,
        "detail": {"context": dict(context), "profile": profile, "context_settings": context.get("context_settings") or {}},
    }


def _backfill_explicit_links(app_catalog: Any, databases: list[Mapping[str, Any]]) -> None:
    connections = app_catalog.list_source_connections(include_archived=True)
    for database in databases:
        if str(database.get("source_kind") or "") != "managed":
            continue
        source = dict(database.get("source_ref") or {})
        connection_id = _text(source.get("connection_id"))
        partition_id = _text(source.get("partition_id"))
        source_root = _text(source.get("source_root"))
        if not connection_id or not partition_id:
            continue
        connection = next((item for item in connections if str(item.get("id")) == connection_id), None)
        if not connection or source_root and _root(source_root) != _root(connection.get("root")):
            continue
        identity = dict(database.get("downstream_identity") or {})
        corpus_id = _text(source.get("corpus_id") or identity.get("corpus_id"))
        if not corpus_id:
            # An explicit source/partition pair without a corpus identity is
            # not strong enough to create a durable link. Leave it visible as
            # unresolved history until the user confirms the pairing.
            continue
        app_catalog.save_database_link({
            "database_id": database["id"], "connection_id": connection_id, "partition_id": partition_id,
            "corpus_id": corpus_id,
            "source_root": connection.get("root"), "target_key": _root((database.get("target") or {}).get("path")),
            "profile_fingerprint": _text(identity.get("profile_fingerprint") or identity.get("managed_profile_fingerprint")),
            "origin": "created", "state": "linked",
            "last_source_release_id": _text(identity.get("upstream_release_id")),
            "last_downstream_release_id": _text(identity.get("downstream_release_id")),
            "detail": {"backfilled": True},
        })


def _cached_unavailable_contexts(app_catalog: Any, connection: Mapping[str, Any], message: str, databases: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep previously discovered context identity visible when the root is down."""
    catalog_path = catalog_cache_path(app_catalog, connection)
    if not catalog_path.is_file():
        return []
    try:
        with ManagedCatalog(catalog_path, read_only=True) as local_catalog:
            raw_contexts = local_catalog.contexts()
            result: list[dict[str, Any]] = []
            for context in raw_contexts:
                partition_id = _text(context.get("partition_id"))
                releases = local_catalog.releases(partition_id)
                profile = local_catalog.profile(partition_id) or {}
                context["releases"] = releases
                context["profile"] = profile
                context["context_settings"] = local_catalog.context_settings(partition_id)
                status = {
                    "partition_id": partition_id,
                    "ready_to_publish": False,
                    "status": "unavailable",
                    "warnings": [message],
                    "error": message,
                }
                result.append(summarize_context(app_catalog, context, connection, databases=databases, source_status=status, releases=releases, profile=profile))
            return result
    except Exception:
        return []


def reconcile_sources(app_catalog: Any, state_dir: Path) -> dict[str, Any]:
    """Refresh app-owned source snapshots, then return actionable summaries."""
    databases = app_catalog.list_databases(include_archived=True)
    _backfill_explicit_links(app_catalog, databases)
    connections = app_catalog.list_source_connections(include_archived=False)
    contexts: list[dict[str, Any]] = []
    connection_results: list[dict[str, Any]] = []
    for connection in connections:
        catalog_path = catalog_cache_path(app_catalog, connection)
        source_root = Path(str(connection.get("root") or "")).expanduser().resolve()
        if not source_root.is_dir():
            message = f"The configured source root is unavailable: {source_root}"
            connection_results.append({**dict(connection), "catalog_path": str(catalog_path), "state": "source_unavailable", "error": message})
            contexts.extend(_cached_unavailable_contexts(app_catalog, connection, message, databases))
            continue
        try:
            # The catalog_path is app-owned cache state created when the root
            # was linked. Producer files are only read by this discovery pass.
            with ManagedCatalog(catalog_path) as local_catalog:
                discovery = discover(local_catalog, [source_root])
            connection_results.append({**dict(connection), "catalog_path": str(catalog_path), "state": "ready", "discovery": {"contexts": len(discovery.get("contexts") or []), "releases": len(discovery.get("releases") or []), "rejected": discovery.get("invalid") or []}})
        except Exception as exc:
            message = f"The source catalog could not be refreshed: {type(exc).__name__}: {exc}"
            connection_results.append({**dict(connection), "catalog_path": str(catalog_path), "state": "source_unavailable", "error": message})
            contexts.extend(_cached_unavailable_contexts(app_catalog, connection, message, databases))
            continue
        try:
            with ManagedCatalog(catalog_path, read_only=True) as local_catalog:
                raw_contexts = local_catalog.contexts()
                for context in raw_contexts:
                    partition_id = _text(context.get("partition_id"))
                    releases = local_catalog.releases(partition_id)
                    profile = local_catalog.profile(partition_id) or {}
                    context["releases"] = releases
                    context["profile"] = profile
                    context["context_settings"] = local_catalog.context_settings(partition_id)
                    status = _source_status(source_root, partition_id)
                    active_id = _text(status.get("active_release_id"))
                    for release in releases:
                        app_catalog.save_source_observation({
                            "connection_id": connection["id"], "partition_id": partition_id,
                            "upstream_release_id": release.get("upstream_release_id"),
                            "source_root": connection.get("root"), "corpus_id": context.get("corpus_id"),
                            "status": release.get("status") or "discovered", "active": str(release.get("upstream_release_id")) == active_id,
                            "snapshot": {
                                "source_fingerprint": release.get("source_fingerprint"),
                                "source_path": release.get("source_path"),
                                "discovered_at": release.get("discovered_at"),
                                "context_status": status,
                                "profile_fingerprint": profile.get("profile_fingerprint"),
                            },
                        })
                    contexts.append(summarize_context(app_catalog, context, connection, databases=databases, source_status=status, releases=releases, profile=profile))
        except Exception as exc:
            message = f"The source context inventory could not be read: {type(exc).__name__}: {exc}"
            connection_results.append({**dict(connection), "catalog_path": str(catalog_path), "state": "source_unavailable", "error": message})
            contexts.extend(_cached_unavailable_contexts(app_catalog, connection, message, databases))
    return {"checked_at": utc_now(), "connections": connection_results, "contexts": contexts, "source_observations": app_catalog.list_source_observations()}


def select_database_link(app_catalog: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Persist the user's explicit destination choice for a partition."""
    database_id = _text(payload.get("database_id"))
    connection_id = _text(payload.get("connection_id"))
    partition_id = _text(payload.get("partition_id"))
    if not database_id or not connection_id or not partition_id:
        raise BridgeError("VALIDATION_FAILED", "database_id, connection_id, and partition_id are required.")

    database = app_catalog.get_database(database_id)
    if str(database.get("source_kind") or "") != "managed":
        raise BridgeError("VALIDATION_FAILED", "Only a managed database can be selected for a managed partition.")
    if bool(database.get("archived")):
        raise BridgeError("VALIDATION_FAILED", "Archived databases cannot be selected as a supplement destination.")

    link = next(
        (
            item for item in app_catalog.list_database_links(connection_id=connection_id, partition_id=partition_id)
            if str(item.get("database_id")) == database_id
        ),
        None,
    )
    if link is None:
        raise BridgeError("IDENTITY_UNRESOLVED", "The selected database is not explicitly linked to this partition.")
    return app_catalog.select_database_link(database_id=database_id, connection_id=connection_id, partition_id=partition_id)


def adopt_database_link(app_catalog: Any, payload: Mapping[str, Any], state_dir: Path | None = None) -> dict[str, Any]:
    database_id = _text(payload.get("database_id"))
    connection_id = _text(payload.get("connection_id"))
    partition_id = _text(payload.get("partition_id"))
    if not database_id or not connection_id or not partition_id:
        raise BridgeError("VALIDATION_FAILED", "database_id, connection_id, and partition_id are required.")
    database = app_catalog.get_database(database_id)
    if str(database.get("source_kind") or "") != "managed":
        raise BridgeError("VALIDATION_FAILED", "Only a managed export can be linked to a source partition.")
    if bool(database.get("archived")):
        raise BridgeError("VALIDATION_FAILED", "Archived databases are not eligible existing targets. Restore it before linking.")
    connection = next((item for item in app_catalog.list_source_connections(include_archived=True) if str(item.get("id")) == connection_id), None)
    if not connection:
        raise BridgeError("IDENTITY_UNRESOLVED", "The source connection is not registered.")
    source = dict(database.get("source_ref") or {})
    if _root(source.get("source_root")) and _root(source.get("source_root")) != _root(connection.get("root")):
        raise BridgeError("VALIDATION_FAILED", "The database source root does not match the selected source connection.")
    if _text(source.get("partition_id")) and _text(source.get("partition_id")) != partition_id:
        raise BridgeError("VALIDATION_FAILED", "The database partition does not match the selected partition.")
    # Adoption is a confirmation against the cached context already shown in
    # the UI. Do not rescan the producer root as a side effect of this action;
    # pipeline reconciliation is intentionally reserved for initial load and
    # the explicit Refresh pipeline control.
    from .sources import get_context

    try:
        context = get_context(app_catalog, {"connection_id": connection_id, "partition_id": partition_id})
    except BridgeError as exc:
        if exc.code in {"IDENTITY_UNRESOLVED", "SOURCE_UNAVAILABLE"}:
            raise BridgeError("SOURCE_UNAVAILABLE", "The selected source partition could not be read from the cached pipeline state.") from exc
        raise
    if _text(source.get("corpus_id")) and _text(source.get("corpus_id")) != _text(context.get("corpus_id")):
        raise BridgeError("VALIDATION_FAILED", "The database corpus does not match the selected partition.")
    candidate = next((item for item in (context.get("tracking") or {}).get("legacy_candidates") or [] if str(item.get("database_id") or "") == database_id), None)
    if candidate is None:
        raise BridgeError("IDENTITY_UNRESOLVED", "The selected database is not a validated legacy candidate for this partition.")
    candidate_identity = dict(candidate.get("downstream_identity") or {})
    if str(candidate.get("status") or "") != "adoption_available":
        raise BridgeError("IDENTITY_UNRESOLVED", "The export metadata is incomplete or ambiguous; pair it explicitly before linking.")
    identity = dict(database.get("downstream_identity") or {})
    latest = dict((context.get("tracking") or {}).get("latest_release") or {})
    link = app_catalog.save_database_link({
        "database_id": database_id, "connection_id": connection_id, "partition_id": partition_id,
        "corpus_id": str(context.get("corpus_id") or partition_id), "source_root": connection.get("root"),
        "target_key": _root((database.get("target") or {}).get("path")),
        "profile_fingerprint": _text(payload.get("profile_fingerprint") or context.get("profile_fingerprint")),
        "origin": "adopted", "state": "linked",
        "last_source_release_id": _text(payload.get("last_source_release_id") or candidate_identity.get("upstream_release_id") or identity.get("upstream_release_id") or latest.get("upstream_release_id")),
        "last_downstream_release_id": _text(payload.get("last_downstream_release_id") or candidate.get("active_release_id") or candidate_identity.get("downstream_release_id") or identity.get("downstream_release_id")),
        "detail": {"confirmed_at": utc_now()},
    })
    app_catalog.save_database_release_event({
        "database_id": database_id, "connection_id": connection_id, "partition_id": partition_id,
        "upstream_release_id": link.get("last_source_release_id"), "downstream_release_id": link.get("last_downstream_release_id"),
        "operation": "adopt", "status": "succeeded", "detail": {"origin": "adopted"},
    })
    return link
