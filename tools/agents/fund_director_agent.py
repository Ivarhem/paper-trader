#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agents.domain_supervisor_lib import contract_status, emit_supervisor, fitness, read_json, warnings

OWNED = ["paper_fund_simulator", "paper_fund_historical_replay", "paper_fund_price_replay", "fund_registry", "fund_performance_evaluator", "fund_risk_guardian", "fund_consensus", "fund_recommendation_consensus"]
ARTIFACTS = {
    "paper_fund_simulator": "/tmp/paper_fund_simulator_latest.json",
    "paper_fund_historical_replay": "/tmp/paper_fund_historical_replay_latest.json",
    "paper_fund_price_replay": "/tmp/paper_fund_price_replay_latest.json",
    "fund_registry": "/tmp/fund_registry_latest.json",
    "fund_performance_evaluator": "/tmp/fund_performance_evaluator_latest.json",
    "fund_risk_guardian": "/tmp/fund_risk_guardian_latest.json",
    "fund_consensus": "/tmp/fund_consensus_latest.json",
    "fund_recommendation_consensus": "/tmp/fund_recommendation_consensus_latest.json",
}


def main() -> None:
    packets = {agent: read_json(path) for agent, path in ARTIFACTS.items()}
    registry = packets["fund_registry"]
    evaluator = packets["fund_performance_evaluator"]
    risk = packets["fund_risk_guardian"]
    consensus = packets["fund_consensus"]
    rec_consensus = packets["fund_recommendation_consensus"]
    rows = []
    for agent in OWNED:
        packet = packets.get(agent) or {}
        checks = []
        penalty = 0
        if agent == "fund_registry":
            count = (registry.get("summary") or {}).get("registered_fund_count") or registry.get("fund_count") or 0
            checks.append(f"registered_funds={count}")
            if int(count or 0) == 0:
                penalty += 25
        if agent == "fund_consensus":
            count = (consensus.get("summary") or {}).get("consensus_symbol_count") or len(consensus.get("items") or [])
            checks.append(f"consensus_symbols={count}")
            if int(count or 0) == 0:
                penalty += 20
        if agent == "fund_recommendation_consensus":
            count = (rec_consensus.get("summary") or {}).get("recommendation_consensus_count") or len(rec_consensus.get("items") or [])
            checks.append(f"recommendation_consensus={count}")
            if int(count or 0) == 0:
                penalty += 20
        rows.append(fitness(agent, contract_status(packet), len(warnings(packet)), checks, penalty))
    risk_findings = (risk.get("summary") or {}).get("risk_finding_count") or len(risk.get("findings") or [])
    summary = {
        "registered_fund_count": (registry.get("summary") or {}).get("registered_fund_count") or registry.get("fund_count"),
        "champion_count": (evaluator.get("summary") or {}).get("champion_count"),
        "candidate_count": (evaluator.get("summary") or {}).get("candidate_count"),
        "risk_finding_count": risk_findings,
        "consensus_symbol_count": (consensus.get("summary") or {}).get("consensus_symbol_count") or len(consensus.get("items") or []),
        "recommendation_consensus_count": (rec_consensus.get("summary") or {}).get("recommendation_consensus_count") or len(rec_consensus.get("items") or []),
    }
    coordination_boundaries = [{"cluster": "fund_replay_engines", "agents": ["paper_fund_historical_replay", "paper_fund_price_replay"], "supervisor_view": "Keep both only while they answer different questions: snapshot replay vs price-derived direct replay."}]
    assignments = [
        {"owner_agent": "fund_risk_guardian", "assignment": "surface risk findings as allocation caps, not standalone trade rejections", "priority": "normal", "source_bottleneck": "fund_risk_visibility", "target_artifact": "/tmp/fund_risk_guardian_latest.json"},
        {"owner_agent": "fund_consensus", "assignment": "keep top-fund consensus as recommendation overlay, with stale/weak fund caveats explicit", "priority": "normal", "source_bottleneck": "fund_consensus_quality", "target_artifact": "/tmp/fund_consensus_latest.json"},
        {"owner_agent": "fund_performance_evaluator", "assignment": "separate champion/candidate funds so weak candidates do not dominate recommendations", "priority": "normal", "source_bottleneck": "fund_role_quality", "target_artifact": "/tmp/fund_performance_evaluator_latest.json"},
    ]
    emit_supervisor(supervisor="fund_director", title="Fund Director", domain="fund_research", owned_agents=OWNED, role_fitness=rows, summary=summary, coordination_boundaries=coordination_boundaries, bottleneck="fund risk/consensus quality must remain visible before recommendations" if risk_findings else None, bottleneck_severity="watch", next_cycle_assignments=assignments, output="/tmp/fund_director_latest.json")


if __name__ == "__main__":
    main()
