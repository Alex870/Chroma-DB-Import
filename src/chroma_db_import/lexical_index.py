"""Portable immutable lexical-index-v1 sidecars with deterministic BM25 search."""
from __future__ import annotations
import hashlib, json, math, re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

CONTRACT="lexical-index-v1"; TOKENIZER="unicode-word-casefold-v1"; IMPLEMENTATION="podcast-bm25-v1"
class LexicalIndexError(ValueError): pass
def _canonical(value:Any)->bytes: return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
def _hash(value:Any)->str: return hashlib.sha256(_canonical(value)).hexdigest()
def tokenize(text:str)->list[str]: return re.findall(r"[^\W_]+(?:['-][^\W_]+)*",str(text or "").casefold(),flags=re.UNICODE)

def inspect_corpus(path:str|Path)->dict[str,Any]:
 value=json.loads(Path(path).read_text(encoding="utf-8"))
 if value.get("contract_version")!="representation-corpus-1.0": raise LexicalIndexError("unsupported representation corpus contract")
 ids=[str(item.get("document_id") or "") for item in value.get("documents") or []]
 if not ids or any(not item for item in ids) or len(ids)!=len(set(ids)): raise LexicalIndexError("representation corpus requires unique stable document IDs")
 terms=sum(len(tokenize(str(item.get("lexical_text") or ""))) for item in value.get("documents") or [])
 return {"source_corpus_hash":_hash(value),"document_count":len(ids),"representation_id":str((value.get("representations") or {}).get("lexical_text") or ""),"document_ids_hash":_hash(sorted(ids)),"work_estimate":{"source_bytes":Path(path).stat().st_size,"term_count":terms,"estimated_index_bytes":max(Path(path).stat().st_size,terms*24)}}

def build_sidecar(corpus_path:str|Path,output_path:str|Path,*,parent_release_id:str)->dict[str,Any]:
 corpus=json.loads(Path(corpus_path).read_text(encoding="utf-8")); evidence=inspect_corpus(corpus_path)
 rows=[]; vocabulary=Counter()
 for item in sorted(corpus["documents"],key=lambda row:row["document_id"]):
  terms=Counter(tokenize(item["lexical_text"])); vocabulary.update(terms)
  metadata=item.get("metadata") or {}
  rows.append({"document_id":item["document_id"],"source_hash":_hash({"id":item["document_id"],"lexical_text":item["lexical_text"]}),"length":sum(terms.values()),"terms":dict(sorted(terms.items())),"filters":{"speaker":metadata.get("speaker"),"speakers":metadata.get("speakers") or [],"episode_date":metadata.get("episode_date"),"episode_sort_key":metadata.get("episode_sort_key"),"node_type":metadata.get("node_type")}})
 smoke=[]
 for row in rows[:3]:
  term=next(iter(row["terms"]),"");smoke.append({"document_id":row["document_id"],"term":term,"passed":bool(term)})
 manifest={"contract_version":CONTRACT,"parent_corpus_release_id":parent_release_id,"representation_id":evidence["representation_id"],"tokenizer":{"id":TOKENIZER,"lowercase":True},"implementation":{"id":IMPLEMENTATION,"version":"1.0"},"field_weights":{"lexical_text":1.0},"document_count":len(rows),"ordered_document_ids":[row["document_id"] for row in rows],"document_ids_hash":evidence["document_ids_hash"],"source_corpus_hash":evidence["source_corpus_hash"],"work_estimate":evidence["work_estimate"],"build_diagnostics":{"total_terms":sum(row["length"] for row in rows),"unique_terms":len(vocabulary),"smoke_tests":smoke},"documents":rows}
 manifest["channel_id"]="lexical_"+_hash(manifest); manifest["checksum"]=_hash({k:v for k,v in manifest.items() if k!="checksum"})
 Path(output_path).write_text(json.dumps(manifest,sort_keys=True,indent=2,ensure_ascii=True)+"\n",encoding="utf-8"); return manifest

def validate_sidecar(value:Mapping[str,Any],*,release_id:str|None=None,expected:Mapping[str,Any]|None=None)->None:
 if value.get("contract_version")!=CONTRACT: raise LexicalIndexError("unsupported lexical sidecar contract")
 if release_id and value.get("parent_corpus_release_id")!=release_id: raise LexicalIndexError("lexical sidecar release mismatch")
 if value.get("checksum")!=_hash({k:v for k,v in value.items() if k!="checksum"}): raise LexicalIndexError("lexical sidecar checksum mismatch")
 ids=list(value.get("ordered_document_ids") or []); rows=list(value.get("documents") or [])
 if ids!=[row.get("document_id") for row in rows] or len(ids)!=value.get("document_count"): raise LexicalIndexError("lexical sidecar document alignment mismatch")
 if not all(item.get("passed") for item in (value.get("build_diagnostics") or {}).get("smoke_tests") or []): raise LexicalIndexError("lexical sidecar smoke test failed")
 if expected:
  for key in ("document_count","document_ids_hash","source_corpus_hash","representation_id"):
   if expected.get(key)!=value.get(key): raise LexicalIndexError(f"lexical sidecar {key} mismatch")

def search(value:Mapping[str,Any],query:str,*,limit:int=30,speaker:str="",date_start:str="",date_end:str="")->list[dict[str,Any]]:
 validate_sidecar(value); terms=tokenize(query); rows=list(value["documents"]); total=len(rows); average=sum(row["length"] for row in rows)/max(1,total); dfs=Counter()
 for row in rows:
  for term in set(row["terms"]): dfs[term]+=1
 results=[]
 for row in rows:
  filters=row.get("filters") or {}; speakers=set(map(str,filters.get("speakers") or [])); speakers.add(str(filters.get("speaker") or "")); date=str(filters.get("episode_date") or "")
  if speaker and speaker not in speakers: continue
  if date_start and (not date or date<date_start): continue
  if date_end and (not date or date>date_end): continue
  score=0.0
  for term in terms:
   tf=int(row["terms"].get(term,0)); df=dfs[term]
   if tf: score+=math.log(1+(total-df+0.5)/(df+0.5))*((tf*2.2)/(tf+1.2*(0.25+0.75*row["length"]/max(average,1))))
  if score>0: results.append({"document_id":row["document_id"],"score":score})
 return sorted(results,key=lambda item:(-item["score"],item["document_id"]))[:limit]
