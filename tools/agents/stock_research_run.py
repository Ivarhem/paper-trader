#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path


def _ensure_project_venv():
    root = Path(__file__).resolve().parents[2]
    venv_python = root / ".venv" / "bin" / "python"
    if venv_python.exists() and Path(sys.prefix).resolve() != (root / ".venv").resolve():
        import os
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


_ensure_project_venv()
import argparse, json, sqlite3, subprocess, time
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.config import get_settings

DEFAULT_OUTPUT = Path('/tmp/stock_research_latest.json')
DEFAULT_OUTPUT_POINTER = Path('/tmp/stock_research_latest_path')
DEFAULT_NEAR_MISS_QUARANTINE_OUTPUT = Path('/tmp/stock_research_near_miss_quarantine_latest.json')
DEFAULT_NEAR_MISS_QUARANTINE_OUTPUT_POINTER = Path('/tmp/stock_research_near_miss_quarantine_latest_path')



def split_symbols(value: str) -> list[str]:
    out=[]; seen=set()
    for sym in [x.strip().upper() for x in (value or '').split(',') if x.strip()]:
        if sym not in seen:
            seen.add(sym); out.append(sym)
    return out


def latest_recommendation_symbols(limit: int) -> list[str]:
    try:
        data=json.loads(Path('/tmp/recommendations_latest.json').read_text(encoding='utf-8'))
    except Exception:
        return []
    rows=data.get('items') or []
    rows=sorted(rows, key=lambda r: (r.get('action') == 'candidate_buy_zone', float(r.get('score') or 0)), reverse=True)
    return [r.get('symbol') for r in rows if r.get('symbol')][:limit]


