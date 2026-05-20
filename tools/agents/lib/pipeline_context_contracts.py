"""Compact context contracts for the research pipeline.

Keep domain-facing read-order, compact recommendation status, audit status,
and local delegation contracts out of the pipeline runner. The orchestrator
should execute agents; this module defines the small artifacts other agents/UI
should read before opening large debug payloads.
"""
from __future__ import annotations

def compact_value(value, *, list_limit: int = 5, depth: int = 2):
    if depth <= 0:
        if isinstance(value, (dict, list)):
            return {'_type': type(value).__name__, '_count': len(value)}
        return value
    if isinstance(value, dict):
        out = {k: compact_value(v, list_limit=list_limit, depth=depth - 1) for k, v in list(value.items())[:20]}
        if len(value) > 20:
            out['_truncated_keys'] = len(value) - 20
        return out
    if isinstance(value, list):
        out = [compact_value(v, list_limit=list_limit, depth=depth - 1) for v in value[:list_limit]]
        if len(value) > list_limit:
            out.append({'_truncated_items': len(value) - list_limit})
        return out
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + '...'
    return value


def compact_recommendation_item(item: dict) -> dict:
    if not isinstance(item, dict):
        return {'_type': type(item).__name__}
    evidence = item.get('evidence') or item.get('reasons') or item.get('reason_codes') or []
    if isinstance(evidence, list):
        evidence = evidence[:3]
    return {
        'symbol': item.get('symbol'),
        'name': item.get('name') or item.get('symbol_name'),
        'market': item.get('market'),
        'action': item.get('action'),
        'bucket': item.get('recommendation_bucket') or item.get('bucket'),
        'score': item.get('score'),
        'entry_price': item.get('entry_price') or item.get('target_buy_price') or item.get('buy_price'),
        'target_price': item.get('target_1') or item.get('target_price') or item.get('target_return_price'),
        'stop_reference': item.get('stop_reference') or item.get('stop_price'),
        'strategy_id': item.get('strategy_id') or item.get('logic'),
        'trade_eligible': item.get('trade_eligible'),
        'gate': compact_value(item.get('gate') or item.get('risk_gate') or item.get('committee') or {}, depth=1),
        'evidence': compact_value(evidence, list_limit=3, depth=1),
    }


