from __future__ import annotations
import json
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

SYMBOL_NAMES = {
    # US
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "SPY": "SPDR S&P 500 ETF",
    "QQQ": "Invesco QQQ ETF",
    "GOOGL": "Alphabet",
    "AMZN": "Amazon",
    "META": "Meta Platforms",
    "TSLA": "Tesla",
    "AMD": "AMD",
    "AVGO": "Broadcom",
    "CRM": "Salesforce",
    "ORCL": "Oracle",
    "ADBE": "Adobe",
    "INTC": "Intel",
    "JPM": "JPMorgan Chase",
    "V": "Visa",
    "MA": "Mastercard",
    "UNH": "UnitedHealth",
    "LLY": "Eli Lilly",
    "XOM": "Exxon Mobil",
    "COST": "Costco",
    "HD": "Home Depot",
    "NFLX": "Netflix",
    "NOW": "ServiceNow",
    "TXN": "Texas Instruments",
    "QCOM": "Qualcomm",
    "AMAT": "Applied Materials",
    "MU": "Micron",
    "PANW": "Palo Alto Networks",
    "SHOP": "Shopify",
    "UBER": "Uber",
    "ABNB": "Airbnb",
    "BKNG": "Booking Holdings",
    "BA": "Boeing",
    "CAT": "Caterpillar",
    "GE": "GE Aerospace",
    "GS": "Goldman Sachs",
    "BAC": "Bank of America",
    "WMT": "Walmart",
    "TGT": "Target",
    "NKE": "Nike",
    "PEP": "PepsiCo",
    "KO": "Coca-Cola",
    "MCD": "McDonald's",
    "DIS": "Disney",
    "MRK": "Merck",
    "PFE": "Pfizer",
    "TMO": "Thermo Fisher Scientific",
    "ISRG": "Intuitive Surgical",
    "LIN": "Linde",
    "NEE": "NextEra Energy",
    "PLTR": "Palantir",
    "SMCI": "Super Micro Computer",
    "ARM": "Arm Holdings",
    "SNOW": "Snowflake",
    "MDB": "MongoDB",
    "CRWD": "CrowdStrike",
    "ZS": "Zscaler",
    "NET": "Cloudflare",
    "DDOG": "Datadog",
    "DE": "Deere",
    "LMT": "Lockheed Martin",
    "RTX": "RTX",
    "CVX": "Chevron",
    "COP": "ConocoPhillips",
    "SLB": "Schlumberger",

    # Korea
    "005930.KS": "삼성전자",
    "000660.KS": "SK하이닉스",
    "035420.KS": "NAVER",
    "005380.KS": "현대차",
    "068270.KS": "셀트리온",
    "035720.KS": "카카오",
    "051910.KS": "LG화학",
    "000270.KS": "기아",
    "012330.KS": "현대모비스",
    "105560.KS": "KB금융",
    "055550.KS": "신한지주",
    "066570.KS": "LG전자",
    "028260.KS": "삼성물산",
    "028050.KS": "삼성E&A",
    "096770.KS": "SK이노베이션",
    "003550.KS": "LG",
    "017670.KS": "SK텔레콤",
    "032830.KS": "삼성생명",
    "006400.KS": "삼성SDI",
    "207940.KS": "삼성바이오로직스",
    "373220.KS": "LG에너지솔루션",
    "005490.KS": "POSCO홀딩스",
    "015760.KS": "한국전력",
    "034020.KS": "두산에너빌리티",
    "009540.KS": "HD한국조선해양",
    "086790.KS": "하나금융지주",
    "316140.KS": "우리금융지주",
    "033780.KS": "KT&G",
    "011200.KS": "HMM",
    "010130.KS": "고려아연",
    "018260.KS": "삼성에스디에스",
    "086280.KS": "현대글로비스",
    "024110.KS": "기업은행",
    "251270.KS": "넷마블",
    "009150.KS": "삼성전기",
    "010950.KS": "S-Oil",
    "034730.KS": "SK",
    "011070.KS": "LG이노텍",
    "030200.KS": "KT",
    "003670.KS": "포스코퓨처엠",
    "090430.KS": "아모레퍼시픽",
    "326030.KS": "SK바이오팜",
    "352820.KS": "하이브",
    "259960.KS": "크래프톤",
    "036570.KS": "엔씨소프트",
    "302440.KS": "SK바이오사이언스",
    "047810.KS": "한국항공우주",
    "161390.KS": "한국타이어앤테크놀로지",
    "128940.KS": "한미약품",
    "001460.KS": "BYC",
    "026960.KS": "동서",
    "036090.KQ": "위지트",
    "042700.KS": "한미반도체",
    "267260.KS": "HD현대일렉트릭",
    "000990.KS": "DB하이텍",
    "058470.KS": "리노공업",
    "108320.KS": "LX세미콘",
    "039030.KQ": "이오테크닉스",
    "095340.KQ": "ISC",
    "357780.KQ": "솔브레인",
    "222800.KQ": "심텍",
    "195870.KQ": "해성디에스",
    "091990.KQ": "셀트리온헬스케어",
    "145020.KQ": "휴젤",
    "196170.KQ": "알테오젠",
    "214150.KQ": "클래시스",
    "068760.KQ": "셀트리온제약",
    "086900.KQ": "메디톡스",
    "237690.KQ": "에스티팜",
    "247540.KQ": "에코프로비엠",
    "278280.KQ": "천보",
    "ASML": "ASML",
    "TSM": "TSMC",
    "LRCX": "Lam Research",
    "KLAC": "KLA",
    "MRVL": "Marvell Technology",
    "ON": "ON Semiconductor",
    "NXPI": "NXP Semiconductors",
    "MPWR": "Monolithic Power Systems",
    "ANET": "Arista Networks",
    "DELL": "Dell Technologies",
    "HPE": "Hewlett Packard Enterprise",
    "^KS11": "KOSPI",
    "^KQ11": "KOSDAQ",
}


