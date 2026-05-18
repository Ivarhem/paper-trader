
from __future__ import annotations
CORPORATE_ACTION_KEYWORDS = ['감자','감자결정','무상감자','유상감자','자본감소','주식분할','액면분할','액면병합','주식병합','병합','거래정지','매매거래정지','권리락','신주권','변경상장']
HIGH_KEYWORDS = ['감자','감자결정','무상감자','유상감자','자본감소','거래정지','매매거래정지','액면병합','주식병합']

def detect_corporate_action_text(text: str | None) -> dict:
    compact = str(text or '').replace(' ', '')
    hits = [kw for kw in CORPORATE_ACTION_KEYWORDS if kw in compact]
    high_hits = [kw for kw in HIGH_KEYWORDS if kw in compact]
    return {'flagged': bool(hits), 'keywords': hits, 'severity': 'high' if high_hits else ('medium' if hits else 'none')}

def symbol_corporate_action_events(conn, symbol: str, lookback_yyyymmdd: str | None = None, limit: int = 20) -> list[dict]:
    params=[symbol]; where='symbol = ?'
    if lookback_yyyymmdd:
        where += ' AND rcept_dt >= ?'; params.append(lookback_yyyymmdd)
    rows=conn.execute(f"""SELECT rcept_no,rcept_dt,corp_name,symbol,report_nm,category,risk_level FROM disclosure_events WHERE {where} ORDER BY rcept_dt DESC,id DESC LIMIT ?""", [*params, limit]).fetchall()
    events=[]
    for r in rows:
        item=dict(r); detected=detect_corporate_action_text(item.get('report_nm'))
        if detected['flagged']:
            item['corporate_action']=detected; events.append(item)
    return events

def ensure_corporate_action_flags(conn) -> None:
    conn.execute('''CREATE TABLE IF NOT EXISTS corporate_action_flags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        reason TEXT,
        source TEXT NOT NULL DEFAULT 'manual',
        event_date TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_corporate_action_flags_symbol_active ON corporate_action_flags(symbol, active)')


def manual_corporate_action_flags(conn, symbol: str) -> list[dict]:
    ensure_corporate_action_flags(conn)
    rows = conn.execute('''SELECT id, symbol, event_type, severity, reason, source, event_date, active, created_at
                           FROM corporate_action_flags WHERE symbol=? AND active=1 ORDER BY id DESC LIMIT 20''', (symbol,)).fetchall()
    return [dict(r) for r in rows]


def symbol_corporate_action_risk(conn, symbol: str, lookback_yyyymmdd: str | None = None) -> dict:
    events=symbol_corporate_action_events(conn, symbol, lookback_yyyymmdd=lookback_yyyymmdd)
    manual_flags=manual_corporate_action_flags(conn, symbol)
    severities=[(e.get('corporate_action') or {}).get('severity') for e in events] + [f.get('severity') for f in manual_flags]
    severity='high' if 'high' in severities else ('medium' if ('medium' in severities or events or manual_flags) else 'none')
    keywords=sorted({kw for e in events for kw in (e.get('corporate_action') or {}).get('keywords', [])} | {f.get('event_type') for f in manual_flags if f.get('event_type')})
    return {'flagged': bool(events or manual_flags), 'severity': severity, 'event_count': len(events) + len(manual_flags), 'events': events[:5], 'manual_flags': manual_flags[:5], 'keywords': keywords}
