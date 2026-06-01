#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.config import get_settings
from tools.agents.lib.agent_contract import attach_contract


def load_json(path: str | Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def append_unique(out: list[str], values) -> None:
    for value in values or []:
        sym = str(value or "").upper().strip()
        if sym and sym not in out:
            out.append(sym)


def candidate_symbols(limit: int) -> tuple[list[str], dict]:
    symbols: list[str] = []
    recs = load_json("/tmp/recommendations_latest.json")
    append_unique(symbols, [x.get("symbol") for x in recs.get("items") or []])
    fund_rec = load_json("/tmp/fund_recommendation_consensus_latest.json")
    rows = sorted(fund_rec.get("items") or [], key=lambda x: float(x.get("weighted_score") or x.get("score") or 0), reverse=True)
    append_unique(symbols, [x.get("symbol") for x in rows])
    mover = load_json("/tmp/market_mover_seed_latest.json")
    upper = mover.get("top_upper_limit_items") or []
    append_unique(symbols, [x.get("symbol") for x in upper])
    return symbols[:limit], {
        "recommendation_count": len(recs.get("items") or []),
        "fund_recommendation_count": len(fund_rec.get("items") or []),
        "upper_limit_count": len(upper),
    }


def metric_block(rows: list[dict]) -> dict:
    vals = [float(x.get("excess_return_pct") or 0) for x in rows]
    finals = [float(x.get("final_return_pct") or 0) for x in rows]
    if not rows:
        return {"samples": 0}
    cutoff_years = {str(x.get("cutoff") or "")[:4] for x in rows if x.get("cutoff")}
    horizons = {int(x.get("horizon_days") or 0) for x in rows if x.get("horizon_days")}
    positive_years = 0
    for year in cutoff_years:
        yvals = [float(x.get("excess_return_pct") or 0) for x in rows if str(x.get("cutoff") or "").startswith(year)]
        if len(yvals) >= 2 and sum(yvals) / len(yvals) > 0:
            positive_years += 1
    return {
        "samples": len(rows),
        "avg_excess_return_pct": sum(vals) / len(vals),
        "avg_final_return_pct": sum(finals) / len(finals),
        "excess_win_rate": sum(1 for v in vals if v > 0) / len(vals),
        "worst_excess_return_pct": min(vals),
        "target_hit_rate": sum(1 for x in rows if x.get("result") == "success") / len(rows),
        "cutoff_year_count": len(cutoff_years),
        "positive_cutoff_year_count": positive_years,
        "horizon_count": len(horizons),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build anti-overfit fast-lane validation candidates from current recommendation/fund/mover symbols.")
    ap.add_argument("--symbol-limit", type=int, default=48)
    ap.add_argument("--logic-limit", type=int, default=16)
    ap.add_argument("--min-samples", type=int, default=5)
    ap.add_argument("--output", default="/tmp/alpha_fast_lane_latest.json")
    args = ap.parse_args()

    symbols, source_meta = candidate_symbols(args.symbol_limit)
    packet = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": "alpha_fast_lane",
        "real_trading": False,
        "source_meta": source_meta,
        "candidate_symbol_count": len(symbols),
        "items": [],
        "symbols": [],
        "logics": [],
        "policy": "historical/paper validation only; fast lane is exploration priority, not promotion evidence; promotion requires out-of-sample/cross-year/cross-horizon/cross-symbol support and existing gates remain strict",
    }
    if not symbols:
        attach_contract(packet, "alpha_fast_lane", status="degraded", warnings=["no candidate symbols"], outputs={"items": 0})
        Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(packet, ensure_ascii=False, indent=2))
        return 0

    placeholders = ",".join("?" for _ in symbols)
    q = f"""
    SELECT logic, symbol, cutoff, horizon_days, result, final_return_pct, excess_return_pct
    FROM recommendation_validation_results
    WHERE action='candidate_buy_zone' AND symbol IN ({placeholders})
    """
    conn = sqlite3.connect(get_settings().database_path)
    conn.row_factory = sqlite3.Row
    raw_rows = [dict(r) for r in conn.execute(q, symbols).fetchall()]
    conn.close()

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    logic_grouped: dict[str, list[dict]] = defaultdict(list)
    for row in raw_rows:
        grouped[(row.get("logic"), row.get("symbol"))].append(row)
        logic_grouped[row.get("logic")].append(row)
    logic_metrics = {logic: metric_block(rows) for logic, rows in logic_grouped.items()}

    items = []
    for (logic, symbol), rows in grouped.items():
        m = metric_block(rows)
        samples = int(m.get("samples") or 0)
        if samples < args.min_samples:
            continue
        avg_ex = float(m.get("avg_excess_return_pct") or 0)
        win = float(m.get("excess_win_rate") or 0)
        worst = float(m.get("worst_excess_return_pct") or 0)
        hit = float(m.get("target_hit_rate") or 0)
        lm = logic_metrics.get(logic) or {}
        logic_samples = int(lm.get("samples") or 0)
        logic_avg = float(lm.get("avg_excess_return_pct") or 0)
        logic_win = float(lm.get("excess_win_rate") or 0)
        score = avg_ex * 8 + win * 20 + hit * 6 + min(samples, 40) * 0.15 + max(worst, -20) * 0.4

        blockers = []
        if avg_ex <= 0:
            blockers.append("non_positive_avg_excess")
        if win < 0.52:
            blockers.append("weak_excess_win_rate")
        if worst < -18:
            blockers.append("left_tail_risk")

        overfit_flags = []
        if samples < 20:
            overfit_flags.append("small_symbol_sample")
        if m.get("cutoff_year_count", 0) < 2 or m.get("positive_cutoff_year_count", 0) < 2:
            overfit_flags.append("insufficient_cross_year_support")
        if m.get("horizon_count", 0) < 2:
            overfit_flags.append("single_horizon_support")
        if logic_samples < 80 or logic_avg <= 0 or logic_win < 0.50:
            overfit_flags.append("weak_logic_level_support")
        if worst < -18:
            overfit_flags.append("left_tail_risk")

        verdict = "fast_lane_candidate" if not blockers else "needs_more_validation"
        if overfit_flags:
            verdict = "exploration_candidate"

        items.append({
            "logic": logic,
            "symbol": symbol,
            "samples": samples,
            "avg_excess_return_pct": round(avg_ex, 3),
            "avg_final_return_pct": round(float(m.get("avg_final_return_pct") or 0), 3),
            "excess_win_rate_pct": round(win * 100, 2),
            "target_hit_rate_pct": round(hit * 100, 2),
            "worst_excess_return_pct": round(worst, 3),
            "cutoff_year_count": m.get("cutoff_year_count"),
            "positive_cutoff_year_count": m.get("positive_cutoff_year_count"),
            "horizon_count": m.get("horizon_count"),
            "logic_samples": logic_samples,
            "logic_avg_excess_return_pct": round(logic_avg, 3),
            "logic_excess_win_rate_pct": round(logic_win * 100, 2),
            "fast_lane_score": round(score, 3),
            "blockers": blockers,
            "overfit_flags": overfit_flags,
            "verdict": verdict,
        })
    items.sort(key=lambda x: (x["verdict"] == "fast_lane_candidate", x["fast_lane_score"], x["samples"]), reverse=True)

    out_symbols: list[str] = []
    out_logics: list[str] = []
    for item in items:
        if item["symbol"] not in out_symbols:
            out_symbols.append(item["symbol"])
        if item["logic"] not in out_logics:
            out_logics.append(item["logic"])
        if len(out_symbols) >= args.symbol_limit and len(out_logics) >= args.logic_limit:
            break

    packet.update({
        "items": items[:100],
        "symbols": out_symbols[:args.symbol_limit],
        "logics": out_logics[:args.logic_limit],
        "summary": {
            "candidate_count": len(items),
            "fast_lane_candidate_count": sum(1 for x in items if x["verdict"] == "fast_lane_candidate"),
            "exploration_candidate_count": sum(1 for x in items if x["verdict"] == "exploration_candidate"),
            "overfit_flagged_count": sum(1 for x in items if x.get("overfit_flags")),
            "top_symbols": out_symbols[:10],
            "top_logics": out_logics[:10],
        },
    })
    status = "ok" if items else "degraded"
    warnings = [] if items else ["no validation history for current fast-lane symbols"]
    if items:
        warnings.append("fast_lane_is_exploration_priority_not_promotion_evidence")
    attach_contract(packet, "alpha_fast_lane", status=status, outputs={"candidate_count": len(items), "symbols": len(packet["symbols"]), "logics": len(packet["logics"])}, metrics=packet["summary"], warnings=warnings)
    Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
