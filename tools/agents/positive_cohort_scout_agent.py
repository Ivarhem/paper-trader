#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sqlite3,sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, list_universe_members
from tools.agents.lib.agent_contract import attach_contract
from tools.agents import recommendation_auditor as aud

def pct(a,b): return round((float(a)/float(b)-1)*100,2) if b else None

def metric(arr):
    if not arr: return {'sample_count':0,'quality_score':0,'quality_flags':['no_samples']}
    return {**aud.metric_block(arr), **aud.validation_quality(arr,{}) , 'sample_count':len(arr)}

def features(conn,sym,cutoff,hist):
    closes=[float(r['close']) for r in hist if r['close'] is not None]
    vols=[float(r['volume'] or 0) for r in hist]
    if len(closes)<61: return {}
    r5=pct(closes[-1],closes[-6]) if len(closes)>=6 else None
    r20=pct(closes[-1],closes[-21]) if len(closes)>=21 else None
    r60=pct(closes[-1],closes[-61]) if len(closes)>=61 else None
    ma20=sum(closes[-20:])/20; ma60=sum(closes[-60:])/60
    vol20=sum(vols[-20:])/20 if len(vols)>=20 else None
    vol60=sum(vols[-60:])/60 if len(vols)>=60 else None
    bench=aud.benchmark_symbol_for(sym)
    brows=conn.execute('SELECT date, close FROM price_bars WHERE symbol=? AND timeframe="1d" AND date<? ORDER BY date ASC',(bench,cutoff)).fetchall()
    b20=b60=None
    if len(brows)>=21: b20=pct(float(brows[-1]['close']),float(brows[-21]['close']))
    if len(brows)>=61: b60=pct(float(brows[-1]['close']),float(brows[-61]['close']))
    return {'market':aud.market_of(sym),'r5':r5,'r20':r20,'r60':r60,'ex20':round(r20-b20,2) if r20 is not None and b20 is not None else None,'ex60':round(r60-b60,2) if r60 is not None and b60 is not None else None,'above_ma20':closes[-1]>ma20,'above_ma60':closes[-1]>ma60,'ma20_gt_ma60':ma20>ma60,'vol20_gt_60':vol20 is not None and vol60 is not None and vol20>vol60,'benchmark_20':b20,'benchmark_60':b60}

