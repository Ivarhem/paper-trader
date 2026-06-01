#!/usr/bin/env bash
set -euo pipefail
cd /service/services/paper_trader

STATUS=/tmp/validation_worker_status.json
PLANNER=/tmp/validation_capacity_planner_latest.json
WORKER=/tmp/simulation_validation_latest.json
LOCK=/tmp/paper_trader_validation_worker_${USER:-clawd}.lock
CAPACITY_OUT=/tmp/validation_capacity_worker_cron_${USER:-clawd}.out
CAPACITY_ERR=/tmp/validation_capacity_worker_cron_${USER:-clawd}.err
SIM_OUT=/tmp/simulation_validation_worker_cron_${USER:-clawd}.out
SIM_ERR=/tmp/simulation_validation_worker_cron_${USER:-clawd}.err
export CAPACITY_OUT CAPACITY_ERR SIM_OUT SIM_ERR
LOG_PREFIX="$(date -Is)"
PAUSE_GUARD=scripts/batch_pause_guard.py

if ! python3 "$PAUSE_GUARD" check --task-id validation-worker --owner cron:validation_worker --skip-status "$STATUS" >/dev/null; then
  echo "$LOG_PREFIX validation worker skip source edit pause"
  cp -f /tmp/paper_trader_batch_pause_latest.json static/paper_trader_batch_pause_latest.json 2>/dev/null || true
  exit 0
fi

load=$(cut -d" " -f1 /proc/loadavg)
if awk "BEGIN {exit !($load > 4.0)}"; then
  echo "$LOG_PREFIX validation worker skip high load=$load"
  python3 - <<PY
import json, pathlib
p=pathlib.Path("$STATUS")
try:
    data=json.loads(p.read_text())
except Exception:
    data={}
data.update({
    "status":"skipped_high_load",
    "last_skip_at":"$LOG_PREFIX",
    "load":float("$load"),
    "reason":"load > 4.0",
})
p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
pathlib.Path("static/validation_worker_status_latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
PY
  exit 0
fi

set +e
flock -n "$LOCK" bash -c '
  set -euo pipefail
  .venv/bin/python tools/agents/validation_capacity_planner.py --default-batch-size 300 --max-batch-size 900 > "$CAPACITY_OUT" 2>"$CAPACITY_ERR"
  batch=$(.venv/bin/python -c "import json; print(int(json.load(open(\"/tmp/validation_capacity_planner_latest.json\")).get(\"recommended_batch_size\") or 300))")
  if [ "$batch" -lt 1 ]; then
    batch=300
  fi
  .venv/bin/python tools/agents/simulation_validation_worker.py --batch-size "$batch" > "$SIM_OUT" 2>"$SIM_ERR"
'
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
  echo "$LOG_PREFIX validation worker failed or locked rc=$rc"
  python3 - <<PY
import json, pathlib
status=pathlib.Path("$STATUS")
try:
    data=json.loads(status.read_text())
except Exception:
    data={}
cf=int(data.get("consecutive_failures") or 0)+1
err_parts=[]
for path in ("$CAPACITY_ERR", "$SIM_ERR"):
    p=pathlib.Path(path)
    if p.exists():
        txt=p.read_text(errors="ignore").strip()
        if txt:
            err_parts.append(f"{path}: {txt[-1200:]}")
data.update({
    "status":"failed_or_locked",
    "last_fail_at":"$LOG_PREFIX",
    "consecutive_failures":cf,
    "last_error_tail":"\n".join(err_parts)[-2400:],
})
status.write_text(json.dumps(data, ensure_ascii=False, indent=2))
pathlib.Path("static/validation_worker_status_latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
PY
  exit $rc
fi

python3 - <<PY
import json, pathlib
planner_path=pathlib.Path("$PLANNER")
worker_path=pathlib.Path("$WORKER")
status_path=pathlib.Path("$STATUS")
try:
    planner=json.loads(planner_path.read_text())
except Exception as e:
    planner={"error":str(e)}
try:
    worker=json.loads(worker_path.read_text())
except Exception as e:
    worker={"error":str(e)}
try:
    prev=json.loads(status_path.read_text())
except Exception:
    prev={}
processed=int(worker.get("processed_combinations") or 0)
saved=worker.get("saved") or {}
problems=[]
if processed <= 0:
    problems.append("no validation combinations processed")
if planner.get("error"):
    problems.append(f"planner read error: {planner.get('error')}")
if worker.get("error"):
    problems.append(f"worker read error: {worker.get('error')}")
compact={
    "status":"needs_attention" if problems else "ok",
    "last_run_at":"$LOG_PREFIX",
    "last_ok_at":"$LOG_PREFIX" if not problems else prev.get("last_ok_at"),
    "consecutive_failures":0 if not problems else int(prev.get("consecutive_failures") or 0)+1,
    "load":float("$load"),
    "recommended_batch_size":planner.get("recommended_batch_size"),
    "cadence_recommendation":planner.get("cadence_recommendation"),
    "pending_results_estimate":planner.get("pending_results_estimate"),
    "coverage_pct":planner.get("coverage_pct"),
    "processed_combinations":processed,
    "saved":saved,
    "planned_total":worker.get("planned_total"),
    "problems":problems,
}
status_path.write_text(json.dumps(compact, ensure_ascii=False, indent=2))
static_path=pathlib.Path("static/validation_worker_status_latest.json")
static_payload=dict(compact)
static_payload["capacity"] = planner
static_payload["simulation"] = worker
static_payload["current_recommendation"] = {}
try:
    cur=pathlib.Path("/tmp/current_recommendation_validation_latest.json")
    if cur.exists():
        static_payload["current_recommendation"] = json.loads(cur.read_text())
except Exception:
    pass
static_path.write_text(json.dumps(static_payload, ensure_ascii=False, indent=2))
print(json.dumps(compact, ensure_ascii=False))
PY
