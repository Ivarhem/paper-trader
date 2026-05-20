from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import (
    calculate_cash,
    calculate_realized_pnl,
    create_portfolio_snapshot,
    generate_sample_csv,
    get_connection,
    get_latest_price,
    get_latest_price_krw,
    import_price_csv,
    import_upbit_candles,
    init_db,
    list_backtest_runs,
    list_external_context_snapshots,
    list_forward_signals,
    list_universe_members,
    upsert_universe_members,
    list_validation_results,
    validation_summary,
    list_strategy_registry,
    validation_coverage,
    latest_research_org_report,
    latest_artifact,
    latest_financial_quality,
    list_recommendation_outcomes,
    list_recommendation_daily_outcomes,
    recommendation_daily_outcome_summary,
    list_disclosure_events,
    disclosure_feature_summary,
    recommendation_outcome_summary,
    save_external_context_snapshot,
    save_forward_signal,
    utc_now,
)
from app.symbols import SYMBOL_NAMES, resolve_symbol
from app.schemas import BacktestRequest, BacktestSweepRequest, ExternalContextSnapshotRequest, ForwardSignalRequest, PriceImportRequest, TradeRequest, UpbitImportRequest, UniverseMemberUpsertRequest, WatchlistCreate
from tools.agents.lib.fx import fx_rate_for_symbol, latest_usdkrw, price_to_krw


settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
SAMPLE_CSV = BASE_DIR / "sample_data" / "prices_sample.csv"


def valuation_snapshot(symbol: str, financial: dict | None = None) -> dict:
    out = {"per": None, "pbr": None, "roe_pct": None, "market_cap": None, "dividend_yield_pct": None, "beta": None, "source": "financial_snapshots"}
    if financial and financial.get("net_income") not in (None, 0) and financial.get("equity") not in (None, 0):
        try:
            out["roe_pct"] = round(float(financial["net_income"]) / float(financial["equity"]) * 100, 2)
        except Exception:
            pass
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = {}
        try:
            info = ticker.get_info() or {}
        except Exception:
            info = getattr(ticker, "info", {}) or {}
        per = info.get("trailingPE") or info.get("forwardPE")
        pbr = info.get("priceToBook")
        roe = info.get("returnOnEquity")
        market_cap = info.get("marketCap")
        dividend_yield = info.get("dividendYield")
        beta = info.get("beta")
        if per is not None:
            out["per"] = round(float(per), 2)
        if pbr is not None:
            out["pbr"] = round(float(pbr), 2)
        if roe is not None:
            out["roe_pct"] = round(float(roe) * 100, 2)
        if market_cap is not None:
            out["market_cap"] = float(market_cap)
        if dividend_yield is not None:
            dy = float(dividend_yield)
            out["dividend_yield_pct"] = round(dy if dy > 1 else dy * 100, 2)
        if beta is not None:
            out["beta"] = round(float(beta), 2)
        if out["per"] is not None or out["pbr"] is not None:
            out["source"] = "yfinance"
    except Exception as exc:
        out["error"] = str(exc)[:180]
    return out


@asynccontextmanager
async def lifespan(_: FastAPI):
    generate_sample_csv(SAMPLE_CSV)
    init_db()
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, root_path=settings.app_root_path, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def no_cache_file_response(path: Path) -> FileResponse:
    response = FileResponse(path)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/static/{file_path:path}", include_in_schema=False)
def static_file_fallback(file_path: str) -> FileResponse:
    target = (STATIC_DIR / file_path).resolve()
    static_root = STATIC_DIR.resolve()
    if not str(target).startswith(str(static_root)) or not target.is_file():
        raise HTTPException(status_code=404, detail="Static file not found")
    return no_cache_file_response(target)


AUTH_REALM = "paper-trader"
AUTH_EXEMPT_PATHS = {"/health", "/healthz", "/login", "/api/auth/login"}
AUTH_USERS_PATH = Path(os.getenv("PAPER_TRADER_AUTH_USERS", BASE_DIR / "auth_users.json"))


def auth_challenge() -> Response:
    # Do not send WWW-Authenticate for browser/API auth failures.
    # That header makes browsers show a native Basic Auth popup before
    # the SPA can redirect to /login. Login still accepts Basic headers
    # for backward compatibility, but unauthenticated web/API requests
    # should use the custom login page only.
    return JSONResponse({"detail": "Authentication required"}, status_code=401)


def password_hash(password: str, salt: str | None = None) -> dict:
    salt = salt or secrets.token_urlsafe(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
    return {"salt": salt, "hash": digest, "algo": "pbkdf2_sha256", "iterations": 200_000}


def verify_password(password: str, stored: dict) -> bool:
    salt = stored.get("salt") or ""
    expected = stored.get("hash") or ""
    iterations = int(stored.get("iterations") or 200_000)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
    return secrets.compare_digest(actual, expected)


def load_auth_users() -> dict:
    if not AUTH_USERS_PATH.exists():
        return {"users": {}}
    try:
        return json.loads(AUTH_USERS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"users": {}}


def save_auth_users(data: dict) -> None:
    AUTH_USERS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    try:
        AUTH_USERS_PATH.chmod(0o640)
    except OSError:
        pass


def auth_users_enabled() -> bool:
    return bool(load_auth_users().get("users"))


def parse_basic_auth(header: str | None) -> tuple[str, str] | None:
    if not header or not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1], validate=True).decode("utf-8")
    except Exception:
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password



def session_secret() -> str:
    return os.getenv("PAPER_TRADER_SESSION_SECRET") or os.getenv("PAPER_TRADER_PASSWORD") or "paper-trader-dev-secret"


