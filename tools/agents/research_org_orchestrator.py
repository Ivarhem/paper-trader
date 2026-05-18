#!/usr/bin/env python3
from __future__ import annotations
"""Profit-improvement orchestrator for paper_trader.

This is intentionally paper/historical only. It does not place orders. Its job is
not to summarize the pipeline, but to choose the next bounded research actions
that could improve recommendation returns, run safe diagnostics, and make the
agenda visible to the UI/cron loop.
"""
import argparse, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.database import init_db, validation_coverage, list_strategy_registry, save_research_org_report
from tools.agents.lib.agent_contract import attach_contract


def now(): return datetime.now(timezone.utc).isoformat()

def read_json(path: str) -> dict:
    p=Path(path)
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc: return {'_read_error': str(exc), '_path': path}

def run(name: str, cmd: list[str], timeout: int = 180) -> dict:
    started=now()
    try:
        p=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,timeout=timeout)
        rc=p.returncode; out=p.stdout; err=p.stderr; timed_out=False
    except subprocess.TimeoutExpired as exc:
        rc=124; timed_out=True
        out=(exc.stdout or '') if isinstance(exc.stdout,str) else (exc.stdout or b'').decode('utf-8',errors='replace')
        err=(exc.stderr or '') if isinstance(exc.stderr,str) else (exc.stderr or b'').decode('utf-8',errors='replace')
        err=f"{err}\n{name} timed out after {timeout}s".strip()
    return {'name':name,'cmd':cmd,'started_at':started,'ended_at':now(),'returncode':rc,'timed_out':timed_out,'stdout_tail':out[-2500:],'stderr_tail':err[-2500:],'error':rc!=0}

def strategy_score(row: dict) -> float:
    s=row.get('summary') or {}
    return round(float(row.get('avg_excess_return_pct') or 0)*4 + float(row.get('recent_avg_excess_return_pct') or 0)*2 + float(row.get('success_rate_pct') or 0)/10 + min(8, int(row.get('samples') or 0)/200), 2)


def blocker_tags(row: dict) -> list[str]:
    tags=[]
    reason=str(row.get('reason') or '')
    recent=float(row.get('recent_avg_excess_return_pct') or 0)
    success=float(row.get('success_rate_pct') or 0)
    ex=float(row.get('avg_excess_return_pct') or 0)
    samples=int(row.get('samples') or 0)
    if 'recent rolling window deteriorated' in reason or recent < 0:
        tags.append('recent_deterioration')
    if success < 35:
        tags.append('low_success_rate')
    if ex < 1:
        tags.append('weak_avg_excess')
    if samples < 500:
        tags.append('sample_gap')
    if 'overselective' in reason:
        tags.append('overselective_signal_rate')
    if not tags:
        tags.append('near_threshold_quality')
    return tags


def active_pool_gap(strategies: list[dict], target_active: int = 7) -> dict:
    active=[x for x in strategies if x.get('status')=='active']
    pool=[x for x in strategies if x.get('status') in ('watch','probation','candidate')]
    ranked=sorted(pool,key=strategy_score,reverse=True)[:10]
    blockers={}
    items=[]
    for r in ranked:
        tags=blocker_tags(r)
        for t in tags: blockers[t]=blockers.get(t,0)+1
        items.append({'logic':r.get('logic'),'status':r.get('status'),'score':strategy_score(r),'samples':r.get('samples'),'success_rate_pct':r.get('success_rate_pct'),'avg_excess_return_pct':r.get('avg_excess_return_pct'),'recent_avg_excess_return_pct':r.get('recent_avg_excess_return_pct'),'blockers':tags,'reason':r.get('reason')})
    return {'target_active':target_active,'active_count':len(active),'gap':max(0,target_active-len(active)),'promotion_proposal_count':0,'dominant_blockers':sorted(blockers.items(),key=lambda x:x[1],reverse=True),'top_blocked_candidates':items}


def assigned_research_tasks(agenda: list[dict], gap: dict, recs: dict) -> list[dict]:
    tasks=[]
    for item in gap.get('top_blocked_candidates',[])[:4]:
        blockers=set(item.get('blockers') or [])
        if 'recent_deterioration' in blockers:
            owner='exit_policy_optimizer'; action='retest exit/holding rules for recent deterioration'
            unblock='recent_avg_excess_return_pct >= 0 and tail-risk flags reduced'
        elif 'low_success_rate' in blockers:
            owner='recommendation_audit'; action='audit comparable variants and inspect win-rate confidence'
            unblock='wilson/quality score improves or strategy remains probation'
        elif 'overselective_signal_rate' in blockers:
            owner='strategy_success_optimizer'; action='broaden signal threshold without collapsing precision'
            unblock='signal rate rises while avg excess stays positive'
        else:
            owner='simulation_validation_worker'; action='add validation samples around near-threshold candidate'
            unblock='candidate qualifies for lifecycle active or is retired'
        tasks.append({'priority':'high' if len(tasks)<2 else 'medium','owner_agent':owner,'theme':'active_pool','target':item.get('logic'),'action':action,'unblock_condition':unblock,'evidence':item})
    rec_items=recs.get('items') or []
    paper=[x for x in rec_items if x.get('recommendation_bucket')=='paper_buy_candidate'][:3]
    for r in paper:
        tasks.append({'priority':'high','owner_agent':'current_recommendation_validation','theme':'recommendation_conversion','target':r.get('symbol'),'action':'increase symbol-specific validation for paper_buy_candidate','unblock_condition':'critic under_validated issue clears or committee keeps research-only label','evidence':{'symbol':r.get('symbol'),'score':r.get('score'),'bucket':r.get('recommendation_bucket'),'critic_summary':(r.get('critic') or {}).get('summary')}})
    return tasks[:8]

