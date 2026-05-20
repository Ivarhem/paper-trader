#!/usr/bin/env python3
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agents.lib.agent_contract import attach_contract
from tools.agents.lib.runtime import now_utc, read_json, write_json_and_static
from tools.agents.domain_supervisor_lib import description_for_agent, role_summary_for_agent

DIRECTORS = {
    "data_steward": "/tmp/data_steward_latest.json",
    "market_context_director": "/tmp/market_context_director_latest.json",
    "strategy_director": "/tmp/strategy_director_latest.json",
    "fund_director": "/tmp/fund_director_latest.json",
    "recommendation_desk_lead": "/tmp/recommendation_desk_lead_latest.json",
    "governance_director": "/tmp/governance_director_latest.json",
}
DIRECTOR_TITLES = {
    "data_steward": "Data Steward",
    "market_context_director": "Market Context Director",
    "strategy_director": "Strategy Director",
    "fund_director": "Fund Director",
    "recommendation_desk_lead": "Recommendation Desk Lead",
    "governance_director": "Governance Director",
}
SUBORGS = {
    "data_office": "/tmp/data_suborg_summary_latest.json",
    "strategy_lab": "/tmp/strategy_suborg_summary_latest.json",
    "market_context_desk": "/tmp/market_suborg_summary_latest.json",
    "fund_research_desk": "/tmp/fund_suborg_summary_latest.json",
    "recommendation_desk": "/tmp/recommendation_suborg_summary_latest.json",
    "governance": "/tmp/governance_suborg_summary_latest.json",
}


def status_of(packet: dict[str, Any]) -> str:
    return str(packet.get("domain_status") or packet.get("status") or (packet.get("contract") or {}).get("status") or "unknown")


def severity(status: str) -> int:
    return {"failed": 4, "action_required": 3, "degraded": 2, "needs_attention": 2, "watch": 1, "ok": 0, "info": 0}.get(status, 1)


def role_summary_for(name: str, packet: dict[str, Any]) -> str:
    summary = str(packet.get("role_summary") or role_summary_for_agent(name))
    if "역할 요약 미정의" not in summary:
        return summary
    title = packet.get("title") or DIRECTOR_TITLES.get(name, name)
    domain = packet.get("domain") or "unknown_domain"
    managed_agents = packet.get("managed_agents") or packet.get("owned_agents") or []
    agent_count = len(managed_agents) if isinstance(managed_agents, list) else 0
    return f"{title} supervises the {domain} domain and manages {agent_count} child agents."


def main() -> None:
    directors = {name: read_json(path) for name, path in DIRECTORS.items()}
    suborgs = {name: read_json(path) for name, path in SUBORGS.items()}
    queue = read_json("/tmp/research_queue_latest.json")
    org_eval = read_json("/tmp/research_org_evaluation_latest.json")
    architecture = read_json("/tmp/org_architecture_review_latest.json")

    director_rows = []
    for name, packet in directors.items():
        contract = packet.get("contract") or {}
        director_rows.append({
            "director": name,
            "agent_name": packet.get("agent_name") or packet.get("supervisor") or name,
            "title": packet.get("title") or DIRECTOR_TITLES.get(name, name),
            "display_name": packet.get("display_name") or packet.get("title") or DIRECTOR_TITLES.get(name, name),
            "role": packet.get("role") or "domain_supervisor",
            "role_summary": role_summary_for(name, packet),
            "description": packet.get("description") or description_for_agent(name),
            "domain": packet.get("domain"),
            "status": status_of(packet),
            "managed_agents": packet.get("managed_agents") or packet.get("owned_agents") or [],
            "managed_agent_details": packet.get("managed_agent_details") or [],
            "managed_agent_count": len(packet.get("managed_agents") or packet.get("owned_agents") or []),
            "role_fitness_avg": (contract.get("metrics") or {}).get("role_fitness_avg"),
            "bottleneck": packet.get("bottleneck"),
            "bottleneck_severity": packet.get("bottleneck_severity"),
            "duplicate_work_count": len(packet.get("duplicate_work") or []),
            "missing_capability_count": len(packet.get("missing_capability") or []),
            "assignment_count": len(packet.get("next_cycle_assignments") or []),
        })
    suborg_rows = [{"suborg": name, "status": status_of(packet), "summary": packet.get("summary")} for name, packet in suborgs.items()]
    counts = Counter(row["status"] for row in director_rows + suborg_rows)
    max_severity = max([severity(row["status"]) for row in director_rows + suborg_rows] or [0])
    if max_severity >= 3:
        org_status = "action_required"
    elif max_severity == 2:
        org_status = "degraded"
    elif max_severity == 1:
        org_status = "watch"
    else:
        org_status = "ok"

    escalations = []
    for row in director_rows:
        if row["bottleneck"] or row["missing_capability_count"]:
            next_action = "track as supervisor watch item"
            if row.get("bottleneck_severity") == "action" or row["missing_capability_count"]:
                next_action = "route this through next-cycle assignments before creating new peer agents"
            escalations.append({
                "owner": row["director"],
                "reason": row["bottleneck"] or "missing capability",
                "severity": row.get("bottleneck_severity") or "watch",
                "next_action": next_action,
            })
    for finding in (org_eval.get("findings") or [])[:4]:
        escalations.append({"owner": "org_evaluator", "reason": finding.get("finding"), "next_action": finding.get("recommendation")})

    packet = {
        "schema": "paper_trader.executive_director.v1",
        "run_at": now_utc(),
        "supervisor": "executive_director",
        "title": "Executive Organization Director",
        "org_status": org_status,
        "authority_boundary": "organization_supervision_only_no_trade_or_policy_mutation",
        "managed_directors": director_rows,
        "managed_suborgs": suborg_rows,
        "status_counts": dict(counts),
        "research_queue_mode": queue.get("mode"),
        "research_queue_item_count": len(queue.get("queue_items") or []),
        "architecture_summary": architecture.get("summary"),
        "escalations": escalations[:8],
        "next_cycle_priorities": [
            "Resolve action-severity director bottlenecks before adding new strategy generators.",
            "Keep compact suborg summaries as first-read context for UI and agents.",
            "Keep data/market/governance directors connected to explicit repair or owner queues before adding more peer agents.",
        ],
    }
    attach_contract(
        packet,
        "executive_director",
        status="ok" if org_status in {"ok", "watch"} else "degraded",
        outputs={"managed_director_count": len(director_rows), "managed_suborg_count": len(suborg_rows), "escalation_count": len(packet["escalations"])},
        metrics={"director_action_required_count": counts.get("action_required", 0), "research_queue_item_count": packet["research_queue_item_count"]},
        warnings=[f"organization status {org_status}"] if org_status not in {"ok", "watch"} else [],
        next_actions=packet["next_cycle_priorities"],
    )
    write_json_and_static("/tmp/executive_director_latest.json", packet)
    import json
    print(json.dumps(packet, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
