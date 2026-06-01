#!/usr/bin/env python3
"""Persist latest JSON artifact files into latest_artifacts.

Compatibility file mirrors still exist, but the DB is the canonical source for
UI/API readers. Cron shell wrappers call this after writing compact status JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import save_latest_artifact


def artifact_key(path: Path) -> str:
    return path.name.removesuffix(".json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync latest JSON artifact files into DB")
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    synced: list[str] = []
    missing: list[str] = []
    failed: dict[str, str] = {}
    for raw in args.paths:
        path = Path(raw)
        if not path.exists():
            missing.append(str(path))
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                payload = {"items": payload}
            contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
            save_latest_artifact(
                artifact_key(path),
                payload,
                artifact_path=str(path),
                status=payload.get("status") or contract.get("status"),
                summary=str(payload.get("summary") or contract.get("summary") or "")[:1000],
            )
            synced.append(str(path))
        except Exception as exc:
            failed[str(path)] = str(exc)[:240]
    print(json.dumps({"synced": synced, "missing": missing, "failed": failed}, ensure_ascii=False, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
