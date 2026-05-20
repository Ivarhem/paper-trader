#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.database import init_db
from tools.agents.lib.agent_contract import attach_contract, write_json_shared


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(a: float, b: float) -> float | None:
    return round((float(a) / float(b) - 1) * 100, 2) if b else None


def market_of(symbol: str) -> str:
    return "KR" if str(symbol).endswith((".KS", ".KQ")) else "US"


def benchmark_symbol(symbol: str) -> str:
    return "069500.KS" if market_of(symbol) == "KR" else "SPY"


def price_on_or_after(conn: sqlite3.Connection, symbol: str, date: str):
    return conn.execute(
        """
        SELECT date, close
        FROM price_bars
        WHERE symbol=? AND timeframe='1d' AND date>=?
        ORDER BY date ASC
        LIMIT 1
        """,
        (symbol, date),
    ).fetchone()


def future_price(conn: sqlite3.Connection, symbol: str, entry_date: str, horizon_days: int):
    rows = conn.execute(
        """
        SELECT date, close
        FROM price_bars
        WHERE symbol=? AND timeframe='1d' AND date>=?
        ORDER BY date ASC
        LIMIT ?
        """,
        (symbol, entry_date, horizon_days + 1),
    ).fetchall()
    if len(rows) <= horizon_days:
        return None
    return rows[horizon_days]


def summarize(rows: list[dict]) -> dict:
    complete = [r for r in rows if r.get("status") == "complete" and r.get("excess_return_pct") is not None]
    out = {"sample_count": len(rows), "complete_count": len(complete)}
    if not complete:
        return out
    excess = [float(r["excess_return_pct"]) for r in complete]
    ret = [float(r["forward_return_pct"]) for r in complete if r.get("forward_return_pct") is not None]
    bench = [float(r["benchmark_return_pct"]) for r in complete if r.get("benchmark_return_pct") is not None]
    out.update(
        {
            "avg_forward_return_pct": round(sum(ret) / len(ret), 2) if ret else None,
            "avg_benchmark_return_pct": round(sum(bench) / len(bench), 2) if bench else None,
            "avg_excess_return_pct": round(sum(excess) / len(excess), 2),
            "excess_win_rate_pct": round(sum(1 for x in excess if x > 0) / len(excess) * 100, 2),
            "p10_excess_return_pct": round(sorted(excess)[max(0, int(len(excess) * 0.1) - 1)], 2),
            "positive_count": sum(1 for x in excess if x > 0),
            "negative_count": sum(1 for x in excess if x <= 0),
        }
    )
    return out


def gate(summary: dict, min_samples: int) -> dict:
    complete = int(summary.get("complete_count") or 0)
    avg = summary.get("avg_excess_return_pct")
    win = summary.get("excess_win_rate_pct")
    p10 = summary.get("p10_excess_return_pct")
    if complete < min_samples:
        return {"decision": "collect_samples", "reason": f"complete samples {complete} < {min_samples}", "suggested_multiplier": 1.0}
    if avg is not None and avg >= 1.0 and (win or 0) >= 52 and (p10 is None or p10 > -8):
        return {"decision": "consider_upweight", "reason": "investor-flow cohort shows positive excess with tolerable tail", "suggested_multiplier": 1.05}
    if avg is not None and (avg <= -1.0 or (win is not None and win < 45) or (p10 is not None and p10 <= -10)):
        return {"decision": "downweight", "reason": "investor-flow cohort has weak excess or poor left-tail", "suggested_multiplier": 0.9}
    return {"decision": "keep", "reason": "mixed investor-flow cohort", "suggested_multiplier": 1.0}


