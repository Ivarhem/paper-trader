#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, subprocess, sys, urllib.request
from datetime import datetime, timezone


def post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=180) as res:
        return json.loads(res.read())


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as res:
        return json.loads(res.read())


def evaluate(sweep: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, "tools/agents/agent_evaluator.py"],
        input=json.dumps(sweep),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run scheduled crypto lab dry-run: data update, sweep, evaluate")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--symbols", default="KRW-BTC,KRW-ETH")
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--output", default="/tmp/crypto_lab_latest.json")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    imports = []
    for symbol in symbols:
        imports.append(post_json(base + "/api/crypto/upbit/import", {"symbol": symbol, "timeframe": args.timeframe, "count": args.count}))
    context = get_json(base + "/api/external-context/latest")
    sweep = post_json(base + "/api/backtests/sweep", {"symbols": symbols, "limit": args.limit})
    evaluation = evaluate(sweep)
    packet = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry_run_no_real_orders",
        "imports": imports,
        "external_context": context.get("context"),
        "sweep_count": sweep.get("count"),
        "filtered_count": sweep.get("filtered_count"),
        "top_candidates": sweep.get("items", [])[: args.limit],
        "evaluation": evaluation,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(packet, f, ensure_ascii=False, indent=2)
    print(json.dumps({
        "run_at": packet["run_at"],
        "mode": packet["mode"],
        "symbols": symbols,
        "sweep_count": packet["sweep_count"],
        "filtered_count": packet["filtered_count"],
        "promoted": [i for i in evaluation.get("items", []) if i.get("promote")],
        "output": args.output,
    }, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
