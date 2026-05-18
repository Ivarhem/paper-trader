#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.agents.lib.agent_contract import attach_contract

OUT = Path("/tmp/recommendation_funnel_latest.json")


def load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc), "_path": path}


def symbol_set(rows, key: str = "symbol") -> set[str]:
    return {str(row.get(key)).upper() for row in rows or [] if row.get(key)}


def count_by_action(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        action = row.get("action") or "unknown"
        out[action] = out.get(action, 0) + 1
    return out


def main() -> None:
    discovery = load_json("/tmp/universe_discovery_latest.json")
    refresh = load_json("/tmp/daily_price_refresh_latest.json")
    quality = load_json("/tmp/data_quality_latest.json")
    curator = load_json("/tmp/universe_curator_latest.json")
    scout = load_json("/tmp/universe_scout_latest.json")
    recs = load_json("/tmp/recommendations_latest.json")
    critic = load_json("/tmp/recommendation_critic_latest.json")
    committee = load_json("/tmp/investment_committee_latest.json")

    rec_items = recs.get("items") or []
    critic_items = critic.get("items") or []
    committee_items = committee.get("items") or []
    quality_symbols = quality.get("symbols") or []

    discovered = discovery.get("items") or discovery.get("selected") or discovery.get("candidates") or [
        {"symbol": sym} for sym in (discovery.get("selected_for_import") or [])
    ]
    scout_selected = scout.get("selected") or []
    curator_items = curator.get("items") or []
    active_curated = [x for x in curator_items if x.get("status") == "active"]
    quality_fail = [x for x in quality_symbols if x.get("level") == "fail"]
    quality_watch = [x for x in quality_symbols if x.get("level") == "watch"]
    critic_high = [x for x in critic_items if x.get("severity") == "high"]
    critic_issue_summary = critic.get("issue_summary") or {}
    top_critic_issues = critic_issue_summary.get("top_issues") or []
    committee_support = [
        x for x in committee_items
        if ((x.get("committee") or {}).get("synthesis") or {}).get("decision") == "committee_support"
    ]
    committee_watch = [
        x for x in committee_items
        if ((x.get("committee") or {}).get("synthesis") or {}).get("decision") == "watch"
    ]
    committee_reject = [
        x for x in committee_items
        if ((x.get("committee") or {}).get("synthesis") or {}).get("decision") == "reject"
    ]

    stages = [
        {"stage": "discovered", "count": len(discovered), "symbols": sorted(symbol_set(discovered))[:100]},
        {"stage": "price_refreshed", "count": refresh.get("symbol_count") or 0, "symbols": (refresh.get("symbols") or [])[:100]},
        {"stage": "data_quality_fail", "count": len(quality_fail), "symbols": [x.get("symbol") for x in quality_fail[:100]]},
        {"stage": "data_quality_watch", "count": len(quality_watch), "symbols": [x.get("symbol") for x in quality_watch[:100]]},
        {"stage": "curated_active", "count": len(active_curated), "symbols": sorted(symbol_set(active_curated))[:100]},
        {"stage": "scout_selected", "count": len(scout_selected), "symbols": sorted(symbol_set(scout_selected))[:100]},
        {"stage": "recommendation_candidates", "count": sum((recs.get("candidate_market_counts") or {}).values()), "by_market": recs.get("candidate_market_counts") or {}},
        {"stage": "final_recommendations", "count": len(rec_items), "by_action": count_by_action(rec_items), "symbols": sorted(symbol_set(rec_items))[:100]},
        {"stage": "critic_high", "count": len(critic_high), "symbols": sorted(symbol_set(critic_high))[:100], "top_issues": top_critic_issues[:5]},
        {"stage": "committee_support", "count": len(committee_support), "symbols": sorted(symbol_set(committee_support))[:100]},
        {"stage": "committee_watch", "count": len(committee_watch), "symbols": sorted(symbol_set(committee_watch))[:100]},
        {"stage": "committee_reject", "count": len(committee_reject), "symbols": sorted(symbol_set(committee_reject))[:100]},
    ]
    summary_text = (
        f"Recommendation funnel: final {len(rec_items)}, critic high {len(critic_high)}, "
        f"committee support/watch/reject {len(committee_support)}/{len(committee_watch)}/{len(committee_reject)}, "
        f"data quality fail/watch {len(quality_fail)}/{len(quality_watch)}."
    )
    packet = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "role": "Recommendation Funnel Agent",
        "mode": "recommendation_decision_funnel",
        "objective": "추천 후보가 발굴부터 최종 위원회 판단까지 어떤 단계에서 통과/차단되는지 계측합니다.",
        "responsibilities": [
            "universe discovery, data quality, scout, recommendation, critic, committee 산출물을 한 번에 연결합니다.",
            "단계별 후보 수와 주요 탈락 신호를 기록해 추천 품질 병목을 찾습니다.",
            "추천 점수나 최종 판정을 직접 수정하지 않고 관측 지표만 제공합니다."
        ],
        "real_trading": False,
        "stages": stages,
        "summary_text": summary_text,
        "summary": {
            "final_recommendations": len(rec_items),
            "critic_high": len(critic_high),
            "committee_support": len(committee_support),
            "committee_watch": len(committee_watch),
            "committee_reject": len(committee_reject),
            "data_quality_fail": len(quality_fail),
            "data_quality_watch": len(quality_watch),
            "top_critic_issues": top_critic_issues[:5],
            "dominant_critic_issue": critic_issue_summary.get("dominant_issue"),
        },
    }
    warnings = []
    if not rec_items:
        warnings.append("no final recommendation rows found")
    attach_contract(
        packet,
        "recommendation_funnel_agent",
        status="degraded" if warnings else "ok",
        outputs={"stage_count": len(stages), "final_recommendations": len(rec_items)},
        metrics=packet["summary"],
        warnings=warnings,
        next_actions=(["Inspect upstream recommendation_agent output."] if warnings else ([f"Top critic bottleneck: {top_critic_issues[0]['issue']} ({top_critic_issues[0]['count']} rows)."] if top_critic_issues else [])),
    )
    OUT.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(packet, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
