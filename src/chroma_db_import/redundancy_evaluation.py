"""Deterministic label export and conservative evaluation helpers."""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any, Iterable, Mapping

from .redundancy_candidates import material_guards
from .redundancy_models import AnalysisUnit, CandidatePair
from .redundancy_judge import RELATIONS


class RedundancyEvaluationError(ValueError):
    pass


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _source_groups(unit: AnalysisUnit) -> set[str]:
    """Return stable provenance groups used for leakage-safe evaluation splits."""
    metadata = dict(unit.metadata or {})
    raw_groups = metadata.get("source_group_ids") or []
    if isinstance(raw_groups, str):
        raw_groups = [raw_groups]
    groups = {str(value) for value in raw_groups if str(value)}
    if unit.episode_uid:
        groups.add("episode:" + unit.episode_uid)
    if unit.cache_fingerprint:
        groups.add("cache:" + unit.cache_fingerprint)
    for key in ("duplicate_group_id", "alias_target_id", "exact_group_id"):
        if metadata.get(key):
            groups.add("lineage:" + str(metadata[key]))
    return groups or {"unit:" + unit.document_id}


def _distinct_occurrence(left: AnalysisUnit, right: AnalysisUnit) -> bool:
    """Keep exact aliases distinguishable from independent occurrence records."""
    return not (set(left.occurrence_ids) & set(right.occurrence_ids))


def export_labels(candidate_pairs: Iterable[CandidatePair | Mapping[str, Any]], units: Mapping[str, AnalysisUnit | Mapping[str, Any]], output_path: str | Path, *, base_scope: Mapping[str, Any], base_fingerprint: str, split: str = "development") -> dict[str, Any]:
    rows = []
    inventory = {str(key): value if isinstance(value, AnalysisUnit) else AnalysisUnit.from_mapping(value) for key, value in units.items()}
    for raw in candidate_pairs:
        pair = raw if isinstance(raw, CandidatePair) else CandidatePair(str(raw.get("left_id") or ""), str(raw.get("right_id") or ""), tuple(raw.get("channels") or ()), dict(raw.get("scores") or {}), dict(raw.get("channel_ranks") or {}), tuple(raw.get("guards") or ()))
        left, right = inventory.get(pair.left_id), inventory.get(pair.right_id)
        if not left or not right:
            continue
        rows.append({"left_id": pair.left_id, "right_id": pair.right_id, "left_text_hash": _hash(left.text), "right_text_hash": _hash(right.text), "left_text": left.text, "right_text": right.text, "relation": None, "material_difference": None, "distinct_occurrence": _distinct_occurrence(left, right), "reviewer": "", "split": split, "source_group_ids": sorted(_source_groups(left) | _source_groups(right)), "channels": list(pair.channels), "scores": dict(pair.scores), "guards": list(pair.guards)})
    rows.sort(key=lambda row: (row["left_id"], row["right_id"]))
    payload = {"contract_version": "redundancy-labels-v1", "base_scope": dict(base_scope), "base_fingerprint": base_fingerprint, "pairs": rows}
    validate_labels(payload)
    path = Path(output_path).expanduser().resolve(); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "pair_count": len(rows), "split_counts": {split: sum(row.get("split") == split for row in rows) for split in ("development", "held_out")}, "fingerprint": _hash(payload)}


