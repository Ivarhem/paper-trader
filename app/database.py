from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config import get_settings
from tools.agents.lib.fx import fx_rate_for_symbol, latest_usdkrw, price_to_krw


settings = get_settings()

VALUATION_CACHE_PATH = Path(os.getenv("PAPER_TRADER_VALUATION_CACHE", "/tmp/paper_trader_valuation_cache.json"))
VALUATION_CACHE_TTL_SECONDS = int(os.getenv("PAPER_TRADER_VALUATION_CACHE_TTL_SECONDS", "21600"))
VALUATION_MAX_FETCHES_PER_PROCESS = int(os.getenv("PAPER_TRADER_VALUATION_MAX_FETCHES_PER_PROCESS", "25"))
_VALUATION_FETCHES_THIS_PROCESS = 0


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.database_path, timeout=45)
    conn.execute("PRAGMA busy_timeout=45000")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS watchlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                note TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS price_bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                market TEXT NOT NULL DEFAULT 'stock',
                exchange TEXT,
                timeframe TEXT NOT NULL DEFAULT '1d',
                created_at TEXT NOT NULL,
                UNIQUE(symbol, date)
            );

            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                notional REAL NOT NULL,
                fees REAL NOT NULL DEFAULT 0,
                executed_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                quantity REAL NOT NULL,
                average_cost REAL NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_at TEXT NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                total_value REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                realized_pnl REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                params_json TEXT NOT NULL,
                bars INTEGER NOT NULL,
                total_return_pct REAL NOT NULL,
                buy_hold_return_pct REAL NOT NULL,
                max_drawdown_pct REAL NOT NULL,
                trade_count INTEGER NOT NULL,
                win_rate_pct REAL NOT NULL,
                profit_factor REAL,
                final_equity REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS external_context_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                market_regime TEXT NOT NULL,
                event_window TEXT,
                context_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS forward_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy TEXT NOT NULL,
                action TEXT NOT NULL,
                price REAL,
                reason TEXT,
                context_snapshot_id INTEGER,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS disclosure_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rcept_no TEXT NOT NULL UNIQUE,
                rcept_dt TEXT NOT NULL,
                corp_code TEXT,
                corp_name TEXT NOT NULL,
                stock_code TEXT,
                symbol TEXT,
                report_nm TEXT NOT NULL,
                category TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS financial_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                corp_code TEXT,
                bsns_year TEXT NOT NULL,
                reprt_code TEXT NOT NULL,
                revenue REAL,
                operating_income REAL,
                net_income REAL,
                assets REAL,
                liabilities REAL,
                equity REAL,
                operating_cashflow REAL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(symbol, bsns_year, reprt_code)
            );

            CREATE TABLE IF NOT EXISTS universe_members (
                symbol TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT,
                score REAL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recommendation_validation_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_key TEXT NOT NULL UNIQUE,
                logic TEXT NOT NULL,
                symbol TEXT NOT NULL,
                cutoff TEXT NOT NULL,
                horizon_days INTEGER NOT NULL,
                action TEXT,
                result TEXT,
                entry REAL,
                target REAL,
                stop REAL,
                final_return_pct REAL,
                benchmark_return_pct REAL,
                excess_return_pct REAL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS strategy_registry (
                logic TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                samples INTEGER NOT NULL,
                success_rate_pct REAL NOT NULL,
                avg_excess_return_pct REAL,
                recent_success_rate_pct REAL,
                recent_avg_excess_return_pct REAL,
                reason TEXT,
                summary_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS strategy_state_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                logic TEXT NOT NULL,
                old_status TEXT,
                new_status TEXT NOT NULL,
                reason TEXT,
                event_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS research_org_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT NOT NULL,
                run_id TEXT,
                status TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                returncode INTEGER,
                artifact_path TEXT,
                artifact_hash TEXT,
                summary TEXT,
                metrics_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS latest_artifacts (
                artifact_key TEXT PRIMARY KEY,
                artifact_path TEXT,
                status TEXT,
                summary TEXT,
                payload_json TEXT NOT NULL,
                payload_hash TEXT,
                updated_at TEXT NOT NULL
            );



            CREATE TABLE IF NOT EXISTS investor_flow_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                investor_type TEXT NOT NULL,
                net_buy_amount REAL,
                net_buy_qty REAL,
                rank INTEGER,
                source TEXT NOT NULL,
                authority TEXT NOT NULL,
                raw_text TEXT,
                payload_json TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(symbol, date, investor_type, source)
            );

            CREATE TABLE IF NOT EXISTS recommendation_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_history_id INTEGER NOT NULL,
                run_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT,
                action TEXT NOT NULL,
                horizon_days INTEGER NOT NULL,
                entry_date TEXT,
                entry_close REAL,
                final_date TEXT,
                final_close REAL,
                forward_return_pct REAL,
                benchmark_symbol TEXT,
                benchmark_return_pct REAL,
                excess_return_pct REAL,
                max_favorable_excursion_pct REAL,
                max_adverse_excursion_pct REAL,
                hit INTEGER,
                stopped_out INTEGER,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(recommendation_history_id, horizon_days)
            );
            """
        )
        ensure_column(conn, "price_bars", "market", "TEXT NOT NULL DEFAULT 'stock'")
        ensure_column(conn, "price_bars", "exchange", "TEXT")
        ensure_column(conn, "price_bars", "timeframe", "TEXT NOT NULL DEFAULT '1d'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_investor_flow_daily_symbol_date ON investor_flow_daily(symbol, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_investor_flow_daily_date_type ON investor_flow_daily(date, investor_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_created ON agent_runs(agent_name, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_run_id ON agent_runs(run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_latest_artifacts_updated ON latest_artifacts(updated_at)")
    seed_demo_data()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed_demo_data() -> None:
    with get_connection() as conn:
        watchlist_count = conn.execute("SELECT COUNT(*) AS count FROM watchlist_items").fetchone()["count"]
        price_count = conn.execute("SELECT COUNT(*) AS count FROM price_bars").fetchone()["count"]

        if watchlist_count == 0:
            created_at = utc_now()
            conn.executemany(
                "INSERT INTO watchlist_items (symbol, note, created_at) VALUES (?, ?, ?)",
                [
                    ("AAPL", "Large cap tech", created_at),
                    ("MSFT", "Cloud and AI", created_at),
                    ("NVDA", "Semis momentum", created_at),
                ],
            )

        if price_count == 0:
            sample_path = Path(__file__).resolve().parent.parent / "sample_data" / "prices_sample.csv"
            import_price_csv(str(sample_path), conn=conn)

        snapshot_count = conn.execute("SELECT COUNT(*) AS count FROM portfolio_snapshots").fetchone()["count"]
        if snapshot_count == 0:
            create_portfolio_snapshot(conn)


def import_price_csv(csv_path: str, conn: sqlite3.Connection | None = None) -> dict[str, int]:
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    path = Path(csv_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / csv_path
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    inserted = 0
    skipped = 0
    created_at = utc_now()
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"symbol", "date", "open", "high", "low", "close", "volume"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"CSV must contain headers: {sorted(required)}")

        for row in reader:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO price_bars (symbol, date, open, high, low, close, volume, market, exchange, timeframe, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'stock', NULL, '1d', ?)
                    """,
                    (
                        row["symbol"].upper().strip(),
                        row["date"].strip(),
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        float(row["volume"]),
                        created_at,
                    ),
                )
                if cur.rowcount:
                    inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1
    create_portfolio_snapshot(conn)
    if close_conn:
        conn.commit()
        conn.close()
    return {"inserted": inserted, "skipped": skipped}


