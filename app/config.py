from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    app_version: str
    app_root_path: str
    database_path: str
    initial_cash: float


def get_settings() -> Settings:
    root_dir = Path(__file__).resolve().parent.parent
    load_env_file(root_dir / ".env")
    return Settings(
        app_name=os.getenv("APP_NAME", "Paper Trader"),
        app_env=os.getenv("APP_ENV", "development"),
        app_version=os.getenv("APP_VERSION", "0.1.0"),
        app_root_path=os.getenv("APP_ROOT_PATH", ""),
        database_path=os.getenv("DATABASE_PATH", str(root_dir / "paper_trader.db")),
        initial_cash=float(os.getenv("INITIAL_CASH", "100000")),
    )
