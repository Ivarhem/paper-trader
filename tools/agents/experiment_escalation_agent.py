#!/usr/bin/env python3
"""Experiment Escalation Agent.

Paper/historical research only. Reads recent retest/scout artifacts and decides
whether repeated minor/no-op improvements should escalate to bolder experiment
families. It proposes next experiments only; it does not apply parameters,
modify strategy registry, or place orders.
"""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract


def read_json(path: str) -> dict:
    p=Path(path)
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception: return {}


def classify_result(r: dict) -> str:
    s=r.get('summary') or {}
    d=r.get('delta_vs_baseline') or r.get('delta_vs_market_baseline') or r.get('delta') or {}
    blockers=set(r.get('blockers') or [])
    verdict=str(r.get('verdict') or '')
    q_delta=float(d.get('quality_score_delta') or 0)
    avg=float(s.get('avg_excess_return_pct') or -999)
    ev=float(s.get('expected_excess_value_pct') or -999)
    p10=float(s.get('p10_excess_return_pct') or -999)
    flags=set(s.get('quality_flags') or [])
    if avg > 0 and ev >= -1 and p10 > -8 and not ({'negative_expected_excess_value','left_tail_excess_risk'} & flags) and not blockers:
        return 'meaningful'
    if q_delta >= 12 or avg > 0 or 'improved' in verdict or 'candidate' in verdict:
        if ev < -1 or p10 <= -8 or blockers or {'left_tail_excess_risk','negative_expected_excess_value'} & flags:
            return 'minor_blocked'
        return 'meaningful'
    return 'stagnant'


def collect_results():
    specs=[
        ('exit_policy_retest','/tmp/exit_policy_retest_latest.json','results'),
        ('market_route_retest','/tmp/market_route_retest_latest.json','results'),
        ('relative_excess_gate_retest','/tmp/relative_excess_gate_retest_latest.json','results'),
        ('entry_regime_filter_retest','/tmp/entry_regime_filter_retest_latest.json','results'),
        ('audit_tail_quarantine_scout','/tmp/audit_tail_quarantine_scout_latest.json','experiments'),
        ('positive_cohort_scout','/tmp/positive_cohort_scout_latest.json','candidates'),
    ]
    out=[]
    for name,path,key in specs:
        data=read_json(path)
        for r in data.get(key) or []:
            item=dict(r)
            item['_source_agent']=name
            item['_class']=classify_result(item)
            out.append(item)
    return out


def blockers_from_audit():
    audit=read_json('/tmp/recommendation_audit_latest.json')
    best=(audit.get('summary') or {}).get('best') or {}
    flags=set(best.get('quality_flags') or [])
    out=[]
    if 'negative_expected_excess_value' in flags or (best.get('expected_excess_value_pct') or 0) <= 0: out.append('negative_ev')
    if 'left_tail_excess_risk' in flags or (best.get('p10_excess_return_pct') or 0) <= -8: out.append('left_tail')
    if 'period_instability' in flags: out.append('period_instability')
    if 'no_positive_average_excess' in flags or (best.get('avg_excess_return_pct') or 0) <= 0: out.append('avg_excess_not_positive')
    return out, best


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',default='/tmp/experiment_escalation_latest.json'); args=ap.parse_args()
    results=collect_results()
    counts={k:sum(1 for r in results if r.get('_class')==k) for k in ['meaningful','minor_blocked','stagnant']}
    blockers,best=blockers_from_audit()
    # Escalate when there are repeated minor/stagnant results and core blockers remain.
    weak_streak=counts['minor_blocked'] + counts['stagnant']
    if counts['meaningful']:
        level=1
        reason='meaningful candidate exists; keep validating but do not broaden aggressively yet'
    elif weak_streak >= 5 and {'negative_ev','left_tail'} & set(blockers):
        level=3
        reason='many minor/stagnant experiments and EV/tail blockers remain; switch to bold family/regime experiments'
    elif weak_streak >= 3:
        level=2
        reason='repeated minor/stagnant experiments; widen search beyond plain stop/target grids'
    else:
        level=1
        reason='insufficient repeated weak results; continue bounded validation'
    forbidden=[]
    if level >= 2:
        forbidden += ['plain_stop_target_grid_only','repeat_same_exit_policy_without_new_filter']
    if level >= 3:
        forbidden += ['global_mixed_market_single_logic_only','minor_threshold_tweak_only']
    bold=[]
    if level >= 2:
        bold += [
            {'id':'kr_timeout_decay_plus_no_chase','family':'market_split_plus_entry_filter','market':'KR','policy':'timeout_decay_exit','filters':['exclude_high_chase_risk','require_positive_relative_strength'],'goal':'keep KR avg excess positive while improving p10/EV'},
            {'id':'us_relative_strength_pullback','family':'market_split_new_family','market':'US','strategy_family':'relative_strength_pullback','filters':['ma_trend_positive','avoid_extended_gap'],'goal':'preserve US high route quality with lower timeout/tail'},
            {'id':'exit_policy_combo_fast_loss_timeout','family':'combined_exit_policy','policy_combo':['fast_loss_cut','timeout_decay_exit'],'goal':'combine p10 improvement with timeout/fail reduction'},
        ]
    if level >= 3:
        bold += [
            {'id':'quarantine_current_best_logic_for_kr','family':'logic_market_quarantine','market':'KR','logic':best.get('logic') or 'technical_ma_trend_f10_s40_q60','goal':'stop spending KR budget on globally-best logic until KR route passes'},
            {'id':'new_kr_multi_evidence_entry_family','family':'new_strategy_family','market':'KR','signals':['supply_close_strength','disclosure_clean','relative_strength','no_chase'],'goal':'replace weak KR trend entry with multi-evidence entry'},
            {'id':'rolling_period_stability_retest','family':'gate_design_retest','change':'year_periods_to_rolling_or_quarter_regime','goal':'test whether period_instability is an artifact or real instability'},
        ]
    packet={
        'run_at':datetime.now(timezone.utc).isoformat(),
        'mode':'experiment_escalation',
        'real_trading':False,
        'authority':'proposal_only_no_order_no_param_apply',
        'audit_context':{k:best.get(k) for k in ['quality_score','quality_grade','quality_flags','avg_excess_return_pct','expected_excess_value_pct','p10_excess_return_pct','p25_excess_return_pct']},
        'remaining_blockers':blockers,
        'result_counts':counts,
        'escalation_level':level,
        'reason':reason,
        'forbidden_repeats':forbidden,
        'bold_experiments':bold,
        'evidence':[{'source':r.get('_source_agent'),'policy':r.get('policy') or r.get('id') or r.get('market'),'class':r.get('_class'),'verdict':r.get('verdict'),'blockers':r.get('blockers'), 'summary':{k:(r.get('summary') or {}).get(k) for k in ['quality_score','quality_grade','avg_excess_return_pct','expected_excess_value_pct','p10_excess_return_pct']}} for r in results[:20]],
        'summary':{'escalation_level':level,'bold_experiment_count':len(bold),'forbidden_repeat_count':len(forbidden),'remaining_blocker_count':len(blockers),'meaningful_count':counts['meaningful'],'minor_or_stagnant_count':weak_streak},
        'next_actions':['Run bold experiments as paper-only proposals; do not apply parameters until EV/p10 blockers clear.'] if level>=2 else ['Continue current bounded validation.']
    }
    attach_contract(packet,'experiment_escalation',status='ok',outputs=packet['summary'],metrics=packet['summary'],warnings=[],next_actions=packet['next_actions'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
