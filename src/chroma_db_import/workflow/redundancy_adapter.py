"""Bridge-safe adapter around the existing semantic redundancy contracts."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from chroma_db_import.dedup_artifacts import validate_dedup_artifacts
from chroma_db_import.redundancy_artifacts import validate_bundle
from chroma_db_import.redundancy_inventory import load_inventory
from chroma_db_import.redundancy_policy import RedundancyPolicyError, policy_fingerprint, resolve_redundancy_policy

from .models import BridgeError, ContextRef, make_report, stable_hash, utc_now

ACTION_FIELDS: dict[str, set[str]] = {
    "preview": {"context", "upstream_release_id", "channels"},
    "assess": {"context", "upstream_release_id", "channels"},
    "resume": {"context", "frozen_job_id"},
    "pilot": {"context", "upstream_release_id", "channels", "review_id"},
    "label_export": {"context", "artifact_id", "output_path"},
    "evaluate": {"context", "artifact_id", "labels_path", "queries_path", "query_results_path", "output_path"},
}
CHANNELS = {"lexical", "structural", "dense"}


def validate_action(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise BridgeError("VALIDATION_FAILED", "The redundancy action must be an object.")
    action = str(payload.get("action") or "")
    if action not in ACTION_FIELDS:
        raise BridgeError("OPERATION_UNSUPPORTED", "Redundancy action is not allowlisted.", field="action")
    unknown = set(payload).difference(ACTION_FIELDS[action] | {"action"})
    if unknown:
        raise BridgeError("VALIDATION_FAILED", f"Field {sorted(unknown)[0]} does not belong to the {action} action.")
    if not isinstance(payload.get("context"), Mapping):
        raise BridgeError("VALIDATION_FAILED", "A context scope is required for redundancy analysis.", field="context")
    if action in {"preview", "assess", "pilot"}:
        channels = payload.get("channels")
        if channels is not None and (not isinstance(channels, list) or not channels or not set(map(str, channels)).issubset(CHANNELS)):
            raise BridgeError("VALIDATION_FAILED", "At least one supported analysis channel is required.", field="channels")
    if action == "resume" and not str(payload.get("frozen_job_id") or "").strip():
        raise BridgeError("VALIDATION_FAILED", "A frozen assessment job ID is required to resume.", field="frozen_job_id")
    if action == "pilot" and not str(payload.get("review_id") or "").strip():
        raise BridgeError("VALIDATION_FAILED", "A reviewed pilot ID is required before running a judge pilot.", field="review_id")
    if action == "label_export":
        if not str(payload.get("artifact_id") or "").strip():
            raise BridgeError("VALIDATION_FAILED", "A validated bundle artifact ID is required.", field="artifact_id")
        if not str(payload.get("output_path") or "").strip():
            raise BridgeError("VALIDATION_FAILED", "An output path is required for label export.", field="output_path")
    if action == "evaluate":
        for field in ("artifact_id", "labels_path", "output_path"):
            if not str(payload.get(field) or "").strip():
                raise BridgeError("VALIDATION_FAILED", f"{field} is required for evaluation.", field=field)
    return dict(payload)


def _read_saved_settings(path: Path, partition_id: str) -> dict[str, Any]:
    """Read settings without opening ManagedCatalog's lazy default writer."""
    if not path.is_file():
        return {"policy": resolve_redundancy_policy(), "policy_fingerprint": policy_fingerprint({}), "revision": 0, "judge_config": None}
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT policy_json,policy_fingerprint,updated_at FROM redundancy_profiles WHERE partition_id=?", (partition_id,)).fetchone()
        judge_row = connection.execute("SELECT value FROM catalog_meta WHERE key=?", (f"redundancy_judge:{partition_id}",)).fetchone()
    except sqlite3.Error:
        row, judge_row = None, None
    finally:
        connection.close()
    policy = json.loads(row["policy_json"]) if row else resolve_redundancy_policy()
    judge = None
    if judge_row:
        try: judge = json.loads(judge_row[0])
        except json.JSONDecodeError: judge = None
    return {"policy": policy, "policy_fingerprint": str(row["policy_fingerprint"]) if row else policy_fingerprint(policy), "revision": 1 if row else 0, "updated_at": row["updated_at"] if row else None, "judge_config": judge}


