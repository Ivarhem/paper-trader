#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.config import get_settings
from app.database import init_db
from app.symbols import display_name


def score_symbol(conn: sqlite3.Connection, symbol: str, lookback_days: int) -> dict | None:
    rows = conn.execute(
        """
        SELECT date, close, volume
        FROM price_bars
        WHERE symbol = ? AND timeframe = '1d'
        ORDER BY date ASC
        """,
        (symbol,),
    ).fetchall()
    if len(rows) < 80:
        return None
    latest = rows[-1]
    close = float(latest['close'])
    def ret(days: int) -> float | None:
        if len(rows) <= days:
            return None
        base = float(rows[-days-1]['close'])
        return round((close / base - 1) * 100, 2) if base else None
    r20, r60, r120 = ret(20), ret(60), ret(120)
    vol20 = sum(float(r['volume']) for r in rows[-20:]) / min(20, len(rows))
    vol60 = sum(float(r['volume']) for r in rows[-60:]) / min(60, len(rows))
    volume_surge = round(vol20 / vol60, 2) if vol60 else 0
    high_252 = max(float(r['close']) for r in rows[-252:])
    near_high_pct = round((close / high_252 - 1) * 100, 2) if high_252 else None

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime('%Y%m%d')
    disc = conn.execute(
        """
        SELECT risk_level, report_nm, rcept_dt
        FROM disclosure_events
        WHERE symbol = ? AND rcept_dt >= ?
        ORDER BY rcept_dt DESC, id DESC
        """,
        (symbol, cutoff),
    ).fetchall()
    high = sum(1 for d in disc if d['risk_level'] == 'high')
    medium = sum(1 for d in disc if d['risk_level'] == 'medium')
    positive = sum(1 for d in disc if d['risk_level'] == 'positive')

    score = 0.0
    for value, weight in [(r20, 0.8), (r60, 0.5), (r120, 0.3)]:
        if value is not None:
            score += value * weight
    if near_high_pct is not None and near_high_pct > -10:
        score += (10 + near_high_pct) * 2
    if volume_surge > 1.2:
        score += min((volume_surge - 1) * 10, 15)
    score += positive * 4
    score -= high * 30
    score -= medium * 8

    reasons = []
    if r20 is not None and r20 > 5: reasons.append(f'20d momentum {r20}%')
    if r60 is not None and r60 > 10: reasons.append(f'60d momentum {r60}%')
    if near_high_pct is not None and near_high_pct > -5: reasons.append(f'near 252d high {near_high_pct}%')
    if volume_surge > 1.2: reasons.append(f'volume surge {volume_surge}x')
    if positive: reasons.append(f'{positive} positive disclosures')
    if high or medium: reasons.append(f'risk disclosures high={high}, medium={medium}')

    return {
        'symbol': symbol,
        'name': display_name(symbol),
        'score': round(score, 2),
        'latest_close': close,
        'return_20d_pct': r20,
        'return_60d_pct': r60,
        'return_120d_pct': r120,
        'volume_surge_20v60': volume_surge,
        'near_252d_high_pct': near_high_pct,
        'disclosures': {'high': high, 'medium': medium, 'positive': positive, 'total': len(disc)},
        'reasons': reasons,
    }


def main():
    ap = argparse.ArgumentParser(description='Universe Scout / Idea Sourcing Agent')
    ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--lookback-days', type=int, default=30)
    ap.add_argument('--exclude-risk', action='store_true', help='Exclude symbols with high-risk or 2+ medium-risk disclosures')
    ap.add_argument('--output', default='/tmp/universe_scout_latest.json')
    args = ap.parse_args()
    init_db()
    conn = sqlite3.connect(get_settings().database_path)
    conn.row_factory = sqlite3.Row
    excluded = {r['symbol'] for r in conn.execute("SELECT symbol FROM universe_members WHERE status IN ('quarantine','retired')").fetchall()}
    symbols = [r['symbol'] for r in conn.execute("SELECT DISTINCT symbol FROM price_bars WHERE timeframe='1d' ORDER BY symbol").fetchall() if r['symbol'] not in excluded and not r['symbol'].startswith('^')]
    candidates = []
    for symbol in symbols:
        item = score_symbol(conn, symbol, args.lookback_days)
        if not item:
            continue
        if args.exclude_risk and (item['disclosures']['high'] > 0 or item['disclosures']['medium'] >= 2):
            continue
        candidates.append(item)
    conn.close()
    candidates.sort(key=lambda x: x['score'], reverse=True)
    packet = {
        'run_at': datetime.now(timezone.utc).isoformat(),
        'role': 'Universe Scout',
        'method': 'momentum_volume_disclosure_score',
        'lookback_days': args.lookback_days,
        'scanned_count': len(symbols),
        'candidate_count': len(candidates),
        'selected': candidates[:args.limit],
    }
    Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(packet, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
