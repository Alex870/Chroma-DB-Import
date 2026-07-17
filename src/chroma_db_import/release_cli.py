from __future__ import annotations
import argparse, json
from pathlib import Path
from .releases import ReleaseStore, plan_release

def _read(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def main(argv=None):
    p=argparse.ArgumentParser(prog="chroma-release"); c=p.add_subparsers(dest="command",required=True)
    plan=c.add_parser("plan-release"); plan.add_argument("delta"); plan.add_argument("--parent-release"); plan.add_argument("--embedding",required=True); plan.add_argument("--active-embedding"); plan.add_argument("--selection",required=True); plan.add_argument("--output",required=True)
    for name in ("stage","promote"):
        q=c.add_parser(name); q.add_argument("plan"); q.add_argument("--store",required=True); q.add_argument("--payload"); q.add_argument("--approve")
    status=c.add_parser("status"); status.add_argument("--store",required=True)
    rollback=c.add_parser("rollback"); rollback.add_argument("release_id"); rollback.add_argument("--store",required=True); rollback.add_argument("--approve",required=True)
    a=p.parse_args(argv)
    if a.command=="plan-release":
        value=plan_release(_read(a.delta),parent_release_id=a.parent_release,active_embedding=_read(a.active_embedding) if a.active_embedding else None,requested_embedding=_read(a.embedding),selection_fingerprint=a.selection); Path(a.output).write_text(json.dumps(value,sort_keys=True,indent=2)+"\n",encoding="utf-8"); print(value["plan_id"]); return 0
    store=ReleaseStore(a.store)
    if a.command=="status": print(json.dumps({"active_release_id":store.active()},sort_keys=True)); return 0
    if a.command=="rollback": store.rollback(a.release_id,approved_plan_id=a.approve,rollback_plan_id=a.approve); return 0
    value=_read(a.plan)
    if a.command=="stage": store.stage(value,_read(a.payload) if a.payload else {}); return 0
    store.promote(value,approved_plan_id=a.approve); return 0
if __name__=="__main__": raise SystemExit(main())
