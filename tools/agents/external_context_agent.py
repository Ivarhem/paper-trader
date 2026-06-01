#!/usr/bin/env python3
"""External context packet builder for crypto strategy agents.

This tool intentionally does not trade. It turns externally gathered headlines/search
snippets into a compact risk context packet for other agents.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone

HIGH_KEYWORDS = [
    "hack", "exploit", "outage", "halt", "bankruptcy", "sec sues", "lawsuit",
    "liquidation cascade", "flash crash", "cpi", "fomc", "rate decision",
]
ELEVATED_KEYWORDS = [
    "liquidation", "etf outflow", "regulation", "fed", "yields", "nasdaq", "risk-off",
    "kimchi premium", "exchange", "stablecoin", "depeg",
]


def classify(text: str) -> dict:
    lower = text.lower()
    high_hits = [kw for kw in HIGH_KEYWORDS if kw in lower]
    elevated_hits = [kw for kw in ELEVATED_KEYWORDS if kw in lower]
    if high_hits:
        risk = "high"
        multiplier = 0.25
        allow = False
        regime = "risk_off"
    elif elevated_hits:
        risk = "elevated"
        multiplier = 0.5
        allow = True
        regime = "cautious"
    else:
        risk = "normal"
        multiplier = 1.0
        allow = True
        regime = "neutral"
    urls = re.findall(r"https?://\S+", text)
    notes = []
    if high_hits:
        notes.append("high-risk keywords: " + ", ".join(sorted(set(high_hits))))
    if elevated_hits:
        notes.append("elevated-risk keywords: " + ", ".join(sorted(set(elevated_hits))))
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "risk_level": risk,
        "market_regime": regime,
        "event_window": None,
        "strategy_adjustments": {
            "allow_new_entries": allow,
            "position_size_multiplier": multiplier,
            "prefer_timeframes": ["4h", "1d"] if risk in {"elevated", "high"} else ["1h", "4h", "1d"],
        },
        "notes": notes or ["no major external risk keyword detected in provided snippets"],
        "source_urls": urls[:10],
    }


def post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", default="-", help="Search snippets/headlines file, or stdin")
    ap.add_argument("--save", action="store_true", help="POST snapshot to paper_trader API")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    packet = classify(text)
    if args.save:
        saved = post_json(args.base_url.rstrip("/") + "/api/external-context/snapshots", packet)
        packet["snapshot_id"] = saved.get("id")
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
