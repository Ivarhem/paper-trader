#!/usr/bin/env python3
from __future__ import annotations
"""Research agenda orchestrator for paper_trader.

This is intentionally paper/historical only. It does not place orders. Its job is
not to own strategy generation or recommendation decisions. It reads director
contracts, chooses cross-domain research priorities, and routes bounded agenda
items back to the director that owns the work.
"""
import argparse, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.database import init_db, validation_coverage, save_research_org_report
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

def director_status(packet: dict) -> str:
    return str(packet.get('domain_status') or packet.get('status') or ((packet.get('contract') or {}).get('status')) or 'unknown')


def director_bottleneck(packet: dict) -> str | None:
    return packet.get('bottleneck') or ((packet.get('summary') or {}).get('bottleneck'))


def assigned_research_tasks(agenda: list[dict]) -> list[dict]:
    tasks=[]
    for item in agenda:
        owner=item.get('owner_director')
        if not owner:
            continue
        tasks.append({
            'priority': item.get('priority') or 'medium',
            'owner_agent': owner,
            'theme': item.get('theme'),
            'target': item.get('target'),
            'action': item.get('action'),
            'unblock_condition': item.get('unblock_condition') or 'owner director reports bottleneck cleared or demoted to watch with evidence',
            'evidence': item.get('evidence') or {'why': item.get('why')},
        })
    return tasks[:8]

def build_research_agenda(directors, recs, evaluation, calibration):
    items=[]
    strategy=directors.get('strategy_director') or {}
    fund=directors.get('fund_director') or {}
    recommendation=directors.get('recommendation_desk_lead') or {}
    governance=directors.get('governance_director') or {}
    data=directors.get('data_steward') or {}
    rec_items=recs.get('items') or []
    trade_eligible=sum(1 for x in rec_items if x.get('trade_eligible') or x.get('recommendation_bucket')=='approved')
    strategy_summary=strategy.get('summary') or {}
    queue=strategy.get('promotion_queue') or []
    if director_status(strategy) in ('watch','degraded','action_required') or strategy.get('bottleneck'):
        items.append({
            'priority': 'high' if director_status(strategy) in ('degraded','action_required') else 'medium',
            'theme': 'strategy_research_backlog',
            'owner_director': 'strategy_director',
            'objective': 'keep strategy generation/validation/lifecycle inside Strategy Director ownership',
            'why': director_bottleneck(strategy) or 'strategy director reports watch-level research bottleneck',
            'action': 'Strategy Director should route promotion_queue work to its owned validation/lifecycle workers; orchestrator only tracks the agenda.',
            'target': 'promotion_queue',
            'unblock_condition': 'strategy_director reports high-confidence historical active count or documents why queue remains watch-only',
            'evidence': {'status': director_status(strategy), 'summary': strategy_summary, 'promotion_queue': queue[:5]},
        })
    if trade_eligible == 0 and rec_items:
        items.append({'priority':'high','theme':'recommendation_conversion','owner_director':'recommendation_desk_lead','objective':'turn research_watch candidates into qualified paper-buy candidates', 'why':'current recommendation list has no approved/trade_eligible candidates', 'action':'Recommendation Desk Lead should route validation, calibration, critic, and committee bottlenecks; do not generate more symbols from the orchestrator.', 'target':'current_recommendations'})
    cal_findings=calibration.get('findings') or []
    bad_cal=[x for x in cal_findings if x.get('severity') in ('action','urgent','watch')]
    if bad_cal:
        items.append({'priority':'medium','theme':'score_calibration','owner_director':'recommendation_desk_lead','objective':'align score buckets with realized forward returns','why':bad_cal[0].get('finding'), 'action':bad_cal[0].get('recommendation')})
    for name, packet in [('data_steward', data), ('fund_director', fund), ('governance_director', governance)]:
        status=director_status(packet)
        if status in ('degraded','action_required'):
            items.append({'priority':'high','theme':f'{name}_bottleneck','owner_director':name,'objective':'clear director-level blocker before strategy or recommendation expansion','why':director_bottleneck(packet) or f'{name} status is {status}', 'action':'Owner director should produce the repair queue; orchestrator tracks cross-domain priority only.', 'evidence':{'status':status,'summary':packet.get('summary')}})
    org_actions=evaluation.get('next_actions') or []
    for action in org_actions[:2]:
        items.append({'priority':'medium','theme':'org_evaluator','owner_director':'governance_director','objective':'resolve organization finding that blocks recommendation quality','why':'org evaluator next action', 'action':action})
    if not items:
        items.append({'priority':'low','theme':'steady_state','owner_director':'executive_director','objective':'continue compounding evidence','why':'no urgent bottleneck detected', 'action':'Keep directors validating their own queues and monitor drift.'})
    return items[:8]

