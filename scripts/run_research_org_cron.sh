#!/usr/bin/env bash
set -euo pipefail
cd /service/services/paper_trader

STATUS=/tmp/research_pipeline_status.json
LATEST=/tmp/research_pipeline_latest.json
LOCK=/tmp/paper_trader_pipeline_${USER:-clawd}.lock
PIPELINE_OUT=/tmp/research_pipeline_cron_${USER:-clawd}.out
PIPELINE_ERR=/tmp/research_pipeline_cron_${USER:-clawd}.err
LOG_PREFIX="$(date -Is)"
STATE_LATEST=state/latest
TASK_STATE=scripts/agent_task_state.py
PAUSE_GUARD=scripts/batch_pause_guard.py
RUN_MODE="${1:-}"
BACKGROUND_PIPELINE="${PAPER_TRADER_PIPELINE_BACKGROUND:-1}"
BACKGROUND_OUT=/tmp/research_pipeline_background_${USER:-clawd}.out
BACKGROUND_ERR=/tmp/research_pipeline_background_${USER:-clawd}.err
if [ "$RUN_MODE" = "--foreground" ]; then
  BACKGROUND_PIPELINE=0
fi
mkdir -p "$STATE_LATEST"

persist_latest_outputs() {
  mkdir -p "$STATE_LATEST"
  for f in \
    /tmp/research_pipeline_status.json \
    /tmp/research_pipeline_latest.json \
    /tmp/strategy_novelty_pruner_latest.json \
    /tmp/recommendation_audit_latest.json \
    /tmp/recommendations_latest.json \
    /tmp/active_strategy_balancer_latest.json \
    /tmp/strategy_candidates_latest.json \
    /tmp/paper_fund_price_replay_latest.json \
    /tmp/investment_committee_latest.json \
    /tmp/stock_research_latest.json \
    /tmp/org_improvement_guardian_latest.json \
    /tmp/research_experiment_ledger_latest.json \
    /tmp/stock_research_near_miss_quarantine_latest.json \
    /tmp/stock_research_peer_basket_latest.json \
    /tmp/next_trade_issue_context_status.json \
    /tmp/next_trade_issue_context_latest.json \
    /tmp/agent_task_state_latest.json; do
    [ -f "$f" ] && cp -f "$f" "$STATE_LATEST/$(basename "$f")" || true
  done
  return 0
}

if ! python3 "$PAUSE_GUARD" check --task-id paper-trader-research-pipeline --owner cron:research_pipeline --skip-status "$STATUS" >/dev/null; then
  echo "$LOG_PREFIX skip research pipeline: source edit pause active"
  persist_latest_outputs
  exit 0
fi

load=$(cut -d" " -f1 /proc/loadavg)
if awk "BEGIN {exit !($load > 4.0)}"; then
  echo "$LOG_PREFIX skip high load=$load"
  python3 "$TASK_STATE" skip --task-id paper-trader-research-pipeline --owner cron:research_pipeline --kind cron --scope "hourly research pipeline" --summary "skipped high load=$load" >/dev/null || true
  python3 - <<PY
import json, pathlib
p=pathlib.Path("$STATUS")
try: data=json.loads(p.read_text())
except Exception: data={}
data.update({"status":"skipped_high_load","last_skip_at":"$LOG_PREFIX","load":float("$load"),"reason":"load > 4.0"})
p.write_text(json.dumps(data,ensure_ascii=False,indent=2))
PY
  persist_latest_outputs
  exit 0
fi

if [ "$BACKGROUND_PIPELINE" = "1" ]; then
  python3 "$TASK_STATE" start --task-id paper-trader-research-pipeline --owner cron:research_pipeline --kind cron --scope "hourly research pipeline" --files "scripts/run_research_org_cron.sh,tools/agents/research_pipeline_agent.py,configs/research_pipeline.yaml" --checks "research_pipeline_status,paper_trader_contract_check" --summary "delegated hourly research pipeline to background worker" >/dev/null || true
  nohup "$0" --foreground >"$BACKGROUND_OUT" 2>"$BACKGROUND_ERR" &
  echo "$LOG_PREFIX delegated research pipeline pid=$!"
  persist_latest_outputs
  exit 0
fi

python3 "$TASK_STATE" start --task-id paper-trader-research-pipeline --owner worker:research_pipeline --kind cron --scope "hourly research pipeline" --files "scripts/run_research_org_cron.sh,tools/agents/research_pipeline_agent.py,configs/research_pipeline.yaml" --checks "research_pipeline_status,paper_trader_contract_check" --summary "started hourly research pipeline foreground worker" >/dev/null || true

