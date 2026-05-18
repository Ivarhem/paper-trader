#!/usr/bin/env python3
from __future__ import annotations
import json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from tools.agents.lib.agent_contract import attach_contract

OUT=Path('/tmp/committee_performance_ledger_latest.json')


def load_json(path):
    p=Path(path)
    if not p.exists(): return {} if path.endswith('.json') else None
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception: return {} if path.endswith('.json') else None


def pct(a,b): return round((a/b-1)*100,2) if b else None


def future_outcome(conn, symbol, run_at, target, stop, horizon=20):
    run_date=run_at[:10]
    rows=conn.execute('SELECT date, close FROM price_bars WHERE symbol=? AND timeframe="1d" AND date>=? ORDER BY date ASC LIMIT ?', (symbol, run_date, horizon+1)).fetchall()
    if len(rows)<2: return {'status':'pending','bars':len(rows)}
    entry=float(rows[0]['close']); maxp=entry; minp=entry
    for i,r in enumerate(rows[1:], start=1):
        c=float(r['close']); maxp=max(maxp,c); minp=min(minp,c)
        if stop and c<=float(stop): return {'status':'fail','days':i,'entry':entry,'final':c,'return_pct':pct(c,entry),'max_upside_pct':pct(maxp,entry),'max_drawdown_pct':pct(minp,entry)}
        if target and c>=float(target): return {'status':'success','days':i,'entry':entry,'final':c,'return_pct':pct(c,entry),'max_upside_pct':pct(maxp,entry),'max_drawdown_pct':pct(minp,entry)}
    final=float(rows[-1]['close'])
    return {'status':'timeout' if len(rows)>=horizon else 'pending','bars':len(rows),'entry':entry,'final':final,'return_pct':pct(final,entry),'max_upside_pct':pct(maxp,entry),'max_drawdown_pct':pct(minp,entry)}


def main():
    history=load_json('/tmp/investment_committee_history.json') or []
    rec_history=load_json(ROOT/'static'/'recommendation_history.json') or {}
    payload_by_key={}
    for item in rec_history.get('items',[]):
        payload_by_key[(item.get('run_at'), item.get('symbol'))]=item
    conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    ledger=[]; perf={}
    for run in history[-120:]:
        run_at=run.get('run_at')
        for item in run.get('items',[]):
            sym=item.get('symbol'); rec=payload_by_key.get((run_at,sym)) or {}
            syn=(item.get('committee') or {}).get('synthesis') or {}
            out=future_outcome(conn,sym,run_at,rec.get('target_1'),rec.get('stop_reference')) if run_at and sym else {'status':'unknown'}
            row={'run_at':run_at,'symbol':sym,'committee_decision':syn.get('decision'),'committee_score':syn.get('score'),'outcome':out,'opinions':(item.get('committee') or {}).get('opinions') or []}
            ledger.append(row)
            if out.get('status') in ('pending','unknown'): continue
            label='good' if out.get('status')=='success' or (out.get('return_pct') is not None and out.get('return_pct')>1) else ('bad' if out.get('status')=='fail' or (out.get('return_pct') is not None and out.get('return_pct')<-1) else 'mixed')
            for op in row['opinions']:
                agent=op.get('agent'); d=perf.setdefault(agent,{'correct':0,'wrong':0,'watch':0,'n':0})
                if op.get('opinion')=='watch': d['watch']+=1; continue
                d['n']+=1
                correct=(op.get('opinion')=='support' and label=='good') or (op.get('opinion')=='oppose' and label=='bad')
                if correct: d['correct']+=1
                else: d['wrong']+=1
    conn.close()
    for d in perf.values():
        n=d.get('n') or 0; d['hit_rate']=round(d['correct']/n,3) if n else None
    evaluated=sum(1 for r in ledger if r.get('outcome',{}).get('status') not in ('pending','unknown'))
    pending=sum(1 for r in ledger if r.get('outcome',{}).get('status') in ('pending','unknown'))
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'committee_performance_ledger','real_trading':False,'ledger':ledger[-300:],'performance':perf,'summary':{'evaluated_rows':evaluated,'pending_rows':pending,'agent_count':len(perf),'weight_learning_ready':evaluated>=10,'min_evaluated_rows_for_weights':10}}
    attach_contract(packet,'committee_performance_ledger',status='ok',outputs={'ledger_rows':len(packet['ledger'])},metrics=packet['summary'],warnings=[],next_actions=[])
    OUT.write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
