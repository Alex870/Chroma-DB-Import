"""Minimal LM Studio-compatible local judge client with safe caching."""

from __future__ import annotations

import hashlib
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .redundancy_judge import validate_judgment
from .redundancy_models import AnalysisUnit, CandidatePair, Judgment


DEFAULT_BASE_URL = "http://localhost:1234/v1"


class LocalJudgeError(RuntimeError):
    pass


def _base_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value or DEFAULT_BASE_URL)
    if parsed.scheme != "http" or not parsed.hostname:
        raise LocalJudgeError("judge endpoint must be an explicit HTTP host")
    host = parsed.hostname.lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise LocalJudgeError("non-loopback judge endpoints require an explicit allowlist")
    return value.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise LocalJudgeError("judge endpoint redirects are disabled")


class LMStudioClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, model: str | None = None, timeout: float = 30.0, auth_token: str | None = None, allow_hosts: set[str] | None = None):
        parsed = urllib.parse.urlparse(base_url or DEFAULT_BASE_URL)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "http" or not host:
            raise LocalJudgeError("judge endpoint must use an explicit HTTP URL")
        if host not in {"localhost", "127.0.0.1", "::1"} and host not in {str(item).lower() for item in (allow_hosts or set())}:
            raise LocalJudgeError("judge endpoint host is not on the explicit allowlist")
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = str(model) if model else ""
        self.timeout = float(timeout)
        self.auth_token = auth_token
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self.last_usage: Mapping[str, Any] | None = None
        self.last_elapsed_seconds: float | None = None

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        url = self.base_url + "/" + path.lstrip("/")
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if self.auth_token:
            headers["Authorization"] = "Bearer " + self.auth_token
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except (urllib.error.URLError, TimeoutError, socket.timeout, LocalJudgeError) as exc:
            raise LocalJudgeError(f"judge request failed: {type(exc).__name__}") from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalJudgeError("judge response was not valid JSON") from exc
        if not isinstance(value, dict):
            raise LocalJudgeError("judge response must be an object")
        return value

    def list_models(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/models")
        data = payload.get("data")
        if not isinstance(data, list):
            raise LocalJudgeError("judge model response has no model list")
        return [dict(item) for item in data if isinstance(item, Mapping)]

    def chat(self, request_payload: Mapping[str, Any]) -> dict[str, Any]:
        if not self.model:
            raise LocalJudgeError("an explicit model ID is required for chat completions")
        payload = {key: value for key, value in dict(request_payload).items() if not str(key).startswith("_") and key != "tools"}
        payload["model"] = self.model
        payload["stream"] = False
        payload["temperature"] = 0
        payload["max_tokens"] = 768
        # Match the RAG pipeline's request-scoped Qwen control.  Qwen3.8
        # enables reasoning by default; the judge needs a bounded direct JSON
        # response, so disable thinking through the vLLM chat template rather
        # than relying on a prompt convention.
        payload["chat_template_kwargs"] = {"enable_thinking": False}
        response = self._request("POST", "/chat/completions", payload)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise LocalJudgeError("judge response has no completion choice")
        choice = choices[0]
        message = choice.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str) or not content.strip():
            raise LocalJudgeError("judge completion has no message content")
        if choice.get("finish_reason") not in (None, "stop"):
            raise LocalJudgeError("judge completion was unfinished")
        served = str(response.get("model") or "")
        if served and served != self.model:
            raise LocalJudgeError("judge served-model ID does not match configured model")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LocalJudgeError("judge content is not JSON") from exc
        if not isinstance(parsed, dict):
            raise LocalJudgeError("judge content must be a JSON object")
        return {"content": parsed, "model": served or self.model, "usage": response.get("usage"), "finish_reason": choice.get("finish_reason")}

    def request_judgment(self, request_payload: Mapping[str, Any], *, candidate_id: str, supplied_units: Mapping[str, AnalysisUnit | Mapping[str, Any]], pair: CandidatePair | Mapping[str, Any] | None = None, allowed_matched_ids: Iterable[str] | None = None) -> Judgment:
        started = time.monotonic()
        self.last_usage = None
        try:
            response = self.chat(request_payload)
        except LocalJudgeError as exc:
            self.last_elapsed_seconds = round(time.monotonic() - started, 6)
            return Judgment(candidate_id, "uncertain", (), (), (), (), {}, "retain_evidence", str(exc))
        self.last_elapsed_seconds = round(time.monotonic() - started, 6)
        usage = response.get("usage")
        self.last_usage = dict(usage) if isinstance(usage, Mapping) else None
        return validate_judgment(response["content"], candidate_id=candidate_id, supplied_units=supplied_units, pair=pair, allowed_matched_ids=allowed_matched_ids)


