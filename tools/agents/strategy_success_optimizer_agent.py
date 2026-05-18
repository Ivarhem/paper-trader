#!/usr/bin/env python3
from __future__ import annotations
import json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.config import get_settings
from app.database import init_db, list_strategy_registry
from tools.agents.lib.agent_contract import attach_contract


def f(v, default=0.0):
    try:
        return float(v if v is not None else default)
    except Exception:
        return default


def load_short_horizon_profile(path='/tmp/short_horizon_profit_profile_latest.json') -> dict:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return {}


def short_horizon_adjustment_hint(profile: dict, logic: str) -> dict | None:
    # Observation-only input. The optimizer may use it to choose a retest target,
    # but auditor acceptance still decides whether recommendation_agent applies it.
    by_h=(profile.get('by_horizon') or {})
    candidates=[]
    for horizon_key, min_hit, min_delta in [('5', 22.0, 8.0), ('2', 18.0, 8.0)]:
        hbucket=(by_h.get(horizon_key) or {})
        hdata=((hbucket.get('by_logic') or {}).get(logic) or hbucket.get(logic) or {})
        if not hdata:
            continue
        base=f(hdata.get('target_hit_pct'))
        for pp, field in [(1.0,'target_minus_1_pct_point_hit_pct'), (1.5,'target_minus_1_5_pct_points_hit_pct'), (2.0,'target_minus_2_pct_points_hit_pct')]:
            hit=f(hdata.get(field))
            delta=hit-base
            if hit >= min_hit and delta >= min_delta:
                candidates.append({'horizon_days': int(horizon_key), 'target_return_adjustment_pct_points': pp, 'hit_pct': round(hit,2), 'original_target_hit_pct': round(base,2), 'hit_delta_pct': round(delta,2), 'profile': hdata.get('adjusted_target_profile') or hdata.get('profile'), 'field': field})
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x['horizon_days'] == 5, x['hit_delta_pct'], x['hit_pct'], -x['target_return_adjustment_pct_points']), reverse=True)
    return candidates[0]


def classify(row: dict) -> dict:
    summary = row.get('summary') or {}
    samples = int(row.get('samples') or 0)
    success = f(row.get('success_rate_pct'))
    avg_excess = f(row.get('avg_excess_return_pct'))
    recent_success = f(row.get('recent_success_rate_pct'))
    recent_excess = f(row.get('recent_avg_excess_return_pct'))
    excess_win = f(summary.get('excess_win_rate_pct'))
    evaluation_sample_count = int(summary.get('evaluation_sample_count') or 0)
    raw_evaluation_success = summary.get('evaluation_success_rate_pct')
    evaluation_success = f(raw_evaluation_success, success) if evaluation_sample_count >= min(30, max(5, samples // 4)) else success
    aggregate_quality = f(summary.get('aggregate_quality_score'))
    expected_excess_value = f(summary.get('expected_excess_value_pct'), avg_excess)
    p10_excess = f(summary.get('p10_excess_return_pct'))
    p25_excess = f(summary.get('p25_excess_return_pct'))
    payoff = summary.get('payoff_profile') or {}
    payoff_class = payoff.get('class') or 'unknown'
    concentration = f(summary.get('symbol_concentration_pct'), 100)
    opportunity_count = int(summary.get('opportunity_count') or 0)
    signal_rate = f(summary.get('signal_rate_pct'), 100)
    reason = row.get('reason') or ''
    flags = []
    if samples < 80:
        flags.append('low_sample')
    if success < 35:
        flags.append('low_execution_success_rate')
    if evaluation_sample_count and evaluation_sample_count < min(30, max(5, samples // 4)):
        flags.append('thin_evaluation_success_sample')
    if evaluation_success < 45:
        flags.append('low_evaluation_success_rate')
    if aggregate_quality < 52:
        flags.append('low_aggregate_quality')
    if avg_excess <= 0:
        flags.append('non_positive_excess')
    if expected_excess_value <= 0:
        flags.append('negative_expected_excess_value')
    if p10_excess < -6:
        flags.append('left_tail_risk')
    if concentration > 45:
        flags.append('symbol_concentration_risk')
    if opportunity_count >= 80 and signal_rate < 2:
        flags.append('extremely_low_signal_rate')
    elif opportunity_count >= 80 and signal_rate < 5:
        flags.append('low_signal_rate')
    if recent_excess < -2:
        flags.append('recent_decay')
    if excess_win < 50:
        flags.append('weak_excess_win_rate')
    if 'floor guardrail' in reason:
        flags.append('active_floor_preserved')
    flags.extend([x for x in (payoff.get('flags') or []) if x not in flags])
    signal_rate_ok = opportunity_count < 80 or signal_rate >= 5
    severe_tail_or_ev = expected_excess_value < -3 or p10_excess < -10 or (expected_excess_value < 0 and p10_excess < -8)
    recommendation_enabled = signal_rate_ok and samples >= 80 and avg_excess > 0 and expected_excess_value > -1 and p10_excess > -10 and concentration <= 65 and recent_excess >= -4 and payoff_class not in ('overfit_or_noise','fragile_alpha') and not severe_tail_or_ev
    high_confidence_historical = signal_rate_ok and samples >= 150 and aggregate_quality >= 68 and expected_excess_value > 0 and p10_excess > -6 and concentration <= 45 and evaluation_success >= 50 and recent_success >= 35 and recent_excess >= -1.5 and payoff_class == 'high_conviction'
    if high_confidence_historical:
        tier = 'high_confidence_historical'
        action = 'allow_candidate_buy_zone_research'
    elif recommendation_enabled:
        tier = 'research_only'
        action = 'allow_watch_candidates_only'
    elif severe_tail_or_ev and samples >= 80 and avg_excess > 0:
        tier = 'repair_only'
        action = 'route_exit_policy_retest_watch_only'
    elif samples >= 80:
        tier = 'disabled'
        action = 'disable_for_new_recommendations'
    else:
        tier = 'needs_more_samples'
        action = 'validate_more_before_use'
    return {
        'logic': row.get('logic'),
        'status': row.get('status'),
        'samples': samples,
        'success_rate_pct': success,
        'avg_excess_return_pct': avg_excess,
        'recent_success_rate_pct': recent_success,
        'recent_avg_excess_return_pct': recent_excess,
        'excess_win_rate_pct': excess_win,
        'market_profile': summary.get('market_profile') or {},
        'evaluation_success_rate_pct': evaluation_success,
        'raw_evaluation_success_rate_pct': raw_evaluation_success,
        'evaluation_sample_count': evaluation_sample_count,
        'aggregate_quality_score': aggregate_quality,
        'expected_excess_value_pct': expected_excess_value,
        'p10_excess_return_pct': p10_excess,
        'p25_excess_return_pct': p25_excess,
        'payoff_profile': payoff,
        'payoff_class': payoff_class,
        'technical_signal_role': payoff.get('technical_signal_role'),
        'position_size_hint': payoff.get('position_size_hint'),
        'symbol_concentration_pct': concentration,
        'opportunity_count': opportunity_count,
        'signal_rate_pct': signal_rate,
        'tier': tier,
        'recommended_action': action,
        'recommendation_enabled': recommendation_enabled,
        'severe_tail_or_ev_guard': severe_tail_or_ev,
        'high_confidence_historical': high_confidence_historical,
        # Backward-compatible alias; this is NOT real-trading eligibility.
        'trade_eligible_strategy': high_confidence_historical,
        'flags': flags,
        'score': round(aggregate_quality * 0.8 + expected_excess_value * 8 + recent_excess * 2 + min(10, samples / 40), 2),
    }


def symbol_edges(conn: sqlite3.Connection, logic: str, limit=8) -> dict:
    rows = conn.execute(
        """
        SELECT symbol,
               COUNT(*) AS samples,
               AVG(CASE WHEN result='success' THEN 1.0 ELSE 0.0 END) * 100 AS success_rate_pct,
               AVG(excess_return_pct) AS avg_excess_return_pct
        FROM recommendation_validation_results
        WHERE logic = ? AND action = 'candidate_buy_zone'
        GROUP BY symbol
        HAVING COUNT(*) >= 3
        ORDER BY avg_excess_return_pct DESC
        """,
        (logic,),
    ).fetchall()
    strengths = [dict(r) for r in rows[:limit]]
    weaknesses = [dict(r) for r in rows[-limit:]]
    blocked = []
    preferred = []
    for r in rows:
        d = dict(r)
        samples = int(d.get('samples') or 0)
        sr = f(d.get('success_rate_pct'))
        ex = f(d.get('avg_excess_return_pct'))
        if samples >= 4 and (ex <= -1.0 or sr <= 20):
            blocked.append({'symbol': d.get('symbol'), 'samples': samples, 'success_rate_pct': round(sr, 2), 'avg_excess_return_pct': round(ex, 2), 'reason': 'historical symbol edge weak for this strategy'})
        elif samples >= 4 and ex >= 3.0 and sr >= 45:
            preferred.append({'symbol': d.get('symbol'), 'samples': samples, 'success_rate_pct': round(sr, 2), 'avg_excess_return_pct': round(ex, 2), 'reason': 'historical symbol edge strong for this strategy'})
    return {'strengths': strengths, 'weaknesses': list(reversed(weaknesses)), 'blocked_symbols': blocked[:25], 'preferred_symbols': preferred[:25]}


def main():
    init_db()
    strategies = list_strategy_registry()
    rows = [classify(r) for r in strategies]
    by_logic = {r['logic']: r for r in rows if r.get('logic')}
    active = [r for r in rows if r.get('status') in ('active','repair_active','validation_active')]
    strict_active = [r for r in rows if r.get('status') == 'active']
    repair_active = [r for r in rows if r.get('status') in ('repair_active','validation_active')]
    with sqlite3.connect(get_settings().database_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in rows:
            if row.get('logic') and row.get('status') in ('active','repair_active','validation_active'):
                row['symbol_edges'] = symbol_edges(conn, row['logic'], limit=5)
    summary = {
        'strategy_count': len(rows),
        'active_count': len(strict_active),
        'repair_active_count': len(repair_active),
        'effective_research_active_count': len(active),
        'high_confidence_historical_active_count': sum(1 for r in active if r.get('high_confidence_historical')),
        # Backward-compatible alias; not real trading.
        'trade_eligible_active_count': sum(1 for r in active if r.get('high_confidence_historical')),
        'research_only_active_count': sum(1 for r in active if r.get('tier') == 'research_only'),
        'disabled_active_count': sum(1 for r in active if r.get('tier') in ('disabled', 'needs_more_samples')),
        'repair_only_active_count': sum(1 for r in active if r.get('tier') == 'repair_only'),
        'tier_counts': {tier: sum(1 for r in rows if r.get('tier') == tier) for tier in ('high_confidence_historical', 'research_only', 'repair_only', 'disabled', 'needs_more_samples')},
    }
    action_plan = {'demote_or_isolate_logics': [], 'watch_only_logics': [], 'blocked_logic_symbols': [], 'preferred_logic_symbols': [], 'blocked_logic_markets': [], 'preferred_logic_markets': [], 'target_adjustments': [], 'validation_priorities': []}
    short_horizon_profile = load_short_horizon_profile()
    for row in rows:
        logic = row.get('logic')
        if not logic:
            continue
        if row.get('tier') in ('disabled', 'needs_more_samples'):
            action_plan['demote_or_isolate_logics'].append({'logic': logic, 'tier': row.get('tier'), 'flags': row.get('flags'), 'reason': 'fails historical recommendation_enabled gate'})
        elif row.get('tier') == 'repair_only':
            action_plan['demote_or_isolate_logics'].append({'logic': logic, 'tier': row.get('tier'), 'flags': row.get('flags'), 'reason': 'profit guard repair-only: exit-policy retest required before recommendation use'})
            action_plan['validation_priorities'].append({'logic': logic, 'reason': 'repair_only_strategy_needs_exit_policy_retest', 'expected_excess_value_pct': row.get('expected_excess_value_pct'), 'p10_excess_return_pct': row.get('p10_excess_return_pct')})
        elif row.get('tier') == 'research_only':
            action_plan['watch_only_logics'].append({'logic': logic, 'tier': row.get('tier'), 'flags': row.get('flags'), 'reason': 'passes minimum historical gate but not high-confidence historical gate'})
        for mk, mp in (row.get('market_profile') or {}).items():
            ms=int(mp.get('samples') or 0); mex=f(mp.get('avg_excess_return_pct')); msr=f(mp.get('success_rate_pct')); mev=mp.get('evaluation_success_rate_pct')
            if ms >= 80 and (mex <= 0 or (mex < 0.5 and mev is not None and f(mev) < 43)):
                action_plan['blocked_logic_markets'].append({'logic': logic, 'market': mk, 'samples': ms, 'success_rate_pct': round(msr,2), 'evaluation_success_rate_pct': mev, 'avg_excess_return_pct': round(mex,2), 'reason': 'historical market edge weak for this strategy'})
            elif ms >= 80 and mex >= 1.0 and (mev is None or f(mev) >= 47):
                action_plan['preferred_logic_markets'].append({'logic': logic, 'market': mk, 'samples': ms, 'success_rate_pct': round(msr,2), 'evaluation_success_rate_pct': mev, 'avg_excess_return_pct': round(mex,2), 'reason': 'historical market edge strong for this strategy'})
            # Target adjustment is allowed only when the market edge is positive.
            # It is a paper/research exit-policy hint, not a success-rate-only shortcut.
            if row.get('status') in ('active','repair_active','validation_active') and ms >= 120 and mex > 0.75 and msr < 38:
                hint = short_horizon_adjustment_hint(short_horizon_profile, logic)
                scale = 0.80 if msr < 34 else 0.90
                if mex >= 2.0 and msr < 35:
                    scale = 0.75
                adjustment={'logic': logic, 'market': mk, 'target_scale': scale, 'samples': ms, 'success_rate_pct': round(msr,2), 'avg_excess_return_pct': round(mex,2), 'evaluation_success_rate_pct': mev, 'reason': 'positive market excess but low target-hit rate; lower target for watch-only repair/exit-policy retest while monitoring EV/excess'}
                if hint:
                    adjustment.update({'target_return_adjustment_pct_points': hint['target_return_adjustment_pct_points'], 'target_adjustment_basis': 'short_horizon_profit_profile', 'short_horizon_hint': hint, 'reason': 'positive market excess plus short-horizon adjusted-target evidence; retest return-point target adjustment while monitoring EV/excess'})
                action_plan['target_adjustments'].append(adjustment)
        edges = row.get('symbol_edges') or {}
        for x in edges.get('blocked_symbols') or []:
            action_plan['blocked_logic_symbols'].append({'logic': logic, **x})
        for x in edges.get('preferred_symbols') or []:
            action_plan['preferred_logic_symbols'].append({'logic': logic, **x})
        for x in (edges.get('strengths') or [])[:5]:
            samples = int(x.get('samples') or 0)
            avg_excess = f(x.get('avg_excess_return_pct'))
            if samples < 60 and avg_excess >= 3.0:
                action_plan['validation_priorities'].append({
                    'logic': logic,
                    'symbol': x.get('symbol'),
                    'reason': 'promising symbol edge needs more samples before any strategy promotion',
                    'avg_excess_return_pct': x.get('avg_excess_return_pct'),
                    'success_rate_pct': x.get('success_rate_pct'),
                    'samples': samples,
                })
        for x in (edges.get('weaknesses') or [])[:3]:
            action_plan['validation_priorities'].append({'logic': logic, 'symbol': x.get('symbol'), 'reason': 'active strategy weakness needs more validation/repair', 'avg_excess_return_pct': x.get('avg_excess_return_pct'), 'samples': x.get('samples')})
    recommendations = []
    if summary['high_confidence_historical_active_count'] == 0:
        recommendations.append('No active strategy currently meets high-confidence historical validation gates; generated candidates remain watch-only.')
    if action_plan['demote_or_isolate_logics']:
        recommendations.append('Lifecycle should demote/isolate strategies that fail historical recommendation_enabled gates.')
    if action_plan['blocked_logic_symbols']:
        recommendations.append('Recommendation generation should skip weak strategy-symbol historical edges and prefer strong edges when available.')
    recommendations.append('Prioritize validation for current recommendation symbols plus promising/weak symbol edges before any promotion.')
    packet = {
        'run_at': datetime.now(timezone.utc).isoformat(),
        'mode': 'historical_validation_strategy_success_optimizer',
        'real_trading': False,
        'summary': summary,
        'logic_gates': by_logic,
        'active': sorted(active, key=lambda x: x.get('score') or 0, reverse=True),
        'source': 'simulation_validation_worker historical recommendation_validation_results',
        'action_plan': action_plan,
        'recommendations': recommendations,
    }
    status = 'ok' if summary['high_confidence_historical_active_count'] > 0 else ('degraded' if summary.get('effective_research_active_count', 0) == 0 else 'watch')
    attach_contract(
        packet,
        'strategy_success_optimizer',
        status=status,
        outputs={'summary': summary, 'logic_gate_count': len(by_logic), 'blocked_logic_symbol_count': len(action_plan['blocked_logic_symbols']), 'target_adjustment_count': len(action_plan['target_adjustments'])},
        metrics=summary,
        warnings=[] if status == 'ok' else ['no high-confidence historical active strategy'] + ([] if summary.get('effective_research_active_count', 0) else ['no effective research-active strategy']),
        next_actions=recommendations,
    )
    Path('/tmp/strategy_success_optimizer_latest.json').write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(packet, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