def main():
    ap=argparse.ArgumentParser(description='Route paper-research improvement agenda across directors')
    ap.add_argument('--output', default='/tmp/research_org_orchestrator_latest.json')
    ap.add_argument('--execute-safe-actions', action='store_true', default=False)
    args=ap.parse_args(); init_db()

    coverage=validation_coverage()
    recs=read_json('/tmp/recommendations_latest.json')
    evaluation=read_json('/tmp/research_org_evaluation_latest.json')
    calibration=read_json('/tmp/recommendation_calibration_latest.json')
    hypotheses=read_json('/tmp/research_hypotheses_latest.json')
    experiment_plan=read_json('/tmp/research_experiment_plan_latest.json')
    evidence_judge=read_json('/tmp/research_evidence_judge_latest.json')
    experiment_ledger=read_json('/tmp/research_experiment_ledger_latest.json')
    directors={
        'data_steward': read_json('/tmp/data_steward_latest.json'),
        'strategy_director': read_json('/tmp/strategy_director_latest.json'),
        'fund_director': read_json('/tmp/fund_director_latest.json'),
        'recommendation_desk_lead': read_json('/tmp/recommendation_desk_lead_latest.json'),
        'governance_director': read_json('/tmp/governance_director_latest.json'),
    }

    agenda=build_research_agenda(directors,recs,evaluation,calibration)
    assigned_tasks=assigned_research_tasks(agenda)
    # Execution belongs to directors and the autonomous experiment loop. This
    # agent is a routing surface, so it should not invoke strategy workers.
    actions=[]

    recs_after=read_json('/tmp/recommendations_latest.json')
    trade_eligible=sum(1 for x in (recs_after.get('items') or []) if x.get('trade_eligible') or x.get('recommendation_bucket')=='approved')
    strategy_summary=(directors.get('strategy_director') or {}).get('summary') or {}
    payload={
        'run_at':now(),
        'mode':'research_agenda_orchestrator',
        'real_trading':False,
        'mission':'Route paper/historical improvement work across director-owned queues; do not own strategy generation, validation, lifecycle, or recommendation decisions.',
        'objective_metrics':{'strategy_active_count':strategy_summary.get('active_count'),'strategy_promotion_queue_count':strategy_summary.get('promotion_queue_count'),'trade_eligible_recommendations':trade_eligible,'coverage_pct':coverage.get('coverage_pct_estimate'),'completed_results':coverage.get('completed_results')},
        'director_statuses':{name: director_status(packet) for name, packet in directors.items()},
        'research_agenda':agenda,
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
        'execution_model':'director_routed_agenda_only',
        'notify': bool([x for x in agenda if x.get('priority')=='high'] or [a for a in actions if a.get('error')]),
    }
    status='degraded' if any(a.get('error') for a in actions) else 'ok'
    warnings=[f"{a['name']} failed" for a in actions if a.get('error')]
    next_actions=[f"{x.get('owner_director')}: {x.get('action')}" for x in agenda if x.get('priority') in ('high','medium')][:5]
    summary=f"Research Agenda Orchestrator: {len(agenda)} agenda items, {len(actions)} direct actions, trade-eligible recs {trade_eligible}."
    payload['summary']=summary
    attach_contract(payload,'research_org_orchestrator',status=status,outputs={'agenda_count':len(agenda),'assigned_task_count':len(assigned_tasks),'autonomous_hypothesis_count':len(hypotheses.get('hypotheses') or []),'autonomous_plan_count':len(experiment_plan.get('plans') or []),'safe_action_count':len(actions),'trade_eligible_recommendations':trade_eligible},metrics=payload['objective_metrics']|{'agenda_count':len(agenda),'assigned_task_count':len(assigned_tasks),'autonomous_hypothesis_count':len(hypotheses.get('hypotheses') or []),'autonomous_plan_count':len(experiment_plan.get('plans') or []),'safe_action_count':len(actions)},warnings=warnings,next_actions=next_actions)
    rid=save_research_org_report('orchestrator_run',summary,payload); payload['report_id']=rid
    Path(args.output).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    if any(a.get('error') for a in actions): sys.exit(1)

if __name__=='__main__': main()
