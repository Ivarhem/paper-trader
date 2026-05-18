#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast, json, re, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.database import init_db, validation_coverage, list_strategy_registry, latest_research_org_report, save_research_org_report
from tools.agents.lib.agent_contract import attach_contract


def load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {}


def severity(level: str) -> int:
    return {'info': 1, 'watch': 2, 'action': 3, 'urgent': 4}.get(level, 0)


def iso_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except Exception:
        return None


def add(items: list[dict], level: str, area: str, finding: str, recommendation: str, metric: dict | None = None) -> None:
    items.append({'severity': level, 'area': area, 'finding': finding, 'recommendation': recommendation, 'metric': metric or {}})


def propose_agent(proposals: list[dict], name: str, trigger: str, mission: str, first_version: str, priority: str = 'medium', inputs: list[str] | None = None, outputs: list[str] | None = None) -> None:
    proposals.append({
        'name': name,
        'priority': priority,
        'trigger': trigger,
        'mission': mission,
        'first_version': first_version,
        'inputs': inputs or [],
        'outputs': outputs or [],
        'status': 'proposed',
    })


def agent_script_exists(script_name: str) -> bool:
    return (ROOT / 'tools' / 'agents' / script_name).exists()


def latest_output_exists(path: str) -> bool:
    p = Path(path)
    return p.exists() and p.stat().st_size > 0




def pipeline_declared_steps() -> list[str]:
    src = ROOT / 'tools' / 'agents' / 'research_pipeline_agent.py'
    if not src.exists():
        return []
    try:
        return re.findall(r"add\('([^']+)'", src.read_text(encoding='utf-8'))
    except Exception:
        return []


def pipeline_role_summary() -> dict:
    src = ROOT / 'tools' / 'agents' / 'research_pipeline_agent.py'
    if not src.exists():
        return {}
    try:
        tree = ast.parse(src.read_text(encoding='utf-8'))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, 'id', '') == 'AGENT_ROLE_SUMMARY' for t in node.targets):
                return ast.literal_eval(node.value)
    except Exception:
        return {}
    return {}


def pipeline_config_agents() -> set[str]:
    cfg = ROOT / 'configs' / 'research_pipeline.yaml'
    if not cfg.exists():
        return set()
    try:
        data = json.loads(cfg.read_text(encoding='utf-8'))
        return set((data.get('agents') or {}).keys())
    except Exception:
        return set()


def scheduled_scripts() -> set[str]:
    src = ROOT / 'tools' / 'agents' / 'research_pipeline_agent.py'
    if not src.exists():
        return set()
    try:
        return set(re.findall(r"'tools/agents/([^']+\.py)'", src.read_text(encoding='utf-8')))
    except Exception:
        return set()




def toolbox_manifest() -> dict:
    cfg = ROOT / 'configs' / 'research_agents.yaml'
    if not cfg.exists():
        return {}
    groups = {}
    current = None
    in_toolbox = False
    for line in cfg.read_text(encoding='utf-8').splitlines():
        if line.strip() == 'toolbox_agents:':
            in_toolbox = True
            current = None
            continue
        if in_toolbox and line and not line.startswith('  '):
            break
        if not in_toolbox:
            continue
        if re.match(r'  [A-Za-z0-9_]+:', line):
            current = line.strip().rstrip(':')
            groups[current] = []
        elif current and line.strip().startswith('- '):
            groups[current].append(line.strip()[2:])
    return groups

