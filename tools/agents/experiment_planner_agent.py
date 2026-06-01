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

def current_validation_cmd(batch_size='600', symbol_limit='32', logic_limit='14'):
    return [
        'python3',
        'tools/agents/current_recommendation_validation_worker.py',
        '--batch-size',
        str(batch_size),
        '--symbol-limit',
        str(symbol_limit),
        '--logic-limit',
        str(logic_limit),
        '--fund-consensus-boost',
    ]


def ledger_key(target, experiment_type):
    import hashlib
    return hashlib.sha1(f'{target}|{experiment_type}'.encode()).hexdigest()[:12]

def result_metric_delta(entry):
    metrics_list=(entry or {}).get('result_metrics') or []
    if isinstance(metrics_list, dict):
        metrics_list=[metrics_list]
    for metrics in metrics_list:
        if not isinstance(metrics, dict):
            continue
        saved=metrics.get('saved') if isinstance(metrics.get('saved'),dict) else {}
        for key in ('inserted','updated','audited_items','sample_count'):
            try:
                if float(metrics.get(key) or 0) > 0:
                    return True
            except Exception:
                pass
        for key in ('inserted','updated'):
            try:
                if float(saved.get(key) or 0) > 0:
                    return True
            except Exception:
                pass
    return False

def has_meaningful_delta(entry):
    d=(entry or {}).get('delta') or {}
    return bool(d.get('strategies') or d.get('recommendations') or result_metric_delta(entry))

def load_repeat_history():
    data=read_json('/tmp/research_experiment_ledger.json')
    return {e.get('experiment_key'):e for e in data.get('entries') or [] if e.get('experiment_key')}

def repeat_suppression_reason(entry):
    if not entry:
        return None
    if has_meaningful_delta(entry):
        return None
    if entry.get('repeated') is True or int(entry.get('repeat_seen_count') or 0) > 0:
        return 'repeated_without_measurable_delta'
    if entry.get('last_repeat_reason') or entry.get('last_seen_at'):
        return 'ledger_repeat_without_delta'
    return None

def filter_repeated_targets(plan, repeat_history, suppressed):
    targets=[str(t) for t in (plan.get('targets') or ([plan.get('target')] if plan.get('target') else [])) if t]
    exp=plan.get('experiment_type') or plan.get('runner_task') or plan.get('task')
    if not targets:
        if plan.get('task') in ('run_validation_probe','run_theme_spillover_backtest'):
            suppressed.append({'plan':plan.get('hypothesis_id') or plan.get('spec_id'), 'task':plan.get('task'), 'reason':'no_executable_targets'})
            return None
        return plan
    kept=[]
    for target in targets:
        entry=repeat_history.get(ledger_key(target, exp))
        reason=repeat_suppression_reason(entry)
        if reason:
            suppressed.append({'target':target,'experiment_type':exp,'task':plan.get('task'),'prior_run_at':entry.get('run_at'),'repeat_seen_count':entry.get('repeat_seen_count',0),'reason':reason})
        else:
            kept.append(target)
    if not kept and (plan.get('task') or '').startswith('run_'):
        return None
    q=dict(plan)
    q['targets']=kept
    q['target']=kept[0] if kept else plan.get('target')
    return q

def plan_for(h):
    et=h.get('experiment_type'); target=h.get('target'); owner=h.get('owner_agent')
    base={'hypothesis_id':h.get('id'),'priority':h.get('priority'),'target':target,'experiment_type':et,'owner_agent':owner,'real_trading':False,'authority':'bounded_diagnostic_only','success_criteria':h.get('success_criteria'),'expected_improvement':h.get('expected_improvement')}
    if et in ('exit_policy_retest','portfolio_exit_policy_retest'):
        base.update({'task':'run_exit_policy_optimizer','cmd':['python3','tools/agents/exit_policy_optimizer_agent.py'],'timeout_seconds':120,'output':'/tmp/exit_policy_optimizer_latest.json'})
    elif et in ('entry_filter_retest','threshold_variant_retest','near_threshold_validation','coverage_gap_validation'):
        base.update({'task':'run_validation_probe','cmd':['python3','tools/agents/discovery_validation_worker.py','--batch-size','600','--logic-filter',str(target)],'timeout_seconds':240,'output':'/tmp/discovery_validation_latest.json','notes':'Targeted discovery validation via --logic-filter'})
    elif et in ('symbol_validation_boost','mover_symbol_validation_boost'):
        base.update({'task':'run_current_recommendation_validation','cmd':current_validation_cmd(),'timeout_seconds':600,'output':'/tmp/current_recommendation_validation_latest.json'})
    elif et=='theme_spillover_follow_through':
        base.update({'task':'run_theme_spillover_backtest','cmd':['python3','tools/agents/theme_spillover_backtest_agent.py','--theme',str(target)],'timeout_seconds':240,'output':'/tmp/theme_spillover_backtest_latest.json','notes':'Historical follow-through backtest of theme spillover; no recommendation or strategy status mutation.'})
    else:
        base.update({'task':'observe','cmd':[],'timeout_seconds':0,'output':None})
    return base

