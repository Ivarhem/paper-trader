#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.database import init_db
from tools.agents.lib.agent_contract import attach_contract

OUT = Path("/tmp/recommendation_calibration_latest.json")


def bucket_score(score) -> str:
    try:
        value = float(score)
    except Exception:
        return "unknown"
    if value >= 80:
        return "80+"
    if value >= 65:
        return "65-79"
    if value >= 50:
        return "50-64"
    return "<50"


def avg(values: list[float]) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def pct(values: list[int]) -> float | None:
    vals = [int(v) for v in values if v is not None]
    return round(sum(vals) / len(vals) * 100, 2) if vals else None


def summarize(rows: list[sqlite3.Row], key_fn) -> list[dict]:
    buckets: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        buckets.setdefault(str(key_fn(row)), []).append(row)
    out = []
    for key, items in buckets.items():
        out.append({
            "bucket": key,
            "n": len(items),
            "avg_forward_return_pct": avg([r["forward_return_pct"] for r in items]),
            "avg_excess_return_pct": avg([r["excess_return_pct"] for r in items]),
            "hit_rate_pct": pct([r["hit"] for r in items]),
        })
    return sorted(out, key=lambda x: (x["bucket"]))


def main() -> None:
    init_db()
    conn = sqlite3.connect(get_settings().database_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT ro.*, rh.score, rh.confidence_grade, rh.strategy_id, rh.payload_json AS recommendation_payload
        FROM recommendation_outcomes ro
        JOIN recommendation_history rh ON rh.id = ro.recommendation_history_id
        WHERE ro.status IN ('complete', 'stopped_out')
        ORDER BY ro.run_at DESC
        LIMIT 5000
        """
    ).fetchall()
    conn.close()

    decoded = []
    for row in rows:
        item = dict(row)
        try:
            payload = json.loads(item.get("recommendation_payload") or "{}")
        except json.JSONDecodeError:
            payload = {}
        item["score_bucket"] = bucket_score(item.get("score"))
        item["critic_severity"] = ((payload.get("critic") or {}).get("severity")) or "none"
        item["committee_decision"] = (((payload.get("investment_committee") or {}).get("synthesis") or {}).get("decision")) or "none"
        item["financial_bucket"] = "negative" if ((payload.get("financial_quality") or {}).get("score_adjustment") or 0) < 0 else "neutral_or_positive"
        decoded.append(item)

    by_score = summarize(decoded, lambda r: f"{r['horizon_days']}D:{r['score_bucket']}")
    by_action = summarize(decoded, lambda r: f"{r['horizon_days']}D:{r['action']}")
    by_critic = summarize(decoded, lambda r: f"{r['horizon_days']}D:{r['critic_severity']}")
    by_committee = summarize(decoded, lambda r: f"{r['horizon_days']}D:{r['committee_decision']}")
    by_financial = summarize(decoded, lambda r: f"{r['horizon_days']}D:{r['financial_bucket']}")

    findings = []
    score20 = [x for x in by_score if x["bucket"].startswith("20D:")]
    if score20:
        ranked = sorted(score20, key=lambda x: {"80+": 4, "65-79": 3, "50-64": 2, "<50": 1, "unknown": 0}.get(x["bucket"].split(":", 1)[1], 0), reverse=True)
        if len(ranked) >= 2 and ranked[0].get("avg_excess_return_pct") is not None and ranked[-1].get("avg_excess_return_pct") is not None:
            if ranked[0]["avg_excess_return_pct"] < ranked[-1]["avg_excess_return_pct"]:
                findings.append({
                    "severity": "watch",
                    "area": "confidence_calibration",
                    "finding": "높은 점수 bucket이 낮은 점수 bucket보다 20D 초과수익이 낮습니다.",
                    "recommendation": "recommendation score weight와 critic penalty calibration을 재검토하세요.",
                    "metric": {"top_bucket": ranked[0], "bottom_bucket": ranked[-1]},
                })
    committee_reject = [x for x in by_committee if "reject" in x["bucket"]]
    for row in committee_reject:
        if row.get("avg_excess_return_pct") is not None and row["avg_excess_return_pct"] > 1:
            findings.append({
                "severity": "watch",
                "area": "committee_strictness",
                "finding": "committee reject bucket의 평균 초과수익이 양수입니다.",
                "recommendation": "committee reject threshold가 과도하게 보수적인지 검토하세요.",
                "metric": row,
            })

    packet = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "role": "Recommendation Calibration Agent",
        "mode": "recommendation_calibration",
        "objective": "추천 점수, critic, committee, 재무 품질 판단이 실제 forward outcome과 정렬되어 있는지 검증합니다.",
        "responsibilities": [
            "완료된 recommendation_outcomes를 score/action/critic/committee/financial bucket별로 집계합니다.",
            "높은 점수 bucket이 낮은 점수 bucket보다 성과가 낮거나, reject bucket이 양호한 성과를 내는지 감시합니다.",
            "표본이 부족할 때는 threshold를 자동 변경하지 않고 degraded 경고만 남깁니다."
        ],
        "real_trading": False,
        "sample_count": len(decoded),
        "by_score_bucket": by_score,
        "by_action": by_action,
        "by_critic": by_critic,
        "by_committee": by_committee,
        "by_financial": by_financial,
        "findings": findings,
        "summary": {
            "sample_count": len(decoded),
            "finding_count": len(findings),
            "complete": len(decoded),
        },
        "summary_text": f"Recommendation calibration: completed samples {len(decoded)}, findings {len(findings)}.",
    }
    warnings = []
    if len(decoded) < 30:
        warnings.append("calibration sample size below 30 completed outcomes")
    attach_contract(
        packet,
        "recommendation_calibration_agent",
        status="degraded" if warnings else "ok",
        outputs={"sample_count": len(decoded), "finding_count": len(findings)},
        metrics=packet["summary"],
        warnings=warnings,
        next_actions=["Wait for more 1D/5D/20D outcomes before tuning thresholds."] if warnings else [],
    )
    OUT.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(packet, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