def structural_org_audit() -> dict:
    steps = pipeline_declared_steps()
    roles = pipeline_role_summary()
    cfg_agents = pipeline_config_agents()
    scripts = {p.name for p in (ROOT / 'tools' / 'agents').glob('*.py')}
    scheduled = scheduled_scripts()
    toolbox = sorted(scripts - scheduled - {'research_pipeline_agent.py'})
    toolbox_groups = toolbox_manifest()
    classified_toolbox = sorted({x for arr in toolbox_groups.values() for x in arr})
    unclassified_toolbox = sorted(set(toolbox) - set(classified_toolbox))
    missing_roles = sorted(set(steps) - set(roles))
    missing_config = sorted(set(steps) - cfg_agents)
    duplicate_scripts = sorted([name for name in set(steps) if steps.count(name) > 1])
    pipeline_text = ''
    try:
        pipeline_text = (ROOT / 'tools' / 'agents' / 'research_pipeline_agent.py').read_text(encoding='utf-8')
    except Exception:
        pipeline_text = ''
    scheduled_set = set(steps)
    status_writers = ['strategy_lifecycle'] if 'strategy_lifecycle' in scheduled_set else []
    if 'active_strategy_balancer' in scheduled_set and '--apply-promotions' in pipeline_text:
        status_writers.append('active_strategy_balancer')
    if 'strategy_tail_risk_filter' in scheduled_set and '--apply-status' in pipeline_text:
        status_writers.append('strategy_tail_risk_filter')
    # Recommendation governance target: recommendation_agent creates the base row;
    # risk/regime/critic agents add named subdocuments; investment_committee is the
    # only scheduled final bucket/trade_eligible writer.
    final_recommendation_writers = ['investment_committee'] if 'investment_committee' in scheduled_set else []
    for agent, fn in {
        'recommendation_critic': 'recommendation_critic_agent.py',
        'portfolio_risk_manager': 'portfolio_risk_manager_agent.py',
        'market_regime_gate': 'market_regime_gate_agent.py',
    }.items():
        if agent not in scheduled_set:
            continue
        try:
            txt = (ROOT / 'tools' / 'agents' / fn).read_text(encoding='utf-8')
        except Exception:
            txt = ''
        if "['recommendation_bucket']" in txt or "['trade_eligible']" in txt:
            final_recommendation_writers.append(agent)
    authority_overlaps = {}
    if len(status_writers) > 1:
        authority_overlaps['strategy_status_writers_or_influencers'] = status_writers
    if len(final_recommendation_writers) > 1:
        authority_overlaps['recommendation_mutators'] = final_recommendation_writers
    meta = [x for x in ['research_org_orchestrator', 'org_evaluator', 'org_improvement_guardian', 'paper_trader_integrity'] if x in scheduled_set]
    if len(meta) >= 2:
        authority_overlaps['meta_governance'] = meta
    authority_model = {
        'strategy_status_canonical_writer': status_writers[0] if status_writers else None,
        'strategy_status_noncanonical_apply_writers': status_writers[1:],
        'recommendation_base_writer': 'recommendation_agent' if 'recommendation_agent' in scheduled_set else None,
        'recommendation_final_writer': 'investment_committee' if 'investment_committee' in scheduled_set else None,
        'recommendation_overlay_agents': [x for x in ['recommendation_critic','portfolio_risk_manager','market_regime_gate'] if x in scheduled_set],
    }
    return {
        'scheduled_step_count': len(steps),
        'agent_script_count': len(scripts),
        'toolbox_agent_count': len(toolbox),
        'missing_role_summaries': missing_roles,
        'missing_pipeline_config': missing_config,
        'duplicate_step_names': duplicate_scripts,
        'toolbox_agents': toolbox[:40],
        'toolbox_groups': toolbox_groups,
        'unclassified_toolbox_agents': unclassified_toolbox,
        'authority_overlaps': authority_overlaps,
        'authority_model': authority_model,
        'target_layers': {
            'executive': ['research_org_orchestrator', 'org_evaluator', 'org_improvement_guardian'],
            'data_evidence': ['universe_discovery', 'daily_price_refresh', 'disclosure_impact', 'data_quality', 'market_context'],
            'strategy_research': ['strategy_generator', 'simulation_validation_worker', 'strategy_lifecycle', 'active_strategy_balancer', 'strategy_tail_risk_filter', 'strategy_success_optimizer'],
            'recommendation_decision': ['recommendation_agent', 'recommendation_critic', 'portfolio_risk_manager', 'market_regime_gate', 'investment_committee'],
            'feedback_learning': ['current_recommendation_validation', 'recommendation_outcome_tracker', 'recommendation_funnel', 'recommendation_calibration', 'recommendation_audit'],
        },
    }