def create_portfolio_snapshot(conn: sqlite3.Connection) -> None:
    cash = calculate_cash(conn)
    positions = conn.execute("SELECT symbol, quantity, average_cost FROM positions WHERE quantity > 0").fetchall()
    fx = latest_usdkrw(conn)
    positions_value = 0.0
    unrealized_pnl = 0.0
    for position in positions:
        latest_price = get_latest_price(position["symbol"], conn)
        if latest_price is None:
            continue
        latest_price_krw = price_to_krw(position["symbol"], latest_price, fx)
        market_value = latest_price_krw * position["quantity"]
        positions_value += market_value
        unrealized_pnl += (latest_price_krw - position["average_cost"]) * position["quantity"]
    realized_pnl = calculate_realized_pnl(conn)
    conn.execute(
        """
        INSERT INTO portfolio_snapshots (
            snapshot_at, cash, positions_value, total_value, unrealized_pnl, realized_pnl
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(),
            round(cash, 2),
            round(positions_value, 2),
            round(cash + positions_value, 2),
            round(unrealized_pnl, 2),
            round(realized_pnl, 2),
        ),
    )


def get_latest_price(symbol: str, conn: sqlite3.Connection) -> float | None:
    row = conn.execute(
        "SELECT close FROM price_bars WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol.upper(),),
    ).fetchone()
    return float(row["close"]) if row else None


def get_latest_price_krw(symbol: str, conn: sqlite3.Connection) -> dict | None:
    price = get_latest_price(symbol, conn)
    if price is None:
        return None
    fx = latest_usdkrw(conn)
    return {
        "symbol": symbol.upper(),
        "price": float(price),
        "price_krw": price_to_krw(symbol, float(price), fx),
        "fx_rate": fx_rate_for_symbol(symbol, fx),
        "fx_date": fx.get("date"),
        "fx_source": fx.get("source"),
    }


def calculate_cash(conn: sqlite3.Connection) -> float:
    trades = conn.execute(
        "SELECT side, notional, fees FROM paper_trades ORDER BY executed_at ASC, id ASC"
    ).fetchall()
    cash = settings.initial_cash
    for trade in trades:
        if trade["side"] == "BUY":
            cash -= float(trade["notional"]) + float(trade["fees"])
        else:
            cash += float(trade["notional"]) - float(trade["fees"])
    return cash


def calculate_realized_pnl(conn: sqlite3.Connection) -> float:
    trades = conn.execute(
        """
        SELECT symbol, side, quantity, price, notional, fees
        FROM paper_trades
        ORDER BY executed_at ASC, id ASC
        """
    ).fetchall()
    inventory: dict[str, dict[str, float]] = {}
    realized = 0.0
    for trade in trades:
        symbol = trade["symbol"]
        quantity = float(trade["quantity"])
        notional = float(trade["notional"])
        inventory.setdefault(symbol, {"quantity": 0.0, "average_cost": 0.0})
        lot = inventory[symbol]
        if trade["side"] == "BUY":
            total_cost = (lot["quantity"] * lot["average_cost"]) + notional
            lot["quantity"] += quantity
            lot["average_cost"] = total_cost / lot["quantity"]
        else:
            realized += notional - (lot["average_cost"] * quantity) - float(trade["fees"])
            lot["quantity"] -= quantity
            if lot["quantity"] <= 0:
                lot["quantity"] = 0.0
                lot["average_cost"] = 0.0
    return realized


def generate_sample_csv(csv_path: Path) -> None:
    if csv_path.exists():
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    start_date = datetime(2024, 1, 2, tzinfo=timezone.utc)
    symbols = {
        "AAPL": 182.0,
        "MSFT": 372.0,
        "NVDA": 492.0,
        "GOOG": 138.0,
    }
    rows: list[dict[str, str | float]] = []
    for symbol, base in symbols.items():
        for offset in range(35):
            date = (start_date + timedelta(days=offset)).date().isoformat()
            drift = offset * 0.8
            modifier = ((offset % 5) - 2) * 1.4
            close = round(base + drift + modifier, 2)
            open_price = round(close - 1.1, 2)
            high = round(close + 1.8, 2)
            low = round(close - 2.0, 2)
            volume = 1000000 + (offset * 15000)
            rows.append(
                {
                    "symbol": symbol,
                    "date": date,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }
            )
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["symbol", "date", "open", "high", "low", "close", "volume"]
        )
        writer.writeheader()
        writer.writerows(rows)


UPBIT_TIMEFRAMES = {
    "1m": "minutes/1",
    "3m": "minutes/3",
    "5m": "minutes/5",
    "10m": "minutes/10",
    "15m": "minutes/15",
    "30m": "minutes/30",
    "60m": "minutes/60",
    "240m": "minutes/240",
    "1d": "days",
    "1w": "weeks",
    "1mo": "months",
}


def import_upbit_candles(symbol: str, timeframe: str = "1h", count: int = 200) -> dict[str, int | str]:
    normalized_symbol = symbol.upper().strip()
    normalized_timeframe = timeframe.lower().strip()
    if normalized_timeframe == "1h":
        normalized_timeframe = "60m"
    if normalized_timeframe == "4h":
        normalized_timeframe = "240m"
    if normalized_timeframe not in UPBIT_TIMEFRAMES:
        raise ValueError(f"Unsupported Upbit timeframe: {timeframe}")
    if not 1 <= count <= 200:
        raise ValueError("Upbit count must be between 1 and 200")

    endpoint = UPBIT_TIMEFRAMES[normalized_timeframe]
    query = urlencode({"market": normalized_symbol, "count": count})
    url = f"https://api.upbit.com/v1/candles/{endpoint}?{query}"
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "paper-trader/0.1"})
    with urlopen(req, timeout=15) as response:
        candles = json.loads(response.read().decode("utf-8"))

    inserted = 0
    skipped = 0
    created_at = utc_now()
    with get_connection() as conn:
        for candle in reversed(candles):
            candle_at = candle.get("candle_date_time_kst") or candle.get("candle_date_time_utc")
            try:
                cur = conn.execute(
                    """
                    INSERT INTO price_bars (
                        symbol, date, open, high, low, close, volume, market, exchange, timeframe, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'crypto', 'upbit', ?, ?)
                    """,
                    (
                        normalized_symbol,
                        candle_at,
                        float(candle["opening_price"]),
                        float(candle["high_price"]),
                        float(candle["low_price"]),
                        float(candle["trade_price"]),
                        float(candle.get("candle_acc_trade_volume") or 0),
                        normalized_timeframe,
                        created_at,
                    ),
                )
                if cur.rowcount:
                    inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1
        conn.execute(
            """
            INSERT INTO watchlist_items (symbol, note, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET note = COALESCE(watchlist_items.note, excluded.note)
            """,
            (normalized_symbol, f"Upbit crypto {normalized_timeframe}", created_at),
        )
        create_portfolio_snapshot(conn)
    return {
        "symbol": normalized_symbol,
        "exchange": "upbit",
        "timeframe": normalized_timeframe,
        "inserted": inserted,
        "skipped": skipped,
    }



def save_external_context_snapshot(context: dict) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO external_context_snapshots (
                captured_at, risk_level, market_regime, event_window, context_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                context.get("captured_at") or utc_now(),
                context.get("risk_level") or "unknown",
                context.get("market_regime") or "unknown",
                context.get("event_window"),
                json.dumps(context, ensure_ascii=False, sort_keys=True),
                utc_now(),
            ),
        )
        return int(cur.lastrowid)


def list_external_context_snapshots(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM external_context_snapshots
            ORDER BY captured_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["context"] = json.loads(item.pop("context_json"))
        except json.JSONDecodeError:
            item["context"] = {}
        return_items = item
        items.append(return_items)
    return items



def list_backtest_runs(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM backtest_runs
            ORDER BY run_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["params"] = json.loads(item.pop("params_json"))
        except json.JSONDecodeError:
            item["params"] = {}
        items.append(item)
    return items



def save_forward_signal(signal: dict) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO forward_signals (
                signal_at, symbol, timeframe, strategy, action, price, reason,
                context_snapshot_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.get("signal_at") or utc_now(),
                signal["symbol"],
                signal.get("timeframe") or "15m",
                signal["strategy"],
                signal["action"],
                signal.get("price"),
                signal.get("reason"),
                signal.get("context_snapshot_id"),
                json.dumps(signal, ensure_ascii=False, sort_keys=True),
                utc_now(),
            ),
        )
        return int(cur.lastrowid)


def list_forward_signals(limit: int = 100) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM forward_signals
            ORDER BY signal_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json"))
        except json.JSONDecodeError:
            item["payload"] = {}
        items.append(item)
    return items


DISCLOSURE_RISK_KEYWORDS = {
    "high": ["상장폐지", "관리종목", "거래정지", "감자", "회생", "파산", "불성실공시", "횡령", "배임"],
    "medium": ["유상증자", "전환사채", "신주인수권", "타법인주식", "최대주주", "소송", "기재정정"],
    "positive": ["자기주식취득", "수주", "공급계약", "현금배당", "무상증자"],
}


def stock_code_to_symbol(stock_code: str | None) -> str | None:
    if not stock_code or len(stock_code) != 6 or not stock_code.isdigit():
        return None
    return f"{stock_code}.KS"


def classify_disclosure(report_name: str) -> tuple[str, str]:
    name = report_name or ""
    for risk, keywords in DISCLOSURE_RISK_KEYWORDS.items():
        if any(keyword in name for keyword in keywords):
            category = "risk" if risk in {"high", "medium"} else "positive"
            return category, risk
    if "분기보고서" in name or "반기보고서" in name or "사업보고서" in name:
        return "periodic_report", "low"
    return "other", "low"


def save_disclosure_events(events: list[dict]) -> dict[str, int]:
    init_db()
    inserted = skipped = 0
    created_at = utc_now()
    with get_connection() as conn:
        for event in events:
            report_name = event.get("report_nm") or ""
            category, risk_level = classify_disclosure(report_name)
            symbol = stock_code_to_symbol(event.get("stock_code"))
            try:
                cur = conn.execute(
                    """
                    INSERT INTO disclosure_events (
                        rcept_no, rcept_dt, corp_code, corp_name, stock_code, symbol,
                        report_nm, category, risk_level, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.get("rcept_no"),
                        event.get("rcept_dt"),
                        event.get("corp_code"),
                        event.get("corp_name") or "",
                        event.get("stock_code") or None,
                        symbol,
                        report_name,
                        category,
                        risk_level,
                        json.dumps(event, ensure_ascii=False, sort_keys=True),
                        created_at,
                    ),
                )
                if cur.rowcount:
                    inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1
    return {"inserted": inserted, "skipped": skipped}



def save_financial_snapshot(snapshot: dict) -> bool:
    init_db()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO financial_snapshots (
                symbol, corp_code, bsns_year, reprt_code, revenue, operating_income, net_income,
                assets, liabilities, equity, operating_cashflow, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, bsns_year, reprt_code) DO UPDATE SET
                corp_code=excluded.corp_code, revenue=excluded.revenue, operating_income=excluded.operating_income,
                net_income=excluded.net_income, assets=excluded.assets, liabilities=excluded.liabilities,
                equity=excluded.equity, operating_cashflow=excluded.operating_cashflow,
                payload_json=excluded.payload_json, created_at=excluded.created_at
            """,
            (
                snapshot.get("symbol"), snapshot.get("corp_code"), snapshot.get("bsns_year"), snapshot.get("reprt_code"),
                snapshot.get("revenue"), snapshot.get("operating_income"), snapshot.get("net_income"),
                snapshot.get("assets"), snapshot.get("liabilities"), snapshot.get("equity"), snapshot.get("operating_cashflow"),
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True), utc_now(),
            ),
        )
    return True


def _float_or_none(value) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _load_valuation_cache() -> dict:
    try:
        return json.loads(VALUATION_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_valuation_cache(cache: dict) -> None:
    try:
        VALUATION_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def latest_valuation_metrics(symbol: str, financial: dict | None = None) -> dict:
    """Fetch a small cached valuation packet for scoring.

    This is deliberately best-effort: recommendation generation must continue even
    if yfinance is slow, unavailable, or missing a multiple for a KR ticker.
    """
    symbol = symbol.upper()
    cache = _load_valuation_cache()
    cached = cache.get(symbol) or {}
    now = datetime.now(timezone.utc)
    try:
        fetched_at = datetime.fromisoformat(str(cached.get("fetched_at")).replace("Z", "+00:00"))
    except Exception:
        fetched_at = None
    if fetched_at and (now - fetched_at).total_seconds() <= VALUATION_CACHE_TTL_SECONDS:
        return cached.get("metrics") or {}

    if cached.get("metrics") and fetched_at:
        stale_metrics = dict(cached.get("metrics") or {})
        stale_metrics["stale"] = True
        stale_metrics["source"] = f"{stale_metrics.get('source') or 'yfinance_cached'}_stale"
        if _valuation_fetch_cap_reached():
            stale_metrics["refresh_deferred"] = "process_fetch_cap_reached"
            return stale_metrics

    metrics = {
        "per": None,
        "pbr": None,
        "roe_pct": None,
        "ev_ebitda": None,
        "market_cap": None,
        "dividend_yield_pct": None,
        "source": "yfinance_cached",
    }
    if financial and financial.get("net_income") not in (None, 0) and financial.get("equity") not in (None, 0):
        try:
            metrics["roe_pct"] = round(float(financial["net_income"]) / float(financial["equity"]) * 100, 2)
        except Exception:
            pass
    if _valuation_fetch_cap_reached():
        metrics["source"] = "financial_snapshot_only_fetch_deferred"
        metrics["refresh_deferred"] = "process_fetch_cap_reached"
        return metrics
    try:
        import yfinance as yf

        _increment_valuation_fetch_count()
        info = yf.Ticker(symbol).get_info() or {}
        per = _float_or_none(info.get("trailingPE") or info.get("forwardPE"))
        pbr = _float_or_none(info.get("priceToBook"))
        roe = _float_or_none(info.get("returnOnEquity"))
        ev_ebitda = _float_or_none(info.get("enterpriseToEbitda"))
        market_cap = _float_or_none(info.get("marketCap"))
        dividend_yield = _float_or_none(info.get("dividendYield"))
        if per is not None and per > 0:
            metrics["per"] = round(per, 2)
        if pbr is not None and pbr > 0:
            metrics["pbr"] = round(pbr, 2)
        if roe is not None:
            metrics["roe_pct"] = round(roe * 100 if abs(roe) <= 2 else roe, 2)
        if ev_ebitda is not None and ev_ebitda > 0:
            metrics["ev_ebitda"] = round(ev_ebitda, 2)
        if market_cap is not None:
            metrics["market_cap"] = market_cap
        if dividend_yield is not None:
            metrics["dividend_yield_pct"] = round(dividend_yield * 100 if dividend_yield <= 0.2 else dividend_yield, 2)
    except Exception as exc:
        metrics["error"] = str(exc)[:180]

    cache[symbol] = {"fetched_at": now.isoformat(), "metrics": metrics}
    _save_valuation_cache(cache)
    return metrics


def _valuation_fetch_cap_reached() -> bool:
    return VALUATION_MAX_FETCHES_PER_PROCESS >= 0 and _VALUATION_FETCHES_THIS_PROCESS >= VALUATION_MAX_FETCHES_PER_PROCESS


def _increment_valuation_fetch_count() -> None:
    global _VALUATION_FETCHES_THIS_PROCESS
    _VALUATION_FETCHES_THIS_PROCESS += 1


def valuation_score_adjustment(financial: dict, debt_ratio: float | None, op_margin: float | None, rev_growth: float | None) -> dict:
    val = latest_valuation_metrics(str(financial.get("symbol") or ""), financial)
    per = _float_or_none(val.get("per"))
    pbr = _float_or_none(val.get("pbr"))
    roe = _float_or_none(val.get("roe_pct"))
    ev_ebitda = _float_or_none(val.get("ev_ebitda"))
    score = 0
    supports: list[str] = []
    warnings: list[str] = []

    if per is not None:
        if 0 < per <= 8:
            score += 4; supports.append(f"PER 저평가 {per}")
        elif per <= 12:
            score += 3; supports.append(f"PER 양호 {per}")
        elif per <= 18:
            score += 1; supports.append(f"PER 보통 {per}")
        elif per >= 35:
            score -= 4; warnings.append(f"PER 고평가 {per}")
    if pbr is not None:
        if pbr <= 0.8:
            score += 4; supports.append(f"PBR 저평가 {pbr}")
        elif pbr <= 1.2:
            score += 3; supports.append(f"PBR 양호 {pbr}")
        elif pbr <= 1.8:
            score += 1; supports.append(f"PBR 보통 {pbr}")
        elif pbr >= 4:
            score -= 3; warnings.append(f"PBR 고평가 {pbr}")
    if roe is not None:
        if roe >= 15:
            score += 4; supports.append(f"ROE 우수 {roe}%")
        elif roe >= 8:
            score += 2; supports.append(f"ROE 양호 {roe}%")
        elif roe < 0:
            score -= 5; warnings.append(f"ROE 음수 {roe}%")
    if ev_ebitda is not None:
        if ev_ebitda <= 6:
            score += 2; supports.append(f"EV/EBITDA 저평가 {ev_ebitda}")
        elif ev_ebitda >= 20:
            score -= 2; warnings.append(f"EV/EBITDA 부담 {ev_ebitda}")

    cheap_enough = (
        (per is not None and per <= 12)
        or (pbr is not None and pbr <= 1.2)
        or (ev_ebitda is not None and ev_ebitda <= 7)
    )
    quality_ok = (
        (financial.get("net_income") is None or float(financial.get("net_income") or 0) > 0)
        and (op_margin is None or op_margin > 0)
        and (debt_ratio is None or debt_ratio <= 250)
        and (rev_growth is None or rev_growth > -15)
    )
    if cheap_enough and quality_ok and (roe is None or roe >= 8):
        score += 4
        supports.append("저평가+재무품질 composite 통과")
    elif cheap_enough and not quality_ok:
        score -= 4
        warnings.append("저평가처럼 보이나 재무 훼손/value trap 주의")

    return {
        "score_adjustment": max(-10, min(15, score)),
        "warnings": warnings,
        "supports": supports,
        "metrics": val,
        "policy": "cached_per_pbr_roe_ev_ebitda_value_composite",
    }


def latest_financial_quality(symbol: str) -> dict | None:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM financial_snapshots
            WHERE symbol=?
            ORDER BY bsns_year DESC, reprt_code DESC
            LIMIT 4
            """,
            (symbol.upper(),),
        ).fetchall()
    if not rows:
        return None
    latest = dict(rows[0])
    prev = dict(rows[1]) if len(rows) > 1 else None
    def growth(now, old):
        if now is None or old in (None, 0):
            return None
        return round((float(now) - float(old)) / abs(float(old)) * 100, 2)
    debt_ratio = None
    if latest.get("liabilities") is not None and latest.get("equity") not in (None, 0):
        debt_ratio = round(float(latest["liabilities"]) / float(latest["equity"]) * 100, 2)
    op_margin = None
    if latest.get("operating_income") is not None and latest.get("revenue") not in (None, 0):
        op_margin = round(float(latest["operating_income"]) / float(latest["revenue"]) * 100, 2)
    rev_growth = growth(latest.get("revenue"), prev.get("revenue") if prev else None)
    op_growth = growth(latest.get("operating_income"), prev.get("operating_income") if prev else None)
    warnings=[]; supports=[]; score=0
    if latest.get("net_income") is not None and float(latest["net_income"]) < 0:
        warnings.append("최근 순손실") ; score -= 12
    if op_margin is not None and op_margin < 0:
        warnings.append("영업이익률 적자") ; score -= 10
    if debt_ratio is not None and debt_ratio > 250:
        warnings.append(f"부채비율 높음 {debt_ratio}%") ; score -= 8
    if rev_growth is not None and rev_growth < -15:
        warnings.append(f"매출 감소 {rev_growth}%") ; score -= 6
    if op_growth is not None and op_growth < -25:
        warnings.append(f"영업이익 감소 {op_growth}%") ; score -= 6
    if latest.get("net_income") is not None and float(latest["net_income"]) > 0:
        supports.append("최근 순이익 흑자") ; score += 4
    if op_margin is not None and op_margin > 8:
        supports.append(f"영업이익률 {op_margin}%") ; score += 4
    if rev_growth is not None and rev_growth > 10:
        supports.append(f"매출 성장 {rev_growth}%") ; score += 3
    valuation = valuation_score_adjustment(latest, debt_ratio, op_margin, rev_growth)
    score += float(valuation.get("score_adjustment") or 0)
    warnings.extend(valuation.get("warnings") or [])
    supports.extend(valuation.get("supports") or [])
    return {
        "symbol": symbol.upper(), "latest_period": f"{latest.get('bsns_year')}/{latest.get('reprt_code')}",
        "score_adjustment": score, "warnings": warnings, "supports": supports,
        "valuation_score_adjustment": valuation.get("score_adjustment"),
        "valuation_policy": valuation.get("policy"),
        "valuation_metrics": valuation.get("metrics"),
        "debt_ratio_pct": debt_ratio, "operating_margin_pct": op_margin,
        "revenue_growth_pct": rev_growth, "operating_income_growth_pct": op_growth,
        "revenue": latest.get("revenue"), "operating_income": latest.get("operating_income"),
        "net_income": latest.get("net_income"), "assets": latest.get("assets"),
        "liabilities": latest.get("liabilities"), "equity": latest.get("equity"),
    }


