#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, list_universe_members, list_strategy_registry
from tools.agents.recommendation_auditor import signal
from tools.agents.recommendation_agent import rows_for, market_of, display_name, technical_risk_context
from tools.agents.lib.agent_contract import attach_contract

DISCOVERY_PREFIXES=('volatility_contraction_breakout','pullback_uptrend','relative_strength_persistence')

def utc_now(): return datetime.now(timezone.utc).isoformat()

def main():
 ap=argparse.ArgumentParser(description='Generate paper-only shadow picks from candidate/probation discovery strategies')
 ap.add_argument('--limit',type=int,default=30); ap.add_argument('--per-market-limit',type=int,default=10); ap.add_argument('--output',default='/tmp/shadow_recommendations_latest.json')
 args=ap.parse_args(); init_db()
 strategies=[s for s in list_strategy_registry() if s.get('status') in ('candidate','probation','watch') and str(s.get('logic','')).startswith(DISCOVERY_PREFIXES)]
 active_symbols=[m['symbol'] for m in list_universe_members(status='active')]
 conn=sqlite3.connect(get_settings().database_path, timeout=30); conn.row_factory=sqlite3.Row
 items=[]
 for sym in active_symbols:
  hist=rows_for(conn,sym)
  if len(hist)<120: continue
  ctx=technical_risk_context(hist)
  for st in strategies:
   logic=st['logic']
   try: sig=signal(hist, logic)
   except Exception: sig=None
   if not sig or sig.get('action')!='candidate_buy_zone': continue
   score=float(sig.get('score') or 0)
   score += max(0,float(st.get('avg_excess_return_pct') or 0))*2
   if ctx.get('trend_strength')=='weak': score-=4
   if ctx.get('atr_bucket')=='high': score-=4
   if ctx.get('volume_confirmation') is False: score-=3
   items.append({'kind':'shadow_signal','run_at':utc_now(),'real_trading':False,'external_publish':False,'symbol':sym,'market':market_of(sym),'name':display_name(sym),'logic':logic,'strategy_status':st.get('status'),'shadow_score':round(score,2),'raw_signal_score':sig.get('score'),'entry':sig.get('entry'),'target':sig.get('target'),'stop':sig.get('stop'),'reasons':sig.get('reasons') or [],'technical_risk_context':ctx,'policy_note':'paper-only shadow pick; not eligible for active recommendation until lifecycle validation passes'})
 conn.close()
 items.sort(key=lambda x:x['shadow_score'], reverse=True)
 selected=[]; counts=Counter()
 for x in items:
  if args.per_market_limit and counts[x['market']]>=args.per_market_limit: continue
  selected.append(x); counts[x['market']]+=1
  if len(selected)>=args.limit: break
 packet={'run_at':utc_now(),'mode':'shadow_recommendations_for_discovery_strategies','real_trading':False,'policy':{'external_publish':False,'copy_trading':False,'broker_sync':False,'active_recommendation_eligible':False},'strategy_count':len(strategies),'candidate_count':len(items),'market_counts':dict(Counter(x['market'] for x in selected)),'items':selected}
 status='ok'
 warnings=[]
 if not strategies: warnings.append('no discovery candidate/probation strategies available'); status='degraded'
 attach_contract(packet,'shadow_recommendation_agent',status=status,outputs={'item_count':len(selected),'candidate_count':len(items),'market_counts':packet['market_counts']},metrics={'strategy_count':len(strategies),'selected_count':len(selected)},warnings=warnings,next_actions=['Track shadow outcomes before any active promotion.'])
 Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
 static_out=ROOT / 'static' / 'shadow_recommendations_latest.json'
 static_out.write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
