"""Strict, portable semantic-redundancy policy resolution."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping


POLICY_VERSION = "redundancy-policy-v1"


class RedundancyPolicyError(ValueError):
    pass


_DEFAULTS: dict[str, Any] = {
    "version": POLICY_VERSION,
    "lexical_enabled": True,
    "structural_enabled": True,
    "dense_enabled": True,
    "minhash_permutations": 64,
    "minhash_bands": 16,
    "rows_per_band": 4,
    "posting_cap": 512,
    "lexical_neighbors": 20,
    "structural_neighbors": 20,
    "dense_neighbors": 20,
    "pair_jaccard_gate": 0.50,
    "pair_containment_gate": 0.80,
    "pair_cosine_gate": 0.85,
    "judge_enabled": False,
    "judge_record_fraction": 0.05,
    "judge_max_calls": 250,
    "judge_max_neighbors": 5,
    "judge_timeout_seconds": 30,
    "judge_job_seconds": 900,
    "judge_max_request_bytes": 24576,
    "judge_max_output_tokens": 768,
    "retrieval_mode": "ranked",
    "mmr_lambda": 0.75,
    "candidate_cap": 200,
    "retrieval_oversample": 5,
    "vector_storage": "full",
}

_FIXED = {
    "version", "minhash_permutations", "minhash_bands", "rows_per_band", "posting_cap",
    "judge_max_request_bytes", "judge_max_output_tokens", "candidate_cap", "retrieval_oversample",
}


def canonical_policy_json(policy: Mapping[str, Any]) -> str:
    return json.dumps(dict(policy), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def policy_fingerprint(policy: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_policy_json(resolve_redundancy_policy(policy)).encode("utf-8")).hexdigest()


def _number(value: Any, name: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RedundancyPolicyError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < lo or result > hi:
        raise RedundancyPolicyError(f"{name} must be between {lo} and {hi}")
    return result


def _integer(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise RedundancyPolicyError(f"{name} must be an integer between {lo} and {hi}")
    return value


def resolve_redundancy_policy(mapping: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if mapping is not None and not isinstance(mapping, Mapping):
        raise RedundancyPolicyError("redundancy policy must be an object")
    raw = dict(mapping or {})
    unknown = sorted(set(raw) - set(_DEFAULTS))
    if unknown:
        raise RedundancyPolicyError("unknown redundancy policy field(s): " + ", ".join(unknown))
    result = dict(_DEFAULTS)
    result.update(raw)
    if result["version"] != POLICY_VERSION:
        raise RedundancyPolicyError("unsupported redundancy policy version")
    for key in ("lexical_enabled", "structural_enabled", "dense_enabled", "judge_enabled"):
        if not isinstance(result[key], bool):
            raise RedundancyPolicyError(f"{key} must be boolean")
    for key in _FIXED - {"version"}:
        if result[key] != _DEFAULTS[key]:
            raise RedundancyPolicyError(f"{key} is fixed in {POLICY_VERSION}")
    for key in ("lexical_neighbors", "structural_neighbors", "dense_neighbors"):
        result[key] = _integer(result[key], key, 1, 50)
    result["judge_max_calls"] = _integer(result["judge_max_calls"], "judge_max_calls", 0, 10000)
    result["judge_max_neighbors"] = _integer(result["judge_max_neighbors"], "judge_max_neighbors", 1, 5)
    result["judge_timeout_seconds"] = _integer(result["judge_timeout_seconds"], "judge_timeout_seconds", 1, 60)
    result["judge_job_seconds"] = _integer(result["judge_job_seconds"], "judge_job_seconds", 1, 86400)
    result["pair_jaccard_gate"] = _number(result["pair_jaccard_gate"], "pair_jaccard_gate", 0, 1)
    result["pair_containment_gate"] = _number(result["pair_containment_gate"], "pair_containment_gate", 0, 1)
    result["pair_cosine_gate"] = _number(result["pair_cosine_gate"], "pair_cosine_gate", -1, 1)
    result["judge_record_fraction"] = _number(result["judge_record_fraction"], "judge_record_fraction", 0, 1)
    result["mmr_lambda"] = _number(result["mmr_lambda"], "mmr_lambda", 0, 1)
    if result["retrieval_mode"] not in {"ranked", "semantic_mmr"}:
        raise RedundancyPolicyError("retrieval_mode must be ranked or semantic_mmr")
    if result["vector_storage"] not in {"full", "shared_input"}:
        raise RedundancyPolicyError("vector_storage must be full or shared_input")
    # Return a fresh JSON-safe mapping and normalize integral numeric values.
    return json.loads(canonical_policy_json(result))


def resolve_policy(mapping: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return resolve_redundancy_policy(mapping)


__all__ = ["POLICY_VERSION", "RedundancyPolicyError", "canonical_policy_json", "policy_fingerprint", "resolve_policy", "resolve_redundancy_policy"]
