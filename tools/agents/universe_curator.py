#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.config import get_settings
from app.database import init_db, upsert_universe_members
from app.symbols import display_name

HIGH_RISK_TERMS = ["상장폐지", "관리종목", "거래정지", "감자", "회생", "파산", "불성실공시", "횡령", "배임", "감사의견", "의견거절"]
MATERIAL_MEDIUM_RISK_TERMS = ["유상증자", "전환사채", "신주인수권", "소송", "담보", "질권", "반대매매", "대량매도", "처분결정", "자기주식처분", "CB", "BW"]
BENIGN_DISCLOSURE_TERMS = ["최대주주등소유주식변동신고서", "임원ㆍ주요주주특정증권등소유상황보고서", "주식등의대량보유상황보고서", "연결재무제표기준영업(잠정)실적", "현금ㆍ현물배당", "기업설명회", "특수관계인과의내부거래", "특수관계인에대한출자"]


def is_material_medium_disclosure(name: str, risk_level: str | None) -> bool:
    compact = (name or '').replace(' ', '')
    if any(term in compact for term in HIGH_RISK_TERMS):
        return False
    if any(term in compact for term in MATERIAL_MEDIUM_RISK_TERMS):
        return True
    # OpenDART frequently emits ownership/major-shareholder/internal-transaction
    # updates and corrections for large caps. Treat those as risk notes, not
    # quarantine signals, unless material stress keywords are present above.
    if any(term.replace(' ', '') in compact for term in BENIGN_DISCLOSURE_TERMS):
        return False
    return risk_level == 'medium' and '기재정정' in compact and not any(term.replace(' ', '') in compact for term in BENIGN_DISCLOSURE_TERMS)


def curate_symbol(conn: sqlite3.Connection, symbol: str, stale_days: int, min_bars: int, disclosure_days: int) -> dict:
    now = datetime.now(timezone.utc)
    if symbol.startswith('^'):
        return {
            'symbol': symbol,
            'name': display_name(symbol),
            'status': 'benchmark',
            'reason': 'benchmark index used for market-relative validation; not a trade recommendation universe member',
            'score': 100.0,
            'updated_at': now.isoformat(),
            'checks': {'price_bars': 0, 'disclosures': 0, 'high_risk': 0, 'medium_risk': 0},
        }
    prices = conn.execute(
        "SELECT date, close, volume FROM price_bars WHERE symbol=? AND timeframe='1d' ORDER BY date ASC",
        (symbol,),
    ).fetchall()
    reasons=[]; status='active'; score=100.0
    if len(prices) < min_bars:
        status='quarantine'; reasons.append(f'insufficient price bars: {len(prices)} < {min_bars}'); score -= 40
    if prices:
        latest_date = prices[-1]['date'][:10]
        try:
            age = (now.date() - datetime.fromisoformat(latest_date).date()).days
            if age > stale_days:
                status='quarantine'; reasons.append(f'stale price data: {age} days old'); score -= min(age, 60)
        except Exception:
            status='quarantine'; reasons.append(f'invalid latest price date: {latest_date}'); score -= 30
        if len(prices) >= 20:
            avg_vol = sum(float(r['volume']) for r in prices[-20:]) / 20
            if avg_vol <= 0:
                status='quarantine'; reasons.append('zero recent volume'); score -= 50
    else:
        status='quarantine'; reasons.append('no price data'); score -= 80

    cutoff = (now - timedelta(days=disclosure_days)).strftime('%Y%m%d')
    disclosures = conn.execute(
        "SELECT rcept_dt, report_nm, risk_level FROM disclosure_events WHERE symbol=? AND rcept_dt >= ? ORDER BY rcept_dt DESC",
        (symbol, cutoff),
    ).fetchall()
    high=[]; medium=[]; benign_medium=[]
    for d in disclosures:
        name = d['report_nm'] or ''
        risk = d['risk_level']
        if risk == 'high' or any(term in name for term in HIGH_RISK_TERMS):
            high.append(dict(d))
        elif is_material_medium_disclosure(name, risk):
            medium.append(dict(d))
        elif risk == 'medium':
            benign_medium.append(dict(d))
    if high:
        status='quarantine'; reasons.append(f'high-risk disclosures: {len(high)}'); score -= 60
    if len(medium) >= 2 and status != 'quarantine':
        status='watch'; reasons.append(f'multiple medium-risk disclosures: {len(medium)}'); score -= 25
    if len(medium) >= 3:
        status='quarantine'; reasons.append(f'excessive material medium-risk disclosures: {len(medium)}'); score -= 35
    elif benign_medium:
        reasons.append(f'benign/recurring disclosure notes: {len(benign_medium)}')
        score -= min(10, len(benign_medium) * 2)

    if score < 20 or any('상장폐지' in (d.get('report_nm') or '') or '파산' in (d.get('report_nm') or '') for d in high):
        status='retired'
    if not reasons:
        reasons.append('passes current universe hygiene checks')
    return {
        'symbol': symbol,
        'name': display_name(symbol),
        'status': status,
        'reason': '; '.join(reasons),
        'score': round(score, 2),
        'updated_at': now.isoformat(),
        'checks': {'price_bars': len(prices), 'disclosures': len(disclosures), 'high_risk': len(high), 'medium_risk': len(medium), 'benign_medium_risk': len(benign_medium)},
    }


def main():
    ap=argparse.ArgumentParser(description='Universe Curator: active/watch/quarantine/retired hygiene agent')
    ap.add_argument('--stale-days', type=int, default=10)
    ap.add_argument('--min-bars', type=int, default=80)
    ap.add_argument('--disclosure-days', type=int, default=90)
    ap.add_argument('--save', action='store_true')
    ap.add_argument('--output', default='/tmp/universe_curator_latest.json')
    args=ap.parse_args()
    init_db()
    conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    symbols=[r['symbol'] for r in conn.execute("SELECT DISTINCT symbol FROM price_bars WHERE timeframe='1d' ORDER BY symbol").fetchall()]
    items=[curate_symbol(conn, s, args.stale_days, args.min_bars, args.disclosure_days) for s in symbols]
    conn.close()
    counts={}
    for item in items: counts[item['status']]=counts.get(item['status'],0)+1
    save_result=upsert_universe_members(items) if args.save else None
    packet={'run_at': datetime.now(timezone.utc).isoformat(), 'role':'Universe Curator', 'counts': counts, 'items': items, 'save_result': save_result}
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
