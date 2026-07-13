from __future__ import annotations

import json
import re
from typing import Any

def document_speakers(metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    speaker = metadata.get("speaker")
    if isinstance(speaker, str) and speaker.strip() and speaker.lower() not in {"unknown", "multiple", "mixed"}:
        values.append(speaker.strip())

    speakers = metadata.get("speakers")
    if isinstance(speakers, str):
        try:
            speakers = json.loads(speakers)
        except json.JSONDecodeError:
            speakers = [part.strip() for part in speakers.split(",")]
    if isinstance(speakers, list):
        for item in speakers:
            if isinstance(item, str) and item.strip() and item.lower() not in {"unknown", "multiple", "mixed"}:
                values.append(item.strip())

    return list(dict.fromkeys(values))

def first_present(values) -> Any:  # type: ignore[no-untyped-def]
    for value in values:
        if value not in (None, ""):
            return value
    return None

def slugify(value: str) -> str:
    slug = "".join(character.lower() if character.isalnum() else "-" for character in value)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "podcast"

def safe_folder_name(value: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", value).strip()
    safe = re.sub(r"\s+", " ", safe)
    return safe or "Podcast Export"