def list_disclosure_events(limit: int = 100, symbol: str | None = None, risk_level: str | None = None) -> list[dict]:
    init_db()
    where = []
    params: list[object] = []
    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    if risk_level:
        where.append("risk_level = ?")
        params.append(risk_level)
    sql = "SELECT * FROM disclosure_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rcept_dt DESC, id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json"))
        except json.JSONDecodeError:
            item["payload"] = {}
        items.append(item)
    return items


def disclosure_feature_summary(symbols: list[str], lookback_days: int = 30) -> dict[str, dict]:
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y%m%d")
    result = {symbol: {"total": 0, "high": 0, "medium": 0, "positive": 0, "latest": None} for symbol in symbols}
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT symbol, risk_level, report_nm, rcept_dt
            FROM disclosure_events
            WHERE symbol IS NOT NULL AND rcept_dt >= ?
            ORDER BY rcept_dt DESC, id DESC
            """,
            (cutoff,),
        ).fetchall()
    for row in rows:
        symbol = row["symbol"]
        if symbol not in result:
            continue
        result[symbol]["total"] += 1
        risk = row["risk_level"]
        if risk in result[symbol]:
            result[symbol][risk] += 1
        if result[symbol]["latest"] is None:
            result[symbol]["latest"] = {"rcept_dt": row["rcept_dt"], "report_nm": row["report_nm"], "risk_level": risk}
    return result



def upsert_universe_members(items: list[dict]) -> dict[str, int]:
    init_db()
    updated = 0
    with get_connection() as conn:
        for item in items:
            conn.execute(
                """
                INSERT INTO universe_members (symbol, status, reason, score, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    status=excluded.status,
                    reason=excluded.reason,
                    score=excluded.score,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    item["symbol"], item["status"], item.get("reason"), item.get("score"),
                    item.get("updated_at") or utc_now(), json.dumps(item, ensure_ascii=False, sort_keys=True),
                ),
            )
            updated += 1
    return {"updated": updated}


