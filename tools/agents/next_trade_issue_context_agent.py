#!/usr/bin/env python3
from __future__ import annotations
import json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.database import get_settings, init_db
from tools.agents.lib.agent_contract import attach_contract

OUT = Path('/tmp/next_trade_issue_context_latest.json')

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def read_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}

def issue_score(issue: dict) -> float:
    return float(issue.get('impact_score') or issue.get('confidence', 0) * 100 or 0)

def symbols_from_issue(issue: dict) -> list[str]:
    syms = []
    for key in ('affected_symbols', 'mentioned_symbols'):
        for sym in issue.get(key) or []:
            sym = str(sym or '').upper()
            if sym and sym not in syms:
                syms.append(sym)
    for member in issue.get('members') or []:
        sym = str(member.get('symbol') or '').upper()
        if sym and sym not in syms:
            syms.append(sym)
    for row in ((issue.get('price_confirmation') or {}).get('symbols') or []):
        sym = str(row.get('symbol') or '').upper()
        if sym and sym not in syms:
            syms.append(sym)
    return syms

def latest_disclosure_risk(conn, symbol: str) -> dict:
    rows = conn.execute(
        """SELECT rcept_dt, report_nm, category, risk_level
           FROM disclosure_events
           WHERE symbol=?
           ORDER BY rcept_dt DESC, id DESC
           LIMIT 5""",
        (symbol,),
    ).fetchall()
    high = sum(1 for r in rows if r['risk_level'] == 'high')
    medium = sum(1 for r in rows if r['risk_level'] == 'medium')
    positive = sum(1 for r in rows if r['category'] == 'positive' or r['risk_level'] == 'positive')
    return {
        'recent_count': len(rows),
        'high': high,
        'medium': medium,
        'positive': positive,
        'latest': dict(rows[0]) if rows else None,
    }

def classify_action(symbol: str, evidences: list[dict], disclosure: dict) -> tuple[str, float, list[str]]:
    score = 0.0
    reasons = []
    if disclosure.get('high'):
        return 'block_or_avoid', -8.0, ['recent high-risk disclosure']
    if disclosure.get('medium', 0) >= 3:
        return 'caution_only', -3.0, ['clustered medium-risk disclosures']
    for ev in evidences:
        policy = ev.get('recommendation_policy')
        risk = ev.get('risk')
        impact = float(ev.get('impact_score') or 0)
        if policy in ('context_boost_allowed', 'short_term_boost_allowed') and impact >= 65:
            add = 2.0 if impact < 80 else 3.0
            score += add
            reasons.append(f"{ev.get('label')} context +{add}")
        elif policy in ('watch_boost_only', 'watch_only') or risk in ('high_chase_risk', 'moderate_chase_risk'):
            score += 0.75
            reasons.append(f"{ev.get('label')} watch-priority only")
        elif policy == 'long_term_context_only':
            score += 0.25
            reasons.append(f"{ev.get('label')} long-term context only")
    if disclosure.get('positive'):
        score += 1.0
        reasons.append('recent positive disclosure support')
    score = max(-8.0, min(3.0, round(score, 2)))
    if score >= 2:
        return 'next_batch_priority', score, reasons
    if score > 0:
        return 'watch_or_validation_priority', score, reasons
    if score < 0:
        return 'caution_only', score, reasons
    return 'neutral_context', 0.0, reasons

def main():
    init_db()
    price_issues = read_json('/tmp/market_issue_scout_latest.json').get('issues') or []
    news_issues = read_json('/tmp/market_news_issue_scout_latest.json').get('issues') or []
    recs = read_json('/tmp/recommendations_latest.json').get('items') or []
    rec_symbols = [str(r.get('symbol') or '').upper() for r in recs if r.get('symbol')]
    evidence_by_symbol: dict[str, list[dict]] = {}
    source_issues = []
    for source, issues in [('price_volume', price_issues), ('news', news_issues)]:
        for issue in issues:
            score = issue_score(issue)
            if score < 55 and source == 'news':
                continue
            slim = {
                'source': source,
                'issue_id': issue.get('issue_id'),
                'label': issue.get('label'),
                'theme_hint': issue.get('theme_hint'),
                'impact_score': round(score, 2),
                'confidence': issue.get('confidence'),
                'risk': issue.get('risk'),
                'recommendation_policy': issue.get('recommendation_policy'),
                'recency_policy': issue.get('recency_policy'),
                'expected_impact': issue.get('expected_impact'),
            }
            source_issues.append(slim)
            for sym in symbols_from_issue(issue):
                evidence_by_symbol.setdefault(sym, []).append(slim)

    conn = sqlite3.connect(get_settings().database_path)
    conn.row_factory = sqlite3.Row
    items = []
    for sym, evidences in evidence_by_symbol.items():
        disclosure = latest_disclosure_risk(conn, sym)
        action, adjustment, reasons = classify_action(sym, evidences, disclosure)
        in_current_recommendations = sym in rec_symbols
        items.append({
            'symbol': sym,
            'action': action,
            'context_score_adjustment': adjustment,
            'in_current_recommendations': in_current_recommendations,
            'evidence_count': len(evidences),
            'top_evidence': sorted(evidences, key=lambda x: x.get('impact_score') or 0, reverse=True)[:5],
            'disclosure_risk': disclosure,
            'reasons': reasons[:6],
        })
    conn.close()
    items = sorted(
        items,
        key=lambda x: (
            x['action'] == 'next_batch_priority',
            x['action'] == 'watch_or_validation_priority',
            x['in_current_recommendations'],
            x['context_score_adjustment'],
            x['evidence_count'],
        ),
        reverse=True,
    )
    by_symbol = {x['symbol']: x for x in items}
    packet = {
        'run_at': utc_now(),
        'mode': 'next_trade_issue_context',
        'real_trading': False,
        'authority': 'paper_only_pretrade_context_for_next_batch_not_order_signal',
        'policy': {
            'max_recommendation_score_adjustment': 3.0,
            'news_only_without_price_confirmation': 'watch_or_validation_priority_only',
            'high_risk_disclosure': 'block_or_avoid',
            'purpose': 'Fuse hourly issue/news/disclosure context into the next recommendation and validation batch.',
        },
        'item_count': len(items),
        'by_action': {k: sum(1 for x in items if x['action'] == k) for k in sorted({x['action'] for x in items})},
        'items': items[:80],
        'by_symbol': by_symbol,
        'source_issues': source_issues[:30],
        'summary': {
            'next_batch_priority': [x['symbol'] for x in items if x['action'] == 'next_batch_priority'][:20],
            'watch_or_validation_priority': [x['symbol'] for x in items if x['action'] == 'watch_or_validation_priority'][:20],
            'block_or_avoid': [x['symbol'] for x in items if x['action'] == 'block_or_avoid'][:20],
        },
    }
    attach_contract(
        packet,
        'next_trade_issue_context_agent',
        status='ok',
        inputs={'price_issue_scout': bool(price_issues), 'news_issue_scout': bool(news_issues), 'recommendations': bool(recs)},
        outputs={'item_count': len(items), 'by_action': packet['by_action']},
        metrics={'source_issue_count': len(source_issues), 'symbol_count': len(items)},
        warnings=[],
        next_actions=['Run hourly before recommendation batches; keep as context/validation priority, not order authority.'],
    )
    OUT.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(packet, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
