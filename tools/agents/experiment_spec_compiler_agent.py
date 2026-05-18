#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agents.lib.agent_contract import attach_contract, write_json_shared
from tools.agents.lib.runtime import now_utc, read_json


OUT = Path("/tmp/research_experiment_specs_latest.json")


def add_spec(specs: list[dict[str, Any]], *, source: str, title: str, runner_task: str, targets: list[str] | None = None, priority: str = "medium", success_criteria: dict[str, Any] | None = None, evidence: dict[str, Any] | None = None) -> None:
    clean_targets = [str(x) for x in (targets or []) if x not in (None, "")]
    key = (runner_task, tuple(clean_targets), title)
    for existing in specs:
        if existing.get("_dedupe_key") == key:
            existing.setdefault("sources", []).append(source)
            return
    idx = len(specs) + 1
    specs.append({
        "_dedupe_key": key,
        "id": f"exp_spec_{idx:03d}",
        "source": source,
        "sources": [source],
        "title": title,
        "priority": priority,
        "runner_task": runner_task,
        "targets": clean_targets,
        "authority": "bounded_diagnostic_only",
        "real_trading": False,
        "success_criteria": success_criteria or {},
        "evidence": evidence or {},
    })


def main() -> None:
    queue = read_json("/tmp/research_queue_latest.json")
    audit = read_json("/tmp/recommendation_audit_latest.json")
    escalation = read_json("/tmp/experiment_escalation_latest.json")
    positive = read_json("/tmp/positive_cohort_scout_latest.json")
    tail = read_json("/tmp/audit_tail_quarantine_scout_latest.json")
    route = read_json("/tmp/market_route_audit_latest.json")
    specs: list[dict[str, Any]] = []

    queue_summary = queue.get("summary") or {}
    dominant = queue_summary.get("dominant_critic_issue") or {}
    if dominant.get("issue"):
        add_spec(
            specs,
            source="research_queue",
            title=f"Validate dominant recommendation bottleneck: {dominant.get('issue')}",
            runner_task="current_recommendation_validation",
            targets=[dominant.get("issue")],
            priority="high",
            success_criteria={"processed_combinations": "> 0", "dominant_bottleneck_count": "decrease"},
            evidence=dominant,
        )

    best = ((audit.get("summary") or {}).get("best") or {})
    best_logic = (audit.get("summary") or {}).get("best_logic")
    flags = set(best.get("quality_flags") or [])
    if flags.intersection({"left_tail_excess_risk", "negative_expected_excess_value", "no_positive_average_excess"}):
        add_spec(
            specs,
            source="recommendation_audit",
            title=f"Retest exit policy for weak EV/tail risk: {best_logic}",
            runner_task="exit_policy_optimizer",
            targets=[best_logic] if best_logic else [],
            priority="high",
            success_criteria={"expected_excess_value_pct": ">= 0", "p10_excess_return_pct": "> -8"},
            evidence={"quality_flags": sorted(flags), "avg_excess_return_pct": best.get("avg_excess_return_pct"), "expected_excess_value_pct": best.get("expected_excess_value_pct")},
        )

    for item in (escalation.get("bold_experiments") or [])[:4]:
        title = item.get("title") or item.get("name") or item.get("experiment") or "Bold paper-only experiment"
        target = item.get("target") or item.get("logic") or item.get("theme")
        add_spec(
            specs,
            source="experiment_escalation",
            title=str(title),
            runner_task="validation_probe",
            targets=[target] if target else [],
            priority=str(item.get("priority") or "medium"),
            success_criteria=item.get("success_criteria") or {"evidence_collected": True},
            evidence={k: item.get(k) for k in ("reason", "remaining_blocker", "proposal_id") if item.get(k) is not None},
        )

    for item in (positive.get("candidates") or [])[:3]:
        logic = item.get("logic") or item.get("family") or item.get("policy")
        add_spec(
            specs,
            source="positive_cohort_scout",
            title=f"Validate positive cohort candidate: {logic}",
            runner_task="validation_probe",
            targets=[logic] if logic else [],
            priority="medium",
            success_criteria={"avg_excess_return_pct": "> 0", "sample_count": "increase"},
            evidence={k: item.get(k) for k in ("summary", "baseline", "verdict") if item.get(k) is not None},
        )

    for item in (tail.get("experiments") or [])[:3]:
        title = item.get("title") or item.get("policy") or "Tail quarantine experiment"
        target = item.get("logic") or item.get("policy") or item.get("symbol")
        add_spec(
            specs,
            source="audit_tail_quarantine_scout",
            title=str(title),
            runner_task="validation_probe",
            targets=[target] if target else [],
            priority="medium",
            success_criteria={"p10_excess_return_pct": "> -8", "tail_loss_count": "decrease"},
            evidence={k: item.get(k) for k in ("reason", "summary", "delta") if item.get(k) is not None},
        )

    for item in (route.get("watch_candidates") or [])[:3]:
        market = item.get("market")
        add_spec(
            specs,
            source="market_route_audit",
            title=f"Review market route eligibility: {market}",
            runner_task="market_route_review",
            targets=[market] if market else [],
            priority="low",
            success_criteria={"market_quality": "improves_or_remains_blocked"},
            evidence=item,
        )

    public_specs = [{k: v for k, v in spec.items() if k != "_dedupe_key"} for spec in specs[:12]]
    packet = {
        "run_at": now_utc(),
        "mode": "generic_experiment_spec_compiler",
        "real_trading": False,
        "authority": "compile_specs_only",
        "specs": public_specs,
        "summary": {
            "spec_count": len(public_specs),
            "high_priority_count": sum(1 for spec in public_specs if spec.get("priority") == "high"),
            "source_counts": {source: sum(1 for spec in public_specs if source in (spec.get("sources") or [])) for source in sorted({s for spec in public_specs for s in (spec.get("sources") or [])})},
        },
        "next_actions": ["Planner should consume these specs before falling back to raw hypotheses."],
    }
    attach_contract(packet, "experiment_spec_compiler", status="ok", outputs={"spec_count": len(public_specs)}, metrics=packet["summary"], warnings=[], next_actions=packet["next_actions"])
    write_json_shared(OUT, packet)
    print(json.dumps(packet, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