def list_universe_members(limit: int = 500, status: str | None = None) -> list[dict]:
    init_db()
    sql = "SELECT * FROM universe_members"
    params: list[object] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC, symbol ASC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    items=[]
    for row in rows:
        item=dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json"))
        except json.JSONDecodeError:
            item["payload"] = {}
        items.append(item)
    return items



def save_validation_results(items: list[dict]) -> dict[str, int]:
    init_db()
    inserted = skipped = 0
    with get_connection() as conn:
        for item in items:
            key = f"{item.get('logic')}|{item.get('symbol')}|{item.get('cutoff')}|{item.get('horizon_days')}"
            try:
                cur = conn.execute(
                    """
                    INSERT INTO recommendation_validation_results (
                        run_key, logic, symbol, cutoff, horizon_days, action, result,
                        entry, target, stop, final_return_pct, benchmark_return_pct,
                        excess_return_pct, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key, item.get('logic'), item.get('symbol'), item.get('cutoff'), item.get('horizon_days'),
                        item.get('action'), item.get('result'), item.get('entry'), item.get('target'), item.get('stop'),
                        item.get('final_return_pct'), item.get('benchmark_return_pct'), item.get('excess_return_pct'),
                        json.dumps(item, ensure_ascii=False, sort_keys=True), utc_now(),
                    ),
                )
                if cur.rowcount:
                    inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1
    return {'inserted': inserted, 'skipped': skipped}


def list_validation_results(limit: int = 500, logic: str | None = None) -> list[dict]:
    init_db()
    sql = "SELECT * FROM recommendation_validation_results"
    params: list[object] = []
    if logic:
        sql += " WHERE logic = ?"
        params.append(logic)
    sql += " ORDER BY cutoff DESC, id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    items=[]
    for row in rows:
        item=dict(row)
        try:
            item['payload']=json.loads(item.pop('payload_json'))
        except json.JSONDecodeError:
            item['payload']={}
        items.append(item)
    return items


def validation_summary() -> dict:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT logic,
                   COUNT(*) AS samples,
                   SUM(CASE WHEN result='success' AND action='candidate_buy_zone' THEN 1 ELSE 0 END) AS success,
                   SUM(CASE WHEN result='fail' AND action='candidate_buy_zone' THEN 1 ELSE 0 END) AS fail,
                   SUM(CASE WHEN result='timeout' AND action='candidate_buy_zone' THEN 1 ELSE 0 END) AS timeout,
                   AVG(CASE WHEN action='candidate_buy_zone' THEN final_return_pct END) AS avg_return,
                   AVG(CASE WHEN action='candidate_buy_zone' THEN excess_return_pct END) AS avg_excess
            FROM recommendation_validation_results
            WHERE action='candidate_buy_zone'
            GROUP BY logic
            ORDER BY avg_excess DESC
            """
        ).fetchall()
    by_logic=[]
    for r in rows:
        samples=int(r['samples'] or 0)
        success=int(r['success'] or 0)
        by_logic.append({
            'logic': r['logic'], 'samples': samples, 'success': success,
            'fail': int(r['fail'] or 0), 'timeout': int(r['timeout'] or 0),
            'success_rate_pct': round(success / samples * 100, 2) if samples else 0,
            'avg_final_return_pct': round(float(r['avg_return'] or 0), 2),
            'avg_excess_return_pct': round(float(r['avg_excess'] or 0), 2),
            'verdict': 'pass' if samples >= 30 and success / samples >= 0.45 and float(r['avg_excess'] or 0) > 0 else ('weak' if samples >= 10 else 'insufficient_samples'),
        })
    ranked = sorted(
        by_logic,
        key=lambda x: (
            {'pass': 3, 'weak': 2, 'insufficient_samples': 1}.get(x.get('verdict'), 0),
            x.get('samples') or 0,
            x.get('avg_excess_return_pct') or -999,
            x.get('success_rate_pct') or 0,
        ),
        reverse=True,
    )
    return {'by_logic': by_logic, 'best': ranked[0] if ranked else None}



