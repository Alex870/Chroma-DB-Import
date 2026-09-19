import json,tempfile,unittest
from pathlib import Path
from chroma_db_import.lexical_index import LexicalIndexError,build_sidecar,search,validate_sidecar
from chroma_db_import.releases import ReleaseStore,export_fingerprint,plan_release
from tests.test_releases import DELTA,QWEN_EMBEDDING,ReleaseTests

class LexicalIndexTests(unittest.TestCase):
 def corpus(self,path):
   value={"contract_version":"representation-corpus-1.0","representations":{"lexical_text":"normalized-lexical-v1"},"documents":[{"document_id":"a","lexical_text":"Dr Amara Voss discussed podcast","metadata":{"stable_document_id":"a","speaker":"Host","speaker_scope":"single","node_type":"leaf_chunk","episode_id":"episode-a","episode_uid":"episode-a","episode_date":"2026-01-17","episode_sort_key":"20260117","source_segment_id":"segment-a"}},{"document_id":"b","lexical_text":"institutional trust and policy","metadata":{"stable_document_id":"b","speaker":"Guest","speaker_scope":"single","node_type":"leaf_chunk","episode_id":"episode-b","episode_uid":"episode-b","episode_date":"2025-01-01","episode_sort_key":"20250101","source_segment_id":"segment-b"}}]}; path.write_text(json.dumps(value),encoding="utf-8"); return path
 def test_deterministic_bm25_filters_and_ties(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); sidecar=build_sidecar(self.corpus(root/"corpus.json"),root/"lexical.json",parent_release_id="r")
   self.assertEqual("a",search(sidecar,"podcast",speaker="Host",date_start="2026-01-01")[0]["document_id"])
   self.assertEqual([],search(sidecar,"podcast",speaker="Guest")); validate_sidecar(sidecar,release_id="r")
   with self.assertRaisesRegex(LexicalIndexError,"release mismatch"): validate_sidecar(sidecar,release_id="other")
 def test_release_staging_binds_optional_sidecar(self):
  scratch=Path(__file__).parent.parent/".test_tmp"; scratch.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=scratch) as d:
   root=Path(d); export=ReleaseTests().write_export(root); corpus=self.corpus(root/"corpus.json")
   podcast=json.loads((export/"podcast.json").read_text(encoding="utf-8"));podcast["collection_name"]="collection__qwen3-embedding-4b-shadow";(export/"podcast.json").write_text(json.dumps(podcast),encoding="utf-8")
   (export/"chroma.sqlite3").unlink();import chromadb
   client=chromadb.PersistentClient(path=str(export));collection=client.create_collection("collection__qwen3-embedding-4b-shadow");collection.add(ids=["a","b"],documents=["Dr Amara Voss discussed podcast","institutional trust and policy"],embeddings=[[1.0,0.0],[0.0,1.0]],metadatas=[{"speaker":"Host"},{"speaker":"Guest"}]);client._system.stop();chromadb.api.client.SharedSystemClient.clear_system_cache();del collection;del client
   plan=plan_release(DELTA,parent_release_id=None,active_embedding=None,requested_embedding=QWEN_EMBEDDING,selection_fingerprint="hybrid",removal_mode="reconcile_approved",export_bundle_fingerprint=export_fingerprint(export),vector_representation_id="repr-1",lexical_corpus=corpus)
   store=ReleaseStore(root/"store"); staged=store.stage_export(plan,export,lexical_corpus=corpus)
   sidecar=json.loads((staged/"export/lexical-index.json").read_text(encoding="utf-8")); validate_sidecar(sidecar,release_id=plan["release_id"])
   self.assertEqual(sidecar["channel_id"],json.loads((staged/"export/podcast.json").read_text(encoding="utf-8"))["retrieval_channels"][0]["channel_id"])
   chromadb.api.client.SharedSystemClient.clear_system_cache()
 def test_alignment_failure_never_changes_active_release(self):
  scratch=Path(__file__).parent.parent/".test_tmp";scratch.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=scratch) as d:
   root=Path(d);export=ReleaseTests().write_export(root);corpus=self.corpus(root/"corpus.json")
   podcast=json.loads((export/"podcast.json").read_text(encoding="utf-8"));podcast["collection_name"]="collection__qwen3-embedding-4b-shadow";(export/"podcast.json").write_text(json.dumps(podcast),encoding="utf-8")
   (export/"chroma.sqlite3").unlink();import chromadb
   client=chromadb.PersistentClient(path=str(export));collection=client.create_collection("collection__qwen3-embedding-4b-shadow");collection.add(ids=["a"],documents=["podcast"],embeddings=[[1.0,0.0]]);client._system.stop();chromadb.api.client.SharedSystemClient.clear_system_cache();del collection;del client
   plan=plan_release(DELTA,parent_release_id=None,active_embedding=None,requested_embedding=QWEN_EMBEDDING,selection_fingerprint="bad",removal_mode="reconcile_approved",export_bundle_fingerprint=export_fingerprint(export),vector_representation_id="repr-1",lexical_corpus=corpus);store=ReleaseStore(root/"store")
   with self.assertRaisesRegex(Exception,"exactly align"):store.stage_export(plan,export,lexical_corpus=corpus)
   self.assertIsNone(store.active())
   chromadb.api.client.SharedSystemClient.clear_system_cache()

if __name__=="__main__": unittest.main()
