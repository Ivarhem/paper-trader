#!/usr/bin/env bash
set -euo pipefail
cd /service/services/paper_trader
LOG=/tmp/next_trade_issue_context_cron.log
STATUS=/tmp/next_trade_issue_context_status.json
LOCK=/tmp/next_trade_issue_context_${USER:-clawd}.lock
STATE_LATEST=state/latest
TASK_STATE=scripts/agent_task_state.py
PAUSE_GUARD=scripts/batch_pause_guard.py
mkdir -p "$STATE_LATEST"
{
  echo "$(date -Is) start next_trade_issue_context"
  if ! python3 "$PAUSE_GUARD" check --task-id next-trade-issue-context --owner cron:issue_context --skip-status "$STATUS" >/dev/null; then
    echo "$(date -Is) skip next_trade_issue_context: source edit pause active"
    [ -f /tmp/paper_trader_batch_pause_latest.json ] && cp -f /tmp/paper_trader_batch_pause_latest.json "$STATE_LATEST/paper_trader_batch_pause_latest.json" || true
    exit 0
  fi
  python3 "$TASK_STATE" start --task-id next-trade-issue-context --owner cron:issue_context --kind cron --scope "hourly issue collection feeding next trade context" --files "scripts/run_next_trade_issue_context_cron.sh,tools/agents/next_trade_issue_context_agent.py" --checks "next_trade_issue_context_status" --summary "started hourly issue context" >/dev/null || true
  set +e
  flock -n -E 75 "$LOCK" bash -lc '
    .venv/bin/python tools/agents/market_mover_seed_agent.py --limit-per-market 160 --us-limit 160 --stock-only >/tmp/market_mover_seed_hourly.out
    .venv/bin/python tools/agents/daily_price_refresh_agent.py --lookback-days 30 --active-limit 500 --watch-limit 200 --mover-seed-limit 260 --investor-flow-seed-limit 80 --chunk-size 30 >/tmp/discovery_price_refresh_hourly.out
    tools/agents/market_issue_scout_agent.py >/tmp/market_issue_scout_hourly.out
    tools/agents/market_news_issue_scout_agent.py --max-queries 24 >/tmp/market_news_issue_scout_hourly.out
    tools/agents/next_trade_issue_context_agent.py >/tmp/next_trade_issue_context_hourly.out
  '
  rc=$?
  set -e
  echo "$(date -Is) done next_trade_issue_context rc=$rc"
  if [ "$rc" -eq 0 ]; then
    python3 "$TASK_STATE" complete --task-id next-trade-issue-context --owner cron:issue_context --kind cron --scope "hourly issue collection feeding next trade context" --summary "issue context completed" --returncode "$rc" >/dev/null || true
  elif [ "$rc" -eq 75 ]; then
    python3 "$TASK_STATE" skip --task-id next-trade-issue-context --owner cron:issue_context --kind cron --scope "hourly issue collection feeding next trade context" --summary "skipped: already running" --returncode "$rc" >/dev/null || true
  else
    python3 "$TASK_STATE" fail --task-id next-trade-issue-context --owner cron:issue_context --kind cron --scope "hourly issue collection feeding next trade context" --summary "issue context failed rc=$rc" --returncode "$rc" >/dev/null || true
  fi
  python3 - <<PY
import json, pathlib
status = pathlib.Path("$STATUS")
try:
    prev = json.loads(status.read_text())
except Exception:
    prev = {}
err = ""
for path in ("/tmp/market_mover_seed_hourly.out", "/tmp/discovery_price_refresh_hourly.out", "/tmp/market_issue_scout_hourly.out", "/tmp/market_news_issue_scout_hourly.out", "/tmp/next_trade_issue_context_hourly.out"):
    p = pathlib.Path(path)
    if p.exists() and int("$rc") != 0:
        err += f"\n{path}: " + p.read_text(errors="ignore")[-1200:]
payload = {
    "status": "ok" if int("$rc") == 0 else ("skipped_lock" if int("$rc") == 75 else "failed"),
    "last_run_at": "$(date -Is)",
    "last_ok_at": "$(date -Is)" if int("$rc") == 0 else prev.get("last_ok_at"),
    "last_fail_at": "$(date -Is)" if int("$rc") not in (0, 75) else prev.get("last_fail_at"),
    "consecutive_failures": 0 if int("$rc") in (0, 75) else int(prev.get("consecutive_failures") or 0) + 1,
    "returncode": int("$rc"),
    "last_error_tail": err[-2400:],
}
try:
    latest = json.loads(pathlib.Path("/tmp/next_trade_issue_context_latest.json").read_text())
    payload.update({
        "item_count": latest.get("item_count"),
        "by_action": latest.get("by_action"),
        "summary": latest.get("summary"),
    })
except Exception:
    pass
status.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
pathlib.Path("$STATE_LATEST/next_trade_issue_context_status.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
PY
  [ -f /tmp/next_trade_issue_context_latest.json ] && cp -f /tmp/next_trade_issue_context_latest.json "$STATE_LATEST/next_trade_issue_context_latest.json" || true
  [ -f /tmp/agent_task_state_latest.json ] && cp -f /tmp/agent_task_state_latest.json "$STATE_LATEST/agent_task_state_latest.json" || true
  exit "$rc"
} >>"$LOG" 2>&1
