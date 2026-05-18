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

DEFAULT_ARMS=[-2.0,-1.5,-1.0,-0.5,0.0]

def safe_payload(text):
    try: return json.loads(text or '{}')
    except Exception: return {}

def avg(vals):
    vals=[v for v in vals if v is not None]
    return round(sum(vals)/len(vals),2) if vals else None

def pct(n,d):
    return round(n/d*100,2) if d else None

def arm_upside(original_upside_pct, adjustment):
    if original_upside_pct is None: return None
    return round(max(float(original_upside_pct)+float(adjustment),0.0),2)

def summarize_arm(rows, adjustment):
    complete=[r for r in rows if r.get('status') in ('complete','stopped_out') and r.get('forward_return_pct') is not None and r.get('original_upside_pct') is not None]
    targets=[arm_upside(r.get('original_upside_pct'), adjustment) for r in complete]
    pairs=[(r,t) for r,t in zip(complete,targets) if t is not None]
    hits=[r for r,t in pairs if float(r.get('forward_return_pct') or 0) >= t]
    # Paper proxy: if target is hit, assume exit around target; otherwise horizon forward return.
    realized=[min(float(r.get('forward_return_pct') or 0), t) for r,t in pairs]
    excess=[r.get('excess_return_pct') for r,_ in pairs]
    stopped=[r for r,_ in pairs if r.get('stopped_out')]
    shortfalls=[float(r.get('forward_return_pct') or 0)-t for r,t in pairs]
    return {
        'adjustment_pct_points': adjustment,
        'sample_count': len(rows),
        'complete_count': len(pairs),
        'avg_target_upside_pct': avg(targets),
        'target_hit_rate_pct': pct(len(hits), len(pairs)),
        'avg_proxy_realized_return_pct': avg(realized),
        'avg_excess_return_pct': avg(excess),
        'stopped_out_rate_pct': pct(len(stopped), len(pairs)),
        'avg_target_shortfall_pct_points': avg(shortfalls),
    }

def summarize_current(rows, min_samples):
    current_groups={}
    for r in rows:
        current_groups.setdefault(str(r['adjustment_pct_points']),[]).append(r)
    out={}
    for k,rs in current_groups.items():
        adj=float(k)
        arm=summarize_arm(rs, adj)
        complete=arm.get('complete_count') or 0
        if complete < min_samples:
            gate={'decision':'hold_collect_samples','reason':f'complete samples {complete} < {min_samples}','suggested_adjustment_pct_points':None}
        else:
            gate={'decision':'evaluate_parameter_arms','reason':'current adjustment has enough completed samples; compare parameter arms by proxy realized return and risk','suggested_adjustment_pct_points':'see_parameter_arm_meta'}
        arm['gate']=gate
        out[k]=arm
    return out