def build_alpha_agenda(strategies, recs, evaluation, calibration, funnel, optimizer, tail_filter, target_active=7):
    items=[]
    active=[x for x in strategies if x.get('status')=='active']
    watch=[x for x in strategies if x.get('status') in ('watch','probation')]
    rec_items=recs.get('items') or []
    trade_eligible=sum(1 for x in rec_items if x.get('trade_eligible') or x.get('recommendation_bucket')=='approved')
    if len(active) < target_active:
        ranked=sorted(watch, key=strategy_score, reverse=True)[:5]
        items.append({'priority':'high','theme':'active_pool','objective':'increase qualified active research strategies','why':f'active {len(active)} < target research pool {target_active}; trade eligible recommendations {trade_eligible}', 'action':'Run active_pool_gap diagnostics and assign blocker-specific validation/optimizer tasks.', 'candidates':[{'logic':r.get('logic'),'status':r.get('status'),'score':strategy_score(r),'avg_excess_return_pct':r.get('avg_excess_return_pct'),'recent_avg_excess_return_pct':r.get('recent_avg_excess_return_pct'),'samples':r.get('samples'),'blockers':blocker_tags(r)} for r in ranked]})
    if trade_eligible == 0 and rec_items:
        items.append({'priority':'high','theme':'recommendation_conversion','objective':'turn research_watch candidates into qualified paper-buy candidates', 'why':'current recommendation list has no approved/trade_eligible candidates', 'action':'Prioritize current recommendation validation, calibration, and critic bottleneck reduction before generating more symbols.'})
    cal_findings=calibration.get('findings') or []
    bad_cal=[x for x in cal_findings if x.get('severity') in ('action','urgent','watch')]
    if bad_cal:
        items.append({'priority':'medium','theme':'score_calibration','objective':'align score buckets with realized forward returns','why':bad_cal[0].get('finding'), 'action':bad_cal[0].get('recommendation')})
    opt_summary=optimizer.get('summary') or {}
    if opt_summary.get('research_only_active_count',0) and not opt_summary.get('trade_eligible_active_count'):
        items.append({'priority':'medium','theme':'strategy_quality','objective':'move from research-only active to high-confidence historical active','why':f"research-only active {opt_summary.get('research_only_active_count')}, trade-eligible active {opt_summary.get('trade_eligible_active_count')}", 'action':'Use optimizer gates/tail-risk output to target exit policy and strategy family improvements.'})
    severe=(tail_filter.get('summary') or {}).get('severe_count') or 0
    if severe:
        items.append({'priority':'high','theme':'tail_risk','objective':'raise risk-adjusted returns by reducing left-tail strategies','why':f'{severe} severe tail-risk strategies detected', 'action':'Keep severe strategies out of approved recommendations and run exit policy optimizer.'})
    org_actions=evaluation.get('next_actions') or []
    for action in org_actions[:2]:
        items.append({'priority':'medium','theme':'org_evaluator','objective':'resolve organization finding that blocks recommendation quality','why':'org evaluator next action', 'action':action})
    if not items:
        items.append({'priority':'low','theme':'steady_state','objective':'continue compounding evidence','why':'no urgent bottleneck detected', 'action':'Keep validating current recommendations and monitor drift.'})
    return items[:8]

