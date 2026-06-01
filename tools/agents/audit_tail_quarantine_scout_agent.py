#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sqlite3,sys,statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, list_universe_members
from tools.agents.lib.agent_contract import attach_contract
from tools.agents import recommendation_auditor as aud

def metric(arr):
    if not arr: return {'sample_count':0}
    base=aud.metric_block(arr); q=aud.validation_quality(arr,{})
    return {**base,**q,'sample_count':len(arr)}

def period_key(d): return str(d or '')[:7]

def main():
    ap=argparse.ArgumentParser(description='Research-only scout for audit tail/period quarantine candidates; no trading/orders/param apply')
    ap.add_argument('--output',default='/tmp/audit_tail_quarantine_scout_latest.json')
    ap.add_argument('--min-symbol-samples',type=int,default=8)
    args=ap.parse_args()
    init_db(); conn=sqlite3.connect(get_settings().database_path,timeout=45); conn.row_factory=sqlite3.Row
    audit=json.load(open('/tmp/recommendation_audit_latest.json')) if Path('/tmp/recommendation_audit_latest.json').exists() else {}
    logic=(audit.get('summary') or {}).get('best_logic') or 'technical_ma_trend_f10_s40_q60'
    symbols=[m['symbol'] for m in list_universe_members(status='active')]
    cutoffs,meta=aud.mixed_cutoffs(conn,symbols,'2024-01-01',monthly_step=1,random_count=24,seed=131,recent_cap_per_quarter=4,horizon=20)
    rows=[]
    for sym in symbols:
        bars=conn.execute('SELECT date, open, high, low, close, volume FROM price_bars WHERE symbol=? AND timeframe="1d" ORDER BY date ASC',(sym,)).fetchall()
        for cutoff in cutoffs:
            hist=[r for r in bars if r['date']<cutoff]; fut=[r for r in bars if r['date']>=cutoff][:20]
            sig=aud.signal(hist,logic)
            if not sig or sig.get('action')!='candidate_buy_zone' or len(fut)<10: continue
            entry=float(sig['entry']); target=sig.get('target'); stop=sig.get('stop')
            bench=aud.benchmark_return(conn,cutoff,20,aud.benchmark_symbol_for(sym))
            result,days,maxp,minp,exit_px,reason=aud.judge(fut,target,stop,entry=entry,fill_model='close_only')
            if result=='corporate_action_excluded': continue
            final=float(exit_px) if exit_px is not None else float(fut[-1]['close'])
            fret=aud.pct(final,entry)
            row={'symbol':sym,'market':aud.market_of(sym),'cutoff':cutoff,'period':period_key(cutoff),'result':result,'final_return_pct':fret,'benchmark_return_pct':bench,'excess_return_pct':round(fret-bench,2) if bench is not None else None,'max_upside_pct':aud.pct(maxp,entry) if maxp else None,'max_drawdown_pct':aud.pct(minp,entry) if minp else None,'exit_reason':reason}
            aud.attach_evaluation(row); rows.append(row)
    conn.close()
    baseline=metric(rows)
    by_period=defaultdict(list); by_symbol=defaultdict(list); by_market=defaultdict(list)
    for r in rows:
        by_period[r['period']].append(r); by_symbol[r['symbol']].append(r); by_market[r['market']].append(r)
    period_rows=[]
    for p,arr in by_period.items():
        if len(arr)<20: continue
        m=metric(arr)
        period_rows.append({'period':p,'summary':m,'verdict':'bad_period_candidate' if (m.get('avg_excess_return_pct') or 0)<-3 and (m.get('p10_excess_return_pct') or 0)<-12 else 'normal_period'})
    period_rows.sort(key=lambda x:(x['summary'].get('avg_excess_return_pct') or 999,x['summary'].get('p10_excess_return_pct') or 999))
    symbol_rows=[]
    for sym,arr in by_symbol.items():
        if len(arr)<args.min_symbol_samples: continue
        m=metric(arr)
        symbol_rows.append({'symbol':sym,'market':arr[0]['market'],'summary':m,'verdict':'tail_quarantine_candidate' if (m.get('avg_excess_return_pct') or 0)<-4 and (m.get('p10_excess_return_pct') or 0)<-14 else 'normal_symbol'})
    symbol_rows.sort(key=lambda x:(x['summary'].get('expected_excess_value_pct') or 999,x['summary'].get('avg_excess_return_pct') or 999))
    # Simulate excluding worst periods/symbols, bounded so it cannot overfit into tiny samples.
    experiments=[]
    bad_periods=[x['period'] for x in period_rows if x['verdict']=='bad_period_candidate'][:3]
    bad_symbols=[x['symbol'] for x in symbol_rows if x['verdict']=='tail_quarantine_candidate'][:20]
    for name, pred in [
        ('exclude_worst_periods_top3', lambda r: r['period'] not in set(bad_periods)),
        ('exclude_tail_symbols_top20', lambda r: r['symbol'] not in set(bad_symbols)),
        ('exclude_worst_periods_and_tail_symbols', lambda r: r['period'] not in set(bad_periods) and r['symbol'] not in set(bad_symbols)),
    ]:
        arr=[r for r in rows if pred(r)]
        m=metric(arr)
        delta={}
        for k in ['avg_excess_return_pct','expected_excess_value_pct','p10_excess_return_pct','p25_excess_return_pct','evaluation_success_rate_pct','quality_score']:
            if baseline.get(k) is not None and m.get(k) is not None: delta[k+'_delta']=round(float(m[k])-float(baseline[k]),2)
        verdict='research_quarantine_candidate' if len(arr)>=max(200,len(rows)*0.65) and delta.get('avg_excess_return_pct_delta',0)>0.8 and delta.get('p10_excess_return_pct_delta',0)>1.5 else 'not_enough'
        experiments.append({'policy':name,'verdict':verdict,'excluded_periods':bad_periods if 'period' in name else [],'excluded_symbols':bad_symbols if 'symbol' in name else [],'summary':m,'delta_vs_baseline':delta,'authority':'paper_quarantine_retest_only_no_apply'})
    experiments.sort(key=lambda x:(x['verdict']=='research_quarantine_candidate',x['delta_vs_baseline'].get('expected_excess_value_pct_delta',-999),x['delta_vs_baseline'].get('p10_excess_return_pct_delta',-999)),reverse=True)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'audit_tail_quarantine_scout','real_trading':False,'authority':'research_only_no_order_no_param_apply','logic':logic,'cutoff_meta':meta,'baseline':baseline,'summary':{'sample_count':len(rows),'bad_period_candidate_count':sum(1 for x in period_rows if x['verdict']=='bad_period_candidate'),'tail_symbol_candidate_count':sum(1 for x in symbol_rows if x['verdict']=='tail_quarantine_candidate'),'best_experiment':experiments[0]['policy'] if experiments else None,'best_verdict':experiments[0]['verdict'] if experiments else None},'period_candidates':period_rows[:8],'symbol_candidates':symbol_rows[:20],'experiments':experiments}
    attach_contract(packet,'audit_tail_quarantine_scout',status='ok',outputs=packet['summary'],metrics=packet['summary'],warnings=[],next_actions=['Validate quarantine candidates out-of-sample before applying any registry/status change.'] if packet['summary']['tail_symbol_candidate_count'] else ['No quarantine candidate ready.'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
