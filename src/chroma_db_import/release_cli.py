from __future__ import annotations
import argparse, json, os, tempfile
from pathlib import Path
from .releases import ReleaseStore, export_fingerprint, export_representation_id, inspect_export_work, plan_release
from .upstream_contracts import parse_processed_delta
from .release_jobs import ReleaseJobStore, backup_store, consumer_checks, plan_job, restore_store, run_job

def _read(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def _write(path,value):
    target=Path(path); target.parent.mkdir(parents=True,exist_ok=True); handle,temporary=tempfile.mkstemp(dir=target.parent,prefix=f".{target.name}.",suffix=".tmp")
    try:
        with os.fdopen(handle,"w",encoding="utf-8",newline="\n") as stream: json.dump(value,stream,sort_keys=True,indent=2); stream.write("\n")
        os.replace(temporary,target)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
def main(argv=None):
    p=argparse.ArgumentParser(prog="chroma-release"); c=p.add_subparsers(dest="command",required=True)
    plan=c.add_parser("plan-release"); plan.add_argument("delta"); plan.add_argument("--store",required=True); plan.add_argument("--parent-release"); plan.add_argument("--embedding",required=True); plan.add_argument("--active-embedding"); plan.add_argument("--selection",required=True); plan.add_argument("--export",required=True); plan.add_argument("--output",required=True); plan.add_argument("--reconcile-removals",action="store_true"); plan.add_argument("--lexical-corpus")
    for name in ("stage","promote"):
        q=c.add_parser(name); q.add_argument("plan"); q.add_argument("--store",required=True); q.add_argument("--export"); q.add_argument("--payload"); q.add_argument("--approve"); q.add_argument("--lexical-corpus")
    status=c.add_parser("status"); status.add_argument("--store",required=True)
    rollback_plan=c.add_parser("plan-rollback"); rollback_plan.add_argument("release_id"); rollback_plan.add_argument("--store",required=True)
    rollback=c.add_parser("rollback"); rollback.add_argument("release_id"); rollback.add_argument("--store",required=True); rollback.add_argument("--approve",required=True)
    prune_plan=c.add_parser("plan-prune"); prune_plan.add_argument("release_ids",nargs="+"); prune_plan.add_argument("--store",required=True)
    prune=c.add_parser("prune"); prune.add_argument("release_ids",nargs="+"); prune.add_argument("--store",required=True); prune.add_argument("--approve",required=True)
    job_plan=c.add_parser("plan-job"); job_plan.add_argument("plan"); job_plan.add_argument("--export",required=True); job_plan.add_argument("--store",required=True); job_plan.add_argument("--jobs",required=True); job_plan.add_argument("--lexical-corpus")
    for command in ("run-job","resume-job"):
        job_run=c.add_parser(command); job_run.add_argument("job_id"); job_run.add_argument("--jobs",required=True); job_run.add_argument("--approve",required=True)
    job_status=c.add_parser("job-status"); job_status.add_argument("job_id"); job_status.add_argument("--jobs",required=True)
    job_cancel=c.add_parser("cancel-job"); job_cancel.add_argument("job_id"); job_cancel.add_argument("--jobs",required=True)
    backup=c.add_parser("backup"); backup.add_argument("--store",required=True); backup.add_argument("--destination",required=True)
    restore=c.add_parser("restore"); restore.add_argument("--backup",required=True); restore.add_argument("--destination",required=True)
    checks=c.add_parser("verify-consumers"); checks.add_argument("export")
    a=p.parse_args(argv)
    if a.command=="plan-job":
        value=plan_job(_read(a.plan),a.export,a.store,ReleaseJobStore(a.jobs),lexical_corpus=a.lexical_corpus); print(value["job_id"]); return 0
    if a.command in {"run-job","resume-job","job-status","cancel-job"}:
        jobs=ReleaseJobStore(a.jobs)
        if a.command in {"run-job","resume-job"}: value=run_job(jobs,a.job_id,approved_plan_id=a.approve)
        elif a.command=="cancel-job": value=jobs.cancel(a.job_id)
        else: value=jobs.load(a.job_id)
        print(json.dumps(value,sort_keys=True)); return 0
    if a.command=="backup": print(json.dumps(backup_store(a.store,a.destination),sort_keys=True)); return 0
    if a.command=="restore": print(json.dumps(restore_store(a.backup,a.destination),sort_keys=True)); return 0
    if a.command=="verify-consumers": print(json.dumps(consumer_checks(a.export),sort_keys=True)); return 0
    if a.command=="plan-release":
        store=ReleaseStore(a.store); parent_release=a.parent_release or store.active(); active_embedding=_read(a.active_embedding) if a.active_embedding else (store.load_release(parent_release).get("embedding_identity") if parent_release else None)
        value=plan_release(parse_processed_delta(a.delta),parent_release_id=parent_release,active_embedding=active_embedding,requested_embedding=_read(a.embedding),selection_fingerprint=a.selection,removal_mode="reconcile_approved" if a.reconcile_removals else "retain_advisory",export_bundle_fingerprint=export_fingerprint(a.export),vector_representation_id=export_representation_id(a.export),export_work=inspect_export_work(a.export),lexical_corpus=a.lexical_corpus); _write(a.output,value); print(value["plan_id"]); return 0
    store=ReleaseStore(a.store)
    if a.command=="status": print(json.dumps({"active_release_id":store.active(),"active_export_path":str(store.active_path()) if store.active_path() else None,"available_release_ids":sorted(path.name for path in store.releases.iterdir() if path.is_dir()),"staged_release_ids":sorted(path.name for path in store.staging.iterdir() if path.is_dir()),"retention_candidates":store.retention_candidates()},sort_keys=True)); return 0
    if a.command=="plan-rollback": print(store.rollback_plan_id(a.release_id)); return 0
    if a.command=="rollback": store.rollback(a.release_id,approved_plan_id=a.approve,rollback_plan_id=a.approve); return 0
    if a.command=="plan-prune": print(store.prune_plan_id(a.release_ids)); return 0
    if a.command=="prune": store.prune(a.release_ids,approved_plan_id=a.approve); return 0
    value=_read(a.plan)
    if a.command=="stage":
        if a.export: store.stage_export(value,a.export,lexical_corpus=a.lexical_corpus)
        else: store.stage(value,_read(a.payload) if a.payload else {})
        return 0
    store.promote(value,approved_plan_id=a.approve); return 0
if __name__=="__main__": raise SystemExit(main())
