#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.database import init_db, get_connection

US_SEEDS = [
    # Mega/liquid anchors
    'AAPL','MSFT','NVDA','SPY','QQQ','GOOGL','AMZN','META','TSLA','AMD','AVGO','CRM','ORCL','ADBE','INTC','JPM','V','MA','UNH','LLY','XOM','COST','HD',
    # Existing growth/cyclical/defensive seeds
    'NFLX','NOW','TXN','QCOM','AMAT','MU','PANW','SHOP','UBER','ABNB','BKNG','BA','CAT','GE','GS','MS','BAC','WMT','TGT','NKE','PEP','KO','MCD','DIS','MRK','PFE','TMO','ISRG','LIN','NEE','PLTR','SMCI','ARM','SNOW','MDB','CRWD','ZS','NET','DDOG','DE','LMT','RTX','CVX','COP','SLB',
    # More active discovery breadth: liquid sector leaders and high-beta growth names
    'ASML','TSM','LRCX','KLAC','MRVL','ON','NXPI','MPWR','ANET','DELL','HPE','IBM','ACN','INTU','ADSK','TEAM','WDAY','HUBS','OKTA','BILL','S','U','RBLX','TTD','APP','ROKU','COIN','HOOD','XYZ','PYPL','MELI','SE','PDD','BABA','JD','LI','NIO','XPEV','RIVN','F','GM','TM','HMC','STLA','ELF','LULU','SBUX','CMG','YUM','LOW','TJX','ROST','PG','CL','KMB','MDLZ','MNST','CELH','ABBV','JNJ','AMGN','GILD','VRTX','REGN','MRNA','BIIB','ZTS','DHR','SYK','BSX','MDT','PGR','AXP','BLK','SCHW','C','WFC','KKR','BX','SPGI','MCO','ICE','CME','URI','ETN','EMR','HON','MMM','NOC','GD','EOG','OXY','HAL','BKR','KMI','WMB','ENPH','FSLR','SEDG','BE','CCJ','NEM','FCX','SCCO','NUE','STLD','APD','SHW','CEG','SO','DUK','AEP','EXC','AWK','AMT','PLD','EQIX','DLR','O','VNQ','IWM','DIA','XLK','XLF','XLE','XLV','XLY','XLI','XLC','XLP','XLU','XLB','SMH','ARKK','XBI'
]
KR_SEEDS = [
    '005930.KS','000660.KS','035420.KS','005380.KS','068270.KS','035720.KS','051910.KS','000270.KS','012330.KS','105560.KS','055550.KS','066570.KS','028260.KS','096770.KS','003550.KS','017670.KS','032830.KS',
    '006400.KS','207940.KS','373220.KS','005490.KS','015760.KS','034020.KS','009540.KS','086790.KS','316140.KS','033780.KS','011200.KS','010130.KS','018260.KS','086280.KS','024110.KS','251270.KS','009150.KS','010950.KS','034730.KS','011070.KS','030200.KS','003670.KS','090430.KS','326030.KS','352820.KS','259960.KS','036570.KS','302440.KS','047810.KS','161390.KS','128940.KS',
    # Broader KR discovery seeds: semis, batteries, bio, internet/games, defense/shipbuilding, finance, consumer
    '042700.KS','267260.KS','000990.KS','058470.KS','108320.KS','039030.KQ','095340.KQ','357780.KQ','222800.KQ','195870.KQ','091990.KQ','145020.KQ','196170.KQ','214150.KQ','068760.KQ','086900.KQ','237690.KQ','247540.KQ','278280.KQ','003490.KS','180640.KS','241560.KS','272210.KS','112610.KS','064350.KS','079550.KS','001450.KS','010620.KS','138040.KS','267250.KS','071050.KS','138930.KS','006800.KS','039490.KS','071320.KS','139480.KS','004170.KS','021240.KS','271560.KS','192820.KS','214320.KS','383220.KS','293490.KS','112040.KQ','263750.KQ','259960.KQ','293490.KQ','036490.KQ','041510.KQ','122870.KQ','240810.KQ','005290.KQ','064760.KQ','078600.KQ','067160.KQ','089030.KQ','121600.KQ','215200.KQ','222080.KQ','272290.KQ','011790.KS','010060.KS','298020.KS','298050.KS','010120.KS','047050.KS','005830.KS','001040.KS','097950.KS','004370.KS','081660.KS'
]

