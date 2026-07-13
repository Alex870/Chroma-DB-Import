from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

def load_state(path: Path) -> dict[str, Any]:
    """Load resumable import state, recovering cleanly from corrupt JSON."""
    if not path.exists():
        return {"version": 1, "files": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = path.with_suffix(f".corrupt.{int(time.time())}.json")
        backup.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        print(f"State file was invalid JSON. Backed it up to {backup} and starting fresh.")
        return {"version": 1, "files": {}}

def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, indent=2, ensure_ascii=True), encoding="utf-8")
    temp_path.replace(path)

def write_json(path: Path, payload: Any) -> None:
    """Atomically write a JSON payload to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    temp_path.replace(path)
