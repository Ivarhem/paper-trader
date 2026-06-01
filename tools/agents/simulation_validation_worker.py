#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, list_strategy_registry, list_universe_members, save_validation_results, validation_summary, validation_coverage
from tools.agents.recommendation_auditor import LOGICS, audit_symbol, logic_config, month_cutoffs
from tools.agents.lib.agent_contract import attach_contract


def existing_keys(conn):
    try:
        return {r['run_key'] for r in conn.execute('SELECT run_key FROM recommendation_validation_results').fetchall()}
    except sqlite3.OperationalError:
        return set()


def logic_sample_counts() -> dict[str, int]:
    coverage = validation_coverage()
    counts = {x['logic']: int(x.get('candidate_samples') or 0) for x in coverage.get('by_logic', [])}
    for logic in LOGICS:
        counts.setdefault(logic, 0)
    # Include legacy/generated repair-active logics that are still valid via logic_config().
    try:
        for row in list_strategy_registry():
            logic = row.get('logic')
            if logic and logic_config(logic):
                counts.setdefault(logic, int(row.get('samples') or 0))
    except Exception:
        pass
    return counts


def logic_priority(logic: str, counts: dict[str, int], current_symbols: set[str], weakness_symbols: set[str]) -> tuple:
    cfg = logic_config(logic) or {}
    samples = counts.get(logic, 0)
    # First priority: newly-added deterministic data-only strategies until they have a basic sample base.
    data_bucket = 0 if cfg.get('data_only') and samples < 80 else (1 if cfg.get('data_only') else 2)
    # Then generally under-tested strategies.
    sample_bucket = 0 if samples < 30 else (1 if samples < 80 else 2)
    return (data_bucket, sample_bucket, samples, logic)


def build_stratified_backlog(symbols, cutoffs, horizons, logics, done, current_symbols=None, weakness_symbols=None):
    """Return missing combinations, interleaved by priority.

    Priority favors data-only/under-sampled logics first, and current recommendation or known-weakness symbols
    inside each logic so new strategy families get enough historical samples quickly.
    """
    counts = logic_sample_counts()
    current_symbols=set(current_symbols or [])
    weakness_symbols=set(weakness_symbols or [])
    ranked_logics = sorted(logics, key=lambda l: logic_priority(l, counts, current_symbols, weakness_symbols))
    per_logic = {}
    for l in ranked_logics:
        rows = []
        ranked_symbols=sorted(symbols, key=lambda sym: (0 if sym in current_symbols else (1 if sym in weakness_symbols else 2), sym))
        for h in horizons:
            # Recent cutoffs are useful, but only after enough forward bars
            # exist for the requested horizon. Otherwise batches get consumed
            # by insufficient-data combinations and no validation samples land.
            min_age = timedelta(days=max(30, int(h * 1.8)))
            eligible_cutoffs = []
            for c in cutoffs:
                try:
                    cutoff_dt = datetime.fromisoformat(str(c)).replace(tzinfo=timezone.utc)
                except Exception:
                    cutoff_dt = datetime.now(timezone.utc)
                if datetime.now(timezone.utc) - cutoff_dt >= min_age:
                    eligible_cutoffs.append(c)
            for c in reversed(eligible_cutoffs or cutoffs):
                for s in ranked_symbols:
                    key = f'{l}|{s}|{c}|{h}'
                    if key not in done:
                        rows.append((s, c, h, l))
        per_logic[l] = rows
    todo = []
    max_len = max((len(v) for v in per_logic.values()), default=0)
    for i in range(max_len):
        for l in ranked_logics:
            rows = per_logic[l]
            if i < len(rows):
                todo.append(rows[i])
    return todo, counts


def load_priority_symbols() -> tuple[set[str], set[str]]:
    current=set(); weak=set()
    try:
        rec=json.loads(Path('/tmp/recommendations_latest.json').read_text(encoding='utf-8'))
        current={x.get('symbol') for x in rec.get('items',[]) if x.get('symbol')}
    except Exception:
        pass
    try:
        opt=json.loads(Path('/tmp/strategy_success_optimizer_latest.json').read_text(encoding='utf-8'))
        for row in (opt.get('action_plan') or {}).get('validation_priorities') or []:
            if row.get('symbol'): weak.add(row.get('symbol'))
    except Exception:
        pass
    return current, weak

def main():
    ap=argparse.ArgumentParser(description='Continuously consume recommendation simulation validation backlog')
    ap.add_argument('--symbols')
    ap.add_argument('--monthly-from', default='2023-01-01')
    ap.add_argument('--monthly-step', type=int, default=1)
    ap.add_argument('--horizons', default='20,40,60,120')
    ap.add_argument('--logics', default=','.join(LOGICS.keys()))
    ap.add_argument('--batch-size', type=int, default=80)
    ap.add_argument('--output', default='/tmp/simulation_validation_latest.json')
    args=ap.parse_args()
    init_db()
    conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    symbols=[s.strip().upper() for s in args.symbols.split(',')] if args.symbols else [m['symbol'] for m in list_universe_members(status='active')]
    cutoffs=month_cutoffs(args.monthly_from, step=args.monthly_step)
    horizons=[int(x) for x in args.horizons.split(',') if x.strip()]
    logics=[l.strip() for l in args.logics.split(',') if logic_config(l.strip())]
    done=existing_keys(conn)
    current_symbols, weakness_symbols = load_priority_symbols()
    todo, sample_counts = build_stratified_backlog(symbols, cutoffs, horizons, logics, done, current_symbols, weakness_symbols)
    batch=todo[:args.batch_size]
    items=[]
    for s,c,h,l in batch:
        items.extend([x for x in audit_symbol(conn,s,[c],h,[l]) if x.get('status')=='audited'])
    conn.close()
    save=save_validation_results(items)
    summary=validation_summary()
    processed_by_logic={}
    for _,_,_,logic in batch:
        processed_by_logic[logic]=processed_by_logic.get(logic,0)+1
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'simulation_validation_worker','real_trading':False,'backlog_order':'data_only_under_sampled_then_current_recommendation_symbols','planned_total':len(todo),'processed_combinations':len(batch),'processed_by_logic':processed_by_logic,'sample_counts_before':dict(sorted(sample_counts.items(), key=lambda kv:(logic_priority(kv[0], sample_counts, current_symbols, weakness_symbols)))[:30]),'priority_symbols':{'current_recommendations':sorted(current_symbols),'weaknesses':sorted(weakness_symbols)},'saved':save,'summary':summary,'sample_items':items[:20]}
    status='ok' if len(batch)>0 and len(items)>0 else 'degraded'
    warnings=[]
    if len(batch) <= 0:
        warnings.append('validation backlog empty or no batch selected')
    if len(batch)>0 and len(items)<=0:
        warnings.append('processed validation combinations produced no audited samples')
    attach_contract(packet, 'simulation_validation_worker', status=status, inputs={'batch_size': args.batch_size, 'monthly_from': args.monthly_from, 'monthly_step': args.monthly_step, 'horizons': args.horizons}, outputs={'processed_combinations': len(batch), 'saved': save}, metrics={'planned_total': len(todo), 'processed_combinations': len(batch), 'audited_items': len(items)}, warnings=warnings, next_actions=['Check symbols/cutoffs/logics if backlog unexpectedly empty.'] if status!='ok' else [])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
