#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.config import get_settings
from app.database import init_db
from tools.agents.lib.agent_contract import attach_contract

BUCKETS=[(-999,0,'<=0'),(0,1,'0~1'),(1,2,'1~2'),(2,3.2,'2~3.2'),(3.2,999,'>3.2')]

def bucket(v: float) -> str:
    for lo,hi,name in BUCKETS:
        if lo <= v < hi: return name
    return 'unknown'

def safe_payload(text: str|None) -> dict:
    try: return json.loads(text or '{}')
    except Exception: return {}

def pct_avg(vals):
    vals=[v for v in vals if v is not None]
    return round(sum(vals)/len(vals),2) if vals else None

def summarize(rows: list[dict]) -> dict:
    complete=[r for r in rows if r.get('status') in ('complete','stopped_out') and r.get('excess_return_pct') is not None]
    if not complete:
        return {'sample_count':len(rows),'complete_count':0}
    hits=[r.get('hit') for r in complete if r.get('hit') is not None]
    return {
        'sample_count':len(rows),
        'complete_count':len(complete),
        'avg_excess_return_pct':pct_avg([r.get('excess_return_pct') for r in complete]),
        'avg_forward_return_pct':pct_avg([r.get('forward_return_pct') for r in complete]),
        'hit_rate_pct':round(sum(hits)/len(hits)*100,2) if hits else None,
        'stopped_out_rate_pct':round(sum(1 for r in complete if r.get('stopped_out'))/len(complete)*100,2),
        'avg_max_adverse_excursion_pct':pct_avg([r.get('max_adverse_excursion_pct') for r in complete]),
        'avg_max_favorable_excursion_pct':pct_avg([r.get('max_favorable_excursion_pct') for r in complete]),
    }

def recommendation(summary: dict, min_samples: int) -> dict:
    complete=summary.get('complete_count') or 0
    avg=summary.get('avg_excess_return_pct')
    hit=summary.get('hit_rate_pct')
    stopped=summary.get('stopped_out_rate_pct')
    if complete < min_samples:
        return {'decision':'hold_collect_samples','reason':f'complete samples {complete} < {min_samples}', 'suggested_multiplier':1.0}
    if avg is not None and avg >= 1.0 and (hit is None or hit >= 52) and (stopped is None or stopped <= 20):
        return {'decision':'consider_upweight','reason':'positive excess with acceptable hit/stopped-out profile', 'suggested_multiplier':1.05}
    if avg is not None and (avg <= -1.0 or (hit is not None and hit < 45) or (stopped is not None and stopped > 30)):
        return {'decision':'downweight','reason':'weak/negative excess or poor hit/stopped-out profile', 'suggested_multiplier':0.9}
    return {'decision':'keep','reason':'mixed but not clearly harmful/helpful', 'suggested_multiplier':1.0}

