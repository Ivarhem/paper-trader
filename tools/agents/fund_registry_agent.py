#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract
from tools.agents.lib.runtime import read_json, write_json_and_static

def load(p,d=None):
    try: return read_json(p)
    except Exception: return d if d is not None else {}

DEFAULT_MIN_RANKING_AGE_DAYS = 30

def fund_age_days(f):
    try:
        return int(float(f.get('age_days') or 0))
    except Exception:
        return 0

def source_min_age(*packets):
    for packet in packets:
        try:
            value = int(float((packet or {}).get('ranking_min_age_days') or (packet or {}).get('min_age_days') or 0))
            if value > 0:
                return value
        except Exception:
            pass
    return DEFAULT_MIN_RANKING_AGE_DAYS

def main():
    live=load('/tmp/paper_fund_simulator_latest.json',{})
    hist=load('/tmp/paper_fund_historical_replay_latest.json',{})
    price=load('/tmp/paper_fund_price_replay_latest.json',{})
    min_age=source_min_age(price, hist, live)
    funds=[]
    # The lightweight live simulator often starts as fund_001..N at the initial
    # capital. Once replay artifacts exist, keep that simulator as a fallback
    # only so the fund menu does not look reset after artifact regeneration.
    sources=[]
    if price.get('standings'):
        sources.append((price,'price_replay'))
    if hist.get('standings'):
        sources.append((hist,'snapshot_replay'))
    if not sources and live.get('standings'):
        sources.append((live,'live_paper'))
    for src,label in sources:
        for f in src.get('standings') or []:
            row=dict(f); row['source']=label; row['ranking_min_age_days']=min_age; row['ranking_eligible']=fund_age_days(row) >= min_age; funds.append(row)
    by_id={}
    for f in funds:
        key=(f.get('source'),f.get('id')); by_id[key]=f
    top=sorted(funds,key=lambda x:(bool(x.get('ranking_eligible')), x.get('return_pct') if x.get('return_pct') is not None else -999),reverse=True)[:20]
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'fund_registry','real_trading':False,'authority':'paper_only_fund_org_registry','sources':{'live_run_at':live.get('run_at'),'historical_run_at':hist.get('run_at'),'price_replay_run_at':price.get('run_at')},'fund_count':len(funds),'ranking_min_age_days':min_age,'ranking_policy':{'min_age_days':min_age,'effect':'below-min-age funds remain visible but cannot outrank mature funds in top_funds'},'summary':{'top_fund':next((f for f in top if f.get('ranking_eligible')), top[0] if top else None),'ranking_eligible_count':sum(1 for f in funds if f.get('ranking_eligible')),'probation_count':sum(1 for f in funds if not f.get('ranking_eligible')),'source_counts':{s:sum(1 for f in funds if f.get('source')==s) for s in ['live_paper','snapshot_replay','price_replay']},'top_styles':list(dict.fromkeys([f.get('style') for f in top if f.get('style')]))[:6]},'funds':funds,'top_funds':top,'warnings':[],'next_actions':['Use top fund consensus as recommendation overlay; keep risk committee as guardrail.']}
    attach_contract(packet,'fund_registry_agent',status='ok',outputs={'fund_count':len(funds)},metrics=packet['summary'],warnings=[],next_actions=packet['next_actions'])
    write_json_and_static('/tmp/fund_registry_latest.json', packet)
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