def display_name(symbol: str) -> str:
    return SYMBOL_NAMES.get(symbol) or _dynamic_name_for_symbol(symbol) or symbol


def symbol_meta(symbol: str) -> dict:
    return {"symbol": symbol, "name": display_name(symbol)}


SYMBOL_ALIASES = {
    # Common short names that are not naturally derivable from provider data.
    "마소": "MSFT",
    "페이스북": "META",
    "삼성전자": "005930.KS",
    "삼전": "005930.KS",
    "SK하이닉스": "000660.KS",
    "하이닉스": "000660.KS",
    "네이버": "035420.KS",
    "NAVER": "035420.KS",
    "현대차": "005380.KS",
    "현대자동차": "005380.KS",
    "셀트리온": "068270.KS",
    "카카오": "035720.KS",
    "LG화학": "051910.KS",
    "엘지화학": "051910.KS",
    "기아": "000270.KS",
    "현대모비스": "012330.KS",
    "KB금융": "105560.KS",
    "신한지주": "055550.KS",
    "LG전자": "066570.KS",
    "삼성바이오로직스": "207940.KS",
    "삼성엔지니어링": "028050.KS",
    "삼성E&A": "028050.KS",
    "삼성이앤에이": "028050.KS",
    "Samsung Engineering": "028050.KS",
    "Samsung E&A": "028050.KS",
    "Samsung E and A": "028050.KS",
    "LG에너지솔루션": "373220.KS",
    "엘지에너지솔루션": "373220.KS",
    "포스코홀딩스": "005490.KS",
    "한국전력": "015760.KS",
    "두산에너빌리티": "034020.KS",
    "하나금융지주": "086790.KS",
    "우리금융지주": "316140.KS",
    "하이브": "352820.KS",
    "크래프톤": "259960.KS",
    "SQ": "XYZ",
    "Block": "XYZ",
    "Block Inc": "XYZ",
    "블록": "XYZ",
    "BYC": "001460.KS",
    "비와이씨": "001460.KS",
    "동서": "026960.KS",
    "위지트": "036090.KQ",
}


