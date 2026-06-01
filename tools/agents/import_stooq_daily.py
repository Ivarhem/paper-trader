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
import argparse, ast, json, sqlite3, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.database import create_portfolio_snapshot, get_connection, init_db
from app.symbols import display_name


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _request_text(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 paper_trader/0.1"})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _epoch(date_text: str) -> int:
    return int(datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc).timestamp())


def fetch_yahoo_chart_rows(sym: str, start: str, end: str) -> list[dict]:
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym) + "?" + urllib.parse.urlencode({
        "period1": _epoch(start),
        "period2": _epoch(end) + 86400,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    })
    data = json.loads(_request_text(url))
    result = ((data.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return []
    ts = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adj = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    out = []
    for i, stamp in enumerate(ts):
        try:
            close = adj[i] if i < len(adj) and adj[i] is not None else quote["close"][i]
            row = {
                "date": datetime.fromtimestamp(int(stamp), tz=timezone.utc).date().isoformat(),
                "open": float(quote["open"][i]),
                "high": float(quote["high"][i]),
                "low": float(quote["low"][i]),
                "close": float(close),
                "volume": float(quote["volume"][i] or 0),
            }
        except Exception:
            continue
        if row["close"] > 0:
            out.append(row)
    return out


def fetch_naver_kr_rows(sym: str, start: str, end: str) -> list[dict]:
    code = sym.split(".")[0]
    url = "https://fchart.stock.naver.com/siseJson.nhn?" + urllib.parse.urlencode({
        "symbol": code,
        "requestType": 1,
        "startTime": start.replace("-", ""),
        "endTime": end.replace("-", ""),
        "timeframe": "day",
    })
    text = _request_text(url)
    start_idx = text.find("[")
    end_idx = text.rfind("]")
    if start_idx < 0 or end_idx < start_idx:
        return []
    payload = ast.literal_eval(text[start_idx:end_idx + 1])
    out = []
    for row in payload:
        if not isinstance(row, list) or not row or str(row[0]).lower() == "날짜":
            continue
        try:
            date = str(row[0])
            out.append({
                "date": f"{date[:4]}-{date[4:6]}-{date[6:8]}",
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5] or 0),
            })
        except Exception:
            continue
    return out


def yfinance_rows(sym: str, start: str, end: str) -> list[dict]:
    import yfinance as yf
    frame = yf.download(sym, start=start, end=end, interval="1d", auto_adjust=True, progress=False, threads=False)
    if frame.empty:
        return []
    rows = []
    for idx, row in frame.iterrows():
        def value(name: str) -> float:
            v = row[name]
            if hasattr(v, "iloc"):
                v = v.iloc[0]
            return float(v)
        try:
            rows.append({
                "date": idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10],
                "open": value("Open"),
                "high": value("High"),
                "low": value("Low"),
                "close": value("Close"),
                "volume": value("Volume"),
            })
        except Exception:
            continue
    return rows


def fallback_rows(sym: str, start: str, end: str) -> tuple[list[dict], str]:
    if sym.endswith((".KS", ".KQ")):
        rows = fetch_naver_kr_rows(sym, start, end)
        return rows, "naver_chart"
    rows = fetch_yahoo_chart_rows(sym, start, end)
    return rows, "yahoo_chart"


def import_symbol(symbol: str, start: str, end: str) -> dict:
    init_db()
    sym = symbol.upper().strip()
    warnings = []
    # For Korean listings, Yahoo can lag or disagree with KRX/Naver daily bars
    # around the latest session. Prefer the domestic chart source and only fall
    # back to Yahoo when it is unavailable.
    if sym.endswith((".KS", ".KQ")):
        source = "naver_chart"
        try:
            rows = fetch_naver_kr_rows(sym, start, end)
        except Exception as exc:
            rows = []
            warnings.append(f"naver_failed:{type(exc).__name__}")
        if not rows:
            source = "yfinance_adjusted"
            try:
                rows = yfinance_rows(sym, start, end)
            except Exception as exc:
                rows = []
                warnings.append(f"yfinance_failed:{type(exc).__name__}")
    else:
        source = "yfinance_adjusted"
        try:
            rows = yfinance_rows(sym, start, end)
        except Exception as exc:
            rows = []
            warnings.append(f"yfinance_failed:{type(exc).__name__}")
        if not rows:
            try:
                rows, source = fallback_rows(sym, start, end)
            except Exception as exc:
                rows = []
                warnings.append(f"fallback_failed:{type(exc).__name__}")
    inserted = skipped = 0
    created = utc_now()
    if not rows:
        return {"symbol": sym, "inserted": 0, "skipped": 0, "source": source, "timeframe": "1d", "empty": True, "warnings": warnings}
    with get_connection() as conn:
        try:
            conn.execute("PRAGMA busy_timeout=30000")
        except Exception:
            pass
        for row in rows:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO price_bars (symbol,date,open,high,low,close,volume,market,exchange,timeframe,created_at)
                    VALUES (?,?,?,?,?,?,?,'stock',?,'1d',?)
                    ON CONFLICT(symbol, date) DO UPDATE SET
                        open=excluded.open,
                        high=excluded.high,
                        low=excluded.low,
                        close=excluded.close,
                        volume=excluded.volume,
                        exchange=excluded.exchange,
                        timeframe=excluded.timeframe,
                        created_at=excluded.created_at
                    """,
                    (sym, row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"], source, created),
                )
                if cur.rowcount:
                    inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1
        name = display_name(sym)
        note = f"{name} daily via {source}" if name != sym else f"stock daily via {source}"
        conn.execute(
            """
            INSERT INTO watchlist_items (symbol,note,created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET note = COALESCE(watchlist_items.note, excluded.note)
            """,
            (sym, note, created),
        )
        create_portfolio_snapshot(conn)
    return {"symbol": sym, "inserted": inserted, "skipped": skipped, "source": source, "timeframe": "1d", "warnings": warnings}


def main():
    ap = argparse.ArgumentParser(description="Import daily US stock bars via yfinance")
    ap.add_argument("--symbols", default="AAPL,MSFT,NVDA,SPY,QQQ,005930.KS,000660.KS,035420.KS,005380.KS,068270.KS,035720.KS,051910.KS,005930.KS,000660.KS,035420.KS,005380.KS,068270.KS,035720.KS,051910.KS")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default=datetime.now().date().isoformat())
    args = ap.parse_args()
    results = []
    for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        results.append(import_symbol(symbol, args.start, args.end))
    import json
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
