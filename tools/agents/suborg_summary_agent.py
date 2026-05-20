#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agents.lib.agent_contract import attach_contract
from tools.agents.lib.runtime import now_utc, read_json, write_json_and_static


MANAGEMENT_TREE = {
    "executive_director": {
        "domain": "executive_governance",
        "manages": ["data_steward", "market_context_director", "strategy_director", "fund_director", "recommendation_desk_lead", "governance_director", "suborg_summary"],
    },
    "data_steward": {
        "domain": "data_office",
        "manages": ["pipeline_smoke_check", "universe_discovery", "common_universe", "daily_price_refresh", "data_quality", "opendart_disclosures", "sec_edgar_disclosures", "market_mover_seed", "investor_flow_seed", "external_mover_validation"],
    },
    "market_context_director": {
        "domain": "market_context_desk",
        "manages": ["market_context", "market_shock_mover_scout", "supply_close_strength_scout", "theme_spillover_backtest", "market_issue_scout", "market_news_issue_scout", "market_issue_narrative", "next_trade_issue_context", "recommendation_market_context", "market_regime_gate", "market_route_audit", "us_route_eligibility"],
    },
    "strategy_director": {
        "domain": "strategy_research",
        "manages": ["strategy_generator", "capacity_planner", "simulation_validation_worker", "discovery_validation", "strategy_novelty_pruner", "strategy_lifecycle", "active_strategy_balancer", "strategy_tail_risk_filter", "strategy_success_optimizer", "recommendation_audit", "outcome_attribution", "audit_tail_quarantine_scout", "positive_cohort_scout", "exit_policy_optimizer", "short_horizon_profit_profile", "strategy_context_router", "strategy_context_outcome_ledger", "target_return_adjustment_evaluator"],
    },
    "fund_director": {
        "domain": "fund_research",
        "manages": ["paper_fund_simulator", "paper_fund_historical_replay", "paper_fund_price_replay", "fund_registry", "fund_performance_evaluator", "fund_risk_guardian", "fund_consensus", "fund_recommendation_consensus"],
    },
    "recommendation_desk_lead": {
        "domain": "recommendation_committee",
        "manages": ["recommendation_agent", "recommendation_agent_after_disclosure", "recommendation_critic", "portfolio_risk_manager", "market_regime_gate", "investment_committee", "oversold_recovery", "shadow_recommendations", "internal_signal_board", "alpha_fast_lane", "current_recommendation_validation", "committee_performance_ledger", "recommendation_outcome_tracker", "recommendation_funnel", "recommendation_calibration", "supply_weight_evaluator", "investor_flow_outcome_evaluator", "fund_recommendation_consensus"],
    },
    "governance_director": {
        "domain": "governance_office",
        "manages": ["paper_trader_integrity", "org_evaluator", "org_improvement_guardian", "org_architecture_review", "research_hypothesis", "experiment_spec_compiler", "experiment_planner", "experiment_runner", "evidence_judge", "research_experiment_ledger", "research_org_orchestrator", "experiment_escalation"],
    },
}


def compact_list(items: list[Any], limit: int = 5) -> list[Any]:
    return items[:limit] if isinstance(items, list) else []


def count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        value = row.get(key)
        counts[str(value if value not in (None, "") else "unknown")] += 1
    return dict(counts)


def metric_status(value: Any, *, good: float, warn: float, higher_is_better: bool = True) -> str:
    try:
        number = float(value)
    except Exception:
        return "unknown"
    if higher_is_better:
        if number >= good:
            return "ok"
        if number >= warn:
            return "watch"
        return "needs_attention"
    if number <= good:
        return "ok"
    if number <= warn:
        return "watch"
    return "needs_attention"



def top_symbols(rows: list[dict[str, Any]], limit: int = 12) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
        if len(out) >= limit:
            break
    return out