US_LOCALIZED_NAMES = {
    "AAPL": ["애플", "애플컴퓨터"],
    "MSFT": ["마이크로소프트", "마이크로소프트사"],
    "NVDA": ["엔비디아"],
    "GOOGL": ["구글", "알파벳"],
    "AMZN": ["아마존"],
    "META": ["메타", "메타플랫폼스"],
    "TSLA": ["테슬라"],
    "AMD": ["AMD", "에이엠디"],
    "AVGO": ["브로드컴"],
    "CRM": ["세일즈포스"],
    "ORCL": ["오라클"],
    "ADBE": ["어도비"],
    "INTC": ["인텔"],
    "JPM": ["JP모건", "제이피모건", "제이피모간"],
    "V": ["비자"],
    "MA": ["마스터카드"],
    "UNH": ["유나이티드헬스", "유나이티드헬스그룹"],
    "LLY": ["일라이릴리", "릴리"],
    "XOM": ["엑슨모빌"],
    "COST": ["코스트코"],
    "HD": ["홈디포"],
    "NFLX": ["넷플릭스"],
    "NOW": ["서비스나우"],
    "TXN": ["텍사스인스트루먼트"],
    "QCOM": ["퀄컴"],
    "AMAT": ["어플라이드머티어리얼즈"],
    "MU": ["마이크론"],
    "PANW": ["팔로알토네트웍스"],
    "SHOP": ["쇼피파이"],
    "UBER": ["우버"],
    "ABNB": ["에어비앤비"],
    "BKNG": ["부킹홀딩스"],
    "BA": ["보잉"],
    "CAT": ["캐터필러"],
    "GE": ["GE", "제너럴일렉트릭"],
    "GS": ["골드만삭스"],
    "BAC": ["뱅크오브아메리카"],
    "WMT": ["월마트"],
    "TGT": ["타겟"],
    "NKE": ["나이키"],
    "PEP": ["펩시", "펩시코"],
    "KO": ["코카콜라"],
    "MCD": ["맥도날드"],
    "DIS": ["디즈니"],
    "MRK": ["머크"],
    "PFE": ["화이자"],
    "TMO": ["써모피셔", "써모피셔사이언티픽"],
    "ISRG": ["인튜이티브서지컬"],
    "LIN": ["린데"],
    "NEE": ["넥스트에라에너지"],
    "PLTR": ["팔란티어"],
    "SMCI": ["슈퍼마이크로", "슈퍼마이크로컴퓨터"],
    "ARM": ["암홀딩스"],
    "SNOW": ["스노우플레이크"],
    "MDB": ["몽고DB", "몽고디비"],
    "CRWD": ["크라우드스트라이크"],
    "ZS": ["지스케일러"],
    "NET": ["클라우드플레어"],
    "DDOG": ["데이터독"],
    "DE": ["디어", "존디어"],
    "LMT": ["록히드마틴"],
    "RTX": ["RTX"],
    "CVX": ["셰브론", "쉐브론"],
    "COP": ["코노코필립스"],
    "SLB": ["슐럼버거"],
}


def _localized_aliases() -> dict[str, str]:
    out = {}
    for sym, names in US_LOCALIZED_NAMES.items():
        for name in names:
            out[_norm_query(name)] = sym
    return out


def _norm_query(text: str) -> str:
    text = str(text or '').strip().lower()
    text = text.replace('㈜', '').replace('(주)', '')
    text = re.sub(r'[\s\-_.,&()]+', '', text)
    for suffix in ('주식회사', '주식', '보통주', '우선주', 'incorporated', 'corporation', 'company', 'holdings', 'holding', 'limited', 'ltd', 'inc', 'corp', 'co'):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def _kr_stock_code(symbol: str) -> str | None:
    upper = str(symbol or '').upper()
    if upper.endswith(('.KS', '.KQ')) and len(upper) >= 9:
        return upper.split('.')[0]
    return None


def _symbol_from_kr_stock_code(stock_code: str) -> str:
    code = str(stock_code or '').strip()
    if not code:
        return code
    for suffix in ('.KS', '.KQ'):
        sym = f'{code}{suffix}'
        if sym in SYMBOL_NAMES:
            return sym
    db_sym = _db_symbol_for_stock_code(code)
    if db_sym:
        return db_sym
    return f'{code}.KS'


