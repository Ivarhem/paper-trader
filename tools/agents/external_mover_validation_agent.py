#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.database import init_db, list_strategy_registry
from tools.agents.lib.agent_contract import attach_contract

OUT = Path('/tmp/external_mover_validation_latest.json')


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def symbol_supported(symbol: str) -> bool:
    return bool(symbol) and not symbol.startswith(('^', 'KRW-', 'USD/'))


def row_rank_score(row: dict) -> tuple:
    direction = row.get('direction')
    change = abs(float(row.get('change_pct') or 0))
    volume = float(row.get('volume') or 0)
    upper = 1 if row.get('upper_limit_candidate') else 0
    positive = 1 if direction == 'gainer' and float(row.get('change_pct') or 0) > 0 else 0
    active = 1 if direction == 'active' else 0
    return (upper, positive, active, change, volume, -int(row.get('rank') or 9999))


def selected_movers(seed: dict, top_n: int, include_losers: bool = False) -> tuple[list[str], list[dict]]:
    rows = seed.get('top_stock_items') or seed.get('items') or []
    candidates = []
    seen = set()
    for row in sorted(rows, key=row_rank_score, reverse=True):
        if row.get('probable_stock') is False:
            continue
        if row.get('direction') == 'loser' and not include_losers:
            continue
        sym = str(row.get('symbol') or '').upper().strip()
        if not symbol_supported(sym) or sym in seen:
            continue
        seen.add(sym)
        candidates.append({
            'symbol': sym,
            'name': row.get('name'),
            'market': row.get('market'),
            'exchange': row.get('exchange'),
            'source': row.get('source'),
            'direction': row.get('direction'),
            'rank': row.get('rank'),
            'change_pct': row.get('change_pct'),
            'volume': row.get('volume'),
            'upper_limit_candidate': row.get('upper_limit_candidate'),
        })
        if len(candidates) >= top_n:
            break
    return [x['symbol'] for x in candidates], candidates


def chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def import_history(symbols: list[str], start: str, chunk_size: int, timeout_seconds: int) -> dict:
    results = []
    rc = 0
    for chunk in chunks(symbols, chunk_size):
        cmd = [sys.executable, 'tools/agents/import_stooq_daily.py', '--start', start, '--symbols', ','.join(chunk)]
        try:
            proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout_seconds)
            result = {'cmd': cmd, 'returncode': proc.returncode, 'symbols': chunk, 'stdout_tail': proc.stdout[-3000:], 'stderr_tail': proc.stderr[-3000:]}
            try:
                result['payload'] = json.loads(proc.stdout)
            except Exception:
                pass
        except subprocess.TimeoutExpired as exc:
            result = {'cmd': cmd, 'returncode': 124, 'timeout': True, 'symbols': chunk, 'stdout_tail': (exc.stdout or '')[-3000:] if isinstance(exc.stdout, str) else '', 'stderr_tail': (exc.stderr or '')[-3000:] if isinstance(exc.stderr, str) else ''}
        results.append(result)
        if result.get('returncode') != 0 and rc == 0:
            rc = int(result.get('returncode') or 1)
    return {'returncode': rc, 'chunks': results}


def strategy_logics(limit: int) -> list[str]:
    rows = list_strategy_registry()
    preferred_status = {'active': 0, 'repair_active': 1, 'validation_active': 2, 'watch': 3, 'probation': 4}
    usable = [r for r in rows if r.get('status') in preferred_status]
    def key(row: dict) -> tuple:
        logic = str(row.get('logic') or '')
        volume_bonus = 1 if any(x in logic for x in ('volume', 'breakout', 'relative_strength', 'momentum', 'supply')) else 0
        return (-preferred_status.get(row.get('status'), 99), volume_bonus, float(row.get('avg_excess_return_pct') or -999), float(row.get('success_rate_pct') or 0), int(row.get('samples') or 0))
    out = []
    for row in sorted(usable, key=key, reverse=True):
        logic = row.get('logic')
        if logic and logic not in out:
            out.append(logic)
        if len(out) >= limit:
            break
    return out


