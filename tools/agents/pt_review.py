#!/usr/bin/env python3
"""Compact read-only paper_trader pipeline review helper.

Reads existing status artifacts and stores its own latest result in the DB
latest_artifacts table by default. It does not call broker/order endpoints,
mutate strategy state, or run the research pipeline.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from app.database import latest_artifact, save_latest_artifact
except Exception:  # pragma: no cover
    latest_artifact = None
    save_latest_artifact = None

ARTIFACTS = {
    "pipeline_status": {
        "keys": ["research_pipeline_status"],
        "files": [ROOT / "state/latest/research_pipeline_status.json", Path("/tmp/research_pipeline_status.json")],
    },
    "recommendations_status": {
        "keys": ["recommendations_status_latest"],
        "files": [ROOT / "state/latest/recommendations_status_latest.json", Path("/tmp/recommendations_status_latest.json")],
    },
    "recommendations": {
        "keys": ["recommendations_latest"],
        "files": [ROOT / "state/latest/recommendations_latest.json", Path("/tmp/recommendations_latest.json")],
    },
    "audit": {
        "keys": ["recommendation_audit_latest"],
        "files": [ROOT / "state/latest/recommendation_audit_latest.json", Path("/tmp/recommendation_audit_latest.json")],
    },
    "novelty_pruner": {
        "keys": ["strategy_novelty_pruner_latest"],
        "files": [ROOT / "state/latest/strategy_novelty_pruner_latest.json", Path("/tmp/strategy_novelty_pruner_latest.json")],
    },
    "active_balancer": {
        "keys": ["active_strategy_balancer_latest"],
        "files": [ROOT / "state/latest/active_strategy_balancer_latest.json", Path("/tmp/active_strategy_balancer_latest.json")],
    },
    "strategy_candidates": {
        "keys": ["strategy_candidates_latest"],
        "files": [ROOT / "state/latest/strategy_candidates_latest.json", Path("/tmp/strategy_candidates_latest.json")],
    },
    "validation_worker_status": {
        "keys": ["validation_worker_status"],
        "files": [ROOT / "state/latest/validation_worker_status.json", Path("/tmp/validation_worker_status.json")],
    },
    "batch_pause_status": {
        "keys": ["batch_pause_guard", "paper_trader_batch_pause_status"],
        "files": [ROOT / "state/batch_pause_guard.json", Path("/tmp/paper_trader_batch_pause_status.json")],
    },
    "smoke_check": {
        "keys": ["pipeline_smoke_check_latest"],
        "files": [ROOT / "state/latest/pipeline_smoke_check_latest.json", Path("/tmp/pipeline_smoke_check_latest.json")],
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_artifact(spec: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, str | None]:
    errors: list[str] = []
    candidates: list[tuple[float, int, dict[str, Any], str]] = []

    def parsed_time(value: dict[str, Any], fallback: float) -> float:
        for key in ("last_run_at", "run_at", "last_ok_at", "updated_at", "_db_updated_at"):
            raw = value.get(key)
            if not raw:
                continue
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
        contract = value.get("contract")
        if isinstance(contract, dict):
            raw = contract.get("run_at")
            if raw:
                try:
                    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
                except Exception:
                    pass
        return fallback

    if latest_artifact is not None:
        for key in spec.get("keys") or []:
            try:
                value = latest_artifact(key)
            except Exception as exc:
                errors.append(f"db:{key}: {exc}")
                continue
            if isinstance(value, dict):
                candidates.append((parsed_time(value, 0.0), 0, value, f"db:{key}"))
    for path in spec.get("files") or []:
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path}: {exc}")
            continue
        if isinstance(value, dict):
            candidates.append((parsed_time(value, path.stat().st_mtime), 1, value, str(path)))
            continue
        errors.append(f"{path}: top-level JSON is {type(value).__name__}")
    if candidates:
        # DB latest_artifacts can lag behind cron-written /tmp status files. Pick
        # the freshest artifact, preferring files when timestamps tie.
        _, _, value, source = max(candidates, key=lambda item: (item[0], item[1]))
        return value, source, None
    return None, None, "; ".join(errors) if errors else "missing"


def pick(data: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    if not isinstance(data, dict):
        return default
    for key in keys:
        if key in data:
            return data[key]
    contract = data.get("contract")
    if isinstance(contract, dict):
        for key in keys:
            if key in contract:
                return contract[key]
    return default


def count_items(data: dict[str, Any] | None) -> int | None:
    if not isinstance(data, dict):
        return None
    for key in ("items", "recommendations", "rows", "candidates"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    for key in ("recommendation_count", "item_count", "items_total", "items_total_filtered"):
        value = data.get(key)
        if isinstance(value, int):
            return value
    return None


def contract_status(data: dict[str, Any] | None) -> str | None:
    if not isinstance(data, dict):
        return None
    contract = data.get("contract")
    if isinstance(contract, dict):
        return contract.get("status")
    return data.get("status")


def summarize_artifacts() -> dict[str, Any]:
    loaded: dict[str, Any] = {}
    sources: dict[str, str] = {}
    missing: dict[str, str] = {}
    for name, spec in ARTIFACTS.items():
        data, source, error = read_artifact(spec)
        loaded[name] = data
        if source:
            sources[name] = source
        else:
            missing[name] = error or "missing"

    pipeline = loaded.get("pipeline_status")
    audit = loaded.get("audit")
    recs = loaded.get("recommendations")
    rec_status = loaded.get("recommendations_status")
    smoke = loaded.get("smoke_check")
    validation = loaded.get("validation_worker_status")
    batch_pause = loaded.get("batch_pause_status")

    problems: list[str] = []
    notes: list[str] = []
    next_actions: list[str] = []

    pipeline_status = pick(pipeline, "status", default="unknown")
    pipeline_inner = pick(pipeline, "pipeline_status", default=None)
    consecutive_failures = pick(pipeline, "consecutive_failures", default=0)
    degraded_count = pick(pipeline, "degraded_count", default=0)
    active_count = pick(pipeline, "active_count", default=None)
    repair_active_count = pick(pipeline, "repair_active_count", default=None)
    recommendation_count = pick(pipeline, "recommendation_count", default=count_items(recs))

    actual_failure_state = pipeline_status == "failed" or pipeline_inner == "failed"

    if pipeline_status not in ("ok", "healthy"):
        problems.append(f"pipeline status is {pipeline_status}")
    if pipeline_inner and pipeline_inner not in ("ok", "healthy"):
        problems.append(f"pipeline inner status is {pipeline_inner}")
    if actual_failure_state and isinstance(consecutive_failures, int) and consecutive_failures > 0:
        problems.append(f"consecutive pipeline failures: {consecutive_failures}")
    if isinstance(degraded_count, int) and degraded_count > 0:
        problems.append(f"degraded required agents: {degraded_count}")
    if active_count == 0 and repair_active_count == 0:
        problems.append("no active or repair-active strategies")

    smoke_status = contract_status(smoke)
    if smoke_status and smoke_status != "ok":
        problems.append(f"smoke check status is {smoke_status}")

    audit_status = contract_status(audit)
    if audit_status and audit_status != "ok":
        problems.append(f"recommendation audit status is {audit_status}")
    audit_contract = audit.get("contract") if isinstance(audit, dict) else None
    if isinstance(audit_contract, dict):
        next_actions.extend(audit_contract.get("next_actions") or [])
    for action in pick(pipeline, "next_actions", default=[]) or []:
        if action not in next_actions:
            next_actions.append(action)

    if recommendation_count is not None:
        notes.append(f"recommendations: {recommendation_count}")
    status_rows = count_items(rec_status)
    if status_rows is not None:
        notes.append(f"recommendation status rows: {status_rows}")
    if isinstance(validation, dict):
        validation_status = pick(validation, "status", default="unknown")
        notes.append(f"validation worker status: {validation_status}")
    if isinstance(batch_pause, dict):
        pause_status = pick(batch_pause, "status", "pause_status", default=None)
        if pause_status:
            notes.append(f"batch pause: {pause_status}")

    status = "needs_attention" if problems else "ok"
    return {
        "run_at": utc_now(),
        "mode": "pt_review",
        "real_trading": False,
        "status": status,
        "summary": "; ".join(problems[:3]) if problems else "Paper trader artifacts look ok from compact read-only review.",
        "metrics": {
            "pipeline_status": pipeline_status,
            "pipeline_inner_status": pipeline_inner,
            "consecutive_failures": consecutive_failures,
            "degraded_count": degraded_count,
            "active_count": active_count,
            "repair_active_count": repair_active_count,
            "recommendation_count": recommendation_count,
        },
        "problems": problems,
        "notes": notes,
        "next_actions": next_actions[:8],
        "sources": sources,
        "missing_optional_artifacts": missing,
        "contract": {
            "schema": "paper_trader.agent_contract.v1",
            "agent": "pt_review",
            "status": status,
            "run_at": utc_now(),
            "inputs": {"artifact_count": len(ARTIFACTS)},
            "outputs": {"source_count": len(sources), "missing_count": len(missing)},
            "warnings": problems,
            "next_actions": next_actions[:8],
            "metrics": {
                "consecutive_failures": consecutive_failures,
                "degraded_count": degraded_count,
                "recommendation_count": recommendation_count,
            },
        },
    }


def render_human(report: dict[str, Any]) -> str:
    status = report.get("status") or "unknown"
    metrics = report.get("metrics") or {}
    problems = report.get("problems") or []
    notes = report.get("notes") or []
    actions = report.get("next_actions") or []
    lines = [f"Paper trader review: {status}"]
    lines.append(
        "Pipeline: "
        f"{metrics.get('pipeline_status', 'unknown')}"
        f" / inner={metrics.get('pipeline_inner_status', 'unknown')}"
        f" / failures={metrics.get('consecutive_failures', 0)}"
        f" / degraded={metrics.get('degraded_count', 0)}"
    )
    lines.append(
        "Strategies: "
        f"active={metrics.get('active_count')}"
        f" / repair_active={metrics.get('repair_active_count')}"
        f" / recommendations={metrics.get('recommendation_count')}"
    )
    if problems:
        lines.append("Problems:")
        lines.extend(f"- {item}" for item in problems[:6])
    if notes:
        lines.append("Notes:")
        lines.extend(f"- {item}" for item in notes[:4])
    if actions:
        lines.append("Next actions:")
        lines.extend(f"- {item}" for item in actions[:4])
    return "\n".join(lines)


def save_report_to_db(key: str, report: dict[str, Any]) -> None:
    if save_latest_artifact is None:
        raise RuntimeError("app.database.save_latest_artifact is unavailable; DB write skipped")
    save_latest_artifact(
        key,
        report,
        artifact_path=None,
        status=report.get("status"),
        summary=str(report.get("summary") or "")[:1000],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only compact paper_trader pipeline review")
    parser.add_argument("--artifact-key", default="pt_review_latest", help="latest_artifacts key for DB output")
    parser.add_argument("--output", help="Deprecated alias: stores to DB key derived from this path, not to a file")
    parser.add_argument("--no-write", action="store_true", help="Do not write DB output artifact")
    parser.add_argument("--quiet", action="store_true", help="Only print one-line summary")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout for debugging")
    args = parser.parse_args()

    artifact_key = args.artifact_key
    if args.output:
        artifact_key = Path(args.output).name.removesuffix(".json")

    report = summarize_artifacts()
    if not args.no_write:
        save_report_to_db(artifact_key, report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.quiet:
        print(f"{report.get('status')}: {report.get('summary')}")
    else:
        print(render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
