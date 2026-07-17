import unittest
from pathlib import Path
from chroma_db_import.upstream_contracts import parse_processed_delta
class UpstreamContractTests(unittest.TestCase):
 def test_consumes_vendored_delta_fixture(self):
  path=Path(__file__).parent/"fixtures/contracts/podcast-rag/processed-delta-v1/valid.json"
  self.assertEqual("processed-delta-v1",parse_processed_delta(path)["contract_version"])
if __name__=="__main__": unittest.main()
