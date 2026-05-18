#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,hashlib,sqlite3,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from tools.agents.lib.agent_contract import attach_contract

LEDGER=Path('/tmp/research_experiment_ledger.json')

def now(): return datetime.now(timezone.utc).isoformat()
def read_json(path):
    p=Path(path)
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc: return {'_read_error':str(exc),'_path':path}
def key_for(target, experiment_type): return hashlib.sha1(f'{target}|{experiment_type}'.encode()).hexdigest()[:12]
def strategy_snapshot(targets):
    out={}
    if not targets: return out
    conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    q=','.join('?' for _ in targets)
    for r in conn.execute(f"SELECT logic,status,samples,success_rate_pct,avg_excess_return_pct,recent_success_rate_pct,recent_avg_excess_return_pct,reason,updated_at FROM strategy_registry WHERE logic IN ({q})", targets).fetchall():
        out[r['logic']]={k:r[k] for k in r.keys()}
    conn.close(); return out
def recommendation_snapshot(targets):
    recs=read_json('/tmp/recommendations_latest.json'); out={}
    for r in recs.get('items') or []:
        if r.get('symbol') in targets:
            out[r['symbol']]={k:r.get(k) for k in ['symbol','score','action','recommendation_bucket','trade_eligible','validation_priority','risk_notes']}
            out[r['symbol']]['critic_summary']=(r.get('critic') or {}).get('summary')
    return out
def snapshot_for(targets):
    targets=[t for t in targets if t]
    return {'strategies':strategy_snapshot([t for t in targets if '.' not in t]),'recommendations':recommendation_snapshot([t for t in targets if '.' in t])}
def delta(before, after):
    changes={}
    for section in ('strategies','recommendations'):
        changes[section]={}
        keys=set((before.get(section) or {}).keys())|set((after.get(section) or {}).keys())
        for k in keys:
            b=(before.get(section) or {}).get(k) or {}; a=(after.get(section) or {}).get(k) or {}
            diff={}
            for field in set(b.keys())|set(a.keys()):
                if b.get(field)!=a.get(field): diff[field]={'before':b.get(field),'after':a.get(field)}
            if diff: changes[section][k]=diff
    return changes

def load_ledger():
    if not LEDGER.exists(): return {'schema':'research_experiment_ledger.v1','entries':[]}
    try: return json.loads(LEDGER.read_text(encoding='utf-8'))
    except Exception: return {'schema':'research_experiment_ledger.v1','entries':[]}

def main():
    ap=argparse.ArgumentParser(description='Append autonomous research experiment ledger with before/after deltas and repeat detection')
    ap.add_argument('--output',default='/tmp/research_experiment_ledger_latest.json')
    args=ap.parse_args()
    hyps=read_json('/tmp/research_hypotheses_latest.json').get('hypotheses') or []
    plans=read_json('/tmp/research_experiment_plan_latest.json').get('plans') or []
    results=read_json('/tmp/research_experiment_results_latest.json').get('results') or []
    judgments=read_json('/tmp/research_evidence_judge_latest.json').get('judgments') or []
    ledger=load_ledger(); entries=ledger.setdefault('entries',[])
    prior_keys={e.get('experiment_key'):e for e in entries}
    new_entries=[]; repeat_count=0; updated_repeats=[]
    by_judgment={tuple(j.get('hypothesis_ids') or []):j for j in judgments}
    for p in plans:
        targets=p.get('targets') or [p.get('target')]
        exp=p.get('experiment_type') or p.get('task')
        for t in targets:
            k=key_for(t,exp)
            repeated=k in prior_keys
            repeat_count += 1 if repeated else 0
            related=[r for r in results if t in (r.get('targets') or [])]
            j=None
            for jj in judgments:
                if t in (jj.get('targets') or []): j=jj; break
            # We only have after snapshot for first ledger integration; future runs will compare to prior after_snapshot.
            before=(prior_keys.get(k) or {}).get('after_snapshot') or {}
            after=snapshot_for([t])
            ent={'run_at':now(),'experiment_key':k,'repeated':repeated,'target':t,'experiment_type':exp,'hypothesis_ids':p.get('hypothesis_ids'),'task':p.get('task'),'decision':(j or {}).get('decision'),'reason':(j or {}).get('reason'),'result_outputs':[r.get('output') for r in related],'before_snapshot':before,'after_snapshot':after,'delta':delta(before,after),'real_trading':False,'authority':'ledger_only'}
            has_delta=bool(ent['delta'].get('strategies') or ent['delta'].get('recommendations'))
            if repeated and not has_delta:
                prev=prior_keys.get(k) or {}
                prev['last_seen_at']=now()
                prev['repeat_seen_count']=int(prev.get('repeat_seen_count') or 0)+1
                prev['last_repeat_decision']=ent.get('decision')
                prev['last_repeat_reason']=ent.get('reason')
                updated_repeats.append({'experiment_key':k,'target':t,'experiment_type':exp,'repeat_seen_count':prev['repeat_seen_count'],'reason':'deduped_repeated_without_delta'})
                continue
            entries.append(ent); new_entries.append(ent); prior_keys[k]=ent
    # keep bounded history
    ledger['entries']=entries[-500:]; ledger['updated_at']=now(); LEDGER.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
    useful=sum(1 for e in new_entries if e.get('delta') and (e['delta'].get('strategies') or e['delta'].get('recommendations')))
    packet={'run_at':now(),'mode':'research_experiment_ledger','real_trading':False,'new_entries':new_entries,'updated_repeats':updated_repeats,'summary':{'new_entry_count':len(new_entries),'repeat_count':repeat_count,'deduped_repeat_count':len(updated_repeats),'delta_count':useful,'ledger_size':len(ledger['entries'])}}
    warnings=['repeated_hypotheses_detected'] if repeat_count else []
    attach_contract(packet,'research_experiment_ledger',status='ok',outputs={'new_entry_count':len(new_entries),'deduped_repeat_count':len(updated_repeats)},metrics=packet['summary'],warnings=warnings,next_actions=['Use repeat/delta metrics to avoid stale experiments and prioritize useful hypotheses.'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
