import json, tempfile, unittest
from pathlib import Path
from chroma_db_import.upstream_contracts import UpstreamContractError, parse_processed_delta
class UpstreamContractTests(unittest.TestCase):
 def test_consumes_vendored_delta_fixture(self):
  path=Path(__file__).parent/"fixtures/contracts/podcast-rag/processed-delta-v1/valid.json"
  self.assertEqual("processed-delta-v1",parse_processed_delta(path)["contract_version"])
 def test_rejects_tampered_or_failed_delta(self):
  fixture=Path(__file__).parent/"fixtures/contracts/podcast-rag/processed-delta-v1/valid.json"; payload=json.loads(fixture.read_text(encoding="utf-8"))
  scratch=Path(__file__).parent.parent/".test_tmp"; scratch.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=scratch) as d:
   path=Path(d)/"delta.json"; payload["changed_document_ids"].append("tampered"); path.write_text(json.dumps(payload),encoding="utf-8")
   with self.assertRaisesRegex(UpstreamContractError,"reason|identity"): parse_processed_delta(path)
if __name__=="__main__": unittest.main()
