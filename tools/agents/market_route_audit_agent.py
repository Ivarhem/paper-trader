#!/usr/bin/env python3
"""Market Route Audit: split recommendation audit quality by market route.

Paper/historical analysis only. This does not alter recommendations, strategy
registry, parameters, or place orders. It summarizes the current auditor preview
by KR/US so weak global audit quality is not hidden or misattributed.
"""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents import recommendation_auditor as aud
from tools.agents.lib.agent_contract import attach_contract


def market_of_item(item: dict) -> str:
    return item.get('market') or aud.market_of(item.get('symbol') or '')


def metric_subset(items):
    if not items:
        q={'quality_score':0,'quality_grade':'low','quality_flags':['no_samples']}
        return {'sample_count':0, **q}
    base=aud.metric_block(items)
    q=aud.validation_quality(items,{})
    return {**base, **q, 'sample_count':len(items)}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--audit', default='/tmp/recommendation_audit_latest.json')
    ap.add_argument('--route-retest', default='/tmp/market_route_retest_latest.json')
    ap.add_argument('--output', default='/tmp/market_route_audit_latest.json')
    args=ap.parse_args()
    audit=json.loads(Path(args.audit).read_text(encoding='utf-8')) if Path(args.audit).exists() else {}
    items=audit.get('items') or []
    by_market={}
    for item in items:
        by_market.setdefault(market_of_item(item),[]).append(item)
    market_quality={m:metric_subset(v) for m,v in sorted(by_market.items())}
    global_best=(audit.get('summary') or {}).get('best') or {}
    route_retest=json.loads(Path(args.route_retest).read_text(encoding='utf-8')) if Path(args.route_retest).exists() else {}
    retest_results=route_retest.get('results') or []
    watch_candidates=[]
    for r in retest_results:
        s=r.get('summary') or {}; d=r.get('delta_vs_market_baseline') or {}
        if r.get('market')=='KR' and r.get('policy')=='timeout_decay_exit':
            watch_candidates.append({
                'market':r.get('market'), 'policy':r.get('policy'), 'verdict':r.get('verdict'),
                'blockers':r.get('blockers') or [], 'quality_score':s.get('quality_score'),
                'quality_grade':s.get('quality_grade'), 'avg_excess_return_pct':s.get('avg_excess_return_pct'),
                'expected_excess_value_pct':s.get('expected_excess_value_pct'), 'p10_excess_return_pct':s.get('p10_excess_return_pct'),
                'delta':d, 'authority':'watch_only_paper_retest_no_apply'
            })
    best_market=None
    if market_quality:
        best_market=max(market_quality, key=lambda m:(market_quality[m].get('quality_score') or 0, market_quality[m].get('avg_excess_return_pct') or -999))
    warnings=[]
    for m,s in market_quality.items():
        if (s.get('quality_grade')=='low') or (s.get('avg_excess_return_pct') is not None and s.get('avg_excess_return_pct')<0):
            warnings.append(f'{m} route audit remains weak: grade={s.get("quality_grade")}, avg_excess={s.get("avg_excess_return_pct")}')
    packet={
        'run_at':datetime.now(timezone.utc).isoformat(),
        'mode':'market_route_audit',
        'real_trading':False,
        'authority':'paper_audit_summary_only_no_order_no_param_apply',
        'best_logic':(audit.get('summary') or {}).get('best_logic'),
        'global_audit':{k:global_best.get(k) for k in ['samples','quality_score','quality_grade','quality_flags','avg_excess_return_pct','expected_excess_value_pct','p10_excess_return_pct','p25_excess_return_pct']},
        'market_quality':market_quality,
        'watch_candidates':watch_candidates,
        'summary':{
            'market_count':len(market_quality),
            'best_market':best_market,
            'best_market_quality_score':(market_quality.get(best_market) or {}).get('quality_score') if best_market else None,
            'weak_markets':[m for m,s in market_quality.items() if s.get('quality_grade')=='low'],
            'kr_timeout_decay_watch':bool(watch_candidates),
            'ready_to_apply':False,
        },
        'warnings':warnings,
        'next_actions':['Keep market-route audit separate; repeat KR timeout_decay_exit retest before any parameter application.'] if watch_candidates else ['Keep market-route audit separate; no route-specific parameter is ready to apply.']
    }
    attach_contract(packet,'market_route_audit',status='ok',outputs=packet['summary'],metrics=packet['summary'],warnings=warnings,next_actions=packet['next_actions'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