set +e
timeout 900s flock -n -E 75 "$LOCK" \
  .venv/bin/python tools/agents/research_pipeline_agent.py \
  --batch-size 300 \
  --max-batch-size 900 \
  --random-cutoffs 12 \
  --seed 42 \
  --skip-data-refresh \
  > "$PIPELINE_OUT" 2>"$PIPELINE_ERR"
rc=$?
set -e
if [ "$rc" -eq 75 ]; then
  echo "$LOG_PREFIX skip pipeline already running"
  python3 "$TASK_STATE" skip --task-id paper-trader-research-pipeline --owner worker:research_pipeline --kind cron --scope "hourly research pipeline" --summary "skipped: pipeline already running" --returncode "$rc" >/dev/null || true
  python3 - <<PY
import json, pathlib
status=pathlib.Path("$STATUS")
try: data=json.loads(status.read_text())
except Exception: data={}
data.update({"status":"skipped_lock","last_skip_at":"$LOG_PREFIX","reason":"pipeline already running"})
status.write_text(json.dumps(data,ensure_ascii=False,indent=2))
PY
  persist_latest_outputs
  exit 0
fi
if [ "$rc" -ne 0 ]; then
  echo "$LOG_PREFIX pipeline contract issue rc=$rc"
  python3 "$TASK_STATE" fail --task-id paper-trader-research-pipeline --owner worker:research_pipeline --kind cron --scope "hourly research pipeline" --summary "pipeline failed rc=$rc" --detail "$(tail -c 2000 "$PIPELINE_ERR" 2>/dev/null || true)" --returncode "$rc" >/dev/null || true
  cat "$PIPELINE_ERR" || true
  python3 - <<PY
import json, pathlib
status=pathlib.Path("$STATUS")
latest=pathlib.Path("$LATEST")
try: data=json.loads(status.read_text())
except Exception: data={}
cf=int(data.get("consecutive_failures") or 0)+1
err=pathlib.Path('$PIPELINE_ERR').read_text(errors='ignore')[-2000:]
try:
    d=json.loads(latest.read_text())
except Exception as exc:
    d={"status":"failed","summary":f"invalid latest pipeline artifact: {exc}"}
steps=d.get("steps") or []
failed_steps=[s for s in steps if s.get("status") in ("failed_required","failed_optional","error","failed")]
degraded_steps=[s for s in steps if s.get("status") == "degraded"]
rec=d.get("recommendations_summary") or {}
val=d.get("validation_summary") or {}
after_status=((d.get("after") or {}).get("strategy_status") or {})
actual_active_count=after_status.get("active", 0)
repair_active_count=after_status.get("repair_active", 0) + after_status.get("validation_active", 0)
compact={
 "status":"failed",
 "pipeline_status":d.get("status") or "failed",
 "last_run_at":d.get("run_at"),
 "last_ok_at":data.get("last_ok_at"),
 "last_fail_at":"$LOG_PREFIX",
 "consecutive_failures":cf,
 "active_count":actual_active_count,
 "repair_active_count":repair_active_count,
 "effective_research_active_count":actual_active_count + repair_active_count,
 "recommendation_active_strategy_count":rec.get("active_strategy_count"),
 "recommendation_repair_active_strategy_count":rec.get("repair_active_strategy_count"),
 "recommendation_count":rec.get("item_count"),
 "failure_count":len(failed_steps),
 "failed_agents":[
   {
     "agent":s.get("agent") or s.get("display_name") or s.get("name"),
     "status":s.get("status"),
     "returncode":s.get("returncode"),
     "warnings":(s.get("warnings") or [])[:5],
   }
   for s in failed_steps[:12]
 ],
 "degraded_count":len(degraded_steps),
 "degraded_agents":[
   {
     "agent":s.get("agent") or s.get("display_name") or s.get("name"),
     "status":s.get("status"),
     "warnings":(s.get("warnings") or [])[:5],
   }
   for s in degraded_steps[:12]
 ],
 "latest_cutoff":val.get("latest_cutoff"),
 "audit_preview_count":val.get("preview_count"),
 "summary":d.get("summary"),
 "problems":[f"{len(failed_steps)} pipeline agent(s) failed"] if failed_steps else ["pipeline failed"],
 "notes":[f"{len(degraded_steps)} optional agent(s) degraded"] if degraded_steps else [],
 "next_actions":d.get("next_actions") or [],
 "last_error_tail":err,
}
status.write_text(json.dumps(compact,ensure_ascii=False,indent=2))
PY
  persist_latest_outputs
  exit $rc
