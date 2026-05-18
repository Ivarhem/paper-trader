#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, urllib.request
from datetime import datetime, timezone


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as res:
        return json.loads(res.read())


def post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read())


def decide_from_signal(signal: dict, context: dict) -> dict:
    if context and context.get("strategy_adjustments", {}).get("allow_new_entries") is False:
        return {"action":"BLOCKED", "reason":"external_context_blocks_new_entries"}
    crossover = signal.get("crossover_signal")
    rsi = signal.get("rsi_14")
    if crossover == "bullish":
        return {"action":"BUY", "reason":"ma_bullish_cross"}
    if crossover == "bearish":
        return {"action":"SELL", "reason":"ma_bearish_cross"}
    if rsi is not None and rsi <= 30:
        return {"action":"BUY", "reason":"rsi_oversold"}
    if rsi is not None and rsi >= 60:
        return {"action":"SELL", "reason":"rsi_reversion_exit"}
    return {"action":"HOLD", "reason":"no_strategy_trigger"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Record 15m forward-test paper signals; no real orders")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--symbols", default="KRW-BTC,KRW-ETH")
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--count", type=int, default=200)
    args = ap.parse_args()
    base = args.base_url.rstrip('/')
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    latest_context = get_json(base + "/api/external-context/latest")
    context = latest_context.get("context") or {}
    context_id = latest_context.get("id")
    results = []
    for symbol in symbols:
        post_json(base + "/api/crypto/upbit/import", {"symbol":symbol, "timeframe":args.timeframe, "count":args.count})
        sig = get_json(base + f"/api/signals/{symbol}")
        decision = decide_from_signal(sig, context)
        payload = {
            "signal_at": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "timeframe": args.timeframe,
            "strategy": "ma_rsi_forward_v1",
            "action": decision["action"],
            "price": sig.get("latest_close"),
            "reason": decision["reason"],
            "context_snapshot_id": context_id,
            "payload": {"signal": sig, "external_context": context},
        }
        saved = post_json(base + "/api/forward-signals", payload)
        results.append({"symbol": symbol, "action": decision["action"], "reason": decision["reason"], "price": sig.get("latest_close"), "id": saved.get("id")})
    print(json.dumps({"mode":"paper_forward_signal_only", "timeframe":args.timeframe, "results":results}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