def judgment_cache_key(*, base_fingerprint: str, scope: Mapping[str, Any], candidate: AnalysisUnit | Mapping[str, Any], comparisons: list[AnalysisUnit | Mapping[str, Any]], model_artifact_id: str | None, session_nonce: str | None, prompt_version: str, schema_version: str, generation: Mapping[str, Any]) -> str:
    def convert(value: Any) -> Any:
        if isinstance(value, AnalysisUnit):
            return value.as_dict()
        if isinstance(value, Mapping):
            return {str(key): convert(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
        if isinstance(value, (list, tuple)):
            return [convert(item) for item in value]
        return value
    identity = {
        "base_fingerprint": base_fingerprint,
        "scope": convert(scope),
        "candidate": convert(candidate),
        "comparisons": [convert(item) for item in comparisons],
        "model_artifact_id": model_artifact_id,
        "session_nonce": session_nonce,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "generation": convert(generation),
    }
    return "sha256:" + hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class JudgmentCache:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.values: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    self.values = {str(key): value for key, value in payload.items() if isinstance(value, dict) and value.get("validated")}
            except (OSError, json.JSONDecodeError):
                self.values = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.values.get(str(key))

    def put(self, key: str, judgment: Judgment, *, usage: Mapping[str, Any] | None = None) -> None:
        if judgment.status != "retain_evidence" or judgment.relation == "uncertain":
            return
        self.values[str(key)] = {"validated": True, "judgment": judgment.as_dict(), "usage": dict(usage or {})}
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.values, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def nonce(self) -> str:
        return uuid.uuid4().hex


def _request_comparison_ids(request: Mapping[str, Any]) -> set[str]:
    """Extract the request-local comparison IDs for response binding."""
    messages = request.get("messages")
    if not isinstance(messages, list):
        return set()
    user_content = next((item.get("content") for item in messages if isinstance(item, Mapping) and item.get("role") == "user"), None)
    if not isinstance(user_content, str):
        return set()
    try:
        data = json.loads(user_content)
    except json.JSONDecodeError:
        return set()
    comparisons = data.get("comparisons") if isinstance(data, Mapping) else None
    if not isinstance(comparisons, list):
        return set()
    return {str(item.get("document_id") or "") for item in comparisons if isinstance(item, Mapping) and str(item.get("document_id") or "")}


def run_bounded_judgments(
    client: LMStudioClient,
    requests: list[Mapping[str, Any]],
    *,
    candidate_ids: list[str],
    supplied_units: Mapping[str, AnalysisUnit | Mapping[str, Any]],
    pairs: list[CandidatePair | Mapping[str, Any] | None] | None = None,
    max_calls: int = 250,
    job_seconds: float = 900,
    cancel_check: Any | None = None,
    cache: JudgmentCache | None = None,
    cache_keys: list[str] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    if len(requests) != len(candidate_ids):
        raise LocalJudgeError("judge request and candidate counts differ")
    if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 0:
        raise LocalJudgeError("judge max_calls must be a non-negative integer")
    if isinstance(job_seconds, bool) or not isinstance(job_seconds, (int, float)) or not float(job_seconds) > 0:
        raise LocalJudgeError("judge job_seconds must be positive")
    started = time.monotonic()
    judgments: list[dict[str, Any]] = []
    call_records: list[dict[str, Any]] = []
    calls = 0
    if max_calls == 0:
        return {"status": "zero_budget", "judgments": judgments, "calls": calls, "elapsed_seconds": 0.0, "token_usage": None, "call_records": call_records}
    status = "completed"
    if progress_callback:
        progress_callback(0, len(requests), "Starting bounded judge calls.")
    for index, request in enumerate(requests):
        if calls >= max_calls:
            status = "completed_partial"
            break
        if time.monotonic() - started >= job_seconds:
            status = "completed_partial"
            break
        if cancel_check is not None and cancel_check():
            status = "cancelled"
            break
        if bool(request.get("_context_limit")):
            judgments.append(Judgment(candidate_ids[index], "uncertain", (), (), (), (), {}, "retain_evidence", "context_limit").as_dict())
            call_records.append({"candidate_id": candidate_ids[index], "elapsed_seconds": 0.0, "token_usage": None, "cache_hit": False, "status": "context_limit", "request_bytes": request.get("_request_bytes"), "removed_neighbors": request.get("_removed_neighbors", 0)})
            status = "completed_partial"
            continue
        key = cache_keys[index] if cache_keys and index < len(cache_keys) else None
        allowed_matched_ids = _request_comparison_ids(request)
        cached = cache.get(key) if cache and key else None
        if cached and isinstance(cached.get("judgment"), Mapping):
            cached_pair = pairs[index] if pairs and index < len(pairs) else None
            cached_payload = dict(cached["judgment"])
            # status and distinct_occurrence are derived fields included in
            # the portable Judgment artifact, but neither is part of the
            # strict raw model-response schema.
            cached_payload.pop("distinct_occurrence", None)
            cached_payload.pop("status", None)
            checked = validate_judgment(cached_payload, candidate_id=candidate_ids[index], supplied_units=supplied_units, pair=cached_pair, allowed_matched_ids=allowed_matched_ids)
            if checked.status == "retain_evidence" and checked.relation != "uncertain":
                judgments.append(checked.as_dict())
                cached_usage = cached.get("usage")
                call_records.append({"candidate_id": candidate_ids[index], "elapsed_seconds": 0.0, "token_usage": cached_usage if isinstance(cached_usage, Mapping) else None, "cache_hit": True, "status": "cached", "request_bytes": request.get("_request_bytes"), "removed_neighbors": request.get("_removed_neighbors", 0)})
                if progress_callback:
                    progress_callback(index + 1, len(requests), f"Reused judge result {index + 1} of {len(requests)}.")
                continue
        call_started = time.monotonic()
        judgment = client.request_judgment(request, candidate_id=candidate_ids[index], supplied_units=supplied_units, pair=(pairs[index] if pairs and index < len(pairs) else None), allowed_matched_ids=allowed_matched_ids)
        calls += 1
        elapsed = float(client.last_elapsed_seconds if client.last_elapsed_seconds is not None else time.monotonic() - call_started)
        usage = dict(client.last_usage) if isinstance(client.last_usage, Mapping) else None
        judgments.append(judgment.as_dict())
        call_records.append({"candidate_id": candidate_ids[index], "elapsed_seconds": round(elapsed, 6), "token_usage": usage, "cache_hit": False, "status": judgment.relation, "request_bytes": request.get("_request_bytes"), "removed_neighbors": request.get("_removed_neighbors", 0)})
        if progress_callback:
            progress_callback(index + 1, len(requests), f"Completed judge call {index + 1} of {len(requests)}.")
        if cache and key:
            cache.put(key, judgment, usage=usage)
        if time.monotonic() - call_started >= client.timeout:
            status = "completed_partial"
            break
        if judgment.relation == "uncertain" and any(token in judgment.reason.lower() for token in ("request failed", "unavailable", "timeout", "not json", "served-model", "unfinished")):
            status = "completed_partial"
            break
    totals: dict[str, int] = {}
    for record in call_records:
        usage = record.get("token_usage")
        if not isinstance(usage, Mapping):
            continue
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                totals[key] = totals.get(key, 0) + value
    return {"status": status, "judgments": judgments, "calls": calls, "elapsed_seconds": round(time.monotonic() - started, 6), "token_usage": totals or None, "call_records": call_records}


__all__ = ["DEFAULT_BASE_URL", "JudgmentCache", "LMStudioClient", "LocalJudgeError", "judgment_cache_key", "run_bounded_judgments"]
