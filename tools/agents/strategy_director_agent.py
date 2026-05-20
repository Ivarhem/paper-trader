#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agents.domain_supervisor_lib import contract_status, emit_supervisor, fitness, read_json, warnings
from tools.agents.lib.runtime import write_json_and_static
from app.database import init_db, list_strategy_registry

OWNED = [
    "strategy_generator", "capacity_planner", "simulation_validation_worker", "discovery_validation",
    "strategy_novelty_pruner", "strategy_lifecycle", "active_strategy_balancer", "strategy_tail_risk_filter",
    "strategy_success_optimizer", "recommendation_audit", "outcome_attribution", "audit_tail_quarantine_scout",
    "positive_cohort_scout", "exit_policy_optimizer", "short_horizon_profit_profile", "strategy_context_router",
    "strategy_context_outcome_ledger", "target_return_adjustment_evaluator",
]

ARTIFACTS = {
    "strategy_generator": "/tmp/strategy_candidates_latest.json",
    "capacity_planner": "/tmp/validation_capacity_planner_latest.json",
    "simulation_validation_worker": "/tmp/simulation_validation_latest.json",
    "discovery_validation": "/tmp/discovery_validation_latest.json",
    "strategy_novelty_pruner": "/tmp/strategy_novelty_pruner_latest.json",
    "strategy_lifecycle": "/tmp/strategy_lifecycle_latest.json",
    "active_strategy_balancer": "/tmp/active_strategy_balancer_latest.json",
    "strategy_tail_risk_filter": "/tmp/strategy_tail_risk_filter_latest.json",
    "strategy_success_optimizer": "/tmp/strategy_success_optimizer_latest.json",
    "recommendation_audit": "/tmp/recommendation_audit_latest.json",
    "outcome_attribution": "/tmp/outcome_attribution_latest.json",
    "audit_tail_quarantine_scout": "/tmp/audit_tail_quarantine_scout_latest.json",
    "positive_cohort_scout": "/tmp/positive_cohort_scout_latest.json",
    "exit_policy_optimizer": "/tmp/exit_policy_optimizer_latest.json",
    "short_horizon_profit_profile": "/tmp/short_horizon_profit_profile_latest.json",
    "strategy_context_router": "/tmp/strategy_context_router_latest.json",
    "strategy_context_outcome_ledger": "/tmp/strategy_context_outcome_ledger_latest.json",
    "target_return_adjustment_evaluator": "/tmp/target_return_adjustment_evaluator_latest.json",
}


