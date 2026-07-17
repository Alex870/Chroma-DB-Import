"""Dependency-free parsers for producer-owned input contracts."""
import json
from pathlib import Path
from typing import Any
class UpstreamContractError(ValueError): pass
def parse_processed_delta(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("contract_version") != "processed-delta-v1":
        raise UpstreamContractError("unsupported processed delta contract")
    affected = list(value.get("changed_document_ids", [])) + list(value.get("removed_document_ids", []))
    if any(item not in value.get("reasons", {}) for item in affected):
        raise UpstreamContractError("processed delta is missing impact reasons")
    return value
