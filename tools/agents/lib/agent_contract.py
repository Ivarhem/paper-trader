from __future__ import annotations
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def contract(agent: str, status: str = "ok", inputs: dict[str, Any] | None = None, outputs: dict[str, Any] | None = None, warnings: list[str] | None = None, next_actions: list[str] | None = None, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema": "paper_trader.agent_contract.v1",
        "agent": agent,
        "status": status,
        "run_at": utc_now(),
        "inputs": inputs or {},
        "outputs": outputs or {},
        "metrics": metrics or {},
        "warnings": warnings or [],
        "next_actions": next_actions or [],
    }


def write_json_shared(path: str | Path, payload: dict[str, Any]) -> None:
    """Write JSON artifacts in a way that survives mixed cron/manual users."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.chmod(tmp_name, 0o666)
        os.replace(tmp_name, target)
        tmp_name = ""
        try:
            os.chmod(target, 0o666)
        except PermissionError:
            pass
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def write_contract(path: str | Path, payload: dict[str, Any]) -> None:
    write_json_shared(path, payload)


def attach_contract(packet: dict[str, Any], agent: str, status: str = "ok", inputs: dict[str, Any] | None = None, outputs: dict[str, Any] | None = None, warnings: list[str] | None = None, next_actions: list[str] | None = None, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    merged_outputs={"packet_keys": sorted(packet.keys())}
    if outputs:
        merged_outputs.update(outputs)
    packet["contract"] = contract(agent, status=status, inputs=inputs, outputs=merged_outputs, warnings=warnings, next_actions=next_actions, metrics=metrics)
    return packet
