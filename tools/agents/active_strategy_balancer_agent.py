#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sqlite3,sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, utc_now, list_strategy_registry
from tools.agents.lib.agent_contract import attach_contract

def family(logic):
    if logic.startswith('us_'): return 'us_momentum'
    if logic.startswith('technical_ma_trend'): return 'technical_ma_trend'
    if logic.startswith('technical_volume_breakout'): return 'technical_volume_breakout'
    if logic.startswith('quality_pullback') or logic.startswith('pullback_uptrend'): return 'pullback_uptrend'
    if logic.startswith('quality_breakout') or logic.startswith('volatility_contraction'): return 'breakout'
    if logic.startswith('stable_relative_strength') or logic.startswith('relative_strength_persistence'): return 'relative_strength'
    if logic.startswith('range_grid_'): return 'range_grid'
    if 'range' in logic: return 'range_baseline'
    if 'rsi' in logic: return 'mean_reversion'
    return 'other'

def score(row):
    summary=row.get('summary') or {}
    return float(summary.get('aggregate_quality_score') or 0)*0.8 + float(summary.get('expected_excess_value_pct') or row.get('avg_excess_return_pct') or 0)*8 + float(row.get('recent_avg_excess_return_pct') or 0)*2 + min(10, int(row.get('samples') or 0)/40)

def qualifies(row):
    summary=row.get('summary') or {}
    return (int(row.get('samples') or 0) >= 50 and float(summary.get('aggregate_quality_score') or 0) >= 68 and float(summary.get('expected_excess_value_pct') or 0) > 0 and float(summary.get('p10_excess_return_pct') or 0) > -6 and float(summary.get('symbol_concentration_pct') or 100) <= 45 and float(row.get('recent_avg_excess_return_pct') or 0) >= -1.5)


def reserve_qualifies(row):
    # Paper-only reserve: preserves strategy diversity when strict gates leave too few active logics.
    # These are explicitly down-weighted/labeled downstream and are not trade-quality approvals.
    # Avoid churn: do not promote reserve candidates whose recent paper edge is negative.
    summary=row.get('summary') or {}
    return (int(row.get('samples') or 0) >= 250 and float(row.get('avg_excess_return_pct') or 0) >= 0.6 and float(row.get('recent_avg_excess_return_pct') or 0) >= 0.0 and float(summary.get('signal_rate_pct') or 100) >= 5.0 and float(summary.get('p10_excess_return_pct') or -99) > -10 and float(summary.get('p25_excess_return_pct') or -99) > -5 and float(summary.get('expected_excess_value_pct') or row.get('avg_excess_return_pct') or 0) > -1.0)

def high_upside_qualifies(row):
    # Aggressive paper-only exploration tier. This is intentionally more active
    # than the strict promotion gate: it promotes a few promising hypotheses to
    # gather forward paper evidence instead of waiting for perfect historical
    # quality. Downstream recommendations heavily down-weight and label these.
    summary=row.get('summary') or {}
    logic=row.get('logic') or ''
    samples=int(row.get('samples') or 0)
    avg=float(row.get('avg_excess_return_pct') or 0)
    recent=float(row.get('recent_avg_excess_return_pct') or 0)
    exwin=float(summary.get('excess_win_rate_pct') or 0)
    sr=float(row.get('success_rate_pct') or 0)
    expected=float(summary.get('expected_excess_value_pct') or avg or 0)
    p10=float(summary.get('p10_excess_return_pct') or -99)
    # Enough evidence + positive edge; tolerate imperfect tails for research slots.
    if samples >= 120 and sr >= 25 and avg >= 1.0 and recent >= -2.0 and expected > -1.0 and p10 > -10 and (exwin >= 48 or sr >= 35):
        return True
    # New quality-gated families can enter earlier if they show non-trivial edge.
    if logic.startswith(('quality_', 'stable_')) and samples >= 50 and avg >= 0.8 and recent >= -2.0 and expected > -1.0 and p10 > -10:
        return True
    # US momentum gets a small fast lane because active recommendations are currently US-only.
    if logic.startswith('us_') and samples >= 50 and sr >= 25 and avg >= 0.8 and recent >= -2.0 and expected > -1.0 and p10 > -10 and exwin >= 45:
        return True
    # Keep an exploratory lane for positive-EV candidates even when avg is modest.
    return samples >= 300 and expected > -1.0 and avg >= 0.6 and recent >= -2.5 and p10 > -10 and exwin >= 49

