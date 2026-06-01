#!/usr/bin/env bash
set -euo pipefail
cd /service/services/paper_trader

STATUS=/tmp/market_data_freshness_status.json
LOCK=/tmp/paper_trader_market_data_freshness_${USER:-clawd}.lock
MOVER_OUT=/tmp/market_data_mover_seed_${USER:-clawd}.out
MOVER_ERR=/tmp/market_data_mover_seed_${USER:-clawd}.err
UPPER_BACKFILL_OUT=/tmp/market_data_upper_limit_backfill_${USER:-clawd}.out
UPPER_BACKFILL_ERR=/tmp/market_data_upper_limit_backfill_${USER:-clawd}.err
PRICE_OUT=/tmp/market_data_price_refresh_${USER:-clawd}.out
PRICE_ERR=/tmp/market_data_price_refresh_${USER:-clawd}.err
DART_DISC_OUT=/tmp/market_data_opendart_disclosures_${USER:-clawd}.out
DART_DISC_ERR=/tmp/market_data_opendart_disclosures_${USER:-clawd}.err
SEC_DISC_OUT=/tmp/market_data_sec_edgar_disclosures_${USER:-clawd}.out
SEC_DISC_ERR=/tmp/market_data_sec_edgar_disclosures_${USER:-clawd}.err
DART_FIN_OUT=/tmp/market_data_opendart_financials_${USER:-clawd}.out
DART_FIN_ERR=/tmp/market_data_opendart_financials_${USER:-clawd}.err
export MOVER_OUT MOVER_ERR UPPER_BACKFILL_OUT UPPER_BACKFILL_ERR PRICE_OUT PRICE_ERR DART_DISC_OUT DART_DISC_ERR SEC_DISC_OUT SEC_DISC_ERR DART_FIN_OUT DART_FIN_ERR
LOG_PREFIX="$(date -Is)"
PAUSE_GUARD=scripts/batch_pause_guard.py

if ! python3 "$PAUSE_GUARD" check --task-id market-data-freshness --owner cron:market_data_freshness --skip-status "$STATUS" >/dev/null; then
  echo "$LOG_PREFIX market data freshness skip source edit pause"
  exit 0
fi

load=$(cut -d" " -f1 /proc/loadavg)
if awk "BEGIN {exit !($load > 4.0)}"; then
  echo "$LOG_PREFIX market data freshness skip high load=$load"
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
PY
  exit 0
fi

set +e
flock -n "$LOCK" bash -c '
  set -euo pipefail
  .venv/bin/python tools/agents/market_mover_seed_agent.py --limit-per-market 160 --us-limit 160 --stock-only > "$MOVER_OUT" 2>"$MOVER_ERR"
  upper_symbols=$(cat /tmp/market_mover_upper_limit_symbols.txt 2>/dev/null || true)
  if [ -n "$upper_symbols" ]; then
    .venv/bin/python tools/agents/import_stooq_daily.py --start 2019-01-01 --symbols "$upper_symbols" > "$UPPER_BACKFILL_OUT" 2>"$UPPER_BACKFILL_ERR"
  else
    printf "%s\n" "{\"skipped\":true,\"reason\":\"no upper-limit symbols\"}" > "$UPPER_BACKFILL_OUT"
    : > "$UPPER_BACKFILL_ERR"
  fi
  .venv/bin/python tools/agents/daily_price_refresh_agent.py --lookback-days 10 > "$PRICE_OUT" 2>"$PRICE_ERR" || true
  .venv/bin/python tools/agents/opendart_disclosure_agent.py --symbols active-kr --save > "$DART_DISC_OUT" 2>"$DART_DISC_ERR"
  .venv/bin/python tools/agents/sec_edgar_disclosure_agent.py --symbols active-us --save > "$SEC_DISC_OUT" 2>"$SEC_DISC_ERR"
  .venv/bin/python tools/agents/opendart_financial_agent.py --symbols active-kr --limit 100 --save > "$DART_FIN_OUT" 2>"$DART_FIN_ERR"
'
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
  echo "$LOG_PREFIX market data freshness failed or locked rc=$rc"
  python3 - <<PY
import json, pathlib
status=pathlib.Path("$STATUS")
try:
    data=json.loads(status.read_text())