def build_research_queue(recs: dict[str, Any], funnel: dict[str, Any], audit: dict[str, Any], org_eval: dict[str, Any]) -> dict[str, Any]:
    items = recs.get("items") or []
    bucket_counts = count_by(items, "recommendation_bucket")
    trade_eligible_count = sum(1 for row in items if row.get("trade_eligible"))
    active_count = int(recs.get("active_strategy_count") or 0)
    repair_active = int(recs.get("repair_active_strategy_count") or 0)
    effective_count = int(recs.get("effective_strategy_count") or 0)
    dominant_issue = ((funnel.get("summary") or {}).get("dominant_critic_issue") or {})
    top_issues = ((funnel.get("summary") or {}).get("top_critic_issues") or [])
    best = ((audit.get("summary") or {}).get("best") or {})
    all_rejected = bool(items) and bucket_counts.get("rejected", 0) == len(items)
    no_supported = trade_eligible_count == 0 and active_count == 0 and effective_count == 0
    mode = "research_queue" if all_rejected or no_supported else "candidate_review"
    queue_items: list[dict[str, Any]] = []
    if dominant_issue:
        queue_items.append({
            "type": "dominant_critic_bottleneck",
            "title": dominant_issue.get("issue"),
            "count": dominant_issue.get("count"),
            "priority": "high",
            "next_action": "Route this bottleneck into validation priority and experiment specs before generating more recommendation candidates.",
        })
    for issue in top_issues[:4]:
        title = issue.get("issue")
        if title == dominant_issue.get("issue"):
            continue
        queue_items.append({
            "type": "critic_bottleneck",
            "title": title,
            "count": issue.get("count"),
            "priority": "medium",
            "next_action": "Aggregate as a queue item; do not repeat the same caution on every recommendation row.",
        })
    if best:
        queue_items.append({
            "type": "audit_trust_gap",
            "title": f"{(audit.get('summary') or {}).get('best_logic') or 'best_logic'} quality {best.get('quality_grade')}",
            "priority": "high" if best.get("quality_grade") in ("low", "very_low") else "medium",
            "evidence": {
                "avg_excess_return_pct": best.get("avg_excess_return_pct"),
                "expected_excess_value_pct": best.get("expected_excess_value_pct"),
                "quality_flags": best.get("quality_flags"),
            },
            "next_action": "Keep weak logic in research/watch lanes until positive excess evidence returns.",
        })
    for finding in (org_eval.get("findings") or [])[:3]:
        queue_items.append({
            "type": "governance_finding",
            "title": finding.get("finding"),
            "priority": finding.get("severity") or "watch",
            "next_action": finding.get("recommendation"),
        })
    return {
        "run_at": now_utc(),
        "mode": mode,
        "real_trading": False,
        "summary": {
            "recommendation_count": len(items),
            "trade_eligible_count": trade_eligible_count,
            "bucket_counts": bucket_counts,
            "active_strategy_count": active_count,
            "repair_active_strategy_count": repair_active,
            "effective_strategy_count": effective_count,
            "dominant_critic_issue": dominant_issue,
        },
        "candidate_symbols": top_symbols(items),
        "queue_items": queue_items[:12],
        "next_actions": [
            "Treat current output as research queue, not trade approval." if mode == "research_queue" else "Continue candidate review through committee gates.",
            "Use compact suborg summaries before opening full recommendation or audit artifacts.",
        ],
    }


