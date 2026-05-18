#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path

DEFAULT_SYMBOLS = {
    "005930.KS": "삼성전자",
    "000660.KS": "SK하이닉스",
    "035420.KS": "NAVER",
    "005380.KS": "현대차",
    "068270.KS": "셀트리온",
    "035720.KS": "카카오",
    "051910.KS": "LG화학",
}


def main():
    ap = argparse.ArgumentParser(description="Maintain Korean stock symbol metadata seed")
    ap.add_argument("--output", default="/tmp/krx_symbol_seed.json")
    args = ap.parse_args()
    packet = {"source": "seed; replace with KRX KIND/OpenDART corp-code sync", "symbols": DEFAULT_SYMBOLS}
    Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(packet, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
