#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract

def load(p,d=None):
    try: return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception: return d if d is not None else {}

def main():
    live=load('/tmp/paper_fund_simulator_latest.json',{})
    hist=load('/tmp/paper_fund_historical_replay_latest.json',{})
    price=load('/tmp/paper_fund_price_replay_latest.json',{})
    funds=[]
    for src,label in [(live,'live_paper'),(hist,'snapshot_replay'),(price,'price_replay')]:
        for f in src.get('standings') or []:
            row=dict(f); row['source']=label; funds.append(row)
    by_id={}
    for f in funds:
        key=(f.get('source'),f.get('id')); by_id[key]=f
    top=sorted(funds,key=lambda x:(x.get('return_pct') if x.get('return_pct') is not None else -999),reverse=True)[:20]
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'fund_registry','real_trading':False,'authority':'paper_only_fund_org_registry','sources':{'live_run_at':live.get('run_at'),'historical_run_at':hist.get('run_at'),'price_replay_run_at':price.get('run_at')},'fund_count':len(funds),'summary':{'top_fund':top[0] if top else None,'source_counts':{s:sum(1 for f in funds if f.get('source')==s) for s in ['live_paper','snapshot_replay','price_replay']},'top_styles':list(dict.fromkeys([f.get('style') for f in top if f.get('style')]))[:6]},'funds':funds,'top_funds':top,'warnings':[],'next_actions':['Use top fund consensus as recommendation overlay; keep risk committee as guardrail.']}
    attach_contract(packet,'fund_registry_agent',status='ok',outputs={'fund_count':len(funds)},metrics=packet['summary'],warnings=[],next_actions=packet['next_actions'])
    payload=json.dumps(packet,ensure_ascii=False,indent=2)
    Path('/tmp/fund_registry_latest.json').write_text(payload,encoding='utf-8')
    static_path=ROOT/'static/fund_registry_latest.json'
    static_path.write_text(payload,encoding='utf-8')
    print(payload)
if __name__=='__main__': main()