def universe_expansion_symbols(limit: int) -> list[str]:
    if limit <= 0: return []
    try:
        conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
        rows=conn.execute("""
            SELECT symbol, status, score, updated_at
            FROM universe_members
            WHERE status IN ('active','watch') AND symbol NOT LIKE '^%'
            ORDER BY updated_at DESC, score DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [r['symbol'] for r in rows]
    except Exception:
        return []


def expand_symbols(base: list[str], recommendation_limit: int, universe_limit: int, max_symbols: int) -> tuple[list[str], dict]:
    recs=latest_recommendation_symbols(recommendation_limit)
    uni=universe_expansion_symbols(universe_limit)
    out=[]; seen=set()
    for sym in [*base, *recs, *uni]:
        if not sym or sym in seen: continue
        seen.add(sym); out.append(sym)
        if len(out) >= max_symbols: break
    return out, {'base_count':len(base),'recommendation_added':[s for s in recs if s not in base],'universe_added':[s for s in uni if s not in base and s not in recs],'max_symbols':max_symbols}

def log_progress(message: str):
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": message}, ensure_ascii=False), flush=True)


def write_json_artifact(path: str | Path, payload: dict, *, latest_path: Path, pointer_path: Path) -> dict:
    artifact_path = Path(path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    if artifact_path != latest_path:
        latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    pointer_path.write_text(str(artifact_path), encoding='utf-8')
    return {
        'path': str(artifact_path),
        'latest_path': str(latest_path),
        'pointer_path': str(pointer_path),
    }


def _tail_output(value: str | None, *, max_chars: int = 4000) -> str:
    if not value:
        return ''
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]


def _log_child_output(label: str, exc: subprocess.CalledProcessError | subprocess.TimeoutExpired):
    stdout = _tail_output(getattr(exc, 'stdout', None))
    stderr = _tail_output(getattr(exc, 'stderr', None))
    if stdout:
        log_progress(f"child_stdout_tail:{label}:{stdout}")
    if stderr:
        log_progress(f"child_stderr_tail:{label}:{stderr}")


def run(cmd, *, label: str, timeout_seconds: int):
    log_progress(f"start:{label}")
    started = time.monotonic()
    try:
        completed = subprocess.run(cmd, text=True, capture_output=True, check=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.monotonic() - started, 2)
        log_progress(f"timeout:{label}:{elapsed}s")
        _log_child_output(label, exc)
        raise
    except subprocess.CalledProcessError as exc:
        elapsed = round(time.monotonic() - started, 2)
        log_progress(f"failed:{label}:rc={exc.returncode}:{elapsed}s")
        _log_child_output(label, exc)
        raise
    elapsed = round(time.monotonic() - started, 2)
    log_progress(f"done:{label}:{elapsed}s")
    return completed



def apply_concentration_gate(promoted: list[dict], *, min_symbols: int = 2, max_symbol_share: float = 0.67, min_cutoffs_per_symbol: int = 3) -> tuple[list[dict], list[dict], dict]:
    """Withhold narrow repeated promotions before they are surfaced as promotable.

    Walk-forward can legitimately find a good symbol-specific setup, but if every
    promotion is the same symbol/strategy/params cluster, it is better treated as
    a watchlist hypothesis until broader or stronger confirmation appears. This
    is historical/paper-only hygiene; it does not place orders.
    """
    if not promoted:
        return [], [], {
            'enabled': True,
            'min_symbols': min_symbols,
            'max_symbol_share': max_symbol_share,
            'min_cutoffs_per_symbol': min_cutoffs_per_symbol,
            'withheld_count': 0,
            'reasons': [],
        }
    by_symbol: dict[str, list[dict]] = {}
    by_cluster: dict[tuple, list[dict]] = {}
    for item in promoted:
        sym = str(item.get('symbol') or '')
        by_symbol.setdefault(sym, []).append(item)
        selected = item.get('selected_train') or {}
        params = selected.get('params') or {}
        cluster = (sym, selected.get('strategy'), tuple(sorted(params.items())))
        by_cluster.setdefault(cluster, []).append(item)

    total = len(promoted)
    unique_symbols = len([s for s in by_symbol if s])
    max_symbol_count = max((len(v) for v in by_symbol.values()), default=0)
    max_cluster_count = max((len(v) for v in by_cluster.values()), default=0)
    reasons = []
    if unique_symbols < min_symbols:
        reasons.append(f'promotion concentration: unique promoted symbols {unique_symbols} < {min_symbols}')
    if total and (max_symbol_count / total) > max_symbol_share:
        reasons.append(f'promotion concentration: top symbol share {round(max_symbol_count / total, 2)} > {max_symbol_share}')
    if max_cluster_count == total and total < min_cutoffs_per_symbol:
        reasons.append(f'promotion concentration: same symbol/strategy/params across only {total} cutoffs < {min_cutoffs_per_symbol}')

    gate = {
        'enabled': True,
        'min_symbols': min_symbols,
        'max_symbol_share': max_symbol_share,
        'min_cutoffs_per_symbol': min_cutoffs_per_symbol,
        'unique_promoted_symbols': unique_symbols,
        'max_symbol_count': max_symbol_count,
        'max_symbol_share_observed': round(max_symbol_count / total, 4) if total else 0,
        'max_cluster_count': max_cluster_count,
        'raw_promoted_count': total,
        'withheld_count': total if reasons else 0,
        'reasons': reasons,
    }
    if not reasons:
        return promoted, [], gate
    withheld = []
    for item in promoted:
        copy = dict(item)
        copy['decision_before_concentration_gate'] = item.get('decision')
        copy['decision'] = 'withheld_concentration_risk'
        copy['concentration_gate_reasons'] = reasons
        withheld.append(copy)
    return [], withheld, gate



def build_near_miss_quarantine(withheld_promotions: list[dict], *, cutoffs: list[str], min_supporting_cutoffs: int = 3) -> dict:
    """Turn concentration-withheld promotions into explicit validation work.

    This keeps the promotion gate conservative while preserving promising
    historical-only signals for additional regime/cutoff testing. It never
    places orders and never changes promoted_count.
    """
    clusters: dict[tuple, list[dict]] = {}
    for item in withheld_promotions:
        selected = item.get('selected_train') or {}
        params = selected.get('params') or {}
        key = (str(item.get('symbol') or ''), selected.get('strategy'), tuple(sorted(params.items())))
        clusters.setdefault(key, []).append(item)

    items = []
    for (symbol, strategy, params_tuple), rows in sorted(clusters.items(), key=lambda kv: len(kv[1]), reverse=True):
        observed_cutoffs = sorted({str(r.get('cutoff') or r.get('train_end') or '') for r in rows if (r.get('cutoff') or r.get('train_end'))})
        tests = [r.get('out_of_sample_test') or {} for r in rows]
        excess = []
        drawdowns = []
        trades = []
        for t in tests:
            tr = t.get('total_return_pct')
            bh = t.get('buy_hold_return_pct')
            if tr is not None and bh is not None:
                excess.append(float(tr) - float(bh))
            if t.get('max_drawdown_pct') is not None:
                drawdowns.append(float(t.get('max_drawdown_pct')))
            if t.get('trade_count') is not None:
                trades.append(int(t.get('trade_count')))
        needed = max(0, min_supporting_cutoffs - len(observed_cutoffs))
        items.append({
            'symbol': symbol,
            'strategy': strategy,
            'params': dict(params_tuple),
            'withheld_count': len(rows),
            'observed_cutoffs': observed_cutoffs,
            'additional_cutoffs_needed': needed,
            'avg_oos_excess_pct': round(sum(excess) / len(excess), 2) if excess else None,
            'worst_oos_drawdown_pct': round(min(drawdowns), 2) if drawdowns else None,
            'min_oos_trades': min(trades) if trades else None,
            'quarantine_reason': 'promotion concentration risk: validate across more cutoffs/regimes before promotion',
            'validation_requirements': {
                'min_supporting_cutoffs': min_supporting_cutoffs,
                'must_keep_positive_oos_excess': True,
                'must_pass_trade_count_gate': True,
                'must_pass_drawdown_review': True,
                'must_not_be_single_symbol_only_if_multiple_candidates_exist': True,
            },
            'suggested_next_run': {
                'symbols': symbol,
                'add_regime_cutoffs': True,
                'keep_historical_only': True,
                'real_trading': False,
            },
        })
    return {
        'run_at': datetime.now(timezone.utc).isoformat(),
        'mode': 'near_miss_quarantine_historical_only',
        'real_trading': False,
        'source': 'stock_research_run.withheld_promotions',
        'cutoffs': cutoffs,
        'min_supporting_cutoffs': min_supporting_cutoffs,
        'count': len(items),
        'items': items,
    }

def walk_forward_summary(wf: dict) -> dict:
    rows = wf.get('results') or []
    reason_counts = {}
    by_symbol = {}
    strategy_counts = {}
    for r in rows:
        by_symbol.setdefault(r.get('symbol'), []).append(r)
        st = ((r.get('selected_train') or {}).get('strategy'))
        if st:
            strategy_counts[st] = strategy_counts.get(st, 0) + 1
        for reason in r.get('reasons') or []:
            key = str(reason).split('(')[0].strip()
            reason_counts[key] = reason_counts.get(key, 0) + 1
    symbol_summaries = []
    for sym, arr in sorted(by_symbol.items()):
        rejects = sum(1 for x in arr if x.get('decision') == 'reject')
        promotes = sum(1 for x in arr if x.get('decision') == 'promote')
        tests = [x.get('out_of_sample_test') or {} for x in arr]
        excess=[]; drawdowns=[]; trades=[]
        for x, t in zip(arr, tests):
            tr = t.get('total_return_pct')
            bh = t.get('buy_hold_return_pct')
            if tr is not None and bh is not None:
                excess.append(float(tr) - float(bh))
            if t.get('max_drawdown_pct') is not None:
                drawdowns.append(float(t.get('max_drawdown_pct')))
            if t.get('trade_count') is not None:
                trades.append(int(t.get('trade_count')))
        symbol_summaries.append({
            'symbol': sym,
            'runs': len(arr),
            'promotes': promotes,
            'rejects': rejects,
            'avg_oos_excess_pct': round(sum(excess)/len(excess), 2) if excess else None,
            'worst_oos_drawdown_pct': round(min(drawdowns), 2) if drawdowns else None,
            'min_oos_trades': min(trades) if trades else None,
        })
    return {
        'result_count': len(rows),
        'decision_counts': {k: sum(1 for r in rows if (r.get('decision') or 'unknown') == k) for k in sorted({(r.get('decision') or 'unknown') for r in rows})},
        'top_reject_reasons': sorted(reason_counts.items(), key=lambda kv: kv[1], reverse=True)[:8],
        'selected_strategy_counts': dict(sorted(strategy_counts.items(), key=lambda kv: kv[1], reverse=True)),
        'symbol_summaries': sorted(symbol_summaries, key=lambda x: (x.get('promotes', 0), x.get('avg_oos_excess_pct') if x.get('avg_oos_excess_pct') is not None else -999), reverse=True),
    }

def main():
    ap=argparse.ArgumentParser(description='Bounded stock strategy research loop')
    ap.add_argument('--symbols', default='AAPL,MSFT,NVDA,SPY,QQQ,005930.KS,000660.KS,035420.KS,005380.KS,068270.KS,035720.KS,051910.KS')
    ap.add_argument('--cutoffs', default='2023-01-01,2024-01-01,2025-01-01,2026-01-01')
    ap.add_argument('--start', default='2018-01-01')
    ap.add_argument('--output', default=str(DEFAULT_OUTPUT))
    ap.add_argument('--near-miss-quarantine-output', default=str(DEFAULT_NEAR_MISS_QUARANTINE_OUTPUT))
    ap.add_argument('--dynamic-symbols', action=argparse.BooleanOptionalAction, default=True, help='Expand fixed anchors with current recommendation and active/watch universe symbols')
    ap.add_argument('--recommendation-symbol-limit', type=int, default=10)
    ap.add_argument('--universe-symbol-limit', type=int, default=10)
    ap.add_argument('--max-symbols', type=int, default=28)
    ap.add_argument('--skip-import', action='store_true')
    ap.add_argument('--no-disclosures', action='store_true')
    ap.add_argument('--min-oos-trades', type=int, default=10)
    ap.add_argument('--min-oos-excess-pct', type=float, default=2.0)
    ap.add_argument('--step-timeout-seconds', type=int, default=600, help='Maximum seconds allowed for each child research step before failing loudly')
    args=ap.parse_args()
    base_symbols=split_symbols(args.symbols)
    symbols, symbol_expansion = expand_symbols(base_symbols, args.recommendation_symbol_limit, args.universe_symbol_limit, args.max_symbols) if args.dynamic_symbols else (base_symbols, {'base_count':len(base_symbols),'dynamic':False})
    symbols_csv=','.join(symbols)
    imports=None
    if not args.skip_import:
        imports=json.loads(run([sys.executable,'tools/agents/import_stooq_daily.py','--symbols',symbols_csv,'--start',args.start], label='import_stooq_daily', timeout_seconds=args.step_timeout_seconds).stdout)
    wf_cmd=[sys.executable,'tools/agents/walk_forward_agent.py','--symbols',symbols_csv,'--cutoffs',args.cutoffs,'--min-train-bars','250','--min-test-bars','60','--min-oos-trades',str(args.min_oos_trades),'--min-oos-excess-pct',str(args.min_oos_excess_pct)]
    if args.no_disclosures:
        wf_cmd.append('--no-disclosures')
    wf=json.loads(run(wf_cmd, label='walk_forward_agent', timeout_seconds=args.step_timeout_seconds).stdout)
    raw_promoted=[]
    rejected=0
    for item in wf.get('results',[]):
        if item.get('decision')=='promote': raw_promoted.append(item)
        elif item.get('status')=='ok': rejected+=1
    promoted, withheld_promotions, concentration_gate = apply_concentration_gate(raw_promoted)
    result_count=len(wf.get('results',[]))
    promoted_count=len(promoted)
    promotion_gates={
        'min_oos_trades': args.min_oos_trades,
        'min_oos_excess_pct': args.min_oos_excess_pct,
        'min_train_bars': 250,
        'min_test_bars': 60,
        'disclosures_enabled': not args.no_disclosures,
        'concentration_gate': concentration_gate,
    }
    research_summary=walk_forward_summary(wf)
    cutoffs=args.cutoffs.split(',')
    near_miss_quarantine=build_near_miss_quarantine(withheld_promotions, cutoffs=cutoffs)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'bounded_stock_research_historical_only','real_trading':False,'symbols':symbols,'base_symbols':base_symbols,'symbol_expansion':symbol_expansion,'cutoffs':cutoffs,'result_count':result_count,'raw_promoted_count':len(raw_promoted),'promoted_count':promoted_count,'withheld_promoted_count':len(withheld_promotions),'promotion_gates':promotion_gates,'near_miss_quarantine':near_miss_quarantine,'summary':research_summary,'imports':imports,'walk_forward':wf,'promoted':promoted,'withheld_promotions':withheld_promotions,'rejected_ok_count':rejected}
    output_artifact=write_json_artifact(args.output, packet, latest_path=DEFAULT_OUTPUT, pointer_path=DEFAULT_OUTPUT_POINTER)
    near_miss_artifact=write_json_artifact(args.near_miss_quarantine_output, near_miss_quarantine, latest_path=DEFAULT_NEAR_MISS_QUARANTINE_OUTPUT, pointer_path=DEFAULT_NEAR_MISS_QUARANTINE_OUTPUT_POINTER)
    print(json.dumps({'run_at':packet['run_at'],'real_trading':False,'symbols':symbols,'base_symbols':base_symbols,'symbol_expansion':symbol_expansion,'cutoffs':cutoffs,'result_count':result_count,'promoted_count':promoted_count,'withheld_promoted_count':len(withheld_promotions),'near_miss_quarantine_count':near_miss_quarantine.get('count',0),'promotion_gates':promotion_gates,'summary':research_summary,'output':args.output,'latest_output':output_artifact.get('latest_path'),'output_pointer':output_artifact.get('pointer_path'),'near_miss_quarantine_output':args.near_miss_quarantine_output,'near_miss_quarantine_latest_output':near_miss_artifact.get('latest_path'),'near_miss_quarantine_output_pointer':near_miss_artifact.get('pointer_path')},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