def choose_arm(arms, current_adjustment, min_samples):
    completed=[a for a in arms if (a.get('complete_count') or 0) >= min_samples]
    if not completed:
        return {'decision':'hold_collect_samples','reason':f'no arm has complete samples >= {min_samples}','selected_adjustment_pct_points':current_adjustment,'candidate_adjustments':[]}
    # Risk guard: do not select arms with meaningfully worse stop-out than the current simulated arm.
    current=next((a for a in completed if abs(float(a['adjustment_pct_points'])-float(current_adjustment))<1e-9), None)
    current_stop=(current or {}).get('stopped_out_rate_pct')
    pool=[]
    for a in completed:
        if current_stop is not None and a.get('stopped_out_rate_pct') is not None and a['stopped_out_rate_pct'] > current_stop + 5:
            continue
        pool.append(a)
    pool=pool or completed
    ranked=sorted(pool, key=lambda a: ((a.get('avg_proxy_realized_return_pct') is not None, a.get('avg_proxy_realized_return_pct') or -999), (a.get('target_hit_rate_pct') or -999)), reverse=True)
    best=ranked[0]
    candidates=[{'adjustment_pct_points':a['adjustment_pct_points'],'avg_proxy_realized_return_pct':a.get('avg_proxy_realized_return_pct'),'target_hit_rate_pct':a.get('target_hit_rate_pct'),'stopped_out_rate_pct':a.get('stopped_out_rate_pct')} for a in ranked[:3]]
    if current and best['adjustment_pct_points']==current['adjustment_pct_points']:
        decision='keep_current'
        reason='current adjustment ranks best/acceptable by proxy realized return under risk guard'
    else:
        decision='test_or_promote_parameter_arm'
        reason='alternate adjustment arm has better proxy realized return under risk guard; promote only after repeated confirmation'
    return {'decision':decision,'reason':reason,'selected_adjustment_pct_points':best['adjustment_pct_points'],'candidate_adjustments':candidates}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--horizon-days',type=int,default=1)
    ap.add_argument('--limit',type=int,default=10000)
    ap.add_argument('--min-samples',type=int,default=30)
    ap.add_argument('--arms',default=','.join(str(x) for x in DEFAULT_ARMS))
    ap.add_argument('--output',default='/tmp/target_return_adjustment_evaluator_latest.json')
    args=ap.parse_args()
    arms=[float(x.strip()) for x in args.arms.split(',') if x.strip()]
    init_db(); conn=sqlite3.connect(get_settings().database_path,timeout=30); conn.row_factory=sqlite3.Row
    rows=conn.execute("""SELECT ro.*, rh.payload_json AS recommendation_payload_json, rh.score AS recommendation_score FROM recommendation_outcomes ro JOIN recommendation_history rh ON rh.id=ro.recommendation_history_id WHERE ro.horizon_days=? ORDER BY CASE WHEN ro.status='pending' THEN 1 ELSE 0 END, ro.run_at DESC, ro.id DESC LIMIT ?""",(args.horizon_days,args.limit)).fetchall(); conn.close()
    items=[]
    for r in rows:
        payload=safe_payload(r['recommendation_payload_json']); adj=payload.get('target_return_adjustment') or {}
        if adj.get('adjustment_pct_points') is None: continue
        items.append({'symbol':r['symbol'],'market':r['market'],'run_at':r['run_at'],'status':r['status'],'action':r['action'],'score':r['recommendation_score'],'horizon_days':r['horizon_days'],'forward_return_pct':r['forward_return_pct'],'excess_return_pct':r['excess_return_pct'],'hit':r['hit'],'stopped_out':r['stopped_out'],'adjustment_pct_points':float(adj.get('adjustment_pct_points') or 0),'adjusted_target':payload.get('target_1'),'original_target':payload.get('original_target_1'),'adjusted_upside_pct':payload.get('upside_1_pct'),'original_upside_pct':payload.get('original_upside_1_pct'),'policy':adj.get('policy')})
    current_values=sorted({x['adjustment_pct_points'] for x in items})
    current_adjustment=current_values[0] if current_values else -1.5
    by_adj=summarize_current(items,args.min_samples)
    parameter_arms=[summarize_arm(items,a) for a in arms]
    meta_decision=choose_arm(parameter_arms,current_adjustment,args.min_samples)
    proposals=[]
    if meta_decision['decision']=='test_or_promote_parameter_arm':
        proposals.append({'scope':'target_return_parameter_arm','current_adjustment_pct_points':current_adjustment,'decision':meta_decision['decision'],'reason':meta_decision['reason'],'suggested_adjustment_pct_points':meta_decision['selected_adjustment_pct_points'],'candidate_adjustments':meta_decision['candidate_adjustments'],'authority':'proposal_only_repeated_confirmation_required'})
    warnings=[]
    if not items: warnings.append('no recommendations with target_return_adjustment found')
    if max([a.get('complete_count') or 0 for a in parameter_arms] or [0]) < args.min_samples: warnings.append('insufficient completed target-return parameter outcome samples')
    arm_backlog=[{'adjustment_pct_points':a.get('adjustment_pct_points'),'complete_count':a.get('complete_count') or 0,'needed_complete_count':max(0,args.min_samples-(a.get('complete_count') or 0)),'gate':('ready' if (a.get('complete_count') or 0) >= args.min_samples else 'collect_samples')} for a in parameter_arms]
    next_actions=['Collect completed outcomes before changing default target-return parameter.' if warnings else 'Compare repeated parameter-arm results before applying a default target-return change.']
    if arm_backlog:
        backlog_text=', '.join(f"{x['adjustment_pct_points']}%p needs {x['needed_complete_count']} more" for x in arm_backlog if x['needed_complete_count'] > 0)
        if backlog_text:
            next_actions.append('Target-return arm backlog: ' + backlog_text[:240])
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'target_return_parameter_meta_evaluator','real_trading':False,'authority':'proposal_only_target_return_parameter_meta_gate','horizon_days':args.horizon_days,'rows_scanned':len(items),'current_adjustment_pct_points':current_adjustment,'parameter_arms':parameter_arms,'arm_sample_backlog':arm_backlog,'meta_decision':meta_decision,'summary':{'by_current_adjustment_pct_points':by_adj,'parameter_arm_count':len(parameter_arms),'arm_sample_backlog':arm_backlog},'target_adjustment_proposals':proposals,'warnings':warnings,'next_actions':next_actions}
    attach_contract(packet,'target_return_adjustment_evaluator_agent',status='degraded' if warnings else 'ok',inputs={'horizon_days':args.horizon_days,'limit':args.limit,'min_samples':args.min_samples,'arms':arms},outputs={'proposal_count':len(proposals),'rows_scanned':len(items),'parameter_arm_count':len(parameter_arms)},metrics={'rows_scanned':len(items),'proposal_count':len(proposals),'warning_count':len(warnings)},warnings=warnings,next_actions=packet['next_actions'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