def get_settings(catalog_path: str | Path, partition_id: str) -> dict[str, Any]:
    if not str(partition_id).strip():
        raise BridgeError("VALIDATION_FAILED", "A partition ID is required.", field="partition_id")
    return _read_saved_settings(Path(catalog_path).expanduser().resolve(), str(partition_id))


def get_dedup_settings(catalog_path: str | Path, partition_id: str) -> dict[str, Any]:
    """Read the saved dedup policy without invoking any lazy catalog writer."""
    path = Path(catalog_path).expanduser().resolve()
    profile: dict[str, Any] = {}
    fingerprint = ""
    if path.is_file():
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            row = connection.execute("SELECT profile_json, profile_fingerprint FROM import_profiles WHERE partition_id=?", (str(partition_id),)).fetchone()
        except sqlite3.Error:
            row = None
        finally:
            connection.close()
        if row:
            try:
                profile = json.loads(row[0]) if isinstance(row[0], str) else {}
            except json.JSONDecodeError as exc:
                raise BridgeError("SOURCE_INVALID", "The saved import profile is invalid.") from exc
            fingerprint = str(row[1] or "")
    from chroma_db_import.deduplication import resolve_dedup_policy

    try:
        policy = resolve_dedup_policy(profile.get("dedup_policy"), default_profile="safe")
    except Exception as exc:
        raise BridgeError("SOURCE_INVALID", str(exc)) from exc
    return {"policy": policy, "policy_fingerprint": stable_hash(policy), "profile_fingerprint": fingerprint, "profile": profile}


def save_dedup_policy(catalog_path: str | Path, partition_id: str, changes: Mapping[str, Any], base_profile_fingerprint: str | None = None) -> dict[str, Any]:
    from chroma_db_import.deduplication import resolve_dedup_policy
    from chroma_db_import.managed import ManagedCatalog

    current = get_dedup_settings(catalog_path, partition_id)
    if base_profile_fingerprint and str(base_profile_fingerprint) != str(current.get("profile_fingerprint") or ""):
        raise BridgeError("STALE_SETTINGS", "Deduplication settings changed in another window. Reload before saving.")
    try:
        policy = resolve_dedup_policy(changes, default_profile="safe")
    except Exception as exc:
        raise BridgeError("VALIDATION_FAILED", str(exc)) from exc
    profile = dict(current.get("profile") or {})
    profile["dedup_policy"] = policy
    with ManagedCatalog(Path(catalog_path).expanduser().resolve()) as catalog:
        fingerprint = catalog.save_profile(str(partition_id), profile)
    return {"policy": policy, "policy_fingerprint": stable_hash(policy), "profile_fingerprint": fingerprint, "saved_at": utc_now()}


def review_dedup_export(export_path: str | Path, *, scope: Mapping[str, Any] | None = None) -> dict[str, Any]:
    root = Path(export_path).expanduser().resolve()
    try:
        release = json.loads((root / "release.json").read_text(encoding="utf-8"))
        validated = validate_dedup_artifacts(root, release)
    except Exception as exc:
        return make_report("dedup_review", dict(scope or {}), status="unavailable", findings=[{"severity": "error", "code": "DEDUP_EVIDENCE_UNAVAILABLE", "message": str(exc)}], details={"export_path": str(root)})
    manifest = dict(validated.get("manifest") or {})
    coverage = dict(manifest.get("coverage") or {})
    report_scope = dict(scope or {"partition_id": release.get("partition_id"), "corpus_id": release.get("corpus_id"), "release_id": release.get("release_id")})
    return make_report("dedup_review", report_scope, summary={"stored": len(validated.get("stored_ids") or []), "suppressed": len(validated.get("suppressed_ids") or []), "groups": int(validated.get("exact_group_count") or 0), "edges": int(validated.get("near_edge_count") or 0), "would_suppress": coverage.get("would_suppress_exact")}, details={"export_path": str(root), "coverage": coverage, "policy": manifest.get("policy"), "policy_fingerprint": manifest.get("policy_fingerprint")})


