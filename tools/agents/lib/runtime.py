from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.agents.lib.agent_contract import write_json_shared


ROOT = Path(__file__).resolve().parents[3]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str | Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {} if default is None else default
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(target)}


def write_json_and_static(path: str | Path, payload: dict[str, Any]) -> None:
    write_json_shared(path, payload)
    static_path = ROOT / "static" / Path(path).name
    try:
        write_json_shared(static_path, payload)
    except Exception:
        pass