def main() -> None:
    run_at = now_utc()
    data_quality = read_json("/tmp/data_quality_latest.json")
    price = read_json("/tmp/daily_price_refresh_latest.json")
    disclosures_kr = read_json("/tmp/opendart_disclosures_latest.json")
    disclosures_us = read_json("/tmp/sec_edgar_disclosures_latest.json")
    universe = read_json("/tmp/common_universe_latest.json")
    strategy_lifecycle = read_json("/tmp/strategy_lifecycle_latest.json")
    balancer = read_json("/tmp/active_strategy_balancer_latest.json")
    optimizer = read_json("/tmp/strategy_success_optimizer_latest.json")
    validation = read_json("/tmp/validation_capacity_planner_latest.json")
    audit = read_json("/tmp/recommendation_audit_latest.json")
    market_context = read_json("/tmp/market_context_latest.json")
    regime = read_json("/tmp/market_regime_gate_latest.json")
    issue = read_json("/tmp/market_issue_scout_latest.json")
    news_issue = read_json("/tmp/market_news_issue_scout_latest.json")
    fund_org = read_json("/tmp/fund_org_summary_latest.json")
    fund_registry = read_json("/tmp/fund_registry_latest.json")
    fund_eval = read_json("/tmp/fund_performance_evaluator_latest.json")
    fund_risk = read_json("/tmp/fund_risk_guardian_latest.json")
    fund_consensus = read_json("/tmp/fund_consensus_latest.json")
    recs = read_json("/tmp/recommendations_latest.json")
    funnel = read_json("/tmp/recommendation_funnel_latest.json")
    committee = read_json("/tmp/investment_committee_latest.json")
    calibration = read_json("/tmp/recommendation_calibration_latest.json")
    outcomes = read_json("/tmp/recommendation_outcomes_latest.json")
    org_eval = read_json("/tmp/research_org_evaluation_latest.json")
    guardian = read_json("/tmp/org_improvement_guardian_latest.json")
    architecture = read_json("/tmp/org_architecture_review_latest.json")
    integrity = read_json("/tmp/paper_trader_integrity_latest.json")
    experiment_plan = read_json("/tmp/research_experiment_plan_latest.json")
    experiment_results = read_json("/tmp/research_experiment_results_latest.json")
    experiment_escalation = read_json("/tmp/experiment_escalation_latest.json")
    data_steward = read_json("/tmp/data_steward_latest.json")
    market_context_director = read_json("/tmp/market_context_director_latest.json")
    strategy_director = read_json("/tmp/strategy_director_latest.json")
    fund_director = read_json("/tmp/fund_director_latest.json")
    recommendation_desk_lead = read_json("/tmp/recommendation_desk_lead_latest.json")
    governance_director = read_json("/tmp/governance_director_latest.json")
    queue = build_research_queue(recs, funnel, audit, org_eval)
    attach_contract(
        queue,
        "research_queue",
        status="needs_attention" if queue["mode"] == "research_queue" else "ok",
        outputs={"queue_item_count": len(queue["queue_items"])},
        metrics={
            "recommendation_count": queue["summary"]["recommendation_count"],
            "trade_eligible_count": queue["summary"]["trade_eligible_count"],
            "queue_item_count": len(queue["queue_items"]),
        },
        warnings=["recommendation desk is in research_queue mode"] if queue["mode"] == "research_queue" else [],
        next_actions=queue["next_actions"],
    )

    data_summary = {
        "run_at": run_at,
        "suborg": "data_office",
        "status": data_steward.get("domain_status") or (price.get("contract") or {}).get("status") or (data_quality.get("contract") or {}).get("status") or "unknown",
        "summary": {
            "universe_item_count": universe.get("item_count"),
            "universe_market_counts": universe.get("market_counts"),
            "price_symbol_count": price.get("symbol_count"),
            "price_refreshed_count": price.get("refreshed_count"),
            "price_max_lag_by_market_days": price.get("max_lag_by_market_days"),
            "data_quality_summary": data_quality.get("summary"),
            "kr_disclosure_count": len(disclosures_kr.get("list") or []),
            "us_disclosure_count": len(disclosures_us.get("items") or disclosures_us.get("list") or []),
        },
        "director": data_steward,
        "artifact_refs": {"director": "/tmp/data_steward_latest.json", "full": ["/tmp/data_quality_latest.json", "/tmp/daily_price_refresh_latest.json"]},
    }
    strategy_status = (strategy_lifecycle.get("summary") or {}).get("status_counts") or (strategy_lifecycle.get("status_counts") or {})
    best = ((audit.get("summary") or {}).get("best") or {})
    strategy_summary = {
        "run_at": run_at,
        "suborg": "strategy_lab",
        "status": "needs_attention" if best.get("quality_grade") in ("low", "very_low") else "watch",
        "summary": {
            "status_counts": strategy_status,
            "validation_coverage_pct": (validation.get("summary") or {}).get("coverage_pct") or validation.get("coverage_pct"),
            "balancer_summary": balancer.get("summary"),
            "optimizer_summary": optimizer.get("summary"),
            "audit_best_logic": (audit.get("summary") or {}).get("best_logic"),
            "audit_quality_grade": best.get("quality_grade"),
            "audit_quality_flags": best.get("quality_flags"),
            "audit_avg_excess_return_pct": best.get("avg_excess_return_pct"),
            "audit_expected_excess_value_pct": best.get("expected_excess_value_pct"),
            "director_status": strategy_director.get("domain_status"),
            "director_bottleneck": strategy_director.get("bottleneck"),
        },
        "next_actions": compact_list(((audit.get("contract") or {}).get("next_actions") or []), 5),
        "director": strategy_director,
        "artifact_refs": {"director": "/tmp/strategy_director_latest.json", "compact": "/tmp/audit_status_latest.json", "full_debug": "/tmp/recommendation_audit_full_latest.json"},
    }
    market_summary = {
        "run_at": run_at,
        "suborg": "market_context_desk",
        "status": "ok",
        "summary": {
            "market_context": market_context.get("summary"),
            "regime_decisions": (regime.get("summary") or {}).get("decision_counts"),
            "issue_count": len(issue.get("issues") or []),
            "news_issue_count": len(news_issue.get("issues") or []),
            "top_issues": ((issue.get("summary") or {}).get("top_issues") or [])[:5],
            "top_news_issues": ((news_issue.get("summary") or {}).get("top_issues") or [])[:5],
        },
        "authority": "context_opinion_only",
        "director": market_context_director,
        "artifact_refs": {"director": "/tmp/market_context_director_latest.json", "full": ["/tmp/market_context_latest.json", "/tmp/market_issue_scout_latest.json", "/tmp/next_trade_issue_context_latest.json"]},
    }
    fund_summary = {
        "schema": fund_org.get("schema") or "paper_trader.fund_suborg_summary.v1",
        "run_at": fund_org.get("run_at") or run_at,
        "source": fund_org.get("source") or "suborg_summary_agent",
        "suborg": "fund_research_desk",
        "mode": fund_org.get("mode") or "paper_only_fund_research",
        "real_trading": False,
        "status": fund_org.get("status") or "unknown",
        "authority": fund_org.get("authority") or "recommendation_overlay_only_no_orders",
        "summary": fund_org.get("summary") or {
            "registry": fund_registry.get("summary"),
            "performance": fund_eval.get("summary"),
            "risk": fund_risk.get("summary"),
            "consensus": fund_consensus.get("summary"),
        },
        "director": fund_director,
        "component_effectiveness": fund_org.get("component_effectiveness") or [],
        "next_actions": fund_org.get("next_actions") or [],
        "artifact_refs": fund_org.get("artifact_refs") or {
            "canonical": "/tmp/fund_suborg_summary_latest.json",
            "legacy_alias": "/tmp/fund_org_summary_latest.json",
            "full_debug": ["/tmp/fund_registry_latest.json", "/tmp/fund_performance_evaluator_latest.json", "/tmp/fund_risk_guardian_latest.json", "/tmp/fund_consensus_latest.json"],
        },
    }
    rec_items = recs.get("items") or []
    recommendation_summary = {
        "run_at": run_at,
        "suborg": "recommendation_desk",
        "mode": queue["mode"],
        "status": "research_queue" if queue["mode"] == "research_queue" else "ok",
        "summary": {
            **queue["summary"],
            "committee_summary": committee.get("summary"),
            "funnel_summary": funnel.get("summary"),
            "calibration_summary": calibration.get("summary"),
            "outcome_summary": outcomes.get("summary"),
            "director_status": recommendation_desk_lead.get("domain_status"),
            "director_bottleneck": recommendation_desk_lead.get("bottleneck"),
            "action_counts": count_by(rec_items, "action"),
        },
        "director": recommendation_desk_lead,
        "research_queue_ref": "/tmp/research_queue_latest.json",
        "artifact_refs": {"compact": "/tmp/recommendations_status_latest.json", "full_debug": "/tmp/recommendations_latest.json"},
    }
    governance_summary = {
        "run_at": run_at,
        "suborg": "governance_office",
        "status": "needs_attention" if (org_eval.get("verdict") == "needs_intervention") else "ok",
        "summary": {
            "health_score": org_eval.get("health_score"),
            "verdict": org_eval.get("verdict"),
            "finding_count": len(org_eval.get("findings") or []),
            "guardian_summary": guardian.get("summary"),
            "architecture_summary": architecture.get("summary"),
            "integrity_summary": integrity.get("summary"),
            "experiment_plan_summary": experiment_plan.get("summary"),
            "experiment_results_summary": experiment_results.get("summary"),
            "experiment_escalation_summary": experiment_escalation.get("summary"),
        },
        "next_actions": compact_list(org_eval.get("next_actions") or [], 5) + compact_list(guardian.get("next_actions") or [], 5),
        "director": governance_director,
        "artifact_refs": {"director": "/tmp/governance_director_latest.json", "full": ["/tmp/research_org_evaluation_latest.json", "/tmp/org_architecture_review_latest.json", "/tmp/paper_trader_integrity_latest.json"]},
    }
    suborgs = {
        "data_office": data_summary,
        "strategy_lab": strategy_summary,
        "market_context_desk": market_summary,
        "fund_research_desk": fund_summary,
        "recommendation_desk": recommendation_summary,
        "governance_office": governance_summary,
    }
    packet = {
        "run_at": run_at,
        "mode": "compact_suborg_contracts",
        "real_trading": False,
        "purpose": "Provide token-efficient department-level contracts before any agent or LLM opens full artifacts.",
        "read_order": [
            "/tmp/research_org_suborg_summary_latest.json",
            "/tmp/research_queue_latest.json",
            "/tmp/*_suborg_summary_latest.json",
            "paged APIs / DB summaries",
            "full artifacts only for targeted debugging",
        ],
        "suborgs": {name: {"status": data.get("status"), "mode": data.get("mode"), "summary": data.get("summary"), "director": data.get("director"), "artifact_refs": data.get("artifact_refs")} for name, data in suborgs.items()},
        "management_tree": MANAGEMENT_TREE,
        "domain_supervisors": {
            "data_steward": data_steward,
            "market_context_director": market_context_director,
            "strategy_director": strategy_director,
            "fund_director": fund_director,
            "recommendation_desk_lead": recommendation_desk_lead,
            "governance_director": governance_director,
        },
        "research_queue": queue,
        "artifact_refs": {
            "data": "/tmp/data_suborg_summary_latest.json",
            "strategy": "/tmp/strategy_suborg_summary_latest.json",
            "market": "/tmp/market_suborg_summary_latest.json",
            "fund": "/tmp/fund_suborg_summary_latest.json",
            "recommendation": "/tmp/recommendation_suborg_summary_latest.json",
            "governance": "/tmp/governance_suborg_summary_latest.json",
            "data_steward": "/tmp/data_steward_latest.json",
            "market_context_director": "/tmp/market_context_director_latest.json",
            "strategy_director": "/tmp/strategy_director_latest.json",
            "fund_director": "/tmp/fund_director_latest.json",
            "recommendation_desk_lead": "/tmp/recommendation_desk_lead_latest.json",
            "governance_director": "/tmp/governance_director_latest.json",
        },
    }
    warnings = []
    if queue["mode"] == "research_queue":
        warnings.append("recommendation desk is in research_queue mode")
    if best.get("quality_grade") in ("low", "very_low"):
        warnings.append("strategy audit quality remains low")
    attach_contract(
        packet,
        "suborg_summary",
        status="degraded" if warnings else "ok",
        outputs={"suborg_count": len(suborgs), "research_queue_mode": queue["mode"]},
        metrics={
            "recommendation_count": queue["summary"]["recommendation_count"],
            "trade_eligible_count": queue["summary"]["trade_eligible_count"],
            "audit_quality_grade": best.get("quality_grade"),
        },
        warnings=warnings,
        next_actions=queue["next_actions"] + compact_list(strategy_summary.get("next_actions") or [], 3),
    )
    outputs = {
        "/tmp/data_suborg_summary_latest.json": data_summary,
        "/tmp/strategy_suborg_summary_latest.json": strategy_summary,
        "/tmp/market_suborg_summary_latest.json": market_summary,
        "/tmp/fund_suborg_summary_latest.json": fund_summary,
        "/tmp/recommendation_suborg_summary_latest.json": recommendation_summary,
        "/tmp/governance_suborg_summary_latest.json": governance_summary,
        "/tmp/research_queue_latest.json": queue,
        "/tmp/research_org_suborg_summary_latest.json": packet,
    }
    for path, payload in outputs.items():
        write_json_and_static(path, payload)
    print(json.dumps(packet, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
