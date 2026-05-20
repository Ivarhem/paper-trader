#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import ast
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agents.lib.agent_contract import attach_contract
from tools.agents.lib.runtime import now_utc, read_json, write_json_and_static

_ROLE_SUMMARY_CACHE: dict[str, tuple[str, str]] | None = None

_AGENT_GUARDRAIL_OVERRIDES: dict[str, str] = {
    "pipeline_smoke_check": "Runs compile/contract/readiness checks only; it must not create recommendations, mutate strategy state, or mask required-agent failures.",
    "universe_discovery": "Adds research candidates only; discovered symbols need data quality, liquidity, and validation gates before recommendation use.",
    "common_universe": "Maintains the canonical symbol universe; it should dedupe and route symbols without promoting trades or bypassing validation.",
    "daily_price_refresh": "Refreshes historical daily bars for paper research; stale, adjusted, or missing data must block downstream confidence rather than be guessed.",
    "data_quality": "Can downgrade or quarantine bad inputs, but cannot rescue a recommendation by overwriting weak evidence.",
    "opendart_disclosures": "Supplies KR disclosure risk context; filings are conservative evidence and never an automatic buy or sell trigger.",
    "sec_edgar_disclosures": "Supplies US filing risk context; title/type classification must stay secondary to validated price and committee evidence.",
    "market_mover_seed": "Creates provisional mover seeds; spike candidates require backfill and paper validation before entering recommendation lanes.",
    "investor_flow_seed": "Collects foreign/institutional flow seeds; flow strength is a research input, not direct recommendation authority.",
    "external_mover_validation": "Validates external mover candidates with historical/paper tests only; promotions remain lifecycle/audit gated.",
    "strategy_generator": "Registers candidate logic only; new strategies start untrusted until independent validation, novelty, and lifecycle gates pass.",
    "capacity_planner": "Adjusts validation throughput within resource limits; it must prefer bounded backlog progress over server load spikes.",
    "simulation_validation_worker": "Runs cutoff-based historical validation only; no future data leakage, live orders, or direct lifecycle promotion.",
    "discovery_validation": "Backfills evidence for low-sample discoveries; it may reduce uncertainty but cannot skip lifecycle thresholds.",
    "strategy_novelty_pruner": "Deprioritizes duplicate or overfit candidates conservatively; it should not delete proven active strategies.",
    "strategy_lifecycle": "Owns canonical strategy status changes; promotions require sample, excess-return, recency, and repair-active guard evidence.",
    "active_strategy_balancer": "May restore a thin active pool only from near-qualified strategies; it must not force weak strategies into active use.",
    "strategy_tail_risk_filter": "Flags left-tail strategy risk; downgrade pressure should be explicit and reversible through later evidence.",
    "strategy_success_optimizer": "Narrows usable strategy scope from observed outcomes; it must not optimism-adjust failed logic into recommendation use.",
    "recommendation_audit": "Audits recommendations at historical cutoffs only; audit previews are trust evidence, not direct trade instructions.",
    "outcome_attribution": "Explains observed outcomes as hypotheses; attribution should feed new tests, not rewrite policy by itself.",
    "audit_tail_quarantine_scout": "Finds weak tail cohorts for quarantine review; quarantine proposals need supervisor/lifecycle confirmation.",
    "positive_cohort_scout": "Finds positive-edge cohorts for follow-up; cohort findings stay proposal-only until validation confirms persistence.",
    "exit_policy_optimizer": "Proposes target/stop/holding-period experiments; exit policy changes require bounded tests before adoption.",
    "short_horizon_profit_profile": "Compares 1D-5D target behavior; short-horizon tuning must not overfit sparse recent wins.",
    "strategy_context_router": "Routes strategy family preferences by context; routing is advisory unless validated outcomes support the context split.",
    "strategy_context_outcome_ledger": "Records context-outcome feedback; ledger rows are evidence, not automatic parameter changes.",
    "target_return_adjustment_evaluator": "Tests target-return adjustment arms; adjustment proposals remain paper-only until sample and excess-return gates pass.",
    "market_context": "Summarizes market backdrop; context can caution recommendations but must not override committee/risk gates alone.",
    "market_shock_mover_scout": "Turns after-market shocks into research hypotheses; shock narratives require validation before recommendation use.",
    "supply_close_strength_scout": "Detects volume/close-strength candidates; supply signals stay provisional until benchmark-relative outcomes are checked.",
    "theme_spillover_backtest": "Backtests theme propagation with historical cutoffs; theme evidence cannot introduce look-ahead assumptions.",
    "market_issue_scout": "Detects price/volume issues; detected issues are context and queue input, not standalone strategy approval.",
    "market_news_issue_scout": "Detects news-led market themes; unverified narratives should be downgraded when price/validation evidence disagrees.",
    "market_issue_narrative": "Adds explanation to detected issues; narrative clarity must not raise confidence without matching evidence.",
    "next_trade_issue_context": "Prepares context for the next recommendation/validation cycle; it cannot pre-approve symbols.",
    "recommendation_market_context": "Adds index, volume, and disclosure context to candidates; recommendation gates remain canonical.",
    "market_regime_gate": "Applies regime caution; missing or ambiguous regime data should become watch status, not fabricated certainty.",
    "market_route_audit": "Audits KR/US route quality separately; route suggestions need validation before changing production routing.",
    "us_route_eligibility": "Determines US paper-watch eligibility; eligibility is a filter, not a buy signal or broker action.",
    "paper_fund_simulator": "Runs paper-only fund evolution; simulated fund choices cannot create real orders or bypass recommendation review.",
    "paper_fund_historical_replay": "Replays historical recommendation snapshots for fund scoring; replay results are backtest evidence with cutoff discipline.",
    "paper_fund_price_replay": "Replays fund behavior from historical prices; price-only replay must be labeled as proxy evidence.",
    "fund_registry": "Registers and organizes paper funds; registry metadata should not imply allocation approval.",
    "fund_performance_evaluator": "Grades paper fund performance; tiers influence overlay weight, not standalone trades.",
    "fund_risk_guardian": "Caps or flags risky paper funds; risk findings limit consensus weight rather than becoming direct sell signals.",
    "fund_consensus": "Builds top-fund overlay consensus; consensus is additive evidence and cannot replace committee/risk review.",
    "fund_recommendation_consensus": "Promotes yesterday top-fund agreement as primary view input; fund consensus still remains paper-only recommendation evidence.",
    "recommendation_agent": "Generates current paper recommendations; outputs require disclosure, critic, risk, regime, and committee review.",
    "recommendation_agent_after_disclosure": "Recomputes candidates after disclosure context; disclosure-adjusted output must keep audit and committee gates visible.",
    "recommendation_critic": "Surfaces opposition and uncertainty; critic findings balance the decision and should not be hidden by high scores.",
    "portfolio_risk_manager": "Annotates concentration and exposure risk; it is paper research risk context, not live position management.",
    "investment_committee": "Synthesizes investor-style opinions; committee support is paper decision evidence, not order authorization.",
    "oversold_recovery": "Finds auxiliary oversold-recovery signals; auxiliary signals cannot outrank validated recommendation evidence.",
    "shadow_recommendations": "Produces comparison candidates; shadow ideas stay non-canonical until promoted through normal recommendation flow.",
    "internal_signal_board": "Aggregates internal signals for visibility; board summaries do not directly mutate recommendation state.",
    "alpha_fast_lane": "Prioritizes promising candidates for validation; fast lane changes order of testing, not evidence thresholds.",
    "current_recommendation_validation": "Validates visible candidates first; it complements broad backlog validation and must report sample limits.",
    "committee_performance_ledger": "Records committee decisions and outcomes; ledger evidence informs calibration but does not auto-change decisions.",
    "recommendation_outcome_tracker": "Tracks forward returns only after horizons mature; pending rows must not be counted as wins.",
    "recommendation_funnel": "Measures stage drop-off and bottlenecks; funnel metrics are diagnostics, not recommendation edits.",
    "recommendation_calibration": "Compares scores and outcomes; sparse samples should produce watch warnings instead of threshold rewrites.",
    "supply_weight_evaluator": "Evaluates supply/flow weighting proposals; weight changes remain proposal-only until enough outcomes accumulate.",
    "investor_flow_outcome_evaluator": "Evaluates investor-flow seed outcomes benchmark-relative; flow boosts need persistence across samples.",
    "paper_trader_integrity": "Checks paper-only safety and UI visibility; integrity findings should fail loudly rather than silently normalize risk.",
    "org_evaluator": "Evaluates organization coverage and duplication; findings are governance proposals, not trade or policy execution.",
    "org_improvement_guardian": "Auto-applies only low-risk reversible maintenance; strategy thresholds, agent topology, and external service changes require review.",
    "org_architecture_review": "Reviews role boundaries and consolidation candidates; architecture findings need explicit implementation ownership.",
    "research_hypothesis": "Creates bottleneck-driven hypotheses; hypotheses must become bounded experiments before any pipeline behavior changes.",
    "experiment_spec_compiler": "Normalizes queue/audit/scout inputs into experiment specs; specs remain paper-only and bounded.",
    "experiment_planner": "Converts hypotheses into diagnostic plans; plans should define evidence gates before execution.",
    "experiment_runner": "Runs bounded experiments; results should be recorded and judged before adoption.",
    "evidence_judge": "Classifies experiment evidence; weak or sparse evidence should hold, not promote.",
    "research_experiment_ledger": "Tracks experiment history and deltas; ledger facts prevent repetition but do not apply changes alone.",
    "research_org_orchestrator": "Routes improvement agenda to director-owned queues; it must not directly own strategy generation, validation, lifecycle, or recommendation decisions.",
    "experiment_escalation": "Escalates repeated low-impact experiments; escalation proposes bolder paper tests, not production policy changes.",
}