def list_strategy_registry() -> list[dict]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM strategy_registry ORDER BY status ASC, avg_excess_return_pct DESC").fetchall()
    items=[]
    for row in rows:
        item=dict(row)
        try: item['summary']=json.loads(item.pop('summary_json'))
        except json.JSONDecodeError: item['summary']={}
        items.append(item)
    return items



def upsert_strategy_candidates(items: list[dict]) -> dict[str, int]:
    init_db()
    inserted = updated = 0
    with get_connection() as conn:
        for item in items:
            logic = item['logic']
            existing = conn.execute("SELECT logic FROM strategy_registry WHERE logic=?", (logic,)).fetchone()
            if existing:
                updated += 1
                continue
            summary = {"candidate_config": item, "narrative": f"{logic}: pending validation."}
            conn.execute(
                """INSERT INTO strategy_registry
                (logic,status,samples,success_rate_pct,avg_excess_return_pct,recent_success_rate_pct,recent_avg_excess_return_pct,reason,summary_json,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (logic, 'pending_validation', 0, 0, None, 0, None, 'generated candidate awaiting validation', json.dumps(summary, ensure_ascii=False, sort_keys=True), utc_now())
            )
            inserted += 1
    return {"inserted": inserted, "updated": updated}


def _monthly_cutoff_count(start: str = '2023-01-01') -> int:
    today = datetime.now(timezone.utc).date()
    y, m = map(int, start[:7].split('-'))
    count = 0
    while (y, m) <= (today.year, today.month):
        count += 1
        m += 1
        if m > 12:
            y += 1
            m = 1
    return count


def validation_coverage() -> dict:
    init_db()
    with get_connection() as conn:
        total_strategies = conn.execute("SELECT COUNT(*) AS c FROM strategy_registry").fetchone()['c']
        active_symbols = conn.execute("SELECT COUNT(*) AS c FROM universe_members WHERE status='active'").fetchone()['c']
        rows = conn.execute("""
            SELECT logic, COUNT(*) AS completed,
                   SUM(CASE WHEN action='candidate_buy_zone' THEN 1 ELSE 0 END) AS candidate_samples
            FROM recommendation_validation_results GROUP BY logic
        """).fetchall()
        statuses = conn.execute("SELECT status, COUNT(*) AS c FROM strategy_registry GROUP BY status").fetchall()
    by_logic = [{'logic': r['logic'], 'completed': int(r['completed'] or 0), 'candidate_samples': int(r['candidate_samples'] or 0)} for r in rows]
    completed = sum(x['completed'] for x in by_logic)
    monthly_cutoffs = _monthly_cutoff_count('2023-01-01')
    horizon_count = 4
    target_results = int(total_strategies or 0) * int(active_symbols or 0) * monthly_cutoffs * horizon_count
    pending_estimate = max(target_results - completed, 0)
    return {
        'strategy_count': int(total_strategies or 0),
        'active_symbol_count': int(active_symbols or 0),
        'monthly_cutoff_count': monthly_cutoffs,
        'horizon_count': horizon_count,
        'completed_results': completed,
        'target_results_estimate': target_results,
        'pending_results_estimate': pending_estimate,
        'coverage_pct_estimate': round(completed / target_results * 100, 2) if target_results else 0,
        'by_status': {r['status']: int(r['c'] or 0) for r in statuses},
        'under_tested': sorted(by_logic, key=lambda x: x['candidate_samples'])[:20],
        'top_tested': sorted(by_logic, key=lambda x: x['candidate_samples'], reverse=True)[:20],
    }



def save_agent_run_artifact(
    agent_name: str,
    status: str,
    *,
    run_id: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    returncode: int | None = None,
    artifact_path: str | None = None,
    artifact_hash: str | None = None,
    summary: str | None = None,
    metrics: dict | None = None,
    warnings: list | None = None,
    payload: dict | None = None,
) -> int:
    """Persist a compact DB index for generated agent artifacts.

    Raw, large JSON can remain in /tmp or state artifacts; this table keeps the
    searchable run metadata needed for latest views, trend checks, and cleanup.
    """
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO agent_runs (
                agent_name, run_id, status, started_at, ended_at, returncode,
                artifact_path, artifact_hash, summary, metrics_json,
                warnings_json, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_name,
                run_id,
                status,
                started_at,
                ended_at,
                returncode,
                artifact_path,
                artifact_hash,
                summary,
                json.dumps(metrics or {}, ensure_ascii=False, sort_keys=True),
                json.dumps(warnings or [], ensure_ascii=False, sort_keys=True),
                json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                utc_now(),
            ),
        )
        return int(cur.lastrowid)


def save_latest_artifact(
    artifact_key: str,
    payload: dict,
    *,
    artifact_path: str | None = None,
    status: str | None = None,
    summary: str | None = None,
) -> None:
    """Persist the latest small/medium agent artifact in DB.

    File mirrors remain for static fallback and compatibility, but UI/API code
    should prefer this table so mixed /tmp ownership stops being a correctness
    dependency.
    """
    init_db()
    payload = payload or {}
    contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
    payload_status = status or payload.get("status") or contract.get("status")
    payload_summary = summary or str(payload.get("summary") or contract.get("summary") or "")[:1000]
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    payload_hash = __import__("hashlib").sha256(payload_json.encode("utf-8")).hexdigest()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO latest_artifacts (
                artifact_key, artifact_path, status, summary, payload_json,
                payload_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_key) DO UPDATE SET
                artifact_path=excluded.artifact_path,
                status=excluded.status,
                summary=excluded.summary,
                payload_json=excluded.payload_json,
                payload_hash=excluded.payload_hash,
                updated_at=excluded.updated_at
            """,
            (
                artifact_key,
                artifact_path,
                payload_status,
                payload_summary,
                payload_json,
                payload_hash,
                utc_now(),
            ),
        )


