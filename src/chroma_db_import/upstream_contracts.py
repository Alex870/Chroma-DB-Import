"""Dependency-free parsers for producer-owned input contracts."""
import hashlib
import json
from pathlib import Path
from typing import Any
class UpstreamContractError(ValueError): pass
MUTABLE_IDENTITY_KEYS = {"notes", "display_label", "ui_state", "delta_id"}

def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def parse_processed_delta(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("contract_version") != "processed-delta-v1":
        raise UpstreamContractError("unsupported processed delta contract")
    affected = list(value.get("changed_document_ids", [])) + list(value.get("removed_document_ids", []))
    if any(item not in value.get("reasons", {}) for item in affected):
        raise UpstreamContractError("processed delta is missing impact reasons")
    identity = {key: item for key, item in value.items() if key not in MUTABLE_IDENTITY_KEYS}
    expected = f"delta_{hashlib.sha256(_canonical(identity)).hexdigest()}"
    if value.get("delta_id") != expected:
        raise UpstreamContractError("processed delta identity mismatch")
    if value.get("failures"):
        raise UpstreamContractError("failed processed delta cannot create a corpus release")
    if value.get("validation", {}).get("evidence_closure") is not True:
        raise UpstreamContractError("processed delta evidence closure is not valid")
    return value