def build_context_goal_artifacts(report: dict, recs: dict, audit: dict, steps: list[dict], status: str, summary: str, next_actions: list[str]) -> tuple[dict, dict, dict, dict]:
    failed_steps = [x for x in steps if x.get('returncode') != 0 or x.get('status') not in ('ok',)]
    degraded_steps = [x for x in steps if x.get('status') == 'degraded']
    changed = recs.get('recommendation_changes') or {}
    items = recs.get('items') or []
    audit_summary = audit.get('summary') or {}
    artifact_refs = {
        'suborg_compact': {'path': '/tmp/research_org_suborg_summary_latest.json', 'purpose': 'department-level compact contracts; read this before full artifacts'},
        'research_queue': {'path': '/tmp/research_queue_latest.json', 'purpose': 'research queue when recommendations are rejected or not trade-eligible'},
        'pipeline_full': {'path': '/tmp/research_pipeline_latest.json', 'purpose': 'full debug payload; avoid unless diagnosing pipeline internals'},
        'recommendations_full': {'path': '/tmp/recommendations_latest.json', 'purpose': 'full recommendation cards; prefer compact API/status first'},
        'audit_full': {'path': '/tmp/recommendation_audit_latest.json', 'purpose': 'paged audit source; prefer compact status or API filters first'},
        'audit_raw_full': {'path': '/tmp/recommendation_audit_full_latest.json', 'purpose': 'large raw audit artifact; do not read directly in LLM context'},
        'agent_task_state': {'path': '/tmp/agent_task_state_latest.json', 'purpose': 'shared orchestration lifecycle state; owner/status/attempt/check handoff'},
    }
    recommendations_status = {
        'run_at': recs.get('run_at'),
        'status': recs.get('status') or 'ok',
        'item_count': len(items),
        'market_counts': recs.get('market_counts'),
        'active_strategy_count': recs.get('active_strategy_count'),
        'effective_strategy_count': recs.get('effective_strategy_count'),
        'repair_active_strategy_count': recs.get('repair_active_strategy_count'),
        'bucket_counts': {k: sum(1 for r in items if r.get('recommendation_bucket') == k) for k in ('approved','watch','research_watch','rejected')},
        'change_summary': {
            'change_count': changed.get('change_count'),
            'new_symbols': changed.get('new_symbols') or [],
            'removed_symbols': changed.get('removed_symbols') or [],
            'action_changes': compact_value(changed.get('action_changes') or [], list_limit=5, depth=2),
            'bucket_changes': compact_value(changed.get('bucket_changes') or changed.get('post_committee_bucket_changes') or [], list_limit=8, depth=2),
        },
        'quality_notes': compact_value(recs.get('aggregate_quality_notes') or [], list_limit=5, depth=2),
        'top_items': [compact_recommendation_item(item) for item in items[:20]],
        'artifact_refs': {'full': artifact_refs['recommendations_full']},
    }
    audit_status = {
        'run_at': audit.get('run_at'),
        'status': audit.get('status') or ((audit.get('contract') or {}).get('status')),
        'latest_cutoff': audit.get('latest_cutoff'),
        'items_total_filtered': audit.get('items_total_filtered'),
        'items_total_audited': audit.get('items_total_audited'),
        'items_total_candidate_buy_zone': audit.get('items_total_candidate_buy_zone'),
        'items_preview_filter': audit.get('items_preview_filter'),
        'action_counts': compact_value(audit.get('action_counts') or {}, depth=1),
        'preview_count': len(audit.get('items') or []),
        'best_logic': audit_summary.get('best_logic'),
        'best': compact_value(audit_summary.get('best') or {}, depth=2),
        'quality_flags': compact_value((audit_summary.get('best') or {}).get('quality_flags') or [], list_limit=8, depth=1),
        'strategy_trust_improvement_plan': compact_value(audit_summary.get('strategy_trust_improvement_plan') or {}, depth=3, list_limit=6),
        'artifact_refs': {'paged': artifact_refs['audit_full'], 'raw_full': artifact_refs['audit_raw_full']},
    }
    context_goal = {
        'schema': 'paper_trader.context_goal.v1',
        'goal': 'Read compact status first; open full artifacts only for explicit diagnosis.',
        'run_at': report.get('run_at'),
        'status': status,
        'summary': summary,
        'next_actions': next_actions[:5],
        'read_order': ['/tmp/research_pipeline_status.json','/tmp/research_org_suborg_summary_latest.json','/tmp/research_queue_latest.json','/tmp/context_goal_latest.json','/tmp/recommendations_status_latest.json','/tmp/audit_status_latest.json','agent_runs DB / paged APIs','full artifacts only for targeted debugging'],
        'compact_contracts': {
            'suborgs': artifact_refs['suborg_compact'],
            'research_queue': artifact_refs['research_queue'],
        },
        'pipeline': {
            'agent_count': len(steps),
            'failure_count': len(failed_steps),
            'degraded_count': len(degraded_steps),
            'failed_or_degraded': [{'agent': x.get('agent'), 'status': x.get('status'), 'returncode': x.get('returncode'), 'warnings': compact_value(x.get('warnings') or [], list_limit=5, depth=1)} for x in (failed_steps + degraded_steps)[:12]],
            'selected_batch_size': report.get('selected_batch_size'),
            'data_refresh_mode': report.get('data_refresh_mode'),
        },
        'recommendations': {
            'item_count': recommendations_status['item_count'],
            'bucket_counts': recommendations_status['bucket_counts'],
            'market_counts': recommendations_status['market_counts'],
            'change_count': recommendations_status['change_summary']['change_count'],
        },
        'audit': {
            'best_logic': audit_status['best_logic'],
            'latest_cutoff': audit_status['latest_cutoff'],
            'preview_count': audit_status['preview_count'],
            'quality_flags': audit_status['quality_flags'],
            'trust_improvement_plan': audit_status['strategy_trust_improvement_plan'],
        },
        'artifact_refs': artifact_refs,
    }
    local_llm_delegation = {
        'schema': 'paper_trader.local_llm_delegation.v1',
        'goal': 'Delegate bounded review slices using compact artifacts only; no broker/order authority.',
        'run_at': report.get('run_at'),
        'status': status,
        'safety_boundary': {
            'allowed': ['historical research review', 'paper recommendation critique', 'artifact summarization', 'proposal-only improvement ideas'],
            'forbidden': ['real trading', 'broker/order endpoints', 'credential handling', 'direct production mutation'],
        },
        'shared_inputs': [
            '/tmp/research_pipeline_status.json',
            '/tmp/context_goal_latest.json',
            '/tmp/recommendations_status_latest.json',
            '/tmp/audit_status_latest.json',
            '/tmp/local_llm_delegation_latest.json',
            '/tmp/agent_task_state_latest.json',
        ],
        'output_contract': {
            'format': 'json',
            'required_keys': ['task_id', 'owner', 'status', 'verification_gate', 'findings', 'evidence_refs', 'next_action'],
            'status_values': ['queued', 'in_progress', 'routine', 'needs_human_review', 'proposal_only', 'blocked', 'completed', 'failed'],
            'lifecycle_keys': ['owner', 'lease_expires_at', 'attempt', 'verification_gate', 'result_artifact', 'escalation_reason'],
            'max_findings': 5,
        },
        'delegation_queue': [
            {
                'task_id': 'pipeline_health_review',
                'owner': 'unassigned',
                'status': 'queued',
                'lease_expires_at': None,
                'attempt': 0,
                'verification_gate': 'research_pipeline_status.status in {ok, needs_attention} and required failure evidence summarized',
                'result_artifact': None,
                'escalation_reason': None,
                'purpose': 'Check failures, skips, drift, and degraded agents from compact status only.',
                'inputs': ['/tmp/research_pipeline_status.json', '/tmp/context_goal_latest.json', '/tmp/agent_task_state_latest.json'],
                'notify_if': ['failure_count > 0', 'consecutive_failures > 0', 'unexpected degraded required agent'],
            },
            {
                'task_id': 'recommendation_drift_review',
                'owner': 'unassigned',
                'status': 'queued',
                'lease_expires_at': None,
                'attempt': 0,
                'verification_gate': 'recommendations_status change_summary reviewed and top item entry fields present',
                'result_artifact': None,
                'escalation_reason': None,
                'purpose': 'Review new/removed symbols, bucket moves, and top-card entry-plan sanity without opening full recommendations.',
                'inputs': ['/tmp/recommendations_status_latest.json'],
                'notify_if': ['new_symbols non-empty', 'large bucket churn', 'approved/research_watch inconsistency', 'entry plan missing on top items'],
            },
            {
                'task_id': 'audit_quality_review',
                'owner': 'unassigned',
                'status': 'queued',
                'lease_expires_at': None,
                'attempt': 0,
                'verification_gate': 'audit_status trust plan and sample counts reviewed',
                'result_artifact': None,
                'escalation_reason': None,
                'purpose': 'Judge whether current strategy trust labels, role profiles, and fund-routing evidence are weak, improving, or anomalous from compact audit status; prioritize strategy_trust_improvement_plan actions.',
                'inputs': ['/tmp/audit_status_latest.json'],
                'notify_if': ['quality_flags improve materially', 'best logic changes', 'positive excess appears', 'tail-risk anomaly appears', 'top trust-label action changes'],
            },
            {
                'task_id': 'org_improvement_review',
                'owner': 'unassigned',
                'status': 'queued',
                'lease_expires_at': None,
                'attempt': 0,
                'verification_gate': 'proposal includes file owner, acceptance criteria, and check command',
                'result_artifact': None,
                'escalation_reason': None,
                'purpose': 'Propose one concrete low-risk code/docs improvement from compact pipeline summaries.',
                'inputs': ['/tmp/context_goal_latest.json', '/tmp/agent_task_state_latest.json'],
                'notify_if': ['non-trivial proposal with clear file target and verification gate'],
            },
        ],
        'routing': {
            'recommended_default': 'local_llm',
            'escalate_to_codex_when': ['code edit needed', 'large artifact diagnosis needed', 'conflicting evidence', 'production behavior change'],
            'local_llm_prompt_budget': 'Use only shared_inputs plus at most one task-specific compact API response.',
        },
    }
    return context_goal, recommendations_status, audit_status, local_llm_delegation