def latest_artifact(artifact_key: str) -> dict | None:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM latest_artifacts WHERE artifact_key = ?",
            (artifact_key,),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        payload = json.loads(item.get("payload_json") or "{}")
    except json.JSONDecodeError:
        payload = {"status": "error", "error": "invalid latest_artifacts payload_json"}
    if isinstance(payload, dict):
        payload = dict(payload)
        payload.setdefault("_db_artifact_key", artifact_key)
        payload.setdefault("_db_updated_at", item.get("updated_at"))
        payload.setdefault("_db_payload_hash", item.get("payload_hash"))
    return payload


def latest_agent_runs(limit: int = 100, agent_name: str | None = None) -> list[dict]:
    init_db()
    params: list[object] = []
    sql = "SELECT * FROM agent_runs"
    if agent_name:
        sql += " WHERE agent_name = ?"
        params.append(agent_name)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        for source, target in (("metrics_json", "metrics"), ("warnings_json", "warnings"), ("payload_json", "payload")):
            try:
                item[target] = json.loads(item.pop(source))
            except json.JSONDecodeError:
                item[target] = {} if target != "warnings" else []
        items.append(item)
    return items


def save_research_org_report(report_type: str, summary: str, payload: dict) -> int:
    init_db()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO research_org_reports (report_type, summary, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (report_type, summary, json.dumps(payload, ensure_ascii=False, sort_keys=True), utc_now()),
        )
        return int(cur.lastrowid)


