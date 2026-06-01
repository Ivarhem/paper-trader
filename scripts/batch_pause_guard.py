#!/usr/bin/env python3
"""Shared pause gate for paper_trader cron/batch writers."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_STATE = Path("state/batch_pause_guard.json")
DEFAULT_LATEST = Path("/tmp/paper_trader_batch_pause_latest.json")
DEFAULT_STATUS = Path("/tmp/paper_trader_batch_pause_status.json")
DEFAULT_TTL_SECONDS = 7200
TASK_STATE = Path("scripts/agent_task_state.py")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KNOWN_PROCESS_PATTERNS = [
    "tools/agents/research_pipeline_agent.py",
    "tools/agents/validation_capacity_planner.py",
    "tools/agents/simulation_validation_worker.py",
    "tools/agents/market_mover_seed_agent.py",
    "tools/agents/daily_price_refresh_agent.py",
    "tools/agents/market_issue_scout_agent.py",
    "tools/agents/market_news_issue_scout_agent.py",
    "tools/agents/next_trade_issue_context_agent.py",
    "tools/agents/external_mover_validation_agent.py",
    "tools/agents/import_stooq_daily.py",
    "tools/agents/opendart_disclosure_agent.py",
    "tools/agents/sec_edgar_disclosure_agent.py",
    "tools/agents/opendart_financial_agent.py",
]

def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()

def iso(ts: datetime | None = None) -> str:
    return (ts or now()).isoformat(timespec="seconds")

def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None

@contextlib.contextmanager
def locked_json(path: Path):
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
            data.setdefault("events", [])
            yield data
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

def compact(data: dict[str, Any]) -> dict[str, Any]:
    active = bool(data.get("active"))
    expires_at = parse_time(data.get("expires_at"))
    stale = bool(active and expires_at and expires_at <= now())
    if stale:
        active = False
    return {
        "status": "paused" if active else ("expired" if stale else "open"),
        "active": active,
        "owner": data.get("owner"),
        "reason": data.get("reason"),
        "started_at": data.get("started_at"),
        "expires_at": data.get("expires_at"),
        "released_at": data.get("released_at"),
        "last_check_at": iso(),
        "events": list(data.get("events") or [])[-20:],
        "contract": {
            "status": "source_edit_pause" if active else "ok",
            "warnings": ["batch writers are paused for source edits"] if active else [],
            "metrics": {"active": active},
        },
    }

def write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        from app.database import save_latest_artifact

        save_latest_artifact(
            path.name.removesuffix(".json"),
            payload,
            artifact_path=str(path),
            status=((payload.get("contract") or {}).get("status") if isinstance(payload.get("contract"), dict) else payload.get("status")),
            summary=str(payload.get("reason") or payload.get("status") or "")[:1000],
        )
    except Exception as exc:
        payload.setdefault("contract", {}).setdefault("warnings", []).append(f"latest_artifact_db_write_failed: {str(exc)[:180]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)

def write_skip_status(path: Path | None, pause: dict[str, Any], task_id: str) -> None:
    if not path:
        return
    try:
        previous = json.loads(path.read_text())
    except Exception:
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    payload = dict(previous)
    payload.update({
        "status": "skipped_source_edit_pause",
        "last_skip_at": pause["last_check_at"],
        "last_ok_at": previous.get("last_ok_at"),
        "consecutive_failures": previous.get("consecutive_failures", 0),
        "reason": pause.get("reason") or "source edit pause active",
        "pause_owner": pause.get("owner"),
        "pause_started_at": pause.get("started_at"),
        "pause_expires_at": pause.get("expires_at"),
        "task_id": task_id,
    })
    write_json(path, payload)

def record_task_skip(task_id: str, owner: str, reason: str) -> None:
    if not TASK_STATE.exists():
        return
    subprocess.run([
        "python3", str(TASK_STATE), "skip",
        "--task-id", task_id,
        "--owner", owner,
        "--kind", "cron",
        "--scope", "batch paused during source edits",
        "--summary", reason[:500],
        "--returncode", "75",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

def stop_known_processes() -> list[dict[str, Any]]:
    current = os.getpid()
    try:
        out = subprocess.check_output(["pgrep", "-af", "paper_trader"], text=True)
    except subprocess.CalledProcessError:
        return []
    stopped: list[dict[str, Any]] = []
    for line in out.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid = int(parts[0])
        cmd = parts[1]
        if pid == current or "batch_pause_guard.py" in cmd:
            continue
        if not any(pattern in cmd for pattern in KNOWN_PROCESS_PATTERNS):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append({"pid": pid, "signal": "TERM", "cmd": cmd[:300]})
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            stopped.append({"pid": pid, "error": str(exc), "cmd": cmd[:300]})
    return stopped

def enter(args: argparse.Namespace) -> dict[str, Any]:
    deadline = now() + timedelta(seconds=args.ttl_seconds)
    with locked_json(Path(args.state)) as data:
        data.update({
            "active": True,
            "owner": args.owner,
            "reason": args.reason,
            "started_at": iso(),
            "expires_at": iso(deadline),
            "released_at": None,
        })
        event = {"at": iso(), "event": "enter", "owner": args.owner, "reason": args.reason, "expires_at": data["expires_at"]}
        if args.stop_running:
            event["stopped_processes"] = stop_known_processes()
        data["events"].append(event)
        del data["events"][:-100]
        payload = compact(data)
    write_json(Path(args.latest), payload)
    write_json(Path(args.status), payload)
    return payload

def leave(args: argparse.Namespace) -> dict[str, Any]:
    with locked_json(Path(args.state)) as data:
        data.update({"active": False, "released_at": iso(), "release_owner": args.owner})
        data["events"].append({"at": iso(), "event": "leave", "owner": args.owner, "reason": args.reason})
        del data["events"][:-100]
        payload = compact(data)
    write_json(Path(args.latest), payload)
    write_json(Path(args.status), payload)
    return payload

def check(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    with locked_json(Path(args.state)) as data:
        expires_at = parse_time(data.get("expires_at"))
        if data.get("active") and expires_at and expires_at <= now():
            data.update({"active": False, "released_at": iso(), "release_owner": "batch_pause_guard:ttl"})
            data["events"].append({"at": iso(), "event": "expire", "owner": "batch_pause_guard", "reason": "ttl elapsed"})
        payload = compact(data)
    write_json(Path(args.latest), payload)
    if args.status:
        write_json(Path(args.status), payload)
    if payload["active"]:
        reason = payload.get("reason") or "source edit pause active"
        write_skip_status(Path(args.skip_status) if args.skip_status else None, payload, args.task_id)
        if args.task_id:
            record_task_skip(args.task_id, args.owner or "batch_pause_guard", reason)
        return 75, payload
    return 0, payload

def main() -> int:
    parser = argparse.ArgumentParser(description="Pause paper_trader batch writers during source edits")
    parser.add_argument("command", choices=["enter", "leave", "check", "status"])
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--latest", default=str(DEFAULT_LATEST))
    parser.add_argument("--status", default=str(DEFAULT_STATUS))
    parser.add_argument("--skip-status", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--owner", default="manual")
    parser.add_argument("--reason", default="source edit in progress")
    parser.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    parser.add_argument("--stop-running", action="store_true")
    args = parser.parse_args()
    if args.command == "enter":
        rc, payload = 0, enter(args)
    elif args.command == "leave":
        rc, payload = 0, leave(args)
    elif args.command == "check":
        rc, payload = check(args)
    else:
        with locked_json(Path(args.state)) as data:
            payload = compact(data)
        write_json(Path(args.latest), payload)
        write_json(Path(args.status), payload)
        rc = 0
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return rc

if __name__ == "__main__":
    raise SystemExit(main())