def build_fund_org_summary_artifact(report: dict) -> dict:
    # Older UI fallback read static/fund_org_summary_latest.json, which could drift from the
    # canonical pipeline packet and show stale top-fund data.
    fund_org_summary = report.get('fund_org_summary') or {}
    fund_registry_summary = fund_org_summary.get('registry') or {}
    fund_performance_summary = fund_org_summary.get('performance') or {}
    fund_risk_summary = fund_org_summary.get('risk') or {}
    fund_consensus_summary = fund_org_summary.get('consensus') or {}
    fund_recommendation_summary = fund_org_summary.get('recommendation_consensus') or {}
    registered_fund_count = int(fund_performance_summary.get('fund_count') or sum((fund_registry_summary.get('source_counts') or {}).values()) or 0)
    champion_count = int(fund_performance_summary.get('champion_count') or 0)
    candidate_count = int(fund_performance_summary.get('candidate_count') or 0)
    risk_finding_count = int(fund_risk_summary.get('finding_count') or 0)
    consensus_symbol_count = int(fund_consensus_summary.get('symbol_consensus_count') or 0)
    recommendation_consensus_count = int(fund_recommendation_summary.get('item_count') or 0)
    top_fund = fund_performance_summary.get('top_fund') or fund_registry_summary.get('top_fund') or {}
    top_fund_return = top_fund.get('return_pct')
    top_fund_mdd = top_fund.get('mdd_pct')
    component_effectiveness = [
        {
            'agent': 'fund_registry',
            'effectiveness': 'high' if registered_fund_count >= 60 else ('medium' if registered_fund_count else 'low'),
            'evidence': {'registered_fund_count': registered_fund_count, 'source_counts': fund_registry_summary.get('source_counts')},
            'recommendation': 'Keep as fund sub-org inventory spine; it gives the rest of the organization a stable unit to evaluate.',
        },
        {
            'agent': 'fund_performance_evaluator',
            'effectiveness': 'high' if champion_count or candidate_count >= 5 else ('medium' if registered_fund_count else 'low'),
            'evidence': {'champion_count': champion_count, 'candidate_count': candidate_count, 'top_fund_return_pct': top_fund_return},
            'recommendation': 'Use champion/candidate tiering as the main fund selection gate; keep retire_pressure away from recommendation overlays.',
        },
        {
            'agent': 'fund_risk_guardian',
            'effectiveness': 'medium' if registered_fund_count else 'low',
            'evidence': {'risk_finding_count': risk_finding_count, 'top_fund_mdd_pct': top_fund_mdd},
            'recommendation': 'Effective as a guardrail; turnover/MDD findings cap consensus weight and future allocation rather than excluding funds outright.',
        },
        {
            'agent': 'fund_consensus',
            'effectiveness': 'medium' if consensus_symbol_count else ('watch' if registered_fund_count else 'low'),
            'evidence': {'symbol_consensus_count': consensus_symbol_count, 'top_styles': fund_consensus_summary.get('top_styles')},
            'recommendation': 'Keep style consensus active; symbol consensus remains proxy-driven until live holdings are deep enough.',
        },
        {
            'agent': 'fund_recommendation_consensus',
            'effectiveness': 'medium' if recommendation_consensus_count else 'watch',
            'evidence': {'item_count': recommendation_consensus_count, 'top_symbols': fund_recommendation_summary.get('top_symbols')},
            'recommendation': 'Useful as a primary view candidate source, but risk/committee gates must remain visible next to it.',
        },
    ]
    fund_org_packet={
        'schema': 'paper_trader.fund_suborg_summary.v1',
        'run_at': report.get('run_at'),
        'source': 'research_pipeline_latest',
        'suborg': 'fund_research_desk',
        'mode': 'paper_only_fund_research',
        'real_trading': False,
        'status': 'ok' if registered_fund_count else 'not_ready',
        'authority': 'recommendation_overlay_only_no_orders',
        'summary': {
            'registered_fund_count': registered_fund_count,
            'champion_count': champion_count,
            'candidate_count': candidate_count,
            'risk_finding_count': risk_finding_count,
            'risk_guardrail_policy': 'fund risk findings cap consensus/allocation weight; they do not create standalone trade signals',
            'consensus_symbol_count': consensus_symbol_count,
            'recommendation_consensus_count': recommendation_consensus_count,
            'top_fund_id': top_fund.get('id'),
            'top_fund_return_pct': top_fund_return,
            'top_fund_mdd_pct': top_fund_mdd,
            'purpose_fit': 'fund sub-org is useful as a recommendation-quality engine, not as standalone trade authority',
        },
        'component_effectiveness': component_effectiveness,
        'artifact_refs': {
            'canonical': '/tmp/fund_suborg_summary_latest.json',
            'legacy_alias': '/tmp/fund_org_summary_latest.json',
            'registry': '/tmp/fund_registry_latest.json',
            'performance': '/tmp/fund_performance_evaluator_latest.json',
            'risk': '/tmp/fund_risk_guardian_latest.json',
            'consensus': '/tmp/fund_consensus_latest.json',
            'recommendation_consensus': '/tmp/fund_recommendation_consensus_latest.json',
            'trades': '/tmp/fund_trade_history_latest.json',
        },
        'ui_contract': {
            'primary_endpoint': '/api/research/fund/org/latest',
            'detail_endpoints': ['/api/research/fund/performance/latest', '/api/research/fund/trades/latest'],
        },
        'next_actions': [
            'Keep fund sub-org as a separate managed research unit with one summary contract.',
            'Use fund consensus as recommendation overlay only; do not bypass critic/risk/committee gates.',
            'Add concentration/exposure effectiveness metrics when live fund holdings are mature.',
        ],
        'fund_org_summary': fund_org_summary,
        'paper_fund_league_summary': report.get('paper_fund_league_summary') or {},
        'paper_fund_historical_replay_summary': report.get('paper_fund_historical_replay_summary') or {},
        'paper_fund_price_replay_summary': report.get('paper_fund_price_replay_summary') or {},
    }
    return fund_org_packet