@lru_cache(maxsize=1)
def _dart_name_maps() -> tuple[dict[str, dict], dict[str, dict]]:
    by_name: dict[str, dict] = {}
    by_code: dict[str, dict] = {}
    path = Path('/tmp/opendart_corp_codes.xml')
    if not path.exists() or path.stat().st_size <= 0:
        return by_name, by_code
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return by_name, by_code
    for item in root.findall('list'):
        stock_code = (item.findtext('stock_code') or '').strip()
        corp_name = (item.findtext('corp_name') or '').strip()
        corp_code = (item.findtext('corp_code') or '').strip()
        if not stock_code or not corp_name:
            continue
        row = {'stock_code': stock_code, 'corp_name': corp_name, 'corp_code': corp_code}
        by_code.setdefault(stock_code, row)
        by_name.setdefault(_norm_query(corp_name), row)
    return by_name, by_code


def _resolve_from_dart(query: str) -> dict | None:
    n = _norm_query(query)
    if not n:
        return None
    by_name, _ = _dart_name_maps()
    row = by_name.get(n)
    matched_by = 'opendart_name'
    alternatives: list[str] = []
    if not row:
        partial = [v for k, v in by_name.items() if n in k or k in n]
        if not partial:
            return None
        partial.sort(key=lambda x: (len(x.get('corp_name') or ''), x.get('corp_name') or ''))
        row = partial[0]
        alternatives = [_symbol_from_kr_stock_code(x['stock_code']) for x in partial[1:6]]
        matched_by = 'opendart_partial_name'
    sym = _symbol_from_kr_stock_code(row['stock_code'])
    return {'symbol': sym, 'name': row.get('corp_name') or display_name(sym), 'matched_by': matched_by, 'query': query, 'alternatives': alternatives}


def _dynamic_name_for_symbol(symbol: str) -> str | None:
    code = _kr_stock_code(symbol)
    if code:
        _, by_code = _dart_name_maps()
        row = by_code.get(code)
        if row and row.get('corp_name'):
            return row['corp_name']
    db_name = _db_name_for_symbol(symbol)
    if db_name:
        return db_name
    return None


def _db_path() -> str | None:
    try:
        from app.config import get_settings
        return str(get_settings().database_path)
    except Exception:
        return None


def _db_symbol_for_stock_code(stock_code: str) -> str | None:
    db = _db_path()
    if not db:
        return None
    try:
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT symbol FROM price_bars WHERE symbol IN (?, ?) LIMIT 1",
            (f'{stock_code}.KS', f'{stock_code}.KQ'),
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _db_name_for_symbol(symbol: str) -> str | None:
    db = _db_path()
    if not db:
        return None
    try:
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT payload_json FROM universe_members WHERE symbol=? LIMIT 1", (symbol,)).fetchall()
        conn.close()
    except Exception:
        return None
    for (payload_json,) in rows:
        try:
            payload = json.loads(payload_json or '{}')
        except Exception:
            payload = {}
        name = payload.get('name')
        if name:
            return str(name)
    return None


def _resolve_from_db_names(query: str) -> dict | None:
    n = _norm_query(query)
    db = _db_path()
    if not db or not n:
        return None
    try:
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT symbol, payload_json FROM universe_members LIMIT 2000").fetchall()
        conn.close()
    except Exception:
        return None
    exact = []
    partial = []
    for sym, payload_json in rows:
        try:
            payload = json.loads(payload_json or '{}')
        except Exception:
            payload = {}
        name = payload.get('name')
        if not name:
            continue
        nn = _norm_query(name)
        if nn == n:
            exact.append((sym, name))
        elif len(n) >= 3 and len(nn) >= 3 and (n in nn or nn in n):
            partial.append((sym, name))
    matches = exact or partial
    if not matches:
        return None
    sym, name = matches[0]
    return {'symbol': sym, 'name': name, 'matched_by': 'db_universe_name', 'query': query, 'alternatives': [x[0] for x in matches[1:6]]}


def _cache_path() -> Path:
    return Path('/tmp/symbol_resolve_cache.json')