fi

python3 - <<PY
import json, pathlib
latest=pathlib.Path("$LATEST")
status=pathlib.Path("$STATUS")
try: d=json.loads(latest.read_text())
except Exception as e: d={"status":"invalid_latest","summary":str(e)}
try: prev=json.loads(status.read_text())
except Exception: prev={}
rec=d.get("recommendations_summary") or {}
val=d.get("validation_summary") or {}
after_status=((d.get("after") or {}).get("strategy_status") or {})
actual_active_count=after_status.get("active", 0)
repair_active_count=after_status.get("repair_active", 0) + after_status.get("validation_active", 0)
effective_research_active_count=actual_active_count + repair_active_count
state=d.get("status") or "unknown"
steps=d.get("steps") or []
degraded_steps=[s for s in steps if s.get("status") == "degraded"]
degraded_required=[s for s in degraded_steps if s.get("required", True)]
problems=[]
notes=[]
if state == "failed":
    problems.append("pipeline status failed")
elif state != "ok" and degraded_required:
    problems.append(f"pipeline status {state}")
elif state != "ok":
    notes.append(f"pipeline status {state}")
if degraded_required:
    problems.append(f"{len(degraded_required)} required agent(s) degraded")
elif degraded_steps:
    notes.append(f"{len(degraded_steps)} optional agent(s) degraded")
if (effective_research_active_count or 0) <= 0: problems.append("no active/repair-active strategies")
if (rec.get("item_count") or 0) <= 0: problems.append("no recommendations")
if (val.get("preview_count") or 0) <= 0:
    audit_best=val.get("best") or {}
    audit_verdict=audit_best.get("verdict")
    audit_filtered=val.get("items_total_filtered")
    if audit_verdict == "insufficient_samples" or audit_filtered == 0:
        notes.append("audit preview empty because audit has insufficient samples")
    else:
        problems.append("no audit preview")
cf=0 if not problems else int(prev.get("consecutive_failures") or 0)+1
compact={
 "status":"needs_attention" if problems else "ok",
 "pipeline_status":state,
 "last_run_at":d.get("run_at"),
 "last_ok_at":d.get("run_at") if not problems else prev.get("last_ok_at"),
 "consecutive_failures":cf,
 "active_count":actual_active_count,
 "repair_active_count":repair_active_count,
 "effective_research_active_count":effective_research_active_count,
 "recommendation_active_strategy_count":rec.get("active_strategy_count"),
 "recommendation_repair_active_strategy_count":rec.get("repair_active_strategy_count"),
 "recommendation_count":rec.get("item_count"),
 "degraded_count":len(degraded_steps),
 "degraded_agents":[
   {
     "agent":s.get("agent") or s.get("display_name") or s.get("name"),
     "status":s.get("status"),
     "warnings":(s.get("warnings") or [])[:5],
   }
   for s in degraded_steps[:12]
 ],
 "latest_cutoff":val.get("latest_cutoff"),
 "audit_preview_count":val.get("preview_count"),
 "summary":d.get("summary"),
 "problems":problems,
 "notes":notes,
 "next_actions":d.get("next_actions") or [],
}
status.write_text(json.dumps(compact,ensure_ascii=False,indent=2))
print(json.dumps(compact,ensure_ascii=False))
PY
pipeline_task_status=$(python3 - <<PY
import json, pathlib
try:
    print(json.loads(pathlib.Path("$STATUS").read_text()).get("status") or "unknown")
except Exception:
    print("unknown")
PY
)
if [ "$pipeline_task_status" = "ok" ]; then
  python3 "$TASK_STATE" complete --task-id paper-trader-research-pipeline --owner worker:research_pipeline --kind cron --scope "hourly research pipeline" --summary "pipeline verification completed" --returncode "$rc" >/dev/null || true
else
  python3 "$TASK_STATE" fail --task-id paper-trader-research-pipeline --owner worker:research_pipeline --kind cron --scope "hourly research pipeline" --summary "pipeline verification needs attention: $pipeline_task_status" --returncode "$rc" >/dev/null || true
fi
persist_latest_outputs