except Exception:
    data={}
cf=int(data.get("consecutive_failures") or 0)+1
err_parts=[]
for path in (
    "$MOVER_ERR",
    "$UPPER_BACKFILL_ERR",
    "$PRICE_ERR",
    "$DART_DISC_ERR",
    "$SEC_DISC_ERR",
    "$DART_FIN_ERR",
):
    p=pathlib.Path(path)
    if p.exists():
        txt=p.read_text(errors="ignore").strip()
        if txt:
            err_parts.append(f"{path}: {txt[-1000:]}")
data.update({
    "status":"failed_or_locked",
    "last_fail_at":"$LOG_PREFIX",
    "consecutive_failures":cf,
    "last_error_tail":"\n".join(err_parts)[-3000:],
})
status.write_text(json.dumps(data, ensure_ascii=False, indent=2))
PY
  exit $rc
fi

python3 - <<PY
import json, pathlib

def read(path):
    p=pathlib.Path(path)
    try:
        return json.loads(p.read_text())
    except Exception as e:
        return {"error":str(e), "path":path}

status_path=pathlib.Path("$STATUS")
try:
    prev=json.loads(status_path.read_text())
except Exception:
    prev={}

mover=read("/tmp/market_mover_seed_latest.json")
upper_backfill=read("$UPPER_BACKFILL_OUT")
price=read("/tmp/daily_price_refresh_latest.json")
kr=read("/tmp/opendart_disclosures_latest.json")
us=read("/tmp/sec_edgar_disclosures_latest.json")
fin=read("/tmp/opendart_financials_latest.json")
problems=[]
if mover.get("error"):
    problems.append(f"mover seed output read error: {mover.get('error')}")
if price.get("error"):
    problems.append(f"price output read error: {price.get('error')}")
if kr.get("error"):
    problems.append(f"OpenDART disclosure output read error: {kr.get('error')}")
if us.get("error"):
    problems.append(f"SEC output read error: {us.get('error')}")
if fin.get("error"):
    problems.append(f"OpenDART financial output read error: {fin.get('error')}")
price_contract=price.get("contract") or {}
if price_contract.get("status") not in (None, "ok"):
    problems.extend(price_contract.get("warnings") or [])
compact={
    "status":"needs_attention" if problems else "ok",
    "last_run_at":"$LOG_PREFIX",
    "last_ok_at":"$LOG_PREFIX" if not problems else prev.get("last_ok_at"),
    "consecutive_failures":0 if not problems else int(prev.get("consecutive_failures") or 0)+1,
    "load":float("$load"),
    "mover_seed":{
        "run_at":mover.get("run_at"),
        "summary":mover.get("summary"),
        "top_upper_limit_symbols":[x.get("symbol") for x in (mover.get("top_upper_limit_items") or [])[:20]],
    },
    "upper_limit_backfill":{
        "skipped":upper_backfill.get("skipped"),
        "result_count":len(upper_backfill.get("results") or []),
        "symbols":[x.get("symbol") for x in (upper_backfill.get("results") or [])],
    },
    "price":{
        "run_at":price.get("run_at"),
        "symbol_count":price.get("symbol_count"),
        "refreshed_count":price.get("refreshed_count"),
        "failed_symbols":price.get("failed_symbols"),
        "max_lag_by_market_days":price.get("max_lag_by_market_days"),
    },
    "kr_disclosures":{
        "run_at":kr.get("run_at"),
        "event_count":len(kr.get("list") or []),
        "save_result":kr.get("save_result"),
        "missing_symbols":kr.get("missing_symbols"),
    },
    "us_disclosures":{
        "run_at":us.get("run_at"),
        "event_count":len(us.get("list") or []),
        "save_result":us.get("save_result"),
        "missing_symbols":us.get("missing_symbols"),
    },
    "kr_financials":{
        "run_at":fin.get("run_at"),
        "item_count":len(fin.get("items") or []),
        "saved":fin.get("saved"),
        "missing_symbols":fin.get("missing_symbols"),
    },
    "problems":problems,
}
status_path.write_text(json.dumps(compact, ensure_ascii=False, indent=2))
print(json.dumps(compact, ensure_ascii=False))
PY
