#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, urllib.request


def post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=120) as res:
        return json.loads(res.read())


def main() -> int:
    ap = argparse.ArgumentParser(description="Run crypto backtest sweep against paper_trader API")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--symbols", default="KRW-BTC,KRW-ETH")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    payload = {"symbols":[s.strip().upper() for s in args.symbols.split(',') if s.strip()], "limit": args.limit}
    result = post_json(args.base_url.rstrip('/') + "/api/backtests/sweep", payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