def save_policy(catalog_path: str | Path, partition_id: str, changes: Mapping[str, Any], base_fingerprint: str | None = None) -> dict[str, Any]:
    try:
        policy = resolve_redundancy_policy(changes)
    except RedundancyPolicyError as exc:
        raise BridgeError("VALIDATION_FAILED", str(exc)) from exc
    current = get_settings(catalog_path, partition_id)
    if base_fingerprint and str(base_fingerprint) != str(current["policy_fingerprint"]):
        raise BridgeError("STALE_SETTINGS", "Redundancy policy changed in another window. Reload before saving.", field="base_fingerprint")
    from chroma_db_import.managed import ManagedCatalog

    with ManagedCatalog(Path(catalog_path).expanduser().resolve()) as catalog:
        try:
            fingerprint = catalog.save_redundancy_policy(str(partition_id), policy)
        except Exception as exc:
            raise BridgeError("VALIDATION_FAILED", str(exc)) from exc
    return {"policy": policy, "policy_fingerprint": fingerprint, "revision": int(current.get("revision") or 0) + 1, "saved_at": utc_now()}


def coverage_preview(export_path: str | Path, *, scope: Mapping[str, Any] | None = None, policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        resolved = resolve_redundancy_policy(policy)
        inventory = load_inventory(export_path, inspect_vectors=False)
    except Exception as exc:
        return make_report("redundancy_coverage", dict(scope or {}), status="unavailable", findings=[{"severity": "error", "code": "ANALYSIS_UNAVAILABLE", "message": str(exc)}], details={"export_path": str(export_path)})
    occurrence_count = len(inventory.occurrences)
    retained = sum(1 for item in inventory.occurrences if str(item.get("storage_status") or "retained") == "retained")
    report_scope = dict(scope or inventory.scope.as_dict())
    return make_report("redundancy_coverage", report_scope, summary={"occurrences": occurrence_count, "retained": retained, "analysis_units": len(inventory.units), "lexical_available": bool(resolved.get("lexical_enabled")), "structural_available": bool(resolved.get("structural_enabled")), "dense_available": bool(resolved.get("dense_enabled"))}, details={"export_path": str(Path(export_path).resolve()), "policy": resolved, "policy_fingerprint": policy_fingerprint(resolved), "coverage": {"status": "model_free", "assessment_job_id": None}})


def validate_redundancy_bundle(path: str | Path, *, expected_scope: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        result = validate_bundle(path, expected_scope=expected_scope)
    except Exception as exc:
        return {"report": make_report("redundancy_bundle", dict(expected_scope or {}), status="failed", findings=[{"severity": "error", "code": "BUNDLE_INVALID", "message": str(exc)}], details={"path": str(path)}), "artifact_id": None}
    artifact_id = stable_hash({"path": str(Path(path).resolve()), "manifest": result.get("manifest") or result})[:24]
    return {"report": make_report("redundancy_bundle", dict(expected_scope or result.get("scope") or {}), status="pass", summary={"artifact_id": artifact_id}, details={"path": str(Path(path).resolve()), "validation": result}), "artifact_id": artifact_id}


def _bundle_review_inputs(bundle_path: str | Path, *, expected_scope: Mapping[str, Any] | None = None) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Load the immutable bundle inputs used by review-only operations."""
    report = validate_bundle(bundle_path, expected_scope=expected_scope)
    root = Path(bundle_path).expanduser().resolve()
    manifest = dict(report.get("manifest") or {})

    def read_jsonl(name: str) -> list[dict[str, Any]]:
        path = root / name
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    evidence = read_jsonl("evidence.jsonl")
    units = {str(row.get("document_id")): row for row in evidence if row.get("document_id")}
    pairs = read_jsonl("candidate_pairs.jsonl")
    judgments = read_jsonl("judgments.jsonl")
    return root, manifest, dict(report["scope"]), units, pairs, judgments


def label_export(bundle_path: str | Path, output_path: str, *, expected_scope: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create a bounded, provenance-bound label review file from a bundle."""
    if not str(output_path).strip():
        raise BridgeError("VALIDATION_FAILED", "An output path is required for label export.", field="output_path")
    try:
        root, manifest, scope, units, pairs, _judgments = _bundle_review_inputs(bundle_path, expected_scope=expected_scope)
        from chroma_db_import.redundancy_evaluation import export_labels, select_label_pairs

        selected = select_label_pairs(pairs, units, limit=600)
        result = export_labels(
            selected,
            units,
            output_path,
            base_scope=scope,
            base_fingerprint=str(manifest.get("base_fingerprint") or ""),
        )
    except BridgeError:
        raise
    except Exception as exc:
        raise BridgeError("BUNDLE_INVALID", f"The redundancy label export could not be prepared: {exc}") from exc
    return make_report(
        "redundancy_label_export",
        scope,
        status="pass",
        summary={"candidate_pairs": len(pairs), "selected_pairs": int(result.get("pair_count") or 0), "limit": 600},
        details={"bundle_path": str(root), "output_path": str(Path(output_path).expanduser().resolve()), "export": result, "base_fingerprint": manifest.get("base_fingerprint")},
    )


def evaluate_export(
    bundle_path: str | Path,
    labels_path: str,
    *,
    queries_path: str | Path | None = None,
    query_results_path: str | Path | None = None,
    output_path: str = "",
    expected_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate reviewed labels against frozen bundle predictions."""
    if not str(labels_path).strip():
        raise BridgeError("VALIDATION_FAILED", "A labels file is required for evaluation.", field="labels_path")
    if not str(output_path).strip():
        raise BridgeError("VALIDATION_FAILED", "An output path is required for evaluation.", field="output_path")
    try:
        root, manifest, scope, units, pairs, judgments = _bundle_review_inputs(bundle_path, expected_scope=expected_scope)
        from chroma_db_import.redundancy_evaluation import evaluate_frozen_snapshot, validate_labels, validate_queries

        labels = json.loads(Path(labels_path).expanduser().resolve().read_text(encoding="utf-8"))
        base_fingerprint = str(manifest.get("base_fingerprint") or "")
        validate_labels(labels, base_scope=scope, base_fingerprint=base_fingerprint, units=units)
        queries = None
        if queries_path:
            queries = json.loads(Path(queries_path).expanduser().resolve().read_text(encoding="utf-8"))
            validate_queries(queries, base_scope=scope, base_fingerprint=base_fingerprint, occurrence_ids=units)
        query_results = None
        if query_results_path:
            query_results = json.loads(Path(query_results_path).expanduser().resolve().read_text(encoding="utf-8"))
            if not isinstance(query_results, Mapping):
                raise ValueError("query results must be an object")

        from chroma_db_import.redundancy_cli import _bundle_predictions

        predictions, measurements = _bundle_predictions(pairs, judgments, labels)
        output_root = Path(output_path).expanduser().resolve()
        evaluation = evaluate_frozen_snapshot(
            labels,
            predictions,
            output_dir=output_root.parent,
            measurements=measurements,
            queries=queries,
            query_results=query_results,
        )
        evaluation_path = output_root.parent / "evaluation.json"
        output_root.parent.mkdir(parents=True, exist_ok=True)
        output_root.write_text(json.dumps(evaluation, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except BridgeError:
        raise
    except Exception as exc:
        raise BridgeError("EVALUATION_INVALID", f"The redundancy evaluation could not be completed: {exc}") from exc
    gates = dict(evaluation.get("gates") or {})
    overall = str(gates.get("overall") or "unknown")
    status = "pass" if overall in {"pass", "ready", "go"} else "partial"
    return make_report(
        "redundancy_evaluation",
        scope,
        status=status,
        summary={"overall": overall, "arm_status": dict((evaluation.get("measurements") or {}).get("arm_status") or {}), "query_status": dict(evaluation.get("queries") or {}).get("status")},
        details={"bundle_path": str(root), "labels_path": str(Path(labels_path).expanduser().resolve()), "output_path": str(output_root), "evaluation_path": str(evaluation_path), "evaluation": evaluation, "base_fingerprint": base_fingerprint},
    )