def pass_cond(f, cond):
    try:
        if cond=='KR_only': return f.get('market')=='KR'
        if cond=='US_only': return f.get('market')=='US'
        if cond=='ex20_positive': return (f.get('ex20') or -999)>0
        if cond=='ex20_gt5': return (f.get('ex20') or -999)>=5
        if cond=='ex60_positive': return (f.get('ex60') or -999)>0
        if cond=='r20_positive': return (f.get('r20') or -999)>0
        if cond=='r60_positive': return (f.get('r60') or -999)>0
        if cond=='above_ma20_ma60': return f.get('above_ma20') and f.get('above_ma60')
        if cond=='ma20_gt_ma60': return f.get('ma20_gt_ma60')
        if cond=='vol20_gt_60': return f.get('vol20_gt_60')
        if cond=='benchmark_not_hot': return (f.get('benchmark_20') if f.get('benchmark_20') is not None else 999)<10
        if cond=='benchmark_positive_not_hot': return (f.get('benchmark_20') if f.get('benchmark_20') is not None else -999)>0 and f.get('benchmark_20')<10
    except Exception: return False
    return False

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',default='/tmp/positive_cohort_scout_latest.json'); args=ap.parse_args()
    init_db(); conn=sqlite3.connect(get_settings().database_path,timeout=45); conn.row_factory=sqlite3.Row
    audit=json.load(open('/tmp/recommendation_audit_latest.json')) if Path('/tmp/recommendation_audit_latest.json').exists() else {}
    logic=(audit.get('summary') or {}).get('best_logic') or 'technical_ma_trend_f10_s40_q60'
    symbols=[m['symbol'] for m in list_universe_members(status='active')]
    cutoffs,meta=aud.mixed_cutoffs(conn,symbols,'2024-01-01',monthly_step=1,random_count=24,seed=151,recent_cap_per_quarter=4,horizon=20)
    rows=[]
    for sym in symbols:
        bars=conn.execute('SELECT date, open, high, low, close, volume FROM price_bars WHERE symbol=? AND timeframe="1d" ORDER BY date ASC',(sym,)).fetchall()
        for cutoff in cutoffs:
            hist=[r for r in bars if r['date']<cutoff]; fut=[r for r in bars if r['date']>=cutoff][:20]
            sig=aud.signal(hist,logic)
            if not sig or sig.get('action')!='candidate_buy_zone' or len(fut)<10: continue
            entry=float(sig['entry']); bench=aud.benchmark_return(conn,cutoff,20,aud.benchmark_symbol_for(sym))
            result,days,maxp,minp,exit_px,reason=aud.judge(fut,sig.get('target'),sig.get('stop'),entry=entry,fill_model='close_only')
            if result=='corporate_action_excluded': continue
            final=float(exit_px) if exit_px is not None else float(fut[-1]['close']); fret=pct(final,entry)
            row={'symbol':sym,'market':aud.market_of(sym),'cutoff':cutoff,'result':result,'final_return_pct':fret,'benchmark_return_pct':bench,'excess_return_pct':round(fret-bench,2) if bench is not None else None,'max_upside_pct':pct(maxp,entry) if maxp else None,'max_drawdown_pct':pct(minp,entry) if minp else None,'features':features(conn,sym,cutoff,hist)}
            aud.attach_evaluation(row); rows.append(row)
    conn.close()
    baseline=metric(rows)
    conds=['KR_only','US_only','ex20_positive','ex20_gt5','ex60_positive','r20_positive','r60_positive','above_ma20_ma60','ma20_gt_ma60','vol20_gt_60','benchmark_not_hot','benchmark_positive_not_hot']
    combos=[]
    candidates=[]
    for c in conds:
        combos.append((c,[c]))
    for i,a in enumerate(conds):
        for b in conds[i+1:]: combos.append((a+'__'+b,[a,b]))
    for name,cs in combos:
        arr=[r for r in rows if all(pass_cond(r.get('features') or {},c) for c in cs)]
        if len(arr)<120: continue
        m=metric(arr); delta={}
        for k in ['avg_excess_return_pct','expected_excess_value_pct','p10_excess_return_pct','p25_excess_return_pct','evaluation_success_rate_pct','quality_score']:
            if baseline.get(k) is not None and m.get(k) is not None: delta[k+'_delta']=round(float(m[k])-float(baseline[k]),2)
        verdict='positive_cohort_candidate' if m.get('avg_excess_return_pct',-999)>0 and m.get('expected_excess_value_pct',-999)>-2 and m.get('p10_excess_return_pct',-999)>-8 else ('improved_watch' if delta.get('avg_excess_return_pct_delta',0)>0.8 and delta.get('p10_excess_return_pct_delta',0)>1 else 'not_enough')
        candidates.append({'cohort':name,'conditions':cs,'verdict':verdict,'summary':m,'delta_vs_baseline':delta,'authority':'paper_positive_cohort_retest_only_no_apply'})
    candidates.sort(key=lambda x:(x['verdict']=='positive_cohort_candidate',x['verdict']=='improved_watch',x['summary'].get('expected_excess_value_pct') or -999,x['summary'].get('avg_excess_return_pct') or -999),reverse=True)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'positive_cohort_scout','real_trading':False,'authority':'research_only_no_order_no_param_apply','logic':logic,'cutoff_meta':meta,'baseline':baseline,'summary':{'tested_cohort_count':len(candidates),'positive_candidate_count':sum(1 for x in candidates if x['verdict']=='positive_cohort_candidate'),'improved_watch_count':sum(1 for x in candidates if x['verdict']=='improved_watch'),'best_cohort':candidates[0]['cohort'] if candidates else None,'best_verdict':candidates[0]['verdict'] if candidates else None},'candidates':candidates[:20]}
    attach_contract(packet,'positive_cohort_scout',status='ok',outputs=packet['summary'],metrics=packet['summary'],warnings=[],next_actions=['Validate best cohort on wider cutoffs before promotion.'] if packet['summary']['positive_candidate_count'] else ['No positive cohort ready; continue evidence expansion.'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