def plan_for_spec(spec):
    runner_task=spec.get('runner_task')
    targets=[str(x) for x in (spec.get('targets') or []) if x]
    primary_target=targets[0] if targets else None
    task_map={
        'exit_policy_optimizer':'run_exit_policy_optimizer',
        'current_recommendation_validation':'run_current_recommendation_validation',
        'validation_probe':'run_validation_probe',
        'theme_spillover_backtest':'run_theme_spillover_backtest',
        'market_route_review':'observe',
    }
    task=task_map.get(runner_task,'observe')
    base={
        'spec_id': spec.get('id'),
        'hypothesis_id': spec.get('id'),
        'hypothesis_ids': [spec.get('id')] if spec.get('id') else [],
        'priority': spec.get('priority') or 'medium',
        'target': primary_target,
        'targets': targets,
        'experiment_type': runner_task,
        'runner_task': runner_task,
        'owner_agent': 'generic_experiment_workflow',
        'real_trading': False,
        'authority': 'bounded_diagnostic_only',
        'success_criteria': spec.get('success_criteria') or {},
        'expected_improvement': spec.get('title'),
        'source': spec.get('source'),
        'sources': spec.get('sources') or [],
        'evidence': spec.get('evidence') or {},
        'task': task,
        'timeout_seconds': 240,
        'output': None,
    }
    if task=='run_exit_policy_optimizer':
        base.update({'cmd':['python3','tools/agents/exit_policy_optimizer_agent.py'],'timeout_seconds':120,'output':'/tmp/exit_policy_optimizer_latest.json'})
    elif task=='run_current_recommendation_validation':
        base.update({'cmd':current_validation_cmd(),'timeout_seconds':600,'output':'/tmp/current_recommendation_validation_latest.json'})
    elif task=='run_validation_probe':
        base.update({'cmd':['python3','tools/agents/discovery_validation_worker.py','--batch-size','600'],'timeout_seconds':240,'output':'/tmp/discovery_validation_latest.json'})
        if targets:
            base['cmd'] += ['--logic-filter', ','.join(targets[:8])]
    elif task=='run_theme_spillover_backtest':
        base.update({'cmd':['python3','tools/agents/theme_spillover_backtest_agent.py'],'timeout_seconds':240,'output':'/tmp/theme_spillover_backtest_latest.json'})
        if targets:
            base['cmd'] += ['--theme', ','.join(targets[:8])]
    else:
        base.update({'cmd':[],'timeout_seconds':0,'notes':'Spec is observation/proposal only; no bounded runner command mapped.'})
    return base

def main():
    ap=argparse.ArgumentParser(description='Turn research hypotheses into bounded experiment plans')
    ap.add_argument('--input',default='/tmp/research_hypotheses_latest.json')
    ap.add_argument('--specs',default='/tmp/research_experiment_specs_latest.json')
    ap.add_argument('--output',default='/tmp/research_experiment_plan_latest.json')
    args=ap.parse_args()
    compiled=read_json(args.specs); specs=compiled.get('specs') or []
    hyp=read_json(args.input); hyps=hyp.get('hypotheses') or []
    plans=[]; seen=set()
    if specs:
        for spec in specs:
            key=(spec.get('runner_task'), tuple(spec.get('targets') or []), spec.get('title'))
            if key in seen: continue
            seen.add(key); plans.append(plan_for_spec(spec))
    else:
        for h in hyps:
            key=(h.get('experiment_type'),h.get('target'))
            if key in seen: continue
            seen.add(key); plans.append(plan_for(h))
    repeat_history=load_repeat_history(); suppressed=[]
    filtered=[]
    for p in plans:
        q=filter_repeated_targets(p, repeat_history, suppressed)
        if q:
            filtered.append(q)
    plans=filtered
    # Collapse duplicate owner commands to a smaller safe action set while keeping hypothesis links.
    grouped=[]; by_task={}
    for p in plans:
        if p.get('task') in ('run_validation_probe','run_theme_spillover_backtest'):
            k=(p.get('task'),)
        else:
            k=(p.get('task'),tuple(p.get('cmd') or []))
        if k not in by_task:
            q=dict(p)
            q['hypothesis_ids']=[p['hypothesis_id']] if p.get('hypothesis_id') else []
            q['targets']=[p.get('target')] if p.get('target') else list(p.get('targets') or [])
            grouped.append(q); by_task[k]=q
        else:
            if p.get('hypothesis_id'):
                by_task[k]['hypothesis_ids'].append(p['hypothesis_id'])
            by_task[k]['targets'].extend([t for t in ([p.get('target')] if p.get('target') else (p.get('targets') or [])) if t])
            if p.get('task') == 'run_validation_probe':
                targets=[str(t) for t in by_task[k]['targets'] if t]
                by_task[k]['cmd']=['python3','tools/agents/discovery_validation_worker.py','--batch-size','600']
                if targets:
                    by_task[k]['cmd'] += ['--logic-filter',','.join(targets)]
            elif p.get('task') == 'run_theme_spillover_backtest':
                targets=[str(t) for t in by_task[k]['targets'] if t]
                by_task[k]['cmd']=['python3','tools/agents/theme_spillover_backtest_agent.py']
                if targets:
                    by_task[k]['cmd'] += ['--theme',','.join(targets)]
    packet={'run_at':now(),'mode':'bounded_experiment_planning','source':'compiled_specs' if specs else 'hypotheses','real_trading':False,'authority':'plan_only','plans':grouped[:8],'raw_plan_count':len(plans),'suppressed_repeats':suppressed[:20],'summary':{'plan_count':min(len(grouped),8),'raw_plan_count':len(plans),'compiled_spec_count':len(specs),'high_priority_count':sum(1 for p in grouped[:8] if p.get('priority')=='high'),'suppressed_repeat_count':len(suppressed)}}
    warnings=['suppressed_repeated_experiments'] if suppressed else []
    attach_contract(packet,'experiment_planner',status='ok',outputs={'plan_count':len(packet['plans']),'suppressed_repeat_count':len(suppressed)},metrics=packet['summary'],warnings=warnings,next_actions=['Research Director/Orchestrator may execute safe diagnostic commands; do not apply policy changes directly.'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