def latest_research_org_report(report_type: str | None = None) -> dict | None:
    init_db()
    with get_connection() as conn:
        if report_type:
            row = conn.execute("SELECT * FROM research_org_reports WHERE report_type=? ORDER BY id DESC LIMIT 1", (report_type,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM research_org_reports ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item['payload'] = json.loads(item.pop('payload_json'))
    except json.JSONDecodeError:
        item['payload'] = {}
    return item


def list_recommendation_outcomes(limit: int = 500, symbol: str | None = None, horizon_days: int | None = None) -> list[dict]:
    init_db()
    where = []
    params: list[object] = []
    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    if horizon_days:
        where.append("horizon_days = ?")
        params.append(horizon_days)
    sql = "SELECT * FROM recommendation_outcomes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY run_at DESC, horizon_days ASC, id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json"))
        except json.JSONDecodeError:
            item["payload"] = {}
        items.append(item)
    return items



def list_recommendation_daily_outcomes(limit: int = 500, symbol: str | None = None, market: str | None = None) -> list[dict]:
    init_db()
    where=[]; params=[]
    if symbol:
        where.append("symbol = ?"); params.append(symbol.upper())
    if market:
        where.append("market = ?"); params.append(market.upper())
    sql="SELECT * FROM recommendation_daily_outcomes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY trade_date DESC, market ASC, id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows=conn.execute(sql, params).fetchall()
    items=[]
    for row in rows:
        item=dict(row)
        try: item["payload"] = json.loads(item.pop("payload_json"))
        except json.JSONDecodeError: item["payload"] = {}
        items.append(item)
    return items


