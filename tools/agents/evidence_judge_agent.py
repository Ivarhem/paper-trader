#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract

def now(): return datetime.now(timezone.utc).isoformat()
def read_json(path):
    p=Path(path)
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc: return {'_read_error':str(exc),'_path':path}



def enrich_judgment(j, metrics=None):
    metrics=metrics or {}
    decision=j.get('decision')
    confidence='low'
    expected_impact='unknown'
    risk='low'
    requires_approval=False
    if decision=='retry_or_inspect':
        confidence='medium'; expected_impact='protect research loop reliability'; risk='low'; requires_approval=False
    elif decision=='propose_policy_review':
        confidence='medium'; expected_impact='potentially improve exit policy / reduce left-tail drag'; risk='medium'; requires_approval=True
    elif decision=='evidence_collected':
        processed=metrics.get('processed_combinations') or metrics.get('audited_items') or metrics.get('saved') or metrics.get('promising_count') or metrics.get('watch_count') or 0
        confidence='medium' if processed else 'low'
        expected_impact='improve validation coverage, theme-spillover evidence, and reduce blind spots'
        risk='low'
    elif decision=='needs_more_samples':
        confidence='low'; expected_impact='more evidence before policy review'; risk='low'
    elif decision=='observe':
        confidence='medium'; expected_impact='no immediate change justified'; risk='low'
    j.update({'confidence':confidence,'expected_impact':expected_impact,'risk':risk,'requires_approval':requires_approval,'proposal':{'route':'guardian_review' if requires_approval else 'observe_or_continue_sampling','authority':'proposal_only','real_trading':False}})
    return j

def judge_plan(plan):
    task=plan.get('task'); out=read_json(plan.get('output') or '') if plan.get('output') else {}
    status=(out.get('contract') or {}).get('status') or out.get('status') or ('missing_output' if not out else 'unknown')
    decision='needs_more_samples'; reason='bounded diagnostic executed or pending; no direct policy change authorized'
    if status in ('failed','error','missing_output'):
        decision='retry_or_inspect'; reason=f'output status {status}'
    elif task=='run_exit_policy_optimizer':
        summ=out.get('summary') or {}; proposals=summ.get('proposal_count') or out.get('proposal_count') or 0
        decision='propose_policy_review' if proposals else 'observe'; reason=f'exit optimizer proposals={proposals}'
    elif task=='run_current_recommendation_validation':
        saved=((out.get('worker') or {}).get('saved')) or out.get('saved') or 0
        decision='needs_more_samples' if saved else 'observe'; reason=f'validation saved={saved}'
    elif task=='run_validation_probe':
        saved=out.get('saved') or out.get('processed_combinations') or 0
        decision='needs_more_samples' if saved else 'observe'; reason=f'validation probe activity={saved}'
    return enrich_judgment({'hypothesis_ids':plan.get('hypothesis_ids') or [plan.get('hypothesis_id')],'targets':plan.get('targets') or [plan.get('target')],'task':task,'output':plan.get('output'),'output_status':status,'decision':decision,'reason':reason,'allowed_next_step':'proposal_only','real_trading':False}, (out.get('contract') or {}).get('metrics') or out.get('summary') or {})

def summarize_runner_results():
    data=read_json('/tmp/research_experiment_results_latest.json')
    out=[]
    for r in data.get('results') or []:
        decision='retry_or_inspect' if r.get('error') else 'needs_more_samples'
        metrics=r.get('output_metrics') or {}
        if r.get('name')=='run_exit_policy_optimizer' and metrics.get('needs_exit_retest'):
            decision='propose_policy_review'
        elif r.get('task')=='run_theme_spillover_backtest' and r.get('output_status') in ('ok','degraded'):
            decision='evidence_collected' if ((metrics.get('promising_count') or 0) or (metrics.get('watch_count') or 0)) else 'observe'
        elif r.get('output_status')=='ok' and ((metrics.get('processed_combinations') or 0) > 0 or (metrics.get('audited_items') or 0) > 0):
            decision='evidence_collected'
        out.append(enrich_judgment({'hypothesis_ids':r.get('hypothesis_ids'),'targets':r.get('targets'),'task':r.get('task') or r.get('name'),'output':r.get('output'),'output_status':r.get('output_status'),'decision':decision,'reason':f"isolated runner metrics={metrics}",'allowed_next_step':'proposal_only','real_trading':False}, metrics))
    return out

def main():
    ap=argparse.ArgumentParser(description='Judge bounded experiment evidence and propose safe next steps')
    ap.add_argument('--plan',default='/tmp/research_experiment_plan_latest.json'); ap.add_argument('--output',default='/tmp/research_evidence_judge_latest.json'); args=ap.parse_args()
    plan=read_json(args.plan); plans=plan.get('plans') or []
    judgments=summarize_runner_results() or [judge_plan(p) for p in plans]
    warnings=[j['reason'] for j in judgments if j.get('decision')=='retry_or_inspect']
    packet={'run_at':now(),'mode':'evidence_judge','real_trading':False,'authority':'judge_and_propose_only','judgments':judgments,'summary':{'judgment_count':len(judgments),'proposal_review_count':sum(1 for j in judgments if j.get('decision')=='propose_policy_review'),'retry_count':sum(1 for j in judgments if j.get('decision')=='retry_or_inspect'),'needs_more_samples_count':sum(1 for j in judgments if j.get('decision')=='needs_more_samples'),'approval_required_count':sum(1 for j in judgments if j.get('requires_approval'))}}
    attach_contract(packet,'evidence_judge',status='ok' if not warnings else 'degraded',outputs={'judgment_count':len(judgments)},metrics=packet['summary'],warnings=warnings,next_actions=['Send proposal_review items to guardian; keep all changes paper-only and approval-gated.'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
