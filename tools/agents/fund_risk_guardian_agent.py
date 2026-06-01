#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract
from tools.agents.lib.runtime import write_json, read_json

def load(p):
    try: return read_json(p)
    except Exception: return {}

def main():
    ev=load('/tmp/fund_performance_evaluator_latest.json'); rows=ev.get('evaluations') or []
    findings=[]
    for f in rows:
        mdd=abs(float(f.get('mdd_pct') or 0)); trades=int(f.get('trade_count') or 0); age=int(f.get('age_days') or 0)
        if mdd>=15: findings.append({'fund_id':f.get('id'),'source':f.get('source'),'severity':'watch','issue':'mdd_high','mdd_pct':f.get('mdd_pct')})
        if age and trades/age>4: findings.append({'fund_id':f.get('id'),'source':f.get('source'),'severity':'watch','issue':'turnover_high','trades_per_day':round(trades/age,2)})
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'fund_risk_guardian','real_trading':False,'authority':'paper_only_fund_risk_guard','summary':{'finding_count':len(findings),'blocked_count':sum(1 for f in findings if f.get('severity')=='block')},'findings':findings,'warnings':[],'next_actions':['Use risk findings as allocation cap/guardrail, not as standalone trading signal.']}
    attach_contract(packet,'fund_risk_guardian_agent',status='ok',outputs={'finding_count':len(findings)},metrics=packet['summary'],warnings=[],next_actions=packet['next_actions'])
    write_json(Path('/tmp/fund_risk_guardian_latest.json'), packet); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