def evaluate(conn: sqlite3.Connection, horizon_days: int, limit: int) -> list[dict]:
    flow_rows = conn.execute(
        """
        SELECT *
        FROM investor_flow_daily
        ORDER BY date DESC, rank ASC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out: list[dict] = []
    for row in flow_rows:
        symbol = row["symbol"]
        entry = price_on_or_after(conn, symbol, row["date"])
        if not entry:
            out.append({"symbol": symbol, "date": row["date"], "investor_type": row["investor_type"], "rank": row["rank"], "status": "missing_entry_price"})
            continue
        future = future_price(conn, symbol, entry["date"], horizon_days)
        bench_symbol = benchmark_symbol(symbol)
        bench_entry = price_on_or_after(conn, bench_symbol, entry["date"])
        bench_future = future_price(conn, bench_symbol, entry["date"], horizon_days) if bench_entry else None
        if not future or not bench_entry or not bench_future:
            out.append({"symbol": symbol, "date": row["date"], "investor_type": row["investor_type"], "rank": row["rank"], "entry_date": entry["date"], "status": "pending"})
            continue
        forward = pct(future["close"], entry["close"])
        bench = pct(bench_future["close"], bench_entry["close"])
        out.append(
            {
                "symbol": symbol,
                "market": market_of(symbol),
                "date": row["date"],
                "investor_type": row["investor_type"],
                "rank": row["rank"],
                "net_buy_amount": row["net_buy_amount"],
                "net_buy_qty": row["net_buy_qty"],
                "source": row["source"],
                "authority": row["authority"],
                "entry_date": entry["date"],
                "entry_close": float(entry["close"]),
                "final_date": future["date"],
                "final_close": float(future["close"]),
                "horizon_days": horizon_days,
                "benchmark_symbol": bench_symbol,
                "forward_return_pct": forward,
                "benchmark_return_pct": bench,
                "excess_return_pct": round((forward or 0) - (bench or 0), 2) if forward is not None and bench is not None else None,
                "status": "complete",
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate KR investor-flow seed cohorts against future benchmark-relative returns")
    ap.add_argument("--horizon-days", type=int, default=5)
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--min-samples", type=int, default=20)
    ap.add_argument("--output", default="/tmp/investor_flow_outcome_evaluator_latest.json")
    args = ap.parse_args()

    init_db()
    conn = sqlite3.connect(get_settings().database_path, timeout=30)
    conn.row_factory = sqlite3.Row
    rows = evaluate(conn, args.horizon_days, args.limit)
    conn.close()

    by_type: dict[str, list[dict]] = defaultdict(list)
    by_rank_bucket: dict[str, list[dict]] = defaultdict(list)
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    by_date_symbol: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in rows:
        by_type[str(r.get("investor_type") or "unknown")].append(r)
        rank = r.get("rank")
        bucket = "rank_1_3" if rank and rank <= 3 else ("rank_4_10" if rank and rank <= 10 else "rank_other")
        by_rank_bucket[bucket].append(r)
        by_symbol[str(r.get("symbol") or "")].append(r)
        if r.get("status") == "complete":
            by_date_symbol[(r.get("date"), r.get("symbol"))].add(str(r.get("investor_type")))

    both_rows = [r for r in rows if "foreign" in by_date_symbol.get((r.get("date"), r.get("symbol")), set()) and "institution" in by_date_symbol.get((r.get("date"), r.get("symbol")), set())]
    by_type["foreign_and_institution_same_day"] = both_rows

    summary_by_type = {k: {**summarize(v), "gate": gate(summarize(v), args.min_samples)} for k, v in sorted(by_type.items())}
    summary_by_rank = {k: {**summarize(v), "gate": gate(summarize(v), args.min_samples)} for k, v in sorted(by_rank_bucket.items())}
    symbol_rows = []
    for sym, arr in by_symbol.items():
        s = summarize(arr)
        if s.get("complete_count"):
            symbol_rows.append({"symbol": sym, **s})
    symbol_rows.sort(key=lambda x: (x.get("complete_count") or 0, x.get("avg_excess_return_pct") or -999), reverse=True)

    proposals = []
    for scope, groups in (("investor_type", summary_by_type), ("rank_bucket", summary_by_rank)):
        for name, s in groups.items():
            g = s.get("gate") or {}
            if g.get("decision") in ("consider_upweight", "downweight"):
                proposals.append({"scope": scope, "bucket": name, "decision": g.get("decision"), "reason": g.get("reason"), "suggested_multiplier": g.get("suggested_multiplier"), "evidence": s})

    warnings = []
    total_complete = sum(1 for r in rows if r.get("status") == "complete")
    if total_complete < args.min_samples:
        warnings.append("insufficient completed investor-flow outcome samples")
    if not rows:
        warnings.append("no investor_flow_daily rows available")

    packet = {
        "run_at": now(),
        "mode": "investor_flow_outcome_evaluator",
        "real_trading": False,
        "authority": "proposal_only_investor_flow_weight_learning_no_orders",
        "horizon_days": args.horizon_days,
        "rows_scanned": len(rows),
        "summary": {
            "total_count": len(rows),
            "complete_count": total_complete,
            "by_investor_type": summary_by_type,
            "by_rank_bucket": summary_by_rank,
            "top_symbols": symbol_rows[:20],
        },
        "weight_adjustment_proposals": proposals,
        "items": rows[:300],
        "warnings": warnings,
        "next_actions": ["Collect more dated investor-flow samples before changing recommendation weights." if warnings else "Review proposals; only apply after repeated confirmation across horizons."],
    }
    attach_contract(
        packet,
        "investor_flow_outcome_evaluator",
        status="degraded" if warnings else "ok",
        inputs={"horizon_days": args.horizon_days, "limit": args.limit, "min_samples": args.min_samples},
        outputs={"rows_scanned": len(rows), "complete_count": total_complete, "proposal_count": len(proposals)},
        metrics={"rows_scanned": len(rows), "complete_count": total_complete, "proposal_count": len(proposals), "warning_count": len(warnings)},
        warnings=warnings,
        next_actions=packet["next_actions"],
    )
    write_json_shared(args.output, packet)
    print(json.dumps(packet, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
