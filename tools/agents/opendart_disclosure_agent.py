#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, sys, urllib.parse, urllib.request, zipfile, xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.config import load_env_file
from app.database import save_disclosure_events, init_db, list_universe_members
from tools.agents.lib.corporate_actions import detect_corporate_action_text

load_env_file(ROOT / ".env")



def corp_code_cache_path() -> Path:
    return Path('/tmp/opendart_corp_codes.xml')


def ensure_corp_codes(api_key: str) -> Path:
    cache = corp_code_cache_path()
    if cache.exists() and cache.stat().st_size > 0:
        return cache
    url = 'https://opendart.fss.or.kr/api/corpCode.xml?' + urllib.parse.urlencode({'crtfc_key': api_key})
    zip_path = Path('/tmp/opendart_corp_codes.zip')
    with urllib.request.urlopen(url, timeout=60) as res:
        zip_path.write_bytes(res.read())
    with zipfile.ZipFile(zip_path) as zf:
        name = zf.namelist()[0]
        cache.write_bytes(zf.read(name))
    return cache


def load_stock_to_corp_code(api_key: str) -> dict[str, str]:
    xml_path = ensure_corp_codes(api_key)
    root = ET.parse(xml_path).getroot()
    mapping = {}
    for item in root.findall('list'):
        stock_code = (item.findtext('stock_code') or '').strip()
        corp_code = (item.findtext('corp_code') or '').strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code
    return mapping


def stock_code_from_symbol(symbol: str) -> str | None:
    if symbol.endswith('.KS') or symbol.endswith('.KQ'):
        return symbol.split('.')[0]
    return None


def active_kr_symbols() -> list[str]:
    init_db()
    return [m['symbol'] for m in list_universe_members(limit=1000, status='active') if m['symbol'].endswith(('.KS', '.KQ'))]


def fetch_for_symbols(api_key: str, symbols: list[str], begin: str, end: str) -> dict:
    mapping = load_stock_to_corp_code(api_key)
    events = []
    missing = []
    calls = []
    for symbol in symbols:
        stock_code = stock_code_from_symbol(symbol)
        corp_code = mapping.get(stock_code or '')
        if not corp_code:
            missing.append(symbol)
            continue
        data = fetch(api_key, begin, end, corp_code)
        calls.append({'symbol': symbol, 'corp_code': corp_code, 'status': data.get('status'), 'count': len(data.get('list') or [])})
        if data.get('status') == '000':
            events.extend(data.get('list') or [])
        elif data.get('status') == '013':
            continue
    return {'status': '000', 'message': '종목별 조회 완료', 'list': events, 'symbols': symbols, 'missing_symbols': missing, 'calls': calls}

def fetch(api_key: str, begin: str, end: str, corp_code: str | None = None) -> dict:
    params = {"crtfc_key": api_key, "bgn_de": begin.replace("-", ""), "end_de": end.replace("-", ""), "page_count": "100"}
    if corp_code:
        params["corp_code"] = corp_code
    url = "https://opendart.fss.or.kr/api/list.json?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description="Fetch recent OpenDART disclosure list; requires OPENDART_API_KEY")
    ap.add_argument("--begin", default=(date.today() - timedelta(days=7)).isoformat())
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--corp-code")
    ap.add_argument("--symbols", help="Comma-separated symbols such as 005930.KS. Use 'active-kr' for active Korean universe.")
    ap.add_argument("--save", action="store_true", help="Persist disclosure list into paper_trader database")
    ap.add_argument("--output", default="/tmp/opendart_disclosures_latest.json")
    args = ap.parse_args()
    key = os.getenv("OPENDART_API_KEY")
    if not key:
        print(json.dumps({"status": "missing_api_key", "env": "OPENDART_API_KEY"}, ensure_ascii=False))
        return
    if args.symbols:
        symbols = active_kr_symbols() if args.symbols == 'active-kr' else [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
        data = fetch_for_symbols(key, symbols, args.begin, args.end)
    else:
        data = fetch(key, args.begin, args.end, args.corp_code)
    if data.get("status") == "000":
        for item in data.get("list", []) or []:
            ca = detect_corporate_action_text(item.get("report_nm"))
            if ca.get("flagged"):
                item["corporate_action"] = ca
                item["category"] = "corporate_action"
                item["risk_level"] = "high" if ca.get("severity") == "high" else "medium"
    if args.save and data.get("status") == "000":
        data["save_result"] = save_disclosure_events(data.get("list", []))
    Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
