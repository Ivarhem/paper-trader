#!/usr/bin/env python3
from __future__ import annotations
"""Meta architecture review for the paper_trader research organization.

Historical/paper research only. This agent does not change strategy status, does
not place orders, and does not weaken gates. It reviews whether the agent
organization itself still fits the system objective: produce better paper
recommendations through reproducible evidence loops.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.agents.lib.agent_contract import attach_contract

OUT = Path("/tmp/org_architecture_review_latest.json")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(path)}


def load_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(path)}


def scheduled_agents() -> list[str]:
    text = (ROOT / "tools/agents/research_pipeline_agent.py").read_text(encoding="utf-8")
    return re.findall(r"add\('([^']+)'", text)


def agent_groups() -> dict:
    cfg = load_yaml(ROOT / "configs/research_agents.yaml")
    return ((cfg.get("agent_groups") or {}) if isinstance(cfg, dict) else {})


def group_index(groups: dict) -> dict[str, str]:
    out = {}
    for group, spec in groups.items():
        for agent in spec.get("agents") or []:
            out[agent] = group
    return out


def step_output_age(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return "missing"
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
    except Exception:
        return "unknown"


def add_review(items: list[dict], kind: str, severity: str, title: str, recommendation: str, evidence: dict | None = None) -> None:
    items.append({
        "kind": kind,
        "severity": severity,
        "title": title,
        "recommendation": recommendation,
        "evidence": evidence or {},
    })


def main() -> None:
    pipeline = load_json("/tmp/research_pipeline_latest.json")
    guardian = load_json("/tmp/org_improvement_guardian_latest.json")
    orchestrator = load_json("/tmp/research_org_orchestrator_latest.json")
    funnel = load_json("/tmp/recommendation_funnel_latest.json")
    validation = load_json("/tmp/current_recommendation_validation_latest.json")
    optimizer = load_json("/tmp/strategy_success_optimizer_latest.json")
    audit = load_json("/tmp/recommendation_audit_latest.json")
    recs = load_json("/tmp/recommendations_latest.json")

    scheduled = scheduled_agents()
    groups = agent_groups()
    by_group = group_index(groups)
    reviews: list[dict] = []

    unclassified = [a for a in scheduled if a not in by_group]
    if unclassified:
        add_review(
            reviews,
            "integrate",
            "watch",
            "Scheduled agents are missing from organization taxonomy",
            "Add these agents to configs/research_agents.yaml so UI/meta-governance can reason about owner, mission, and consolidation candidates.",
            {"agents": unclassified[:40], "count": len(unclassified)},
        )

    priority_meta = validation.get("priority_meta") or {}
    policy = str(validation.get("selection_policy") or priority_meta.get("selection_policy") or "")
    sample_expansion = validation.get("sample_expansion_context") or priority_meta.get("sample_expansion_context") or {}
    routed_symbols = priority_meta.get("under_sampled_recommendations") or []
    worker = validation.get("worker") or {}
    contract = validation.get("contract") or {}
    dominant = ((funnel.get("summary") or {}).get("dominant_critic_issue") or {}).get("issue")
    routed = (
        bool(dominant)
        and (
            "committee_bottleneck_feedback_loop_active" in policy
            or sample_expansion.get("sample_expansion_mode")
            or bool(routed_symbols)
        )
        and (contract.get("status") in (None, "ok"))
        and int(worker.get("processed_combinations") or 0) > 0
    )
    if routed:
        add_review(
            reviews,
            "improve",
            "info",
            "Critic/committee bottlenecks are integrated into validation capacity",
            "Keep observing sample accumulation before adding more strategy-generation capacity.",
            {
                "dominant_critic_issue": dominant,
                "selection_policy": policy,
                "sample_expansion_context": sample_expansion,
                "routed_symbol_count": len(routed_symbols),
                "processed_combinations": worker.get("processed_combinations"),
                "saved": worker.get("saved"),
            },
        )
    elif dominant:
        add_review(
            reviews,
            "improve",
            "action",
            "Dominant recommendation bottleneck is not fully routed into validation",
            "Route dominant critic/committee issue into current_recommendation_validation priority before generating more candidates.",
            {"dominant_critic_issue": dominant, "selection_policy": policy, "sample_expansion_context": sample_expansion, "routed_symbol_count": len(routed_symbols)},
        )

    opt_summary = optimizer.get("summary") or {}
    repair_only = int(opt_summary.get("repair_only_active_count") or 0)
    high_conf = int(opt_summary.get("high_confidence_historical_active_count") or 0)
    trade_eligible_active = int(opt_summary.get("trade_eligible_active_count") or 0)
    rec_count = len(recs.get("items") or [])
    trade_eligible_recs = int((pipeline.get("recommendations_summary") or {}).get("trade_eligible_count") or 0)
    if repair_only and high_conf == 0 and trade_eligible_active == 0:
        add_review(
            reviews,
            "improve",
            "watch",
            "Organization is in repair-only recommendation mode",
            "Do not remove strict gates. Prioritize replacement/demotion experiments for weak repair-active families and promote only after positive excess evidence returns.",
            {"repair_only_active_count": repair_only, "high_confidence_historical_active_count": high_conf, "trade_eligible_active_count": trade_eligible_active, "recommendation_count": rec_count, "trade_eligible_recommendations": trade_eligible_recs},
        )

    audit_flags = (((audit.get("summary") or {}).get("best") or {}).get("quality_flags") or [])
    best_avg = (((audit.get("summary") or {}).get("best") or {}).get("avg_excess_return_pct"))
    if "no_positive_average_excess" in audit_flags or (isinstance(best_avg, (int, float)) and best_avg < 0):
        add_review(
            reviews,
            "remove_or_demote",
            "watch",
            "Best audited logic still has negative/weak excess return",
            "Keep weak logic in research/watch lanes; consider retiring duplicate weak families only after replacement candidates have enough validation samples.",
            {"best_logic": (audit.get("summary") or {}).get("best_logic"), "best_avg_excess_return_pct": best_avg, "quality_flags": audit_flags},
        )

    fund_agents = [a for a in scheduled if a.startswith("fund_") or a.startswith("paper_fund_")]
    if len(fund_agents) >= 6:
        add_review(
            reviews,
            "integrate",
            "info",
            "Fund agents form a separate sub-organization",
            "Keep fund execution modular, but expose a single fund_suborg summary contract for Organization/Engine UI instead of making each UI view infer from many artifacts.",
            {"fund_agents": fund_agents, "count": len(fund_agents)},
        )

    order = {name: i for i, name in enumerate(scheduled)}
    order_ok = order.get("org_evaluator", -1) < order.get("org_improvement_guardian", 999) < order.get("research_org_orchestrator", 999)
    if order_ok:
        add_review(
            reviews,
            "keep",
            "info",
            "Meta-governance order is coherent",
            "Evaluator → Guardian → Hypothesis/Experiment → Orchestrator order should stay; orchestrator should summarize/assign rather than duplicate workers.",
            {"order": {k: order.get(k) for k in ["org_evaluator", "org_improvement_guardian", "research_hypothesis", "experiment_runner", "research_org_orchestrator"]}},
        )
    else:
        add_review(
            reviews,
            "improve",
            "action",
            "Meta-governance order is inconsistent",
            "Run evaluator before guardian, experiment loop before orchestrator, and keep orchestrator last so it can assign next-cycle priorities.",
            {"order": {k: order.get(k) for k in ["org_evaluator", "org_improvement_guardian", "research_hypothesis", "experiment_runner", "research_org_orchestrator"]}},
        )

    # Keep otherwise-unused reads visible as freshness signals in the packet.
    meta_fresh = {
        "guardian_patch_proposal_count": (guardian.get("summary") or {}).get("patch_proposal_count"),
        "orchestrator_agenda_count": len(orchestrator.get("alpha_agenda") or []),
    }
    action_count = sum(1 for x in reviews if x["severity"] == "action")
    watch_count = sum(1 for x in reviews if x["severity"] == "watch")
    summary = {
        "review_count": len(reviews),
        "action_count": action_count,
        "watch_count": watch_count,
        "info_count": sum(1 for x in reviews if x["severity"] == "info"),
        "scheduled_agent_count": len(scheduled),
        "unclassified_agent_count": len(unclassified),
    }
    next_actions = [x["recommendation"] for x in reviews if x["severity"] == "action"][:5]
    payload = {
        "run_at": now(),
        "mode": "research_org_architecture_review",
        "real_trading": False,
        "purpose": "Review whether the research organization structure still fits the objective: reproducible paper-research improvements to recommendation quality.",
        "summary": summary,
        "reviews": reviews,
        "meta_freshness": meta_fresh,
        "artifact_freshness": {
            "pipeline": step_output_age("/tmp/research_pipeline_latest.json"),
            "org_evaluator": step_output_age("/tmp/research_org_evaluation_latest.json"),
            "guardian": step_output_age("/tmp/org_improvement_guardian_latest.json"),
            "orchestrator": step_output_age("/tmp/research_org_orchestrator_latest.json"),
        },
        "next_actions": next_actions,
    }
    attach_contract(
        payload,
        "org_architecture_review",
        status="degraded" if action_count else "ok",
        outputs={"review_count": len(reviews), "action_count": action_count, "watch_count": watch_count, "unclassified_agent_count": len(unclassified)},
        metrics=summary,
        warnings=[x["title"] for x in reviews if x["severity"] == "action"],
        next_actions=next_actions,
    )
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