def main():
    ap=argparse.ArgumentParser(description='Evaluate supply/investor-flow score overlays against paper recommendation outcomes')
    ap.add_argument('--horizon-days', type=int, default=5)
    ap.add_argument('--limit', type=int, default=3000)
    ap.add_argument('--min-samples', type=int, default=30)
    ap.add_argument('--output', default='/tmp/supply_weight_evaluator_latest.json')
    args=ap.parse_args()
    init_db()
    conn=sqlite3.connect(get_settings().database_path, timeout=30)
    conn.row_factory=sqlite3.Row
    rows=conn.execute('''
        SELECT ro.*, rh.payload_json AS recommendation_payload_json, rh.score AS recommendation_score
        FROM recommendation_outcomes ro
        JOIN recommendation_history rh ON rh.id=ro.recommendation_history_id
        WHERE ro.horizon_days=?
        ORDER BY CASE WHEN ro.status='pending' THEN 1 ELSE 0 END, ro.run_at DESC, ro.id DESC
        LIMIT ?
    ''',(args.horizon_days,args.limit)).fetchall()
    items=[]
    for r in rows:
        payload=safe_payload(r['recommendation_payload_json'])
        vb=payload.get('validation_basis') or {}
        supply=float(vb.get('supply_close_score_adjustment_pct') or 0)
        base=float(vb.get('supply_close_base_adjustment_pct') if vb.get('supply_close_base_adjustment_pct') is not None else supply)
        inv=float(vb.get('investor_flow_seed_adjustment_pct') or 0)
        items.append({
            'symbol':r['symbol'],'market':r['market'],'run_at':r['run_at'],'status':r['status'],'action':r['action'],
            'score':r['recommendation_score'],'horizon_days':r['horizon_days'],'forward_return_pct':r['forward_return_pct'],
            'excess_return_pct':r['excess_return_pct'],'hit':r['hit'],'stopped_out':r['stopped_out'],
            'max_adverse_excursion_pct':r['max_adverse_excursion_pct'],'max_favorable_excursion_pct':r['max_favorable_excursion_pct'],
            'supply_adjustment_pct':supply,'supply_base_adjustment_pct':base,'investor_flow_adjustment_pct':inv,
            'supply_bucket':bucket(supply),'base_bucket':bucket(base),'has_investor_flow_seed':inv>0,
        })
    by_bucket={}
    for name in [b[2] for b in BUCKETS]:
        group=[x for x in items if x['supply_bucket']==name]
        s=summarize(group); s['gate']=recommendation(s,args.min_samples); by_bucket[name]=s
    investor_groups={
        'with_investor_flow_seed': summarize([x for x in items if x['has_investor_flow_seed']]),
        'without_investor_flow_seed': summarize([x for x in items if not x['has_investor_flow_seed']]),
    }
    for v in investor_groups.values(): v['gate']=recommendation(v,args.min_samples)
    high=summarize([x for x in items if x['supply_adjustment_pct']>=2])
    low=summarize([x for x in items if x['supply_adjustment_pct']<2])
    proposals=[]
    for name,s in by_bucket.items():
        g=s.get('gate') or {}
        if g.get('decision') in ('downweight','consider_upweight'):
            proposals.append({'scope':'supply_bucket','bucket':name,'decision':g.get('decision'),'reason':g.get('reason'),'suggested_multiplier':g.get('suggested_multiplier'),'evidence':s})
    for name,s in investor_groups.items():
        g=s.get('gate') or {}
        if g.get('decision') in ('downweight','consider_upweight'):
            proposals.append({'scope':'investor_flow_seed','bucket':name,'decision':g.get('decision'),'reason':g.get('reason'),'suggested_multiplier':g.get('suggested_multiplier'),'evidence':s})
    warnings=[]
    if not items: warnings.append('no recommendation_outcomes available')
    if sum((s.get('complete_count') or 0) for s in by_bucket.values()) < args.min_samples:
        warnings.append('insufficient completed outcome samples for reliable supply-weight tuning')
    packet={
        'run_at':datetime.now(timezone.utc).isoformat(), 'mode':'supply_weight_evaluator', 'real_trading':False,
        'authority':'proposal_only_weight_tuning_gate', 'horizon_days':args.horizon_days, 'rows_scanned':len(items),
        'summary':{'by_supply_adjustment_bucket':by_bucket,'investor_flow_seed':investor_groups,'high_supply_adj_ge_2':high,'low_supply_adj_lt_2':low},
        'weight_adjustment_proposals':proposals,
        'next_actions':['Collect more completed paper outcomes before auto-changing weights.' if warnings else 'Review proposals; apply only bounded weight changes after repeated confirmation.'],
        'warnings':warnings,
    }
    attach_contract(packet,'supply_weight_evaluator_agent',status='degraded' if warnings else 'ok',inputs={'horizon_days':args.horizon_days,'limit':args.limit,'min_samples':args.min_samples},outputs={'proposal_count':len(proposals),'rows_scanned':len(items)},metrics={'rows_scanned':len(items),'proposal_count':len(proposals),'warning_count':len(warnings)},warnings=warnings,next_actions=packet['next_actions'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
