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

    fund = data["fund_price_replay"]
    checks.append(status(bool(fund.get("trades")), "fund_trade_source_available", f"trades={len(fund.get('trades') or [])}"))

    mover = data["market_mover_seed"]
    upper = mover.get("top_upper_limit_items") or []
    checks.append(status(bool(upper), "upper_limit_seed_visible", f"top_upper_limit_items={len(upper)}"))

    ok = all(c["ok"] for c in checks)
    packet = {"status": "ok" if ok else "needs_attention", "checks": checks}
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
