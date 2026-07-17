import tempfile, unittest
from pathlib import Path
from chroma_db_import.releases import ReleaseError, ReleaseStore, add_release_to_podcast, plan_release, wrap_legacy_export

DELTA={"delta_id":"delta-1","representation_fingerprint":"repr-1","added_document_ids":["a"],"changed_document_ids":["b"],"removed_document_ids":["c"],"parent_cache_ids":["cache-1"]}
class ReleaseTests(unittest.TestCase):
 def test_mismatch_forces_separate_release_and_counts_reconcile(self):
  p=plan_release(DELTA,parent_release_id="old",active_embedding={"model":"a"},requested_embedding={"model":"b"},selection_fingerprint="s")
  self.assertTrue(p["separate_release_required"]); self.assertEqual({"add":1,"update":1,"remove_advisory":1},p["vector_operations"])
 def test_failed_or_cancelled_staging_preserves_active(self):
  with tempfile.TemporaryDirectory() as d:
   store=ReleaseStore(d); plan=plan_release(DELTA,parent_release_id=None,active_embedding=None,requested_embedding={"model":"a"},selection_fingerprint="s")
   store.stage(plan,{},cancel=True); self.assertIsNone(store.active())
   with self.assertRaises(ReleaseError): store.promote(plan,approved_plan_id=plan["plan_id"])
   self.assertIsNone(store.active())
 def test_promotion_and_rollback_are_approved_and_atomic(self):
  with tempfile.TemporaryDirectory() as d:
   store=ReleaseStore(d); first=plan_release(DELTA,parent_release_id=None,active_embedding=None,requested_embedding={"model":"a"},selection_fingerprint="s1"); store.stage(first,{}); store.promote(first,approved_plan_id=first["plan_id"])
   second=plan_release(DELTA,parent_release_id=first["release_id"],active_embedding={"model":"a"},requested_embedding={"model":"a"},selection_fingerprint="s2"); store.stage(second,{}); store.promote(second,approved_plan_id=second["plan_id"])
   rid=store.rollback_plan_id(first["release_id"]); store.rollback(first["release_id"],approved_plan_id=rid,rollback_plan_id=rid); self.assertEqual(first["release_id"],store.active())
 def test_legacy_and_additive_podcast_metadata(self):
  self.assertTrue(wrap_legacy_export({})["legacy_reduced_evaluability"]); self.assertEqual("r",add_release_to_podcast({},"r")["corpus_release_id"])
if __name__=="__main__": unittest.main()
