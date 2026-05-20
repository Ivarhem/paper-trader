#!/usr/bin/env python3
"""US Route Eligibility Agent.

Paper/historical research only. Separates US route audit quality from weak KR
route and decides whether US can be treated as paper-eligible/watch candidate.
This is not a real-trading approval and does not modify registry/config.
"""
from __future__ import annotations
import argparse,json,sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract

def read_json(path):
    p=Path(path)
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception: return {}

def market_of_symbol(sym):
    return 'KR' if str(sym or '').endswith(('.KS','.KQ')) else 'US'

def metric_block(rows):
    if not rows:
        return {'samples':0}
    excess=[float(x.get('excess_return_pct')) for x in rows if x.get('excess_return_pct') is not None]
    final=[float(x.get('final_return_pct')) for x in rows if x.get('final_return_pct') is not None]
    bench=[float(x.get('benchmark_return_pct')) for x in rows if x.get('benchmark_return_pct') is not None]
    excess_sorted=sorted(excess)
    return {
        'samples': len(rows),
        'audited_count': len(rows),
        'candidate_buy_zone_count': sum(1 for x in rows if x.get('action') == 'candidate_buy_zone'),
        'avg_final_return_pct': round(sum(final)/len(final),2) if final else None,
        'avg_benchmark_return_pct': round(sum(bench)/len(bench),2) if bench else None,
        'avg_excess_return_pct': round(sum(excess)/len(excess),2) if excess else None,
        'excess_win_rate_pct': round(sum(1 for x in excess if x > 0)/len(excess)*100,2) if excess else None,
        'p10_excess_return_pct': round(excess_sorted[max(0,int(len(excess_sorted)*0.1)-1)],2) if excess_sorted else None,
        'interpretation': 'full_audit_all_watch_and_candidate_rows_not_selected_recommendation_subset',
    }

def market_reality_from_audit(packet):
    summary=packet.get('summary') or {}
    reality=summary.get('market_reality') or {}
    if reality:
        return reality
    rows=[x for x in (packet.get('items') or []) if x.get('status') == 'audited']
    if not rows:
        return {}
    return {market: metric_block([x for x in rows if (x.get('market') or market_of_symbol(x.get('symbol'))) == market]) for market in ('KR','US')}


def metric(m,k,default=None):
    v=(m or {}).get(k,default)
    return v

def judge_us(us):
    blockers=[]; cautions=[]
    samples=metric(us,'sample_count') or metric(us,'samples') or metric(us,'audited_count') or 0
    avg=metric(us,'avg_excess_return_pct')
    ev=metric(us,'expected_excess_value_pct')
    p10=metric(us,'p10_excess_return_pct')
    p25=metric(us,'p25_excess_return_pct')
    eval_lb=metric(us,'evaluation_success_wilson_low_pct')
    grade=metric(us,'quality_grade')
    q=metric(us,'quality_score') or 0
    if samples < 100: blockers.append('low_us_sample_size')
    if avg is None or avg <= 0: blockers.append('us_avg_excess_not_positive')
    if ev is None or ev <= -3: blockers.append('us_ev_too_negative')
    elif ev < 0: cautions.append('us_ev_slightly_negative')
    if p10 is None or p10 <= -8: blockers.append('us_left_tail_p10_too_low')
    elif p25 is not None and p25 < -3: cautions.append('us_p25_tail_still_negative')
    if eval_lb is not None and eval_lb < 45: cautions.append('us_evaluation_confidence_below_promotion')
    if q < 80 and grade != 'high': cautions.append('us_quality_not_high')
    if not blockers and avg > 0 and p10 > -8 and (ev is not None and ev > -3):
        verdict='us_paper_watch_eligible'
    else:
        verdict='us_not_paper_eligible'
    if verdict=='us_paper_watch_eligible' and not cautions and ev >= 0:
        verdict='us_paper_eligible_candidate'
    return verdict, blockers, cautions

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',default='/tmp/us_route_eligibility_latest.json'); args=ap.parse_args()
    audit=read_json('/tmp/market_route_audit_latest.json')
    route=read_json('/tmp/market_route_retest_latest.json')
    rec_audit=read_json('/tmp/recommendation_audit_full_latest.json') or read_json('/tmp/recommendation_audit_latest.json')
    market_quality=audit.get('market_quality') or {}
    audit_summary=rec_audit.get('summary') or {}
    market_reality=market_reality_from_audit(rec_audit)
    us=market_reality.get('US') or market_quality.get('US') or ((audit_summary.get('by_market') or {}).get('US') or {}).get('best') or {}
    kr=market_reality.get('KR') or market_quality.get('KR') or {}
    verdict,blockers,cautions=judge_us(us)
    us_samples=metric(us,'sample_count') or metric(us,'samples') or metric(us,'audited_count') or 0
    route_baseline=(route.get('baseline_by_market') or {}).get('US') or {}
    route_candidates=[]
    for r in route.get('results') or []:
        if r.get('market')=='US':
            route_candidates.append({'policy':r.get('policy'),'verdict':r.get('verdict'),'blockers':r.get('blockers'),'summary':{k:(r.get('summary') or {}).get(k) for k in ['sample_count','quality_score','quality_grade','avg_excess_return_pct','expected_excess_value_pct','p10_excess_return_pct','p25_excess_return_pct']},'delta':r.get('delta_vs_market_baseline')})
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'us_route_eligibility','real_trading':False,'authority':'paper_watch_eligibility_only_no_order_no_registry_apply','us_quality':us,'kr_quality':kr,'us_route_retest_baseline':route_baseline,'us_route_retest_candidates':route_candidates[:5],'verdict':verdict,'blockers':blockers,'cautions':cautions,'summary':{'verdict':verdict,'eligible':verdict in ('us_paper_watch_eligible','us_paper_eligible_candidate'),'blocker_count':len(blockers),'caution_count':len(cautions),'us_sample_count':us_samples,'us_quality_score':us.get('quality_score'),'us_quality_grade':us.get('quality_grade'),'us_avg_excess_return_pct':us.get('avg_excess_return_pct'),'us_expected_excess_value_pct':us.get('expected_excess_value_pct'),'us_p10_excess_return_pct':us.get('p10_excess_return_pct'),'kr_quality_score':kr.get('quality_score'),'kr_avg_excess_return_pct':kr.get('avg_excess_return_pct'),'market_reality_source':bool(market_reality)},'next_actions':[]}
    if packet['summary']['eligible']:
        packet['next_actions']=['Keep US route separated from KR in audit/recommendation presentation; require wider validation before promotion.']
    else:
        packet['next_actions']=['US route not independently eligible; continue paper-only route retests.']
    attach_contract(packet,'us_route_eligibility',status='ok',outputs=packet['summary'],metrics=packet['summary'],warnings=cautions,next_actions=packet['next_actions'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
