#!/usr/bin/env python3
"""Offline shadow selection / A-B input comparison. Never updates latest views."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.agents.lib.news_evidence_selection import encode, select
from tools.agents.lib.news_evidence_comparison import compare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="New comparison artifact only; refuses overwrite")
    parser.add_argument("--compare", action="store_true", help="Input contains a list of captured cases")
    parser.add_argument("--max-context-chars", type=int, default=12000)
    parser.add_argument("--tokenizer", help="Optional tiktoken encoding, e.g. cl100k_base; requires tiktoken")
    args = parser.parse_args()
    if args.output and (args.output.exists() or args.output.name.endswith("_latest.json") or args.output.resolve() == args.input.resolve()):
        parser.error("Use a new shadow report path, never an existing input/latest view")
    counter = None
    if args.tokenizer:
        try:
            import tiktoken
        except ImportError:
            parser.error("Install the optional tiktoken package to measure tokens; no estimates substituted")
        encoding = tiktoken.get_encoding(args.tokenizer)
        counter = lambda text: len(encoding.encode(text, disallowed_special=()))
    case = json.loads(args.input.read_text(encoding="utf-8"))
    if args.compare:
        result = compare(case, max_context_chars=args.max_context_chars,
                         token_counter=counter, tokenizer_name=args.tokenizer)
    else:
        result = select(case, max_context_chars=args.max_context_chars)
    from tools.agents.lib.news_evidence_selection import sha
    modules = [Path(__file__), Path(__file__).parent / 'lib/news_evidence_selection.py',
               Path(__file__).parent / 'lib/news_evidence_comparison.py']
    result['implementation_sha256'] = sha({p.name: p.read_text(encoding='utf-8') for p in modules})
    data = encode(result) + "\n"
    if args.output:
        # Exclusive create also protects against a path appearing after validation.
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(data)
    print(data, end="")
    # Input sizing success is not deployment acceptance. Missing human labels,
    # tokens or real cases intentionally return a nonzero comparison status.
    return (0 if result["promotion_eligible"] else 2) if args.compare else (0 if result["status"] == "ready_for_comparison" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
