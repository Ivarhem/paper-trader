#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re, subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(path)}

def market_of(symbol: str) -> str:
    return "KR" if str(symbol or "").endswith((".KS", ".KQ")) else "US"


def service_status(unit: str = "paper-trader.service") -> dict:
    try:
        cp = subprocess.run(
            ["systemctl", "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts", "-p", "ExecMainPID", "-p", "ExecMainStartTimestamp"],
            text=True,
            capture_output=True,
            timeout=5,
        )
        if cp.returncode != 0:
            return {"_error": cp.stderr[-500:] or cp.stdout[-500:], "unit": unit}
        out = {}
        for line in cp.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
        out["unit"] = unit
        try:
            out["NRestarts"] = int(out.get("NRestarts") or 0)
        except Exception:
            out["NRestarts"] = None
        return out
    except Exception as exc:
        return {"_error": str(exc), "unit": unit}

def count_markets(items):
    out = {"KR": 0, "US": 0}
    for row in items or []:
        m = market_of(row.get("symbol"))
        out[m] = out.get(m, 0) + 1
    return out

def main():
    ap = argparse.ArgumentParser(description="Paper-trader integrity checks for paper-only recommendation pipeline")
    ap.add_argument("--output", default="/tmp/paper_trader_integrity_latest.json")
    args = ap.parse_args()

    recs = read_json(Path("/tmp/recommendations_latest.json"))
    shadow = read_json(Path("/tmp/shadow_recommendations_latest.json"))
    board = read_json(Path("/tmp/internal_signal_board_latest.json"))
    audit = read_json(Path("/tmp/recommendation_audit_latest.json"))
    pipeline = read_json(Path("/tmp/research_pipeline_latest.json"))
    static_shadow_path = ROOT / "static" / "shadow_recommendations_latest.json"
    static_shadow = read_json(static_shadow_path)
    monitor_path = ROOT / "static" / "monitor.html"
    monitor_js_path = ROOT / "static" / "monitor.js"
    monitor_html = monitor_path.read_text(encoding="utf-8") if monitor_path.exists() else ""
    monitor_js = monitor_js_path.read_text(encoding="utf-8") if monitor_js_path.exists() else ""
    svc = service_status()

    problems = []
    warnings = []
    checks = []

    def check(name, ok, severity="problem", detail=None):
        item = {"name": name, "ok": bool(ok), "severity": severity, "detail": detail}
        checks.append(item)
        if not ok:
            (problems if severity == "problem" else warnings).append(item)

    for name, payload in [("recommendations", recs), ("shadow", shadow), ("internal_signal_board", board), ("audit", audit)]:
        check(f"{name}_real_trading_false", payload.get("real_trading") is False, "problem", payload.get("real_trading"))
    policy = shadow.get("policy") or {}
    check("shadow_not_active_eligible", policy.get("active_recommendation_eligible") is False, "problem", policy)
    check("shadow_no_external_publish", policy.get("external_publish") is False, "problem", policy)
    check("shadow_no_broker_sync", policy.get("broker_sync") is False, "problem", policy)
    check("shadow_no_copy_trading", policy.get("copy_trading") is False, "problem", policy)

    rec_items = recs.get("items") or []
    shadow_items = shadow.get("items") or []
    static_items = static_shadow.get("items") or []
    shadow_market_counts = shadow.get("market_counts") or count_markets(shadow_items)
    static_market_counts = static_shadow.get("market_counts") or count_markets(static_items)

    check("recommendations_file_readable", not recs.get("_read_error"), "problem", recs.get("_read_error"))
    check("shadow_file_readable", not shadow.get("_read_error"), "problem", shadow.get("_read_error"))
    check("static_shadow_file_readable", not static_shadow.get("_read_error"), "problem", static_shadow.get("_read_error"))
    check("shadow_static_export_count_matches", len(static_items) == len(shadow_items), "problem", {"tmp": len(shadow_items), "static": len(static_items)})
    check("shadow_static_market_counts_match", static_market_counts == shadow_market_counts, "problem", {"tmp": shadow_market_counts, "static": static_market_counts})

    rec_market_counts = recs.get("market_counts") or count_markets(rec_items)
    kr_shadow_needed = (rec_market_counts.get("KR", 0) == 0)
    check("kr_shadow_available_when_regular_kr_empty", (not kr_shadow_needed) or shadow_market_counts.get("KR", 0) > 0, "problem", {"regular": rec_market_counts, "shadow": shadow_market_counts})
    check("monitor_loads_shadow_static", "shadow_recommendations_latest.json" in monitor_js, "problem")
    check("monitor_has_shadow_mapper", "function shadowToResearchWatch" in monitor_js, "problem")
    check("monitor_has_shadow_fallback", "shadowFallback" in monitor_js, "problem")
    match = re.search(r"monitor\.js\?v=([^\"']+)", monitor_html)
    cache_version = match.group(1) if match else None
    monitor_js_exists = (ROOT / "static" / "monitor.js").exists()
    check("monitor_js_cache_version_present", bool(cache_version and monitor_js_exists), "warning", cache_version)

    check("active_recommendations_strict_not_forced", (recs.get("active_strategy_count") or 0) <= 5, "warning", recs.get("active_strategy_count"))
    check("paper_trader_service_running", svc.get("ActiveState") == "active" and svc.get("SubState") == "running", "problem", svc)
    check("paper_trader_service_restart_counter_reasonable", (svc.get("NRestarts") is None) or int(svc.get("NRestarts") or 0) < 1000, "warning", svc)
    audit_contract = audit.get("contract") or {}
    check("audit_available", not audit.get("_read_error"), "problem", audit.get("_read_error"))
    if audit_contract.get("status") and audit_contract.get("status") != "ok":
        warnings.append({"name": "audit_contract_degraded", "ok": False, "severity": "warning", "detail": audit_contract.get("warnings")})

    board_summary = board.get("summary") or {}
    board_kind_counts = board_summary.get("by_kind") or board.get("kind_counts") or {}
    check("internal_board_has_shadow_signals", board_kind_counts.get("shadow_signal", 0) == len(shadow_items), "warning", {"board": board_kind_counts, "shadow_items": len(shadow_items)})

    # Authority contract checks: catch role drift where overlay/proposal agents
    # start writing final fields directly again.
    source_checks = {
        "recommendation_critic_no_final_bucket_write": ROOT / "tools" / "agents" / "recommendation_critic_agent.py",
        "market_regime_gate_no_final_bucket_write": ROOT / "tools" / "agents" / "market_regime_gate_agent.py",
        "portfolio_risk_manager_no_final_bucket_write": ROOT / "tools" / "agents" / "portfolio_risk_manager_agent.py",
    }
    for check_name, src_path in source_checks.items():
        txt = src_path.read_text(encoding="utf-8") if src_path.exists() else ""
        forbidden = "['recommendation_bucket']" in txt or "['trade_eligible']" in txt or "recommendations_latest.json').write_text" in txt or "rec_path.write_text" in txt or "path.write_text" in txt
        check(check_name, not forbidden, "problem", str(src_path))
    for name,path in {
        "critic_opinion_file_available": Path("/tmp/recommendation_opinions_critic_latest.json"),
        "portfolio_opinion_file_available": Path("/tmp/recommendation_opinions_portfolio_latest.json"),
        "regime_opinion_file_available": Path("/tmp/recommendation_opinions_regime_latest.json"),
    }.items():
        payload=read_json(path)
        check(name, bool((payload.get("opinions") or [])) and payload.get("writes_recommendations_latest") is False, "warning", payload.get("_read_error") or len(payload.get("opinions") or []))

    pipeline_txt = (ROOT / "tools" / "agents" / "research_pipeline_agent.py").read_text(encoding="utf-8")
    check("active_balancer_proposal_only_in_pipeline", "--apply-promotions" not in pipeline_txt, "problem")
    check("tail_risk_status_proposal_only_in_pipeline", "--apply-status" not in pipeline_txt, "problem")
    org_profile = read_json(ROOT / "configs" / "org_profile.json")
    target_active = ((org_profile.get("strategy") or {}).get("target_active") if isinstance(org_profile, dict) else None)
    check("pipeline_uses_org_profile_strategy", "strategy_profile.get('target_active')" in pipeline_txt, "warning", {"target_active": target_active})

    status = "failed" if problems else "ok"
    next_actions = []
    if problems:
        next_actions.append("Fix integrity problems before trusting UI/API recommendation visibility.")
    elif warnings:
        next_actions.append("Review non-blocking integrity warnings; paper-only gates remain intact.")
    else:
        next_actions.append("Continue scheduled pipeline; integrity checks passed.")

    packet = {
        "run_at": utc_now(),
        "mode": "paper_trader_integrity_checks",
        "status": status,
        "real_trading": False,
        "summary": {
            "problem_count": len(problems),
            "warning_count": len(warnings),
            "recommendation_market_counts": rec_market_counts,
            "shadow_market_counts": shadow_market_counts,
            "static_shadow_market_counts": static_market_counts,
            "monitor_js_cache_version": cache_version,
            "pipeline_status": pipeline.get("status"),
            "service_active_state": svc.get("ActiveState"),
            "service_n_restarts": svc.get("NRestarts"),
        },
        "checks": checks,
        "problems": problems,
        "warnings": warnings,
        "next_actions": next_actions,
    }
    packet["contract"] = {
        "agent": "paper_trader_integrity",
        "status": status,
        "run_at": packet["run_at"],
        "metrics": packet["summary"],
        "warnings": [w["name"] for w in warnings],
        "next_actions": next_actions,
        "outputs": {"packet_keys": sorted(packet.keys())},
    }
    Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(packet, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
