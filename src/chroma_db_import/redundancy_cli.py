"""Catalog-backed ``chroma-db-import redundancy`` commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from .local_judge_client import DEFAULT_BASE_URL, JudgmentCache, LMStudioClient, LocalJudgeError, judgment_cache_key, run_bounded_judgments
from .managed import ManagedCatalog, ManagedContextError
from .managed_lock import ManagedPartitionBusy, ManagedPartitionLock
from .redundancy_artifacts import publish_bundle, validate_bundle, write_bundle
from .redundancy_candidates import build_lexical_index, generate_candidate_snapshot, lexical_and_source_candidates
from .redundancy_evaluation import evaluate_equivalence, evaluate_frozen_snapshot, export_labels, lexical_baseline_prediction, select_label_pairs, tune_lexical_baseline, validate_labels, validate_queries
from .redundancy_inventory import RedundancyInventoryError, load_inventory
from .redundancy_jobs import JOB_SCHEMA_VERSION, create_redundancy_job, get_redundancy_job, update_redundancy_job
from .redundancy_judge import build_judge_request, select_judge_units
from .redundancy_models import CandidatePair
from .redundancy_policy import RedundancyPolicyError, resolve_redundancy_policy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chroma-db-import redundancy", description="Assess semantic redundancy without changing the active base export.")
    sub = parser.add_subparsers(dest="command", required=True)
    policy = sub.add_parser("policy", help="show or save the scoped redundancy policy")
    policy.add_argument("--catalog", required=True)
    policy.add_argument("--partition")
    policy.add_argument("--storage", choices=("full", "shared_input"))
    policy.add_argument("--retrieval", choices=("ranked", "semantic_mmr"))
    policy.add_argument("--judge-enabled", choices=("true", "false"))
    policy.add_argument("--judge-fraction", type=float)
    policy.add_argument("--judge-max-calls", type=int)
    configure = sub.add_parser("configure-judge", help="save explicit private LM Studio configuration")
    configure.add_argument("--catalog", required=True); configure.add_argument("--partition", required=True); configure.add_argument("--base-url", default=DEFAULT_BASE_URL); configure.add_argument("--model", required=True); configure.add_argument("--model-fingerprint")
    models = sub.add_parser("models", help="list models served by LM Studio")
    models.add_argument("--base-url", default=DEFAULT_BASE_URL)
    preview = sub.add_parser("preview", help="show model-free lexical/source candidate coverage")
    preview.add_argument("--catalog", required=True); preview.add_argument("--partition", required=True); preview.add_argument("--release", required=True)
    assess = sub.add_parser("assess", help="create a private redundancy assessment job")
    assess.add_argument("--catalog", required=True); assess.add_argument("--partition", required=True); assess.add_argument("--release", required=True); assess.add_argument("--channels"); assess.add_argument("--judge", action="store_true", default=None); assess.add_argument("--resume"); assess.add_argument("--cancel-file")
    review = sub.add_parser("review", help="validate and report a bundle")
    review.add_argument("--bundle", required=True)
    labels = sub.add_parser("label-export", help="export unlabeled candidate comparisons")
    labels.add_argument("--bundle", required=True); labels.add_argument("--output", required=True)
    evaluate = sub.add_parser("evaluate", help="evaluate reviewed labels")
    evaluate.add_argument("--bundle", required=True); evaluate.add_argument("--labels", required=True); evaluate.add_argument("--queries", required=False); evaluate.add_argument("--query-results", required=False); evaluate.add_argument("--output", required=True)
    return parser


def _release_path(catalog: ManagedCatalog, partition: str, release: str) -> Path:
    if Path(release).is_dir():
        return Path(release).expanduser().resolve()
    row = catalog.release(partition, release)
    if not row:
        raise ManagedContextError(f"unknown release {release}")
    downstream_id = str(row.get("downstream_release_id") or "")
    if downstream_id:
        from .managed import managed_paths
        managed_root = Path(catalog.get_setting("managed_output_root", str(catalog.path.parent / "exports"))).expanduser()
        managed_export = managed_paths(managed_root, partition, downstream_id).get("export_root")
        if managed_export and managed_export.is_dir():
            return managed_export.resolve()
    source = Path(str(row.get("source_path") or ""))
    if source.is_dir():
        return source
    payload = row.get("payload") or {}
    candidate = payload.get("export_path") if isinstance(payload, dict) else None
    if candidate and Path(candidate).is_dir():
        return Path(candidate).resolve()
    raise ManagedContextError("release path is not available for redundancy analysis")


def _candidate_pair(value: dict[str, object]) -> CandidatePair:
    return CandidatePair(
        str(value.get("left_id") or ""),
        str(value.get("right_id") or ""),
        tuple(str(item) for item in value.get("channels") or ()),
        dict(value.get("scores") or {}),
        dict(value.get("channel_ranks") or {}),
        tuple(str(item) for item in value.get("guards") or ()),
    )


def _bundle_evaluation_inputs(bundle_path: str | Path, labels_path: str | Path, queries_path: str | Path | None = None) -> tuple[dict[str, object], dict[str, object], dict[str, object] | None, dict[str, object], list[dict[str, object]], dict[str, dict[str, object]]]:
    report = validate_bundle(bundle_path)
    root = Path(bundle_path).expanduser().resolve()
    manifest = dict(report["manifest"])
    labels = json.loads(Path(labels_path).expanduser().read_text(encoding="utf-8"))
    evidence = [json.loads(line) for line in (root / "evidence.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    units = {str(row.get("document_id")): row for row in evidence}
    validate_labels(labels, base_scope=report["scope"], base_fingerprint=str(manifest.get("base_fingerprint") or ""), units=units)
    queries = None
    if queries_path:
        queries = json.loads(Path(queries_path).expanduser().read_text(encoding="utf-8"))
        validate_queries(queries, base_scope=report["scope"], base_fingerprint=str(manifest.get("base_fingerprint") or ""), occurrence_ids=units)
    pairs = [json.loads(line) for line in (root / "candidate_pairs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    judgments = [json.loads(line) for line in (root / "judgments.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return report, labels, queries, {"manifest": manifest, "judgments": judgments}, pairs, units


def _bundle_predictions(pairs: list[dict[str, object]], judgments: list[dict[str, object]], labels: dict[str, object]) -> tuple[dict[str, dict[str, str]], dict[str, object]]:
    exact: dict[str, str] = {}
    for row in pairs:
        key = f"{str(row.get('left_id') or '')}::{str(row.get('right_id') or '')}"
        if "exact_text" in set(row.get("channels") or ()):
            exact[key] = "equivalent"
    tuned = tune_lexical_baseline(labels)
    lexical: dict[str, str] = {}
    selected = tuned.get("selected") if isinstance(tuned, dict) else None
    if isinstance(selected, dict):
        for row in pairs:
            pair = _candidate_pair(row)
            if lexical_baseline_prediction(pair, jaccard_threshold=float(selected["jaccard_threshold"]), containment_threshold=float(selected["containment_threshold"])):
                lexical[pair.candidate_id] = "equivalent"
    judge: dict[str, str] = {}
    for row in judgments:
        relation = str(row.get("relation") or "")
        if relation not in {"equivalent", "partial_overlap", "novel", "contradiction", "uncertain"}:
            continue
        candidate_id = str(row.get("candidate_id") or "")
        for matched_id in row.get("matched_ids") or []:
            matched = str(matched_id)
            if not candidate_id or not matched or candidate_id == matched:
                continue
            left, right = sorted((candidate_id, matched))
            judge[f"{left}::{right}"] = relation
    measurements: dict[str, object] = {"lexical_tuning": tuned, "judge_selection_rate": (len(judgments) / len(pairs)) if pairs else None}
    held_out = [row for row in labels.get("pairs") or [] if row.get("split") == "held_out" and row.get("relation") is not None]
    if held_out:
        judge_metrics = evaluate_equivalence(judge, held_out)
        lexical_metrics = evaluate_equivalence(lexical, held_out)
        measurements.update({"judge_precision": judge_metrics.get("equivalence_precision"), "judge_recall": judge_metrics.get("equivalence_recall"), "judge_material_errors": judge_metrics.get("material_change_errors"), "judge_baseline_precision": lexical_metrics.get("equivalence_precision"), "judge_baseline_recall": lexical_metrics.get("equivalence_recall"), "judged_positive_count": judge_metrics.get("predicted_equivalent"), "held_out_count": len(held_out)})
    measurements["arm_status"] = {
        "A": "complete",
        "B": "complete" if tuned.get("status") == "complete" else "insufficient_evidence",
        "C": "not_run:query_results_required",
        "D": "complete" if judgments else "not_run:judge_snapshot_empty",
    }
    return {"A": exact, "B": lexical, "C": None, "D": judge if judgments else None}, measurements


def _run_assessment(catalog: ManagedCatalog, partition: str, release_arg: str, *, channels: list[str] | None, judge_requested: bool | None, resume_job_id: str | None, cancel_file: str | Path | None = None) -> tuple[int, dict[str, object]]:
    root = _release_path(catalog, partition, release_arg)
    policy_record = catalog.get_redundancy_policy(partition)
    policy = dict(policy_record["policy"])
    allowed_channels = {"lexical", "structural", "dense"}
    requested_channels = sorted(set(channels or [name for name, enabled in (("lexical", policy["lexical_enabled"]), ("structural", policy["structural_enabled"]), ("dense", policy["dense_enabled"])) if enabled]))
    if not requested_channels or not set(requested_channels) <= allowed_channels:
        raise ManagedContextError("channels must select one or more of lexical, structural, dense")
    judge_config = catalog.get_redundancy_judge_config(partition) or {}
    resume_job = None
    frozen_spec: dict[str, object] = {}
    frozen_channels: list[str] | None = None
    if resume_job_id:
        resume_job = get_redundancy_job(catalog, resume_job_id)
        if not resume_job or resume_job.get("partition_id") != partition:
            raise ManagedContextError("resume job is unavailable or belongs to another partition")
        frozen_spec = dict(resume_job.get("spec") or {})
        frozen_channels = sorted(set(str(item) for item in frozen_spec.get("channels") or ()))
        if not frozen_channels or not set(frozen_channels) <= allowed_channels:
            raise ManagedContextError("resume job has no valid frozen channel selection")
        if channels is not None and requested_channels != frozen_channels:
            raise ManagedContextError("resume channels do not match the frozen job specification")
    inspect_channels = frozen_channels or requested_channels
    # Every published bundle mode depends on validated base vectors: Dense
    # needs them for candidates, full mode must bind the base collection, and
    # shared_input must materialize representative vectors. Preview remains
    # the only intentionally model/vector-free path.
    inventory = load_inventory(root, inspect_vectors=("dense" in inspect_channels or policy["vector_storage"] in {"full", "shared_input"}))
    if resume_job is not None:
        selected_channels = frozen_channels or []
        frozen_judge_requested = bool(frozen_spec.get("judge_requested"))
        if judge_requested is not None and bool(judge_requested) != frozen_judge_requested:
            raise ManagedContextError("resume judge selection does not match the frozen job specification")
        if frozen_spec.get("base_release_id") != inventory.scope.base_release_id or frozen_spec.get("policy_fingerprint") != policy_record["policy_fingerprint"] or dict(frozen_spec.get("base_scope") or {}) != inventory.scope.as_dict() or dict(frozen_spec.get("base_hashes") or {}) != dict(inventory.hashes):
            raise ManagedContextError("resume job specification no longer matches the selected base or policy")
        if frozen_judge_requested and str(frozen_spec.get("model_id") or "") != str(judge_config.get("model") or ""):
            raise ManagedContextError("resume judge model does not match the frozen job specification")
        judge_requested = frozen_judge_requested
        job = resume_job
    else:
        selected_channels = requested_channels
        judge_requested = bool(judge_requested)
        if judge_requested and not policy.get("judge_enabled"):
            raise ManagedContextError("judge pilot requires saved judge enablement")
        if judge_requested and not judge_config.get("model"):
            raise ManagedContextError("judge pilot requires an explicitly configured model")
        job = create_redundancy_job(catalog, partition, inventory.scope.base_release_id, channels=selected_channels, model_artifact_id=str(judge_config.get("model_fingerprint") or "") or None, model_id=str(judge_config.get("model") or "") or None, judge_requested=judge_requested, base_scope=inventory.scope.as_dict(), base_hashes=inventory.hashes)
    job_id = str(job["job_id"])
    job_spec = dict(job.get("spec") or {})
    base_fingerprint = str(job_spec.get("base_fingerprint") or ("sha256:" + hashlib.sha256(json.dumps(dict(inventory.hashes), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()))
    workspace = (catalog.path.parent / "redundancy_jobs" / job_id).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    cancel_path = Path(cancel_file).expanduser().resolve() if cancel_file else None

    def cancelled() -> bool:
        return bool(cancel_path and cancel_path.is_file())

    def finish_cancelled(reason: str) -> tuple[int, dict[str, object]]:
        detail = {"reason": reason, "partial_reasons": ["cancelled"], "workspace": str(workspace)}
        update_redundancy_job(catalog, job_id, status="cancelled", detail=detail)
        return 2, {"status": "cancelled", "job_id": job_id, "reason": reason}

    update_redundancy_job(catalog, job_id, status="running", detail={"workspace": str(workspace), "channels": selected_channels})
    try:
        if cancelled():
            return finish_cancelled("cancelled before candidate generation")
        snapshot = generate_candidate_snapshot(inventory.units, policy, workspace / "candidate_pairs.jsonl", vectors=inventory.vectors if "dense" in selected_channels else None, require_chroma="dense" in selected_channels)
        if cancelled():
            return finish_cancelled("cancelled after candidate generation")
        selected_channel_set = set(selected_channels)
        pair_rows = [
            dict(row)
            for row in snapshot["candidates"]
            if (set(row.get("channels") or ()) & selected_channel_set)
            or ("exact_text" in set(row.get("channels") or ()) and "lexical" in selected_channel_set)
        ]
        pair_objects = [_candidate_pair(row) for row in pair_rows]
        judgments: list[dict[str, object]] = []
        judge_status = "disabled"
        judge_metrics: dict[str, object] = {}
        if judge_requested:
            unit_map = {unit.document_id: unit for unit in inventory.units}
            selected = select_judge_units(pair_objects, unit_map, policy)
            if not selected:
                judge_status = "zero_budget"
                judge_metrics = {"calls": 0, "elapsed_seconds": 0.0, "token_usage": None, "call_records": []}
            else:
                requests = [build_judge_request(item["candidate"], item["neighbors"], policy, candidate_id=item["candidate"].document_id) for item in selected]
                candidate_ids = [item["candidate"].document_id for item in selected]
                pair_lookup = {pair.candidate_id: pair for pair in pair_objects}
                judge_pairs = [pair_lookup.get(str(item.get("pair_ids", [""])[0])) for item in selected]
                cache = JudgmentCache(workspace / "judgment_cache.json")
                model_identity = str(judge_config.get("model_fingerprint") or "") or None
                session_nonce = str((job.get("spec") or {}).get("session_nonce") or "") or None
                cache_keys = [judgment_cache_key(base_fingerprint=base_fingerprint, scope=inventory.scope.as_dict(), candidate=item["candidate"], comparisons=list(item["neighbors"]), model_artifact_id=model_identity, session_nonce=session_nonce, prompt_version="redundancy_judge_v1", schema_version=JOB_SCHEMA_VERSION, generation={"temperature": 0, "max_tokens": policy["judge_max_output_tokens"]}) for item in selected]
                client = LMStudioClient(str(judge_config.get("base_url") or DEFAULT_BASE_URL), model=str(judge_config["model"]), timeout=float(policy["judge_timeout_seconds"]))
                judge_result = run_bounded_judgments(client, requests, candidate_ids=candidate_ids, supplied_units=unit_map, pairs=judge_pairs, max_calls=int(policy["judge_max_calls"]), job_seconds=float(policy["judge_job_seconds"]), cancel_check=cancelled, cache=cache, cache_keys=cache_keys)
                judgments = list(judge_result["judgments"])
                judge_metrics = {key: judge_result.get(key) for key in ("calls", "elapsed_seconds", "token_usage", "call_records")}
                if judge_result.get("status") == "cancelled":
                    return finish_cancelled("cancelled during judge calls")
                blocking_guards = {"numeric_token_set_changed", "negation_modal_marker_changed"}
                for judgment, item in zip(judgments, selected):
                    if judgment.get("relation") == "equivalent" and any(blocking_guards & set(pair_lookup[str(pair_id)].guards) for pair_id in item.get("pair_ids") or () if str(pair_id) in pair_lookup):
                        judgment["relation"] = "uncertain"
                        judgment["reason"] = "material-change guard prohibits equivalent suppression advice"
                judge_status = str(judge_result["status"])
        partial_reasons: list[str] = []
        available = dict(snapshot.get("coverage", {}).get("channel_available") or {})
        for channel in selected_channels:
            if not available.get(channel, False):
                partial_reasons.append(f"channel_unavailable:{channel}")
        if policy.get("dense_enabled") and "dense" not in selected_channels:
            partial_reasons.append("channel_not_requested:dense")
        if judge_status == "completed_partial":
            partial_reasons.append("judge_incomplete")
        if cancelled():
            return finish_cancelled("cancelled before bundle publication")
        partial = bool(partial_reasons)
        bundle_status = "completed_partial" if partial else "completed"
        embedding_inputs = {str(row["document_id"]): str(row["embedding_input"]) for row in inventory.occurrences if isinstance(row.get("embedding_input"), str)}
        private_bundle = workspace / "bundle"
        coverage = {**dict(snapshot["coverage"]), "candidate_count": len(pair_objects), "candidate_snapshot_hash": snapshot["snapshot_hash"], "candidate_backend": snapshot["backend"], "candidate_configuration": dict(snapshot.get("configuration") or {}), "judge_status": judge_status, "judge_metrics": judge_metrics, "partial_reasons": partial_reasons}
        manifest = write_bundle(private_bundle, inventory=inventory, candidate_pairs=pair_objects, judgments=judgments, policy=policy, coverage=coverage, input_spec=dict(job.get("spec") or {}), storage_mode=policy["vector_storage"], embedding_inputs=embedding_inputs, status=bundle_status, judgment_status={"model": judge_config.get("model") if judge_requested else None, "model_artifact_id": str(judge_config.get("model_fingerprint") or "") or None, "prompt_version": "redundancy_judge_v1", "schema_version": JOB_SCHEMA_VERSION, "metrics": judge_metrics})
        bundles_root = Path(catalog.get_setting("managed_output_root", str(catalog.path.parent / "redundancy_bundles"))).expanduser().resolve() / "partitions" / partition / "redundancy_bundles"
        try:
            with ManagedPartitionLock(bundles_root.parent) as publication_lock:
                publication = publish_bundle(private_bundle, bundles_root, base_export=root, partition_lock=publication_lock)
        except ManagedPartitionBusy as exc:
            raise ManagedContextError(str(exc)) from exc
        final_status = "completed_partial" if partial else "completed"
        update_redundancy_job(catalog, job_id, status=final_status, detail={"bundle_id": manifest["bundle_id"], "publication": publication, "candidate_count": len(pair_objects), "judge_status": judge_status, "judge_metrics": judge_metrics, "partial_reasons": partial_reasons, "coverage": coverage})
        return (2 if partial else 0), {"status": final_status, "job_id": job_id, "bundle_id": manifest["bundle_id"], "publication": publication, "candidate_count": len(pair_objects), "judge_status": judge_status, "judge_metrics": judge_metrics}
    except Exception as exc:
        update_redundancy_job(catalog, job_id, status="failed", detail={"error": f"{type(exc).__name__}: {exc}"})
        raise


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "models":
            client = LMStudioClient(args.base_url)
            print(json.dumps({"status": "complete", "models": client.list_models()}, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "review":
            print(json.dumps(validate_bundle(args.bundle), ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "policy":
            with ManagedCatalog(Path(args.catalog)) as catalog:
                if not args.partition:
                    print(json.dumps(resolve_redundancy_policy(), ensure_ascii=False, sort_keys=True, indent=2)); return 0
                current = catalog.get_redundancy_policy(args.partition)
                updates = {}
                if args.storage: updates["vector_storage"] = args.storage
                if args.retrieval: updates["retrieval_mode"] = args.retrieval
                if args.judge_enabled is not None: updates["judge_enabled"] = args.judge_enabled == "true"
                if args.judge_fraction is not None: updates["judge_record_fraction"] = args.judge_fraction
                if args.judge_max_calls is not None: updates["judge_max_calls"] = args.judge_max_calls
                if updates:
                    policy = dict(current["policy"]); policy.update(updates); fingerprint = catalog.save_redundancy_policy(args.partition, policy); current = catalog.get_redundancy_policy(args.partition); current["saved_fingerprint"] = fingerprint
                print(json.dumps(current, ensure_ascii=False, sort_keys=True, indent=2)); return 0
        if args.command == "configure-judge":
            with ManagedCatalog(Path(args.catalog)) as catalog:
                catalog.set_redundancy_judge_config(args.partition, {"base_url": args.base_url, "model": args.model, "model_fingerprint": args.model_fingerprint})
            print(json.dumps({"status": "complete", "partition": args.partition, "model": args.model})); return 0
        with ManagedCatalog(Path(getattr(args, "catalog", ""))) as catalog:
            root = _release_path(catalog, args.partition, args.release) if hasattr(args, "release") else None
            if args.command == "preview":
                inventory = load_inventory(root, inspect_vectors=False)
                with tempfile.TemporaryDirectory(dir=str(catalog.path.parent)) as preview_workspace:
                    index = build_lexical_index(inventory.units, Path(preview_workspace) / "index.sqlite3", catalog.get_redundancy_policy(args.partition)["policy"])
                    pairs = sum((lexical_and_source_candidates(unit, index) for unit in inventory.units), [])
                print(json.dumps({"status": "complete", "mode": "preview", "candidate_count": len({pair.candidate_id for pair in pairs}), "scope": inventory.scope.as_dict()}, ensure_ascii=False, sort_keys=True)); return 0
            if args.command == "assess":
                channels = [item.strip() for item in args.channels.split(",")] if args.channels else None
                status, result = _run_assessment(catalog, args.partition, args.release, channels=channels, judge_requested=args.judge, resume_job_id=args.resume, cancel_file=args.cancel_file)
                print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return status
        if args.command == "label-export":
            report = validate_bundle(args.bundle); root = Path(args.bundle).resolve(); evidence = [json.loads(line) for line in (root / "evidence.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]; pairs = [json.loads(line) for line in (root / "candidate_pairs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]; units = {str(row["document_id"]): row for row in evidence}; selected = select_label_pairs(pairs, units, limit=600); result = export_labels(selected, units, args.output, base_scope=report["scope"], base_fingerprint=str(report["manifest"].get("base_fingerprint") or "")); result.update({"candidate_pair_count": len(pairs), "selected_pair_count": len(selected), "excluded_pair_count": max(0, len(pairs) - len(selected))}); print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0
        if args.command == "evaluate":
            report, labels, queries, bundle_data, pairs, _units = _bundle_evaluation_inputs(args.bundle, args.labels, args.queries)
            predictions, measurements = _bundle_predictions(pairs, list(bundle_data["judgments"]), labels)
            bundle_root = Path(args.bundle).expanduser().resolve()
            files = [path for path in bundle_root.rglob("*") if path.is_file()]
            measurements["storage_metrics"] = {
                "bundle_bytes": sum(path.stat().st_size for path in files),
                "vector_bytes": sum(path.stat().st_size for path in files if "vector" in path.name.lower() or "vector" in str(path.parent).lower()),
                "evidence_bytes": (bundle_root / "evidence.jsonl").stat().st_size if (bundle_root / "evidence.jsonl").is_file() else None,
            }
            output = Path(args.output).expanduser().resolve()
            query_results = None
            if args.query_results:
                raw_query_results = json.loads(Path(args.query_results).expanduser().read_text(encoding="utf-8"))
                if not isinstance(raw_query_results, dict):
                    raise ValueError("query results must be an object keyed by arm")
                query_results = raw_query_results
            result = evaluate_frozen_snapshot(labels, predictions, output_dir=output.parent, measurements=measurements, queries=queries, query_results=query_results)
            result["bundle"] = {"path": str(bundle_root), "bundle_id": report["manifest"].get("bundle_id"), "candidate_count": len(pairs)}
            output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0
    except (ManagedContextError, RedundancyPolicyError, RedundancyInventoryError, LocalJudgeError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}), file=sys.stderr)
        return 1
    return 1


__all__ = ["main"]
