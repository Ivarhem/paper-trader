#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sqlite3,sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, list_universe_members
from tools.agents.lib.agent_contract import attach_contract
from tools.agents import recommendation_auditor as aud

def main():
    ap=argparse.ArgumentParser(description='KR-only paper entry signal scout; no trading/orders/param apply')
    ap.add_argument('--output',default='/tmp/kr_entry_signal_scout_latest.json')
    ap.add_argument('--max-logics',type=int,default=18)
    args=ap.parse_args()
    init_db(); conn=sqlite3.connect(get_settings().database_path, timeout=45); conn.row_factory=sqlite3.Row
    symbols=[m['symbol'] for m in list_universe_members(status='active') if str(m['symbol']).endswith(('.KS','.KQ'))]
    if not symbols:
        symbols=[m['symbol'] for m in list_universe_members(status='watch') if str(m['symbol']).endswith(('.KS','.KQ'))][:180]
    # Prefer KR-appropriate/risk-adjusted families, plus current best for baseline.
    logic_names=[]
    for name,cfg in aud.LOGICS.items():
        fam=cfg.get('family')
        if fam in {'quality_pullback_uptrend','quality_breakout','stable_relative_strength','supply_close_strength','relative_strength_persistence','pullback_in_uptrend','volatility_contraction_breakout'}:
            logic_names.append(name)
    baseline='technical_ma_trend_f10_s40_q60'
    if baseline in aud.LOGICS and baseline not in logic_names: logic_names.insert(0, baseline)
    logic_names=logic_names[:args.max_logics]
    cutoffs,meta=aud.mixed_cutoffs(conn,symbols,'2024-01-01',monthly_step=1,random_count=24,seed=91,recent_cap_per_quarter=4,horizon=20)
    items=[]
    for sym in symbols:
        items.extend(aud.audit_symbol(conn,sym,cutoffs,20,logic_names,fill_model='close_only'))
    conn.close()
    summary=aud.summarize(items)
    rows=[]
    for logic, s in (summary.get('by_logic') or {}).items():
        if not s or s.get('samples',0)<80: continue
        ev=float(s.get('expected_excess_value_pct') or -999)
        avg=float(s.get('avg_excess_return_pct') or -999)
        p10=float(s.get('p10_excess_return_pct') or -999)
        q=float(s.get('quality_score') or 0)
        flags=set(s.get('quality_flags') or [])
        verdict='promising_kr_entry_candidate' if avg>0 and ev>-2 and p10>-8 and q>=68 and 'period_instability' not in flags else ('watch_research_candidate' if avg>-0.5 and q>=60 else 'reject_for_now')
        rows.append({'logic':logic,'family':aud.logic_config(logic).get('family') if aud.logic_config(logic) else None,'verdict':verdict,'summary':{k:s.get(k) for k in ['samples','avg_excess_return_pct','expected_excess_value_pct','p10_excess_return_pct','p25_excess_return_pct','evaluation_success_rate_pct','quality_score','quality_grade','quality_flags','signal_rate_pct','symbol_count','max_symbol_concentration_pct']},'authority':'kr_entry_signal_research_only_no_apply'})
    rows.sort(key=lambda r:(r['verdict']=='promising_kr_entry_candidate', r['verdict']=='watch_research_candidate', (r['summary'].get('expected_excess_value_pct') or -999), (r['summary'].get('avg_excess_return_pct') or -999), (r['summary'].get('quality_score') or 0)), reverse=True)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'kr_entry_signal_scout','real_trading':False,'authority':'research_only_no_order_no_registry_promotion','symbol_count':len(symbols),'logic_count':len(logic_names),'logics':logic_names,'cutoff_meta':meta,'results':rows,'summary':{'best_logic':rows[0]['logic'] if rows else None,'best_verdict':rows[0]['verdict'] if rows else None,'promising_count':sum(1 for r in rows if r['verdict']=='promising_kr_entry_candidate'),'watch_count':sum(1 for r in rows if r['verdict']=='watch_research_candidate'),'tested_logic_count':len(rows)}}
    next_actions=['Run wider validation and only register/promote if promising result repeats.'] if packet['summary']['promising_count'] else ['No KR entry signal is ready; keep generating risk-adjusted KR hypotheses.']
    attach_contract(packet,'kr_entry_signal_scout',status='ok',outputs=packet['summary'],metrics=packet['summary'],warnings=[],next_actions=next_actions)
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
