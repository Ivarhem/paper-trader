#!/usr/bin/env python3
from __future__ import annotations
import json, os, stat, sys, subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract

OUT=Path('/tmp/org_improvement_guardian_latest.json')
HISTORY=Path('/tmp/org_improvement_guardian_history.json')
PROPOSALS=Path('/tmp/org_improvement_guardian_patch_proposals.json')

SAFE_FILE_EXECUTABLES=[ROOT/'scripts'/'run_research_org_cron.sh']


def load_json(path):
    p=Path(path)
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception: return {}


def finding_key(f):
    metric=f.get('metric') or {}
    return '|'.join(str(x or '') for x in [f.get('area'), metric.get('gate'), metric.get('kind') or metric.get('decision') or metric.get('value')])


def recent_repeat_count(key: str, limit=24) -> int:
    if not key or not HISTORY.exists(): return 0
    try: rows=json.loads(HISTORY.read_text(encoding='utf-8'))
    except Exception: return 0
    count=0
    for packet in rows[-limit:]:
        for c in packet.get('classified_findings') or []:
            if c.get('finding_key') == key:
                count += 1
                break
    return count


def lifecycle_blocking_gates(org=None, pipeline=None, committee=None):
    org = org or load_json('/tmp/research_org_evaluation_latest.json')
    pipeline = pipeline or load_json('/tmp/research_pipeline_latest.json')
    committee = committee or load_json('/tmp/investment_committee_latest.json')
    gates=[]
    vc = pipeline.get('validation_first_control') or {}
    coverage = float(vc.get('coverage_pct') or 0)
    if coverage and coverage < 25:
        gates.append({'gate':'validation_coverage','severity':'high','detail':f'coverage {coverage:.2f}% < 25%; validation-first mode active' if vc.get('validation_first_mode') else f'coverage {coverage:.2f}% < 25%','metric':{'coverage_pct':coverage,'validation_first_mode':bool(vc.get('validation_first_mode')),'repeat_suppression_mode':bool(vc.get('repeat_suppression_mode')),'recent_repeat_count':vc.get('recent_repeat_count')}})
    rec = pipeline.get('recommendations_summary') or {}
    if int(rec.get('trade_eligible_count') or 0) == 0 and int(rec.get('item_count') or 0) >= 10:
        gates.append({'gate':'no_trade_eligible_recommendations','severity':'medium','detail':f"0/{rec.get('item_count')} recommendations trade-eligible; keep criteria strict but route evidence gaps to validation",'metric':{'item_count':rec.get('item_count'),'bucket_counts':rec.get('bucket_counts')}})
    after_status = ((pipeline.get('after') or {}).get('strategy_status') or {})
    if int(after_status.get('active') or 0) == 0 and int(after_status.get('repair_active') or 0) + int(after_status.get('validation_active') or 0) > 0:
        gates.append({'gate':'no_qualified_active_promotions','severity':'high','detail':'0 formal active strategies; strategies remain repair/validation-active until quality gates improve','metric':after_status})
    val = pipeline.get('validation_summary') or {}
    best = val.get('best') or {}
    qflags = best.get('quality_flags') or []
    if qflags:
        gates.append({'gate':'audit_quality_flags','severity':'high','detail':'best logic still has quality flags: ' + ', '.join(qflags[:4]),'metric':{'best_logic':val.get('best_logic'),'quality_score':best.get('quality_score'),'quality_grade':best.get('quality_grade'),'avg_excess_return_pct':best.get('avg_excess_return_pct'),'quality_flags':qflags[:8]}})
    cs = committee.get('summary') or {}
    if int(cs.get('approved_count') or 0) == 0 and int(cs.get('support_count') or 0) == 0 and int(cs.get('item_count') or 0) >= 10:
        gates.append({'gate':'committee_zero_approval','severity':'medium','detail':f"committee approved/support 0/{cs.get('item_count')}; feed reject reasons back to validation",'metric':{'item_count':cs.get('item_count'),'research_support_count':cs.get('research_support_count'),'bucket_counts':cs.get('bucket_counts')}})
    return gates