def main():
    ap=argparse.ArgumentParser(description='Proactively choose paper-research actions to improve recommendation returns')
    ap.add_argument('--output', default='/tmp/research_org_orchestrator_latest.json')
    ap.add_argument('--execute-safe-actions', action='store_true', default=True)
    args=ap.parse_args(); init_db()

    strategies=list_strategy_registry(); coverage=validation_coverage()
    recs=read_json('/tmp/recommendations_latest.json')
    evaluation=read_json('/tmp/research_org_evaluation_latest.json')
    calibration=read_json('/tmp/recommendation_calibration_latest.json')
    funnel=read_json('/tmp/recommendation_funnel_latest.json')
    optimizer=read_json('/tmp/strategy_success_optimizer_latest.json')
    tail_filter=read_json('/tmp/strategy_tail_risk_filter_latest.json')
    outcome=read_json('/tmp/recommendation_outcomes_latest.json')
    hypotheses=read_json('/tmp/research_hypotheses_latest.json')
    experiment_plan=read_json('/tmp/research_experiment_plan_latest.json')
    evidence_judge=read_json('/tmp/research_evidence_judge_latest.json')
    experiment_ledger=read_json('/tmp/research_experiment_ledger_latest.json')

    org_profile=read_json('configs/org_profile.json')
    target_active=int(((org_profile.get('strategy') or {}).get('target_active') if isinstance(org_profile, dict) else None) or 7)
    gap=active_pool_gap(strategies,target_active)
    agenda=build_alpha_agenda(strategies,recs,evaluation,calibration,funnel,optimizer,tail_filter,target_active)
    assigned_tasks=assigned_research_tasks(agenda,gap,recs)
    # The autonomous research loop now owns bounded experiment execution. The
    # orchestrator should direct and summarize, not duplicate worker calls. Keep
    # only a lightweight balancer probe for active-pool visibility if no isolated
    # experiment runner output exists yet.
    actions=[]
    themes={x.get('theme') for x in agenda if x.get('priority') in ('high','medium')}
    runner_results=read_json('/tmp/research_experiment_results_latest.json')
    if args.execute_safe_actions and not (runner_results.get('results') or []) and 'active_pool' in themes:
        actions.append(run('active_strategy_balancer_probe',[sys.executable,'tools/agents/active_strategy_balancer_agent.py','--target-active',str(target_active),'--max-promote',str(((org_profile.get('strategy') or {}).get('max_promote') if isinstance(org_profile, dict) else None) or 4),'--high-upside-slots',str(((org_profile.get('strategy') or {}).get('high_upside_slots') if isinstance(org_profile, dict) else None) or 4),'--output','/tmp/active_strategy_balancer_probe_latest.json'],timeout=120))

    active=[x for x in list_strategy_registry() if x.get('status')=='active']
    recs_after=read_json('/tmp/recommendations_latest.json')
    trade_eligible=sum(1 for x in (recs_after.get('items') or []) if x.get('trade_eligible') or x.get('recommendation_bucket')=='approved')
    payload={
        'run_at':now(),
        'mode':'profit_improvement_orchestrator',
        'real_trading':False,
        'mission':'Actively improve paper/historical recommendation returns by selecting bounded research actions, not merely summarizing pipeline status.',
        'objective_metrics':{'active_count':len(active),'target_active':target_active,'active_gap':gap.get('gap'),'trade_eligible_recommendations':trade_eligible,'coverage_pct':coverage.get('coverage_pct_estimate'),'completed_results':coverage.get('completed_results')},
        'active_pool_gap':gap,
        'alpha_agenda':agenda,
        'assigned_research_tasks':assigned_tasks,
        'autonomous_research': {
            'hypothesis_count': len(hypotheses.get('hypotheses') or []),
            'plan_count': len(experiment_plan.get('plans') or []),
            'judgment_count': len(evidence_judge.get('judgments') or []),
            'ledger_new_entries': (experiment_ledger.get('summary') or {}).get('new_entry_count'),
            'ledger_repeat_count': (experiment_ledger.get('summary') or {}).get('repeat_count'),
            'ledger_delta_count': (experiment_ledger.get('summary') or {}).get('delta_count'),
            'ledger_deduped_repeat_count': (experiment_ledger.get('summary') or {}).get('deduped_repeat_count'),
            'top_hypotheses': (hypotheses.get('hypotheses') or [])[:5],
            'plans': experiment_plan.get('plans') or [],
            'judgments': evidence_judge.get('judgments') or [],
        },
        'safe_actions_executed':actions,
        'execution_model':'autonomous_experiment_runner_primary',
        'notify': bool([x for x in agenda if x.get('priority')=='high'] or [a for a in actions if a.get('error')]),
    }
    status='degraded' if any(a.get('error') for a in actions) else 'ok'
    warnings=[f"{a['name']} failed" for a in actions if a.get('error')]
    next_actions=[x.get('action') for x in agenda if x.get('priority') in ('high','medium')][:5]
    summary=f"Alpha Orchestrator: {len(agenda)} agenda items, {len(actions)} safe actions, active {len(active)}, trade-eligible recs {trade_eligible}."
    payload['summary']=summary
    attach_contract(payload,'research_org_orchestrator',status=status,outputs={'agenda_count':len(agenda),'assigned_task_count':len(assigned_tasks),'autonomous_hypothesis_count':len(hypotheses.get('hypotheses') or []),'autonomous_plan_count':len(experiment_plan.get('plans') or []),'safe_action_count':len(actions),'active_count':len(active),'trade_eligible_recommendations':trade_eligible},metrics=payload['objective_metrics']|{'agenda_count':len(agenda),'assigned_task_count':len(assigned_tasks),'autonomous_hypothesis_count':len(hypotheses.get('hypotheses') or []),'autonomous_plan_count':len(experiment_plan.get('plans') or []),'safe_action_count':len(actions)},warnings=warnings,next_actions=next_actions)
    rid=save_research_org_report('orchestrator_run',summary,payload); payload['report_id']=rid
    Path(args.output).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    if any(a.get('error') for a in actions): sys.exit(1)

if __name__=='__main__': main()