def run_validation(symbols: list[str], logics: list[str], batch_size: int, monthly_from: str, horizons: str, timeout_seconds: int) -> tuple[dict, dict]:
    if not symbols or not logics:
        return {'returncode': 0, 'skipped': True, 'reason': 'empty symbols or logics'}, {}
    worker_output = '/tmp/external_mover_simulation_validation_latest.json'
    cmd = [sys.executable, 'tools/agents/simulation_validation_worker.py', '--symbols', ','.join(symbols), '--logics', ','.join(logics), '--batch-size', str(batch_size), '--monthly-from', monthly_from, '--monthly-step', '1', '--horizons', horizons, '--output', worker_output]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout_seconds)
    return {'cmd': cmd, 'returncode': proc.returncode, 'stdout_tail': proc.stdout[-3000:], 'stderr_tail': proc.stderr[-3000:], 'worker_output': worker_output}, read_json(worker_output)


def main() -> None:
    ap = argparse.ArgumentParser(description='Validate daily external gainer/volume top-N candidates against existing strategy logics; paper-only, no orders.')
    ap.add_argument('--seed', default='/tmp/market_mover_seed_latest.json')
    ap.add_argument('--top-n', type=int, default=80)
    ap.add_argument('--include-losers', action='store_true')
    ap.add_argument('--history-start', default='2022-01-01')
    ap.add_argument('--import-chunk-size', type=int, default=25)
    ap.add_argument('--import-timeout-seconds', type=int, default=240)
    ap.add_argument('--logic-limit', type=int, default=14)
    ap.add_argument('--batch-size', type=int, default=900)
    ap.add_argument('--monthly-from', default='2024-01-01')
    ap.add_argument('--horizons', default='5,20,40')
    ap.add_argument('--worker-timeout-seconds', type=int, default=1200)
    ap.add_argument('--output', default=str(OUT))
    args = ap.parse_args()
    init_db()

    seed = read_json(args.seed)
    symbols, candidates = selected_movers(seed, args.top_n, args.include_losers)
    imports = import_history(symbols, args.history_start, args.import_chunk_size, args.import_timeout_seconds) if symbols else {'returncode': 0, 'chunks': []}
    logics = strategy_logics(args.logic_limit)
    validation_proc, worker = run_validation(symbols, logics, args.batch_size, args.monthly_from, args.horizons, args.worker_timeout_seconds)
    processed = int(worker.get('processed_combinations') or 0)

    warnings = []
    if not symbols:
        warnings.append('no external mover symbols selected')
    if not logics:
        warnings.append('no strategy logics available')
    if imports.get('returncode') != 0:
        warnings.append(f"history import returned {imports.get('returncode')}")
    if validation_proc.get('returncode') != 0:
        warnings.append(f"validation worker returned {validation_proc.get('returncode')}")
    if symbols and logics and processed <= 0:
        warnings.append('no mover validation combinations processed')
    status = 'ok' if not warnings else 'degraded'

    packet = {'run_at': utc_now(), 'mode': 'external_mover_top_n_validation', 'real_trading': False, 'authority': 'paper_only_validation_seed_not_order_signal', 'policy': {'purpose': 'Daily validation of external gainer and volume-rank top-N candidates before they influence future recommendations.', 'candidate_source': args.seed, 'selection': 'upper_limit_and_positive_gainers_first_then_volume_rank; losers excluded unless requested', 'recommendation_authority': 'none; writes validation evidence only'}, 'seed_run_at': seed.get('run_at'), 'top_n': args.top_n, 'candidate_count': len(candidates), 'symbols': symbols, 'candidates': candidates, 'logics': logics, 'history_start': args.history_start, 'import_result': imports, 'validation': validation_proc, 'worker': worker, 'summary': {'processed_combinations': processed, 'saved': worker.get('saved'), 'top_symbols': symbols[:20], 'logic_count': len(logics)}, 'warnings': warnings}
    attach_contract(packet, 'external_mover_validation_agent', status=status, inputs={'seed': args.seed, 'top_n': args.top_n, 'symbols': symbols, 'logics': logics}, outputs={'candidate_count': len(candidates), 'processed_combinations': processed, 'saved': worker.get('saved')}, metrics={'candidate_count': len(candidates), 'symbol_count': len(symbols), 'logic_count': len(logics), 'processed_combinations': processed}, warnings=warnings, next_actions=['Promote only repeatedly validated external movers into recommendation/fund candidate pools; keep same-day movers as watch/validation priority.'])
    Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    if validation_proc.get('returncode') != 0:
        sys.exit(int(validation_proc.get('returncode') or 1))


if __name__ == '__main__':
    main()
