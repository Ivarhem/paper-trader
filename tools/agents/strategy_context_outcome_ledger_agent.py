#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sqlite3,sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db
from tools.agents.lib.agent_contract import attach_contract

def safe(text):
    try: return json.loads(text or '{}')
    except Exception: return {}

def avg(vals):
    vals=[v for v in vals if v is not None]
    return round(sum(vals)/len(vals),2) if vals else None

def main():
    ap=argparse.ArgumentParser(description='Record outcome by market context x selected strategy router arm')
    ap.add_argument('--horizon-days',type=int,default=1)
    ap.add_argument('--limit',type=int,default=3000)
    ap.add_argument('--output',default='/tmp/strategy_context_outcome_ledger_latest.json')
    args=ap.parse_args(); init_db(); conn=sqlite3.connect(get_settings().database_path,timeout=30); conn.row_factory=sqlite3.Row
    sql="""SELECT ro.*, rh.payload_json, rh.strategy_id, rh.score FROM recommendation_outcomes ro JOIN recommendation_history rh ON rh.id=ro.recommendation_history_id WHERE ro.horizon_days=? ORDER BY ro.id DESC LIMIT ?"""
    rows=conn.execute(sql,(args.horizon_days,args.limit)).fetchall(); conn.close()
    ledger=[]; groups={}
    for r in rows:
        payload=safe(r['payload_json']); vb=payload.get('validation_basis') or {}; router=vb.get('strategy_context_router') or {}; top=(router.get('top_signal_decisions') or [{}])[0] or {}
        regime=((router.get('regime_context') or {}).get('regime')) or top.get('regime') or 'unknown'
        logic=r['strategy_id'] or payload.get('strategy_id') or top.get('logic') or 'unknown'
        family=top.get('family') or 'unknown'; decision=top.get('decision') or 'unknown'
        key=(regime,family,logic,decision)
        item={'symbol':r['symbol'],'market':r['market'],'run_at':r['run_at'],'status':r['status'],'horizon_days':r['horizon_days'],'regime':regime,'family':family,'logic':logic,'router_decision':decision,'forward_return_pct':r['forward_return_pct'],'excess_return_pct':r['excess_return_pct'],'hit':r['hit'],'stopped_out':r['stopped_out']}
        ledger.append(item); groups.setdefault(key,[]).append(item)
    summary=[]
    for (regime,family,logic,decision), arr in groups.items():
        complete=[x for x in arr if x.get('status') in ('complete','stopped_out') and x.get('forward_return_pct') is not None]
        summary.append({'regime':regime,'family':family,'logic':logic,'router_decision':decision,'sample_count':len(arr),'complete_count':len(complete),'avg_forward_return_pct':avg([x.get('forward_return_pct') for x in complete]),'avg_excess_return_pct':avg([x.get('excess_return_pct') for x in complete]),'hit_rate_pct':round(sum(1 for x in complete if x.get('hit'))/len(complete)*100,2) if complete else None,'stopped_out_rate_pct':round(sum(1 for x in complete if x.get('stopped_out'))/len(complete)*100,2) if complete else None})
    summary=sorted(summary,key=lambda x:(x.get('complete_count') or 0,x.get('avg_excess_return_pct') or -999),reverse=True)
    warnings=[]
    if not ledger: warnings.append('no recommendation outcomes with router context found')
    if sum(x.get('complete_count') or 0 for x in summary) < 30: warnings.append('insufficient completed strategy-context outcome samples')
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'strategy_context_outcome_ledger','real_trading':False,'authority':'paper_research_feedback_loop','horizon_days':args.horizon_days,'summary':{'group_count':len(summary),'ledger_rows':len(ledger),'complete_rows':sum(x.get('complete_count') or 0 for x in summary),'top_groups':summary[:12]},'groups':summary,'ledger':ledger[:300],'warnings':warnings,'next_actions':['Use completed context x strategy returns to update strategy_context_router weights after repeated confirmation.']}
    attach_contract(packet,'strategy_context_outcome_ledger_agent',status='degraded' if warnings else 'ok',outputs={'group_count':len(summary),'ledger_rows':len(ledger)},metrics=packet['summary'],warnings=warnings,next_actions=packet['next_actions'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
