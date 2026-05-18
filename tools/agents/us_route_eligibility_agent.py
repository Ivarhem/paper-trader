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

def metric(m,k,default=None):
    v=(m or {}).get(k,default)
    return v

def judge_us(us):
    blockers=[]; cautions=[]
    samples=metric(us,'sample_count') or metric(us,'samples') or 0
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
    rec_audit=read_json('/tmp/recommendation_audit_latest.json')
    market_quality=audit.get('market_quality') or {}
    us=market_quality.get('US') or (((rec_audit.get('summary') or {}).get('by_market') or {}).get('US') or {}).get('best') or {}
    kr=market_quality.get('KR') or {}
    verdict,blockers,cautions=judge_us(us)
    route_baseline=(route.get('baseline_by_market') or {}).get('US') or {}
    route_candidates=[]
    for r in route.get('results') or []:
        if r.get('market')=='US':
            route_candidates.append({'policy':r.get('policy'),'verdict':r.get('verdict'),'blockers':r.get('blockers'),'summary':{k:(r.get('summary') or {}).get(k) for k in ['sample_count','quality_score','quality_grade','avg_excess_return_pct','expected_excess_value_pct','p10_excess_return_pct','p25_excess_return_pct']},'delta':r.get('delta_vs_market_baseline')})
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'us_route_eligibility','real_trading':False,'authority':'paper_watch_eligibility_only_no_order_no_registry_apply','us_quality':us,'kr_quality':kr,'us_route_retest_baseline':route_baseline,'us_route_retest_candidates':route_candidates[:5],'verdict':verdict,'blockers':blockers,'cautions':cautions,'summary':{'verdict':verdict,'eligible':verdict in ('us_paper_watch_eligible','us_paper_eligible_candidate'),'blocker_count':len(blockers),'caution_count':len(cautions),'us_quality_score':us.get('quality_score'),'us_quality_grade':us.get('quality_grade'),'us_avg_excess_return_pct':us.get('avg_excess_return_pct'),'us_expected_excess_value_pct':us.get('expected_excess_value_pct'),'us_p10_excess_return_pct':us.get('p10_excess_return_pct'),'kr_quality_score':kr.get('quality_score'),'kr_avg_excess_return_pct':kr.get('avg_excess_return_pct')},'next_actions':[]}
    if packet['summary']['eligible']:
        packet['next_actions']=['Keep US route separated from KR in audit/recommendation presentation; require wider validation before promotion.']
    else:
        packet['next_actions']=['US route not independently eligible; continue paper-only route retests.']
    attach_contract(packet,'us_route_eligibility',status='ok',outputs=packet['summary'],metrics=packet['summary'],warnings=cautions,next_actions=packet['next_actions'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