def classify_finding(f):
    area=f.get('area') or 'unknown'; sev=f.get('severity') or 'info'; rec=f.get('recommendation') or ''; metric=f.get('metric') or {}
    key=finding_key(f); repeats=recent_repeat_count(key)
    if area == 'disclosure_quality' and 'title_only_symbols' in metric:
        return {'area':area,'severity':sev,'recommendation':rec,'class':'auto_apply_low_risk','action':'run_disclosure_impact_for_symbols','symbols':metric.get('title_only_symbols') or [],'reason':'공시 영향 평가 누락은 DB assessment 보강만 수행하므로 low-risk'}
    if area == 'ui_integrity':
        return {'area':area,'severity':sev,'recommendation':rec,'class':'patch_proposal','action':'fix_integrity_false_warning','reason':'UI/integrity warning rule patch 필요'}
    if area in ('recommendation_quality','recommendation_gate'):
        metric = f.get('metric') or {}
        dominant = metric.get('dominant_critic_issue') or {}
        critic_high = int(metric.get('critic_high') or 0)
        final_count = int(metric.get('final_recommendations') or metric.get('total') or 0)
        dominant_issue = str(dominant.get('issue') or '')
        dominant_count = int(dominant.get('count') or 0)
        # Aggregation fixed the noisy critic warning, but a repeated dominant blocker should not
        # stay observe-only forever.  If sample/edge shortage is the main reason candidates are
        # blocked, surface a proposal for the validation loop instead of changing thresholds.
        if dominant or '최대 병목은' in rec:
            if critic_high >= max(5, int(final_count * 0.5)) and (
                repeats >= 2 or dominant_count >= final_count or dominant_count >= max(10, int(final_count * 0.8))
            ) and (
                '샘플' in dominant_issue or 'edge' in dominant_issue or dominant_count >= max(10, int(final_count * 0.8))
            ):
                return {'area':area,'severity':sev,'recommendation':rec,'class':'patch_proposal','action':'expand_symbol_edge_validation_samples','finding_key':key,'recent_repeat_count':repeats,'dominant_critic_issue':dominant,'critic_high':critic_high,'final_recommendations':final_count,'reason':'critic 병목 aggregate가 반복되어 threshold 완화가 아니라 종목별 검증 샘플/edge 확충 루프로 승격'}
            return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','action':'critic_bottleneck_aggregated','finding_key':key,'recent_repeat_count':repeats,'dominant_critic_issue':dominant,'reason':'critic 병목 aggregate가 적용되어 최대 병목을 next action으로 노출 중'}
        return {'area':area,'severity':sev,'recommendation':rec,'class':'patch_proposal','action':'aggregate_critic_bottlenecks','finding_key':key,'recent_repeat_count':repeats,'reason':'추천 품질 기준 변경은 동작 영향이 있어 proposal 우선'}
    if area == 'gate_effectiveness':
        metric=f.get('metric') or {}
        dominant=float(metric.get('dominant_ratio') or 0)
        total=int(metric.get('total') or 0)
        gate=metric.get('gate')
        if metric.get('downgraded_to_aggregate'):
            return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','action':'constant_gate_already_aggregate','gate':gate,'finding_key':key,'recent_repeat_count':repeats,'dominant_ratio':dominant,'total':total,'reason':'상수 게이트가 이미 aggregate 병목으로 낮춰져 item-level gate noise를 만들지 않음'}
        # If a gate is effectively constant across the recommendation set, it stops being a useful gate.
        # Repeated or complete dominance should produce a concrete patch proposal instead of passive observation.
        if dominant >= 0.95 and total >= 10 and (repeats >= 1 or dominant >= 1.0):
            return {'area':area,'severity':sev,'recommendation':rec,'class':'patch_proposal','action':'downgrade_constant_gate_to_aggregate_signal','gate':gate,'finding_key':key,'recent_repeat_count':repeats,'dominant_ratio':dominant,'total':total,'reason':'게이트가 후보 대부분/전체에 동일 판정을 반복하므로 차단/경고 게이트가 아니라 aggregate 병목 및 validation priority 신호로 낮춰야 함'}
        if sev in ('info','watch'):
            return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','action':'monitor_gate_discrimination','gate':gate,'finding_key':key,'recent_repeat_count':repeats,'dominant_ratio':dominant,'total':total,'reason':'게이트 변별력/낡은 룰 감지는 구조 변경 전 관찰과 구체 권고 우선'}
        return {'area':area,'severity':sev,'recommendation':rec,'class':'patch_proposal','action':'fix_stale_gate_rule','gate':gate,'finding_key':key,'recent_repeat_count':repeats,'reason':'게이트 동작 변경은 proposal 우선'}
    if area == 'recommendation_evidence_flow':
        cls = 'observe' if sev == 'info' else 'patch_proposal'
        action = 'evidence_flow_order_ok' if sev == 'info' else 'fix_recommendation_evidence_pipeline_order'
        return {'area':area,'severity':sev,'recommendation':rec,'class':cls,'action':action,'finding_key':key,'recent_repeat_count':repeats,'reason':'종목추천은 제품 decision surface이고 fund/market/audit는 같은 사이클의 보조 evidence로만 주입되어야 함'}
    if area in ('fund_org_loop','fund_recommendation_link','fund_symbol_consensus'):
        action = 'observe_fund_org_loop'
        cls = 'observe'
        champion_count = int(metric.get('champion_count') or 0)
        candidate_count = int(metric.get('candidate_count') or 0)
        fund_count = int(metric.get('fund_count') or 0)
        if area == 'fund_recommendation_link' and sev in ('action','urgent'):
            action = 'repair_fund_recommendation_overlay'
            cls = 'patch_proposal'
        elif area == 'fund_org_loop' and repeats >= 6 and fund_count >= 20 and champion_count == 0:
            action = 'fund_champion_bottleneck_review'
            cls = 'patch_proposal'
        return {'area':area,'severity':sev,'recommendation':rec,'class':cls,'action':action,'finding_key':key,'recent_repeat_count':repeats,'champion_count':champion_count,'candidate_count':candidate_count,'fund_count':fund_count,'reason':'fund-first 구조는 종목추천 품질 엔진이므로 추천 연결 여부를 조직 루프로 추적'}
    if area == 'target_return_adjustment':
        if not metric.get('has_dedicated_evaluator'):
            return {'area':area,'severity':sev,'recommendation':rec,'class':'patch_proposal','action':'add_target_return_adjustment_evaluator','finding_key':key,'recent_repeat_count':repeats,'adjustment_pct_points':metric.get('adjustment_pct_points'),'reason':'목표수익률 보정은 추천 결과에 직접 영향을 주는 가변 파라미터라 별도 paper 성과 evaluator가 필요함'}
        applied_count=int(metric.get('applied_count') or 0); total=int(metric.get('total') or 0)
        if metric.get('arm_sample_backlog_visible'):
            return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','action':'target_return_arm_sample_backlog_visible','finding_key':key,'recent_repeat_count':repeats,'adjustment_pct_points':metric.get('adjustment_pct_points'),'applied_count':applied_count,'total':total,'arm_sample_backlog':metric.get('arm_sample_backlog') or [],'meta_decision':metric.get('meta_decision'),'reason':'arm별 outcome sample backlog와 meta decision이 evaluator/monitor에 노출되어 있으며, 표본 충족 전 자동 변경은 하지 않음'}
        if repeats >= 12 and total > 0 and applied_count >= total:
            return {'area':area,'severity':sev,'recommendation':rec,'class':'patch_proposal','action':'target_return_arm_sample_backlog','finding_key':key,'recent_repeat_count':repeats,'adjustment_pct_points':metric.get('adjustment_pct_points'),'applied_count':applied_count,'total':total,'reason':'전용 evaluator가 있어도 동일 보정 arm이 계속 전원 적용되면 arm별 outcome sample backlog/승격조건을 next action으로 노출해야 함'}
        return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','action':'target_return_adjustment_evaluator_active','finding_key':key,'recent_repeat_count':repeats,'reason':'전용 evaluator가 있어 관찰 모드'}
    if area == 'validation_throughput':
        coverage=float(metric.get('coverage_pct') or 0)
        if metric.get('validation_first_mode'):
            return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','action':'validation_first_mode_active','finding_key':key,'recent_repeat_count':repeats,'coverage_pct':coverage,'reason':'검증 우선 모드가 이미 적용되어 신규 생성보다 검증 capacity를 우선함'}
        if coverage < 25 and repeats >= 6:
            return {'area':area,'severity':sev,'recommendation':rec,'class':'patch_proposal','action':'prioritize_validation_capacity_over_new_generation','finding_key':key,'recent_repeat_count':repeats,'coverage_pct':coverage,'validation_first_mode':bool(metric.get('validation_first_mode')),'reason':'검증 우선 모드가 켜져 있어도 낮은 커버리지가 반복되면 batch/replay capacity를 실제 under-tested 추천/전략에 더 배정해야 함'}
        if coverage < 25 and repeats >= 2:
            return {'area':area,'severity':sev,'recommendation':rec,'class':'patch_proposal','action':'prioritize_validation_capacity_over_new_generation','finding_key':key,'recent_repeat_count':repeats,'coverage_pct':coverage,'reason':'검증 커버리지 부족이 반복되면 신규 전략 생성보다 검증 capacity/batch 우선순위 제안이 필요'}
        return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','finding_key':key,'recent_repeat_count':repeats,'reason':'검증 처리량 관찰 중'}
    if area == 'coverage_balance' and metric.get('monitor_under_tested_visible'):
        return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','action':'under_tested_monitor_visible','finding_key':key,'recent_repeat_count':repeats,'reason':'저샘플 전략 목록이 monitor validation panel에 노출되어 검증 우선순위 추적 중'}
    if area == 'autonomous_research':
        if repeats >= 6 and int(metric.get('suppressed_repeat_count') or 0) == 0 and int(metric.get('ledger_repeat_count') or 0) > int(metric.get('ledger_deduped_repeat_count') or 0):
            return {'area':area,'severity':sev,'recommendation':rec,'class':'patch_proposal','action':'tighten_research_repeat_suppression','finding_key':key,'recent_repeat_count':repeats,'ledger_repeat_count':metric.get('ledger_repeat_count'),'ledger_deduped_repeat_count':metric.get('ledger_deduped_repeat_count'),'reason':'자율 연구가 정상 순환하더라도 반복 억제 카운터가 0이면 중복/유사 실험을 더 적극적으로 proposal 단계에서 걸러야 함'}
        return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','action':'monitor_autonomous_research_loop','finding_key':key,'recent_repeat_count':repeats,'reason':'자율 연구 루프 관찰 중'}
    if area == 'lifecycle_stability':
        blockers = lifecycle_blocking_gates()
        if repeats >= 12 and int(metric.get('completed') or 0) > 0:
            return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','action':'stalled_lifecycle_bottleneck_visible','finding_key':key,'recent_repeat_count':repeats,'completed':metric.get('completed'),'blocking_gates':blockers,'primary_blocking_gate':(blockers[0].get('gate') if blockers else None),'reason':'상태 변화 반복은 lifecycle_bottlenecks와 dashboard card로 노출되어 있으며, 다음 조치는 표시된 blocking gate를 해소하는 것'}
        return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','finding_key':key,'recent_repeat_count':repeats,'blocking_gates':blockers,'primary_blocking_gate':(blockers[0].get('gate') if blockers else None),'reason':'운영 지표 관찰/weight learning 대상이며 즉시 구조 변경 없음'}
    if area == 'investment_committee':
        support_count=int(metric.get('support_count') or 0); approved_count=int(metric.get('approved_count') or 0); trade_eligible_count=int(metric.get('trade_eligible_count') or 0); item_count=int(metric.get('item_count') or 0)
        current_validation = load_json('/tmp/current_recommendation_validation_latest.json')
        selection_policy = str(current_validation.get('selection_policy') or ((current_validation.get('priority_meta') or {}).get('selection_policy')) or '')
        if 'committee_bottleneck' in selection_policy:
            return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','action':'committee_bottleneck_feedback_loop_active','finding_key':key,'recent_repeat_count':repeats,'item_count':item_count,'support_count':support_count,'approved_count':approved_count,'trade_eligible_count':trade_eligible_count,'selection_policy':selection_policy,'reason':'위원회 병목이 current recommendation validation priority에 이미 반영되어 기준 완화 없이 사후성과 샘플을 축적 중'}
        if repeats >= 6 and item_count >= 10 and support_count == 0 and approved_count == 0 and trade_eligible_count == 0:
            return {'area':area,'severity':sev,'recommendation':rec,'class':'patch_proposal','action':'committee_bottleneck_feedback_loop','finding_key':key,'recent_repeat_count':repeats,'item_count':item_count,'support_count':support_count,'approved_count':approved_count,'trade_eligible_count':trade_eligible_count,'reason':'위원회가 계속 전원 보류/거절만 하면 기준 완화가 아니라 어떤 evidence가 부족한지 validation task로 되돌리는 피드백 루프가 필요'}
        return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','finding_key':key,'recent_repeat_count':repeats,'reason':'운영 지표 관찰/weight learning 대상이며 즉시 구조 변경 없음'}
    if area == 'agent_health' and sev == 'info':
        return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','action':'agent_health_ok_stale_snapshot_explained','finding_key':key,'recent_repeat_count':repeats,'reason':'pipeline 산출물이 확인된 정보성 상태이며 조치 대상 아님'}
    if area == 'committee_weights':
        return {'area':area,'severity':sev,'recommendation':rec,'class':'observe','finding_key':key,'recent_repeat_count':repeats,'reason':'운영 지표 관찰/weight learning 대상이며 즉시 구조 변경 없음'}
    if sev in ('urgent','action'):
        return {'area':area,'severity':sev,'recommendation':rec,'class':'approval_required','reason':'전략/조직 동작에 영향 가능성이 있어 승인 필요'}
    return {'area':area,'severity':sev,'recommendation':rec,'class':'manual_review','reason':'자동 적용 규칙 없음'}


