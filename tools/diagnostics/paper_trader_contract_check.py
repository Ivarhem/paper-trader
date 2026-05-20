#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

CHECKS = [
    ("pipeline_status", Path("/tmp/research_pipeline_status.json")),
    ("recommendation_audit", Path("/tmp/recommendation_audit_latest.json")),
    ("outcome_attribution", Path("/tmp/outcome_attribution_latest.json")),
    ("recommendations", Path("/tmp/recommendations_latest.json")),
    ("fund_price_replay", Path("/tmp/paper_fund_price_replay_latest.json")),
    ("market_mover_seed", Path("/tmp/market_mover_seed_latest.json")),
    ("batch_pause", Path("/tmp/paper_trader_batch_pause_latest.json")),
    ("strategy_director", Path("/tmp/strategy_director_latest.json")),
    ("fund_director", Path("/tmp/fund_director_latest.json")),
    ("recommendation_desk_lead", Path("/tmp/recommendation_desk_lead_latest.json")),
    ("executive_director", Path("/tmp/executive_director_latest.json")),
    ("suborg_summary", Path("/tmp/research_org_suborg_summary_latest.json")),
    ("experiment_specs", Path("/tmp/research_experiment_specs_latest.json")),
    ("experiment_plan", Path("/tmp/research_experiment_plan_latest.json")),
]

BATCH_WRAPPERS = [
    Path("scripts/run_research_org_cron.sh"),
    Path("scripts/run_next_trade_issue_context_cron.sh"),
    Path("scripts/run_validation_worker_cron.sh"),
    Path("scripts/run_market_data_freshness_cron.sh"),
    Path("scripts/run_daily_external_mover_validation_cron.sh"),
]

def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"items": data}
    except Exception as exc:
        return {"_error": str(exc), "_path": str(path)}

def status(ok: bool, name: str, detail: str) -> dict:
    return {"ok": ok, "name": name, "detail": detail}

def main() -> int:
    data = {name: load(path) for name, path in CHECKS}
    checks = []
    for name, payload in data.items():
        checks.append(status("_error" not in payload, f"{name}_readable", payload.get("_error") or "readable"))

    audit = data["recommendation_audit"]
    audit_items = audit.get("items") or []
    audited_total = audit.get("items_total_audited")
    filtered_total = audit.get("items_total_filtered")
    candidate_total = audit.get("items_total_candidate_buy_zone")
    preview_filter = audit.get("items_preview_filter")
    checks.append(status(bool(audited_total), "audit_has_audited_samples", f"items_total_audited={audited_total}"))
    checks.append(status(bool(audit_items), "audit_preview_visible", f"preview_count={len(audit_items)} filter={preview_filter}"))
    if audited_total and candidate_total == 0:
        checks.append(status(preview_filter == "all_audited_no_candidate_buy_zone", "audit_watch_only_contract", f"candidate_buy_zone=0 filtered={filtered_total} filter={preview_filter}"))

    outcome = data["outcome_attribution"]
    sample_count = outcome.get("sample_count")
    if audited_total:
        checks.append(status(sample_count == audited_total, "outcome_uses_full_audit_when_preview_paged", f"sample_count={sample_count} audited_total={audited_total}"))

    recs = data["recommendations"]
    rec_items = recs.get("items") or []
    checks.append(status(bool(rec_items), "recommendations_visible", f"item_count={len(rec_items)}"))
    missing_posture = [
        row.get("symbol")
        for row in rec_items
        if not row.get("committee_posture") or not row.get("highlight_targets")
    ]
    checks.append(status(
        not missing_posture,
        "recommendation_price_posture_visible",
        f"missing={missing_posture[:8]} count={len(missing_posture)}",
    ))

    fund = data["fund_price_replay"]
    checks.append(status(bool(fund.get("trades")), "fund_trade_source_available", f"trades={len(fund.get('trades') or [])}"))

    mover = data["market_mover_seed"]
    upper = mover.get("top_upper_limit_items") or []
    checks.append(status(bool(upper), "upper_limit_seed_visible", f"top_upper_limit_items={len(upper)}"))

    pause = data["batch_pause"]
    checks.append(status("_error" not in pause, "batch_pause_status_visible", pause.get("status") or pause.get("_error") or "readable"))

    for name in ("strategy_director", "fund_director", "recommendation_desk_lead"):
        supervisor = data[name]
        contract = supervisor.get("contract") or {}
        checks.append(status(
            supervisor.get("schema") == "paper_trader.domain_supervisor.v1" and bool(supervisor.get("owned_agents")) and bool(supervisor.get("role_fitness")),
            f"{name}_contract_visible",
            f"domain_status={supervisor.get('domain_status')} owned={len(supervisor.get('owned_agents') or [])} role_fitness={len(supervisor.get('role_fitness') or [])} contract={contract.get('status')}",
        ))
    strategy_supervisor = data["strategy_director"]
    promotion_queue = strategy_supervisor.get("promotion_queue") or []
    if strategy_supervisor.get("bottleneck") == "no_high_confidence_historical_active_strategy":
        checks.append(status(
            bool(promotion_queue),
            "strategy_director_promotion_queue_visible",
            f"promotion_queue_count={len(promotion_queue)}",
        ))
        missing_assignment_meta = [
            item.get("owner_agent")
            for item in (strategy_supervisor.get("next_cycle_assignments") or [])
            if item.get("priority") is None or item.get("target_artifact") is None or item.get("validation_batch_hint") is None
        ]
        checks.append(status(
            not missing_assignment_meta,
            "strategy_director_assignments_machine_usable",
            f"missing_meta={missing_assignment_meta[:8]} count={len(missing_assignment_meta)}",
        ))
    executive = data["executive_director"]
    checks.append(status(
        executive.get("schema") == "paper_trader.executive_director.v1" and len(executive.get("managed_directors") or []) >= 3,
        "executive_director_contract_visible",
        f"org_status={executive.get('org_status')} managed_directors={len(executive.get('managed_directors') or [])}",
    ))
    suborg = data["suborg_summary"]
    supervisors = suborg.get("domain_supervisors") or {}
    checks.append(status(
        all(k in supervisors for k in ("strategy_director", "fund_director", "recommendation_desk_lead")),
        "suborg_summary_contains_domain_supervisors",
        f"supervisors={sorted(supervisors.keys())}",
    ))
    specs = data["experiment_specs"]
    spec_sources = {source for spec in (specs.get("specs") or []) for source in (spec.get("sources") or [])}
    if promotion_queue:
        checks.append(status(
            "strategy_director" in spec_sources,
            "experiment_specs_consume_strategy_director",
            f"sources={sorted(spec_sources)}",
        ))
    plan = data["experiment_plan"]
    plan_sources = {source for row in (plan.get("plans") or []) for source in (row.get("sources") or [])}
    if promotion_queue:
        checks.append(status(
            "strategy_director" in plan_sources,
            "experiment_plan_contains_strategy_director_route",
            f"sources={sorted(plan_sources)} plan_count={len(plan.get('plans') or [])}",
        ))
    for wrapper in BATCH_WRAPPERS:
        try:
            text = wrapper.read_text(encoding="utf-8")
        except Exception as exc:
            checks.append(status(False, f"{wrapper.name}_readable", str(exc)))
            continue
        has_guard = "batch_pause_guard.py" in text and "--skip-status" in text
        checks.append(status(
            has_guard,
            f"{wrapper.name}_uses_source_edit_pause_guard",
            "guarded" if has_guard else "missing guard",
        ))

    ok = all(c["ok"] for c in checks)
    packet = {"status": "ok" if ok else "needs_attention", "checks": checks}
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