def evaluate() -> dict:
    init_db()
    coverage = validation_coverage()
    strategies = list_strategy_registry()
    orchestrator = latest_research_org_report('orchestrator_run') or {}
    pipeline = load_json('/tmp/research_pipeline_latest.json')
    pipeline_steps = pipeline.get('steps') or []
    org = load_json('/tmp/stock_research_org_latest.json')
    committee = load_json('/tmp/investment_committee_latest.json')
    committee_weights = load_json('/tmp/investment_committee_weights.json')
    recommendations = load_json('/tmp/recommendations_latest.json')
    funnel = load_json('/tmp/recommendation_funnel_latest.json')
    regime_gate = load_json('/tmp/market_regime_gate_latest.json')
    critic_latest = load_json('/tmp/recommendation_critic_latest.json')
    disclosure_impact = load_json('/tmp/disclosure_impact_latest.json')
    integrity = load_json('/tmp/paper_trader_integrity_latest.json')
    fund_registry = load_json('/tmp/fund_registry_latest.json')
    fund_performance = load_json('/tmp/fund_performance_evaluator_latest.json')
    fund_risk = load_json('/tmp/fund_risk_guardian_latest.json')
    fund_consensus = load_json('/tmp/fund_consensus_latest.json')
    price_replay = load_json('/tmp/paper_fund_price_replay_latest.json')
    hypotheses_latest = load_json('/tmp/research_hypotheses_latest.json')
    experiment_plan = load_json('/tmp/research_experiment_plan_latest.json')
    experiment_results = load_json('/tmp/research_experiment_results_latest.json')
    evidence_judge = load_json('/tmp/research_evidence_judge_latest.json')
    experiment_ledger = load_json('/tmp/research_experiment_ledger_latest.json')
    target_return_eval = load_json('/tmp/target_return_adjustment_evaluator_latest.json')
    current_validation = load_json('/tmp/current_recommendation_validation_latest.json')
    findings: list[dict] = []
    agent_proposals: list[dict] = []
    structural_audit = structural_org_audit()
    if structural_audit.get('missing_role_summaries') or structural_audit.get('missing_pipeline_config'):
        add(findings, 'action', 'org_structure', '일부 scheduled agent의 역할 설명 또는 pipeline config가 누락되어 조직 현황이 불완전합니다.', '모든 scheduled step에 role summary와 timeout/failure policy를 강제하고, 누락 시 smoke/integrity warning으로 올립니다.', {'missing_role_summaries': structural_audit.get('missing_role_summaries'), 'missing_pipeline_config': structural_audit.get('missing_pipeline_config')})
    overlaps = structural_audit.get('authority_overlaps') or {}
    if overlaps.get('strategy_status_writers_or_influencers'):
        add(findings, 'action', 'org_structure', '전략 상태 관리 권한이 lifecycle/balancer/tail-risk/optimizer에 분산되어 있어 demote/promote churn과 책임 불명이 발생할 수 있습니다.', 'strategy_lifecycle을 canonical status writer로 두고, balancer/tail-risk/success-optimizer는 proposal/tier/flag 산출자로 낮춥니다.', {'overlap_group': 'strategy_status', 'agents': overlaps.get('strategy_status_writers_or_influencers')})
    if overlaps.get('recommendation_mutators'):
        add(findings, 'action', 'org_structure', '추천 산출물이 여러 gate에서 순차 overwrite되어 최종 판단 책임과 변경 이유가 흐려질 수 있습니다.', '추천 agent는 base row만 생성하고 critic/risk/regime/committee는 named subdocument를 쓰며, committee가 final bucket을 집계하도록 구조화합니다.', {'overlap_group': 'recommendation_mutation', 'agents': overlaps.get('recommendation_mutators')})
    if structural_audit.get('unclassified_toolbox_agents'):
        add(findings, 'watch', 'org_structure', '일부 toolbox/manual agent가 분류되지 않아 scheduled 조직과 혼동될 수 있습니다.', 'configs/research_agents.yaml의 toolbox_agents에 Manual/Import/Experimental/Legacy 분류를 유지합니다.', {'unclassified_toolbox_agents': structural_audit.get('unclassified_toolbox_agents')})

    # Fund-first recommendation organization loop: fund is not the product; it is the
    # recommendation quality engine. Track whether registry/performance/risk/consensus
    # are alive and whether fund evidence actually reaches recommendation rows.
    fund_summary = fund_performance.get('summary') or {}
    fund_cons_summary = fund_consensus.get('summary') or {}
    fund_risk_summary = fund_risk.get('summary') or {}
    fund_count = int(fund_summary.get('fund_count') or fund_registry.get('fund_count') or 0)
    champion_count = int(fund_summary.get('champion_count') or 0)
    candidate_count = int(fund_summary.get('candidate_count') or 0)
    fund_style_recs = 0
    for r in (recommendations.get('items') or []):
        if ((r.get('validation_basis') or {}).get('fund_style_consensus_boost_total') or 0) > 0:
            fund_style_recs += 1
    fund_metric = {
        'fund_count': fund_count,
        'champion_count': champion_count,
        'candidate_count': candidate_count,
        'risk_findings': fund_risk_summary.get('finding_count'),
        'symbol_consensus_count': fund_cons_summary.get('symbol_consensus_count'),
        'top_styles': fund_cons_summary.get('top_styles'),
        'price_replay_trading_days': price_replay.get('trading_days'),
        'recommendations_with_fund_style_boost': fund_style_recs,
        'recommendation_count': len(recommendations.get('items') or []),
    }
    if not (fund_registry and fund_performance and fund_risk and fund_consensus):
        add(findings, 'action', 'fund_org_loop', 'Fund 기반 추천 품질 조직의 핵심 산출물 일부가 없습니다.', 'fund_registry → fund_performance_evaluator → fund_risk_guardian → fund_consensus 순서를 pipeline에서 유지하고 누락 시 degraded로 표시합니다.', fund_metric)
    elif fund_count < 20 or champion_count == 0:
        add(findings, 'watch', 'fund_org_loop', 'Fund league는 실행 중이지만 추천 품질 엔진으로 삼기엔 champion/candidate 풀이 부족합니다.', 'price replay/live paper fund 샘플을 더 쌓고, champion 기준은 유지하되 추천 overlay 영향은 capped 상태로 둡니다.', fund_metric)
    elif fund_style_recs == 0:
        add(findings, 'action', 'fund_recommendation_link', '상위 fund 성향이 최신 추천 종목에 반영되지 않고 있습니다.', 'recommendation_agent의 fund_style_consensus overlay와 UI 표시를 확인하고, symbol consensus가 없을 때도 style consensus는 점수/근거에 남깁니다.', fund_metric)
    else:
        add(findings, 'info', 'fund_recommendation_link', 'Fund 성과 리그가 종목추천 근거로 연결되어 있습니다.', 'fund symbol consensus는 live holdings가 쌓일 때까지 style consensus 중심으로 유지하고, 사후성과로 boost cap을 조정합니다.', fund_metric)
    if (fund_cons_summary.get('symbol_consensus_count') or 0) == 0:
        add(findings, 'watch', 'fund_symbol_consensus', '상위 fund의 종목 단위 consensus가 아직 비어 있습니다.', 'live fund holdings를 fund_consensus가 더 잘 읽도록 holdings snapshot 저장/노출을 강화하면 추천 근거가 더 직관적입니다.', fund_metric)

    # Recommendation product priority: recommendation rows should be assembled
    # after the common universe and evidence engines run in the same cycle. When
    # org_evaluator runs inside the pipeline, /tmp/research_pipeline_latest.json
    # can still describe the previous cron run, so prefer declared source order
    # if the latest pipeline snapshot is older than recommendation outputs.
    pipe_ts_for_order = iso_ts(pipeline.get('run_at'))
    rec_ts_for_order = iso_ts(recommendations.get('run_at'))
    order_source = 'latest_pipeline_snapshot'
    if pipe_ts_for_order and rec_ts_for_order and pipe_ts_for_order < rec_ts_for_order:
        order_names = pipeline_declared_steps()
        order_source = 'declared_pipeline_source_stale_snapshot'
    else:
        order_names = [x.get('name') or x.get('agent') for x in pipeline_steps if isinstance(x, dict)]
    def pos(name):
        try: return order_names.index(name)
        except ValueError: return -1
    order_metric = {
        'source': order_source,
        'pipeline_run_at': pipeline.get('run_at'),
        'recommendations_run_at': recommendations.get('run_at'),
        'common_universe': pos('common_universe'),
        'fund_consensus': pos('fund_consensus'),
        'recommendation_market_context': pos('recommendation_market_context'),
        'recommendation_agent': pos('recommendation_agent'),
        'recommendation_agent_after_disclosure': pos('recommendation_agent_after_disclosure'),
    }
    if all(order_metric.get(k, -1) >= 0 for k in ['common_universe','fund_consensus','recommendation_market_context','recommendation_agent']):
        if not (order_metric['common_universe'] < order_metric['fund_consensus'] < order_metric['recommendation_market_context'] < order_metric['recommendation_agent']):
            add(findings, 'action', 'recommendation_evidence_flow', '추천 생성이 common universe/fund/market evidence보다 먼저 실행되어 최신 조직 evidence를 한 사이클 늦게 반영할 수 있습니다.', 'pipeline 순서를 common_universe → fund league/consensus → recommendation_market_context → recommendation_agent로 고정하고, fund/market/audit는 추천 row의 별도 subdocument로 유지합니다.', order_metric)
        else:
            add(findings, 'info', 'recommendation_evidence_flow', '추천 생성 전에 common universe와 fund/market evidence가 같은 사이클에서 준비됩니다.', '종목추천은 decision surface로 유지하고 fund/market/audit evidence는 보조 근거로 분리 표시합니다.', order_metric)

    cov = float(coverage.get('coverage_pct_estimate') or 0)
    pending = int(coverage.get('pending_results_estimate') or 0)
    completed = int(coverage.get('completed_results') or 0)
    strategy_count = int(coverage.get('strategy_count') or 0)
    active = [s for s in strategies if s.get('status') == 'active']
    repair_active = [s for s in strategies if s.get('status') in ('repair_active','validation_active')]
    effective_active = active + repair_active
    candidate = [s for s in strategies if s.get('status') in ('candidate', 'pending_validation')]
    watch = [s for s in strategies if s.get('status') in ('watch', 'probation')]
    retired = [s for s in strategies if s.get('status') == 'retired']
    under = coverage.get('under_tested') or []
    min_samples = min([int(x.get('candidate_samples') or 0) for x in under], default=0)

    if cov < 10 and pending > 50000:
        if agent_script_exists('validation_capacity_planner.py') and latest_output_exists('/tmp/validation_capacity_planner_latest.json'):
            add(findings, 'watch', 'validation_throughput', '검증 커버리지가 아직 낮고 백로그가 큽니다.', 'Validation Capacity Planner가 자동 적용 중입니다. 서버 부하와 백로그를 기준으로 batch-size를 조절합니다.', {'coverage_pct': cov, 'pending': pending})
        else:
            add(findings, 'action', 'validation_throughput', '검증 커버리지가 아직 낮고 백로그가 큽니다.', '현재처럼 저샘플 전략 우선 검증을 유지하되, 서버 여유가 있으면 orchestrator batch-size/cadence를 늘립니다.', {'coverage_pct': cov, 'pending': pending})
            propose_agent(agent_proposals, 'Validation Capacity Planner', 'coverage < 10% and large pending backlog', '검증 처리량, batch-size, cadence, 서버 부하를 관찰해 검증 속도를 자동 조절합니다.', '처음에는 최근 coverage 증가량과 pending 수를 기반으로 batch-size/cadence 권고만 생성합니다.', 'high', ['validation_coverage', 'orchestrator_reports'], ['capacity_recommendation'])
    elif cov < 30:
        add(findings, 'watch', 'validation_throughput', '검증 커버리지가 중간 단계입니다.', '검증 우선 모드가 적용되어 신규 전략 생성을 제한하고 기존 후보 샘플 축적을 우선합니다.', {'coverage_pct': cov, 'validation_first_mode': cov < 25, 'strategy_generation_limited_below_pct': 25})

    if strategy_count and len(candidate) / strategy_count > 0.7:
        add(findings, 'action', 'strategy_pipeline', '전략 후보 대부분이 candidate 상태에 머물러 있습니다.', 'Strategy Generator의 신규 후보 생성 속도를 제한하고, 후보당 최소 샘플 기준을 먼저 채우는 모드로 운영합니다.', {'candidate': len(candidate), 'strategy_count': strategy_count})
        if not agent_script_exists('strategy_novelty_pruner.py'):
            propose_agent(agent_proposals, 'Strategy Novelty Pruner', 'candidate ratio > 70%', '서로 거의 같은 grid 전략이나 낮은 차별성 후보를 묶고, 신규 전략 생성 전에 중복/노이즈를 줄입니다.', '전략 파라미터 유사도와 초기 샘플 성과를 기준으로 merge/hold 제안을 생성합니다.', 'high', ['strategy_registry', 'validation_summary'], ['prune_or_hold_recommendations'])

    if active and len(active) < max(3, strategy_count * 0.03):
        add(findings, 'watch', 'strategy_pipeline', 'active 전략 수가 전체 후보 대비 적습니다.', 'active 전환 기준이 너무 엄격한지 확인하되, 샘플 수가 부족한 동안 기준을 낮추지는 않습니다.', {'active': len(active), 'strategy_count': strategy_count})

    auto_summary = {
        'hypothesis_count': (hypotheses_latest.get('summary') or {}).get('hypothesis_count'),
        'suppressed_repeat_count': (hypotheses_latest.get('summary') or {}).get('suppressed_repeat_count'),
        'plan_count': (experiment_plan.get('summary') or {}).get('plan_count'),
        'runner_error_count': (experiment_results.get('summary') or {}).get('error_count'),
        'judgment_count': (evidence_judge.get('summary') or {}).get('judgment_count'),
        'proposal_review_count': (evidence_judge.get('summary') or {}).get('proposal_review_count'),
        'ledger_repeat_count': (experiment_ledger.get('summary') or {}).get('repeat_count'),
        'ledger_delta_count': (experiment_ledger.get('summary') or {}).get('delta_count'),
        'ledger_deduped_repeat_count': (experiment_ledger.get('summary') or {}).get('deduped_repeat_count'),
    }
    if auto_summary.get('runner_error_count'):
        add(findings, 'action', 'autonomous_research', '자율 실험 runner에서 실패가 발생했습니다.', '실패한 isolated experiment output을 확인하고 해당 plan을 retry_or_inspect로 보류합니다.', auto_summary)
    elif auto_summary.get('hypothesis_count') and auto_summary.get('plan_count') and auto_summary.get('judgment_count'):
        add(findings, 'info', 'autonomous_research', '자율 연구 루프가 가설→계획→실험→증거판정까지 정상 순환 중입니다.', '반복 억제와 delta ledger를 유지해 같은 실험을 무의미하게 반복하지 않도록 합니다.', auto_summary)
    if (auto_summary.get('ledger_repeat_count') or 0) > 0 and (auto_summary.get('ledger_delta_count') or 0) == 0:
        add(findings, 'watch', 'autonomous_research', '반복 실험은 감지됐지만 최근 delta가 없습니다.', '반복 가설은 suppress하고 coverage-gap 또는 새로운 blocker 기반 가설로 전환합니다.', auto_summary)

    if min_samples < 10 and under:
        # The monitor exposes the least-sampled strategies directly in the
        # validation panel, so this is now an observed backlog rather than an
        # approval-required structural gap.  Keep it visible until samples rise.
        add(findings, 'watch', 'coverage_balance', '일부 전략의 candidate sample이 아직 매우 낮습니다.', 'Simulation Validation Worker의 least-sampled 우선순위를 유지하고, monitor validation panel의 저샘플 전략 목록으로 추적합니다.', {'min_candidate_samples': min_samples, 'under_tested_count': len(under), 'monitor_under_tested_visible': True})

    pipeline_steps = pipeline.get('steps') or []
    # During a pipeline run, org_evaluator executes before research_pipeline_latest.json
    # is rewritten, so that file may still describe the previous cron run. Avoid
    # raising stale agent-health urgents when newer recommendation outputs already
    # exist in the current run.
    pipe_ts = iso_ts(pipeline.get('run_at'))
    rec_ts = iso_ts(recommendations.get('run_at'))
    if pipe_ts and rec_ts and pipe_ts < rec_ts:
        pipeline_steps = []
    roles = pipeline_steps or (org.get('roles') or [])
    errored = [r for r in roles if r.get('status') in ('error','failed','failed_required','failed_optional') or r.get('returncode') not in (None, 0)]
    skipped = [r for r in roles if r.get('status') == 'skipped']
    if errored:
        add(findings, 'urgent', 'agent_health', '일부 리서치 에이전트가 실패했습니다.', '실패한 역할의 stderr/stdout tail을 점검하고 다음 루프 전에 복구합니다.', {'errored_roles': [r.get('role') or r.get('agent') for r in errored]})
    if any((r.get('role') == 'Disclosure Analyst' or r.get('agent') in ('opendart_disclosure','opendart_financials')) and r.get('status') in ('error', 'skipped','failed','failed_required','failed_optional') for r in roles):
        if agent_script_exists('opendart_disclosure_agent.py') and latest_output_exists('/tmp/opendart_disclosures_latest.json'):
            add(findings, 'info', 'risk_context', '공시/리스크 분석 에이전트가 자동 적용 중입니다.', 'OpenDART active-kr 수집 결과가 universe와 추천 리스크에 반영됩니다.', {})
        else:
            add(findings, 'watch', 'risk_context', '공시/리스크 분석이 완전하지 않을 수 있습니다.', 'OpenDART API 키와 KRX 메타데이터 경로를 완성해 가격 검증에 이벤트 맥락을 더합니다.', {})
            propose_agent(agent_proposals, 'Disclosure Coverage Builder', 'disclosure agent incomplete', 'OpenDART/KRX 데이터 연결 상태를 감시하고, 종목별 공시 커버리지 결손을 메우는 작업을 제안합니다.', 'API 키/메타데이터 누락 여부와 최근 공시 fetch 성공률을 보고 setup checklist를 출력합니다.', 'medium', ['disclosure_events', 'opendart_agent_output'], ['coverage_gap_report'])
    elif not roles:
        # org_evaluator can run before research_pipeline_latest.json is rewritten in the same cron cycle.
        # If current recommendation/funnel/orchestrator artifacts exist, this is a stale snapshot artifact.
        if recommendations.get('run_at') or funnel.get('run_at') or orchestrator.get('run_at'):
            add(findings, 'info', 'agent_health', 'unified research pipeline 산출물이 같은 사이클에서 확인됩니다.', 'pipeline snapshot 갱신 전 evaluator 실행 타이밍으로 인한 stale roles 상태이며 조치 대상은 아닙니다.', {'recommendations_run_at': recommendations.get('run_at'), 'funnel_run_at': funnel.get('run_at'), 'orchestrator_run_at': orchestrator.get('run_at')})
        else:
            add(findings, 'watch', 'agent_health', '최근 unified research pipeline 실행 결과가 없습니다.', 'research_pipeline_agent와 evaluator의 실행 순서를 하나의 cron으로 유지합니다.', {})

    last_orch = orchestrator.get('payload') or {}
    status_changes = last_orch.get('status_changes') or []
    if not status_changes and completed > 5000:
        add(findings, 'info', 'lifecycle_stability', '최근 루프에서 전략 상태 변화는 없었습니다.', '상태 변화가 없더라도 coverage 증가와 under-tested 해소가 진행 중이면 정상입니다.', {'completed': completed})

    if active and cov >= 5 and not (agent_script_exists('regime_segmentation_agent.py') and latest_output_exists('/tmp/regime_segmentation_latest.json')):
        propose_agent(agent_proposals, 'Regime Segmentation Analyst', 'active strategies exist but market-regime sensitivity is unknown', '상승장/하락장/고변동/저변동 구간별로 전략 성과를 분해해 어느 환경에서만 통하는지 찾습니다.', 'cutoff 기간을 연도·변동성·벤치마크 추세로 태깅하고 active 전략 성과를 구간별 요약합니다.', 'medium', ['validation_results', 'price_bars'], ['regime_performance_summary'])


    committee_perf = committee.get('weight_performance') or committee_weights.get('performance') or {}
    committee_summary = committee.get('summary') or {}
    if committee and committee_summary.get('support_count', 0) == 0 and committee_summary.get('research_support_count', 0) == 0:
        add(findings, 'watch', 'investment_committee', 'Adaptive Committee가 현재 모든 후보를 지지하지 않고 있습니다.', 'research_support도 없는 경우만 위원회 병목으로 봅니다. 검증 품질 플래그와 evaluator weight 추이를 확인합니다.', committee_summary)
    elif committee and committee_summary.get('support_count', 0) == 0 and committee_summary.get('research_support_count', 0) > 0:
        add(findings, 'info', 'investment_committee', '위원회가 trade 승인은 보류했지만 research_support 후보를 분리하고 있습니다.', 'Risk Gate 검증대기 후보의 validation_priority와 사후성과를 추적합니다.', committee_summary)
    if committee_perf.get('mode') == 'audit_proxy':
        low_hit = [k for k,v in (committee_perf.get('evaluators') or {}).items() if v.get('hit_rate') is not None and v.get('hit_rate') < 0.35]
        if low_hit:
            add(findings, 'watch', 'committee_weights', '일부 평가성향의 audit proxy 적중률이 낮습니다: ' + ', '.join(low_hit), '해당 evaluator의 룰을 수정하거나 weight 하향을 유지합니다.', {'low_hit_evaluators': low_hit})

    # Structural/data-quality gates: catch the kinds of issues humans keep finding.
    rec_items = recommendations.get('items') or []
    if rec_items:
        assessed = [r for r in rec_items if ((r.get('disclosure_risk') or {}).get('impact_assessed_count') or 0) > 0]
        risky_title_only = [r for r in rec_items if ((r.get('disclosure_risk') or {}).get('benign_medium') or 0) and ((r.get('disclosure_risk') or {}).get('impact_assessed_count') or 0) == 0]
        if risky_title_only:
            add(findings, 'action', 'disclosure_quality', '추천 후보 일부가 공시 본문/영향 평가 없이 제목 기반 리스크만 사용 중입니다.', 'Disclosure Impact Agent를 해당 종목에 우선 실행하고, 제목 기반 quarantine/감점은 fallback으로만 사용합니다.', {'title_only_symbols': [r.get('symbol') for r in risky_title_only[:10]], 'assessed_recommendations': len(assessed), 'total_recommendations': len(rec_items)})
        rejected = sum(1 for r in rec_items if r.get('recommendation_bucket') == 'rejected')
        if rejected == len(rec_items):
            fsum_for_gate = funnel.get('summary') or {}
            dom_for_gate = fsum_for_gate.get('dominant_critic_issue') or {}
            rec_text = (f"rejected-only 상태입니다. 최대 reject/critic 병목은 '{dom_for_gate.get('issue')}' ({dom_for_gate.get('count')}건)이므로 해당 병목을 상위 관찰/개선 대상으로 추적합니다." if dom_for_gate else 'rejected-only 상태에서는 상위 관찰/개선 후보를 별도 섹션으로 표시하고, reject 사유를 aggregate해 가장 큰 병목을 자동 개선 대상으로 올립니다.')
            add(findings, 'action', 'recommendation_gate', '추천 20개가 모두 rejected라 UI상 추천현황이 의미 있는 우선순위를 제공하지 못합니다.', rec_text, {'rejected': rejected, 'total': len(rec_items), 'dominant_critic_issue': dom_for_gate})
        target_adjustments = [r.get('target_return_adjustment') or {} for r in rec_items]
        fixed_adj_values = sorted({x.get('adjustment_pct_points') for x in target_adjustments if x.get('adjustment_pct_points') is not None})
        with_original_targets = [r for r in rec_items if r.get('original_target_1') is not None and r.get('target_1') is not None]
        target_delta_values = sorted({round(float(r.get('upside_1_pct') or 0) - float(r.get('original_upside_1_pct') or 0), 2) for r in with_original_targets if r.get('upside_1_pct') is not None and r.get('original_upside_1_pct') is not None})
        if fixed_adj_values:
            arm_backlog = target_return_eval.get('arm_sample_backlog') or ((target_return_eval.get('summary') or {}).get('arm_sample_backlog') or [])
            parameter_arms = target_return_eval.get('parameter_arms') or []
            metric={'adjustment_pct_points': fixed_adj_values, 'applied_count': len([x for x in target_adjustments if x.get('adjustment_pct_points') is not None]), 'total': len(rec_items), 'target_delta_values': target_delta_values[:8], 'has_dedicated_evaluator': latest_output_exists('/tmp/target_return_adjustment_evaluator_latest.json'), 'policy': (target_adjustments[0] or {}).get('policy'), 'arm_sample_backlog_visible': bool(arm_backlog), 'arm_sample_backlog': arm_backlog[:8], 'parameter_arms': parameter_arms[:8], 'meta_decision': target_return_eval.get('meta_decision')}
            if not metric['has_dedicated_evaluator']:
                add(findings, 'watch', 'target_return_adjustment', '목표수익률 기본 보정값이 전 추천에 적용 중이지만 전용 사후성과 evaluator가 없습니다.', '현재 보정치는 추천 산출물에는 정상 반영되지만, 조직메타는 이를 전략 파라미터 arm으로 추적해야 합니다. target_return_adjustment_evaluator가 보정치별 paper 성과를 비교하고 수익률 개선 후보를 제안하도록 합니다.', metric)
            else:
                add(findings, 'info', 'target_return_adjustment', '목표수익률 보정 파라미터가 전용 evaluator로 추적 중입니다.', '보정치별 parameter arm의 paper 성과, hit rate, proxy realized return, risk 변화를 기준으로 후보 승격/유지 제안을 확인합니다.', metric)
    fsum = funnel.get('summary') or {}
    if fsum.get('critic_high', 0) >= max(5, int((fsum.get('final_recommendations') or 0) * 0.5)):
        dom = fsum.get('dominant_critic_issue') or {}
        if dom:
            issue_text = str(dom.get('issue') or '')
            selection_policy = str((current_validation.get('priority_meta') or {}).get('selection_policy') or '')
            routed_tasks = [t.get('task') for r in ((current_validation.get('priority_meta') or {}).get('under_sampled_recommendations') or []) for t in (r.get('critic_tasks') or []) if isinstance(t, dict)]
            positive_excess_routed = ('positive_excess' in selection_policy or 'retest_positive_excess_or_replace_logic' in routed_tasks)
            if 'active 전략 평균 초과수익' in issue_text:
                rec_msg = f"최대 병목은 '{dom.get('issue')}' ({dom.get('count')}건)입니다. 낮은 평균 초과수익 전략은 positive-excess 재검증/교체 후보로 검증 우선순위에 연결해 추적합니다."
                sev = 'watch' if positive_excess_routed else 'action'
            elif '샘플' in issue_text or 'edge' in issue_text:
                rec_msg = f"최대 병목은 '{dom.get('issue')}' ({dom.get('count')}건)입니다. 해당 병목을 줄이는 검증 샘플 확충/종목 edge 보강을 우선합니다."
                sev = 'action'
            else:
                rec_msg = f"최대 병목은 '{dom.get('issue')}' ({dom.get('count')}건)입니다. 해당 품질 병목을 줄이는 전략 재선별/검증 우선순위 조정을 진행합니다."
                sev = 'action'
            add(findings, sev, 'recommendation_quality', 'Recommendation Critic high 이슈가 과반입니다.', rec_msg, fsum)
        else:
            add(findings, 'action', 'recommendation_quality', 'Recommendation Critic high 이슈가 과반입니다.', 'critic high 사유를 유형별로 묶어 strategy/sample/disclosure/UI 중 가장 반복되는 문제를 다음 개선 작업으로 승격합니다.', fsum)
    dis_sum = disclosure_impact.get('summary') or {}
    if dis_sum and dis_sum.get('assessed', 0) < 50:
        add(findings, 'watch', 'disclosure_quality', '공시 영향 평가 커버리지가 낮습니다.', 'active/recommended 종목의 최근 공시부터 impact assessment를 선행 실행합니다.', dis_sum)
    if dis_sum and dis_sum.get('neutral', 0) and dis_sum.get('negative_medium', 0) == 0 and dis_sum.get('positive', 0) <= 1:
        add(findings, 'watch', 'disclosure_quality', '공시 영향 평가가 대부분 neutral로만 귀결되어 변별력이 낮을 수 있습니다.', '본문 fetch 비율을 높이고 금액/비율/목적 필드 파싱을 추가해 positive/negative severity 변별력을 검증합니다.', dis_sum)
    for w in (integrity.get('warnings') or []):
        if isinstance(w, dict) and w.get('name') == 'monitor_js_cache_busted_after_shadow_patch':
            add(findings, 'watch', 'ui_integrity', 'monitor.js 캐시 버전 검사가 과거 패치명에 고정되어 false warning을 만들고 있습니다.', 'Integrity check를 특정 패치명 문자열이 아니라 현재 기대 버전/존재 여부 기준으로 바꿉니다.', w)

    # Gate Effectiveness Audit: detect stale/no-discrimination gates before they become UI/decision noise.
    if rec_items:
        total=len(rec_items)
        def dominant(counter):
            if not counter: return None, 0, 0
            k,v=counter.most_common(1)[0]
            return k,v,round(v/total,3) if total else 0
        bucket_c=Counter(r.get('recommendation_bucket') for r in rec_items)
        action_c=Counter(r.get('action') for r in rec_items)
        blocker_c=Counter(b for r in rec_items for b in ((r.get('trade_gate') or {}).get('blockers') or []))
        caution_c=Counter(c for r in rec_items for c in ((r.get('trade_gate') or {}).get('cautions') or []))
        regime_c=Counter((r.get('regime_gate') or {}).get('decision') for r in rec_items)
        chase_c=Counter((r.get('regime_gate') or {}).get('chase_risk') for r in rec_items)
        risk_c=Counter(((r.get('investment_committee') or {}).get('synthesis') or {}).get('risk_gate',{}).get('decision') for r in rec_items)
        research_c=Counter(((r.get('investment_committee') or {}).get('synthesis') or {}).get('research_committee',{}).get('decision') for r in rec_items)
        crit_type_c=Counter(((r.get('critic') or {}).get('severity'), (r.get('critic') or {}).get('issue_type')) for r in rec_items)
        priority_c=Counter(r.get('validation_priority') for r in rec_items)
        gate_metrics={'bucket_counts':dict(bucket_c),'action_counts':dict(action_c),'blocker_counts':dict(blocker_c),'caution_counts':dict(caution_c),'regime_counts':dict(regime_c),'chase_risk_counts':dict(chase_c),'risk_gate_counts':dict(risk_c),'research_counts':dict(research_c),'critic_type_counts':{str(k):v for k,v in crit_type_c.items()},'validation_priority_counts':dict(priority_c)}
        # Stale universal blockers/cautions: these are usually low information unless explicitly expected.
        for label,counter,kind in [('blocker',blocker_c,'blocker'),('caution',caution_c,'caution')]:
            k,v,ratio=dominant(counter)
            if k and ratio >= 0.9 and k in ('base_action_not_buy_candidate',):
                add(findings, 'watch', 'gate_effectiveness', f'{kind} `{k}`가 후보 대부분({v}/{total})에 반복되어 변별력이 낮습니다.', '해당 신호는 차단 게이트가 아니라 validation task/priority 산정에 사용하고, UI에서는 aggregate 병목으로만 노출합니다.', {'gate':'trade_or_critic','kind':kind,'value':k,'count':v,'total':total,'dominant_ratio':ratio, **gate_metrics})
        # No discrimination in regime/critic/risk layers should produce a gate audit note,
        # not repeated item-level blockers.  Constant market/risk gates are intentionally
        # downgraded to aggregate context; item-level sorting comes from validation_priority,
        # recommendation_bucket, chase_risk, and concrete blockers.
        for gate_name,counter in [('market_regime_gate',regime_c),('risk_gate',risk_c)]:
            k,v,ratio=dominant(counter)
            if k and ratio >= 0.95 and len(counter) == 1:
                add(findings, 'info', 'gate_effectiveness', f'{gate_name} 1차 판정은 `{k}`로 동일해 aggregate 병목으로만 기록합니다.', '전원 동일 Gate 판정은 카드별 차단/경고로 반복하지 않고, 실제 우선순위는 validation_priority/bucket/chase_risk/구체 blocker로 판단합니다.', {'gate':gate_name,'decision':k,'count':v,'total':total,'dominant_ratio':ratio, 'downgraded_to_aggregate': True, **gate_metrics})
        # Positive check: if a previously stale gate now has useful secondary discrimination, record as info.
        if len(priority_c) >= 2 and (priority_c.get('high') or 0) > 0:
            add(findings, 'info', 'gate_effectiveness', 'Risk/Critic 게이트가 validation_priority로 후보 우선순위를 분리하고 있습니다.', 'high/medium/low 분포와 실제 사후성과를 추적해 priority 산정식을 조정합니다.', {'gate':'risk_critic_validation_priority','validation_priority_counts':dict(priority_c)})

    priority = sorted(findings, key=lambda x: severity(x['severity']), reverse=True)[:8]
    # Health score is an operator signal, not a raw count of notices. Cap repeated
    # watch-level penalties by area so one structural issue (e.g. gate effectiveness)
    # does not look like multiple independent failures.
    health_score = 100
    area_penalties: dict[str, int] = {}
    for f in findings:
        sev = f.get('severity')
        area = f.get('area') or 'general'
        if sev == 'urgent':
            penalty = 25
            cap = 50
        elif sev == 'action':
            penalty = 12
            cap = 24
        elif sev == 'watch':
            penalty = 6
            cap = 10
        elif sev == 'info':
            penalty = 0
            cap = 0
        else:
            penalty = 0
            cap = 0
        already = area_penalties.get(area, 0)
        applied = max(0, min(penalty, cap - already)) if cap else 0
        area_penalties[area] = already + applied
        health_score -= applied
    health_score = max(0, min(100, health_score))
    if health_score >= 80: verdict = 'healthy'
    elif health_score >= 60: verdict = 'needs_attention'
    else: verdict = 'needs_intervention'

    summary = f"Org Evaluator: {verdict}, score {health_score}/100, {len(findings)} findings; coverage {cov}%, active {len(active)}, candidates {len(candidate)}."
    return {
        'run_at': datetime.now(timezone.utc).isoformat(),
        'mode': 'research_org_meta_evaluation',
        'real_trading': False,
        'verdict': verdict,
        'health_score': health_score,
        'summary': summary,
        'score_penalties_by_area': area_penalties,
        'metrics': {
            'coverage': coverage,
            'status_counts': {'active': len(active), 'repair_active': len(repair_active), 'effective_active': len(effective_active), 'candidate': len(candidate), 'watch_probation': len(watch), 'retired': len(retired)},
            'pipeline_agents': len(pipeline_steps),
            'pipeline_failures': len(errored),
            'pipeline_skips': len(skipped),
            'committee_summary': committee.get('summary'),
            'committee_weights': committee_weights.get('weights'),
            'structural_audit': structural_audit,
            'autonomous_research': auto_summary,
            'fund_org': fund_metric,
        },
        'findings': priority,
        'all_findings': findings,
        'agent_proposals': sorted(agent_proposals, key=lambda x: {'high': 3, 'medium': 2, 'low': 1}.get(x.get('priority'), 0), reverse=True),
        'next_actions': [f['recommendation'] for f in priority if f['severity'] in ('urgent', 'action')][:5],
    }


def main():
    ap = argparse.ArgumentParser(description='Evaluate and improve the stock research agent organization')
    ap.add_argument('--output', default='/tmp/research_org_evaluation_latest.json')
    ap.add_argument('--save', action='store_true')
    args = ap.parse_args()
    payload = evaluate()
    attach_contract(payload, 'org_evaluator', status='ok' if payload.get('verdict') == 'healthy' else 'degraded', outputs={'verdict': payload.get('verdict'), 'health_score': payload.get('health_score'), 'finding_count': len(payload.get('all_findings') or [])}, metrics={'health_score': payload.get('health_score'), 'finding_count': len(payload.get('all_findings') or []), 'proposal_count': len(payload.get('agent_proposals') or [])}, warnings=[f.get('finding') for f in (payload.get('findings') or []) if f.get('severity') in ('urgent','action')], next_actions=payload.get('next_actions') or [])
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.save:
        payload['report_id'] = save_research_org_report('org_evaluation', payload['summary'], payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
