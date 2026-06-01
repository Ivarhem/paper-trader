from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.agents.lib.agent_contract import write_json_shared


ROOT = Path(__file__).resolve().parents[3]


def artifact_key_from_path(path: str | Path) -> str:
    return Path(path).name.removesuffix(".json")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str | Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    target = Path(path)
    key = artifact_key_from_path(target)
    try:
        from app.database import latest_artifact

        db_payload = latest_artifact(key)
        if db_payload is not None:
            return db_payload
    except Exception:
        pass
    fallback_paths: list[Path] = []
    if target.is_absolute() and target.parts[:2] == ("/", "tmp"):
        fallback_paths.extend([
            ROOT / "static" / target.name,
            ROOT / "state" / "latest" / target.name,
        ])
    elif not target.is_absolute():
        fallback_paths.extend([
            ROOT / "static" / target.name,
            ROOT / "state" / "latest" / target.name,
        ])
    for fallback in fallback_paths:
        if fallback == target or not fallback.exists():
            continue
        try:
            return json.loads(fallback.read_text(encoding="utf-8"))
        except Exception:
            continue
    if not target.exists():
        return {} if default is None else default
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(target)}



def write_json(path: str | Path, payload: dict[str, Any], *, mirror_file: bool = True) -> None:
    try:
        from app.database import save_latest_artifact

        save_latest_artifact(
            artifact_key_from_path(path),
            payload,
            artifact_path=str(path),
            status=payload.get("status") if isinstance(payload, dict) else None,
            summary=str(payload.get("summary") or "")[:1000] if isinstance(payload, dict) else None,
        )
    except Exception:
        pass
    if mirror_file:
        write_json_shared(path, payload)

def write_json_and_static(path: str | Path, payload: dict[str, Any]) -> None:
    try:
        from app.database import save_latest_artifact

        save_latest_artifact(
            artifact_key_from_path(path),
            payload,
            artifact_path=str(path),
            status=payload.get("status") if isinstance(payload, dict) else None,
            summary=str(payload.get("summary") or "")[:1000] if isinstance(payload, dict) else None,
        )
    except Exception:
        pass
    write_json_shared(path, payload)
    static_path = ROOT / "static" / Path(path).name
    try:
        write_json_shared(static_path, payload)
    except Exception:
        pass
