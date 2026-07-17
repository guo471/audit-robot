from __future__ import annotations
import argparse, json, shutil
from pathlib import Path
from guobu_audit_contract import item_order_id, load_jsonl, network_failure

def task_id(path):
    x=json.loads(path.read_text(encoding="utf-8-sig")); return str(x.get("channel_order_no") or x.get("task_id") or (x.get("row") or {}).get("id") or path.stem)

def main():
    p=argparse.ArgumentParser(); p.add_argument("--source-dir",action="append",required=True); p.add_argument("--out-dir",required=True)
    p.add_argument("--order-id",action="append",default=[]); p.add_argument("--order-ids-file"); p.add_argument("--timeout-jsonl"); p.add_argument("--count",type=int,default=0); p.add_argument("--summary-json"); a=p.parse_args()
    sources=[Path(x) for x in a.source_dir]; candidates={task_id(x):x for s in sources for x in sorted(s.glob("*.json"))}
    requested=[str(x).strip() for x in a.order_id if str(x).strip()]
    if a.order_ids_file: requested += [x.strip() for x in Path(a.order_ids_file).read_text(encoding="utf-8-sig").splitlines() if x.strip()]
    if a.timeout_jsonl: requested += [item_order_id(x) for x in load_jsonl(Path(a.timeout_jsonl)) if network_failure(x) and item_order_id(x)]
    elif not requested: requested=sorted(candidates)
    requested=list(dict.fromkeys(requested)); requested=requested[:a.count] if a.count>0 else requested
    missing=[x for x in requested if x not in candidates]
    if missing: raise SystemExit("Missing order IDs: "+",".join(missing))
    out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    for x in out.glob("*.json"): x.unlink()
    for i,x in enumerate(requested,1): shutil.copy2(candidates[x],out/f"{i:03d}_{x}.json")
    summary={"source_dirs":[str(x) for x in sources],"requested":len(requested),"selected":len(requested),"missing":missing,"orders":requested,"out_dir":str(out)}
    if a.summary_json: Path(a.summary_json).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))
if __name__ == "__main__": main()
