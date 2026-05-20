#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agents.domain_supervisor_lib import contract_status, emit_supervisor, fitness, read_json, warnings

OWNED = ["market_context", "market_shock_mover_scout", "supply_close_strength_scout", "theme_spillover_backtest", "market_issue_scout", "market_news_issue_scout", "market_issue_narrative", "next_trade_issue_context", "recommendation_market_context", "market_regime_gate", "market_route_audit", "us_route_eligibility"]
ARTIFACTS = {
    "market_context": "/tmp/market_context_latest.json",
    "market_shock_mover_scout": "/tmp/market_shock_mover_scout_latest.json",
    "supply_close_strength_scout": "/tmp/supply_close_strength_scout_latest.json",
    "theme_spillover_backtest": "/tmp/theme_spillover_backtest_latest.json",
    "market_issue_scout": "/tmp/market_issue_scout_latest.json",
    "market_news_issue_scout": "/tmp/market_news_issue_scout_latest.json",
    "market_issue_narrative": "/tmp/market_issue_narrative_latest.json",
    "next_trade_issue_context": "/tmp/next_trade_issue_context_latest.json",
    "recommendation_market_context": "/tmp/recommendation_market_context_latest.json",
    "market_regime_gate": "/tmp/market_regime_gate_latest.json",
    "market_route_audit": "/tmp/market_route_audit_latest.json",
    "us_route_eligibility": "/tmp/us_route_eligibility_latest.json",
}


def _items(packet: dict, *keys: str) -> list:
    for key in keys:
        value = packet.get(key)
        if isinstance(value, list):
            return value
    return []


def main() -> None:
    packets = {agent: read_json(path) for agent, path in ARTIFACTS.items()}
    issue = packets["market_issue_scout"]
    news_issue = packets["market_news_issue_scout"]
    next_issue = packets["next_trade_issue_context"]
    regime = packets["market_regime_gate"]
    route = packets["market_route_audit"]
    us_route = packets["us_route_eligibility"]

    rows = []
    for agent in OWNED:
        packet = packets.get(agent) or {}
        checks = []
        penalty = 0
        if agent in {"market_issue_scout", "market_news_issue_scout"}:
            count = len(_items(packet, "issues", "items"))
            checks.append(f"issue_count={count}")
        if agent == "next_trade_issue_context":
            by_action = next_issue.get("by_action") or {}
            checks.append(f"action_buckets={list(by_action)[:6]}")
            if not by_action:
                penalty += 10
        if agent == "market_regime_gate":
            decisions = (regime.get("summary") or {}).get("decision_counts") or {}
            checks.append(f"regime_decisions={decisions}")
        rows.append(fitness(agent, contract_status(packet), len(warnings(packet)), checks, penalty))

    top_issues = ((issue.get("summary") or {}).get("top_issues") or [])[:5]
    top_news = ((news_issue.get("summary") or {}).get("top_issues") or [])[:5]
    dominant_issue = top_issues[0] if top_issues else (top_news[0] if top_news else None)
    coordination_boundaries = [
        {"cluster": "market_issue_detection", "agents": ["market_issue_scout", "market_news_issue_scout", "market_issue_narrative", "next_trade_issue_context"], "supervisor_view": "Keep price/volume, news, narrative, and recommendation-routing opinions separate but linked by issue id/theme."},
        {"cluster": "market_route_context", "agents": ["market_regime_gate", "market_route_audit", "us_route_eligibility", "recommendation_market_context"], "supervisor_view": "Route quality and regime context should inform research queues, not silently veto fund-consensus recommendations."},
    ]
    missing = []
    if dominant_issue and not next_issue.get("by_action"):
        missing.append({"capability": "issue_to_recommendation_routing", "reason": str(dominant_issue), "severity": "watch"})

    summary = {
        "market_context_summary": packets["market_context"].get("summary"),
        "regime_decisions": (regime.get("summary") or {}).get("decision_counts"),
        "issue_count": len(_items(issue, "issues", "items")),
        "news_issue_count": len(_items(news_issue, "issues", "items")),
        "top_issues": top_issues,
        "top_news_issues": top_news,
        "shock_summary": packets["market_shock_mover_scout"].get("summary"),
        "next_trade_issue_context": {"by_action": next_issue.get("by_action"), "summary": next_issue.get("summary")},
        "route_summary": route.get("summary"),
        "us_route_verdict": us_route.get("verdict"),
    }
    assignments = [
        {"owner_agent": "next_trade_issue_context", "assignment": "route dominant market issues into the next recommendation and validation batches", "priority": "high" if dominant_issue else "normal", "source_bottleneck": dominant_issue, "target_artifact": "/tmp/next_trade_issue_context_latest.json"},
        {"owner_agent": "market_route_audit", "assignment": "keep market-specific route quality visible before strategy promotion decisions", "priority": "normal", "source_bottleneck": "market_route_quality", "target_artifact": "/tmp/market_route_audit_latest.json"},
        {"owner_agent": "recommendation_market_context", "assignment": "provide symbol-level market evidence to recommendation rows without overriding committee/fund ownership", "priority": "normal", "source_bottleneck": "context_visibility", "target_artifact": "/tmp/recommendation_market_context_latest.json"},
    ]
    emit_supervisor(supervisor="market_context_director", title="Market Context Director", domain="market_context_desk", owned_agents=OWNED, role_fitness=rows, summary=summary, coordination_boundaries=coordination_boundaries, missing_capability=missing, bottleneck="dominant market issue needs recommendation routing" if missing else None, bottleneck_severity="watch", next_cycle_assignments=assignments, output="/tmp/market_context_director_latest.json")


if __name__ == "__main__":
    main()
