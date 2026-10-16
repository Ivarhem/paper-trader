"""Fail-closed publication boundary for news agents, with durable run fencing.

SQLite latest_artifacts is authoritative. File mirrors are replace-written while
holding the publication transaction. A crash between mirror replacement and DB
commit can leave a stale mirror; reconcile_mirrors repairs it from the DB.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

from app.database import get_connection, init_db, utc_now
from tools.agents.lib.news_acceptance import canonical, digest, evaluate

ROOT = Path(__file__).resolve().parents[3]


class PublicationBlocked(RuntimeError):
    pass


def code_revision() -> str:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT, text=True)
    if dirty:
        raise PublicationBlocked("News publication requires a clean, committed checkout")
    return revision


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS news_publication_runs (
        run_id TEXT PRIMARY KEY, commit_sha TEXT NOT NULL, status TEXT NOT NULL,
        targets_json TEXT NOT NULL, baseline_json TEXT NOT NULL,
        snapshots_json TEXT, reports_json TEXT, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")


def _file_hash(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _baseline(conn, paths):
    result = {}
    for path in paths:
        key = Path(path).stem
        row = conn.execute("SELECT payload_hash FROM latest_artifacts WHERE artifact_key=?", (key,)).fetchone()
        result[path] = {"db": row[0] if row else None, "file": _file_hash(path)}
    return result


def begin(paths: list[str]) -> str:
    """Capture revision and destinations BEFORE running any collection/analysis."""
    commit = code_revision()
    paths = [str(Path(p).resolve()) for p in paths]
    if not paths or len({Path(p).stem for p in paths}) != len(paths):
        raise ValueError("Publication targets must have distinct artifact keys")
    init_db()
    run_id = str(uuid4())
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _schema(conn)
        conn.execute("INSERT INTO news_publication_runs VALUES (?, ?, 'running', ?, ?, NULL, NULL, ?, ?)",
                     (run_id, commit, canonical(paths), canonical(_baseline(conn, paths)), utc_now(), utc_now()))
    return run_id


def cancel(run_id: str) -> bool:
    """Serialize cancellation with publication; a cancelled run cannot publish."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("UPDATE news_publication_runs SET status='cancelled', updated_at=? WHERE run_id=? AND status='running'", (utc_now(), run_id))
        return cursor.rowcount == 1


def _replace(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def publish(run_id: str, candidates: dict[str, dict]) -> dict:
    """Score immutable copies, then fence and promote every target together.

    Blocked candidates and diagnostics are kept outside latest_artifacts. There
    is deliberately no exception-swallowing fallback to a file-only publish.
    """
    snapshots = {str(Path(p).resolve()): json.loads(canonical(v)) for p, v in candidates.items()}
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM news_publication_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row or row["status"] != "running":
            raise PublicationBlocked("Run missing, cancelled, blocked, or already published")
        paths = json.loads(row["targets_json"])
        if set(paths) != set(snapshots):
            raise PublicationBlocked("Publication destinations differ from the captured run")
        reports = {p: evaluate(snapshots[p], run_id=run_id, commit=row["commit_sha"]) for p in paths}
        for path, report in reports.items():
            # A changed checkout, a newer publisher, or a modified legacy file
            # invalidates the whole multi-target publication.
            try:
                same_revision = code_revision() == row["commit_sha"]
            except (PublicationBlocked, subprocess.CalledProcessError):
                same_revision = False
            if not same_revision:
                report["findings"].append({"requirement_id": "NEWS-005", "path": "$", "reason": "Code changed during execution"})
            if _baseline(conn, [path])[path] != json.loads(row["baseline_json"])[path]:
                report["findings"].append({"requirement_id": "NEWS-005", "path": "$", "reason": "Newer publication or source modification; stale run rejected"})
            if report["findings"]:
                report["decision"] = "block"
        blocked = any(r["decision"] != "pass" for r in reports.values())
        conn.execute("UPDATE news_publication_runs SET status=?, snapshots_json=?, reports_json=?, updated_at=? WHERE run_id=?",
                     ("blocked" if blocked else "published", canonical(snapshots), canonical(reports), utc_now(), run_id))
        if not blocked:
            for path, packet in snapshots.items():
                packet["acceptance"] = reports[path]
                data = canonical(packet)
                conn.execute("""INSERT INTO latest_artifacts
                    (artifact_key, artifact_path, status, summary, payload_json, payload_hash, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(artifact_key) DO UPDATE SET
                    artifact_path=excluded.artifact_path, status=excluded.status, summary=excluded.summary,
                    payload_json=excluded.payload_json, payload_hash=excluded.payload_hash, updated_at=excluded.updated_at""",
                    (Path(path).stem, path, packet["contract"]["status"], str(packet.get("summary", ""))[:1000], data, digest(packet), utc_now()))
            # Readers prefer the transactional DB snapshot. Legacy file readers
            # cannot obtain a transactional multi-file snapshot; see contract.
            previous = {p: Path(p).read_text(encoding="utf-8") if Path(p).exists() else None for p in snapshots}
            replaced = []
            try:
                for path, packet in snapshots.items():
                    _replace(path, canonical(packet))
                    replaced.append(path)
            except Exception:
                # Ordinary I/O failures restore the old mirrors before the DB
                # rolls back. Power loss still requires explicit reconciliation.
                for path in reversed(replaced):
                    if previous[path] is None:
                        Path(path).unlink(missing_ok=True)
                    else:
                        _replace(path, previous[path])
                raise
    result = {"run_id": run_id, "status": "blocked" if blocked else "published", "reports": reports}
    if blocked:
        raise PublicationBlocked(canonical(result))
    return result


def reconcile_mirrors(run_id: str) -> None:
    """Restore canonical latest DB state, never an old run's candidate."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT targets_json FROM news_publication_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise PublicationBlocked("Unknown run")
        for path in json.loads(row[0]):
            latest = conn.execute("SELECT payload_json FROM latest_artifacts WHERE artifact_key=?", (Path(path).stem,)).fetchone()
            if latest:
                _replace(path, latest[0])
