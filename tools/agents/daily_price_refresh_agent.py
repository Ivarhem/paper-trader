#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] if "tools/agents" in str(Path(__file__)) else Path.cwd()
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.database import get_connection, init_db, list_universe_members
from tools.agents.lib.agent_contract import attach_contract


DEFAULT_BENCHMARKS = ["SPY", "QQQ", "^GSPC", "^IXIC", "^KS11", "^KQ11", "KRW=X"]
UNSUPPORTED_PREFIXES = ("KRW-",)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_kr(symbol: str) -> bool:
    return symbol.endswith((".KS", ".KQ")) or symbol.startswith("^KS") or symbol.startswith("^KQ")


def market(symbol: str) -> str:
    return "FX" if symbol in ("KRW=X", "USDKRW=X", "USD/KRW", "USD-KRW") else ("KR" if is_kr(symbol) else "US")


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip().upper() for x in value.split(",") if x.strip()]


def is_supported_daily_symbol(symbol: str) -> bool:
    if not symbol:
        return False
    if symbol.startswith(UNSUPPORTED_PREFIXES):
        return False
    return True


def filter_supported(symbols: list[str]) -> list[str]:
    return [s for s in symbols if is_supported_daily_symbol(s)]


def active_symbols(limit: int) -> list[str]:
    return filter_supported([m["symbol"].upper() for m in list_universe_members(limit=limit, status="active")])


def watch_symbols(conn: sqlite3.Connection, limit: int) -> list[str]:
    rows = conn.execute("SELECT symbol FROM watchlist_items ORDER BY symbol LIMIT ?", (limit,)).fetchall()
    return filter_supported([r["symbol"].upper() for r in rows])


def open_position_symbols(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT symbol FROM positions WHERE quantity > 0 ORDER BY symbol").fetchall()
    return filter_supported([r["symbol"].upper() for r in rows])


def read_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def mover_seed_symbols(limit: int, stock_only: bool = True, path: str = "/tmp/market_mover_seed_latest.json") -> list[str]:
    data = read_json(path)
    rows = (data.get("top_upper_limit_items") or []) + (data.get("top_stock_items") or data.get("items") or [])
    out = []
    for row in rows:
        if stock_only and row.get("probable_stock") is False:
            continue
        sym = str(row.get("symbol") or "").upper().strip()
        if sym:
            out.append(sym)
        if len(out) >= limit:
            break
    return filter_supported(out)


def investor_flow_seed_symbols(limit: int, path: str = "/tmp/investor_flow_seed_latest.json") -> list[str]:
    data = read_json(path)
    rows = data.get("top_symbols") or []
    out = []
    for row in rows:
        sym = str(row.get("symbol") or "").upper().strip()
        if sym:
            out.append(sym)
        if len(out) >= limit:
            break
    return filter_supported(out)


def latest_price_map(conn: sqlite3.Connection, symbols: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sym in symbols:
        row = conn.execute(
            """
            SELECT date, close, exchange, timeframe
            FROM price_bars
            WHERE symbol = ? AND timeframe = '1d'
            ORDER BY date DESC
            LIMIT 1
            """,
            (sym,),
        ).fetchone()
        out[sym] = dict(row) if row else {}
    return out


def chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[idx : idx + size] for idx in range(0, len(items), size)]


def run_import_chunk(symbols: list[str], start: str, end: str | None, timeout_seconds: int) -> dict:
    if not symbols:
        return {"returncode": 0, "skipped": True, "reason": "no symbols"}
    cmd = [sys.executable, "tools/agents/import_stooq_daily.py", "--symbols", ",".join(symbols), "--start", start]
    if end:
        cmd.extend(["--end", end])
    try:
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": cmd,
            "returncode": 124,
            "timeout": True,
            "symbols": symbols,
            "stdout_tail": (exc.stdout or "")[-3000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-3000:] if isinstance(exc.stderr, str) else "",
        }
    payload = {
        "cmd": cmd,
        "returncode": proc.returncode,
        "symbols": symbols,
        "stdout_tail": proc.stdout[-3000:],
        "stderr_tail": proc.stderr[-3000:],
    }
    try:
        payload["parsed"] = json.loads(proc.stdout)
    except Exception:
        pass
    return payload


def run_import(symbols: list[str], start: str, end: str | None, chunk_size: int, timeout_seconds: int) -> dict:
    results = []
    returncode = 0
    for chunk in chunks(symbols, chunk_size):
        result = run_import_chunk(chunk, start, end, timeout_seconds)
        results.append(result)
        if result.get("returncode") != 0 and returncode == 0:
            returncode = int(result.get("returncode") or 1)
    return {"returncode": returncode, "chunks": results, "chunk_size": chunk_size, "timeout_seconds": timeout_seconds}