def agent_role_catalog() -> dict[str, tuple[str, str]]:
    """Read the pipeline role map without importing the orchestrator."""
    global _ROLE_SUMMARY_CACHE
    if _ROLE_SUMMARY_CACHE is not None:
        return _ROLE_SUMMARY_CACHE
    catalog: dict[str, tuple[str, str]] = {}
    source = ROOT / "tools" / "agents" / "research_pipeline_agent.py"
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except Exception:
        _ROLE_SUMMARY_CACHE = catalog
        return catalog
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(target, "id", "") == "AGENT_ROLE_SUMMARY" for target in node.targets):
            continue
        try:
            raw = ast.literal_eval(node.value)
        except Exception:
            raw = {}
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, tuple) and len(value) >= 2:
                catalog[key] = (str(value[0]), str(value[1]))
        break
    _ROLE_SUMMARY_CACHE = catalog
    return catalog


def contract_status(packet: dict[str, Any]) -> str:
    if packet.get("_read_error"):
        return "missing_artifact"
    contract = packet.get("contract") or {}
    explicit = packet.get("status") or contract.get("status")
    if explicit:
        return str(explicit)
    return "ok" if packet else "unknown"


def warnings(packet: dict[str, Any]) -> list[str]:
    contract = packet.get("contract") or {}
    out = packet.get("warnings") or contract.get("warnings") or []
    return out if isinstance(out, list) else [str(out)]