def _load_resolve_cache() -> dict:
    try:
        return json.loads(_cache_path().read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_resolve_cache(cache: dict) -> None:
    try:
        _cache_path().write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass


def _resolve_from_yfinance(query: str) -> dict | None:
    n = _norm_query(query)
    if not n:
        return None
    cache = _load_resolve_cache()
    cached = cache.get(n)
    now = time.time()
    if cached and now - float(cached.get('cached_at') or 0) < 86400:
        out = dict(cached.get('result') or {})
        if out:
            out['matched_by'] = f"{out.get('matched_by')}_cache"
            return out
    try:
        import yfinance as yf
        search = yf.Search(query, max_results=10)
        quotes = getattr(search, 'quotes', None) or []
    except Exception:
        return None
    candidates = []
    for q in quotes:
        sym = str(q.get('symbol') or '').upper()
        quote_type = str(q.get('quoteType') or q.get('typeDisp') or '').lower()
        if not sym or quote_type not in ('equity', 'stock'):
            continue
        exchange = str(q.get('exchange') or '').upper()
        name = q.get('longname') or q.get('shortname') or sym
        score = float(q.get('score') or 0)
        penalty = 0
        if exchange in ('NMS', 'NYQ', 'ASE', 'NGM', 'NAS', 'PCX'):
            penalty -= 20
        if exchange in ('KSC', 'KOE'):
            penalty -= 10
        if '.' in sym and exchange not in ('KSC', 'KOE'):
            penalty += 10
        candidates.append((penalty, -score, sym, name, q))
    if not candidates:
        return None
    candidates.sort()
    _, _, sym, name, quote = candidates[0]
    out = {
        'symbol': sym,
        'name': name,
        'matched_by': 'yfinance_search',
        'query': query,
        'exchange': quote.get('exchange'),
        'alternatives': [x[2] for x in candidates[1:6]],
    }
    cache[n] = {'cached_at': now, 'result': out}
    _save_resolve_cache(cache)
    return out


def resolve_symbol(query: str) -> dict:
    raw = str(query or '').strip()
    upper = raw.upper()
    if upper in SYMBOL_NAMES:
        return {"symbol": upper, "name": display_name(upper), "matched_by": "ticker", "query": raw}
    if upper.endswith(('.KS', '.KQ')) and len(upper.split('.')[0]) == 6 and upper.split('.')[0].isdigit():
        return {"symbol": upper, "name": display_name(upper), "matched_by": "ticker", "query": raw}
    if raw in SYMBOL_ALIASES:
        sym = SYMBOL_ALIASES[raw]
        return {"symbol": sym, "name": display_name(sym), "matched_by": "alias", "query": raw}
    n = _norm_query(raw)
    for alias, sym in SYMBOL_ALIASES.items():
        if _norm_query(alias) == n:
            return {"symbol": sym, "name": display_name(sym), "matched_by": "alias", "query": raw}
    localized = _localized_aliases().get(n)
    if localized:
        return {"symbol": localized, "name": display_name(localized), "matched_by": "localized_us_name", "query": raw}
    # Exact or partial company-name match from known symbol table.
    exact=[]; partial=[]
    for sym, name in SYMBOL_NAMES.items():
        nn = _norm_query(name)
        if nn == n:
            exact.append(sym)
        elif len(n) >= 3 and len(nn) >= 3 and (n in nn or nn in n):
            partial.append(sym)
    matches = exact or partial
    if matches:
        sym = matches[0]
        return {"symbol": sym, "name": display_name(sym), "matched_by": "name", "query": raw, "alternatives": matches[1:6]}
    # Korean numeric tickers are usually passed without the Yahoo/Stooq suffix by humans.
    if upper.isdigit() and len(upper) == 6:
        # Default to KOSPI, but keep known KOSDAQ names/symbols on .KQ.
        known_kq = {"036090"}
        suffix = ".KQ" if upper in known_kq else ".KS"
        sym = f"{upper}{suffix}"
        return {"symbol": sym, "name": display_name(sym), "matched_by": "kr_numeric_ticker", "query": raw}
    db_match = _resolve_from_db_names(raw)
    if db_match:
        return db_match
    dart_match = _resolve_from_dart(raw)
    if dart_match:
        return dart_match
    yf_match = _resolve_from_yfinance(raw)
    if yf_match:
        return yf_match
    # Keep backwards-compatible ticker behavior for unknown queries.
    return {"symbol": upper, "name": display_name(upper), "matched_by": "fallback_ticker", "query": raw}