def recently_demoted_by_lifecycle(conn, logic: str, limit: int = 30) -> bool:
    try:
        rows=conn.execute("SELECT old_status,new_status,reason,event_json FROM strategy_state_events WHERE logic=? ORDER BY id DESC LIMIT ?", (logic, limit)).fetchall()
    except Exception:
        return False
    for r in rows:
        reason=(r['reason'] or '') if hasattr(r, 'keys') else (r[2] or '')
        old=(r['old_status'] if hasattr(r, 'keys') else r[0])
        new=(r['new_status'] if hasattr(r, 'keys') else r[1])
        if old == 'active' and new != 'active':
            return True
        # Stop scanning once we hit an older active promotion; only care about the latest demotion cycle.
        if old != 'active' and new == 'active':
            return False
    return False


def probationary_qualifies(row):
    # Paper-research probation tier: used when strict gates leave the active pool
    # under target. Requires broad history and benchmark edge, but tolerates weak
    # recent absolute success so promising strategies can gather live paper
    # recommendation evidence instead of the system depending on one active logic.
    summary=row.get('summary') or {}
    return (int(row.get('samples') or 0) >= 300 and float(summary.get('aggregate_quality_score') or 0) >= 52 and float(summary.get('expected_excess_value_pct') or 0) > 0.0 and float(summary.get('p10_excess_return_pct') or -99) > -8.0 and float(row.get('recent_avg_excess_return_pct') or 0) >= -3.0)