def grade(score: int) -> str:
    if score >= 85:
        return "ok"
    if score >= 70:
        return "watch"
    return "degraded"


def fitness(
    agent: str,
    status: str,
    warning_count: int,
    checks: list[str] | None = None,
    penalty: int = 0,
) -> dict[str, Any]:
    score = 100
    if status in {"failed", "failed_required", "failed_optional", "error"}:
        score -= 45
    elif status in {"missing_artifact", "unknown"}:
        score -= 30
    elif status not in {"ok", "info", "not_run"}:
        score -= 15
    score -= min(25, warning_count * 5)
    score -= penalty
    score = max(0, min(100, score))
    return {"agent": agent, "status": status, "score": score, "grade": grade(score), "checks": checks or []}


def domain_status(
    rows: list[dict[str, Any]],
    action_required: bool = False,
    duplicate_count: int = 0,
    missing_count: int = 0,
    bottleneck_severity: str = "action",
    missing_severity: str = "action",
) -> str:
    if action_required or bottleneck_severity == "action" or (missing_count and missing_severity == "action"):
        return "action_required"
    grades = Counter(row.get("grade") for row in rows)
    if grades.get("degraded"):
        return "degraded"
    if grades.get("watch") or duplicate_count or bottleneck_severity == "watch" or (missing_count and missing_severity == "watch"):
        return "watch"
    return "ok"


