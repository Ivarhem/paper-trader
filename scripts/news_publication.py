#!/usr/bin/env python3
"""Inspect/cancel news runs, repair mirrors, or replay a captured packet."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from tools.agents.lib.news_acceptance import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "cancel", "reconcile"):
        commands.add_parser(command).add_argument("run_id")
    replay = commands.add_parser("evaluate")
    replay.add_argument("snapshot", type=Path)
    replay.add_argument("--run-id", required=True)
    replay.add_argument("--commit", required=True)
    args = parser.parse_args()
    if args.command == "evaluate":
        report = evaluate(json.loads(args.snapshot.read_text()), run_id=args.run_id, commit=args.commit)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["decision"] == "pass" else 1
    if args.command == "status":
        # Status lookup must not initialize/seed the application database.
        path = Path(get_settings().database_path).resolve()
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT run_id, commit_sha, status, reports_json, created_at, updated_at FROM news_publication_runs WHERE run_id=?", (args.run_id,)).fetchone()
        print(json.dumps(dict(row) if row else {"status": "not_found"}, ensure_ascii=False))
        return 0 if row else 1
    from tools.agents.lib.news_publication import cancel, reconcile_mirrors
    if args.command == "cancel":
        changed = cancel(args.run_id)
        print(json.dumps({"cancelled": changed, "run_id": args.run_id}))
        return 0 if changed else 1
    reconcile_mirrors(args.run_id)
    print(json.dumps({"reconciled": True, "run_id": args.run_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
