#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agents.domain_supervisor_lib import contract_status, emit_supervisor, fitness, read_json, warnings

OWNED = ["recommendation_agent", "recommendation_agent_after_disclosure", "recommendation_critic", "portfolio_risk_manager", "market_regime_gate", "investment_committee", "oversold_recovery", "shadow_recommendations", "internal_signal_board", "alpha_fast_lane", "current_recommendation_validation", "committee_performance_ledger", "recommendation_outcome_tracker", "recommendation_funnel", "recommendation_calibration", "supply_weight_evaluator", "investor_flow_outcome_evaluator", "fund_recommendation_consensus"]
ARTIFACTS = {
    "recommendation_agent": "/tmp/recommendations_latest.json",
    "recommendation_agent_after_disclosure": "/tmp/recommendations_latest.json",
    "recommendation_critic": "/tmp/recommendation_critic_latest.json",
    "portfolio_risk_manager": "/tmp/portfolio_risk_latest.json",
    "market_regime_gate": "/tmp/market_regime_gate_latest.json",
    "investment_committee": "/tmp/investment_committee_latest.json",
    "oversold_recovery": "/tmp/oversold_recovery_latest.json",
    "shadow_recommendations": "/tmp/shadow_recommendations_latest.json",
    "internal_signal_board": "/tmp/internal_signal_board_latest.json",
    "alpha_fast_lane": "/tmp/alpha_fast_lane_latest.json",
    "current_recommendation_validation": "/tmp/current_recommendation_validation_latest.json",
    "committee_performance_ledger": "/tmp/committee_performance_ledger_latest.json",
    "recommendation_outcome_tracker": "/tmp/recommendation_outcomes_latest.json",
    "recommendation_funnel": "/tmp/recommendation_funnel_latest.json",
    "recommendation_calibration": "/tmp/recommendation_calibration_latest.json",
    "supply_weight_evaluator": "/tmp/supply_weight_evaluator_latest.json",
    "investor_flow_outcome_evaluator": "/tmp/investor_flow_outcome_evaluator_latest.json",
    "fund_recommendation_consensus": "/tmp/fund_recommendation_consensus_latest.json",
}


def main() -> None:
    packets = {agent: read_json(path) for agent, path in ARTIFACTS.items()}
    recs = packets["recommendation_agent"]
    committee = packets["investment_committee"]
    validation = packets["current_recommendation_validation"]
    funnel = packets["recommendation_funnel"]
    calibration = packets["recommendation_calibration"]
    items = recs.get("items") or []
    trade_eligible = sum(1 for row in items if row.get("trade_eligible"))
    rows = []
    for agent in OWNED:
        packet = packets.get(agent) or {}
        checks = []
        penalty = 0
        if agent in {"recommendation_agent", "recommendation_agent_after_disclosure"}:
            checks.append(f"recommendations={len(items)}")
            if not items:
                penalty += 30
        if agent == "current_recommendation_validation":
            processed = int(((validation.get("worker") or {}).get("processed_combinations") or validation.get("processed_combinations") or 0))
            checks.append(f"processed_combinations={processed}")
            if processed == 0:
                penalty += 20
        if agent == "investment_committee":
            posture_counts = (committee.get("summary") or {}).get("posture_counts") or {}
            checks.append(f"postures={posture_counts}")
            if not posture_counts:
                penalty += 10
        rows.append(fitness(agent, contract_status(packet), len(warnings(packet)), checks, penalty))
    dominant = ((funnel.get("summary") or {}).get("dominant_critic_issue") or {}).get("issue")
    coordination_boundaries = [
        {"cluster": "negative_gate_stack", "agents": ["recommendation_critic", "portfolio_risk_manager", "market_regime_gate", "investment_committee"], "supervisor_view": "Each must keep separate opinion types: evidence quality, portfolio risk, market regime, final posture."},
        {"cluster": "recommendation_generators", "agents": ["recommendation_agent", "recommendation_agent_after_disclosure", "shadow_recommendations", "alpha_fast_lane"], "supervisor_view": "Base/final recommendation output remains canonical; shadow/fast-lane agents propose comparisons and validation priorities only."},
    ]
    missing = []
    if dominant:
        missing.append({"capability": "critic_bottleneck_to_validation_assignment", "reason": dominant, "severity": "watch"})
    assignments = [
        {"owner_agent": "recommendation_critic", "assignment": "label evidence-quality objections separately from hard risk blocks", "priority": "normal", "source_bottleneck": dominant, "target_artifact": "/tmp/recommendation_critic_latest.json"},
        {"owner_agent": "investment_committee", "assignment": "keep aggressive/research personas searching for support while risk personas own hard blocks", "priority": "normal", "source_bottleneck": dominant, "target_artifact": "/tmp/investment_committee_latest.json"},
        {"owner_agent": "current_recommendation_validation", "assignment": "route dominant critic bottleneck and fund-consensus symbols into next validation batch", "priority": "high" if dominant else "normal", "source_bottleneck": dominant, "target_artifact": "/tmp/current_recommendation_validation_latest.json", "validation_batch_hint": "critic_bottleneck_plus_fund_consensus"},
    ]
    summary = {
        "recommendation_count": len(items),
        "trade_eligible_count": trade_eligible,
        "bucket_counts": {k: sum(1 for row in items if row.get("recommendation_bucket") == k) for k in ("approved", "watch", "rejected")},
        "committee_summary": committee.get("summary"),
        "dominant_critic_issue": dominant,
        "calibration_sample_count": (calibration.get("summary") or {}).get("sample_count") or calibration.get("sample_count"),
    }
    emit_supervisor(supervisor="recommendation_desk_lead", title="Recommendation Desk Lead", domain="recommendation_committee", owned_agents=OWNED, role_fitness=rows, summary=summary, coordination_boundaries=coordination_boundaries, missing_capability=missing, bottleneck=dominant, bottleneck_severity="watch", next_cycle_assignments=assignments, output="/tmp/recommendation_desk_lead_latest.json")


if __name__ == "__main__":
    main()
