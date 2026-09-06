import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from chroma_db_import.local_judge_client import JudgmentCache, LMStudioClient, LocalJudgeError, run_bounded_judgments
from chroma_db_import.redundancy_judge import build_judge_request
from chroma_db_import.redundancy_models import AnalysisUnit, CandidatePair


class _JudgeHandler(BaseHTTPRequestHandler):
    payloads = []
    valid_response = False

    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.path == "/v1/models":
            body = json.dumps({"data": [{"id": "fake-model"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        _JudgeHandler.payloads.append(json.loads(self.rfile.read(size).decode("utf-8")))
        content = {}
        if _JudgeHandler.valid_response:
            data = _JudgeHandler.payloads[-1]["messages"][1]["content"]
            source = json.loads(data)
            candidate = source["candidate"]
            comparison = source["comparisons"][0]
            content = {"candidate_id": candidate["document_id"], "relation": "equivalent", "matched_ids": [comparison["document_id"]], "evidence": [{"candidate_quote": candidate["text"], "matched_id": comparison["document_id"], "matched_quote": comparison["text"]}], "novel_quotes": [], "conflict_quotes": [], "attribution_changed": False, "time_changed": False, "qualification_changed": False, "reason": "same claim"}
        body = json.dumps({"model": "fake-model", "choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalJudgeClientTests(unittest.TestCase):
    def setUp(self):
        _JudgeHandler.payloads = []
        _JudgeHandler.valid_response = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _JudgeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_fake_models_and_chat_are_bounded_and_tool_free(self):
        client = LMStudioClient(self.base_url, model="fake-model", timeout=2)
        self.assertEqual("fake-model", client.list_models()[0]["id"])
        result = client.chat({"messages": [{"role": "user", "content": "hello"}], "tools": [{"type": "function"}], "temperature": 0.7, "max_tokens": 9999})
        self.assertEqual({}, result["content"])
        posted = _JudgeHandler.payloads[-1]
        self.assertEqual("fake-model", posted["model"])
        self.assertFalse(posted["stream"])
        self.assertEqual(0, posted["temperature"])
        self.assertEqual(768, posted["max_tokens"])
        self.assertEqual({"enable_thinking": False}, posted["chat_template_kwargs"])
        self.assertNotIn("tools", posted)

    def test_explicit_model_is_required(self):
        client = LMStudioClient(self.base_url)
        with self.assertRaises(LocalJudgeError):
            client.chat({"messages": []})

    def test_bounded_runner_validates_and_records_a_fake_pilot(self):
        _JudgeHandler.valid_response = True
        units = [AnalysisUnit("a", ("a",), "alpha", {"episode_uid": "e1"}, "leaf", "e1", "cache-a"), AnalysisUnit("b", ("b",), "beta", {"episode_uid": "e2"}, "leaf", "e2", "cache-b")]
        pair = CandidatePair("a", "b", channels=("lexical",), scores={"jaccard": 1.0})
        request = build_judge_request(units[0], [units[1]], {"judge_max_request_bytes": 24576})
        result = run_bounded_judgments(LMStudioClient(self.base_url, model="fake-model", timeout=2), [request], candidate_ids=["a"], supplied_units={unit.document_id: unit for unit in units}, pairs=[pair], max_calls=1, job_seconds=10)
        self.assertEqual("completed", result["status"])
        self.assertEqual(1, result["calls"])
        self.assertEqual("equivalent", result["judgments"][0]["relation"])

    def test_zero_budget_is_explicit_and_makes_no_request(self):
        result = run_bounded_judgments(LMStudioClient(self.base_url, model="fake-model", timeout=2), [], candidate_ids=[], supplied_units={}, max_calls=0, job_seconds=10)
        self.assertEqual("zero_budget", result["status"])
        self.assertEqual(0, result["calls"])
        self.assertEqual([], _JudgeHandler.payloads)

    def test_schema_validated_judgment_cache_is_reused(self):
        _JudgeHandler.valid_response = True
        units = [AnalysisUnit("a", ("a",), "alpha", {"episode_uid": "e1", "speaker": "Host"}, "leaf", "e1", "cache-a"), AnalysisUnit("b", ("b",), "beta", {"episode_uid": "e2", "speaker": "Host"}, "leaf", "e2", "cache-b")]
        pair = CandidatePair("a", "b", channels=("lexical",), scores={"jaccard": 1.0})
        request = build_judge_request(units[0], [units[1]])
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test_tmp") as directory:
            cache = JudgmentCache(Path(directory) / "judgment-cache.json")
            client = LMStudioClient(self.base_url, model="fake-model", timeout=2)
            first = run_bounded_judgments(client, [request], candidate_ids=["a"], supplied_units={unit.document_id: unit for unit in units}, pairs=[pair], max_calls=1, job_seconds=10, cache=cache, cache_keys=["stable-key"])
            posted_count = len(_JudgeHandler.payloads)
            second = run_bounded_judgments(client, [request], candidate_ids=["a"], supplied_units={unit.document_id: unit for unit in units}, pairs=[pair], max_calls=1, job_seconds=10, cache=cache, cache_keys=["stable-key"])
        self.assertEqual("completed", first["status"])
        self.assertEqual("equivalent", second["judgments"][0]["relation"])
        self.assertEqual(0, second["calls"])
        self.assertEqual(posted_count, len(_JudgeHandler.payloads))
        self.assertTrue(second["call_records"][0]["cache_hit"])


if __name__ == "__main__":
    unittest.main()
