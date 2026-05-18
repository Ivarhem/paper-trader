#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,subprocess,sys
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
def run_cmd(name, cmd, timeout):
    started=now()
    try:
        p=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,timeout=timeout)
        return {'name':name,'cmd':cmd,'started_at':started,'ended_at':now(),'returncode':p.returncode,'timed_out':False,'stdout_tail':p.stdout[-2000:],'stderr_tail':p.stderr[-2000:],'error':p.returncode!=0}
    except subprocess.TimeoutExpired as exc:
        out=(exc.stdout or '') if isinstance(exc.stdout,str) else (exc.stdout or b'').decode('utf-8',errors='replace')
        err=(exc.stderr or '') if isinstance(exc.stderr,str) else (exc.stderr or b'').decode('utf-8',errors='replace')
        return {'name':name,'cmd':cmd,'started_at':started,'ended_at':now(),'returncode':124,'timed_out':True,'stdout_tail':out[-2000:],'stderr_tail':err[-2000:],'error':True}

def target_cmd(plan, idx):
    task=plan.get('task'); targets=[t for t in (plan.get('targets') or []) if t]
    out=f"/tmp/research_experiment_{idx:02d}_{task}_latest.json"
    if task=='run_validation_probe':
        logics=[t for t in targets if not ('.' in t)]
        cmd=[sys.executable,'tools/agents/simulation_validation_worker.py','--batch-size','240','--monthly-from','2024-01-01','--horizons','20,40,60','--output',out]
        if logics: cmd += ['--logics', ','.join(logics[:6])]
        return cmd,out
    if task=='run_current_recommendation_validation':
        # Worker derives current symbols/logics from latest recommendation cards.
        # Preserve the planner's bounded capacity instead of shrinking it back
        # to the old smoke-test defaults.
        cmd=list(plan.get('cmd') or [])
        if not cmd:
            cmd=[sys.executable,'tools/agents/current_recommendation_validation_worker.py','--batch-size','600','--symbol-limit','32','--logic-limit','14','--fund-consensus-boost']
        if cmd and cmd[0] == 'python3':
            cmd[0]=sys.executable
        if '--fund-consensus-boost' not in cmd:
            cmd.append('--fund-consensus-boost')
        if '--output' not in cmd:
            cmd += ['--output',out]
        return cmd,out
    if task=='run_exit_policy_optimizer':
        return [sys.executable,'tools/agents/exit_policy_optimizer_agent.py','--output',out],out
    if task=='run_theme_spillover_backtest':
        themes=[t for t in targets if t]
        cmd=[sys.executable,'tools/agents/theme_spillover_backtest_agent.py','--output',out]
        if themes: cmd += ['--theme', ','.join(themes[:6])]
        return cmd,out
    return [],out

def main():
    ap=argparse.ArgumentParser(description='Execute bounded target-aware research experiment plans')
    ap.add_argument('--plan',default='/tmp/research_experiment_plan_latest.json')
    ap.add_argument('--output',default='/tmp/research_experiment_results_latest.json')
    ap.add_argument('--max-actions',type=int,default=3)
    args=ap.parse_args()
    plan=read_json(args.plan); plans=(plan.get('plans') or [])[:args.max_actions]
    results=[]
    for i,p in enumerate(plans,1):
        cmd,out=target_cmd(p,i)
        if not cmd:
            results.append({'plan':p,'skipped':True,'reason':'no executable command'})
            continue
        r=run_cmd(p.get('task') or f'plan_{i}',cmd,int(p.get('timeout_seconds') or 180))
        payload=read_json(out)
        output_status=(payload.get('contract') or {}).get('status') or payload.get('status')
        output_metrics=(payload.get('contract') or {}).get('metrics') or payload.get('summary') or {}
        r.update({
            'task':p.get('task') or r.get('name'),
            'spec_id': p.get('spec_id'),
            'runner_task': p.get('runner_task'),
            'sources': p.get('sources') or [],
            'status':'failed' if r.get('error') else 'ok',
            'metrics':output_metrics,
            'hypothesis_ids':p.get('hypothesis_ids'),
            'targets':p.get('targets'),
            'experiment_type':p.get('experiment_type'),
            'output':out,
            'output_status':output_status,
            'output_metrics':output_metrics,
        })
        results.append(r)
    warnings=[r.get('name')+' failed' for r in results if r.get('error')]
    packet={'run_at':now(),'mode':'bounded_experiment_runner','real_trading':False,'authority':'execute_diagnostics_only','results':results,'summary':{'result_count':len(results),'error_count':sum(1 for r in results if r.get('error')),'target_count':sum(len(r.get('targets') or []) for r in results)}}
    attach_contract(packet,'experiment_runner',status='ok' if not warnings else 'degraded',outputs={'result_count':len(results)},metrics=packet['summary'],warnings=warnings,next_actions=['Evidence judge should evaluate isolated experiment outputs; no direct state mutation authorized.'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
    if warnings: sys.exit(1)
if __name__=='__main__': main()