def recommendation_daily_outcome_summary() -> dict:
    init_db()
    with get_connection() as conn:
        rows=conn.execute("""
            SELECT market, status, COUNT(*) n, AVG(forward_return_pct) avg_return,
                   AVG(benchmark_return_pct) avg_benchmark, AVG(excess_return_pct) avg_excess,
                   AVG(hit) hit_rate
            FROM recommendation_daily_outcomes
            WHERE status='complete'
            GROUP BY market, status
            ORDER BY market
        """).fetchall()
        recent=conn.execute("""
            SELECT trade_date, market, COUNT(*) n,
                   SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END) complete_n,
                   AVG(CASE WHEN status='complete' THEN excess_return_pct END) avg_excess,
                   AVG(CASE WHEN status='complete' THEN hit END) hit_rate
            FROM recommendation_daily_outcomes
            GROUP BY trade_date, market
            ORDER BY trade_date DESC, market ASC
            LIMIT 40
        """).fetchall()
    return {
        "by_market":[{"market":r["market"],"status":r["status"],"n":int(r["n"] or 0),"avg_return_pct":round(float(r["avg_return"] or 0),2),"avg_benchmark_pct":round(float(r["avg_benchmark"] or 0),2),"avg_excess_pct":round(float(r["avg_excess"] or 0),2),"hit_rate_pct":round(float(r["hit_rate"] or 0)*100,2)} for r in rows],
        "recent_days":[{"trade_date":r["trade_date"],"market":r["market"],"n":int(r["n"] or 0),"complete_n":int(r["complete_n"] or 0),"avg_excess_pct":round(float(r["avg_excess"] or 0),2),"hit_rate_pct":round(float(r["hit_rate"] or 0)*100,2)} for r in recent],
    }

def recommendation_outcome_summary() -> dict:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT horizon_days, action, status,
                   COUNT(*) AS n,
                   AVG(forward_return_pct) AS avg_return,
                   AVG(benchmark_return_pct) AS avg_benchmark,
                   AVG(excess_return_pct) AS avg_excess,
                   AVG(hit) AS hit_rate
            FROM recommendation_outcomes
            WHERE status IN ('complete', 'stopped_out')
            GROUP BY horizon_days, action, status
            ORDER BY horizon_days ASC, action ASC, status ASC
            """
        ).fetchall()
        confidence_rows = conn.execute(
            """
            SELECT ro.horizon_days, rh.confidence_grade,
                   COUNT(*) AS n,
                   AVG(ro.excess_return_pct) AS avg_excess,
                   AVG(ro.hit) AS hit_rate
            FROM recommendation_outcomes ro
            JOIN recommendation_history rh ON rh.id = ro.recommendation_history_id
            WHERE ro.status IN ('complete', 'stopped_out')
            GROUP BY ro.horizon_days, rh.confidence_grade
            ORDER BY ro.horizon_days ASC, rh.confidence_grade ASC
            """
        ).fetchall()
    by_action = [
        {
            "horizon_days": int(r["horizon_days"]),
            "action": r["action"],
            "status": r["status"],
            "n": int(r["n"] or 0),
            "avg_return_pct": round(float(r["avg_return"] or 0), 2),
            "avg_benchmark_pct": round(float(r["avg_benchmark"] or 0), 2),
            "avg_excess_pct": round(float(r["avg_excess"] or 0), 2),
            "hit_rate_pct": round(float(r["hit_rate"] or 0) * 100, 2),
        }
        for r in rows
    ]
    by_confidence = [
        {
            "horizon_days": int(r["horizon_days"]),
            "confidence_grade": r["confidence_grade"],
            "n": int(r["n"] or 0),
            "avg_excess_pct": round(float(r["avg_excess"] or 0), 2),
            "hit_rate_pct": round(float(r["hit_rate"] or 0) * 100, 2),
        }
        for r in confidence_rows
    ]
    return {"by_action": by_action, "by_confidence": by_confidence}


def save_investor_flow_seed(packet: dict, conn: sqlite3.Connection | None = None) -> dict[str, int]:
    """Persist provisional investor-flow seed rows for paper research evidence.

    Source pages are delayed/scraped, so authority remains paper_monitoring_seed_only.
    Numeric page fields are stored as raw payload; net_buy_amount/qty are best-effort only.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True
    created_at = utc_now()
    inserted = updated = skipped = 0
    rows = packet.get("items") or []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        investor = str(row.get("investor") or "").strip()
        captured_at = row.get("captured_at") or packet.get("run_at") or created_at
        date = str(captured_at)[:10]
        if not symbol or not investor or not date:
            skipped += 1
            continue
        vals = row.get("raw_numeric_values") or []
        net_amount = vals[0] if len(vals) >= 1 else None
        net_qty = vals[1] if len(vals) >= 2 else None
        payload = dict(row)
        payload.setdefault("data_quality", "provisional_delayed_scraped")
        cur = conn.execute(
            """
            INSERT INTO investor_flow_daily (
                symbol, date, investor_type, net_buy_amount, net_buy_qty, rank,
                source, authority, raw_text, payload_json, captured_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date, investor_type, source) DO UPDATE SET
                net_buy_amount=excluded.net_buy_amount,
                net_buy_qty=excluded.net_buy_qty,
                rank=excluded.rank,
                authority=excluded.authority,
                raw_text=excluded.raw_text,
                payload_json=excluded.payload_json,
                captured_at=excluded.captured_at
            """,
            (
                symbol,
                date,
                investor,
                net_amount,
                net_qty,
                row.get("rank"),
                row.get("source") or packet.get("provider") or "unknown",
                row.get("authority") or packet.get("authority") or "paper_monitoring_seed_only",
                row.get("raw_text"),
                json.dumps(payload, ensure_ascii=False),
                captured_at,
                created_at,
            ),
        )
        # sqlite rowcount is 1 for insert/update; distinguish by checking existing is not worth extra queries here.
        if cur.rowcount:
            inserted += 1
    if close_conn:
        conn.commit()
        conn.close()
    return {"inserted_or_updated": inserted, "skipped": skipped, "source_count": len(rows)}


def latest_investor_flow_for_symbol(symbol: str, conn: sqlite3.Connection | None = None, lookback_days: int = 5) -> list[sqlite3.Row]:
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date().isoformat()
    rows = conn.execute(
        """
        SELECT * FROM investor_flow_daily
        WHERE symbol=? AND date>=?
        ORDER BY date DESC, rank ASC
        """,
        (symbol.upper().strip(), cutoff),
    ).fetchall()
    if close_conn:
        conn.close()
    return rows