def num(value: object, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return default


def promotion_score(row: dict) -> float:
    return round(
        num(row.get("avg_excess_return_pct")) * 4
        + num(row.get("recent_avg_excess_return_pct")) * 2
        + num(row.get("success_rate_pct")) / 10
        + min(8.0, num(row.get("samples")) / 200),
        2,
    )


def blocker_tags(row: dict) -> list[str]:
    reason = str(row.get("reason") or "")
    tags: list[str] = []
    if "recent rolling window deteriorated" in reason or num(row.get("recent_avg_excess_return_pct")) < 0:
        tags.append("recent_deterioration")
    if num(row.get("success_rate_pct")) < 35:
        tags.append("low_success_rate")
    if num(row.get("avg_excess_return_pct")) < 1:
        tags.append("weak_avg_excess")
    if int(row.get("samples") or 0) < 500:
        tags.append("sample_gap")
    if "overselective" in reason:
        tags.append("overselective_signal_rate")
    return tags or ["near_threshold_quality"]


def promotion_queue(limit: int = 6) -> list[dict]:
    try:
        init_db()
        strategies = list_strategy_registry()
    except Exception:
        return []
    pool = [row for row in strategies if row.get("status") in {"watch", "probation", "candidate", "validation_active"}]
    ranked = sorted(pool, key=promotion_score, reverse=True)
    queue: list[dict] = []
    for row in ranked[:limit]:
        tags = blocker_tags(row)
        queue.append({
            "logic": row.get("logic"),
            "status": row.get("status"),
            "priority": "high" if len(queue) < 2 else "medium",
            "promotion_score": promotion_score(row),
            "samples": row.get("samples"),
            "success_rate_pct": row.get("success_rate_pct"),
            "avg_excess_return_pct": row.get("avg_excess_return_pct"),
            "recent_avg_excess_return_pct": row.get("recent_avg_excess_return_pct"),
            "blockers": tags,
            "owner_agent": "discovery_validation" if "sample_gap" in tags or "weak_avg_excess" in tags else "exit_policy_optimizer",
            "validation_batch_hint": "promotion_queue_candidate",
            "unblock_condition": "candidate qualifies for lifecycle active or is retired with fresh evidence",
        })
    return queue


def main() -> None:
    packets = {agent: read_json(path) for agent, path in ARTIFACTS.items()}
    optimizer = packets["strategy_success_optimizer"]
    audit = packets["recommendation_audit"]
    lifecycle = packets["strategy_lifecycle"]
    validation = packets["discovery_validation"]
    sim = packets["simulation_validation_worker"]
    candidates = packets["strategy_generator"]
    opt_summary = optimizer.get("summary") or {}
    audit_best = ((audit.get("summary") or {}).get("best") or {})
    status_counts = (lifecycle.get("summary") or {}).get("status_counts") or lifecycle.get("status_counts") or {}
    processed = int(validation.get("processed_combinations") or ((validation.get("worker") or {}).get("processed_combinations") or 0))
    sim_processed = int(sim.get("processed") or sim.get("processed_combinations") or ((sim.get("worker") or {}).get("processed_combinations") or 0))
    rows = []
    for agent in OWNED:
        packet = packets.get(agent) or {}
        penalty = 0
        checks: list[str] = []
        if agent == "strategy_generator":
            generated = len(candidates.get("candidates") or candidates.get("items") or [])
            checks.append(f"generated_candidates={generated}")
            if generated == 0:
                penalty += 10
        if agent in {"simulation_validation_worker", "discovery_validation"}:
            count = sim_processed if agent == "simulation_validation_worker" else processed
            checks.append(f"processed_combinations={count}")
            if count == 0:
                penalty += 20
        if agent == "recommendation_audit":
            quality = audit_best.get("quality_grade") or "unknown"
            checks.append(f"audit_quality={quality}")
            if quality in {"low", "very_low"}:
                penalty += 15
        rows.append(fitness(agent, contract_status(packet), len(warnings(packet)), checks, penalty))
    coordination_boundaries = [
        {
            "cluster": "strategy_state_writers",
            "agents": ["strategy_lifecycle", "active_strategy_balancer", "strategy_tail_risk_filter", "strategy_success_optimizer"],
            "supervisor_view": "Keep strategy_lifecycle canonical; other agents must stay proposal/tier/guardrail writers unless explicitly applied.",
        },
        {
            "cluster": "validation_capacity",
            "agents": ["capacity_planner", "simulation_validation_worker", "discovery_validation", "current_recommendation_validation"],
            "supervisor_view": "Strategy domain owns general backlog; Recommendation domain owns current-card priority validation.",
        },
    ]
    missing = []
    bottleneck = None
    queue = promotion_queue()
    if int(opt_summary.get("high_confidence_historical_active_count") or 0) == 0:
        bottleneck = "no_high_confidence_historical_active_strategy"
        if not queue:
            missing.append({"capability": "replacement_strategy_promotion_queue", "reason": "active pool is repair/watch dominated, but no executable candidate queue could be built", "severity": "action"})
    assignments = [
        {"owner_agent": "discovery_validation", "assignment": "validate replacement promotion queue candidates before more duplicate generation", "priority": "high" if bottleneck else "normal", "source_bottleneck": bottleneck, "target_artifact": "/tmp/discovery_validation_latest.json", "validation_batch_hint": "promotion_queue_candidate", "targets": [x.get("logic") for x in queue if x.get("logic")][:6]},
        {"owner_agent": "strategy_novelty_pruner", "assignment": "reject duplicate candidates before they consume validation slots", "priority": "normal", "source_bottleneck": "duplicate_strategy_candidates", "target_artifact": "/tmp/strategy_novelty_pruner_latest.json", "validation_batch_hint": "pre_validation_duplicate_prune"},
        {"owner_agent": "strategy_lifecycle", "assignment": "keep canonical promotion/demotion authority explicit in reports", "priority": "normal", "source_bottleneck": "strategy_state_writer_overlap", "target_artifact": "/tmp/strategy_lifecycle_latest.json", "validation_batch_hint": "post_validation_promotion_authority"},
    ]
    summary = {
        "active_count": int(status_counts.get("active") or 0),
        "repair_active_count": int(status_counts.get("repair_active") or 0),
        "watch_count": int(status_counts.get("watch") or 0),
        "validation_processed_combinations": processed,
        "audit_quality_grade": audit_best.get("quality_grade"),
        "audit_avg_excess_return_pct": audit_best.get("avg_excess_return_pct"),
        "high_confidence_historical_active_count": int(opt_summary.get("high_confidence_historical_active_count") or 0),
        "promotion_queue_count": len(queue),
    }
    packet = emit_supervisor(supervisor="strategy_director", title="Strategy Director", domain="strategy_research", owned_agents=OWNED, role_fitness=rows, summary=summary, coordination_boundaries=coordination_boundaries, missing_capability=missing, bottleneck=bottleneck, bottleneck_severity="watch", next_cycle_assignments=assignments, output="/tmp/strategy_director_latest.json")
    packet["promotion_queue"] = queue
    write_json_and_static("/tmp/strategy_director_latest.json", packet)
    print(json.dumps(packet, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
