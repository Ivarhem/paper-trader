#!/usr/bin/env python3
"""Shared task-state ledger for paper_trader agent/orchestration work."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_STATE = Path("state/agent_tasks.json")
DEFAULT_LATEST = Path("/tmp/agent_task_state_latest.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


@contextlib.contextmanager
def locked_state(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                data = json.loads(path.read_text()) if path.exists() else {}
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("tasks", {})
            data.setdefault("events", [])
            yield data
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def write_latest(data: dict[str, Any], latest_path: Path) -> None:
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = latest_path.with_suffix(latest_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, latest_path)


def compact_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    tasks = data.get("tasks") or {}
    by_status: dict[str, int] = {}
    for task in tasks.values():
        status = str(task.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    active = [t for t in tasks.values() if t.get("status") in {"in_progress", "blocked"}]
    active.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    recent = list(data.get("events") or [])[-20:]
    return {
        "run_at": now_iso(),
        "task_count": len(tasks),
        "by_status": by_status,
        "active_tasks": active[:12],
        "recent_events": recent,
        "contract": {
            "status": "ok",
            "metrics": {"active_task_count": len(active), "task_count": len(tasks)},
            "warnings": [],
            "next_actions": [],
        },
    }


def upsert_event(args: argparse.Namespace) -> dict[str, Any]:
    state_path = Path(args.state)
    latest_path = Path(args.latest)
    ts = now_iso()
    task_id = args.task_id
    with locked_state(state_path) as data:
        tasks = data.setdefault("tasks", {})
        task = tasks.get(task_id, {})
        previous_status = task.get("status")
        status = args.command
        if status == "start":
            status = "in_progress"
        elif status == "skip":
            status = "skipped"
        elif status == "fail":
            status = "failed"
        elif status == "complete":
            status = "completed"
        elif status == "block":
            status = "blocked"
        task.update(
            {
                "task_id": task_id,
                "status": status,
                "owner": args.owner or task.get("owner"),
                "kind": args.kind or task.get("kind"),
                "scope": args.scope or task.get("scope"),
                "files": parse_csv(args.files) or task.get("files") or [],
                "checks": parse_csv(args.checks) or task.get("checks") or [],
                "updated_at": ts,
            }
        )
        if args.command == "start":
            task.setdefault("started_at", ts)
            task["attempt_count"] = int(task.get("attempt_count") or 0) + 1
        if args.command in {"complete", "fail", "skip", "block"}:
            task["finished_at"] = ts
        if args.command == "fail":
            task["consecutive_failures"] = int(task.get("consecutive_failures") or 0) + 1
        elif args.command == "complete":
            task["consecutive_failures"] = 0
        if args.summary:
            task["summary"] = args.summary
        if args.detail:
            task["detail"] = args.detail[-4000:]
        if args.returncode is not None:
            task["returncode"] = args.returncode
        tasks[task_id] = task
        event = {
            "at": ts,
            "task_id": task_id,
            "event": args.command,
            "status": status,
            "previous_status": previous_status,
            "owner": task.get("owner"),
            "summary": args.summary,
            "returncode": args.returncode,
        }
        events = data.setdefault("events", [])
        events.append(event)
        del events[:-200]
        snapshot = compact_snapshot(data)
        data["last_snapshot"] = snapshot
    write_latest(snapshot, latest_path)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Update paper_trader shared agent task state")
    parser.add_argument("command", choices=["start", "complete", "fail", "skip", "block", "snapshot"])
    parser.add_argument("--task-id", default="paper-trader-ad-hoc")
    parser.add_argument("--owner", default="")
    parser.add_argument("--kind", default="")
    parser.add_argument("--scope", default="")
    parser.add_argument("--files", default="")
    parser.add_argument("--checks", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--detail", default="")
    parser.add_argument("--returncode", type=int)
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--latest", default=str(DEFAULT_LATEST))
    args = parser.parse_args()

    if args.command == "snapshot":
        state_path = Path(args.state)
        latest_path = Path(args.latest)
        with locked_state(state_path) as data:
            snapshot = compact_snapshot(data)
            data["last_snapshot"] = snapshot
        write_latest(snapshot, latest_path)
    else:
        snapshot = upsert_event(args)
    print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
