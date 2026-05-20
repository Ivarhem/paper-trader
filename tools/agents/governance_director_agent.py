#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agents.domain_supervisor_lib import contract_status, emit_supervisor, fitness, read_json, warnings

OWNED = ["paper_trader_integrity", "org_evaluator", "org_improvement_guardian", "org_architecture_review", "research_hypothesis", "experiment_spec_compiler", "experiment_planner", "experiment_runner", "evidence_judge", "research_experiment_ledger", "research_org_orchestrator", "experiment_escalation"]
ARTIFACTS = {
    "paper_trader_integrity": "/tmp/paper_trader_integrity_latest.json",
    "org_evaluator": "/tmp/research_org_evaluation_latest.json",
    "org_improvement_guardian": "/tmp/org_improvement_guardian_latest.json",
    "org_architecture_review": "/tmp/org_architecture_review_latest.json",
    "research_hypothesis": "/tmp/research_hypotheses_latest.json",
    "experiment_spec_compiler": "/tmp/research_experiment_specs_latest.json",
    "experiment_planner": "/tmp/research_experiment_plan_latest.json",
    "experiment_runner": "/tmp/research_experiment_results_latest.json",
    "evidence_judge": "/tmp/research_evidence_judge_latest.json",
    "research_experiment_ledger": "/tmp/research_experiment_ledger_latest.json",
    "research_org_orchestrator": "/tmp/research_org_orchestrator_latest.json",
    "experiment_escalation": "/tmp/experiment_escalation_latest.json",
}


def main() -> None:
    packets = {agent: read_json(path) for agent, path in ARTIFACTS.items()}
    integrity = packets["paper_trader_integrity"]
    org_eval = packets["org_evaluator"]
    guardian = packets["org_improvement_guardian"]
    architecture = packets["org_architecture_review"]
    plan = packets["experiment_planner"]
    results = packets["experiment_runner"]
    ledger = packets["research_experiment_ledger"]

    rows = []
    for agent in OWNED:
        packet = packets.get(agent) or {}
        checks = []
        penalty = 0
        if agent == "paper_trader_integrity":
            problems = len(integrity.get("problems") or [])
            checks.append(f"integrity_problems={problems}")
            if problems:
                penalty += min(35, problems * 7)
        if agent == "org_evaluator":
            verdict = org_eval.get("verdict")
            checks.append(f"verdict={verdict}")
            if verdict == "needs_intervention":
                penalty += 25
        if agent == "experiment_runner":
            count = len(results.get("results") or results.get("items") or [])
            checks.append(f"experiment_results={count}")
        rows.append(fitness(agent, contract_status(packet), len(warnings(packet)), checks, penalty))

    action_reviews = [x for x in (architecture.get("reviews") or []) if str(x.get("severity")) == "action"]
    patch_proposals = guardian.get("patch_proposals") or []
    integrity_problems = len(integrity.get("problems") or [])
    missing = []
    if action_reviews:
        missing.append({"capability": "architecture_action_owner_routing", "reason": f"action reviews: {len(action_reviews)}", "severity": "action"})
    if patch_proposals:
        missing.append({"capability": "guardian_patch_decision_queue", "reason": f"patch proposals: {len(patch_proposals)}", "severity": "watch"})
    if integrity_problems:
        missing.append({"capability": "paper_only_integrity_repair", "reason": f"integrity problems: {integrity_problems}", "severity": "action"})

    summary = {
        "health_score": org_eval.get("health_score"),
        "verdict": org_eval.get("verdict"),
        "finding_count": len(org_eval.get("findings") or []),
        "architecture_summary": architecture.get("summary"),
        "architecture_action_count": len(action_reviews),
        "guardian_summary": guardian.get("summary"),
        "patch_proposal_count": len(patch_proposals),
        "integrity_summary": integrity.get("summary"),
        "integrity_problem_count": integrity_problems,
        "experiment_plan_summary": plan.get("summary"),
        "experiment_results_summary": results.get("summary"),
        "experiment_ledger_summary": ledger.get("summary"),
    }
    assignments = [
        {"owner_agent": "org_architecture_review", "assignment": "turn action-severity architecture findings into explicitly owned next-cycle tickets", "priority": "high" if action_reviews else "normal", "source_bottleneck": "architecture_action_reviews", "target_artifact": "/tmp/org_architecture_review_latest.json"},
        {"owner_agent": "org_improvement_guardian", "assignment": "separate safe auto-fixes from proposals requiring human/owner review", "priority": "normal", "source_bottleneck": "patch_proposal_queue", "target_artifact": "/tmp/org_improvement_guardian_latest.json"},
        {"owner_agent": "paper_trader_integrity", "assignment": "keep paper-only and UI visibility checks as hard governance signals", "priority": "high" if integrity_problems else "normal", "source_bottleneck": "integrity", "target_artifact": "/tmp/paper_trader_integrity_latest.json"},
    ]
    emit_supervisor(supervisor="governance_director", title="Governance Director", domain="governance_office", owned_agents=OWNED, role_fitness=rows, summary=summary, missing_capability=missing, bottleneck="governance action queue needs owner routing" if missing else None, bottleneck_severity="action" if any(x.get("severity") == "action" for x in missing) else "watch", next_cycle_assignments=assignments, output="/tmp/governance_director_latest.json")


if __name__ == "__main__":
    main()