def run(cmd, timeout=120):
    try:
        cp=subprocess.run(cmd, cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        return {'cmd':cmd,'returncode':cp.returncode,'stdout_tail':cp.stdout[-1200:],'stderr_tail':cp.stderr[-1200:]}
    except Exception as e:
        return {'cmd':cmd,'returncode':-1,'error':str(e)}


def auto_apply_classification(c):
    if c.get('action') == 'run_disclosure_impact_for_symbols':
        results=[]
        for sym in (c.get('symbols') or [])[:12]:
            results.append(run(['tools/agents/disclosure_impact_agent.py','--symbol',sym,'--limit','20','--output',f'/tmp/disclosure_impact_{sym.replace(".","_")}_latest.json'], timeout=90))
        return {'action':c.get('action'),'symbols':(c.get('symbols') or [])[:12],'results':results,'rollback':'delete rows from disclosure_impact_assessments for affected rcept_no if needed'}
    return None


def build_patch_proposal(c):
    if c.get('action') == 'fix_integrity_false_warning':
        return {'area':c['area'],'title':'Fix stale monitor.js cache integrity warning','risk':'low','proposal':'Change paper_trader_integrity_agent to validate that monitor.html references an existing monitor.js cache version, not a hard-coded old patch token.','files':['tools/agents/paper_trader_integrity_agent.py']}
    if c.get('action') == 'aggregate_critic_bottlenecks':
        return {'area':c['area'],'title':'Aggregate Recommendation Critic bottlenecks','risk':'medium','proposal':'Add critic issue aggregation by issue text/code to recommendation_funnel/org_evaluator and promote the top repeated blocker to next_actions.','files':['tools/agents/recommendation_funnel_agent.py','tools/agents/org_evaluator_agent.py']}
    if c.get('action') == 'expand_symbol_edge_validation_samples':
        return {'area':c['area'],'title':'Expand symbol-edge validation samples for dominant critic bottleneck','risk':'low','proposal':'Keep critic/committee thresholds strict, but route batch/replay capacity toward current recommendation symbols and under-sampled symbol-strategy pairs until the dominant critic blocker (sample<10 / no positive symbol edge) is reduced. Do not promote to trade eligibility from this proposal alone; require paper outcomes first.','files':['tools/agents/research_pipeline_agent.py','tools/agents/current_recommendation_validation_worker.py','tools/agents/recommendation_auditor.py','tools/agents/recommendation_agent.py'],'evidence':{'dominant_critic_issue':c.get('dominant_critic_issue'),'critic_high':c.get('critic_high'),'final_recommendations':c.get('final_recommendations'),'recent_repeat_count':c.get('recent_repeat_count')}}
    if c.get('action') == 'repair_fund_recommendation_overlay':
        return {'area':c['area'],'title':'Repair Fund Recommendation Overlay','risk':'low','proposal':'Ensure fund_consensus style/symbol evidence is present in recommendation validation_basis and visible in the recommendation/fund UI.','files':['tools/agents/recommendation_agent.py','static/monitor.js'],'evidence':{'recent_repeat_count':c.get('recent_repeat_count')}}
    if c.get('action') == 'fund_champion_bottleneck_review':
        return {'area':c['area'],'title':'Review fund champion bottleneck instead of observing indefinitely','risk':'low','proposal':'Keep champion criteria strict, but emit a concrete review task when many paper funds run for repeated cycles with zero champions: inspect replay sample age, candidate-to-champion thresholds, and whether capped fund style evidence is still useful for recommendations.','files':['tools/agents/paper_fund_historical_replay_agent.py','tools/agents/fund_consensus_agent.py','tools/agents/org_evaluator_agent.py'],'evidence':{'fund_count':c.get('fund_count'),'candidate_count':c.get('candidate_count'),'champion_count':c.get('champion_count'),'recent_repeat_count':c.get('recent_repeat_count')}}
    if c.get('action') == 'add_target_return_adjustment_evaluator':
        return {'area':c['area'],'title':'Add Target Return Adjustment Evaluator','risk':'medium','proposal':'Add/keep a proposal-only meta evaluator that compares target-return parameter arms and recommends candidate adjustments only after enough paper outcome samples.','files':['tools/agents/target_return_adjustment_evaluator_agent.py','tools/agents/research_pipeline_agent.py','tools/agents/org_evaluator_agent.py'],'evidence':{'adjustment_pct_points':c.get('adjustment_pct_points'),'recent_repeat_count':c.get('recent_repeat_count')}}
    if c.get('action') == 'downgrade_constant_gate_to_aggregate_signal':
        return {'area':c['area'],'title':'Downgrade constant gate to aggregate signal','risk':'medium','proposal':'When a risk/critic gate assigns the same decision to >=95% of candidates, stop treating that decision as an item-level blocker/warning. Preserve it as an aggregate bottleneck, and use validation_priority/chase_risk/bucket fields for item-level differentiation.','files':['tools/agents/recommendation_agent.py','tools/agents/org_evaluator_agent.py'],'evidence':{'gate':c.get('gate'),'dominant_ratio':c.get('dominant_ratio'),'total':c.get('total'),'recent_repeat_count':c.get('recent_repeat_count')}}
    if c.get('action') == 'prioritize_validation_capacity_over_new_generation':
        return {'area':c['area'],'title':'Prioritize validation capacity over new generation','risk':'low','proposal':'While validation coverage remains below 25% for repeated runs, reduce or pause low-novelty strategy generation and allocate the batch budget to under-tested/current recommendation validation.','files':['tools/agents/research_pipeline_agent.py','configs/research_pipeline.yaml'],'evidence':{'coverage_pct':c.get('coverage_pct'),'recent_repeat_count':c.get('recent_repeat_count')}}
    if c.get('action') == 'tighten_research_repeat_suppression':
        return {'area':c['area'],'title':'Tighten autonomous research repeat suppression','risk':'low','proposal':'When ledger repeats exceed deduped repeats but suppressed_repeat_count stays zero, make the hypothesis/planner/runner loop skip near-duplicate experiments or require a measurable delta before rerunning similar plans.','files':['tools/agents/research_hypothesis_agent.py','tools/agents/research_experiment_ledger_agent.py','tools/agents/experiment_planner_agent.py','tools/agents/experiment_runner_agent.py'],'evidence':{'ledger_repeat_count':c.get('ledger_repeat_count'),'ledger_deduped_repeat_count':c.get('ledger_deduped_repeat_count'),'recent_repeat_count':c.get('recent_repeat_count')}}
    if c.get('action') == 'surface_stalled_lifecycle_next_action':
        gates=c.get('blocking_gates') or []
        gate_names=[g.get('gate') for g in gates if g.get('gate')]
        return {'area':c['area'],'title':'Surface stalled lifecycle as an actionable bottleneck','risk':'low','proposal':'If lifecycle state remains unchanged for many cycles, show the concrete blocking gates (validation coverage, audit quality, committee zero approval, or no qualified active promotions) in guardian next_actions/dashboard instead of reporting stability only.','files':['tools/agents/org_improvement_guardian_agent.py','static/monitor.js'],'evidence':{'completed':c.get('completed'),'recent_repeat_count':c.get('recent_repeat_count'),'primary_blocking_gate':c.get('primary_blocking_gate'),'blocking_gates':gates,'blocking_gate_names':gate_names}}
    if c.get('action') == 'committee_bottleneck_feedback_loop':
        return {'area':c['area'],'title':'Feed committee bottlenecks back into validation tasks','risk':'low','proposal':'When committee support/approvals stay at zero, aggregate the top reject reasons and create validation priorities for missing evidence instead of merely observing committee output. Do not weaken approval criteria.','files':['tools/agents/investment_committee_agent.py','tools/agents/org_evaluator_agent.py','tools/agents/current_recommendation_validation_worker.py','tools/agents/recommendation_auditor.py'],'evidence':{'item_count':c.get('item_count'),'support_count':c.get('support_count'),'approved_count':c.get('approved_count'),'trade_eligible_count':c.get('trade_eligible_count'),'recent_repeat_count':c.get('recent_repeat_count')}}
    if c.get('action') == 'target_return_arm_sample_backlog':
        return {'area':c['area'],'title':'Expose target-return arm sample backlog','risk':'low','proposal':'Keep target-return adjustment gated, but surface per-arm sample counts and next required outcomes when one adjustment is applied to all recommendations for repeated cycles.','files':['tools/agents/target_return_adjustment_evaluator_agent.py','tools/agents/org_evaluator_agent.py','static/monitor.js'],'evidence':{'adjustment_pct_points':c.get('adjustment_pct_points'),'applied_count':c.get('applied_count'),'total':c.get('total'),'recent_repeat_count':c.get('recent_repeat_count')}}
    return {'area':c.get('area'),'title':'Manual improvement proposal','risk':'medium','proposal':c.get('recommendation'),'files':[]}

def ensure_executable(path: Path):
    before=None; after=None; changed=False
    if path.exists():
        before=oct(path.stat().st_mode & 0o777)
        mode=path.stat().st_mode
        desired=mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        if desired != mode:
            path.chmod(desired); changed=True
        after=oct(path.stat().st_mode & 0o777)
    return {'path':str(path),'exists':path.exists(),'before_mode':before,'after_mode':after,'changed':changed,'rollback':f'chmod {before} {path}' if before else None}


def append_history(packet):
    rows=[]
    if HISTORY.exists():
        try: rows=json.loads(HISTORY.read_text(encoding='utf-8'))
        except Exception: rows=[]
    rows.append(packet)
    HISTORY.write_text(json.dumps(rows[-300:],ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    org=load_json('/tmp/research_org_evaluation_latest.json')
    pipeline=load_json('/tmp/research_pipeline_latest.json')
    committee=load_json('/tmp/investment_committee_latest.json')
    findings=org.get('findings') or []
    classifications=[classify_finding(f) for f in findings]
    auto_checks=[]
    # Low-risk self-healing: executable bit for scheduled cron wrapper. This is reversible and required for scheduled operation.
    for path in SAFE_FILE_EXECUTABLES:
        auto_checks.append(ensure_executable(path))
    auto_actions=[]
    for c in classifications:
        if c.get('class') == 'auto_apply_low_risk':
            res=auto_apply_classification(c)
            if res: auto_actions.append(res)
    patch_proposals=[build_patch_proposal(c) for c in classifications if c.get('class') == 'patch_proposal']
    if patch_proposals:
        PROPOSALS.write_text(json.dumps({'run_at':datetime.now(timezone.utc).isoformat(),'items':patch_proposals},ensure_ascii=False,indent=2),encoding='utf-8')
    applied=[x for x in auto_checks if x.get('changed')] + auto_actions
    approval_required=[x for x in classifications if x['class']=='approval_required']
    lifecycle_gates=lifecycle_blocking_gates(org,pipeline,committee)
    packet={
        'run_at':datetime.now(timezone.utc).isoformat(),
        'mode':'org_improvement_guardian',
        'real_trading':False,
        'policy':{
            'auto_apply_scope':'low-risk reversible maintenance only',
            'approval_required':'strategy thresholds, evaluator add/remove, pipeline topology, external services, destructive changes',
            'rollback_recorded':True,
        },
        'inputs':{
            'org_verdict':org.get('verdict'),
            'org_health_score':org.get('health_score'),
            'pipeline_status':pipeline.get('status'),
            'committee_summary':committee.get('summary'),
        },
        'classified_findings':classifications,
        'auto_checks':auto_checks,
        'auto_applied':applied,
        'patch_proposals':patch_proposals,
        'approval_required':approval_required,
        'lifecycle_bottlenecks':lifecycle_gates,
        'summary':{
            'finding_count':len(findings),
            'auto_applied_count':len(applied),
            'patch_proposal_count':len(patch_proposals),
            'approval_required_count':len(approval_required),
            'observe_count':sum(1 for x in classifications if x['class']=='observe'),
        }
    }
    warnings=[f"approval_required:{x['area']}" for x in approval_required] + [f"patch_proposal:{x['area']}" for x in patch_proposals]
    next_actions=[]
    if lifecycle_gates:
        next_actions.append('Lifecycle bottleneck gates: ' + ', '.join(g.get('gate') for g in lifecycle_gates[:4] if g.get('gate')))
    if patch_proposals: next_actions.append('Review generated patch_proposals; repeated/constant findings should not remain observe-only.')
    if approval_required: next_actions.append('Review approval_required items before structural changes.')
    attach_contract(packet,'org_improvement_guardian',status='ok',outputs={'auto_applied_count':len(applied),'patch_proposal_count':len(patch_proposals),'approval_required_count':len(approval_required)},metrics=packet['summary'],warnings=warnings,next_actions=next_actions)
    OUT.write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    append_history(packet)
    print(json.dumps(packet,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