def normalize_assignments(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for item in assignments:
        row = dict(item)
        row.setdefault("priority", "normal")
        row.setdefault("source_bottleneck", None)
        row.setdefault("target_artifact", None)
        row.setdefault("validation_batch_hint", None)
        normalized.append(row)
    return normalized


def summarize_role(supervisor: str, title: str, domain: str, owned_agents: list[str]) -> str:
    catalog_summary = role_summary_for_agent(supervisor)
    if "역할 요약 미정의" not in catalog_summary:
        return catalog_summary
    agent_count = len(owned_agents)
    return f"{title} supervises the {domain} domain and manages {agent_count} child agent{'s' if agent_count != 1 else ''}."


def display_name_for_agent(agent: str) -> str:
    catalog = agent_role_catalog()
    if agent in catalog:
        return catalog[agent][0]
    return str(agent or "").replace("_", " ").replace("-", " ").title()


def role_summary_for_agent(agent: str) -> str:
    catalog = agent_role_catalog()
    if agent in catalog:
        return catalog[agent][1]
    return f"{display_name_for_agent(agent)} 역할 요약 미정의"


def description_for_agent(agent: str) -> str:
    summary = role_summary_for_agent(agent)
    guardrail = guardrail_for_agent(agent)
    if "역할 요약 미정의" in summary:
        return "Pipeline artifact와 output contract를 기준으로 실행 상태를 관찰하고, 명시된 권한 밖의 정책/추천 변경은 만들지 않습니다."
    return f"{summary}. {guardrail}"


def guardrail_for_agent(agent: str) -> str:
    key = str(agent or "")
    if key in _AGENT_GUARDRAIL_OVERRIDES:
        return _AGENT_GUARDRAIL_OVERRIDES[key]
    if "recommendation" in key:
        return "paper recommendation evidence only; committee/risk/critic gates remain separate and no real orders are allowed."
    if "fund" in key:
        return "paper fund consensus is an overlay signal, not a standalone buy/order authority."
    if "strategy" in key or "validation" in key or "simulation" in key:
        return "historical/paper validation only; strategy promotion must remain gated by lifecycle and audit evidence."
    if "data" in key or "universe" in key or "price" in key or "disclosure" in key or "seed" in key:
        return "data/context producer only; stale or low-quality inputs should block or downgrade downstream use."
    if "market" in key or "issue" in key or "regime" in key or "theme" in key:
        return "market context is supporting evidence; it should not override validated recommendation and risk gates."
    if "org" in key or "governance" in key or "guardian" in key or "experiment" in key:
        return "organization/governance output is proposal or audit authority only; risky structure/policy changes require explicit review."
    return "proposal/context/validation output only; no direct recommendation approval, policy mutation, or real trading authority."


def managed_agent_details(owned_agents: list[str]) -> list[dict[str, Any]]:
    rows = []
    for agent in owned_agents:
        display_name = display_name_for_agent(agent)
        rows.append({
            "agent_name": agent,
            "display_name": display_name,
            "role_summary": role_summary_for_agent(agent),
            "description": description_for_agent(agent),
            "guardrail": guardrail_for_agent(agent),
        })
    return rows


def emit_supervisor(
    *,
    supervisor: str,
    title: str,
    domain: str,
    owned_agents: list[str],
    role_fitness: list[dict[str, Any]],
    summary: dict[str, Any],
    duplicate_work: list[dict[str, Any]] | None = None,
    coordination_boundaries: list[dict[str, Any]] | None = None,
    missing_capability: list[dict[str, Any]] | None = None,
    bottleneck: str | None = None,
    bottleneck_severity: str = "action",
    next_cycle_assignments: list[dict[str, Any]] | None = None,
    authority_boundary: str = "manager_evaluation_only_no_direct_trade_or_status_mutation",
    output: str = "/tmp/domain_supervisor_latest.json",
) -> dict[str, Any]:
    duplicate_work = duplicate_work or []
    coordination_boundaries = coordination_boundaries or []
    missing_capability = missing_capability or []
    next_cycle_assignments = normalize_assignments(next_cycle_assignments or [])
    effective_bottleneck_severity = bottleneck_severity if bottleneck else "none"
    missing_severities = {str(x.get("severity") or "action") for x in missing_capability if isinstance(x, dict)}
    effective_missing_severity = "action" if not missing_severities or "action" in missing_severities else "watch"
    status = domain_status(
        role_fitness,
        duplicate_count=len(duplicate_work),
        missing_count=len(missing_capability),
        bottleneck_severity=effective_bottleneck_severity,
        missing_severity=effective_missing_severity,
    )
    warning_list = []
    if duplicate_work:
        warning_list.append("duplicate or overlapping work needs supervisor review")
    if missing_capability:
        warning_list.append("missing capability detected")
    role_summary = summarize_role(supervisor, title, domain, owned_agents)
    managed_details = managed_agent_details(owned_agents)
    payload = {
        "schema": "paper_trader.domain_supervisor.v1",
        "run_at": now_utc(),
        "supervisor": supervisor,
        "title": title,
        "agent_name": supervisor,
        "display_name": title,
        "role": "domain_supervisor",
        "role_summary": role_summary,
        "description": description_for_agent(supervisor),
        "domain": domain,
        "domain_status": status,
        "owned_agents": owned_agents,
        "managed_agents": owned_agents,
        "managed_agent_details": managed_details,
        "managed_agent_count": len(owned_agents),
        "role_fitness": role_fitness,
        "duplicate_work": duplicate_work,
        "coordination_boundaries": coordination_boundaries,
        "missing_capability": missing_capability,
        "missing_capability_severity": effective_missing_severity if missing_capability else "none",
        "bottleneck": bottleneck,
        "bottleneck_severity": effective_bottleneck_severity,
        "next_cycle_assignments": next_cycle_assignments,
        "authority_boundary": authority_boundary,
        "summary": summary,
    }
    attach_contract(
        payload,
        supervisor,
        status="ok" if status in {"ok", "watch"} else "degraded",
        outputs={"owned_agent_count": len(owned_agents), "assignment_count": len(next_cycle_assignments)},
        metrics={"role_fitness_avg": round(sum(x.get("score", 0) for x in role_fitness) / max(1, len(role_fitness)), 2), **summary},
        warnings=warning_list,
        next_actions=[x.get("assignment") for x in next_cycle_assignments if x.get("assignment")][:5],
    )
    write_json_and_static(output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload
