#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.database import init_db, list_universe_members
from tools.agents.lib.agent_contract import attach_contract
from tools.agents.recommendation_auditor import LOGICS, logic_config


def load_recommendations(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'items': [], '_read_error': str(exc)}



def load_guardian_patch_context() -> dict:
    try:
        guardian = json.loads(Path('/tmp/org_improvement_guardian_latest.json').read_text(encoding='utf-8'))
    except Exception:
        return {'dominant_critic_symbol_edge_mode': False}
    for proposal in guardian.get('patch_proposals') or []:
        if proposal.get('title') != 'Expand symbol-edge validation samples for dominant critic bottleneck':
            continue
        evidence = proposal.get('evidence') or {}
        issue = evidence.get('dominant_critic_issue') or {}
        return {
            'dominant_critic_symbol_edge_mode': True,
            'dominant_critic_issue': issue.get('issue'),
            'critic_high': evidence.get('critic_high'),
            'recent_repeat_count': evidence.get('recent_repeat_count'),
        }
    return {'dominant_critic_symbol_edge_mode': False}


def recommendation_sample_expansion_context(items: list[dict]) -> dict:
    """Detect evidence bottlenecks without relaxing trade or promotion gates."""
    if not items:
        return {'sample_expansion_mode': False}
    total = len(items)
    trade_eligible = 0
    undersampled = 0
    no_positive_edge = 0
    high_critic = 0
    committee_blocked = 0
    for row in items:
        gate = row.get('trade_gate') or {}
        if gate.get('trade_eligible') or row.get('trade_eligible'):
            trade_eligible += 1
        basis = row.get('validation_basis') or {}
        samples = int(basis.get('symbol_validation_sample_count') or 0)
        positive_edges = int(basis.get('positive_symbol_edge_count') or 0)
        if samples < 10:
            undersampled += 1
        if positive_edges <= 0:
            no_positive_edge += 1
        critic = row.get('critic') or {}
        if critic.get('severity') == 'high':
            high_critic += 1
        syn = (row.get('investment_committee') or {}).get('synthesis') or {}
        if syn.get('decision') in ('reject', 'watch'):
            committee_blocked += 1
        risk_gate = syn.get('risk_gate') or {}
        if risk_gate.get('decision') in ('blocked', 'needs_more_validation') or risk_gate.get('under_validated'):
            committee_blocked += 1

    dominant_blocked = max(undersampled, no_positive_edge, high_critic, committee_blocked)
    sample_expansion = (
        total >= 10
        and trade_eligible == 0
        and (
            undersampled >= max(3, total // 4)
            or no_positive_edge >= max(5, total // 2)
            or committee_blocked >= max(5, total // 2)
            or high_critic >= max(3, total // 5)
        )
    )
    return {
        'sample_expansion_mode': sample_expansion,
        'reason': 'trade_eligible_zero_with_symbol_edge_or_committee_bottleneck' if sample_expansion else None,
        'total_recommendations': total,
        'trade_eligible_count': trade_eligible,
        'undersampled_symbol_count': undersampled,
        'no_positive_symbol_edge_count': no_positive_edge,
        'critic_high_count': high_critic,
        'committee_blocked_signal_count': committee_blocked,
        'dominant_blocked_count': dominant_blocked,
    }


def active_universe_symbols(limit: int) -> list[str]:
    try:
        rows = list_universe_members(status='active')
    except Exception:
        return []
    symbols = []
    for row in rows:
        sym = str(row.get('symbol') or '').upper().strip()
        if sym and sym not in symbols:
            symbols.append(sym)
        if limit and len(symbols) >= limit:
            break
    return symbols


ROLE_LOGIC_FAMILY_HINTS = {
    'volume_breakout': ('technical_volume_breakout', 'supply_close_strength', 'quality_breakout'),
    'supply_close_strength': ('supply_close_strength', 'technical_volume_breakout'),
    'momentum_continuation': ('us_momentum', 'relative_strength_persistence', 'technical_ma_trend', 'stable_relative_strength'),
    'trend_following': ('technical_ma_trend', 'stable_relative_strength', 'us_momentum'),
    'pullback_reversion': ('pullback_in_uptrend', 'quality_pullback_uptrend'),
    'pullback_in_uptrend': ('pullback_in_uptrend', 'quality_pullback_uptrend'),
    'oversold_recovery': ('technical_rsi_reversion',),
    'range_reversion': ('range_baseline', 'range_grid_v1'),
    'mean_reversion': ('technical_rsi_reversion', 'range_grid_v1'),
    'range_breakout': ('quality_breakout', 'volatility_contraction_breakout', 'technical_volume_breakout'),
    'low_volatility_guard': ('stable_relative_strength', 'volatility_contraction_breakout'),
    'defensive_trend': ('stable_relative_strength', 'technical_ma_trend'),
}


def append_unique(items: list[str], values: list[str], limit: int | None = None) -> None:
    for value in values:
        value = str(value or '').strip()
        if value and value not in items:
            items.append(value)
        if limit and len(items) >= limit:
            return




def alpha_fast_lane_targets(symbol_limit: int, logic_limit: int) -> tuple[list[str], list[str], dict]:
    try:
        data = json.loads(Path('/tmp/alpha_fast_lane_latest.json').read_text(encoding='utf-8'))
    except Exception:
        return [], [], {'available': False}
    symbols = [str(x or '').upper().strip() for x in (data.get('symbols') or []) if str(x or '').strip()]
    logics = [str(x or '').strip() for x in (data.get('logics') or []) if str(x or '').strip()]
    summary = data.get('summary') or {}
    return symbols[:symbol_limit], logics[:logic_limit], {
        'available': True,
        'run_at': data.get('run_at'),
        'candidate_count': summary.get('candidate_count'),
        'fast_lane_candidate_count': summary.get('fast_lane_candidate_count'),
        'selected_symbols': symbols[:symbol_limit],
        'selected_logics': logics[:logic_limit],
    }


def fund_consensus_symbols(limit: int) -> tuple[list[str], dict]:
    symbols: list[str] = []
    meta = {'symbol_consensus_count': 0, 'recommendation_consensus_count': 0}
    try:
        rec = json.loads(Path('/tmp/fund_recommendation_consensus_latest.json').read_text(encoding='utf-8'))
        rows = sorted(rec.get('items') or [], key=lambda x: float(x.get('weighted_score') or x.get('score') or 0), reverse=True)
        meta['recommendation_consensus_count'] = len(rows)
        append_unique(symbols, [r.get('symbol') for r in rows], limit)
    except Exception:
        pass
    try:
        cons = json.loads(Path('/tmp/fund_consensus_latest.json').read_text(encoding='utf-8'))
        rows = sorted(cons.get('symbol_consensus') or [], key=lambda x: (int(x.get('votes') or 0), float(x.get('weighted_score') or 0)), reverse=True)
        meta['symbol_consensus_count'] = len(rows)
        append_unique(symbols, [r.get('symbol') for r in rows], limit)
    except Exception:
        pass
    meta['selected_symbols'] = symbols[:limit]
    return symbols[:limit], meta


def fund_role_logics(limit: int) -> tuple[list[str], dict]:
    roles: list[str] = []
    meta = {'source': 'fund_performance_evaluator', 'roles': []}
    try:
        perf = json.loads(Path('/tmp/fund_performance_evaluator_latest.json').read_text(encoding='utf-8'))
        top = sorted(perf.get('strategy_role_quality') or [], key=lambda x: (float(x.get('avg_return_pct') or 0), float(x.get('positive_rate_pct') or 0), int(x.get('trade_count') or 0)), reverse=True)
        append_unique(roles, [r.get('strategy_role') for r in top], 12)
        for fund in sorted(perf.get('evaluations') or [], key=lambda x: float(x.get('return_pct') or 0), reverse=True)[:12]:
            mix = fund.get('strategy_mix') or {}
            append_unique(roles, [k for k, _ in sorted(mix.items(), key=lambda kv: int(kv[1] or 0), reverse=True)], 12)
    except Exception:
        pass
    families: list[str] = []
    for role in roles:
        append_unique(families, list(ROLE_LOGIC_FAMILY_HINTS.get(role) or (role,)), None)
    selected: list[str] = []
    for hint in families:
        matches = []
        for logic, cfg in LOGICS.items():
            family = str((cfg or {}).get('family') or '')
            if family and (family.startswith(hint) or hint in family or hint in logic):
                matches.append(logic)
        append_unique(selected, matches, limit)
        if len(selected) >= limit:
            break
    if not selected:
        selected = [l for l in LOGICS if logic_config(l)][:limit]
    meta.update({'roles': roles, 'families': families, 'selected_logics': selected[:limit]})
    return selected[:limit], meta


def collect_targets(
    items: list[dict],
    symbol_limit: int,
    logic_limit: int,
    include_active_universe: bool = False,
    active_universe_limit: int = 400,
    patch_context: dict | None = None,
) -> tuple[list[str], list[str], dict]:
    bucket_rank = {'approved': 0, 'paper_buy_candidate': 1, 'research_watch': 2, 'watch': 3, 'rejected': 4}
    priority_rank = {'high': 0, 'medium': 1, 'low': 2, None: 3}
    patch_context = patch_context or load_guardian_patch_context()
    dominant_critic_mode = bool(patch_context.get('dominant_critic_symbol_edge_mode'))

    def committee_bottlenecks(row: dict) -> list[str]:
        out = []
        gate = row.get('trade_gate') or {}
        for key in gate.get('blockers') or []:
            out.append(str(key))
        for key in gate.get('cautions') or []:
            out.append(str(key))
        syn = (row.get('investment_committee') or {}).get('synthesis') or {}
        if syn.get('decision') in ('reject', 'watch'):
            out.append('committee_' + str(syn.get('decision')))
        risk_gate = syn.get('risk_gate') or {}
        if risk_gate.get('decision') in ('blocked', 'needs_more_validation'):
            out.append('risk_gate_' + str(risk_gate.get('decision')))
        if risk_gate.get('under_validated'):
            out.append('risk_gate_under_validated')
        rationale = row.get('committee_rationale') or {}
        for opp in rationale.get('opposers') or []:
            for concern in opp.get('concerns') or []:
                c = str(concern)
                if any(tok in c for tok in ('검증 샘플 부족', '초과승률 부족', '평균 초과수익', 'active 전략 평균 초과수익', 'audit 품질', '기간 안정성', '하방 꼬리', '목표/위험')):
                    out.append(c)
        return out

    def sample_deficit(row: dict) -> int:
        vb = row.get('validation_basis') or {}
        samples = int(vb.get('symbol_validation_sample_count') or 0)
        positive_edges = int(vb.get('positive_symbol_edge_count') or 0)
        critic = row.get('critic') or {}
        deficit = max(0, 10 - samples)
        if positive_edges <= 0:
            deficit += 5
        if dominant_critic_mode:
            issues = ' / '.join(str(x) for x in (critic.get('issues') or []))
            tasks = critic.get('validation_tasks') or []
            if 'active 전략 평균 초과수익' in issues:
                deficit += 5
            if '초과승률' in issues:
                deficit += 3
            if any(t.get('task') == 'retest_positive_excess_or_replace_logic' for t in tasks):
                deficit += 4
            if samples < 10:
                deficit += 3
        for task in (row.get('critic') or {}).get('validation_tasks') or []:
            if task.get('priority') == 'high':
                deficit += 3
            elif task.get('priority') == 'medium':
                deficit += 1
        if vb.get('thin_no_edge_gate'):
            deficit += 4
        bottlenecks = committee_bottlenecks(row)
        if any(x in bottlenecks for x in ('committee_reject', 'risk_gate_blocked')):
            deficit += 4
        if any(x in bottlenecks for x in ('risk_gate_needs_more_validation', 'risk_gate_under_validated')):
            deficit += 5
        if any(('검증 샘플 부족' in x or '초과승률 부족' in x or '평균 초과수익' in x or 'active 전략 평균 초과수익' in x or 'audit 품질' in x) for x in bottlenecks):
            deficit += 3
        if any(('하방 꼬리' in x or '목표/위험' in x or '기간 안정성' in x) for x in bottlenecks):
            deficit += 2
        return deficit

    ranked = sorted(
        items,
        key=lambda r: (
            priority_rank.get(r.get('validation_priority'), 3),
            -sample_deficit(r),
            bucket_rank.get(r.get('recommendation_bucket'), 2),
            0 if r.get('action') == 'candidate_buy_zone' else 1,
            -float(r.get('score') or 0),
        ),
    )
    symbols = []
    logics = []
    deficits = []
    for row in ranked:
        sym = row.get('symbol')
        deficit = sample_deficit(row)
        if sym and deficit > 0:
            vb = row.get('validation_basis') or {}
            deficits.append({'symbol': sym, 'deficit_score': deficit, 'samples': vb.get('symbol_validation_sample_count'), 'positive_edges': vb.get('positive_symbol_edge_count'), 'priority': row.get('validation_priority'), 'bucket': row.get('recommendation_bucket'), 'committee_bottlenecks': committee_bottlenecks(row)[:8], 'critic_tasks': (row.get('critic') or {}).get('validation_tasks') or []})
        if sym and sym not in symbols:
            symbols.append(sym)
        for logic in [row.get('best_logic'), row.get('logic'), row.get('strategy_id'), row.get('source_strategy_id')]:
            if logic and logic not in logics:
                logics.append(logic)
        for sig in sorted(row.get('signals') or [], key=lambda x: (int(x.get('symbol_samples') or 0), 0 if x.get('symbol_edge_bucket') in ('live_thin', 'missing') else 1, -float(x.get('score') or 0))):
            logic = sig.get('logic')
            if logic and logic not in logics:
                logics.append(logic)
        if len(symbols) >= symbol_limit and len(logics) >= logic_limit:
            break
    try:
        ctx = json.loads(Path('/tmp/market_context_latest.json').read_text(encoding='utf-8'))
        for theme in (ctx.get('themes') or {}).values():
            if theme.get('expected_impact') == 'positive' or (theme.get('impact_score') or 0) >= 62:
                for sym in theme.get('affected_symbols') or []:
                    if sym and sym not in symbols:
                        symbols.append(sym)
                    if len(symbols) >= symbol_limit:
                        break
    except Exception:
        pass
    try:
        scout = json.loads(Path('/tmp/market_issue_scout_latest.json').read_text(encoding='utf-8'))
        for issue in scout.get('issues') or []:
            if (issue.get('impact_score') or 0) >= 65:
                for sym in issue.get('affected_symbols') or []:
                    if sym and sym not in symbols:
                        symbols.append(sym)
                    if len(symbols) >= symbol_limit:
                        break
    except Exception:
        pass
    mover_symbols = []
    try:
        mover = json.loads(Path('/tmp/market_mover_seed_latest.json').read_text(encoding='utf-8'))
        rows = sorted([x for x in (mover.get('top_stock_items') or mover.get('items') or []) if x.get('probable_stock') is not False], key=lambda x: abs(float(x.get('change_pct') or 0)), reverse=True)
        for row in rows:
            sym = row.get('symbol')
            if sym and sym not in mover_symbols:
                mover_symbols.append(sym)
            if len(mover_symbols) >= max(3, symbol_limit // 3):
                break
    except Exception:
        pass
    investor_symbols = []
    try:
        investor = json.loads(Path('/tmp/investor_flow_seed_latest.json').read_text(encoding='utf-8'))
        for row in investor.get('top_symbols') or []:
            sym = row.get('symbol')
            if sym and sym not in investor_symbols:
                investor_symbols.append(sym)
            if len(investor_symbols) >= max(3, symbol_limit // 3):
                break
    except Exception:
        pass
    if mover_symbols or investor_symbols:
        combined_seed = []
        for sym in mover_symbols + investor_symbols:
            if sym not in combined_seed:
                combined_seed.append(sym)
        reserved = max(3, min(len(combined_seed), symbol_limit // 3))
        base = symbols[:max(0, symbol_limit - reserved)]
        symbols = base + [s for s in combined_seed if s not in base]
    alpha_symbols, alpha_logics, alpha_meta = alpha_fast_lane_targets(max(8, symbol_limit // 2), max(4, logic_limit // 2))
    if alpha_symbols:
        symbols = alpha_symbols + [s for s in symbols if s not in alpha_symbols]
    if alpha_logics:
        merged = []
        append_unique(merged, alpha_logics, logic_limit)
        append_unique(merged, logics, logic_limit)
        logics = merged
    fund_symbols, fund_symbol_meta = fund_consensus_symbols(max(6, symbol_limit // 2))
    if fund_symbols:
        append_unique(symbols, fund_symbols, None)
    priority_symbols = symbols[:symbol_limit]
    active_symbols = active_universe_symbols(active_universe_limit) if include_active_universe else []
    if active_symbols:
        for sym in active_symbols:
            if sym not in symbols:
                symbols.append(sym)

    fund_logics, fund_logic_meta = fund_role_logics(max(4, logic_limit // 2))
    if fund_logics:
        merged_logics = []
        append_unique(merged_logics, logics, logic_limit)
        append_unique(merged_logics, fund_logics, logic_limit)
        logics = merged_logics

    sample_context = (patch_context or {}).get('sample_expansion_context') or {}
    selection_parts = ['current_recommendation_and_fund_consensus_symbols_then_fund_role_logics_without_relaxing_gates']
    if sample_context.get('sample_expansion_mode'):
        selection_parts.append(sample_expansion_reason := str(sample_context.get('reason') or 'sample_expansion_mode'))
        if 'committee' in sample_expansion_reason:
            selection_parts.append('committee_bottleneck_feedback_loop_active')
    if (patch_context or {}).get('dominant_critic_symbol_edge_mode'):
        selection_parts.append('dominant_critic_symbol_edge_feedback_loop_active')

    priority_meta = {
        'under_sampled_recommendations': deficits[:symbol_limit],
        'selection_policy': '|'.join(selection_parts),
        'sample_expansion_context': sample_context,
        'guardian_patch_context': patch_context,
        'alpha_fast_lane_meta': alpha_meta,
        'fund_consensus_symbol_meta': fund_symbol_meta,
        'fund_role_logic_meta': fund_logic_meta,
        'priority_symbol_count': len(priority_symbols),
        'active_universe_included': bool(active_symbols),
        'active_universe_symbol_count': len(active_symbols),
        'active_universe_limit': active_universe_limit,
    }
    return symbols, logics[:logic_limit], priority_meta

def main():
    ap = argparse.ArgumentParser(description='Prioritize validation backlog for current recommendation candidates')
    ap.add_argument('--batch-size', type=int, default=260)
    ap.add_argument('--symbol-limit', type=int, default=18)
    ap.add_argument('--logic-limit', type=int, default=10)
    ap.add_argument('--include-active-universe', action='store_true', help='Append the active universe to the validation seed pool after priority recommendation symbols.')
    ap.add_argument('--active-universe-limit', type=int, default=400)
    ap.add_argument('--monthly-from', default='2024-01-01')
    ap.add_argument('--monthly-step', type=int, default=1)
    ap.add_argument('--horizons', default='20,40,60')
    ap.add_argument('--recommendations', default='/tmp/recommendations_latest.json')
    ap.add_argument('--output', default='/tmp/current_recommendation_validation_latest.json')
    ap.add_argument('--fund-consensus-boost', action='store_true', default=True, help='Prioritize top fund consensus symbols and fund role-derived logic families.')
    args = ap.parse_args()
    init_db()

    recs = load_recommendations(Path(args.recommendations))
    patch_context = load_guardian_patch_context()
    rec_items = recs.get('items') or []
    expansion_context = recommendation_sample_expansion_context(rec_items)
    patch_context['sample_expansion_context'] = expansion_context
    if patch_context.get('dominant_critic_symbol_edge_mode') or expansion_context.get('sample_expansion_mode'):
        # Route more capacity toward repeated symbol-edge/average-excess bottlenecks.
        # This remains historical validation only; it does not relax promotion/trade gates.
        args.batch_size = max(args.batch_size, 900)
        args.symbol_limit = max(args.symbol_limit, 32)
        args.logic_limit = max(args.logic_limit, 18)
    symbols, logics, priority_meta = collect_targets(
        rec_items,
        args.symbol_limit,
        args.logic_limit,
        include_active_universe=args.include_active_universe,
        active_universe_limit=args.active_universe_limit,
        patch_context=patch_context,
    )
    packet = {
        'run_at': datetime.now(timezone.utc).isoformat(),
        'mode': 'current_recommendation_validation_worker',
        'real_trading': False,
        'symbols': symbols,
        'logics': logics,
        'batch_size': args.batch_size,
        'source_run_at': recs.get('run_at'),
        'priority_meta': priority_meta,
    }
    if not symbols or not logics:
        packet['worker'] = None
        attach_contract(packet, 'current_recommendation_validation_worker', status='degraded', warnings=['no current recommendation symbols/logics to validate'], outputs={'symbols': symbols, 'logics': logics})
        Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(packet, ensure_ascii=False, indent=2))
        return

    worker_output = '/tmp/current_recommendation_simulation_validation_latest.json'
    cmd = [
        sys.executable, 'tools/agents/simulation_validation_worker.py',
        '--symbols', ','.join(symbols),
        '--logics', ','.join(logics),
        '--batch-size', str(args.batch_size),
        '--monthly-from', args.monthly_from,
        '--monthly-step', str(args.monthly_step),
        '--horizons', args.horizons,
        '--output', worker_output,
    ]
    worker_timeout = max(240, min(1200, args.batch_size * 2))
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=worker_timeout)
    worker = {}
    try:
        worker = json.loads(Path(worker_output).read_text(encoding='utf-8'))
    except Exception as exc:
        worker = {'_read_error': str(exc)}
    packet.update({
        'cmd': cmd,
        'worker_timeout_seconds': worker_timeout,
        'returncode': proc.returncode,
        'stdout_tail': proc.stdout[-3000:],
        'stderr_tail': proc.stderr[-3000:],
        'worker': worker,
    })
    processed = int(worker.get('processed_combinations') or 0)
    status = 'ok' if proc.returncode == 0 and processed > 0 else 'degraded'
    warnings = []
    if proc.returncode != 0:
        warnings.append(f'worker exited {proc.returncode}')
    if processed <= 0:
        warnings.append('no prioritized validation combinations processed')
    attach_contract(
        packet,
        'current_recommendation_validation_worker',
        status=status,
        inputs={'symbols': symbols, 'logics': logics, 'batch_size': args.batch_size},
        outputs={'processed_combinations': processed, 'saved': worker.get('saved'), 'priority_meta': priority_meta},
        metrics={'symbol_count': len(symbols), 'logic_count': len(logics), 'processed_combinations': processed, 'under_sampled_recommendation_count': len(priority_meta.get('under_sampled_recommendations') or [])},
        warnings=warnings,
        next_actions=['Check current recommendations/logics if prioritized backlog is empty.'] if warnings else [],
    )
    Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    if proc.returncode != 0:
        sys.exit(proc.returncode)


if __name__ == '__main__':
    main()