def make_session_token(username: str, max_age_seconds: int = 60 * 60 * 12) -> str:
    exp = str(int(time.time()) + max_age_seconds)
    payload = f"{username}:{exp}"
    sig = hmac.new(session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        username, exp, sig = token.split(":", 2)
        if int(exp) < int(time.time()):
            return None
    except Exception:
        return None
    payload = f"{username}:{exp}"
    expected = hmac.new(session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(sig, expected):
        return None
    user = (load_auth_users().get("users") or {}).get(username)
    if not user or not user.get("active", True):
        return None
    return {"username": username, "roles": user.get("roles") or []}


def authenticate_basic(header: str | None) -> dict | None:
    parsed = parse_basic_auth(header)
    if not parsed:
        return None
    username, password = parsed
    users = load_auth_users().get("users") or {}
    user = users.get(username)
    if user and user.get("active", True) and verify_password(password, user.get("password") or {}):
        return {"username": username, "roles": user.get("roles") or []}
    # Backward-compatible env fallback only if no user file exists.
    if not users:
        expected_user = os.getenv("PAPER_TRADER_USERNAME") or ""
        expected_pass = os.getenv("PAPER_TRADER_PASSWORD") or ""
        if expected_user and expected_pass and secrets.compare_digest(username, expected_user) and secrets.compare_digest(password, expected_pass):
            return {"username": username, "roles": ["admin"]}
    return None


def current_auth_user(request: Request) -> dict | None:
    return verify_session_token(request.cookies.get("pt_session")) or authenticate_basic(request.headers.get("authorization"))


def require_admin(request: Request) -> dict:
    user = current_auth_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if "admin" not in (user.get("roles") or []):
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


@app.middleware("http")
async def require_basic_auth(request: Request, call_next):
    root = settings.app_root_path.rstrip("/")
    path = request.url.path
    root_relative = path[len(root):] if root and path.startswith(root + "/") else path
    if root_relative.startswith("/static/"):
        file_path = root_relative.removeprefix("/static/")
        target = (STATIC_DIR / file_path).resolve()
        static_root = STATIC_DIR.resolve()
        if str(target).startswith(str(static_root)) and target.is_file():
            return no_cache_file_response(target)
        return Response("Static file not found", status_code=404)
    if root_relative in AUTH_EXEMPT_PATHS:
        return await call_next(request)
    if not auth_users_enabled() and not (os.getenv("PAPER_TRADER_USERNAME") and os.getenv("PAPER_TRADER_PASSWORD")):
        return await call_next(request)
    if current_auth_user(request):
        return await call_next(request)
    if root_relative.startswith("/api/"):
        return auth_challenge()
    login_url = f"{root}/login" if root else "/login"
    next_path = root_relative or "/dashboard"
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    if next_path != "/login":
        login_url = f"{login_url}?next={quote(next_path, safe='')}"
    return RedirectResponse(url=login_url, status_code=303)


@app.post("/api/auth/login")
async def login_auth_user(request: Request) -> dict:
    body = await request.json()
    username = str(body.get("username") or body.get("login_id") or "").strip()
    password = str(body.get("password") or "")
    header = "Basic " + base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    user = authenticate_basic(header)
    if not user:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    resp = Response(json.dumps({"ok": True, "user": user}), media_type="application/json")
    cookie_path = settings.app_root_path.rstrip("/") or "/"
    resp.set_cookie("pt_session", make_session_token(user["username"]), httponly=True, samesite="lax", secure=False, max_age=60 * 60 * 12, path=cookie_path)
    return resp


@app.post("/api/auth/logout")
def logout_auth_user() -> Response:
    resp = Response(json.dumps({"ok": True}), media_type="application/json")
    cookie_path = settings.app_root_path.rstrip("/") or "/"
    resp.delete_cookie("pt_session", path=cookie_path)
    resp.delete_cookie("pt_session")
    return resp


@app.get("/api/auth/me")
def me_auth_user(request: Request) -> dict:
    user = current_auth_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"user": user}


@app.get("/api/admin/users")
def list_auth_users(request: Request) -> dict:
    require_admin(request)
    users = load_auth_users().get("users") or {}
    return {"items": [{"username": name, "roles": user.get("roles") or [], "active": user.get("active", True), "created_at": user.get("created_at"), "updated_at": user.get("updated_at")} for name, user in sorted(users.items())]}


@app.post("/api/admin/users")
async def create_auth_user(request: Request) -> dict:
    require_admin(request)
    body = await request.json()
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    roles = body.get("roles") or []
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")
    data = load_auth_users(); users = data.setdefault("users", {})
    if username in users:
        raise HTTPException(status_code=409, detail="user already exists")
    users[username] = {"password": password_hash(password), "roles": roles, "active": True, "created_at": utc_now(), "updated_at": utc_now()}
    save_auth_users(data)
    return {"ok": True, "username": username}


@app.delete("/api/admin/users/{username}")
def delete_auth_user(username: str, request: Request) -> dict:
    admin = require_admin(request)
    if username == admin.get("username"):
        raise HTTPException(status_code=400, detail="cannot delete current admin user")
    data = load_auth_users(); users = data.setdefault("users", {})
    if username not in users:
        raise HTTPException(status_code=404, detail="user not found")
    del users[username]
    save_auth_users(data)
    return {"ok": True, "username": username}


@app.post("/api/admin/users/{username}/reset-password")
async def reset_auth_user_password(username: str, request: Request) -> dict:
    require_admin(request)
    body = await request.json()
    password = str(body.get("password") or "")
    if not password:
        raise HTTPException(status_code=400, detail="password is required")
    data = load_auth_users(); users = data.setdefault("users", {})
    if username not in users:
        raise HTTPException(status_code=404, detail="user not found")
    users[username]["password"] = password_hash(password)
    users[username]["updated_at"] = utc_now()
    save_auth_users(data)
    return {"ok": True, "username": username}




def moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def relative_strength_index(values: list[float], window: int = 14) -> float | None:
    if len(values) <= window:
        return None
    gains = []
    losses = []
    for idx in range(1, len(values)):
        delta = values[idx] - values[idx - 1]
        gains.append(max(delta, 0))
        losses.append(abs(min(delta, 0)))
    avg_gain = sum(gains[-window:]) / window
    avg_loss = sum(losses[-window:]) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def annualized_volatility(values: list[float], window: int = 20) -> float | None:
    if len(values) <= window:
        return None
    returns = []
    for idx in range(-window, 0):
        prev_close = values[idx - 1]
        curr_close = values[idx]
        if prev_close == 0:
            continue
        returns.append((curr_close / prev_close) - 1)
    if len(returns) < 2:
        return None
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
    return (variance ** 0.5) * (252 ** 0.5) * 100


def distance_from_high(values: list[float], window: int = 252) -> float | None:
    if not values:
        return None
    sample = values[-window:] if len(values) >= window else values
    high = max(sample)
    if high == 0:
        return None
    return ((values[-1] / high) - 1) * 100


def get_price_series(symbol: str, conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT symbol, date, open, high, low, close, volume, market, exchange, timeframe
        FROM price_bars
        WHERE symbol = ?
        ORDER BY date ASC
        """,
        (symbol.upper(),),
    ).fetchall()


def get_signal_payload(symbol: str, conn: sqlite3.Connection, short_window: int = 5, long_window: int = 20) -> dict:
    rows = get_price_series(symbol, conn)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No price data for {symbol.upper()}")
    closes = [float(row["close"]) for row in rows]
    latest_close = closes[-1]
    ma_short = moving_average(closes, short_window)
    ma_long = moving_average(closes, long_window)
    prev_short = moving_average(closes[:-1], short_window) if len(closes) > short_window else None
    prev_long = moving_average(closes[:-1], long_window) if len(closes) > long_window else None
    crossover = "neutral"
    if ma_short is not None and ma_long is not None and prev_short is not None and prev_long is not None:
        if prev_short <= prev_long and ma_short > ma_long:
            crossover = "bullish_cross"
        elif prev_short >= prev_long and ma_short < ma_long:
            crossover = "bearish_cross"
        elif ma_short > ma_long:
            crossover = "bullish"
        elif ma_short < ma_long:
            crossover = "bearish"

    momentum_5d = None
    if len(closes) >= 6:
        momentum_5d = ((latest_close / closes[-6]) - 1) * 100
    rsi_14 = relative_strength_index(closes, 14)
    volatility_20d = annualized_volatility(closes, 20)
    distance_52w_high = distance_from_high(closes, 252)

    return {
        "symbol": symbol.upper(),
        "market": rows[-1]["market"] if "market" in rows[-1].keys() else "stock",
        "exchange": rows[-1]["exchange"] if "exchange" in rows[-1].keys() else None,
        "timeframe": rows[-1]["timeframe"] if "timeframe" in rows[-1].keys() else "1d",
        "latest_close": round(latest_close, 2),
        "latest_date": rows[-1]["date"],
        "ma_short": round(ma_short, 2) if ma_short is not None else None,
        "ma_long": round(ma_long, 2) if ma_long is not None else None,
        "short_window": short_window,
        "long_window": long_window,
        "crossover_signal": crossover,
        "momentum_5d_pct": round(momentum_5d, 2) if momentum_5d is not None else None,
        "rsi_14": round(rsi_14, 2) if rsi_14 is not None else None,
        "volatility_20d_pct": round(volatility_20d, 2) if volatility_20d is not None else None,
        "distance_52w_high_pct": round(distance_52w_high, 2) if distance_52w_high is not None else None,
    }


def compute_portfolio(conn: sqlite3.Connection) -> dict:
    positions_rows = conn.execute(
        "SELECT symbol, quantity, average_cost, updated_at FROM positions WHERE quantity > 0 ORDER BY symbol"
    ).fetchall()
    cash = calculate_cash(conn)
    realized_pnl = calculate_realized_pnl(conn)
    fx = latest_usdkrw(conn)
    positions = []
    positions_value = 0.0
    unrealized_pnl = 0.0
    for row in positions_rows:
        latest_price = get_latest_price(row["symbol"], conn)
        if latest_price is None:
            latest_price = row["average_cost"]
        latest_price_krw = price_to_krw(row["symbol"], latest_price, fx)
        market_value = latest_price_krw * row["quantity"]
        position_unrealized = (latest_price_krw - row["average_cost"]) * row["quantity"]
        positions_value += market_value
        unrealized_pnl += position_unrealized
        positions.append(
            {
                "symbol": row["symbol"],
                "quantity": round(float(row["quantity"]), 4),
                "average_cost": round(float(row["average_cost"]), 2),
                "average_cost_currency": "KRW",
                "last_price": round(float(latest_price), 2),
                "last_price_krw": round(float(latest_price_krw), 2),
                "fx_rate": round(fx_rate_for_symbol(row["symbol"], fx), 4),
                "fx_date": fx.get("date"),
                "fx_source": fx.get("source"),
                "market_value": round(market_value, 2),
                "unrealized_pnl": round(position_unrealized, 2),
                "updated_at": row["updated_at"],
            }
        )

    total_value = cash + positions_value
    return {
        "initial_cash": settings.initial_cash,
        "base_currency": "KRW",
        "fx": fx,
        "cash": round(cash, 2),
        "positions_value": round(positions_value, 2),
        "total_value": round(total_value, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "realized_pnl": round(realized_pnl, 2),
        "positions": positions,
    }


def max_drawdown_pct(equity_curve: list[dict]) -> float:
    peak = None
    worst = 0.0
    for point in equity_curve:
        value = float(point["equity"])
        peak = value if peak is None else max(peak, value)
        if peak and peak > 0:
            worst = min(worst, (value / peak - 1) * 100)
    return round(worst, 2)


def persist_backtest_result(conn: sqlite3.Connection, result: dict, params: dict) -> None:
    import json
    conn.execute(
        """
        INSERT INTO backtest_runs (
            run_at, symbol, strategy, params_json, bars, total_return_pct, buy_hold_return_pct,
            max_drawdown_pct, trade_count, win_rate_pct, profit_factor, final_equity
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(), result["symbol"], result["strategy"], json.dumps(params, sort_keys=True),
            result["bars"], result["total_return_pct"], result["buy_hold_return_pct"],
            result["max_drawdown_pct"], result["trade_count"], result["win_rate_pct"],
            result["profit_factor"], result["final_equity"],
        ),
    )


def run_backtest(payload: BacktestRequest, conn: sqlite3.Connection) -> dict:
    rows = get_price_series(payload.symbol, conn)
    if len(rows) < 3:
        raise HTTPException(status_code=400, detail="Not enough price data for backtest")
    if payload.strategy == "ma_cross" and payload.short_window >= payload.long_window:
        raise HTTPException(status_code=400, detail="short_window must be less than long_window")
    if payload.strategy == "rsi_reversion" and payload.rsi_buy >= payload.rsi_sell:
        raise HTTPException(status_code=400, detail="rsi_buy must be lower than rsi_sell")

    cash = float(payload.initial_cash)
    quantity = 0.0
    entry_price = 0.0
    trades = []
    equity_curve = []
    closes: list[float] = []
    fee_rate = payload.fee_bps / 10000
    slip_rate = payload.slippage_bps / 10000

    def buy(row, reason: str):
        nonlocal cash, quantity, entry_price
        if quantity > 0 or cash <= 0:
            return
        fill = float(row["close"]) * (1 + slip_rate)
        spendable = cash / (1 + fee_rate)
        qty = spendable / fill
        fee = spendable * fee_rate
        notional = qty * fill
        cash -= notional + fee
        quantity = qty
        entry_price = fill
        trades.append({"date": row["date"], "side": "BUY", "price": round(fill, 6), "quantity": qty, "fee": round(fee, 6), "reason": reason})

    def sell(row, reason: str):
        nonlocal cash, quantity, entry_price
        if quantity <= 0:
            return
        fill = float(row["close"]) * (1 - slip_rate)
        notional = quantity * fill
        fee = notional * fee_rate
        pnl = (fill - entry_price) * quantity - fee
        cash += notional - fee
        trades.append({"date": row["date"], "side": "SELL", "price": round(fill, 6), "quantity": quantity, "fee": round(fee, 6), "pnl": round(pnl, 6), "reason": reason})
        quantity = 0.0
        entry_price = 0.0

    prev_short = prev_long = None
    for row in rows:
        close = float(row["close"])
        closes.append(close)
        if payload.strategy == "ma_cross":
            ma_short = moving_average(closes, payload.short_window)
            ma_long = moving_average(closes, payload.long_window)
            if ma_short is not None and ma_long is not None and prev_short is not None and prev_long is not None:
                if prev_short <= prev_long and ma_short > ma_long:
                    buy(row, "ma_bull_cross")
                elif prev_short >= prev_long and ma_short < ma_long:
                    sell(row, "ma_bear_cross")
            if ma_short is not None and ma_long is not None:
                prev_short, prev_long = ma_short, ma_long
        else:
            rsi = relative_strength_index(closes, payload.rsi_window)
            if rsi is not None:
                if rsi <= payload.rsi_buy:
                    buy(row, f"rsi<={payload.rsi_buy}")
                elif rsi >= payload.rsi_sell:
                    sell(row, f"rsi>={payload.rsi_sell}")
        equity_curve.append({"date": row["date"], "equity": round(cash + quantity * close, 2)})

    if quantity > 0:
        sell(rows[-1], "final_close")
        equity_curve[-1] = {"date": rows[-1]["date"], "equity": round(cash, 2)}

    sells = [t for t in trades if t["side"] == "SELL"]
    wins = [t for t in sells if t.get("pnl", 0) > 0]
    gross_profit = sum(t.get("pnl", 0) for t in sells if t.get("pnl", 0) > 0)
    gross_loss = abs(sum(t.get("pnl", 0) for t in sells if t.get("pnl", 0) < 0))
    final_equity = cash
    total_return = ((final_equity / payload.initial_cash) - 1) * 100
    buy_hold_return = ((float(rows[-1]["close"]) / float(rows[0]["close"])) - 1) * 100
    return {
        "symbol": payload.symbol.upper(),
        "strategy": payload.strategy,
        "bars": len(rows),
        "initial_cash": payload.initial_cash,
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return, 2),
        "buy_hold_return_pct": round(buy_hold_return, 2),
        "max_drawdown_pct": max_drawdown_pct(equity_curve),
        "trade_count": len(sells),
        "win_rate_pct": round((len(wins) / len(sells) * 100), 2) if sells else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "trades": trades[-20:],
        "equity_curve": equity_curve[-200:],
    }


