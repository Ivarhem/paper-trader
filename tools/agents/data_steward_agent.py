#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agents.domain_supervisor_lib import contract_status, emit_supervisor, fitness, read_json, warnings

OWNED = ["pipeline_smoke_check", "universe_discovery", "common_universe", "daily_price_refresh", "data_quality", "opendart_disclosures", "sec_edgar_disclosures", "market_mover_seed", "investor_flow_seed", "external_mover_validation"]
ARTIFACTS = {
    "pipeline_smoke_check": "/tmp/pipeline_smoke_check_latest.json",
    "universe_discovery": "/tmp/universe_discovery_latest.json",
    "common_universe": "/tmp/common_universe_latest.json",
    "daily_price_refresh": "/tmp/daily_price_refresh_latest.json",
    "data_quality": "/tmp/data_quality_latest.json",
    "opendart_disclosures": "/tmp/opendart_disclosures_latest.json",
    "sec_edgar_disclosures": "/tmp/sec_edgar_disclosures_latest.json",
    "market_mover_seed": "/tmp/market_mover_seed_latest.json",
    "investor_flow_seed": "/tmp/investor_flow_seed_latest.json",
    "external_mover_validation": "/tmp/external_mover_validation_latest.json",
}


def _count_disclosures(packet: dict) -> int:
    return len(packet.get("items") or packet.get("list") or [])


def main() -> None:
    packets = {agent: read_json(path) for agent, path in ARTIFACTS.items()}
    universe = packets["common_universe"]
    price = packets["daily_price_refresh"]
    quality = packets["data_quality"]
    kr_disclosures = packets["opendart_disclosures"]
    us_disclosures = packets["sec_edgar_disclosures"]

    rows = []
    for agent in OWNED:
        packet = packets.get(agent) or {}
        checks = []
        penalty = 0
        if agent == "common_universe":
            count = int(universe.get("item_count") or (universe.get("summary") or {}).get("item_count") or 0)
            checks.append(f"universe_items={count}")
            if count == 0:
                penalty += 30
        if agent == "daily_price_refresh":
            refreshed = int(price.get("refreshed_count") or 0)
            checks.append(f"refreshed_count={refreshed}")
            if refreshed == 0:
                penalty += 15
            max_lag = price.get("max_lag_by_market_days") or {}
            if isinstance(max_lag, dict) and any(int(v or 0) > 5 for v in max_lag.values()):
                checks.append(f"max_lag_by_market_days={max_lag}")
                penalty += 15
        if agent == "data_quality":
            problems = int((quality.get("summary") or {}).get("problem_count") or len(quality.get("problems") or []))
            checks.append(f"quality_problems={problems}")
            if problems:
                penalty += min(30, problems * 5)
        rows.append(fitness(agent, contract_status(packet), len(warnings(packet)), checks, penalty))

    max_lag = price.get("max_lag_by_market_days") or {}
    stale_markets = [market for market, lag in max_lag.items() if int(lag or 0) > 5] if isinstance(max_lag, dict) else []
    quality_problems = int((quality.get("summary") or {}).get("problem_count") or len(quality.get("problems") or []))
    missing = []
    if stale_markets:
        missing.append({"capability": "market_specific_price_freshness_repair", "reason": f"stale markets: {stale_markets}", "severity": "action"})
    if quality_problems:
        missing.append({"capability": "data_quality_repair_queue", "reason": f"quality problems: {quality_problems}", "severity": "action"})

    summary = {
        "universe_item_count": universe.get("item_count") or (universe.get("summary") or {}).get("item_count"),
        "universe_market_counts": universe.get("market_counts"),
        "price_refreshed_count": price.get("refreshed_count"),
        "price_max_lag_by_market_days": max_lag,
        "data_quality_summary": quality.get("summary"),
        "kr_disclosure_count": _count_disclosures(kr_disclosures),
        "us_disclosure_count": _count_disclosures(us_disclosures),
        "mover_seed_count": len(packets["market_mover_seed"].get("items") or packets["market_mover_seed"].get("top_symbols") or []),
        "investor_flow_seed_count": len(packets["investor_flow_seed"].get("items") or packets["investor_flow_seed"].get("top_symbols") or []),
    }
    assignments = [
        {"owner_agent": "daily_price_refresh", "assignment": "repair stale market price coverage before recommendation/fund consumers use same-cycle data", "priority": "high" if stale_markets else "normal", "source_bottleneck": "price_freshness", "target_artifact": "/tmp/daily_price_refresh_latest.json"},
        {"owner_agent": "data_quality", "assignment": "turn quality findings into a bounded repair queue with symbol/market scope", "priority": "high" if quality_problems else "normal", "source_bottleneck": "data_quality", "target_artifact": "/tmp/data_quality_latest.json"},
        {"owner_agent": "common_universe", "assignment": "keep one canonical universe shared by strategy, fund, market, and recommendation agents", "priority": "normal", "source_bottleneck": "universe_drift", "target_artifact": "/tmp/common_universe_latest.json"},
    ]
    emit_supervisor(supervisor="data_steward", title="Data Steward", domain="data_office", owned_agents=OWNED, role_fitness=rows, summary=summary, missing_capability=missing, bottleneck="data freshness or quality requires repair" if missing else None, bottleneck_severity="action" if missing else "none", next_cycle_assignments=assignments, output="/tmp/data_steward_latest.json")


if __name__ == "__main__":
    main()