def main():
    ap=argparse.ArgumentParser(description='Guardedly promote near-qualified strategies when active pool is too small')
    ap.add_argument('--target-active', type=int, default=5)
    ap.add_argument('--max-promote', type=int, default=3)
    ap.add_argument('--output', default='/tmp/active_strategy_balancer_latest.json')
    ap.add_argument('--high-upside-slots', type=int, default=3)
    ap.add_argument('--apply-promotions', action='store_true', help='Apply promotion proposals to strategy_registry. Default is proposal-only; strategy_lifecycle is canonical status writer.')
    args=ap.parse_args(); init_db(); rows=list_strategy_registry(); active=[r for r in rows if r['status'] in ('active','repair_active','validation_active')]
    conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    candidates=[r for r in rows if r['status'] in ('watch','probation','candidate') and not recently_demoted_by_lifecycle(conn, r['logic']) and qualifies(r)]
    mode='strict'
    high_upside=[]
    if len(active)+len(candidates) < args.target_active:
        probationary=[r for r in rows if r['status'] in ('watch','probation','candidate') and r not in candidates and not recently_demoted_by_lifecycle(conn, r['logic']) and probationary_qualifies(r)]
        high_upside=[r for r in rows if r['status'] in ('watch','probation','candidate') and r not in candidates and r not in probationary and not recently_demoted_by_lifecycle(conn, r['logic']) and high_upside_qualifies(r)]
        if probationary:
            candidates.extend(probationary)
            mode='strict_plus_probationary'
        if high_upside:
            high_upside=sorted(high_upside,key=score,reverse=True)[:args.high_upside_slots]
            candidates.extend(high_upside)
            mode='strict_plus_probationary_high_upside'
    if len(active)+len(candidates) < max(2, min(args.target_active, 3)):
        reserve=[r for r in rows if r['status'] in ('watch','probation') and r not in candidates and not recently_demoted_by_lifecycle(conn, r['logic']) and reserve_qualifies(r)]
        if reserve:
            candidates.extend(sorted(reserve,key=score,reverse=True)[:max(0, min(args.target_active, 3)-len(active)-len(candidates))])
            mode='research_reserve_floor'
    candidates=sorted(candidates,key=score,reverse=True)
    # Avoid reserve-floor churn across near-duplicate siblings promoted in recent runs.
    recent_floor_families=set()
    try:
        recent=conn.execute("SELECT event_json, reason FROM strategy_state_events WHERE new_status='active' ORDER BY id DESC LIMIT 12").fetchall()
        for ev in recent:
            payload=json.loads(ev['event_json'] or '{}') if ev['event_json'] else {}
            if 'research_reserve_floor' in (ev['reason'] or ''):
                fam=payload.get('family')
                if fam: recent_floor_families.add(fam)
    except Exception:
        recent_floor_families=set()
    if mode == 'research_reserve_floor' and recent_floor_families:
        candidates=[r for r in candidates if family(r['logic']) not in recent_floor_families]
    # Avoid filling the paper active set with near-identical variants. If the
    # existing active set already covers a family, do not promote another member
    # from that family in this run.
    diversified=[]; used_families={family(r['logic']) for r in active}
    for r in candidates:
        f=family(r['logic'])
        is_high_upside = r in high_upside
        # Allow the isolated high-upside slot to coexist with a core strategy
        # from the same broad family; it is down-weighted in recommendations.
        if f in used_families and not is_high_upside:
            continue
        diversified.append(r)
        if not is_high_upside:
            used_families.add(f)
    candidates=diversified
    need=max(0,args.target_active-len(active)); promote=candidates[:min(args.max_promote,need)]
    events=[]
    if promote:
        for r in promote:
            tier='aggressive_research_active' if r in high_upside else 'core_probation'
            reason=f"guarded {mode} {tier} paper promotion proposal: active pool below {args.target_active}; samples {r['samples']}, success {r['success_rate_pct']}%, excess {r.get('avg_excess_return_pct')}%, recent {r.get('recent_success_rate_pct')}%/{r.get('recent_avg_excess_return_pct')}%"
            event={'logic':r['logic'],'old_status':r['status'],'proposed_status':'active','reason':reason,'score':round(score(r),2),'family':family(r['logic']),'tier':tier,'authority':'proposal_only','canonical_writer':'strategy_lifecycle'}
            if args.apply_promotions:
                conn.execute('UPDATE strategy_registry SET status=?, reason=?, updated_at=? WHERE logic=?',('active',reason,utc_now(),r['logic']))
                conn.execute('INSERT INTO strategy_state_events (logic,old_status,new_status,reason,event_json,created_at) VALUES (?,?,?,?,?,?)',(r['logic'],r['status'],'active',reason,json.dumps({'agent':'active_strategy_balancer','score':score(r),'family':family(r['logic']),'tier':tier,'authority':'legacy_apply'},ensure_ascii=False),utc_now()))
                event['new_status']='active'; event['applied']=True
            else:
                event['applied']=False
            events.append(event)
        if args.apply_promotions:
            conn.commit()
    conn.close()
    status='ok' if len(active) or events else 'degraded'
    warnings=[] if status=='ok' else ['active pool remains empty after balancing']
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'active_strategy_balancer','target_active':args.target_active,'before_active':len(active),'promotion_mode':mode,'qualified_candidates':len(candidates),'promotion_proposals':events, 'promoted': [e for e in events if e.get('applied')],'real_trading':False}
    attach_contract(packet, 'active_strategy_balancer', status=status, inputs={'target_active': args.target_active, 'max_promote': args.max_promote, 'high_upside_slots': args.high_upside_slots}, outputs={'promoted': events}, metrics={'before_active': len(active), 'promoted_count': len(events), 'qualified_candidates': len(candidates)}, warnings=warnings, next_actions=['Relax reserve thresholds or inspect strategy lifecycle inputs.'] if status!='ok' else [])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
