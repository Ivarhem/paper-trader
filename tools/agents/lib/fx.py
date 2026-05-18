from __future__ import annotations

import bisect
import sqlite3

FX_SYMBOL = "USD/KRW"
FX_SYMBOLS = ("USD/KRW", "USD-KRW", "USDKRW=X", "KRW=X")
DEFAULT_USD_KRW = 1350.0


def is_kr_symbol(symbol: str) -> bool:
    sym = str(symbol or "").upper()
    return sym.endswith((".KS", ".KQ")) or sym.startswith("^KS") or sym.startswith("^KQ") or sym.startswith("KRW-")


def is_usd_symbol(symbol: str) -> bool:
    sym = str(symbol or "").upper()
    return bool(sym) and sym not in FX_SYMBOLS and not is_kr_symbol(sym)


def latest_usdkrw(conn: sqlite3.Connection, default: float = DEFAULT_USD_KRW) -> dict:
    row = conn.execute(
        """
        SELECT symbol, date, close, exchange
        FROM price_bars
        WHERE timeframe='1d' AND symbol IN ('USD/KRW','USD-KRW','USDKRW=X','KRW=X')
        ORDER BY CASE WHEN exchange='smbs_std' THEN 0 WHEN symbol='USD/KRW' THEN 1 WHEN symbol='KRW=X' THEN 2 ELSE 3 END, date DESC
        LIMIT 1
        """
    ).fetchone()
    if row:
        return {"rate": float(row["close"]), "date": row["date"], "symbol": row["symbol"], "source": "smbs_std" if row["exchange"] == "smbs_std" else "price_bars"}
    return {"rate": float(default), "date": None, "symbol": FX_SYMBOL, "source": "default_missing_fx"}


def load_usdkrw_series(conn: sqlite3.Connection, start_date: str | None = None, default: float = DEFAULT_USD_KRW) -> dict:
    params = []
    where = "timeframe='1d' AND symbol IN ('USD/KRW','USD-KRW','USDKRW=X','KRW=X')"
    if start_date:
        where += " AND date>=?"
        params.append(start_date)
    rows = conn.execute(
        f"""SELECT date, close, symbol, exchange
             FROM price_bars
             WHERE {where}
             ORDER BY date, CASE WHEN exchange='smbs_std' THEN 0 WHEN symbol='USD/KRW' THEN 1 WHEN symbol='KRW=X' THEN 2 ELSE 3 END""",
        params,
    ).fetchall()
    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], r)
    chosen = [by_date[d] for d in sorted(by_date)]
    dates = [r["date"] for r in chosen]
    rates = [float(r["close"]) for r in chosen]
    latest = {"rate": rates[-1], "date": dates[-1], "symbol": chosen[-1]["symbol"], "source": "smbs_std" if chosen[-1]["exchange"] == "smbs_std" else "price_bars"} if chosen else {"rate": float(default), "date": None, "symbol": FX_SYMBOL, "source": "default_missing_fx"}
    return {"dates": dates, "rates": rates, "latest": latest}


def fx_for_date(series: dict, date: str | None) -> dict:
    dates = series.get("dates") or []
    rates = series.get("rates") or []
    if dates and date:
        idx = bisect.bisect_right(dates, date) - 1
        if idx >= 0:
            return {"rate": float(rates[idx]), "date": dates[idx], "symbol": FX_SYMBOL, "source": "price_bars"}
    return dict(series.get("latest") or {"rate": DEFAULT_USD_KRW, "date": None, "symbol": FX_SYMBOL, "source": "default_missing_fx"})


def fx_rate_for_symbol(symbol: str, fx: dict | float | int | None) -> float:
    if not is_usd_symbol(symbol):
        return 1.0
    if isinstance(fx, dict):
        return float(fx.get("rate") or DEFAULT_USD_KRW)
    if fx is None:
        return DEFAULT_USD_KRW
    return float(fx)


def price_to_krw(symbol: str, price: float, fx: dict | float | int | None) -> float:
    return float(price or 0.0) * fx_rate_for_symbol(symbol, fx)
