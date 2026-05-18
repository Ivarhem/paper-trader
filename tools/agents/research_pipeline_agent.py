#!/usr/bin/env python3
"""Lightweight deterministic multi-agent orchestrator.

Runs the paper_trader research organization as independent Python micro-agents.
Each step writes JSON contract output; this orchestrator records compact step
status for UI/cron review. Historical/paper research only — no real trading.

See configs/research_agents.yaml for the role manifest.
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.database import init_db, list_strategy_registry, validation_coverage, save_research_org_report, save_agent_run_artifact, save_latest_artifact
from tools.agents.lib.agent_contract import write_json_shared
from tools.agents.lib.pipeline_context_contracts import build_context_goal_artifacts, build_fund_org_summary_artifact

AGENT_ROLE_SUMMARY = {
    'pipeline_smoke_check': ('Pipeline Smoke Check', '조직 부팅 전 안전 점검'),
    'universe_discovery': ('Universe Discovery', '신규 종목 발굴/가격 수집'),
    'market_mover_seed': ('Market Mover Seed', '국내 금일 급등락 seed를 수집해 provisional shock 입력 제공'),
    'investor_flow_seed': ('Investor Flow Seed', 'Naver 외국인/기관 순매매 상위 종목을 paper-only 모니터링/검증 seed로 수집'),
    'daily_price_refresh': ('Daily Price Refresh', 'active/watch/mover seed universe 일봉 현행화'),
    'smbs_fx_import': ('SMBS FX Import', '서울외국환중개 공식 USD/KRW 매매기준율 갱신'),
    'opendart_disclosures': ('OpenDART Disclosures', '한국 공시 리스크 수집'),
    'sec_edgar_disclosures': ('SEC EDGAR Disclosures', '미국 공시 리스크 수집'),
    'disclosure_impact': ('Disclosure Impact Agent', '공시 본문/영향 평가 및 제목 기반 리스크 완화'),
    'discovery_validation': ('Discovery Validation', '신규/저검증 종목과 전략의 과거 검증 표본 보강'),
    'disclosure_impact_recommendations': ('Disclosure Impact Agent', '추천 후보 우선 공시 본문/영향 평가'),
    'market_issue_scout': ('Market Issue Scout', '가격/거래량 기반 동적 시장 이슈 탐지'),
    'market_news_issue_scout': ('Market News Issue Scout', '뉴스 우선 시장 이슈/테마 탐지'),
    'market_route_audit': ('Market Route Audit', '시장별 audit 품질 분리/route 후보 감시'),
    'experiment_escalation': ('Experiment Escalation', '반복된 미미한 개선 감지 후 더 과감한 paper-only 실험 제안'),
    'us_route_eligibility': ('US Route Eligibility', 'KR와 분리된 US route paper-watch eligibility 판정'),
    'market_issue_narrative': ('Market Issue Narrative', '시장 이슈 설명/뉴스 내러티브 보강'),
    'strategy_tail_risk_filter': ('Strategy Tail Risk Filter', 'active 전략 tail-risk 티어링/보수적 격하'),
    'exit_policy_optimizer': ('Exit Policy Optimizer', '손절/목표/보유기간 재검증 후보 제안'),
    'shadow_recommendations': ('Shadow Recommendations', '대체 추천 관점/비교 후보 생성'),
    'internal_signal_board': ('Internal Signal Board', '내부 신호 요약판 생성'),
    'universe_curator': ('Universe Curator', '연구 universe 위생 관리'),
    'opendart_financials': ('OpenDART Financials', 'KR 재무 스냅샷 보강'),
    'data_quality': ('Data Quality Agent', '가격 데이터 품질 점검'),
    'strategy_generator': ('Strategy Generator', '전략 후보 생성'),
    'capacity_planner': ('Validation Capacity Planner', '검증 처리량 조절'),
    'simulation_validation_worker': ('Simulation Validation Worker', '과거 시점 검증 실행'),
    'strategy_novelty_pruner': ('Strategy Novelty Pruner', '중복/과최적화 후보 축소'),
    'strategy_lifecycle': ('Strategy Lifecycle Agent', '전략 승격/감시/퇴역'),
    'active_strategy_balancer': ('Active Strategy Balancer', 'active pool 균형 유지'),
    'strategy_success_optimizer': ('Strategy Success Optimizer', '전략 성공률 개선 게이트'),
    'strategy_context_router': ('Strategy Context Router', '현재 시장/전략 성과 컨텍스트에 맞는 전략 family/parameter arm 선택 메타 레이어'),
    'short_horizon_profit_profile': ('Short Horizon Profit Profile', '1~5D 목표수익률 보정 도달률 검증'),
    'recommendation_agent': ('Recommendation Agent', '현재 추천 후보 생성'),
    'recommendation_agent_after_disclosure': ('Recommendation Agent', '공시 영향평가 반영 후 추천 후보 재산출'),
    'recommendation_critic': ('Recommendation Critic', '추천 반대 근거 점검'),
    'portfolio_risk_manager': ('Portfolio Risk Manager', '포트폴리오/집중 리스크 점검'),
    'market_context': ('Market Context Agent', '장 마감 시장 리뷰와 타 시장 영향 분석'),
    'market_shock_mover_scout': ('Market Shock Mover Scout', '장마감 급등락/테마 전파를 paper-only 연구가설로 정리'),
    'supply_close_strength_scout': ('Supply/Close Strength Scout', '거래량 확장과 종가 상단 마감으로 paper-only 수급/마감강도 후보 탐지'),
    'theme_spillover_backtest': ('Theme Spillover Backtest', '테마 전파 후속수익을 벤치마크 대비 과거 검증'),
    'market_regime_gate': ('Market Regime Gate', '시장 환경 필터'),
    'investment_committee': ('Investment Committee', '공격/안전/중립 의견 종합'),
    'oversold_recovery': ('Oversold Recovery', '과매도 회복형 보조 신호 탐지'),
    'current_recommendation_validation': ('Current Recommendation Validation', '현재 추천 후보 우선 검증'),
    'committee_performance_ledger': ('Committee Performance Ledger', '위원회 판단 이력/성과 기록'),
    'recommendation_outcome_tracker': ('Recommendation Outcome Tracker', '추천 사후성과 추적'),
    'strategy_context_outcome_ledger': ('Strategy Context Outcome Ledger', '시장상황 x 선택전략 성과 피드백 루프 기록'),
    'paper_fund_simulator': ('Paper Fund Simulator', '가벼운 paper-only 펀드 리그/진화 루프'),
    'paper_fund_historical_replay': ('Paper Fund Historical Replay', '과거 recommendation snapshot 기반 daily fund league replay'),
    'paper_fund_price_replay': ('Paper Fund Price Replay', '과거 가격 데이터 기반 direct daily fund league replay'),
    'fund_registry': ('Fund Registry', 'paper fund를 조직의 중심 단위로 등록/정리'),
    'fund_performance_evaluator': ('Fund Performance Evaluator', 'fund 성과를 tier/quality로 평가'),
    'fund_risk_guardian': ('Fund Risk Guardian', 'fund 단위 MDD/회전율/집중 위험 가드'),
    'fund_consensus': ('Fund Consensus', '상위 fund 보유/성향 합의를 추천 overlay로 제공'),
    'fund_recommendation_consensus': ('Fund Recommendation Consensus', '전일 기준 상위 fund 공통 매수 종목을 추천 1차 화면으로 제공'),
    'common_universe': ('Common Universe', '추천/fund/context가 함께 쓰는 단일 canonical universe'),
    'recommendation_market_context': ('Recommendation Market Context', '종목추천에 지수 대비/거래량/공시 맥락을 증거 레이어로 제공'),
    'recommendation_funnel': ('Recommendation Funnel Agent', '추천 후보 선별 흐름 계측'),
    'recommendation_calibration': ('Recommendation Calibration Agent', '판단 품질 보정'),
    'audit_tail_quarantine_scout': ('Audit Tail Quarantine Scout', '좌측 꼬리/기간별 취약 구간 격리 후보 탐색'),
    'kr_entry_signal_scout': ('KR Entry Signal Scout', 'KR 진입 신호 후보 발굴'),
    'kr_multi_evidence_signal_scout': ('KR Multi Evidence Signal Scout', 'KR 다중 근거 신호 후보 발굴'),
    'positive_cohort_scout': ('Positive Cohort Scout', '양수 edge cohort 후보 탐색'),
    'supply_weight_evaluator': ('Supply Weight Evaluator', '수급/거래주체 점수 보정의 paper 사후성과를 구간별 평가하고 가중치 조정안을 제안'),
    'target_return_adjustment_evaluator': ('Target Return Adjustment Evaluator', '목표수익률 보정치를 전략 파라미터 arm으로 비교하고 수익률 개선 메타 제안을 생성'),
    'alpha_fast_lane': ('Alpha Fast Lane', '현재 추천/펀드/상한가 후보 중 검증 우위 조합을 validation fast lane으로 선별'),
    'recommendation_audit': ('Strategy Trust Audit', '전략 신뢰도·조건 감사'),
    'outcome_attribution': ('Outcome Attribution', '검증 결과 원인 추정'),
    'org_evaluator': ('Org Evaluator', '조직 메타 평가'),
    'org_improvement_guardian': ('Org Improvement Guardian', '개선안 자동적용 가드레일'),
    'research_hypothesis': ('Research Hypothesis Agent', '병목 기반 실험 가설 생성'),
    'experiment_planner': ('Experiment Planner', '가설을 bounded diagnostic plan으로 변환'),
    'experiment_runner': ('Experiment Runner', 'bounded target-aware 실험 실행'),
    'evidence_judge': ('Evidence Judge', '실험 결과를 평가하고 proposal-only 후속조치 분류'),
    'research_experiment_ledger': ('Research Experiment Ledger', '자율 실험 이력/delta/repeat 추적'),
    'research_org_orchestrator': ('Alpha Orchestrator', '수익률 개선 병목을 고르고 안전한 연구 액션을 실행'),
    'org_architecture_review': ('Org Architecture Review', '조직 구조/역할/중복/통합 후보를 메타 관점에서 점검'),
    'paper_trader_integrity': ('Paper Trader Integrity', 'paper-only 안전/추천 UI 가시성 점검'),
    'experiment_spec_compiler': ('Experiment Spec Compiler', 'research queue/audit/scout/escalation 산출물을 generic experiment spec으로 정규화'),
    'suborg_summary': ('Sub-Organization Summary', '부서별 compact contract와 research queue를 생성해 UI/agent가 큰 artifact 없이 상태를 읽도록 함'),
}


def utc_now(): return datetime.now(timezone.utc).isoformat()


def artifact_key_from_path(path: str | Path) -> str:
    return Path(path).name.removesuffix('.json')


def write_latest_artifact(path: str | Path, payload: dict, *, mirror_file: bool = True) -> None:
    save_latest_artifact(
        artifact_key_from_path(path),
        payload,
        artifact_path=str(path),
        status=payload.get('status') if isinstance(payload, dict) else None,
        summary=str(payload.get('summary') or '')[:1000] if isinstance(payload, dict) else None,
    )
    if mirror_file:
        write_json_shared(path, payload)


def read_json(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception as exc:
        return {'_read_error': str(exc), '_path': path}


def summarize_output(path: str, payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {'_path': path, '_type': type(payload).__name__}
    contract = payload.get('contract') or {}
    summary = payload.get('summary') or contract.get('summary') or {}
    metrics = contract.get('metrics') or payload.get('metrics') or {}
    warnings = payload.get('warnings') or contract.get('warnings') or []
    out = {
        'run_at': payload.get('run_at') or contract.get('run_at'),
        'mode': payload.get('mode'),
        'status': payload.get('status') or contract.get('status'),
        'summary': summary,
        'metrics': metrics,
        'warnings': warnings[:20] if isinstance(warnings, list) else warnings,
    }
    for key in ('recommendation_changes','promoted','updates'):
        if key in payload:
            val = payload.get(key)
            out[key] = val[:20] if isinstance(val, list) else val
    if isinstance(payload.get('items'), list):
        out['item_count'] = len(payload['items'])
    return out


def load_pipeline_config() -> dict:
    path = ROOT / 'configs' / 'research_pipeline.yaml'
    if not path.exists():
        return {'defaults': {'timeout_seconds': 180, 'failure_mode': 'degrade'}, 'agents': {}}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'_read_error': str(exc), 'defaults': {'timeout_seconds': 180, 'failure_mode': 'degrade'}, 'agents': {}}


def agent_policy(config: dict, name: str) -> dict:
    defaults = config.get('defaults') or {}
    policy = dict(defaults)
    policy.update((config.get('agents') or {}).get(name) or {})
    policy.setdefault('timeout_seconds', 180)
    policy.setdefault('failure_mode', 'degrade')
    return policy


def run_agent(name: str, cmd: list[str], outputs: list[str] | None = None, required: bool = True, timeout_seconds: int = 180, failure_mode: str = 'degrade') -> dict:
    started=utc_now()
    timed_out = False
    try:
        p=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,timeout=timeout_seconds)
        returncode = p.returncode
        stdout = p.stdout
        stderr = p.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = (exc.stdout or '') if isinstance(exc.stdout, str) else (exc.stdout or b'').decode('utf-8', errors='replace')
        stderr = (exc.stderr or '') if isinstance(exc.stderr, str) else (exc.stderr or b'').decode('utf-8', errors='replace')
        stderr = f"{stderr}\n{name} timed out after {timeout_seconds}s".strip()
    ended=utc_now()
    raw_output_payloads={path: read_json(path) for path in (outputs or []) if Path(path).exists()}
    output_payloads={path: summarize_output(path, payload) for path, payload in raw_output_payloads.items()}
    contracts=[payload.get('contract') for payload in raw_output_payloads.values() if isinstance(payload, dict) and payload.get('contract')]
    contract_statuses=[c.get('status') for c in contracts if c]
    status='ok' if returncode==0 else ('failed_required' if required else 'failed_optional')
    if returncode==0 and any(x and x != 'ok' for x in contract_statuses):
        status='degraded'
    warnings=[]
    if timed_out:
        warnings.append(f'{name} timed out after {timeout_seconds}s')
    elif returncode!=0:
        warnings.append(f'{name} exited {returncode}')
    if outputs and not output_payloads:
        warnings.append(f'{name} produced no declared output files')
    for c in contracts:
        warnings.extend(c.get('warnings') or [])
    display_name, role_summary = AGENT_ROLE_SUMMARY.get(name, (name, '역할 요약 미정의'))
    if outputs:
        for artifact_path, payload in raw_output_payloads.items():
            summary_payload = output_payloads.get(artifact_path) or {}
            artifact_hash = None
            try:
                artifact_hash = hashlib.sha256(Path(artifact_path).read_bytes()).hexdigest()
            except Exception:
                artifact_hash = None
            try:
                save_agent_run_artifact(
                    name,
                    status,
                    run_id=started,
                    started_at=started,
                    ended_at=ended,
                    returncode=returncode,
                    artifact_path=artifact_path,
                    artifact_hash=artifact_hash,
                    summary=str(summary_payload.get('summary') or '')[:1000],
                    metrics=summary_payload.get('metrics') if isinstance(summary_payload.get('metrics'), dict) else {},
                    warnings=warnings[:50],
                    payload=summary_payload,
                )
            except Exception as exc:
                warnings.append(f'{name} DB artifact index failed: {exc}')
    else:
        try:
            save_agent_run_artifact(
                name,
                status,
                run_id=started,
                started_at=started,
                ended_at=ended,
                returncode=returncode,
                warnings=warnings[:50],
                payload={'cmd': cmd, 'declared_outputs': outputs or []},
            )
        except Exception as exc:
            warnings.append(f'{name} DB run index failed: {exc}')
    return {
        'agent': name,
        'display_name': display_name,
        'role_summary': role_summary,
        'status': status,
        'contract_statuses': contract_statuses,
        'required': required,
        'failure_mode': failure_mode,
        'timeout_seconds': timeout_seconds,
        'cmd': cmd,
        'started_at': started,
        'ended_at': ended,
        'returncode': returncode,
        'stdout_tail': stdout[-3000:],
        'stderr_tail': stderr[-3000:],
        'outputs': output_payloads,
        'warnings': warnings,
    }


def status_counts(rows):
    out={}
    for r in rows: out[r['status']]=out.get(r['status'],0)+1
    return out



def main():
    ap=argparse.ArgumentParser(description='Unified state-based research pipeline agent')
    ap.add_argument('--batch-size',type=int,default=500)
    ap.add_argument('--max-batch-size',type=int,default=1500)
    ap.add_argument('--seed',type=int,default=42)
    ap.add_argument('--random-cutoffs',type=int,default=24)
    ap.add_argument('--skip-data-refresh',action='store_true',help='Use latest price/disclosure/financial outputs without running bulk refresh agents')
    ap.add_argument('--include-heavy-retests',action='store_true',help='Run heavy paper-only retest agents; default 15-minute loop reads their latest artifacts only')
    ap.add_argument('--output',default='/tmp/research_pipeline_latest.json')
    args=ap.parse_args(); init_db()
    config=load_pipeline_config()
    org_profile=read_json(str(ROOT / 'configs' / 'org_profile.json'))
    strategy_profile=(org_profile.get('strategy') or {}) if isinstance(org_profile, dict) else {}
    before={'coverage':validation_coverage(),'strategy_status':status_counts(list_strategy_registry())}
    steps=[]
    heavy_retests_skipped = not args.include_heavy_retests

    def add(name, cmd, outputs=None, required=None):
        policy=agent_policy(config,name)
        failure_mode=str(policy.get('failure_mode') or 'degrade')
        is_required=(failure_mode == 'block') if required is None else required
        step=run_agent(name,[sys.executable,*cmd],outputs,is_required,int(policy.get('timeout_seconds') or 180),failure_mode)
        steps.append(step)
        return step

    add('pipeline_smoke_check',['tools/agents/pipeline_smoke_check.py'],['/tmp/pipeline_smoke_check_latest.json'])

    add('universe_discovery',['tools/agents/universe_discovery_agent.py','--max-new','24','--per-run-floor','24','--start','2019-01-01'],['/tmp/universe_discovery_latest.json'])
    add('investor_flow_seed',['tools/agents/investor_flow_seed_agent.py','--limit-per-market','50'],['/tmp/investor_flow_seed_latest.json'])
    if not args.skip_data_refresh:
        add('market_mover_seed',['tools/agents/market_mover_seed_agent.py','--limit-per-market','80'],['/tmp/market_mover_seed_latest.json'])
        add('daily_price_refresh',['tools/agents/daily_price_refresh_agent.py','--lookback-days','10'],['/tmp/daily_price_refresh_latest.json'])
        add('smbs_fx_import',['tools/agents/smbs_fx_import_agent.py','--start',(datetime.now(timezone.utc).date()-timedelta(days=45)).isoformat(),'--end',datetime.now(timezone.utc).date().isoformat()],['/tmp/smbs_fx_import_latest.json'])
        add('opendart_disclosures',['tools/agents/opendart_disclosure_agent.py','--symbols','active-kr','--save'],['/tmp/opendart_disclosures_latest.json'])
        add('sec_edgar_disclosures',['tools/agents/sec_edgar_disclosure_agent.py','--symbols','active-us','--save'],['/tmp/sec_edgar_disclosures_latest.json'])
    add('disclosure_impact',['tools/agents/disclosure_impact_agent.py','--limit','160'],['/tmp/disclosure_impact_latest.json'])
    add('universe_curator',['tools/agents/universe_curator.py','--save'],['/tmp/universe_curator_latest.json'])
    if not args.skip_data_refresh:
        add('opendart_financials',['tools/agents/opendart_financial_agent.py','--symbols','active-kr','--limit','100','--save'],['/tmp/opendart_financials_latest.json'])
    add('data_quality',['tools/agents/data_quality_agent.py'],['/tmp/data_quality_latest.json'])

    # When validation coverage is low, apply the guardian's recommendation directly:
    # constrain new strategy generation and spend the run budget validating existing
    # under-tested/recommended candidates.  This is research-only and never places orders.
    pre_gen_cov = validation_coverage()
    pre_gen_coverage_pct = float(pre_gen_cov.get('coverage_pct_estimate') or 0)
    recent_ledger = read_json('/tmp/research_experiment_ledger_latest.json')
    if recent_ledger.get('_read_error'):
        recent_ledger = read_json(str(ROOT / 'state' / 'latest' / 'research_experiment_ledger_latest.json'))
    recent_repeat_count = int(((recent_ledger.get('summary') or {}).get('repeat_count') or (recent_ledger.get('metrics') or {}).get('repeat_count') or 0)) if isinstance(recent_ledger, dict) else 0
    guardian_repeat_count = 0
    recent_guardian = read_json('/tmp/org_improvement_guardian_latest.json')
    if recent_guardian.get('_read_error'):
        recent_guardian = read_json(str(ROOT / 'state' / 'latest' / 'org_improvement_guardian_latest.json'))
    for proposal in (recent_guardian.get('patch_proposals') or []) if isinstance(recent_guardian, dict) else []:
        evidence = proposal.get('evidence') or {}
        guardian_repeat_count = max(guardian_repeat_count, int(evidence.get('recent_repeat_count') or proposal.get('recent_repeat_count') or 0))
    recent_repeat_count = max(recent_repeat_count, guardian_repeat_count)
    validation_first_mode = pre_gen_coverage_pct < float(strategy_profile.get('validation_first_coverage_pct') or 25)
    repeat_suppression_mode = validation_first_mode and recent_repeat_count >= int(strategy_profile.get('validation_first_repeat_threshold') or 3)
    if validation_first_mode:
        strategy_limit = int(strategy_profile.get('validation_first_strategy_limit') or 8)
        high_return_quota = int(strategy_profile.get('validation_first_high_return_quota') or 1)
        data_only_ratio = float(strategy_profile.get('validation_first_data_only_ratio') or 0.20)
        if repeat_suppression_mode:
            strategy_limit = int(strategy_profile.get('validation_first_repeat_strategy_limit') or 2)
            high_return_quota = int(strategy_profile.get('validation_first_repeat_high_return_quota') or 0)
            data_only_ratio = float(strategy_profile.get('validation_first_repeat_data_only_ratio') or 0.10)
        strategy_generator_cmd = [
            'tools/agents/strategy_generator_agent.py',
            '--register',
            '--limit', str(strategy_limit),
            '--high-return-quota', str(high_return_quota),
            '--data-only-ratio', str(data_only_ratio),
        ]
    else:
        strategy_generator_cmd = [
            'tools/agents/strategy_generator_agent.py',
            '--register',
            '--high-return-quota', str(strategy_profile.get('high_return_quota') or 8),
            '--data-only-ratio', str(strategy_profile.get('data_only_ratio') or 0.30),
        ]
    add('strategy_generator', strategy_generator_cmd, ['/tmp/strategy_candidates_latest.json'])
    add('capacity_planner',['tools/agents/validation_capacity_planner.py','--default-batch-size',str(args.batch_size),'--max-batch-size',str(args.max_batch_size)],['/tmp/validation_capacity_planner_latest.json'])

    cov=validation_coverage(); pending=cov.get('pending_results_estimate') or 0
    planner=read_json('/tmp/validation_capacity_planner_latest.json')
    batch=args.batch_size
    if planner.get('auto_apply'):
        batch=min(args.max_batch_size,max(1,int(planner.get('recommended_batch_size') or batch)))
    if pending>0:
        add('simulation_validation_worker',['tools/agents/simulation_validation_worker.py','--batch-size',str(batch)],['/tmp/simulation_validation_latest.json'])
        discovery_validation_batch = max(600, min(int(strategy_profile.get('validation_first_discovery_batch') or 1200) if validation_first_mode else 900, batch))
        add('discovery_validation',['tools/agents/discovery_validation_worker.py','--batch-size',str(discovery_validation_batch)],['/tmp/discovery_validation_latest.json'])

    add('strategy_novelty_pruner',['tools/agents/strategy_novelty_pruner.py','--apply','--max-apply','20'],['/tmp/strategy_novelty_pruner_latest.json'])
    add('strategy_lifecycle',['tools/agents/strategy_lifecycle_agent.py'],['/tmp/strategy_lifecycle_latest.json'])
    add('active_strategy_balancer',['tools/agents/active_strategy_balancer_agent.py','--target-active',str(strategy_profile.get('target_active') or 5),'--max-promote',str(strategy_profile.get('max_promote') or 2),'--high-upside-slots',str(strategy_profile.get('high_upside_slots') or 2)],['/tmp/active_strategy_balancer_latest.json'])
    add('strategy_tail_risk_filter',['tools/agents/strategy_tail_risk_filter_agent.py','--apply','--demote-severe','--min-research-active','3'],['/tmp/strategy_tail_risk_filter_latest.json'])
    add('strategy_success_optimizer',['tools/agents/strategy_success_optimizer_agent.py'],['/tmp/strategy_success_optimizer_latest.json'])

    strategies=list_strategy_registry()
    active=[x['logic'] for x in strategies if x['status'] in ('active','repair_active','validation_active')][:20]
    if not active:
        active=[x['logic'] for x in sorted(strategies,key=lambda r:(r.get('avg_excess_return_pct') or -999,r.get('success_rate_pct') or 0,r.get('samples') or 0), reverse=True) if x['status'] in ('watch','probation')][:10]
    logic_csv=','.join(active)

    add('market_context',['tools/agents/market_context_agent.py'],['/tmp/market_context_latest.json'])
    add('market_shock_mover_scout',['tools/agents/market_shock_mover_scout_agent.py'],['/tmp/market_shock_mover_scout_latest.json'])
    add('supply_close_strength_scout',['tools/agents/supply_close_strength_scout_agent.py'],['/tmp/supply_close_strength_scout_latest.json'])
    add('theme_spillover_backtest',['tools/agents/theme_spillover_backtest_agent.py'],['/tmp/theme_spillover_backtest_latest.json'])
    add('market_issue_scout',['tools/agents/market_issue_scout_agent.py'],['/tmp/market_issue_scout_latest.json'])
    add('market_news_issue_scout',['tools/agents/market_news_issue_scout_agent.py'],['/tmp/market_news_issue_scout_latest.json'])
    add('market_issue_narrative',['tools/agents/market_issue_narrative_agent.py'],['/tmp/market_issue_narrative_latest.json','/tmp/market_issue_scout_latest.json'])
    add('short_horizon_profit_profile',['tools/agents/short_horizon_profit_profile_agent.py','--horizon-days-list','2,5'],['/tmp/short_horizon_profit_profile_latest.json'])
    add('strategy_context_router',['tools/agents/strategy_context_router_agent.py'],['/tmp/strategy_context_router_latest.json'])
    # Shared evidence engines must run before recommendation generation so the
    # recommendation rows consume same-cycle universe/fund/market context rather
    # than stale evidence from the previous pipeline run.
    add('common_universe',['tools/agents/common_universe_agent.py'],['/tmp/common_universe_latest.json'])
    add('paper_fund_simulator',['tools/agents/paper_fund_simulator_agent.py','--fund-count','30','--min-age-runs','20','--evolve-every-runs','5'],['/tmp/paper_fund_simulator_latest.json'])
    add('paper_fund_historical_replay',['tools/agents/paper_fund_historical_replay_agent.py','--days','365','--fund-count','30','--min-age-days','20','--evolve-every-days','5'],['/tmp/paper_fund_historical_replay_latest.json'])
    if args.include_heavy_retests:
        add('paper_fund_price_replay',['tools/agents/paper_fund_price_replay_agent.py','--days','365','--fund-count','30','--min-age-days','20','--evolve-every-days','5','--max-symbols','120'],['/tmp/paper_fund_price_replay_latest.json'])
    add('fund_registry',['tools/agents/fund_registry_agent.py'],['/tmp/fund_registry_latest.json'])
    add('fund_performance_evaluator',['tools/agents/fund_performance_evaluator_agent.py'],['/tmp/fund_performance_evaluator_latest.json'])
    add('fund_risk_guardian',['tools/agents/fund_risk_guardian_agent.py'],['/tmp/fund_risk_guardian_latest.json'])
    add('fund_consensus',['tools/agents/fund_consensus_agent.py'],['/tmp/fund_consensus_latest.json'])
    add('fund_recommendation_consensus',['tools/agents/fund_recommendation_consensus_agent.py'],['/tmp/fund_recommendation_consensus_latest.json'])
    add('recommendation_market_context',['tools/agents/recommendation_market_context_agent.py'],['/tmp/recommendation_market_context_latest.json'])
    add('recommendation_agent',['tools/agents/recommendation_agent.py','--limit','20','--per-market-limit','10'],['/tmp/recommendations_latest.json'])
    add('disclosure_impact_recommendations',['tools/agents/disclosure_impact_agent.py','--recommendations','/tmp/recommendations_latest.json','--limit','240','--fetch-documents'],['/tmp/disclosure_impact_latest.json'])
    add('recommendation_agent_after_disclosure',['tools/agents/recommendation_agent.py','--limit','20','--per-market-limit','10'],['/tmp/recommendations_latest.json'])
    add('recommendation_critic',['tools/agents/recommendation_critic_agent.py'],['/tmp/recommendations_latest.json'])
    add('portfolio_risk_manager',['tools/agents/portfolio_risk_manager_agent.py'],['/tmp/recommendations_latest.json'])
    add('market_regime_gate',['tools/agents/market_regime_gate_agent.py'],['/tmp/recommendations_latest.json'])
    add('investment_committee',['tools/agents/investment_committee_agent.py'],['/tmp/recommendations_latest.json','/tmp/investment_committee_latest.json'])
    add('oversold_recovery',['tools/agents/oversold_recovery_agent.py'],['/tmp/oversold_recovery_latest.json'])
    add('shadow_recommendations',['tools/agents/shadow_recommendation_agent.py'],['/tmp/shadow_recommendations_latest.json'])
    add('internal_signal_board',['tools/agents/internal_signal_board_agent.py'],['/tmp/internal_signal_board_latest.json'])
    add('paper_trader_integrity',['tools/agents/paper_trader_integrity_agent.py'],['/tmp/paper_trader_integrity_latest.json'])
    add('alpha_fast_lane',['tools/agents/alpha_fast_lane_agent.py'],['/tmp/alpha_fast_lane_latest.json'])
    current_validation_batch = int(strategy_profile.get('validation_first_current_recommendation_batch') or 600) if validation_first_mode else 360
    current_validation_symbol_limit = int(strategy_profile.get('validation_first_symbol_limit') or 24) if validation_first_mode else 18
    current_validation_logic_limit = int(strategy_profile.get('validation_first_logic_limit') or 14) if validation_first_mode else 10
    if repeat_suppression_mode:
        current_validation_batch = int(strategy_profile.get('validation_first_repeat_current_recommendation_batch') or 900)
        current_validation_symbol_limit = int(strategy_profile.get('validation_first_repeat_symbol_limit') or 32)
        current_validation_logic_limit = int(strategy_profile.get('validation_first_repeat_logic_limit') or 18)
    current_validation_active_universe_limit = int(strategy_profile.get('validation_first_active_universe_limit') or 400)
    current_validation_cmd = [
        'tools/agents/current_recommendation_validation_worker.py',
        '--batch-size', str(current_validation_batch),
        '--symbol-limit', str(current_validation_symbol_limit),
        '--logic-limit', str(current_validation_logic_limit),
        '--fund-consensus-boost',
        '--include-active-universe',
        '--active-universe-limit', str(current_validation_active_universe_limit),
    ]
    add('current_recommendation_validation', current_validation_cmd, ['/tmp/current_recommendation_validation_latest.json'])
    add('committee_performance_ledger',['tools/agents/committee_performance_ledger_agent.py'],['/tmp/committee_performance_ledger_latest.json'])
    add('recommendation_outcome_tracker',['tools/agents/recommendation_outcome_tracker_agent.py','--horizons','1,5,20'],['/tmp/recommendation_outcomes_latest.json'])
    add('strategy_context_outcome_ledger',['tools/agents/strategy_context_outcome_ledger_agent.py','--horizon-days','1'],['/tmp/strategy_context_outcome_ledger_latest.json'])
    add('recommendation_funnel',['tools/agents/recommendation_funnel_agent.py'],['/tmp/recommendation_funnel_latest.json'])
    add('recommendation_calibration',['tools/agents/recommendation_calibration_agent.py'],['/tmp/recommendation_calibration_latest.json'])
    add('supply_weight_evaluator',['tools/agents/supply_weight_evaluator_agent.py','--horizon-days','1'],['/tmp/supply_weight_evaluator_latest.json'])
    add('target_return_adjustment_evaluator',['tools/agents/target_return_adjustment_evaluator_agent.py','--horizon-days','1'],['/tmp/target_return_adjustment_evaluator_latest.json'])
    if logic_csv:
        add('recommendation_audit',['tools/agents/recommendation_auditor.py','--horizon-days','20','--monthly-from','2025-01-01','--monthly-step','2','--cutoff-mode','mixed','--random-cutoffs',str(args.random_cutoffs),'--seed',str(args.seed),'--recent-cap-per-quarter','4','--logics',logic_csv],['/tmp/recommendation_audit_latest.json'])
        add('outcome_attribution',['tools/agents/outcome_attribution_agent.py'],['/tmp/recommendation_audit_latest.json'])
        add('exit_policy_optimizer',['tools/agents/exit_policy_optimizer_agent.py'],['/tmp/exit_policy_optimizer_latest.json'])
        add('market_route_audit',['tools/agents/market_route_audit_agent.py'],['/tmp/market_route_audit_latest.json'])
        add('kr_entry_signal_scout',['tools/agents/kr_entry_signal_scout_agent.py','--max-logics','8'],['/tmp/kr_entry_signal_scout_latest.json'])
        add('kr_multi_evidence_signal_scout',['tools/agents/kr_multi_evidence_signal_scout_agent.py'],['/tmp/kr_multi_evidence_signal_scout_latest.json'])
        add('audit_tail_quarantine_scout',['tools/agents/audit_tail_quarantine_scout_agent.py'],['/tmp/audit_tail_quarantine_scout_latest.json'])
        add('positive_cohort_scout',['tools/agents/positive_cohort_scout_agent.py'],['/tmp/positive_cohort_scout_latest.json'])
        add('experiment_escalation',['tools/agents/experiment_escalation_agent.py'],['/tmp/experiment_escalation_latest.json'])
        add('us_route_eligibility',['tools/agents/us_route_eligibility_agent.py'],['/tmp/us_route_eligibility_latest.json'])

    add('org_evaluator',['tools/agents/org_evaluator_agent.py','--save'],['/tmp/research_org_evaluation_latest.json'])
    add('org_improvement_guardian',['tools/agents/org_improvement_guardian_agent.py'],['/tmp/org_improvement_guardian_latest.json'])
    add('research_hypothesis',['tools/agents/research_hypothesis_agent.py'],['/tmp/research_hypotheses_latest.json'])
    add('experiment_spec_compiler',['tools/agents/experiment_spec_compiler_agent.py'],['/tmp/research_experiment_specs_latest.json'])
    add('experiment_planner',['tools/agents/experiment_planner_agent.py'],['/tmp/research_experiment_plan_latest.json'])
    add('experiment_runner',['tools/agents/experiment_runner_agent.py'],['/tmp/research_experiment_results_latest.json'])
    add('evidence_judge',['tools/agents/evidence_judge_agent.py'],['/tmp/research_evidence_judge_latest.json'])
    add('research_experiment_ledger',['tools/agents/research_experiment_ledger_agent.py'],['/tmp/research_experiment_ledger_latest.json'])
    add('research_org_orchestrator',['tools/agents/research_org_orchestrator.py'],['/tmp/research_org_orchestrator_latest.json'])
    add('org_architecture_review',['tools/agents/org_architecture_review_agent.py'],['/tmp/org_architecture_review_latest.json'])
    add('suborg_summary',['tools/agents/suborg_summary_agent.py'],['/tmp/research_org_suborg_summary_latest.json','/tmp/research_queue_latest.json'])

    after_strategies=list_strategy_registry(); after={'coverage':validation_coverage(),'strategy_status':status_counts(after_strategies)}
    recs=read_json('/tmp/recommendations_latest.json')
    audit=read_json('/tmp/recommendation_audit_latest.json')
    outcomes=read_json('/tmp/recommendation_outcomes_latest.json')
    strategy_context_ledger=read_json('/tmp/strategy_context_outcome_ledger_latest.json')
    paper_funds=read_json('/tmp/paper_fund_simulator_latest.json')
    paper_fund_replay=read_json('/tmp/paper_fund_historical_replay_latest.json')
    paper_fund_price_replay=read_json('/tmp/paper_fund_price_replay_latest.json')
    fund_registry=read_json('/tmp/fund_registry_latest.json')
    fund_eval=read_json('/tmp/fund_performance_evaluator_latest.json')
    fund_risk=read_json('/tmp/fund_risk_guardian_latest.json')
    fund_consensus=read_json('/tmp/fund_consensus_latest.json')
    fund_recommendation_consensus=read_json('/tmp/fund_recommendation_consensus_latest.json')
    recommendation_market_context=read_json('/tmp/recommendation_market_context_latest.json')
    common_universe=read_json('/tmp/common_universe_latest.json')
    funnel=read_json('/tmp/recommendation_funnel_latest.json')
    calibration=read_json('/tmp/recommendation_calibration_latest.json')
    supply_weight_eval=read_json('/tmp/supply_weight_evaluator_latest.json')
    target_return_eval=read_json('/tmp/target_return_adjustment_evaluator_latest.json')
    strategy_router=read_json('/tmp/strategy_context_router_latest.json')
    current_validation=read_json('/tmp/current_recommendation_validation_latest.json')
    market_context=read_json('/tmp/market_context_latest.json')
    market_mover_seed=read_json('/tmp/market_mover_seed_latest.json')
    investor_flow_seed=read_json('/tmp/investor_flow_seed_latest.json')
    market_shock=read_json('/tmp/market_shock_mover_scout_latest.json')
    short_horizon_profit=read_json('/tmp/short_horizon_profit_profile_latest.json')
    supply_close=read_json('/tmp/supply_close_strength_scout_latest.json')
    theme_spillover=read_json('/tmp/theme_spillover_backtest_latest.json')
    signal_board=read_json('/tmp/internal_signal_board_latest.json')
    exit_policy_retest={}
    market_route_retest={}
    market_route_audit=read_json('/tmp/market_route_audit_latest.json')
    kr_entry_signal_scout=read_json('/tmp/kr_entry_signal_scout_latest.json')
    kr_multi_evidence_signal_scout=read_json('/tmp/kr_multi_evidence_signal_scout_latest.json')
    relative_excess_gate_retest={}
    audit_tail_quarantine_scout=read_json('/tmp/audit_tail_quarantine_scout_latest.json')
    positive_cohort_scout=read_json('/tmp/positive_cohort_scout_latest.json')
    experiment_escalation=read_json('/tmp/experiment_escalation_latest.json')
    kr_bold_experiment_retest={}
    kr_multi_evidence_entry_retest={}
    direct_supply_repetition_retest={}
    us_route_eligibility=read_json('/tmp/us_route_eligibility_latest.json')
    us_relative_strength_pullback_retest={}
    tail_first_segmented_retest={}
    shadow_recs=read_json('/tmp/shadow_recommendations_latest.json')
    discovery_validation=read_json('/tmp/discovery_validation_latest.json')
    integrity=read_json('/tmp/paper_trader_integrity_latest.json')
    org_eval=read_json('/tmp/research_org_evaluation_latest.json')
    org_guardian=read_json('/tmp/org_improvement_guardian_latest.json')
    org_architecture=read_json('/tmp/org_architecture_review_latest.json')
    suborg_summary=read_json('/tmp/research_org_suborg_summary_latest.json')
    research_queue=read_json('/tmp/research_queue_latest.json')
    price_refresh=read_json('/tmp/daily_price_refresh_latest.json')
    kr_disclosures=read_json('/tmp/opendart_disclosures_latest.json')
    us_disclosures=read_json('/tmp/sec_edgar_disclosures_latest.json')
    failures=[s for s in steps if s['returncode']!=0]
    degraded_steps=[s for s in steps if s.get('status') == 'degraded']
    required_failures=[s for s in failures if s['required']]
    next_actions=[]
    if required_failures: next_actions.append('Fix required failed agents before trusting recommendations.')
    actual_active_count=after['strategy_status'].get('active',0)
    research_active_count=actual_active_count + after['strategy_status'].get('repair_active',0) + after['strategy_status'].get('validation_active',0)
    recommendation_effective_count=(recs.get('active_strategy_count') or 0) + (recs.get('repair_active_strategy_count') or 0) + (recs.get('effective_strategy_count') or 0)
    if research_active_count==0: next_actions.append('No active/repair-active strategies in registry; review lifecycle thresholds, reserve promotion criteria, or repair-only exit-policy retests.')
    elif recommendation_effective_count==0: next_actions.append('Recommendations used no active/repair-active strategies despite registry research-active set; inspect recommendation active selection timing.')
    if audit.get('_read_error'): next_actions.append('Audit report missing/unreadable; rerun recommendation_auditor.')
    guardian_proposals=org_guardian.get('patch_proposals') or []
    if guardian_proposals:
        titles=', '.join(str(x.get('title') or x.get('area') or 'untitled') for x in guardian_proposals[:3])
        next_actions.append(f'Org Improvement Guardian has {len(guardian_proposals)} patch proposal(s): {titles}.')
    if research_queue.get('mode') == 'research_queue':
        next_actions.append('Recommendation desk is in research_queue mode; prioritize validation/experiment queue over user-facing buy candidates.')
    if not next_actions: next_actions.append('Continue scheduled pipeline; monitor active strategy stability and recommendation drift.')
    status='failed' if required_failures else ('degraded' if failures or degraded_steps else 'ok')
    summary=f"Pipeline {status}: {len(steps)} agents, {len(failures)} failures, {len(degraded_steps)} degraded, active {after['strategy_status'].get('active',0)}, recommendations {len(recs.get('items',[]) or [])}, audit preview {len(audit.get('items',[]) or [])}."
    freshness_summary={
        'price': {
            'run_at': price_refresh.get('run_at'),
            'symbol_count': price_refresh.get('symbol_count'),
            'refreshed_count': price_refresh.get('refreshed_count'),
            'source_counts': price_refresh.get('source_counts'),
            'failed_symbols': price_refresh.get('failed_symbols'),
            'stale_symbols': price_refresh.get('stale_symbols'),
            'max_lag_by_market_days': price_refresh.get('max_lag_by_market_days'),
            'status': (price_refresh.get('contract') or {}).get('status'),
        },
        'market_mover_seed': {
            'run_at': market_mover_seed.get('run_at'),
            'summary': market_mover_seed.get('summary'),
            'status': (market_mover_seed.get('contract') or {}).get('status'),
        },
        'investor_flow_seed': {
            'run_at': investor_flow_seed.get('run_at'),
            'summary': investor_flow_seed.get('summary'),
            'top_symbols': [x.get('symbol') for x in (investor_flow_seed.get('top_symbols') or [])[:10]],
            'status': (investor_flow_seed.get('contract') or {}).get('status'),
        },
        'kr_disclosures': {
            'status': kr_disclosures.get('status'),
            'event_count': len(kr_disclosures.get('list', []) or []),
            'save_result': kr_disclosures.get('save_result'),
            'missing_symbols': kr_disclosures.get('missing_symbols'),
        },
        'us_disclosures': {
            'status': us_disclosures.get('status'),
            'event_count': len(us_disclosures.get('list', []) or []),
            'save_result': us_disclosures.get('save_result'),
            'missing_symbols': us_disclosures.get('missing_symbols'),
        },
    }
    trade_counts={k:sum(1 for r in (recs.get('items') or []) if r.get('recommendation_bucket')==k) for k in ('approved','watch','rejected')}
    report={'run_at':utc_now(),'mode':'state_based_research_pipeline','status':status,'summary':summary,'before':before,'after':after,'selected_batch_size':batch,'validation_first_control':{'coverage_pct':pre_gen_coverage_pct,'validation_first_mode':validation_first_mode,'recent_repeat_count':recent_repeat_count,'repeat_suppression_mode':repeat_suppression_mode,'strategy_generator_cmd':strategy_generator_cmd},'data_refresh_mode':'external_latest' if args.skip_data_refresh else 'inline_bulk_refresh','pipeline_config':{'path':str(ROOT / 'configs' / 'research_pipeline.yaml'),'schema':config.get('schema'),'read_error':config.get('_read_error'),'org_profile':org_profile},'active_logics_for_recommendation':active,'freshness_summary':freshness_summary,'market_context_summary':{'run_at':market_context.get('run_at'),'summary':market_context.get('summary'),'next_actions':market_context.get('next_actions')},'market_shock_summary':{'run_at':market_shock.get('run_at'),'summary':market_shock.get('summary'),'top_surges':[x.get('symbol') for x in (market_shock.get('top_surges') or [])[:5]],'top_crashes':[x.get('symbol') for x in (market_shock.get('top_crashes') or [])[:5]],'active_themes':[x.get('theme') for x in (market_shock.get('theme_spillovers') or []) if x.get('activation')=='active']},'investor_flow_seed_summary':{'run_at':investor_flow_seed.get('run_at'),'summary':investor_flow_seed.get('summary'),'top_symbols':[x.get('symbol') for x in (investor_flow_seed.get('top_symbols') or [])[:10]]},'short_horizon_profit_summary': {'run_at': short_horizon_profit.get('run_at'), 'item_count': short_horizon_profit.get('item_count'), 'horizons': short_horizon_profit.get('horizons'), 'logic_count': len(short_horizon_profit.get('by_logic') or {}), 'warnings': short_horizon_profit.get('warnings'), 'top_profiles': {k: {'samples': v.get('samples'), 'target_minus_2_pct_points_hit_pct': v.get('target_minus_2_pct_points_hit_pct'), 'adjusted_target_profile': v.get('adjusted_target_profile')} for k, v in list((short_horizon_profit.get('by_logic') or {}).items())[:6]}, 'by_horizon_summary': {hk: {lk: {'samples': lv.get('samples'), 'target_minus_2_pct_points_hit_pct': lv.get('target_minus_2_pct_points_hit_pct'), 'target_hit_pct': lv.get('target_hit_pct'), 'adjusted_target_profile': lv.get('adjusted_target_profile')} for lk, lv in list(((hv or {}).get('by_logic') or {}).items())[:6]} for hk, hv in (short_horizon_profit.get('by_horizon') or {}).items()}},
        'supply_close_strength_summary':{'run_at':supply_close.get('run_at'),'summary':supply_close.get('summary'),'top_symbols':[x.get('symbol') for x in (supply_close.get('items') or [])[:5]],'warnings':supply_close.get('warnings')},'theme_spillover_summary':{'run_at':theme_spillover.get('run_at'),'summary':theme_spillover.get('summary'),'promising_themes':[x.get('theme') for x in (theme_spillover.get('items') or []) if x.get('verdict')=='promising_research_candidate']},'strategy_context_router_summary':{'run_at':strategy_router.get('run_at'),'regime_context':strategy_router.get('regime_context'),'summary':strategy_router.get('summary')},'exit_policy_retest_summary':{'run_at':exit_policy_retest.get('run_at'),'best_logic':exit_policy_retest.get('best_logic'),'summary':exit_policy_retest.get('summary'),'baseline':exit_policy_retest.get('baseline'),'top_results':[{'policy':x.get('policy'),'verdict':x.get('verdict'),'delta':x.get('delta_vs_baseline'),'quality_score':((x.get('summary') or {}).get('quality_score')),'expected_excess_value_pct':((x.get('summary') or {}).get('expected_excess_value_pct')),'p10_excess_return_pct':((x.get('summary') or {}).get('p10_excess_return_pct'))} for x in (exit_policy_retest.get('results') or [])[:4]]},'market_route_retest_summary':{'run_at':market_route_retest.get('run_at'),'best_logic':market_route_retest.get('best_logic'),'summary':market_route_retest.get('summary'),'top_results':[{'market':x.get('market'),'policy':x.get('policy'),'verdict':x.get('verdict'),'blockers':x.get('blockers'),'delta':x.get('delta_vs_market_baseline'),'quality_score':((x.get('summary') or {}).get('quality_score')),'avg_excess_return_pct':((x.get('summary') or {}).get('avg_excess_return_pct')),'expected_excess_value_pct':((x.get('summary') or {}).get('expected_excess_value_pct')),'p10_excess_return_pct':((x.get('summary') or {}).get('p10_excess_return_pct'))} for x in (market_route_retest.get('results') or [])[:4]]},'market_route_audit_summary':{'run_at':market_route_audit.get('run_at'),'best_logic':market_route_audit.get('best_logic'),'summary':market_route_audit.get('summary'),'global_audit':market_route_audit.get('global_audit'),'market_quality':{m:{'sample_count':v.get('sample_count'),'quality_score':v.get('quality_score'),'quality_grade':v.get('quality_grade'),'avg_excess_return_pct':v.get('avg_excess_return_pct'),'expected_excess_value_pct':v.get('expected_excess_value_pct'),'p10_excess_return_pct':v.get('p10_excess_return_pct'),'quality_flags':v.get('quality_flags')} for m,v in (market_route_audit.get('market_quality') or {}).items()},'watch_candidates':market_route_audit.get('watch_candidates')},'kr_entry_signal_scout_summary':{'run_at':kr_entry_signal_scout.get('run_at'),'summary':kr_entry_signal_scout.get('summary'),'top_results':[{'logic':x.get('logic'),'family':x.get('family'),'verdict':x.get('verdict'),'avg_excess_return_pct':((x.get('summary') or {}).get('avg_excess_return_pct')),'expected_excess_value_pct':((x.get('summary') or {}).get('expected_excess_value_pct')),'p10_excess_return_pct':((x.get('summary') or {}).get('p10_excess_return_pct')),'quality_score':((x.get('summary') or {}).get('quality_score')),'quality_flags':((x.get('summary') or {}).get('quality_flags'))} for x in (kr_entry_signal_scout.get('results') or [])[:4]]},'kr_multi_evidence_signal_scout_summary':{'run_at':kr_multi_evidence_signal_scout.get('run_at'),'summary':kr_multi_evidence_signal_scout.get('summary'),'input_summary':kr_multi_evidence_signal_scout.get('input_summary'),'top_items':[{'symbol':x.get('symbol'),'score':x.get('score'),'verdict':x.get('verdict'),'blockers':x.get('blockers'),'evidence':x.get('evidence'),'outcome_preview':x.get('outcome_preview')} for x in (kr_multi_evidence_signal_scout.get('items') or [])[:5]]},'relative_excess_gate_retest_summary':{'run_at':relative_excess_gate_retest.get('run_at'),'summary':relative_excess_gate_retest.get('summary'),'top_results':[{'policy':x.get('policy'),'verdict':x.get('verdict'),'delta':x.get('delta_vs_baseline'),'sample_count':((x.get('summary') or {}).get('sample_count')),'avg_excess_return_pct':((x.get('summary') or {}).get('avg_excess_return_pct')),'expected_excess_value_pct':((x.get('summary') or {}).get('expected_excess_value_pct')),'p10_excess_return_pct':((x.get('summary') or {}).get('p10_excess_return_pct')),'quality_score':((x.get('summary') or {}).get('quality_score'))} for x in (relative_excess_gate_retest.get('results') or [])[:4]]},'audit_tail_quarantine_scout_summary':{'run_at':audit_tail_quarantine_scout.get('run_at'),'summary':audit_tail_quarantine_scout.get('summary'),'baseline':audit_tail_quarantine_scout.get('baseline'),'top_periods':audit_tail_quarantine_scout.get('period_candidates'),'top_symbols':audit_tail_quarantine_scout.get('symbol_candidates'),'experiments':audit_tail_quarantine_scout.get('experiments')},'positive_cohort_scout_summary':{'run_at':positive_cohort_scout.get('run_at'),'summary':positive_cohort_scout.get('summary'),'baseline':positive_cohort_scout.get('baseline'),'top_candidates':positive_cohort_scout.get('candidates')},'experiment_escalation_summary':{'run_at':experiment_escalation.get('run_at'),'summary':experiment_escalation.get('summary'),'audit_context':experiment_escalation.get('audit_context'),'remaining_blockers':experiment_escalation.get('remaining_blockers'),'escalation_level':experiment_escalation.get('escalation_level'),'reason':experiment_escalation.get('reason'),'forbidden_repeats':experiment_escalation.get('forbidden_repeats'),'bold_experiments':experiment_escalation.get('bold_experiments')},'kr_bold_experiment_retest_summary':{'run_at':kr_bold_experiment_retest.get('run_at'),'summary':kr_bold_experiment_retest.get('summary'),'proposal_id':kr_bold_experiment_retest.get('proposal_id'),'best_logic':kr_bold_experiment_retest.get('best_logic'),'baseline':kr_bold_experiment_retest.get('baseline'),'top_results':kr_bold_experiment_retest.get('results')},'kr_multi_evidence_entry_retest_summary':{'run_at':kr_multi_evidence_entry_retest.get('run_at'),'summary':kr_multi_evidence_entry_retest.get('summary'),'lookahead_policy':kr_multi_evidence_entry_retest.get('lookahead_policy'),'top_results':kr_multi_evidence_entry_retest.get('results')},'direct_supply_repetition_retest_summary':{'run_at':direct_supply_repetition_retest.get('run_at'),'summary':direct_supply_repetition_retest.get('summary'),'lookahead_policy':direct_supply_repetition_retest.get('lookahead_policy'),'top_results':direct_supply_repetition_retest.get('results')},'us_route_eligibility_summary':{'run_at':us_route_eligibility.get('run_at'),'summary':us_route_eligibility.get('summary'),'verdict':us_route_eligibility.get('verdict'),'blockers':us_route_eligibility.get('blockers'),'cautions':us_route_eligibility.get('cautions'),'us_quality':us_route_eligibility.get('us_quality'),'kr_quality':us_route_eligibility.get('kr_quality')},'us_relative_strength_pullback_retest_summary':{'run_at':us_relative_strength_pullback_retest.get('run_at'),'summary':us_relative_strength_pullback_retest.get('summary'),'proposal_id':us_relative_strength_pullback_retest.get('proposal_id'),'baseline':us_relative_strength_pullback_retest.get('baseline'),'top_results':us_relative_strength_pullback_retest.get('results')},'tail_first_segmented_retest_summary':{'run_at':tail_first_segmented_retest.get('run_at'),'summary':tail_first_segmented_retest.get('summary'),'market_summary':tail_first_segmented_retest.get('market_summary'),'top_results':tail_first_segmented_retest.get('results')},'recommendations_summary':{'run_at':recs.get('run_at'),'active_strategy_count':recs.get('active_strategy_count'),'market_counts':recs.get('market_counts'),'item_count':len(recs.get('items',[]) or []),'trade_eligible_count':sum(1 for r in (recs.get('items') or []) if r.get('trade_eligible')),'bucket_counts':trade_counts},'internal_signal_board_summary':signal_board.get('summary'),'shadow_recommendations_summary':{'run_at':shadow_recs.get('run_at'),'item_count':len(shadow_recs.get('items',[]) or []),'candidate_count':shadow_recs.get('candidate_count'),'market_counts':shadow_recs.get('market_counts')},'discovery_validation_summary':{'run_at':discovery_validation.get('run_at'),'processed_combinations':discovery_validation.get('processed_combinations'),'saved':discovery_validation.get('saved'),'processed_by_logic':discovery_validation.get('processed_by_logic')},'integrity_summary':{'run_at':integrity.get('run_at'),'status':integrity.get('status'),'summary':integrity.get('summary'),'problem_count':len(integrity.get('problems') or []),'warning_count':len(integrity.get('warnings') or [])},'current_recommendation_validation_summary':{'run_at':current_validation.get('run_at'),'symbols':current_validation.get('symbols'),'logics':current_validation.get('logics'),'processed_combinations':((current_validation.get('worker') or {}).get('processed_combinations')),'saved':((current_validation.get('worker') or {}).get('saved')),'status':(current_validation.get('contract') or {}).get('status')},'outcome_summary':{'run_at':outcomes.get('run_at'),'updated_rows':outcomes.get('updated_rows'),'status_counts':outcomes.get('status_counts'),'summary':outcomes.get('summary')},'strategy_context_outcome_summary':{'run_at':strategy_context_ledger.get('run_at'),'summary':strategy_context_ledger.get('summary'),'warnings':strategy_context_ledger.get('warnings')},'paper_fund_league_summary':{'run_at':paper_funds.get('run_at'),'run_count':paper_funds.get('run_count'),'fund_count':paper_funds.get('fund_count'),'summary':paper_funds.get('summary'),'retired_this_run':paper_funds.get('retired_this_run')},'paper_fund_historical_replay_summary':{'run_at':paper_fund_replay.get('run_at'),'actual_run_count':paper_fund_replay.get('actual_run_count'),'fund_count':paper_fund_replay.get('fund_count'),'summary':paper_fund_replay.get('summary'),'warnings':paper_fund_replay.get('warnings')},'paper_fund_price_replay_summary':{'run_at':paper_fund_price_replay.get('run_at'),'trading_days':paper_fund_price_replay.get('trading_days'),'fund_count':paper_fund_price_replay.get('fund_count'),'summary':paper_fund_price_replay.get('summary'),'warnings':paper_fund_price_replay.get('warnings')},'fund_org_summary':{'registry':fund_registry.get('summary'),'performance':fund_eval.get('summary'),'risk':fund_risk.get('summary'),'consensus':fund_consensus.get('summary'),'recommendation_consensus':fund_recommendation_consensus.get('summary'),'market_context':recommendation_market_context.get('summary'),'common_universe':{'item_count':common_universe.get('item_count'),'market_counts':common_universe.get('market_counts'),'run_at':common_universe.get('run_at')}},'recommendation_funnel_summary':{'run_at':funnel.get('run_at'),'role':funnel.get('role'),'summary_text':funnel.get('summary_text'),'summary':funnel.get('summary'),'stages':funnel.get('stages')},'org_improvement_summary':{'evaluator_run_at':org_eval.get('run_at'),'evaluator_verdict':org_eval.get('verdict'),'health_score':org_eval.get('health_score'),'finding_count':len(org_eval.get('findings') or []),'guardian_run_at':org_guardian.get('run_at'),'guardian_summary':org_guardian.get('summary'),'architecture_run_at':org_architecture.get('run_at'),'architecture_summary':org_architecture.get('summary'),'architecture_action_count':((org_architecture.get('summary') or {}).get('action_count')),'architecture_watch_count':((org_architecture.get('summary') or {}).get('watch_count')),'architecture_reviews':[{'kind':x.get('kind'),'severity':x.get('severity'),'title':x.get('title'),'recommendation':x.get('recommendation'),'evidence':x.get('evidence')} for x in (org_architecture.get('reviews') or [])[:5]],'patch_proposal_count':len(org_guardian.get('patch_proposals') or []),'patch_proposals':[{'area':x.get('area'),'title':x.get('title'),'risk':x.get('risk'),'evidence':x.get('evidence')} for x in (org_guardian.get('patch_proposals') or [])[:5]]},'calibration_summary':{'run_at':calibration.get('run_at'),'role':calibration.get('role'),'summary_text':calibration.get('summary_text'),'sample_count':calibration.get('sample_count'),'findings':calibration.get('findings'),'summary':calibration.get('summary')},'supply_weight_evaluation_summary':{'run_at':supply_weight_eval.get('run_at'),'horizon_days':supply_weight_eval.get('horizon_days'),'rows_scanned':supply_weight_eval.get('rows_scanned'),'proposal_count':len(supply_weight_eval.get('weight_adjustment_proposals') or []),'warnings':supply_weight_eval.get('warnings'),'summary':supply_weight_eval.get('summary')},'target_return_adjustment_evaluation_summary':{'run_at':target_return_eval.get('run_at'),'horizon_days':target_return_eval.get('horizon_days'),'rows_scanned':target_return_eval.get('rows_scanned'),'proposal_count':len(target_return_eval.get('target_adjustment_proposals') or []),'warnings':target_return_eval.get('warnings'),'summary':target_return_eval.get('summary')},'validation_summary':{'run_at':audit.get('run_at'),'cutoff_meta':audit.get('cutoff_meta'),'latest_cutoff':audit.get('latest_cutoff'),'items_total_filtered':audit.get('items_total_filtered'),'items_total_audited':audit.get('items_total_audited'),'items_total_candidate_buy_zone':audit.get('items_total_candidate_buy_zone'),'items_preview_filter':audit.get('items_preview_filter'),'action_counts':audit.get('action_counts'),'preview_count':len(audit.get('items',[]) or []),'best_logic':(audit.get('summary') or {}).get('best_logic'),'best':(audit.get('summary') or {}).get('best')},'steps':steps,'warnings':list(dict.fromkeys(w for s in steps for w in s.get('warnings',[]))),'next_actions':next_actions}
    rid=save_research_org_report('research_pipeline',summary,report); report['report_id']=rid
    fund_org_packet = build_fund_org_summary_artifact(report)
    for fund_org_path in (Path('/tmp/fund_org_summary_latest.json'), ROOT / 'static' / 'fund_org_summary_latest.json'):
        try:
            write_latest_artifact(fund_org_path, fund_org_packet)
        except Exception:
            pass
    context_goal, recommendations_status, audit_status, local_llm_delegation = build_context_goal_artifacts(report, recs, audit, steps, status, summary, next_actions)
    for compact_path, compact_payload in (
        (Path('/tmp/context_goal_latest.json'), context_goal),
        (Path('/tmp/recommendations_status_latest.json'), recommendations_status),
        (Path('/tmp/audit_status_latest.json'), audit_status),
        (Path('/tmp/local_llm_delegation_latest.json'), local_llm_delegation),
        (ROOT / 'static' / 'context_goal_latest.json', context_goal),
        (ROOT / 'static' / 'recommendations_status_latest.json', recommendations_status),
        (ROOT / 'static' / 'audit_status_latest.json', audit_status),
        (ROOT / 'static' / 'local_llm_delegation_latest.json', local_llm_delegation),
    ):
        try:
            write_latest_artifact(compact_path, compact_payload)
        except Exception:
            pass
    write_latest_artifact(args.output, report)
    print(json.dumps(context_goal,ensure_ascii=False,indent=2))
    if required_failures: sys.exit(1)


if __name__=='__main__': main()