def validate_labels(value: Mapping[str, Any], *, base_scope: Mapping[str, Any] | None = None, base_fingerprint: str | None = None, units: Mapping[str, AnalysisUnit | Mapping[str, Any]] | None = None) -> dict[str, Any]:
    if value.get("contract_version") != "redundancy-labels-v1" or not isinstance(value.get("pairs"), list):
        raise RedundancyEvaluationError("unsupported labels contract")
    if base_scope is not None and dict(value.get("base_scope") or {}) != dict(base_scope):
        raise RedundancyEvaluationError("labels scope mismatch")
    if base_fingerprint is not None and value.get("base_fingerprint") != base_fingerprint:
        raise RedundancyEvaluationError("labels base fingerprint mismatch")
    seen: set[str] = set()
    groups_by_split: dict[str, set[str]] = {"development": set(), "held_out": set()}
    for row in value["pairs"]:
        if not isinstance(row, Mapping):
            raise RedundancyEvaluationError("label row must be an object")
        left_id, right_id = str(row.get("left_id") or ""), str(row.get("right_id") or "")
        if not left_id or not right_id or left_id == right_id or left_id > right_id:
            raise RedundancyEvaluationError("label pair IDs must be distinct and canonically ordered")
        pair_key = f"{left_id}::{right_id}"
        if pair_key in seen:
            raise RedundancyEvaluationError("label pairs must be unique")
        seen.add(pair_key)
        relation = row.get("relation")
        split = row.get("split")
        if relation not in (None, *RELATIONS) or split not in {"development", "held_out"}:
            raise RedundancyEvaluationError("label row has an invalid relation or split")
        reviewer = row.get("reviewer")
        if not isinstance(reviewer, str):
            raise RedundancyEvaluationError("label reviewer must be a string")
        if relation is not None and not reviewer.strip():
            raise RedundancyEvaluationError("reviewed labels require a reviewer")
        material_difference = row.get("material_difference")
        distinct_occurrence = row.get("distinct_occurrence")
        if relation is not None and not isinstance(material_difference, bool):
            raise RedundancyEvaluationError("reviewed labels require a boolean material_difference")
        if not isinstance(distinct_occurrence, bool):
            raise RedundancyEvaluationError("label rows require a boolean distinct_occurrence")
        source_groups = row.get("source_group_ids")
        if not isinstance(source_groups, list) or any(not isinstance(group, str) or not group for group in source_groups):
            raise RedundancyEvaluationError("source_group_ids must be a list of non-empty strings")
        groups_by_split[str(split)].update(source_groups)
        for field in ("left_text_hash", "right_text_hash"):
            if not isinstance(row.get(field), str) or not row[field]:
                raise RedundancyEvaluationError(f"label row is missing {field}")
        if not isinstance(row.get("left_text"), str) or not isinstance(row.get("right_text"), str):
            raise RedundancyEvaluationError("label row is missing source excerpts")
        if units is not None:
            inventory = {str(key): value if isinstance(value, AnalysisUnit) else AnalysisUnit.from_mapping(value) for key, value in units.items()}
            left_unit, right_unit = inventory.get(left_id), inventory.get(right_id)
            if left_unit is None or right_unit is None:
                raise RedundancyEvaluationError("label pair references an unknown evidence ID")
            if _hash(left_unit.text) != row.get("left_text_hash") or _hash(right_unit.text) != row.get("right_text_hash") or row.get("left_text") != left_unit.text or row.get("right_text") != right_unit.text:
                raise RedundancyEvaluationError("label source excerpt or content hash does not match the bound evidence")
    overlap = groups_by_split["development"] & groups_by_split["held_out"]
    if overlap:
        raise RedundancyEvaluationError("label source groups leak across development and held_out splits")
    labeled_count = sum(row.get("relation") is not None for row in value["pairs"])
    return {"valid": True, "pair_count": len(value["pairs"]), "labeled_count": labeled_count, "split_counts": {split: sum(row.get("split") == split for row in value["pairs"]) for split in groups_by_split}}


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def evaluate_equivalence(predicted: Mapping[str, str], labels: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    reviewed = [row for row in labels if row.get("relation") is not None]
    actual = {f"{str(row.get('left_id'))}::{str(row.get('right_id'))}": str(row.get("relation")) for row in reviewed}
    reviewed_by_key = {f"{str(row.get('left_id'))}::{str(row.get('right_id'))}": row for row in reviewed}
    predicted_equivalent = [str(key) for key, value in predicted.items() if value == "equivalent"]
    true_positive = sum(actual.get(key) == "equivalent" for key in predicted_equivalent)
    actual_positive = sum(value == "equivalent" for value in actual.values())
    material_errors = sum(bool(reviewed_by_key.get(key, {}).get("material_difference")) for key in predicted_equivalent)
    return {
        "equivalence_precision": true_positive / len(predicted_equivalent) if predicted_equivalent else None,
        "equivalence_recall": true_positive / actual_positive if actual_positive else None,
        "predicted_equivalent": len(predicted_equivalent),
        "true_positive": true_positive,
        "actual_positive": actual_positive,
        "material_change_errors": material_errors,
        "material_change_error_rate": material_errors / len(predicted_equivalent) if predicted_equivalent else None,
        "precision_interval": wilson_interval(true_positive, len(predicted_equivalent)),
        "recall_interval": wilson_interval(true_positive, actual_positive),
        "reviewed_count": len(reviewed),
    }


def bootstrap_query_metric(values: Iterable[float], *, seed: int = 0, replicates: int = 1000, groups: Iterable[str] | None = None) -> dict[str, Any]:
    data = [float(value) for value in values]
    if not data:
        return {"estimate": None, "interval": None, "replicates": 0}
    group_values: dict[str, list[float]] | None = None
    if groups is not None:
        group_list = [str(group) for group in groups]
        if len(group_list) != len(data) or not all(group_list):
            raise RedundancyEvaluationError("bootstrap groups must align with metric values")
        group_values = {}
        for group, value in zip(group_list, data):
            group_values.setdefault(group, []).append(value)
    rng = random.Random(seed)
    samples = []
    for _ in range(replicates):
        if group_values is None:
            sample = [data[rng.randrange(len(data))] for _ in data]
        else:
            selected_groups = [rng.choice(sorted(group_values)) for _ in group_values]
            sample = [value for group in selected_groups for value in group_values[group]]
        samples.append(sum(sample) / len(sample))
    samples.sort()
    return {"estimate": sum(data) / len(data), "interval": (samples[int(0.025 * (len(samples) - 1))], samples[int(0.975 * (len(samples) - 1))]), "replicates": replicates, "seed": seed, "grouped": group_values is not None, "group_count": len(group_values) if group_values is not None else len(data)}


def evaluate_labels(labels: Mapping[str, Any], predictions: Mapping[str, str], *, output_path: str | Path | None = None) -> dict[str, Any]:
    validate_labels(labels)
    result = {"contract_version": "redundancy-evaluation-v1", "metrics": evaluate_equivalence(predictions, labels.get("pairs") or []), "input_fingerprint": _hash(labels), "insufficient_evidence": not any(row.get("relation") for row in labels.get("pairs") or [])}
    if output_path is not None:
        path = Path(output_path).expanduser().resolve(); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def _pair_key(value: CandidatePair | Mapping[str, Any]) -> str:
    if isinstance(value, CandidatePair):
        return value.candidate_id
    left, right = sorted((str(value.get("left_id") or ""), str(value.get("right_id") or "")))
    return f"{left}::{right}"


def _pair_scores(value: CandidatePair | Mapping[str, Any]) -> tuple[float, float, tuple[str, ...]]:
    scores = value.scores if isinstance(value, CandidatePair) else dict(value.get("scores") or {})
    guards = value.guards if isinstance(value, CandidatePair) else tuple(value.get("guards") or ())
    jaccard = float(scores.get("jaccard", 0.0) or 0.0)
    containment = max(float(scores.get("text_containment_left", 0.0) or 0.0), float(scores.get("text_containment_right", 0.0) or 0.0), float(scores.get("source_containment_left", 0.0) or 0.0), float(scores.get("source_containment_right", 0.0) or 0.0))
    return jaccard, containment, tuple(guards)


def lexical_baseline_prediction(pair: CandidatePair | Mapping[str, Any], *, jaccard_threshold: float, containment_threshold: float) -> bool:
    """Conservative lexical/source baseline used only for evaluation."""
    jaccard, containment, guards = _pair_scores(pair)
    if set(guards) & {"numeric_token_set_changed", "negation_modal_marker_changed", "material_change_guard"}:
        return False
    return jaccard >= float(jaccard_threshold) and containment >= float(containment_threshold)


def tune_lexical_baseline(labels: Mapping[str, Any], *, development_split: str = "development") -> dict[str, Any]:
    """Select thresholds on reviewed development labels only.

    No reviewed positives returns an explicit insufficient-evidence result;
    zero is never used as a stand-in for an unknown metric.
    """
    rows = [row for row in labels.get("pairs") or [] if row.get("split") == development_split and row.get("relation") is not None]
    thresholds = [(jaccard, containment) for jaccard in (0.8, 0.9, 0.95, 1.0) for containment in (0.9, 0.95, 1.0)]
    if not rows:
        return {"status": "insufficient_evidence", "reason": "no reviewed development labels", "thresholds": None}
    best: dict[str, Any] | None = None
    for jaccard, containment in thresholds:
        predicted = []
        for row in rows:
            scores = dict(row.get("scores") or {})
            guards = tuple(row.get("guards") or ())
            pair = {"left_id": row.get("left_id"), "right_id": row.get("right_id"), "scores": scores, "guards": guards}
            predicted.append((lexical_baseline_prediction(pair, jaccard_threshold=jaccard, containment_threshold=containment), str(row.get("relation")) == "equivalent"))
        selected = sum(item[0] for item in predicted)
        true_positive = sum(item[0] and item[1] for item in predicted)
        actual_positive = sum(item[1] for item in predicted)
        precision = true_positive / selected if selected else None
        recall = true_positive / actual_positive if actual_positive else None
        if precision is None:
            continue
        candidate = {"jaccard_threshold": jaccard, "containment_threshold": containment, "precision": precision, "recall": recall, "selected": selected, "true_positive": true_positive, "actual_positive": actual_positive}
        if best is None or (candidate["precision"], candidate["recall"] or -1, candidate["jaccard_threshold"], candidate["containment_threshold"]) > (best["precision"], best["recall"] or -1, best["jaccard_threshold"], best["containment_threshold"]):
            best = candidate
    return {"status": "complete" if best else "insufficient_evidence", "reason": None if best else "no nonzero-positive baseline threshold", "selected": best}


def select_label_pairs(candidate_pairs: Iterable[CandidatePair | Mapping[str, Any]], units: Mapping[str, AnalysisUnit | Mapping[str, Any]], *, limit: int = 600, seed: int = 0) -> list[dict[str, Any]]:
    """Select a stable sample while keeping source groups in one split.

    Components are built only from verified source/lineage identities carried
    by each unit. Candidate edges are deliberately *not* used to connect
    otherwise unrelated episodes: a semantic similarity edge crossing the
    resulting split boundary is excluded from the sample instead of creating
    benchmark leakage.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise RedundancyEvaluationError("label limit must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise RedundancyEvaluationError("label seed must be an integer")
    inventory = {str(key): value if isinstance(value, AnalysisUnit) else AnalysisUnit.from_mapping(value) for key, value in units.items()}
    rows = []
    for raw in candidate_pairs:
        pair = raw if isinstance(raw, CandidatePair) else CandidatePair(str(raw.get("left_id") or ""), str(raw.get("right_id") or ""), tuple(raw.get("channels") or ()), dict(raw.get("scores") or {}), dict(raw.get("channel_ranks") or {}), tuple(raw.get("guards") or ()))
        left, right = inventory.get(pair.left_id), inventory.get(pair.right_id)
        if not left or not right:
            continue
        stable = _hash({"seed": seed, "left_id": pair.left_id, "right_id": pair.right_id, "left_text": left.text, "right_text": right.text})
        rows.append((stable, pair, left, right))
    rows.sort(key=lambda item: item[0])
    if not rows:
        return []
    row_groups: list[set[str]] = []
    for _, _, left, right in rows:
        row_groups.append(_source_groups(left) | _source_groups(right))
    parent: dict[str, str] = {}

    def find(group: str) -> str:
        parent.setdefault(group, group)
        while parent[group] != group:
            parent[group] = parent[parent[group]]
            group = parent[group]
        return group

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    # Connect only identities co-occurring on the same verified unit.  Do not
    # union a left/right pair here: that would turn candidate discovery into a
    # source-leakage graph and merge unrelated episodes through similarity.
    for unit in inventory.values():
        groups = sorted(_source_groups(unit))
        group_list = sorted(groups)
        for group in group_list:
            find(group)
        for group in group_list[1:]:
            union(group_list[0], group)
    all_groups = sorted(parent)
    component_groups: dict[str, set[str]] = {}
    for group in all_groups:
        component_groups.setdefault(find(group), set()).add(group)
    components = sorted(component_groups, key=lambda root: (_hash({"seed": seed, "groups": sorted(component_groups[root])}), root))
    component_split: dict[str, str] = {}
    component_counts = {"development": 0, "held_out": 0}
    for component in components:
        split = "development" if component_counts["development"] <= component_counts["held_out"] else "held_out"
        component_split[component] = split
        component_counts[split] += len(component_groups[component])
    selected_indexes: list[int] = []
    split_by_index: dict[int, str] = {}
    split_counts = {"development": 0, "held_out": 0}
    target_per_split = max(1, limit // 2)
    for index, groups in enumerate(row_groups):
        splits = {component_split[find(group)] for group in groups}
        if len(splits) != 1:
            continue
        split = next(iter(splits))
        if split_counts[split] >= target_per_split or len(selected_indexes) >= limit:
            continue
        split_by_index[index] = split
        selected_indexes.append(index)
        split_counts[split] += 1
    selected_indexes.sort(key=lambda index: rows[index][0])
    result = []
    for index in selected_indexes:
        _, pair, left, right = rows[index]
        result.append({"left_id": pair.left_id, "right_id": pair.right_id, "left_text_hash": _hash(left.text), "right_text_hash": _hash(right.text), "left_text": left.text, "right_text": right.text, "relation": None, "material_difference": None, "distinct_occurrence": _distinct_occurrence(left, right), "reviewer": "", "split": split_by_index[index], "source_group_ids": sorted(_source_groups(left) | _source_groups(right)), "channels": list(pair.channels), "scores": dict(pair.scores), "guards": list(pair.guards)})
    return result


def validate_queries(value: Mapping[str, Any], *, base_scope: Mapping[str, Any] | None = None, base_fingerprint: str | None = None, occurrence_ids: Iterable[str] | None = None) -> dict[str, Any]:
    if value.get("contract_version") != "redundancy-queries-v1" or not isinstance(value.get("queries"), list):
        raise RedundancyEvaluationError("unsupported queries contract")
    if base_scope is not None and dict(value.get("base_scope") or {}) != dict(base_scope):
        raise RedundancyEvaluationError("queries scope mismatch")
    if base_fingerprint is not None and value.get("base_fingerprint") != base_fingerprint:
        raise RedundancyEvaluationError("queries base fingerprint mismatch")
    seen: set[str] = set()
    groups_by_split: dict[str, set[str]] = {"development": set(), "held_out": set()}
    allowed_modes = {"factual", "ranked", "semantic_mmr", "preserve_occurrences", "recurrence", "timeline", "speaker_comparison"}
    known_occurrences = None if occurrence_ids is None else {str(item) for item in occurrence_ids}
    allowed_filter_fields = {"episodes", "speakers", "node_types", "date_start", "date_end", "allowed_occurrence_ids"}
    for row in value["queries"]:
        if not isinstance(row, Mapping) or not str(row.get("query_id") or "") or not str(row.get("query") or ""):
            raise RedundancyEvaluationError("query row is missing an ID or text")
        query_id = str(row["query_id"])
        if query_id in seen:
            raise RedundancyEvaluationError("query IDs must be unique")
        seen.add(query_id)
        if row.get("mode") not in allowed_modes or row.get("split") not in {"development", "held_out"}:
            raise RedundancyEvaluationError("query row has an invalid mode or split")
        if not isinstance(row.get("filters"), Mapping):
            raise RedundancyEvaluationError("query filters must be an object")
        unknown_filters = set(row["filters"]) - allowed_filter_fields
        if unknown_filters:
            raise RedundancyEvaluationError("query filters contain unknown fields")
        for field in ("episodes", "speakers", "node_types", "allowed_occurrence_ids"):
            if field in row["filters"]:
                raw_values = row["filters"][field]
                if not isinstance(raw_values, list) or any(not isinstance(item, str) or not item for item in raw_values):
                    raise RedundancyEvaluationError(f"query filter {field} must be a list of non-empty strings")
                if len(set(raw_values)) != len(raw_values):
                    raise RedundancyEvaluationError(f"query filter {field} must not contain duplicates")
                if field == "allowed_occurrence_ids" and known_occurrences is not None and not set(raw_values) <= known_occurrences:
                    raise RedundancyEvaluationError("query filter allowed_occurrence_ids references an unknown evidence ID")
        for field in ("date_start", "date_end"):
            if field in row["filters"] and (not isinstance(row["filters"][field], str) or not row["filters"][field].strip()):
                raise RedundancyEvaluationError(f"query filter {field} must be a non-empty string")
        relevance = row.get("relevance")
        if not isinstance(relevance, Mapping) or any(not isinstance(key, str) or not key or isinstance(score, bool) or not isinstance(score, int) or score < 0 or score > 3 for key, score in relevance.items()):
            raise RedundancyEvaluationError("query relevance must map occurrence IDs to integer grades 0..3")
        if known_occurrences is not None and not set(relevance) <= known_occurrences:
            raise RedundancyEvaluationError("query relevance references an unknown evidence ID")
        reviewer = row.get("reviewer")
        if not isinstance(reviewer, str):
            raise RedundancyEvaluationError("query reviewer must be a string")
        source_groups = row.get("source_group_ids")
        if not isinstance(source_groups, list) or any(not isinstance(group, str) or not group for group in source_groups):
            raise RedundancyEvaluationError("query source_group_ids must be a list of non-empty strings")
        groups_by_split[str(row["split"])].update(source_groups)
    overlap = groups_by_split["development"] & groups_by_split["held_out"]
    if overlap:
        raise RedundancyEvaluationError("query source groups leak across development and held_out splits")
    return {"valid": True, "query_count": len(value["queries"]), "split_counts": {split: sum(row.get("split") == split for row in value["queries"]) for split in groups_by_split}}


def select_query_sample(queries: Iterable[Mapping[str, Any]], units: Mapping[str, AnalysisUnit | Mapping[str, Any]], *, limit: int = 100, seed: int = 0) -> list[dict[str, Any]]:
    """Select a stable, leakage-safe development/held-out query sample.

    Query source groups are connected only when they are explicitly attached
    to the same query or are verified provenance groups of required evidence.
    Similarity or embedding relationships never create a split component.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 2:
        raise RedundancyEvaluationError("query limit must be an integer of at least 2")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise RedundancyEvaluationError("query seed must be an integer")
    inventory = {str(key): value if isinstance(value, AnalysisUnit) else AnalysisUnit.from_mapping(value) for key, value in units.items()}
    rows: list[tuple[str, dict[str, Any], set[str]]] = []
    for raw in queries:
        value = dict(raw)
        query_id = str(value.get("query_id") or "")
        query_text = str(value.get("query") or "")
        relevance = value.get("relevance")
        if not query_id or not query_text or not isinstance(relevance, Mapping) or not relevance:
            continue
        unknown = set(str(key) for key in relevance) - set(inventory)
        if unknown:
            raise RedundancyEvaluationError("query sample references an unknown evidence ID")
        groups = {str(item) for item in (value.get("source_group_ids") or []) if str(item)}
        if not groups:
            for occurrence_id in relevance:
                groups.update(_source_groups(inventory[str(occurrence_id)]))
        if not groups:
            groups = {"query:" + query_id}
        value["query_id"] = query_id
        value["query"] = query_text
        value["source_group_ids"] = sorted(groups)
        value.setdefault("reviewer", "")
        value.setdefault("filters", {})
        value.setdefault("mode", "factual")
        stable = _hash({"seed": seed, "query_id": query_id, "query": query_text, "relevance": dict(relevance)})
        rows.append((stable, value, groups))
    rows.sort(key=lambda item: (item[0], item[1]["query_id"]))
    if not rows:
        return []

    parent: dict[str, str] = {}

    def find(group: str) -> str:
        parent.setdefault(group, group)
        if parent[group] != group:
            parent[group] = find(parent[group])
        return parent[group]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for _, _, groups in rows:
        ordered = sorted(groups)
        for group in ordered:
            find(group)
        for group in ordered[1:]:
            union(ordered[0], group)
    component_rows: dict[str, int] = {}
    for _, _, groups in rows:
        roots = {find(group) for group in groups}
        for root in roots:
            component_rows[root] = component_rows.get(root, 0) + 1
    components = sorted(component_rows, key=lambda root: (_hash({"seed": seed, "groups": sorted(group for group in parent if find(group) == root)}), root))
    component_split: dict[str, str] = {}
    split_counts = {"development": 0, "held_out": 0}
    for component in components:
        split = "development" if split_counts["development"] <= split_counts["held_out"] else "held_out"
        component_split[component] = split
        split_counts[split] += component_rows[component]
    target = max(1, limit // 2)
    selected: list[tuple[str, dict[str, Any]]] = []
    selected_counts = {"development": 0, "held_out": 0}
    for stable, value, groups in rows:
        split_set = {component_split[find(group)] for group in groups}
        if len(split_set) != 1:
            continue
        split = next(iter(split_set))
        if selected_counts[split] >= target or len(selected) >= limit:
            continue
        value["split"] = split
        selected.append((stable, value))
        selected_counts[split] += 1
    selected.sort(key=lambda item: (item[0], item[1]["query_id"]))
    return [value for _, value in selected]


def export_queries(queries: Iterable[Mapping[str, Any]], output_path: str | Path, *, base_scope: Mapping[str, Any], base_fingerprint: str) -> dict[str, Any]:
    rows = []
    for row in queries:
        value = dict(row)
        if not value.get("query_id") or not value.get("query") or value.get("mode") not in {"factual", "ranked", "semantic_mmr", "preserve_occurrences", "recurrence", "timeline", "speaker_comparison"}:
            raise RedundancyEvaluationError("query export row is missing a valid ID, text, or mode")
        relevance = value.get("relevance")
        if not isinstance(relevance, Mapping) or any(isinstance(score, bool) or not isinstance(score, int) or score < 0 or score > 3 for score in relevance.values()):
            raise RedundancyEvaluationError("query relevance must map occurrence IDs to integer grades 0..3")
        value.setdefault("reviewer", "")
        value.setdefault("split", "development")
        value.setdefault("filters", {})
        value.setdefault("source_group_ids", [])
        rows.append(value)
    rows.sort(key=lambda item: str(item["query_id"]))
    payload = {"contract_version": "redundancy-queries-v1", "base_scope": dict(base_scope), "base_fingerprint": base_fingerprint, "queries": rows}
    validate_queries(payload)
    path = Path(output_path).expanduser().resolve(); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "query_count": len(rows), "fingerprint": _hash(payload)}


def query_metrics(retrieved: Iterable[str], relevance: Mapping[str, int], *, k: int) -> dict[str, Any]:
    ranked: list[str] = []
    seen: set[str] = set()
    for item in retrieved:
        value = str(item)
        if value in seen:
            continue
        seen.add(value)
        ranked.append(value)
        if len(ranked) >= k:
            break
    positives = {str(item) for item, grade in relevance.items() if int(grade) > 0}
    recall = len(set(ranked) & positives) / len(positives) if positives else None
    dcg = sum((2 ** int(relevance.get(item, 0)) - 1) / math.log2(index + 2) for index, item in enumerate(ranked))
    ideal = sorted((int(grade) for grade in relevance.values()), reverse=True)[:k]
    idcg = sum((2 ** grade - 1) / math.log2(index + 2) for index, grade in enumerate(ideal))
    return {"recall_at_k": recall, "ndcg_at_k": dcg / idcg if idcg else None, "positive_count": len(positives), "retrieved_count": len(ranked)}


def candidate_recall_metrics(audit_pairs: Iterable[Mapping[str, Any]], candidate_pairs: Iterable[CandidatePair | Mapping[str, Any]], *, positive_relations: Iterable[str] = ("equivalent", "partial_overlap", "contradiction"), exact_star_pairs: Iterable[str] = ()) -> dict[str, Any]:
    """Measure candidate recall against an independent labeled audit.

    The audit is authoritative for the denominator.  Generated candidates and
    explicit exact-text star/member relations are only the found-set; missing
    generated rows are never silently treated as negatives.
    """
    positives = set(str(value) for value in positive_relations)
    found = {_pair_key(value) for value in candidate_pairs} | {str(value) for value in exact_star_pairs}
    positive_keys: set[str] = set()
    reviewed = 0
    for row in audit_pairs:
        if not isinstance(row, Mapping) or row.get("relation") is None:
            continue
        reviewed += 1
        relation = str(row.get("relation"))
        if bool(row.get("candidate_positive")) or relation in positives:
            positive_keys.add(_pair_key(row))
    found_positive = positive_keys & found
    recall = len(found_positive) / len(positive_keys) if positive_keys else None
    return {"audit_reviewed_count": reviewed, "positive_count": len(positive_keys), "found_positive_count": len(found_positive), "candidate_recall": recall, "interval": wilson_interval(len(found_positive), len(positive_keys)), "status": "complete" if positive_keys else "insufficient_evidence"}


def evaluate_arms(arms: Mapping[str, Mapping[str, str] | None], labels: Mapping[str, Any]) -> dict[str, Any]:
    reviewed = labels.get("pairs") or []
    result: dict[str, Any] = {}
    for name, predictions in arms.items():
        if predictions is None:
            result[str(name)] = {"status": "not_run", "reason": "retrieval or judge measurements were not supplied"}
            continue
        metrics = evaluate_equivalence(predictions, reviewed)
        metrics["status"] = "complete"
        result[str(name)] = metrics
    return result


def evaluate_query_results(queries: Mapping[str, Any], results_by_arm: Mapping[str, Mapping[str, Iterable[str]]], *, k: int = 10, seed: int = 0, replicates: int = 1000) -> dict[str, Any]:
    validate_queries(queries)
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 200:
        raise RedundancyEvaluationError("query metric k must be an integer between 1 and 200")
    query_by_id = {str(row["query_id"]): row for row in queries["queries"]}
    group_parent: dict[str, str] = {}

    def find(group: str) -> str:
        group_parent.setdefault(group, group)
        if group_parent[group] != group:
            group_parent[group] = find(group_parent[group])
        return group_parent[group]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            group_parent[max(left_root, right_root)] = min(left_root, right_root)

    for row in queries["queries"]:
        groups = sorted({str(group) for group in (row.get("source_group_ids") or []) if str(group)} or {"query:" + str(row["query_id"])})
        for group in groups:
            find(group)
        for group in groups[1:]:
            union(groups[0], group)
    reports: dict[str, Any] = {}
    for arm, results in results_by_arm.items():
        if not isinstance(results, Mapping):
            raise RedundancyEvaluationError(f"query results for arm {arm} must be an object")
        recalls: list[float] = []
        ndcgs: list[float] = []
        groups: list[str] = []
        missing_query_ids = sorted(set(query_by_id) - {str(query_id) for query_id in results})
        skipped_no_positive = 0
        for query_id, retrieved in results.items():
            row = query_by_id.get(str(query_id))
            if row is None:
                raise RedundancyEvaluationError(f"query results reference unknown query {query_id}")
            metric = query_metrics(retrieved, row["relevance"], k=k)
            if metric["positive_count"] == 0 or metric["recall_at_k"] is None or metric["ndcg_at_k"] is None:
                skipped_no_positive += 1
                continue
            recalls.append(float(metric["recall_at_k"]))
            ndcgs.append(float(metric["ndcg_at_k"]))
            source_groups = {str(group) for group in (row.get("source_group_ids") or []) if str(group)} or {"query:" + str(row["query_id"])}
            groups.append(find(sorted(source_groups)[0]))
        reports[str(arm)] = {
            "query_count": len(recalls),
            "total_query_count": len(query_by_id),
            "missing_query_count": len(missing_query_ids),
            "missing_query_ids": missing_query_ids,
            "skipped_no_positive_count": skipped_no_positive,
            "coverage": len(recalls) / len(query_by_id) if query_by_id else None,
            "recall_at_k": bootstrap_query_metric(recalls, seed=seed, replicates=replicates, groups=groups),
            "ndcg_at_k": bootstrap_query_metric(ndcgs, seed=seed, replicates=replicates, groups=groups),
            "k": k,
        }
    return {"status": "complete" if any(report["query_count"] for report in reports.values()) else "insufficient_evidence", "arms": reports, "seed": seed, "replicates": replicates}


def rollout_gates(*, candidate_recall: float | None, mmr_baseline_redundancy: float | None, mmr_redundancy: float | None, baseline_recall_at_k: float | None, mmr_recall_at_k: float | None, baseline_ndcg: float | None, mmr_ndcg: float | None, added_p95_ms: float | None, judge_precision: float | None, judge_material_errors: int | None, judge_baseline_precision: float | None, judged_positive_count: int | None, held_out_count: int | None) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    gates["candidate_recall"] = candidate_recall is not None and candidate_recall >= 0.95
    gates["candidate_recall_status"] = "pass" if gates["candidate_recall"] else "insufficient_evidence" if candidate_recall is None else "fail"
    if mmr_baseline_redundancy in (None, 0) or mmr_redundancy is None:
        gates["mmr"] = "insufficient_evidence" if mmr_baseline_redundancy is None or mmr_redundancy is None else "not_applicable"
    else:
        reduction = (mmr_baseline_redundancy - mmr_redundancy) / mmr_baseline_redundancy
        quality_ok = baseline_recall_at_k is not None and mmr_recall_at_k is not None and baseline_ndcg is not None and mmr_ndcg is not None and baseline_recall_at_k - mmr_recall_at_k <= 0.02 and baseline_ndcg - mmr_ndcg <= 0.02
        gates["mmr"] = "pass" if reduction >= 0.15 and quality_ok and added_p95_ms is not None and added_p95_ms <= 250 else "fail" if added_p95_ms is not None and quality_ok else "insufficient_evidence"
        gates["mmr_redundancy_reduction"] = reduction
    if judge_precision is None or judge_material_errors is None or judge_baseline_precision is None or judged_positive_count in (None, 0) or held_out_count in (None, 0):
        gates["judge"] = "insufficient_evidence"
    else:
        gates["judge"] = "pass" if judge_precision >= 0.99 and judge_material_errors == 0 and judge_precision - judge_baseline_precision >= 0.05 else "fail"
    gates["overall"] = "pass" if all(value is True or value == "pass" for key, value in gates.items() if key not in {"candidate_recall_status", "mmr_redundancy_reduction"}) else "insufficient_evidence" if any(value == "insufficient_evidence" for value in gates.values()) else "fail"
    return gates


def evaluate_frozen_snapshot(labels: Mapping[str, Any], predictions_by_arm: Mapping[str, Mapping[str, str] | None], *, output_dir: str | Path, measurements: Mapping[str, Any] | None = None, queries: Mapping[str, Any] | None = None, query_results: Mapping[str, Mapping[str, Iterable[str]]] | None = None) -> dict[str, Any]:
    validate_labels(labels)
    if queries is not None:
        validate_queries(queries)
    arms = evaluate_arms(predictions_by_arm, labels)
    values = dict(measurements or {})
    query_report: dict[str, Any] = {"status": "insufficient_evidence" if queries is None else "no_retrieval_measurements"}
    if queries is not None and query_results is not None:
        query_report = evaluate_query_results(queries, query_results)
    result = {"contract_version": "redundancy-evaluation-v1", "input_fingerprint": _hash({"labels": labels, "queries": queries, "predictions_by_arm": predictions_by_arm, "measurements": values, "query_results": query_results}), "arms": arms, "queries": query_report, "measurements": values, "gates": rollout_gates(candidate_recall=values.get("candidate_recall"), mmr_baseline_redundancy=values.get("mmr_baseline_redundancy"), mmr_redundancy=values.get("mmr_redundancy"), baseline_recall_at_k=values.get("baseline_recall_at_k"), mmr_recall_at_k=values.get("mmr_recall_at_k"), baseline_ndcg=values.get("baseline_ndcg"), mmr_ndcg=values.get("mmr_ndcg"), added_p95_ms=values.get("added_p95_ms"), judge_precision=values.get("judge_precision"), judge_material_errors=values.get("judge_material_errors"), judge_baseline_precision=values.get("judge_baseline_precision"), judged_positive_count=values.get("judged_positive_count"), held_out_count=values.get("held_out_count"))}
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "evaluation.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    lines = ["# Semantic redundancy evaluation", "", f"Status: {result['gates']['overall']}", "", "## Arms", ""]
    for name, metrics in arms.items():
        if metrics.get("status") != "complete":
            lines.append(f"- {name}: {metrics.get('status', 'unknown')} ({metrics.get('reason', 'no measurement')})")
        else:
            lines.append(f"- {name}: precision={metrics.get('equivalence_precision')!r}, recall={metrics.get('equivalence_recall')!r}, predicted_equivalent={metrics.get('predicted_equivalent')}, reviewed={metrics.get('reviewed_count')}")
    lines.extend(["", "## Evidence status", "", f"- Query measurements: {result['queries']['status']}", f"- Candidate recall: {result['gates']['candidate_recall_status']}", f"- MMR gate: {result['gates']['mmr']}", f"- Judge gate: {result['gates']['judge']}", "", "Missing labels or measurements remain unknown; this report does not enable a retrieval mode or authorize evidence deletion.", ""])
    (root / "evaluation.md").write_text("\n".join(lines), encoding="utf-8")
    return result


__all__ = ["RedundancyEvaluationError", "bootstrap_query_metric", "candidate_recall_metrics", "evaluate_equivalence", "evaluate_frozen_snapshot", "evaluate_labels", "evaluate_arms", "evaluate_query_results", "export_labels", "export_queries", "lexical_baseline_prediction", "query_metrics", "rollout_gates", "select_label_pairs", "select_query_sample", "tune_lexical_baseline", "validate_labels", "validate_queries", "wilson_interval"]
