#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.agents.lib.agent_contract import attach_contract

OUT = Path("/tmp/candidate_funnel_latest.json")


def load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc), "_path": path}


def symbol_set(rows, key: str = "symbol") -> set[str]:
    return {str(row.get(key)).upper() for row in rows or [] if row.get(key)}


def count_by_action(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        action = row.get("action") or "unknown"
        out[action] = out.get(action, 0) + 1
    return out


def main() -> None:
    discovery = load_json("/tmp/universe_discovery_latest.json")
    refresh = load_json("/tmp/daily_price_refresh_latest.json")
    quality = load_json("/tmp/data_quality_latest.json")
    curator = load_json("/tmp/universe_curator_latest.json")
    scout = load_json("/tmp/universe_scout_latest.json")
    recs = load_json("/tmp/recommendations_latest.json")
    critic = load_json("/tmp/recommendation_critic_latest.json")
    committee = load_json("/tmp/investment_committee_latest.json")

    rec_items = recs.get("items") or []
    critic_items = critic.get("items") or []
    committee_items = committee.get("items") or []
    quality_symbols = quality.get("symbols") or []

    discovered = discovery.get("items") or discovery.get("selected") or discovery.get("candidates") or []
    scout_selected = scout.get("selected") or []
    curator_items = curator.get("items") or []
    active_curated = [x for x in curator_items if x.get("status") == "active"]
    quality_fail = [x for x in quality_symbols if x.get("level") == "fail"]
    quality_watch = [x for x in quality_symbols if x.get("level") == "watch"]
    critic_high = [x for x in critic_items if x.get("severity") == "high"]
    committee_support = [
        x for x in committee_items
        if ((x.get("committee") or {}).get("synthesis") or {}).get("decision") == "committee_support"
    ]
    committee_watch = [
        x for x in committee_items
        if ((x.get("committee") or {}).get("synthesis") or {}).get("decision") == "watch"
    ]
    committee_reject = [
        x for x in committee_items
        if ((x.get("committee") or {}).get("synthesis") or {}).get("decision") == "reject"
    ]

    stages = [
        {"stage": "discovered", "count": len(discovered), "symbols": sorted(symbol_set(discovered))[:100]},
        {"stage": "price_refreshed", "count": refresh.get("symbol_count") or 0, "symbols": (refresh.get("symbols") or [])[:100]},
        {"stage": "data_quality_fail", "count": len(quality_fail), "symbols": [x.get("symbol") for x in quality_fail[:100]]},
        {"stage": "data_quality_watch", "count": len(quality_watch), "symbols": [x.get("symbol") for x in quality_watch[:100]]},
        {"stage": "curated_active", "count": len(active_curated), "symbols": sorted(symbol_set(active_curated))[:100]},
        {"stage": "scout_selected", "count": len(scout_selected), "symbols": sorted(symbol_set(scout_selected))[:100]},
        {"stage": "recommendation_candidates", "count": sum((recs.get("candidate_market_counts") or {}).values()), "by_market": recs.get("candidate_market_counts") or {}},
        {"stage": "final_recommendations", "count": len(rec_items), "by_action": count_by_action(rec_items), "symbols": sorted(symbol_set(rec_items))[:100]},
        {"stage": "critic_high", "count": len(critic_high), "symbols": sorted(symbol_set(critic_high))[:100]},
        {"stage": "committee_support", "count": len(committee_support), "symbols": sorted(symbol_set(committee_support))[:100]},
        {"stage": "committee_watch", "count": len(committee_watch), "symbols": sorted(symbol_set(committee_watch))[:100]},
        {"stage": "committee_reject", "count": len(committee_reject), "symbols": sorted(symbol_set(committee_reject))[:100]},
    ]
    packet = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": "candidate_funnel",
        "real_trading": False,
        "stages": stages,
        "summary": {
            "final_recommendations": len(rec_items),
            "critic_high": len(critic_high),
            "committee_support": len(committee_support),
            "committee_watch": len(committee_watch),
            "committee_reject": len(committee_reject),
            "data_quality_fail": len(quality_fail),
            "data_quality_watch": len(quality_watch),
        },
    }
    warnings = []
    if not rec_items:
        warnings.append("no final recommendation rows found")
    attach_contract(
        packet,
        "candidate_funnel_agent",
        status="degraded" if warnings else "ok",
        outputs={"stage_count": len(stages), "final_recommendations": len(rec_items)},
        metrics=packet["summary"],
        warnings=warnings,
        next_actions=["Inspect upstream recommendation_agent output."] if warnings else [],
    )
    OUT.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(packet, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