def execute_trade(side: str, payload: TradeRequest) -> dict:
    symbol = payload.symbol.upper().strip()
    with get_connection() as conn:
        latest_price = get_latest_price(symbol, conn)
        trade_price = payload.price or latest_price
        if trade_price is None:
            raise HTTPException(status_code=404, detail=f"No price data for {symbol}")

        quantity = float(payload.quantity)
        fx = latest_usdkrw(conn)
        trade_price_krw = price_to_krw(symbol, trade_price, fx)
        notional = trade_price_krw * quantity
        cash = calculate_cash(conn)
        current_position = conn.execute(
            "SELECT symbol, quantity, average_cost FROM positions WHERE symbol = ?",
            (symbol,),
        ).fetchone()

        if side == "BUY" and notional > cash:
            raise HTTPException(status_code=400, detail="Insufficient cash for trade")
        if side == "SELL":
            available_qty = float(current_position["quantity"]) if current_position else 0.0
            if quantity > available_qty:
                raise HTTPException(status_code=400, detail="Insufficient position to sell")

        executed_at = utc_now()
        conn.execute(
            """
            INSERT INTO paper_trades (symbol, side, quantity, price, notional, fees, executed_at, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (symbol, side, quantity, trade_price, notional, executed_at, executed_at),
        )

        if side == "BUY":
            old_qty = float(current_position["quantity"]) if current_position else 0.0
            old_avg = float(current_position["average_cost"]) if current_position else 0.0
            new_qty = old_qty + quantity
            # 평균단가 기준으로 포지션을 단순 추적한다.
            new_avg = ((old_qty * old_avg) + notional) / new_qty
            conn.execute(
                """
                INSERT INTO positions (symbol, quantity, average_cost, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    average_cost = excluded.average_cost,
                    updated_at = excluded.updated_at
                """,
                (symbol, new_qty, new_avg, executed_at),
            )
        else:
            remaining_qty = float(current_position["quantity"]) - quantity
            if remaining_qty <= 0:
                conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
            else:
                conn.execute(
                    "UPDATE positions SET quantity = ?, updated_at = ? WHERE symbol = ?",
                    (remaining_qty, executed_at, symbol),
                )

        create_portfolio_snapshot(conn)
        trade = dict(conn.execute("SELECT * FROM paper_trades ORDER BY id DESC LIMIT 1").fetchone())
        trade["price_krw"] = round(trade_price_krw, 2)
        trade["fx_rate"] = round(fx_rate_for_symbol(symbol, fx), 4)
        trade["fx_date"] = fx.get("date")
        trade["fx_source"] = fx.get("source")
        trade["notional_currency"] = "KRW"
        return trade


@app.get("/healthz")
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/meta")
def meta() -> dict:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "root_path": settings.app_root_path,
    }


@app.get("/api/watchlist")
def list_watchlist() -> dict:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM watchlist_items ORDER BY symbol").fetchall()
        return {"items": [dict(row) for row in rows]}


@app.post("/api/watchlist", status_code=201)
def create_watchlist_item(payload: WatchlistCreate) -> dict:
    with get_connection() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO watchlist_items (symbol, note, created_at) VALUES (?, ?, ?)",
                (payload.symbol.upper().strip(), payload.note, utc_now()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Symbol already exists in watchlist") from None
        row = conn.execute("SELECT * FROM watchlist_items WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


@app.delete("/api/watchlist/{item_id}")
def delete_watchlist_item(item_id: int) -> dict:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM watchlist_items WHERE id = ?", (item_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Watchlist item not found")
        return {"deleted": True}


@app.get("/api/prices/{symbol}")
def get_prices(symbol: str) -> dict:
    with get_connection() as conn:
        rows = get_price_series(symbol, conn)
        if not rows:
            raise HTTPException(status_code=404, detail=f"No price data for {symbol.upper()}")
        return {"symbol": symbol.upper(), "prices": [dict(row) for row in rows]}


@app.post("/api/prices/import")
def import_prices(payload: PriceImportRequest) -> dict:
    try:
        result = import_price_csv(payload.csv_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result, "csv_path": payload.csv_path}


@app.post("/api/crypto/upbit/import")
def import_upbit_prices(payload: UpbitImportRequest) -> dict:
    try:
        result = import_upbit_candles(payload.symbol, payload.timeframe, payload.count)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Upbit import failed: {exc}") from exc
    return {"ok": True, **result}


@app.get("/api/signals")
def get_signals() -> dict:
    with get_connection() as conn:
        symbols = [
            row["symbol"]
            for row in conn.execute("SELECT symbol FROM watchlist_items ORDER BY symbol").fetchall()
        ]
        if not symbols:
            symbols = [
                row["symbol"]
                for row in conn.execute("SELECT DISTINCT symbol FROM price_bars ORDER BY symbol").fetchall()
            ]
        return {"items": [get_signal_payload(symbol, conn) for symbol in symbols]}


@app.get("/api/signals/{symbol}")
def get_signal(symbol: str) -> dict:
    with get_connection() as conn:
        return get_signal_payload(symbol, conn)


@app.post("/api/external-context/snapshots", status_code=201)
def create_external_context_snapshot(payload: ExternalContextSnapshotRequest) -> dict:
    context = payload.model_dump()
    context["captured_at"] = context.get("captured_at") or utc_now()
    snapshot_id = save_external_context_snapshot(context)
    return {"ok": True, "id": snapshot_id, "context": context}


@app.get("/api/external-context/snapshots")
def get_external_context_snapshots(limit: int = Query(default=20, ge=1, le=200)) -> dict:
    return {"items": list_external_context_snapshots(limit)}


@app.get("/api/external-context/latest")
def get_latest_external_context() -> dict:
    items = list_external_context_snapshots(1)
    return items[0] if items else {"context": None}


@app.post("/api/forward-signals", status_code=201)
def create_forward_signal(payload: ForwardSignalRequest) -> dict:
    signal = payload.model_dump()
    signal["signal_at"] = signal.get("signal_at") or utc_now()
    signal.update(signal.pop("payload", {}) or {})
    signal_id = save_forward_signal(signal)
    return {"ok": True, "id": signal_id, "signal": signal}


@app.get("/api/forward-signals")
def get_forward_signals(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    return {"items": list_forward_signals(limit)}


@app.get("/api/symbols/names")
def get_symbol_names() -> dict:
    return {"names": SYMBOL_NAMES}


@app.get("/api/strategies")
def get_strategies() -> dict:
    return {"items": list_strategy_registry()}


@app.get("/api/validation/coverage")
def get_validation_coverage() -> dict:
    return validation_coverage()


def _read_json_file(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"status": "missing", "path": path}
    try:
        data = json.loads(p.read_text())
    except Exception as exc:
        return {"status": "error", "path": path, "error": str(exc)}
    try:
        data["mtime"] = p.stat().st_mtime
        data["age_sec"] = round(time.time() - p.stat().st_mtime, 1)
    except Exception:
        pass
    return data if isinstance(data, dict) else {"status": "ok", "items": data}


@app.get("/api/validation/worker-status")
def get_validation_worker_status() -> dict:
    worker = _read_json_file("/tmp/validation_worker_status.json")
    capacity = _read_json_file("/tmp/validation_capacity_worker_cron.out")
    simulation = _read_json_file("/tmp/current_recommendation_simulation_validation_latest.json")
    current = _read_json_file("/tmp/current_recommendation_validation_latest.json")
    cadence = worker.get("cadence_recommendation") or capacity.get("cadence_recommendation")
    next_run_at = None
    if worker.get("mtime") and cadence:
        try:
            mult = 60 if str(cadence).endswith("m") else 1
            amount = int(str(cadence).rstrip("ms"))
            next_run_at = worker["mtime"] + amount * mult
        except Exception:
            next_run_at = None
    return {
        "status": worker.get("status") or "unknown",
        "worker": worker,
        "capacity": capacity,
        "simulation": simulation,
        "current_recommendation": current,
        "next_run_at": next_run_at,
        "next_run_in_sec": round(next_run_at - time.time(), 1) if next_run_at else None,
    }


@app.get("/api/validation/summary")
def get_validation_summary() -> dict:
    return validation_summary()


@app.get("/api/validation/results")
def get_validation_results(limit: int = Query(default=500, ge=1, le=2000), logic: str | None = None) -> dict:
    return {"items": list_validation_results(limit=limit, logic=logic)}


@app.get("/api/recommendations/audit/status")
def get_recommendation_audit_status() -> dict:
    return read_tmp_json("/tmp/audit_status_latest.json", {"status": "not_run"})


@app.get("/api/recommendations/audit/latest")
def get_latest_recommendation_audit(
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    cutoff: str | None = None,
    result: str | None = None,
    action: str | None = Query(default=None),
    dedupe: bool = Query(default=True),
) -> dict:
    path = Path("/tmp/recommendation_audit_latest.json")
    if not path.exists():
        return {"audit": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid recommendation audit file: {exc}") from exc

    all_items = data.get("items") or []
    full_output = data.get("full_output")
    expected_total = data.get("items_total_filtered") or data.get("items_total_audited") or len(all_items)
    if full_output and Path(str(full_output)).exists() and (not all_items or expected_total > len(all_items)):
        try:
            full_data = json.loads(Path(str(full_output)).read_text(encoding="utf-8"))
            all_items = full_data.get("items") or []
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid recommendation audit full file: {exc}") from exc
    items = [x for x in all_items if x.get("status") == "audited"]
    if action:
        items = [x for x in items if x.get("action") == action]
    if cutoff:
        items = [x for x in items if x.get("cutoff") == cutoff]
    if result:
        items = [x for x in items if x.get("result") == result]
    items.sort(
        key=lambda x: (
            str(x.get("cutoff") or ""),
            {"success": 3, "timeout": 2, "fail": 1}.get(x.get("result"), 0),
            float(x.get("excess_return_pct") or 0),
        ),
        reverse=True,
    )
    if dedupe:
        seen = set()
        deduped = []
        for item in items:
            key = (item.get("symbol"), item.get("cutoff"), item.get("result"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        items = deduped
    total_filtered = len(items)
    data["items"] = items[offset : offset + limit]
    data["items_total_raw"] = len(all_items)
    data["items_total_filtered"] = total_filtered
    data["items_limit"] = limit
    data["items_offset"] = offset
    data["items_deduped"] = dedupe
    data["latest_cutoff"] = items[0].get("cutoff") if items else None
    return data


@app.get("/api/recommendations/latest")
def get_latest_recommendations(
    detail: str = Query(default="compact", pattern="^(compact|full)$"),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    if not isinstance(detail, str):
        detail = "compact"
    try:
        limit = int(limit)
    except Exception:
        limit = 20
    if detail == "compact":
        compact = Path("/tmp/recommendations_status_latest.json")
        if compact.exists():
            data = read_tmp_json(str(compact), {"recommendations": None})
            if isinstance(data.get("top_items"), list):
                data = dict(data)
                data["items"] = data.get("top_items", [])[:limit]
                data["items_limit"] = limit
            return data
    data = read_tmp_json("/tmp/recommendations_latest.json", {"recommendations": None})
    if detail == "full":
        return data
    return compact_recommendation_response(data, limit=limit)


@app.get("/api/recommendations/shadow/latest")
def get_latest_shadow_recommendations() -> dict:
    path = Path("/tmp/shadow_recommendations_latest.json")
    if not path.exists():
        return {"items": [], "policy": {"active_recommendation_eligible": False}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid shadow recommendations file: {exc}") from exc


@app.get("/api/recommendations/history")
def get_recommendation_history(
    runs: int = Query(default=20, ge=1, le=200),
    symbol: str | None = None,
    market: str | None = None,
    action: str | None = None,
) -> dict:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recommendation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT,
                action TEXT NOT NULL,
                score REAL,
                strategy_id TEXT,
                target_1 REAL,
                stop_reference REAL,
                confidence_grade TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(run_at, symbol)
            )
        """)
        run_rows = conn.execute("SELECT DISTINCT run_at FROM recommendation_history ORDER BY run_at DESC LIMIT ?", (runs,)).fetchall()
        run_ats = [r["run_at"] for r in run_rows]
        if not run_ats:
            return {"runs": [], "items": [], "summary": {"run_count": 0, "item_count": 0}}
        placeholders = ",".join("?" for _ in run_ats)
        where = [f"run_at IN ({placeholders})"]
        params: list = list(run_ats)
        if symbol:
            where.append("symbol = ?")
            params.append(symbol.strip().upper())
        if market:
            where.append("market = ?")
            params.append(market.strip().upper())
        if action:
            where.append("action = ?")
            params.append(action.strip())
        rows = conn.execute(
            f"""
            SELECT run_at, symbol, market, action, score, strategy_id, target_1, stop_reference, confidence_grade, payload_json
            FROM recommendation_history
            WHERE {' AND '.join(where)}
            ORDER BY run_at DESC, market ASC, score DESC
            """,
            params,
        ).fetchall()
    latest_prices = {}
    symbols = sorted({r["symbol"] for r in rows})
    if symbols:
        placeholders2 = ",".join("?" for _ in symbols)
        with get_connection() as conn:
            price_rows = conn.execute(
                f'''SELECT symbol, date, close FROM price_bars
                    WHERE symbol IN ({placeholders2}) AND timeframe='1d'
                    ORDER BY symbol, date DESC''',
                symbols,
            ).fetchall()
        for pr in price_rows:
            latest_prices.setdefault(pr["symbol"], {"latest_price_date": pr["date"], "latest_close": pr["close"]})
    items=[]
    for r in rows:
        payload={}
        try:
            payload=json.loads(r["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload={}
        price = latest_prices.get(r["symbol"], {})
        items.append({
            "run_at": r["run_at"],
            "symbol": r["symbol"],
            "name": payload.get("name"),
            "market": r["market"],
            "action": r["action"],
            "score": r["score"],
            "strategy_id": r["strategy_id"],
            "latest_close": price.get("latest_close"),
            "latest_price_date": price.get("latest_price_date"),
            "target_1": r["target_1"],
            "stop_reference": r["stop_reference"],
            "confidence_grade": r["confidence_grade"],
            "action_label": payload.get("action_label"),
            "recommendation_reason": payload.get("recommendation_reason"),
            "risk_notes": payload.get("risk_notes") or [],
        })
    by_symbol={}
    for item in items:
        sym=item["symbol"]
        stats=by_symbol.setdefault(sym,{"symbol":sym,"name":item.get("name"),"market":item.get("market"),"count":0,"latest_score":None,"latest_action":None,"latest_run_at":None,"first_run_at":item.get("run_at"),"last_strategy_id":None})
        stats["count"] += 1
        stats["first_run_at"] = item.get("run_at")
        if stats["latest_run_at"] is None or item.get("run_at") > stats["latest_run_at"]:
            stats["latest_run_at"] = item.get("run_at")
            stats["latest_score"] = item.get("score")
            stats["latest_action"] = item.get("action")
            stats["last_strategy_id"] = item.get("strategy_id")
    latest_path = Path("/tmp/recommendations_latest.json")
    latest_changes = None
    if latest_path.exists():
        try:
            latest_changes = json.loads(latest_path.read_text(encoding="utf-8")).get("recommendation_changes")
        except json.JSONDecodeError:
            latest_changes = None
    return {
        "runs": run_ats,
        "items": items,
        "by_symbol": sorted(by_symbol.values(), key=lambda x: (x.get("latest_run_at") or "", x.get("latest_score") or 0), reverse=True),
        "latest_changes": latest_changes,
        "summary": {"run_count": len(run_ats), "item_count": len(items), "symbol_count": len(by_symbol)},
    }


@app.get("/api/recommendations/outcomes")
def get_recommendation_outcomes(
    limit: int = Query(default=500, ge=1, le=5000),
    symbol: str | None = None,
    horizon_days: int | None = Query(default=None, ge=1, le=252),
) -> dict:
    return {
        "items": list_recommendation_outcomes(limit=limit, symbol=symbol, horizon_days=horizon_days),
        "summary": recommendation_outcome_summary(),
    }


@app.get("/api/recommendations/daily-outcomes")
def get_recommendation_daily_outcomes(
    limit: int = Query(default=500, ge=1, le=5000),
    symbol: str | None = None,
    market: str | None = None,
) -> dict:
    return {
        "items": list_recommendation_daily_outcomes(limit=limit, symbol=symbol, market=market),
        "summary": recommendation_daily_outcome_summary(),
    }


def artifact_key_from_path(path: str) -> str:
    return Path(path).name.removesuffix(".json")


def db_artifact_is_fresh(target: Path, payload: dict) -> bool:
    # latest_artifacts is now the authoritative latest-state store. /tmp and
    # static JSON files are compatibility mirrors, so file mtimes should not
    # shadow a DB payload that was written by the pipeline artifact index.
    return True


def read_tmp_json(path: str, fallback: dict, *, prefer_db: bool = True) -> dict:
    target = Path(path)
    db_payload = latest_artifact(artifact_key_from_path(path)) if prefer_db else None
    if db_payload is not None and db_artifact_is_fresh(target, db_payload):
        return db_payload
    paths = [target]
    if target.is_absolute() and target.parent == Path("/tmp"):
        paths.append(Path("static") / target.name)
    for candidate in paths:
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid {candidate.name}: {exc}") from exc
    return db_payload if db_payload is not None else fallback


def read_compact_or_full(compact_path: str, full_path: str, fallback: dict, label: str, detail: str = "compact") -> dict:
    if detail == "full":
        return read_tmp_json(full_path, fallback, prefer_db=False)
    data = read_tmp_json(compact_path, {})
    if data:
        return data
    data = read_tmp_json(full_path, fallback)
    if isinstance(data, dict):
        data = dict(data)
        data["_compact_missing"] = compact_path
        data["_detail"] = "full_fallback"
    return data


def compact_recommendation_response(data: dict, limit: int = 20) -> dict:
    try:
        limit = int(limit)
    except Exception:
        limit = 20
    items = data.get("items") or []
    def card(item: dict) -> dict:
        evidence = item.get("evidence") or item.get("reasons") or item.get("reason_codes") or []
        if isinstance(evidence, list):
            evidence = evidence[:3]
        return {
            "symbol": item.get("symbol"),
            "name": item.get("name") or item.get("symbol_name"),
            "market": item.get("market"),
            "action": item.get("action"),
            "bucket": item.get("recommendation_bucket") or item.get("bucket"),
            "score": item.get("score"),
            "entry_price": item.get("entry_price") or item.get("target_buy_price") or item.get("buy_price"),
            "target_price": item.get("target_1") or item.get("target_price") or item.get("target_return_price"),
            "stop_reference": item.get("stop_reference") or item.get("stop_price"),
            "strategy_id": item.get("strategy_id") or item.get("logic"),
            "trade_eligible": item.get("trade_eligible"),
            "evidence": evidence,
        }
    return {
        "run_at": data.get("run_at"),
        "status": data.get("status") or "ok",
        "item_count": len(items),
        "market_counts": data.get("market_counts"),
        "active_strategy_count": data.get("active_strategy_count"),
        "effective_strategy_count": data.get("effective_strategy_count"),
        "repair_active_strategy_count": data.get("repair_active_strategy_count"),
        "bucket_counts": {k: sum(1 for r in items if r.get("recommendation_bucket") == k) for k in ("approved", "watch", "research_watch", "rejected")},
        "recommendation_changes": data.get("recommendation_changes"),
        "aggregate_quality_notes": data.get("aggregate_quality_notes"),
        "items": [card(item) for item in items[:limit]],
        "items_limit": limit,
        "detail": "compact_generated",
        "artifact_refs": {"full": "/tmp/recommendations_latest.json"},
    }


@app.get("/api/research/recommendation-funnel/latest")
def get_latest_recommendation_funnel() -> dict:
    return read_tmp_json("/tmp/recommendation_funnel_latest.json", {"status": "not_run", "stages": [], "summary": {}})


@app.get("/api/research/candidate-funnel/latest")
def get_latest_candidate_funnel_compat() -> dict:
    return get_latest_recommendation_funnel()


@app.get("/api/recommendations/calibration/latest")
def get_latest_recommendation_calibration() -> dict:
    return read_tmp_json("/tmp/recommendation_calibration_latest.json", {"status": "not_run", "sample_count": 0, "findings": [], "summary": {}})


@app.get("/api/research/universe")
def get_universe_members(limit: int = Query(default=1000, ge=1, le=1000), status: str | None = None) -> dict:
    return {"items": list_universe_members(limit=limit, status=status)}


def ensure_symbol_review_history(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbol_review_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            query TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            market TEXT,
            status TEXT,
            analysis_source TEXT,
            recommendation_hint TEXT,
            bars INTEGER,
            last_price REAL,
            validation_samples INTEGER,
            avg_excess_return_pct REAL,
            active_eval_action TEXT,
            active_eval_score REAL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol_review_history_run_at ON symbol_review_history(run_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol_review_history_symbol ON symbol_review_history(symbol, run_at)")


def save_symbol_review_history(packet: dict) -> None:
    active_eval = packet.get("active_evaluation") or {}
    contract = packet.get("contract") or {}
    trend = packet.get("trend") or {}
    validation = packet.get("validation") or {}
    with get_connection() as conn:
        ensure_symbol_review_history(conn)
        conn.execute(
            """
            INSERT INTO symbol_review_history
            (run_at, query, symbol, name, market, status, analysis_source, recommendation_hint, bars, last_price,
             validation_samples, avg_excess_return_pct, active_eval_action, active_eval_score, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                packet.get("run_at"), packet.get("query"), packet.get("symbol"), packet.get("name"), packet.get("market"),
                contract.get("status"), packet.get("analysis_source"), packet.get("recommendation_hint"), trend.get("bars"), trend.get("last_price"),
                validation.get("samples"), validation.get("avg_excess_return_pct"), active_eval.get("action"), active_eval.get("score"),
                json.dumps(packet, ensure_ascii=False, sort_keys=True), utc_now(),
            ),
        )


@app.get("/api/research/symbol-review/history")
def get_symbol_review_history(limit: int = Query(default=30, ge=1, le=200), symbol: str | None = None) -> dict:
    with get_connection() as conn:
        ensure_symbol_review_history(conn)
        params: list = []
        where = ""
        if symbol:
            where = "WHERE symbol = ?"
            params.append(symbol.strip().upper())
        rows = conn.execute(
            f"""
            SELECT id, run_at, query, symbol, name, market, status, analysis_source, recommendation_hint, bars, last_price,
                   validation_samples, avg_excess_return_pct, active_eval_action, active_eval_score, payload_json
            FROM symbol_review_history
            {where}
            ORDER BY run_at DESC, id DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    items=[]
    for r in rows:
        payload={}
        try:
            payload=json.loads(r["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload={}
        items.append({
            "id": r["id"], "run_at": r["run_at"], "query": r["query"], "symbol": r["symbol"], "name": r["name"],
            "market": r["market"], "status": r["status"], "analysis_source": r["analysis_source"], "recommendation_hint": r["recommendation_hint"],
            "bars": r["bars"], "last_price": r["last_price"], "validation_samples": r["validation_samples"],
            "avg_excess_return_pct": r["avg_excess_return_pct"], "active_eval_action": r["active_eval_action"], "active_eval_score": r["active_eval_score"],
            "summary": payload.get("summary"), "action_counts": (payload.get("validation") or {}).get("action_counts") or {},
            # Include the stored review packet so the UI can reopen a prior review
            # without re-running bounded validation. Keep this endpoint capped by
            # limit<=200; normal monitor usage fetches <=20 rows.
            "payload": payload,
        })
    return {"items": items, "summary": {"item_count": len(items)}}


@app.get("/api/research/symbol-overview")
def get_symbol_overview(symbol: str, history_limit: int = Query(default=10, ge=1, le=50)) -> dict:
    resolved = resolve_symbol(symbol.strip())
    sym = resolved["symbol"].upper()
    try:
        history_limit = int(history_limit)
    except Exception:
        history_limit = 10
    members = list_universe_members(limit=1000)
    member = next((x for x in members if x.get("symbol") == sym), None)
    financial = latest_financial_quality(sym)
    disclosures = list_disclosure_events(limit=8, symbol=sym)
    disclosure_features = disclosure_feature_summary([sym], lookback_days=90).get(sym) or {}
    history = get_symbol_review_history(limit=history_limit, symbol=sym)
    signal = None
    signal_error = None
    try:
        with get_connection() as conn:
            signal = get_signal_payload(sym, conn)
    except HTTPException as exc:
        signal_error = exc.detail
    return {
        "symbol": sym,
        "name": resolved.get("name") or SYMBOL_NAMES.get(sym) or (member or {}).get("name") or sym,
        "resolved": resolved,
        "market": "KR" if sym.endswith((".KS", ".KQ")) else "US",
        "universe_member": member,
        "financial_quality": financial,
        "valuation": valuation_snapshot(sym, financial),
        "disclosure_features": disclosure_features,
        "recent_disclosures": disclosures,
        "price_signal": signal,
        "price_signal_error": signal_error,
        "history": history.get("items") or [],
    }


@app.get("/api/research/symbol-review")
def get_symbol_review(symbol: str, runs: int = Query(default=3, ge=1, le=10)) -> dict:
    sym = symbol.strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    resolved = resolve_symbol(sym)
    safe_symbol = resolved["symbol"].upper().replace("/", "_").replace(".", "_")
    output = Path(f"/tmp/symbol_review_{safe_symbol}_{os.getpid()}.json")
    cmd = [sys.executable, str(BASE_DIR / "tools" / "agents" / "symbol_review_agent.py"), "--symbol", sym, "--runs", str(runs), "--output", str(output)]
    proc = subprocess.run(cmd, cwd=BASE_DIR, text=True, capture_output=True, timeout=180)
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail={"error": "symbol review failed", "stderr": proc.stderr[-2000:], "stdout": proc.stdout[-2000:]})
    try:
        packet = json.loads(output.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid symbol review file: {exc}") from exc
    save_symbol_review_history(packet)
    return packet


@app.post("/api/research/universe-member", status_code=201)
def upsert_research_universe_member(payload: UniverseMemberUpsertRequest) -> dict:
    resolved = resolve_symbol(payload.symbol.strip())
    sym = resolved["symbol"].upper()
    item_payload = dict(payload.payload or {})
    item_payload.update({
        "name": resolved.get("name"),
        "resolved": resolved,
        "source": item_payload.get("source") or "manual_symbol_review",
    })
    item = {
        "symbol": sym,
        "status": payload.status,
        "reason": payload.reason or item_payload.get("summary") or "manual symbol review handoff",
        "score": payload.score,
        "updated_at": utc_now(),
        "payload": item_payload,
    }
    result = upsert_universe_members([item])
    return {"ok": True, "result": result, "item": item}


@app.get("/api/research/curator/latest")
def get_latest_universe_curator() -> dict:
    path = Path("/tmp/universe_curator_latest.json")
    if not path.exists():
        return {"curator": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid universe curator file: {exc}") from exc


@app.get("/api/research/scout/latest")
def get_latest_universe_scout() -> dict:
    path = Path("/tmp/universe_scout_latest.json")
    if not path.exists():
        return {"scout": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid universe scout file: {exc}") from exc




@app.get("/api/research/fund/performance/latest")
def get_latest_fund_performance() -> dict:
    path = Path("/tmp/fund_performance_evaluator_latest.json")
    if not path.exists():
        return {"status": "not_run", "evaluations": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid fund performance file: {exc}") from exc


@app.get("/api/research/fund/registry/latest")
def get_latest_fund_registry() -> dict:
    path = Path("/tmp/fund_registry_latest.json")
    if not path.exists():
        return {"status": "not_run", "funds": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid fund registry file: {exc}") from exc




def read_latest_json_any(paths: list[Path], empty: dict, label: str) -> dict:
    for path in paths:
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid {label} file: {exc}") from exc
    return empty


def read_latest_tmp_json(filename: str, empty: dict, label: str, *, static_fallback: bool = True) -> dict:
    paths = [Path("/tmp") / filename]
    if static_fallback:
        paths.append(Path("static") / filename)
    return read_latest_json_any(paths, empty, label)


@app.get("/api/research/fund/consensus/latest")
def get_latest_fund_consensus() -> dict:
    return read_latest_tmp_json("fund_consensus_latest.json", {"items": [], "symbol_consensus": []}, "fund consensus")


@app.get("/api/research/fund/recommendation-consensus/latest")
def get_latest_fund_recommendation_consensus() -> dict:
    return read_latest_tmp_json("fund_recommendation_consensus_latest.json", {"items": []}, "fund recommendation consensus")


@app.get("/api/research/fund/trades/latest")
def get_latest_fund_trades() -> dict:
    data = read_latest_tmp_json("fund_trade_history_latest.json", {"items": []}, "fund trade history")
    if data.get("items"):
        return data
    replay = read_latest_tmp_json("paper_fund_price_replay_latest.json", {}, "paper fund price replay")
    trades = replay.get("trades") or []
    if trades:
        return {
            "run_at": replay.get("run_at"),
            "source": "paper_fund_price_replay_latest",
            "cost_model": replay.get("cost_model"),
            "items": trades[-500:],
        }
    return data


@app.get("/api/recommendations/market-context/latest")
def get_latest_recommendation_market_context() -> dict:
    return read_latest_tmp_json("recommendation_market_context_latest.json", {"items": []}, "recommendation market context")


@app.get("/api/research/fund/org/latest")
def get_latest_fund_org_summary() -> dict:
    # Single fund sub-organization contract for UI/API consumers. Prefer the
    # pipeline-generated /tmp artifact; fall back to static for no-restart deployments.
    for path in (Path("/tmp/fund_suborg_summary_latest.json"), Path("/tmp/fund_org_summary_latest.json"), Path("static/fund_suborg_summary_latest.json"), Path("static/fund_org_summary_latest.json")):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=500, detail=f"Invalid fund org summary file: {exc}") from exc
    pipe = Path("/tmp/research_pipeline_latest.json")
    if pipe.exists():
        try:
            payload = json.loads(pipe.read_text(encoding="utf-8"))
            return {"run_at": payload.get("run_at"), "source": "research_pipeline_latest", "fund_org_summary": payload.get("fund_org_summary") or {}}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid research pipeline file: {exc}") from exc
    return {"status": "not_run"}


@app.get("/api/research/org/orchestrator/latest")
def get_latest_research_org_orchestrator() -> dict:
    path = Path("/tmp/research_org_orchestrator_latest.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid research org orchestrator file: {exc}") from exc
    return latest_research_org_report("orchestrator_run") or {"status": "not_run"}


@app.get("/api/research/pipeline/latest")
def get_latest_research_pipeline(
    detail: str = Query(default="compact", pattern="^(compact|full)$"),
) -> dict:
    if not isinstance(detail, str):
        detail = "compact"
    if detail == "compact":
        payload = read_compact_or_full(
            "/tmp/context_goal_latest.json",
            "/tmp/research_pipeline_latest.json",
            latest_research_org_report("research_pipeline") or {"status": "not_run"},
            "research pipeline",
            detail=detail,
        )
        supervisors = read_tmp_json("/tmp/executive_director_latest.json", {})
        domain_summary = read_tmp_json("/tmp/research_org_suborg_summary_latest.json", {})
        payload["domain_supervisors"] = (domain_summary.get("domain_supervisors") or {}) if isinstance(domain_summary, dict) else {}
        if supervisors:
            payload["domain_supervisors"]["executive_director"] = supervisors
        return payload
    return read_tmp_json("/tmp/research_pipeline_latest.json", latest_research_org_report("research_pipeline") or {"status": "not_run"})


@app.get("/api/research/local-llm-delegation/latest")
def get_latest_local_llm_delegation() -> dict:
    return read_compact_or_full(
        "/tmp/local_llm_delegation_latest.json",
        "/tmp/context_goal_latest.json",
        {"status": "not_run"},
        "local LLM delegation",
    )


@app.get("/api/research/domain-supervisors/latest")
def get_latest_domain_supervisors() -> dict:
    summary = read_tmp_json("/tmp/research_org_suborg_summary_latest.json", {})
    supervisors = (summary.get("domain_supervisors") or {}) if isinstance(summary, dict) else {}
    executive = read_tmp_json("/tmp/executive_director_latest.json", {})
    if executive:
        supervisors["executive_director"] = executive
    return {"status": "ok" if supervisors else "not_run", "domain_supervisors": supervisors}


@app.get("/api/research/org/evaluation/latest")
def get_latest_research_org_evaluation() -> dict:
    path = Path("/tmp/research_org_evaluation_latest.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid research org evaluation file: {exc}") from exc
    return latest_research_org_report("org_evaluation") or {"status": "not_run"}


@app.get("/api/research/org/improvement-guardian/latest")
def get_latest_org_improvement_guardian() -> dict:
    path = Path("/tmp/org_improvement_guardian_latest.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid org improvement guardian file: {exc}") from exc
    return {"status": "not_run"}


@app.get("/api/research/org/architecture/latest")
def get_latest_org_architecture_review() -> dict:
    path = Path("/tmp/org_architecture_review_latest.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid org architecture review file: {exc}") from exc
    return {"status": "not_run"}


@app.get("/api/research/market-mover-seed/latest")
def get_latest_market_mover_seed() -> dict:
    path = Path("/tmp/market_mover_seed_latest.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid market mover seed file: {exc}") from exc
    return {"status": "not_run"}


@app.get("/api/research/market-shock/latest")
def get_latest_market_shock_mover_scout() -> dict:
    path = Path("/tmp/market_shock_mover_scout_latest.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid market shock mover scout file: {exc}") from exc
    return {"status": "not_run"}


@app.get("/api/research/theme-spillover/latest")
def get_latest_theme_spillover_backtest() -> dict:
    path = Path("/tmp/theme_spillover_backtest_latest.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid theme spillover backtest file: {exc}") from exc
    return {"status": "not_run"}


@app.get("/api/research/integrity/latest")
def get_latest_paper_trader_integrity() -> dict:
    path = Path("/tmp/paper_trader_integrity_latest.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid paper trader integrity file: {exc}") from exc
    return {"status": "not_run"}


@app.get("/api/research/data-quality/latest")
def get_latest_data_quality() -> dict:
    path = Path("/tmp/data_quality_latest.json")
    if not path.exists():
        return {"status": "not_run"}
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/research/strategy-pruner/latest")
def get_latest_strategy_pruner() -> dict:
    path = Path("/tmp/strategy_novelty_pruner_latest.json")
    if not path.exists():
        return {"status": "not_run"}
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/research/regime/latest")
def get_latest_regime_segmentation() -> dict:
    path = Path("/tmp/regime_segmentation_latest.json")
    if not path.exists():
        return {"status": "not_run"}
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/research/org/latest")
def get_latest_research_org() -> dict:
    # Compatibility endpoint. The old implementation read /tmp/stock_research_org_latest.json,
    # which belongs to the retired research_org_run flow and can be stale/missing. Keep the
    # route alive, but derive it from the current 15-minute pipeline snapshot.
    path = Path("/tmp/research_pipeline_latest.json")
    if path.exists():
        try:
            pipe = json.loads(path.read_text(encoding="utf-8"))
            domain_supervisors = pipe.get("domain_supervisors") or {}
            executive = read_tmp_json("/tmp/executive_director_latest.json", {})
            if executive:
                domain_supervisors["executive_director"] = executive
            return {
                "status": pipe.get("status") or "not_run",
                "source": "research_pipeline_latest",
                "run_at": pipe.get("run_at"),
                "summary": pipe.get("summary"),
                "steps": pipe.get("steps") or [],
                "after": pipe.get("after") or {},
                "fund_org_summary": pipe.get("fund_org_summary") or {},
                "recommendations_summary": pipe.get("recommendations_summary") or {},
                "domain_supervisors": domain_supervisors,
                "suborg_summary": read_tmp_json("/tmp/research_org_suborg_summary_latest.json", {}),
                "next_actions": pipe.get("next_actions") or [],
            }
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid research pipeline file: {exc}") from exc
    return latest_research_org_report("research_pipeline") or {"status": "not_run", "source": "research_pipeline_latest"}


@app.get("/api/research/stock/latest")
def get_latest_stock_research() -> dict:
    path = Path("/tmp/stock_research_latest.json")
    if not path.exists():
        return {"research": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid stock research file: {exc}") from exc


@app.get("/api/disclosures")
def get_disclosures(limit: int = Query(default=100, ge=1, le=500), symbol: str | None = None, risk_level: str | None = None) -> dict:
    return {"items": list_disclosure_events(limit=limit, symbol=symbol, risk_level=risk_level)}


@app.get("/api/disclosures/features")
def get_disclosure_features(symbols: str = Query(default="AAPL,MSFT,NVDA,SPY,QQQ,005930.KS,000660.KS,035420.KS"), lookback_days: int = Query(default=30, ge=1, le=365)) -> dict:
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    return {"lookback_days": lookback_days, "features": disclosure_feature_summary(symbol_list, lookback_days)}


@app.get("/api/backtests/runs")
def get_backtest_runs(limit: int = Query(default=50, ge=1, le=500)) -> dict:
    return {"items": list_backtest_runs(limit)}


@app.post("/api/backtests/run")
def run_backtest_api(payload: BacktestRequest) -> dict:
    with get_connection() as conn:
        result = run_backtest(payload, conn)
        persist_backtest_result(conn, result, payload.model_dump())
        return result


@app.post("/api/backtests/sweep")
def run_backtest_sweep(payload: BacktestSweepRequest) -> dict:
    results = []
    with get_connection() as conn:
        for symbol in payload.symbols:
            for strategy in payload.strategies:
                if strategy == "ma_cross":
                    for short in payload.short_windows:
                        for long in payload.long_windows:
                            if short >= long:
                                continue
                            req = BacktestRequest(symbol=symbol, strategy=strategy, initial_cash=payload.initial_cash, fee_bps=payload.fee_bps, slippage_bps=payload.slippage_bps, short_window=short, long_window=long)
                            result = run_backtest(req, conn)
                            persist_backtest_result(conn, result, req.model_dump())
                            results.append({**result, "params": {"short_window": short, "long_window": long}})
                elif strategy == "rsi_reversion":
                    for buy in payload.rsi_buys:
                        for sell in payload.rsi_sells:
                            if buy >= sell:
                                continue
                            req = BacktestRequest(symbol=symbol, strategy=strategy, initial_cash=payload.initial_cash, fee_bps=payload.fee_bps, slippage_bps=payload.slippage_bps, rsi_buy=buy, rsi_sell=sell)
                            result = run_backtest(req, conn)
                            persist_backtest_result(conn, result, req.model_dump())
                            results.append({**result, "params": {"rsi_buy": buy, "rsi_sell": sell}})
    filtered = [r for r in results if r["trade_count"] >= payload.min_trades]
    ranked = sorted(filtered, key=lambda r: (r["total_return_pct"] - r["buy_hold_return_pct"], r["max_drawdown_pct"], r["trade_count"]), reverse=True)
    return {"count": len(results), "filtered_count": len(filtered), "items": ranked[: payload.limit]}


@app.post("/api/trades/buy", status_code=201)
def buy_trade(payload: TradeRequest) -> dict:
    return execute_trade("BUY", payload)


@app.post("/api/trades/sell", status_code=201)
def sell_trade(payload: TradeRequest) -> dict:
    return execute_trade("SELL", payload)


@app.get("/api/trades")
def list_trades() -> dict:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM paper_trades ORDER BY executed_at DESC, id DESC").fetchall()
        return {"items": [dict(row) for row in rows]}


@app.get("/api/portfolio")
def get_portfolio() -> dict:
    with get_connection() as conn:
        return compute_portfolio(conn)


@app.get("/api/portfolio/history")
def get_portfolio_history() -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY snapshot_at ASC, id ASC"
        ).fetchall()
        return {"items": [dict(row) for row in rows]}


@app.post("/api/portfolio/reset")
def reset_portfolio() -> dict:
    with get_connection() as conn:
        conn.execute("DELETE FROM paper_trades")
        conn.execute("DELETE FROM positions")
        conn.execute("DELETE FROM portfolio_snapshots")
        create_portfolio_snapshot(conn)
        return {"reset": True, "cash": settings.initial_cash}


@app.get("/login")
def login_page() -> FileResponse:
    return no_cache_file_response(STATIC_DIR / "login.html")


@app.get("/users")
def users_page() -> FileResponse:
    return no_cache_file_response(STATIC_DIR / "users.html")


@app.get("/dashboard")
def dashboard() -> FileResponse:
    return no_cache_file_response(STATIC_DIR / "index.html")


@app.get("/monitor")
def monitor() -> FileResponse:
    return no_cache_file_response(STATIC_DIR / "monitor.html")


@app.get("/")
def index() -> FileResponse:
    return no_cache_file_response(STATIC_DIR / "monitor.html")
