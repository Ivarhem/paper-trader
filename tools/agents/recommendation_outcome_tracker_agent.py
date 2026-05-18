#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.database import init_db, recommendation_outcome_summary, utc_now
from tools.agents.lib.agent_contract import attach_contract


def pct(a: float, b: float) -> float | None:
    return round((a / b - 1) * 100, 2) if b else None


def market_of(symbol: str) -> str:
    return "KR" if symbol.endswith((".KS", ".KQ")) else "US"


def benchmark_symbol_for(symbol: str) -> str:
    return "^KS11" if market_of(symbol) == "KR" else "SPY"


def price_rows(conn: sqlite3.Connection, symbol: str, start_date: str, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT date, close
        FROM price_bars
        WHERE symbol = ? AND timeframe = '1d' AND date >= ?
        ORDER BY date ASC
        LIMIT ?
        """,
        (symbol, start_date, limit),
    ).fetchall()


def compute_outcome(conn: sqlite3.Connection, row: sqlite3.Row, horizon: int) -> dict:
    run_date = row["run_at"][:10]
    symbol = row["symbol"]
    try:
        rec_payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        rec_payload = {}
    critic = rec_payload.get("critic") or {}
    committee = ((rec_payload.get("investment_committee") or {}).get("synthesis") or {})
    financial_quality = rec_payload.get("financial_quality") or {}
    decision_context = {
        "score": row["score"],
        "confidence_grade": row["confidence_grade"],
        "critic_severity": critic.get("severity") or "none",
        "critic_issue_count": len(critic.get("issues") or []),
        "committee_decision": committee.get("decision") or "none",
        "committee_score": committee.get("score"),
        "financial_score_adjustment": financial_quality.get("score_adjustment"),
    }
    rows = price_rows(conn, symbol, run_date, horizon + 1)
    if len(rows) < 2:
        return {
            "status": "pending",
            "bars": len(rows),
            "horizon_days": horizon,
            "symbol": symbol,
            "run_date": run_date,
            **decision_context,
        }

    entry = float(rows[0]["close"])
    final_row = rows[-1]
    final = float(final_row["close"])
    max_close = max(float(r["close"]) for r in rows)
    min_close = min(float(r["close"]) for r in rows)
    target = row["target_1"]
    stop = row["stop_reference"]
    status = "complete" if len(rows) >= horizon + 1 else "pending"
    stopped_out = False
    if stop is not None and min_close <= float(stop):
        stopped_out = True
        status = "stopped_out"

    bench = benchmark_symbol_for(symbol)
    bench_rows = price_rows(conn, bench, run_date, len(rows))
    bench_return = None
    if len(bench_rows) >= 2:
        bench_return = pct(float(bench_rows[-1]["close"]), float(bench_rows[0]["close"]))
    forward_return = pct(final, entry)
    excess = round(forward_return - bench_return, 2) if forward_return is not None and bench_return is not None else None
    hit = None
    if row["action"] in ("candidate_buy_zone", "buy", "strong_buy"):
        hit = 1 if (excess is not None and excess > 0) or (bench_return is None and (forward_return or 0) > 0) else 0
    elif row["action"] in ("avoid", "sell"):
        hit = 1 if (forward_return or 0) <= 0 else 0
    elif row["action"] in ("watch", "hold"):
        hit = 1 if abs(forward_return or 0) <= 3 else 0

    return {
        "status": status,
        "horizon_days": horizon,
        "symbol": symbol,
        "market": row["market"],
        "action": row["action"],
        "entry_date": rows[0]["date"],
        "entry_close": entry,
        "final_date": final_row["date"],
        "final_close": final,
        "forward_return_pct": forward_return,
        "benchmark_symbol": bench,
        "benchmark_return_pct": bench_return,
        "excess_return_pct": excess,
        "max_favorable_excursion_pct": pct(max_close, entry),
        "max_adverse_excursion_pct": pct(min_close, entry),
        "hit": hit,
        "stopped_out": 1 if stopped_out else 0,
        "bars": len(rows),
        "target_1": target,
        "stop_reference": stop,
        **decision_context,
    }


def ensure_outcome_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
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
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_recommendation_outcomes_symbol ON recommendation_outcomes(symbol, horizon_days, run_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_recommendation_outcomes_run ON recommendation_outcomes(run_at, horizon_days)")


def upsert_outcome(conn: sqlite3.Connection, rec: sqlite3.Row, horizon: int, outcome: dict) -> None:
    conn.execute(
        """
        INSERT INTO recommendation_outcomes (
            recommendation_history_id, run_at, symbol, market, action, horizon_days,
            entry_date, entry_close, final_date, final_close, forward_return_pct,
            benchmark_symbol, benchmark_return_pct, excess_return_pct,
            max_favorable_excursion_pct, max_adverse_excursion_pct, hit, stopped_out,
            status, payload_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(recommendation_history_id, horizon_days) DO UPDATE SET
            entry_date=excluded.entry_date,
            entry_close=excluded.entry_close,
            final_date=excluded.final_date,
            final_close=excluded.final_close,
            forward_return_pct=excluded.forward_return_pct,
            benchmark_symbol=excluded.benchmark_symbol,
            benchmark_return_pct=excluded.benchmark_return_pct,
            excess_return_pct=excluded.excess_return_pct,
            max_favorable_excursion_pct=excluded.max_favorable_excursion_pct,
            max_adverse_excursion_pct=excluded.max_adverse_excursion_pct,
            hit=excluded.hit,
            stopped_out=excluded.stopped_out,
            status=excluded.status,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
        """,
        (
            rec["id"],
            rec["run_at"],
            rec["symbol"],
            rec["market"],
            rec["action"],
            horizon,
            outcome.get("entry_date"),
            outcome.get("entry_close"),
            outcome.get("final_date"),
            outcome.get("final_close"),
            outcome.get("forward_return_pct"),
            outcome.get("benchmark_symbol"),
            outcome.get("benchmark_return_pct"),
            outcome.get("excess_return_pct"),
            outcome.get("max_favorable_excursion_pct"),
            outcome.get("max_adverse_excursion_pct"),
            outcome.get("hit"),
            outcome.get("stopped_out"),
            outcome.get("status"),
            json.dumps(outcome, ensure_ascii=False, sort_keys=True),
            utc_now(),
        ),
    )




def market_close_ready(market: str, trade_date: str) -> bool:
    if market == "KR":
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        if now.date().isoformat() > trade_date:
            return True
        return now.date().isoformat() == trade_date and now.time() >= time(15, 40)
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.date().isoformat() > trade_date:
        return True
    return now.date().isoformat() == trade_date and now.time() >= time(16, 10)

def ensure_daily_outcome_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recommendation_daily_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open_recommendation_history_id INTEGER NOT NULL,
            open_run_at TEXT NOT NULL,
            close_validated_at TEXT,
            action TEXT NOT NULL,
            score REAL,
            strategy_id TEXT,
            entry_date TEXT,
            entry_close REAL,
            final_date TEXT,
            final_close REAL,
            forward_return_pct REAL,
            benchmark_symbol TEXT,
            benchmark_return_pct REAL,
            excess_return_pct REAL,
            hit INTEGER,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(trade_date, market, symbol)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_outcomes_date_market ON recommendation_daily_outcomes(trade_date DESC, market)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_outcomes_symbol ON recommendation_daily_outcomes(symbol, trade_date DESC)")


def compute_daily_outcome(conn: sqlite3.Connection, rec: sqlite3.Row) -> dict:
    trade_date = rec["run_at"][:10]
    symbol = rec["symbol"]
    market = rec["market"] or market_of(symbol)
    try:
        payload = json.loads(rec["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    entry = payload.get("last_price")
    entry_date = payload.get("latest_price_date") or trade_date
    rows = price_rows(conn, symbol, trade_date, 3)
    if entry is None and rows:
        entry = float(rows[0]["close"])
        entry_date = rows[0]["date"]
    if entry is None:
        return {"trade_date": trade_date, "market": market, "symbol": symbol, "status": "pending", "entry_date": entry_date}
    final_row = rows[-1] if rows else None
    final = float(final_row["close"]) if final_row else None
    final_date = final_row["date"] if final_row else None
    # Daily canonical outcome is complete only after the relevant market close.
    ready = market_close_ready(market, trade_date)
    status = "complete" if ready and final is not None and final_date >= trade_date else "pending"
    bench = benchmark_symbol_for(symbol)
    bench_return = None
    if final_date:
        bench_rows = price_rows(conn, bench, trade_date, len(rows) or 2)
        if len(bench_rows) >= 1:
            bench_entry = float(bench_rows[0]["close"])
            bench_final = float(bench_rows[-1]["close"])
            bench_return = pct(bench_final, bench_entry)
    forward_return = pct(final, float(entry)) if final is not None else None
    excess = round(forward_return - bench_return, 2) if forward_return is not None and bench_return is not None else None
    hit = None
    if status == "complete":
        if rec["action"] in ("candidate_buy_zone", "buy", "strong_buy"):
            hit = 1 if (excess is not None and excess > 0) or (bench_return is None and (forward_return or 0) > 0) else 0
        elif rec["action"] in ("avoid", "sell"):
            hit = 1 if (forward_return or 0) <= 0 else 0
        else:
            hit = 1 if abs(forward_return or 0) <= 3 else 0
    return {
        "trade_date": trade_date,
        "market": market,
        "symbol": symbol,
        "status": status,
        "entry_date": entry_date,
        "entry_close": float(entry),
        "final_date": final_date,
        "final_close": final,
        "forward_return_pct": forward_return,
        "benchmark_symbol": bench,
        "benchmark_return_pct": bench_return,
        "excess_return_pct": excess,
        "hit": hit,
        "snapshot_type": "open_recommendation_close_validation",
    }


def upsert_daily_outcome(conn: sqlite3.Connection, rec: sqlite3.Row, outcome: dict) -> None:
    payload = {"open_recommendation": json.loads(rec["payload_json"] or "{}"), "outcome": outcome}
    conn.execute(
        """
        INSERT INTO recommendation_daily_outcomes (
            trade_date, market, symbol, open_recommendation_history_id, open_run_at,
            close_validated_at, action, score, strategy_id, entry_date, entry_close,
            final_date, final_close, forward_return_pct, benchmark_symbol, benchmark_return_pct,
            excess_return_pct, hit, status, payload_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_date, market, symbol) DO UPDATE SET
            close_validated_at=excluded.close_validated_at,
            final_date=excluded.final_date,
            final_close=excluded.final_close,
            forward_return_pct=excluded.forward_return_pct,
            benchmark_symbol=excluded.benchmark_symbol,
            benchmark_return_pct=excluded.benchmark_return_pct,
            excess_return_pct=excluded.excess_return_pct,
            hit=excluded.hit,
            status=excluded.status,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
        """,
        (
            outcome["trade_date"], outcome["market"], outcome["symbol"], rec["id"], rec["run_at"],
            utc_now() if outcome.get("status") == "complete" else None,
            rec["action"], rec["score"], rec["strategy_id"], outcome.get("entry_date"), outcome.get("entry_close"),
            outcome.get("final_date"), outcome.get("final_close"), outcome.get("forward_return_pct"), outcome.get("benchmark_symbol"), outcome.get("benchmark_return_pct"),
            outcome.get("excess_return_pct"), outcome.get("hit"), outcome.get("status"), json.dumps(payload, ensure_ascii=False, sort_keys=True), utc_now(),
        ),
    )



def export_daily_outcomes_static(conn: sqlite3.Connection, limit: int = 500) -> dict:
    rows = conn.execute(
        "SELECT * FROM recommendation_daily_outcomes ORDER BY trade_date DESC, market ASC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    items=[]
    for row in rows:
        item=dict(row)
        try: item["payload"] = json.loads(item.pop("payload_json"))
        except json.JSONDecodeError: item["payload"] = {}
        items.append(item)
    by_market_rows = conn.execute("""
        SELECT market, status, COUNT(*) n, AVG(forward_return_pct) avg_return,
               AVG(benchmark_return_pct) avg_benchmark, AVG(excess_return_pct) avg_excess, AVG(hit) hit_rate
        FROM recommendation_daily_outcomes WHERE status='complete' GROUP BY market, status ORDER BY market
    """).fetchall()
    recent_rows = conn.execute("""
        SELECT trade_date, market, COUNT(*) n,
               SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END) complete_n,
               AVG(CASE WHEN status='complete' THEN excess_return_pct END) avg_excess,
               AVG(CASE WHEN status='complete' THEN hit END) hit_rate
        FROM recommendation_daily_outcomes GROUP BY trade_date, market ORDER BY trade_date DESC, market ASC LIMIT 40
    """).fetchall()
    summary={
        "item_count": len(items),
        "complete_count": sum(1 for x in items if x.get("status")=="complete"),
        "pending_count": sum(1 for x in items if x.get("status")=="pending"),
        "by_market":[{"market":r["market"],"status":r["status"],"n":int(r["n"] or 0),"avg_return_pct":round(float(r["avg_return"] or 0),2),"avg_benchmark_pct":round(float(r["avg_benchmark"] or 0),2),"avg_excess_pct":round(float(r["avg_excess"] or 0),2),"hit_rate_pct":round(float(r["hit_rate"] or 0)*100,2)} for r in by_market_rows],
        "recent_days":[{"trade_date":r["trade_date"],"market":r["market"],"n":int(r["n"] or 0),"complete_n":int(r["complete_n"] or 0),"avg_excess_pct":round(float(r["avg_excess"] or 0),2),"hit_rate_pct":round(float(r["hit_rate"] or 0)*100,2)} for r in recent_rows],
    }
    packet={"run_at": datetime.now(timezone.utc).isoformat(), "mode":"daily_open_close_recommendation_outcomes", "items":items, "summary":summary}
    out=ROOT/'static'/'recommendation_daily_outcomes.json'
    out.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary

def update_daily_outcomes(conn: sqlite3.Connection, limit_days: int = 90) -> dict:
    ensure_daily_outcome_table(conn)
    rows = conn.execute(
        """
        SELECT * FROM recommendation_history rh
        WHERE rh.id IN (
            SELECT MIN(id) FROM recommendation_history
            WHERE run_at >= date('now', ?)
            GROUP BY substr(run_at,1,10), COALESCE(market,''), symbol
        )
        ORDER BY run_at DESC
        """,
        (f"-{limit_days} day",),
    ).fetchall()
    status_counts = {}
    for rec in rows:
        outcome = compute_daily_outcome(conn, rec)
        upsert_daily_outcome(conn, rec, outcome)
        status_counts[outcome.get("status")] = status_counts.get(outcome.get("status"), 0) + 1
    return {"daily_rows_scanned": len(rows), "daily_status_counts": status_counts}

def main() -> None:
    parser = argparse.ArgumentParser(description="Track forward outcomes for recommendation history")
    parser.add_argument("--horizons", default="1,5,20")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--output", default="/tmp/recommendation_outcomes_latest.json")
    args = parser.parse_args()
    horizons = [int(x.strip()) for x in args.horizons.split(",") if x.strip()]
    init_db()
    conn = sqlite3.connect(get_settings().database_path, timeout=30)
    conn.row_factory = sqlite3.Row
    ensure_outcome_table(conn)
    ensure_daily_outcome_table(conn)
    daily_metrics = update_daily_outcomes(conn)
    daily_static_summary = export_daily_outcomes_static(conn)
    rows = conn.execute(
        """
        SELECT id, run_at, symbol, market, action, score, target_1, stop_reference, confidence_grade, payload_json
        FROM recommendation_history
        ORDER BY run_at DESC, id DESC
        LIMIT ?
        """,
        (args.limit,),
    ).fetchall()
    updated = 0
    status_counts: dict[str, int] = {}
    for rec in rows:
        for horizon in horizons:
            outcome = compute_outcome(conn, rec, horizon)
            upsert_outcome(conn, rec, horizon, outcome)
            updated += 1
            status_counts[outcome["status"]] = status_counts.get(outcome["status"], 0) + 1
    conn.commit()
    conn.close()

    summary = recommendation_outcome_summary()
    packet = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": "recommendation_outcome_tracker",
        "real_trading": False,
        "history_rows_scanned": len(rows),
        "horizons": horizons,
        "updated_rows": updated,
        "status_counts": status_counts,
        "daily": {**daily_metrics, "static": daily_static_summary},
        "summary": summary,
    }
    warnings = []
    if not rows:
        warnings.append("recommendation_history has no rows")
    attach_contract(
        packet,
        "recommendation_outcome_tracker_agent",
        status="degraded" if warnings else "ok",
        inputs={"horizons": horizons, "limit": args.limit},
        outputs={"updated_rows": updated, "status_counts": status_counts},
        metrics={"history_rows_scanned": len(rows), "updated_rows": updated, **status_counts, **daily_metrics, "daily_static_items": daily_static_summary.get("item_count")},
        warnings=warnings,
        next_actions=["Generate recommendations before tracking outcomes."] if not rows else [],
    )
    Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(packet, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