def max_lag_by_market(after: dict[str, dict]) -> dict[str, int | None]:
    today = datetime.now(timezone.utc).date()
    out: dict[str, int | None] = {"US": None, "KR": None, "FX": None}
    for sym, row in after.items():
        if not row.get("date"):
            continue
        try:
            lag = (today - datetime.fromisoformat(row["date"]).date()).days
        except ValueError:
            continue
        mkt = market(sym)
        out[mkt] = lag if out[mkt] is None else max(out[mkt] or 0, lag)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh daily yfinance bars for recommendation universe freshness")
    parser.add_argument("--symbols", help="Comma-separated explicit symbols. If omitted, active/watch/open/benchmark symbols are used.")
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--active-limit", type=int, default=1000)
    parser.add_argument("--watch-limit", type=int, default=500)
    parser.add_argument("--benchmarks", default=",".join(DEFAULT_BENCHMARKS))
    parser.add_argument("--mover-seed-limit", type=int, default=80, help="Include top Korean mover seed symbols from /tmp/market_mover_seed_latest.json when present.")
    parser.add_argument("--investor-flow-seed-limit", type=int, default=80, help="Include top KR foreign/institution seed symbols from /tmp/investor_flow_seed_latest.json when present.")
    parser.add_argument("--chunk-size", type=int, default=25)
    parser.add_argument("--chunk-timeout-seconds", type=int, default=90)
    parser.add_argument("--end")
    parser.add_argument("--output", default="/tmp/daily_price_refresh_latest.json")
    args = parser.parse_args()

    init_db()
    started = utc_now()
    with get_connection() as conn:
        explicit = split_csv(args.symbols)
        if explicit:
            symbols = filter_supported(explicit)
            source_counts = {"explicit": len(symbols)}
        else:
            active = active_symbols(args.active_limit)
            watch = watch_symbols(conn, args.watch_limit)
            positions = open_position_symbols(conn)
            benchmarks = split_csv(args.benchmarks)
            mover_seed = mover_seed_symbols(args.mover_seed_limit)
            investor_flow_seed = investor_flow_seed_symbols(args.investor_flow_seed_limit)
            symbols = filter_supported(sorted(set(active + watch + positions + benchmarks + mover_seed + investor_flow_seed)))
            source_counts = {
                "active": len(active),
                "watch": len(watch),
                "open_positions": len(positions),
                "benchmarks": len(benchmarks),
                "mover_seed": len(mover_seed),
                "investor_flow_seed": len(investor_flow_seed),
            }
        before = latest_price_map(conn, symbols)

    start_date = datetime.now(timezone.utc).date()
    start = start_date.replace(year=start_date.year - 1).isoformat() if args.lookback_days > 365 else (start_date.toordinal() - args.lookback_days)
    if isinstance(start, int):
        start = datetime.fromordinal(start).date().isoformat()

    # yfinance treats --end as exclusive. If omitted, use tomorrow so today's closed KR bar
    # can be imported during evening Asia/Seoul pipeline runs instead of lagging one session.
    effective_end = args.end or (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    imports = run_import(symbols, start, effective_end, args.chunk_size, args.chunk_timeout_seconds)
    with get_connection() as conn:
        after = latest_price_map(conn, symbols)

    stale_symbols = [sym for sym, row in after.items() if not row.get("date")]
    failed_symbols = []
    for chunk in imports.get("chunks", []) or []:
        parsed = chunk.get("parsed") or {}
        if chunk.get("timeout") or chunk.get("returncode") != 0:
            failed_symbols.extend(chunk.get("symbols") or [])
        for item in parsed.get("results", []) or []:
            if item.get("empty") or item.get("error"):
                failed_symbols.append(item.get("symbol"))
    failed_symbols = sorted({x for x in failed_symbols if x})
    warnings = []
    if stale_symbols:
        warnings.append(f"{len(stale_symbols)} symbols still have no daily price data")
    if failed_symbols:
        warnings.append(f"{len(failed_symbols)} symbols returned empty/error from yfinance")
    if imports.get("returncode") != 0:
        warnings.append("price import command failed")

    changed = [
        sym
        for sym in symbols
        if (before.get(sym) or {}).get("date") != (after.get(sym) or {}).get("date")
    ]
    status = "failed" if imports.get("returncode") != 0 else ("degraded" if warnings else "ok")
    packet = {
        "run_at": utc_now(),
        "started_at": started,
        "mode": "daily_price_refresh",
        "provider": "yfinance",
        "timeframe": "1d",
        "adjusted": True,
        "lookback_days": args.lookback_days,
        "effective_end": effective_end,
        "source_counts": source_counts,
        "symbol_count": len(symbols),
        "refreshed_count": len(changed),
        "chunk_size": args.chunk_size,
        "chunk_timeout_seconds": args.chunk_timeout_seconds,
        "symbols": symbols,
        "changed_symbols": changed[:200],
        "stale_symbols": stale_symbols,
        "failed_symbols": failed_symbols,
        "max_lag_by_market_days": max_lag_by_market(after),
        "before_latest": before,
        "after_latest": after,
        "import_result": imports,
        "real_trading": False,
    }
    attach_contract(
        packet,
        "daily_price_refresh_agent",
        status=status,
        inputs={"symbols": args.symbols or "active+watch+open+benchmarks+mover_seed", "lookback_days": args.lookback_days},
        outputs={"symbol_count": len(symbols), "refreshed_count": len(changed), "failed_symbols": failed_symbols},
        metrics={"symbol_count": len(symbols), "refreshed_count": len(changed), "stale_count": len(stale_symbols), "failed_count": len(failed_symbols)},
        warnings=warnings,
        next_actions=["Repair failed/stale symbols before trusting recommendations."] if warnings else [],
    )
    Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    if imports.get("returncode") != 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
