#!/usr/bin/env bash
set -euo pipefail
cd /service/services/paper_trader

LOG_PREFIX="$(date -Is)"
STATUS=/tmp/external_mover_validation_status.json
LOCK=/tmp/external_mover_validation_${USER:-clawd}.lock
STATE_LATEST=state/latest
TASK_STATE=scripts/agent_task_state.py
PAUSE_GUARD=scripts/batch_pause_guard.py
MOVER_OUT=/tmp/external_mover_seed_daily.out
VALIDATION_OUT=/tmp/external_mover_validation_daily.out
mkdir -p "$STATE_LATEST"

if ! python3 "$PAUSE_GUARD" check --task-id external-mover-validation --owner cron:external_mover_validation --skip-status "$STATUS" >/dev/null; then
  echo "$LOG_PREFIX external mover validation skip source edit pause"
  [ -f /tmp/paper_trader_batch_pause_latest.json ] && cp -f /tmp/paper_trader_batch_pause_latest.json "$STATE_LATEST/paper_trader_batch_pause_latest.json" || true
  exit 0
fi

load=$(cut -d" " -f1 /proc/loadavg)
if awk "BEGIN {exit !($load > 4.0)}"; then
  echo "$LOG_PREFIX external mover validation skip high load=$load"
  python3 "$TASK_STATE" skip --task-id external-mover-validation --owner cron:external_mover_validation --kind cron --scope "daily external gainer/volume top-N validation" --summary "skipped high load=$load" >/dev/null || true
  python3 - <<PY
import json, pathlib
p=pathlib.Path("$STATUS")
try: data=json.loads(p.read_text())
except Exception: data={}
data.update({"status":"skipped_high_load","last_skip_at":"$LOG_PREFIX","load":float("$load"),"reason":"load > 4.0"})
p.write_text(json.dumps(data,ensure_ascii=False,indent=2))
pathlib.Path("$STATE_LATEST/external_mover_validation_status.json").write_text(json.dumps(data,ensure_ascii=False,indent=2))
PY
  exit 0
fi

python3 "$TASK_STATE" start --task-id external-mover-validation --owner cron:external_mover_validation --kind cron --scope "daily external gainer/volume top-N validation" --files "scripts/run_daily_external_mover_validation_cron.sh,tools/agents/external_mover_validation_agent.py,tools/agents/market_mover_seed_agent.py" --checks "external_mover_validation_status" --summary "started daily external mover validation" >/dev/null || true

set +e
timeout 1800s flock -n -E 75 "$LOCK" bash -lc '
  set -euo pipefail
  .venv/bin/python tools/agents/market_mover_seed_agent.py --limit-per-market 160 --us-limit 160 --stock-only > /tmp/external_mover_seed_daily.out
  .venv/bin/python tools/agents/external_mover_validation_agent.py --top-n 80 --history-start 2022-01-01 --batch-size 900 --logic-limit 14 > /tmp/external_mover_validation_daily.out
'
rc=$?
set -e
echo "$LOG_PREFIX external mover validation done rc=$rc"

if [ "$rc" -eq 0 ]; then
  python3 "$TASK_STATE" complete --task-id external-mover-validation --owner cron:external_mover_validation --kind cron --scope "daily external gainer/volume top-N validation" --summary "completed daily external mover validation" --returncode "$rc" >/dev/null || true
elif [ "$rc" -eq 75 ]; then
  python3 "$TASK_STATE" skip --task-id external-mover-validation --owner cron:external_mover_validation --kind cron --scope "daily external gainer/volume top-N validation" --summary "skipped: already running" --returncode "$rc" >/dev/null || true
else
  python3 "$TASK_STATE" fail --task-id external-mover-validation --owner cron:external_mover_validation --kind cron --scope "daily external gainer/volume top-N validation" --summary "failed rc=$rc" --returncode "$rc" >/dev/null || true
fi

python3 - <<PY
import json, pathlib
status=pathlib.Path("$STATUS")
try: prev=json.loads(status.read_text())
except Exception: prev={}
err=""
for path in ("$MOVER_OUT", "$VALIDATION_OUT"):
    p=pathlib.Path(path)
    if p.exists() and int("$rc") != 0:
        err += f"\n{path}: " + p.read_text(errors="ignore")[-1600:]
payload={"status":"ok" if int("$rc") == 0 else ("skipped_lock" if int("$rc") == 75 else "failed"),"last_run_at":"$LOG_PREFIX","last_ok_at":"$LOG_PREFIX" if int("$rc") == 0 else prev.get("last_ok_at"),"last_fail_at":"$LOG_PREFIX" if int("$rc") not in (0,75) else prev.get("last_fail_at"),"consecutive_failures":0 if int("$rc") in (0,75) else int(prev.get("consecutive_failures") or 0)+1,"returncode":int("$rc"),"last_error_tail":err[-3000:]}
try:
    latest=json.loads(pathlib.Path("/tmp/external_mover_validation_latest.json").read_text())
    payload.update({"candidate_count": latest.get("candidate_count"),"symbols": latest.get("symbols"),"summary": latest.get("summary"),"contract_status": (latest.get("contract") or {}).get("status"),"warnings": latest.get("warnings")})
except Exception:
    pass
status.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
pathlib.Path("$STATE_LATEST/external_mover_validation_status.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2))
PY

[ -f /tmp/external_mover_validation_latest.json ] && cp -f /tmp/external_mover_validation_latest.json "$STATE_LATEST/external_mover_validation_latest.json" || true
[ -f /tmp/external_mover_simulation_validation_latest.json ] && cp -f /tmp/external_mover_simulation_validation_latest.json "$STATE_LATEST/external_mover_simulation_validation_latest.json" || true
[ -f /tmp/agent_task_state_latest.json ] && cp -f /tmp/agent_task_state_latest.json "$STATE_LATEST/agent_task_state_latest.json" || true
exit "$rc"