FAILED_DISCOVERY_PATH = Path("/tmp/universe_discovery_failed_symbols.json")

def load_recent_failed_symbols(days: int = 14) -> set[str]:
    try:
        data = json.loads(FAILED_DISCOVERY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    failed = set()
    for sym, meta in (data or {}).items():
        try:
            ts = datetime.fromisoformat(str(meta.get("last_failed_at")).replace("Z", "+00:00"))
        except Exception:
            ts = None
        if ts and ts >= cutoff:
            failed.add(str(sym).upper())
    return failed

def record_failed_imports(import_result: dict) -> list[str]:
    failed = []
    for row in (((import_result or {}).get("payload") or {}).get("results") or []):
        if row.get("empty") or (int(row.get("inserted") or 0) == 0 and int(row.get("skipped") or 0) == 0):
            sym = str(row.get("symbol") or "").upper()
            if sym:
                failed.append(sym)
    if not failed:
        return []
    try:
        data = json.loads(FAILED_DISCOVERY_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    now = datetime.now(timezone.utc).isoformat()
    for sym in failed:
        meta = data.get(sym) or {}
        meta["last_failed_at"] = now
        meta["failure_count"] = int(meta.get("failure_count") or 0) + 1
        data[sym] = meta
    try:
        FAILED_DISCOVERY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return failed


# Additional liquid discovery backlog.  The original seed pool is now mostly
# already imported, so keep a deeper queue to make each scheduled discovery run
# test new names instead of stalling after a few failed downloads.
US_SEEDS.extend([
    'BRK-B','T','VZ','CMCSA','TMUS','PM','MO','CVS','CI','HUM','ELV','HCA','IDXX','AON','MMC','CB','TRV','ALL','USB','PNC','TFC','COF','DFS','MSCI','NDAQ','ROP','ADP','PAYX','FIS','FI','GPN','EA','TTWO','CDNS','SNPS','MCHP','WDC','STX','GLW','TEL','APH','KEYS','FTNT','DOCU','TWLO','ZM','DASH','CPNG','GTLB','ESTC','FVRR','UPST','AFRM','DKNG','PINS','EXPE','MAR','HLT','RCL','CCL','DAL','UAL','AAL','LUV','FDX','UPS','CSX','UNP','NSC','DECK','ONON','BURL','AZO','ORLY','KMX','TSCO','ULTA','KR','CAG','GIS','KHC','HSY','KDP','TAP','STZ','ADM','MOS','CF','CTVA','FMC','BMY','SNY','NVO','RMD','EW','ALGN','ILMN','DXCM','PODD','CRSP','BEAM','TECH','MSTR','IBIT','GLD','SLV','TLT','HYG','LQD','EEM','EWY'
])
KR_SEEDS.extend([
    '000810.KS','032640.KS','051900.KS','011170.KS','010780.KS','005940.KS','078930.KS','000720.KS','047040.KS','006360.KS','375500.KS','034220.KS','011780.KS','002380.KS','004020.KS','006260.KS','017800.KS','069960.KS','047310.KS','000120.KS','018880.KS','001740.KS','000880.KS','012450.KS','103140.KS','000150.KS','042660.KS','010140.KS','329180.KS','003230.KS','000100.KS','185750.KS','145720.KS','214370.KS','141080.KQ','140410.KQ','086450.KQ','095700.KQ','084370.KQ','101490.KQ','067630.KQ','060250.KQ','036540.KQ','053800.KQ','058970.KQ','065350.KQ','035900.KQ','078340.KQ','131970.KQ','048410.KQ','036930.KQ','067310.KQ','053610.KQ','096530.KQ','099190.KQ','064550.KQ','215000.KQ','032500.KQ','060720.KQ'
])

def existing_symbols() -> set[str]:
    with get_connection() as conn:
        return {r['symbol'] for r in conn.execute("SELECT DISTINCT symbol FROM price_bars WHERE timeframe='1d'").fetchall()}

def round_robin_by_market(symbols: list[str], max_new: int, markets: set[str]) -> list[str]:
    kr=[s for s in symbols if s.endswith('.KS') or s.endswith('.KQ')]
    us=[s for s in symbols if s not in kr]
    buckets=[]
    if 'KR' in markets: buckets.append(kr)
    if 'US' in markets: buckets.append(us)
    selected=[]; seen=set(); idx=0
    while len(selected) < max_new and any(idx < len(b) for b in buckets):
        for b in buckets:
            if idx < len(b) and b[idx] not in seen:
                selected.append(b[idx]); seen.add(b[idx])
                if len(selected) >= max_new: break
        idx += 1
    return selected

def import_symbols(symbols: list[str], start: str) -> dict:
    if not symbols:
        return {'results': []}
    cmd = [str(ROOT / '.venv/bin/python'), 'tools/agents/import_stooq_daily.py', '--start', start, '--symbols', ','.join(symbols)]
    p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=900)
    result = {'cmd': cmd, 'returncode': p.returncode, 'stdout_tail': p.stdout[-4000:], 'stderr_tail': p.stderr[-4000:]}
    try:
        result['payload'] = json.loads(p.stdout)
    except Exception:
        pass
    return result

def main():
    ap = argparse.ArgumentParser(description='Discover external stock candidates and import their daily bars for research universe expansion')
    ap.add_argument('--markets', default='KR,US')
    ap.add_argument('--max-new', type=int, default=24)
    ap.add_argument('--per-run-floor', type=int, default=12, help='Minimum import target when enough new candidates exist; keeps discovery active even if caller uses a conservative max-new')
    ap.add_argument('--start', default='2019-01-01')
    ap.add_argument('--output', default='/tmp/universe_discovery_latest.json')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(); init_db()
    markets = {m.strip().upper() for m in args.markets.split(',') if m.strip()}
    seeds=[]
    if 'KR' in markets: seeds.extend(KR_SEEDS)
    if 'US' in markets: seeds.extend(US_SEEDS)
    seen=set(); ordered=[]
    for sym in seeds:
        if sym not in seen:
            seen.add(sym); ordered.append(sym)
    existing=existing_symbols()
    recent_failed = load_recent_failed_symbols()
    new=[sym for sym in ordered if sym not in existing and sym.upper() not in recent_failed and not sym.startswith('^')]
    target=max(args.max_new, args.per_run_floor)
    selected=round_robin_by_market(new, target, markets)
    import_result={'dry_run': True, 'results': []} if args.dry_run else import_symbols(selected, args.start)
    failed_import_symbols = [] if args.dry_run else record_failed_imports(import_result)
    imported_count = sum(1 for row in (((import_result or {}).get("payload") or {}).get("results") or []) if int(row.get("inserted") or 0) > 0 or int(row.get("skipped") or 0) > 0)
    packet={
        'run_at': datetime.now(timezone.utc).isoformat(),
        'role': 'Universe Discovery Agent',
        'mode': 'external_seed_discovery_import',
        'markets': sorted(markets),
        'seed_count': len(ordered),
        'discovery_policy': {'max_new_requested': args.max_new, 'per_run_floor': args.per_run_floor, 'market_round_robin': True},
        'existing_count': len(existing),
        'new_candidate_count': len(new),
        'selected_for_import': selected,
        'imported_count': imported_count,
        'failed_import_symbols': failed_import_symbols,
        'recent_failed_symbol_count': len(recent_failed),
        'import_result': import_result,
        'real_trading': False,
    }
    Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    if import_result.get('returncode', 0) != 0:
        sys.exit(import_result.get('returncode') or 1)
if __name__ == '__main__': main()
