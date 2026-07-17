"""Corpus-release-v1 plans and an atomic local release lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


CONTRACT = "corpus-release-v1"
MUTABLE = {"notes", "display_label", "ui_state", "release_id", "plan_id"}


class ReleaseError(ValueError):
    pass



def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _id(value: Mapping[str, Any], prefix: str) -> str:
    payload = {key: deepcopy(item) for key, item in value.items() if key not in MUTABLE}
    return f"{prefix}_{hashlib.sha256(_canonical(payload)).hexdigest()}"


def plan_release(delta: Mapping[str, Any], *, parent_release_id: str | None,
                 active_embedding: Mapping[str, Any] | None, requested_embedding: Mapping[str, Any],
                 selection_fingerprint: str, source_cache_ids: list[str] | None = None) -> dict[str, Any]:
    mismatch = active_embedding is not None and active_embedding != requested_embedding
    added = len(delta.get("added_document_ids", [])); changed = len(delta.get("changed_document_ids", []))
    removed = len(delta.get("removed_document_ids", []))
    plan: dict[str, Any] = {
        "contract_version": CONTRACT,
        "producer": {"name": "chroma-db-import", "contract_version": "1"},
        "parent_release_id": parent_release_id,
        "source_cache_ids": sorted(source_cache_ids or delta.get("parent_cache_ids", [])),
        "applied_delta_ids": [delta["delta_id"]],
        "representation_fingerprint": delta["representation_fingerprint"],
        "embedding_identity": dict(requested_embedding),
        "selection_fingerprint": selection_fingerprint,
        "vector_operations": {"add": added, "update": changed, "remove_advisory": removed},
        "smoke_tests": [], "storage": {}, "timings_ms": {},
        "rollback_release_id": parent_release_id,
        "retention_state": "planned",
        "separate_release_required": mismatch,
        "legacy_reduced_evaluability": False,
        "validation": {"delta_counts_reconciled": True},
    }
    plan["release_id"] = _id(plan, "release")
    plan["plan_id"] = _id({"release": plan, "action": "stage_and_promote"}, "release_plan")
    validate_release(plan)
    return plan


def wrap_legacy_export(metadata: Mapping[str, Any]) -> dict[str, Any]:
    wrapped = dict(metadata)
    wrapped.setdefault("contract_version", CONTRACT)
    wrapped.setdefault("release_id", None)
    wrapped["legacy_reduced_evaluability"] = True
    return wrapped


def validate_release(release: Mapping[str, Any]) -> None:
    if release.get("contract_version") != CONTRACT:
        raise ReleaseError("unsupported corpus release contract")
    if release.get("release_id") != _id(release, "release"):
        raise ReleaseError("corpus release identity mismatch")
    ops = release.get("vector_operations", {})
    if any(not isinstance(ops.get(key), int) or ops[key] < 0 for key in ("add", "update", "remove_advisory")):
        raise ReleaseError("invalid vector operation counts")


def add_release_to_podcast(podcast: Mapping[str, Any], release_id: str | None) -> dict[str, Any]:
    result = dict(podcast)
    if release_id is not None:
        result["corpus_release_id"] = release_id
    return result


class ReleaseStore:
    def __init__(self, root: str | Path, *, retain: int = 3):
        self.root = Path(root); self.retain = retain
        self.releases = self.root / "releases"; self.staging = self.root / "staging"
        self.root.mkdir(parents=True, exist_ok=True); self.releases.mkdir(exist_ok=True); self.staging.mkdir(exist_ok=True)

    @property
    def active_file(self) -> Path: return self.root / "active-release.json"

    def active(self) -> str | None:
        if not self.active_file.exists(): return None
        return json.loads(self.active_file.read_text(encoding="utf-8"))["release_id"]

    def stage(self, plan: Mapping[str, Any], payload: Mapping[str, Any], *, cancel: bool = False) -> Path:
        validate_release(plan)
        destination = self.staging / str(plan["release_id"])
        if cancel: return destination
        if destination.exists(): shutil.rmtree(destination)
        destination.mkdir(parents=True)
        (destination / "release.json").write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (destination / "payload.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return destination

    def promote(self, plan: Mapping[str, Any], *, approved_plan_id: str) -> str:
        validate_release(plan)
        if approved_plan_id != plan.get("plan_id"):
            raise ReleaseError("human approval does not match release plan")
        release_id = str(plan["release_id"]); staged = self.staging / release_id
        if not staged.exists(): raise ReleaseError("release is not staged")
        destination = self.releases / release_id
        os.replace(staged, destination)
        temporary = self.root / ".active-release.tmp"
        temporary.write_text(json.dumps({"release_id": release_id}, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.active_file)
        self._prune(known_good=str(plan.get("rollback_release_id") or ""))
        return release_id

    def rollback(self, release_id: str, *, approved_plan_id: str, rollback_plan_id: str) -> str:
        expected = _id({"action": "rollback", "target_release_id": release_id}, "rollback_plan")
        if rollback_plan_id != expected or approved_plan_id != expected:
            raise ReleaseError("human approval does not match rollback plan")
        if not (self.releases / release_id / "release.json").exists():
            raise ReleaseError("rollback release is unavailable")
        temporary = self.root / ".active-release.tmp"
        temporary.write_text(json.dumps({"release_id": release_id}, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.active_file); return release_id

    @staticmethod
    def rollback_plan_id(release_id: str) -> str:
        return _id({"action": "rollback", "target_release_id": release_id}, "rollback_plan")

    def _prune(self, *, known_good: str) -> None:
        items = sorted(self.releases.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        keep = {item.name for item in items[:self.retain]}; keep.add(known_good); keep.add(self.active() or "")
        for item in items:
            if item.name not in keep: shutil.rmtree(item)
