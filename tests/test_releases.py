import json, tempfile, unittest
from pathlib import Path
from chroma_db_import.release_cli import main as release_cli_main
from chroma_db_import.releases import ReleaseError, ReleaseStore, add_release_to_podcast, export_fingerprint, inspect_export_work, plan_release, wrap_legacy_export
from chroma_db_import.representation import QWEN3_MODEL, QWEN3_MODEL_REVISION, QWEN3_PROFILE

DELTA={"contract_version":"processed-delta-v1","delta_id":"delta-1","representation_fingerprint":"repr-1","added_document_ids":["a"],"changed_document_ids":["b"],"removed_document_ids":["c"],"parent_cache_ids":["cache-1"],"validation":{"evidence_closure":True},"failures":[]}
QWEN_EMBEDDING={"model":QWEN3_MODEL,"model_revision":QWEN3_MODEL_REVISION,"dimension":2560,"provider":"sentence_transformers","normalize_embeddings":True,"distance_metric":"cosine","profile":QWEN3_PROFILE,"query_instruction_profile":"podcast-retrieval-v1"}
class ReleaseTests(unittest.TestCase):
 def test_mismatch_forces_separate_release_and_counts_reconcile(self):
  p=plan_release(DELTA,parent_release_id="old",active_embedding={**QWEN_EMBEDDING,"contextualization":"minimal"},requested_embedding={**QWEN_EMBEDDING,"contextualization":"full"},selection_fingerprint="s")
  self.assertTrue(p["separate_release_required"]); self.assertEqual(1,p["vector_operations"]["add"]); self.assertEqual(1,p["vector_operations"]["update"]); self.assertEqual(1,p["vector_operations"]["remove_advisory"])
 def test_failed_or_cancelled_staging_preserves_active(self):
  with tempfile.TemporaryDirectory() as d:
   store=ReleaseStore(d); plan=plan_release(DELTA,parent_release_id=None,active_embedding=None,requested_embedding=QWEN_EMBEDDING,selection_fingerprint="s")
   store.stage(plan,{},cancel=True); self.assertIsNone(store.active())
   with self.assertRaises(ReleaseError): store.promote(plan,approved_plan_id=plan["plan_id"])
   self.assertIsNone(store.active())
 def test_promotion_and_rollback_are_approved_and_atomic(self):
  scratch=Path(__file__).parent.parent/".test_tmp"; scratch.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=scratch) as d:
   root=Path(d); store=ReleaseStore(root/"store"); export=self.write_export(root)
   first=plan_release(DELTA,parent_release_id=None,active_embedding=None,requested_embedding=QWEN_EMBEDDING,selection_fingerprint="s1",removal_mode="reconcile_approved",export_bundle_fingerprint=export_fingerprint(export),vector_representation_id="repr-1"); store.stage_export(first,export); store.promote(first,approved_plan_id=first["plan_id"])
   second=plan_release(DELTA,parent_release_id=first["release_id"],active_embedding=QWEN_EMBEDDING,requested_embedding=QWEN_EMBEDDING,selection_fingerprint="s2",removal_mode="reconcile_approved",export_bundle_fingerprint=export_fingerprint(export),vector_representation_id="repr-1"); store.stage_export(second,export); store.promote(second,approved_plan_id=second["plan_id"])
   rid=store.rollback_plan_id(first["release_id"]); store.rollback(first["release_id"],approved_plan_id=rid,rollback_plan_id=rid); self.assertEqual(first["release_id"],store.active())
 def test_legacy_and_additive_podcast_metadata(self):
  self.assertTrue(wrap_legacy_export({})["legacy_reduced_evaluability"]); self.assertEqual("r",add_release_to_podcast({},"r")["corpus_release_id"])
 def write_export(self,root,*,mode="reconcile",representation="repr-1",model=QWEN3_MODEL):
  export=Path(root)/"export"; export.mkdir()
  (export/"chroma.sqlite3").write_bytes(b"fixture")
  rep={**QWEN_EMBEDDING,"model_id":QWEN3_MODEL,"representation_id":representation,"query_document_mode":"separate-query-instruction","contextualization":"minimal","context_header_version":"1.0","pooling":"mean","index_schema_version":"1.0","implementation_version":"stage-04-v2","inference_dtype":"bfloat16","output_dimension":None}
  (export/"podcast.json").write_text(json.dumps({"database_id":"db","collection_name":"c__qwen3-embedding-4b-shadow","embedding_model":model,"embedding_model_revision":QWEN3_MODEL_REVISION,"representation_id":representation,"embedding_dimension":2560,"representation":rep,"episodes":[],"speakers":[]}),encoding="utf-8")
  (export/"import_manifest.json").write_text(json.dumps({"representation_id":representation,"embedding_model":model,"embedding_dimension":2560,"representation":rep,"operation":{"mode":mode,"elapsed_seconds":12.5},"validation":{"valid":True},"staging":{"valid":True,"smoke_query_ids":["a"]},"embedding_cache":{"hits":7,"misses":2},"reconciliation":{"added":["a"],"changed":["b"],"removed":["c"],"unchanged":["d"]}}),encoding="utf-8")
  return export
 def test_export_work_inspection_is_grounded_in_real_bundle(self):
  scratch=Path(__file__).parent.parent/".test_tmp"; scratch.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=scratch) as d:
   export=self.write_export(Path(d)); work=inspect_export_work(export)
   self.assertEqual({"add":1,"update":1,"metadata_only":0,"remove":1,"unchanged":1},work["vector_work"])
   self.assertEqual({"embedding_hits":7,"embedding_misses":2},work["cache_reuse"])
   self.assertEqual("measured_import",work["duration"]["basis"]); self.assertEqual(12.5,work["duration"]["seconds"])
   self.assertGreater(work["storage"]["export_bytes"],0); self.assertEqual(3,work["storage"]["file_count"])
 def test_real_export_staging_writes_release_metadata_and_promotes_consumer_path(self):
  scratch=Path(__file__).parent.parent/".test_tmp"; scratch.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=scratch) as d:
   root=Path(d); store=ReleaseStore(root/"store"); export=self.write_export(root)
   plan=plan_release(DELTA,parent_release_id=None,active_embedding=None,requested_embedding=QWEN_EMBEDDING,selection_fingerprint="s",removal_mode="reconcile_approved",export_bundle_fingerprint=export_fingerprint(export),vector_representation_id="repr-1")
   staged=store.stage_export(plan,export); self.assertTrue((staged/"export/chroma.sqlite3").exists())
   podcast=json.loads((staged/"export/podcast.json").read_text(encoding="utf-8")); manifest=json.loads((staged/"export/import_manifest.json").read_text(encoding="utf-8"))
   self.assertEqual(plan["release_id"],podcast["corpus_release_id"]); self.assertEqual(plan["release_id"],manifest["corpus_release_id"])
   store.promote(plan,approved_plan_id=plan["plan_id"]); self.assertEqual(store.releases/plan["release_id"]/"export",store.active_path())
 def test_export_mismatch_fails_without_changing_active_release(self):
  scratch=Path(__file__).parent.parent/".test_tmp"; scratch.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=scratch) as d:
   root=Path(d); store=ReleaseStore(root/"store"); export=self.write_export(root,representation="wrong")
   plan=plan_release(DELTA,parent_release_id=None,active_embedding=None,requested_embedding=QWEN_EMBEDDING,selection_fingerprint="s",removal_mode="reconcile_approved",export_bundle_fingerprint=export_fingerprint(export),vector_representation_id="repr-1")
   with self.assertRaisesRegex(ReleaseError,"representation"): store.stage_export(plan,export)
   self.assertIsNone(store.active())
 def test_retention_never_deletes_without_separate_approval(self):
  scratch=Path(__file__).parent.parent/".test_tmp"; scratch.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=scratch) as d:
   root=Path(d); store=ReleaseStore(root/"store",retain=1); export=self.write_export(root); releases=[]
   for index in range(3):
    plan=plan_release(DELTA,parent_release_id=store.active(),active_embedding=None,requested_embedding=QWEN_EMBEDDING,selection_fingerprint=str(index),removal_mode="reconcile_approved",export_bundle_fingerprint=export_fingerprint(export),vector_representation_id="repr-1"); store.stage_export(plan,export); store.promote(plan,approved_plan_id=plan["plan_id"]); releases.append(plan["release_id"])
   self.assertEqual(3,len(list(store.releases.iterdir())))
   candidates=store.retention_candidates(); approval=store.prune_plan_id(candidates)
   with self.assertRaisesRegex(ReleaseError,"approval"): store.prune(candidates,approved_plan_id="wrong")
   self.assertEqual(sorted(candidates),sorted(store.prune(candidates,approved_plan_id=approval)))
 def test_release_cli_runs_validated_delta_to_consumer_readable_release(self):
  scratch=Path(__file__).parent.parent/".test_tmp"; scratch.mkdir(exist_ok=True)
  fixture=Path(__file__).parent/"fixtures/contracts/podcast-rag/processed-delta-v1/valid.json"
  with tempfile.TemporaryDirectory(dir=scratch) as d:
   root=Path(d); export=self.write_export(root,mode="update",representation="vector-space-1")
   manifest_path=export/"import_manifest.json"; manifest=json.loads(manifest_path.read_text(encoding="utf-8")); manifest["reconciliation"]={"added":[],"changed":["leaf-1"],"removed":[]}; manifest_path.write_text(json.dumps(manifest),encoding="utf-8")
   embedding=root/"embedding.json"; embedding.write_text(json.dumps(QWEN_EMBEDDING),encoding="utf-8"); plan_path=root/"plan.json"; store_path=root/"store"
   self.assertEqual(0,release_cli_main(["plan-release",str(fixture),"--store",str(store_path),"--embedding",str(embedding),"--selection","selection-1","--export",str(export),"--output",str(plan_path)]))
   plan=json.loads(plan_path.read_text(encoding="utf-8")); self.assertEqual("page-content-v1",plan["representation_fingerprint"]); self.assertEqual("vector-space-1",plan["vector_representation_id"]); self.assertEqual(12.5,plan["work_estimate"]["duration"]["seconds"]); self.assertEqual(0,release_cli_main(["stage",str(plan_path),"--store",str(store_path),"--export",str(export)])); self.assertEqual(0,release_cli_main(["promote",str(plan_path),"--store",str(store_path),"--approve",plan["plan_id"]]))
   self.assertTrue((store_path/"releases"/plan["release_id"]/"export/podcast.json").exists())
   next_plan_path=root/"next-plan.json"; self.assertEqual(0,release_cli_main(["plan-release",str(fixture),"--store",str(store_path),"--embedding",str(embedding),"--selection","selection-2","--export",str(export),"--output",str(next_plan_path)])); next_plan=json.loads(next_plan_path.read_text(encoding="utf-8")); self.assertEqual(plan["release_id"],next_plan["parent_release_id"]); self.assertFalse(next_plan["separate_release_required"])
if __name__=="__main__": unittest.main()
