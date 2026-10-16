"""Deterministic, side-effect-free acceptance over a captured news packet.

This checks evidence structure, not whether a headline is factually true. Never
imports the application database, fetches a URL, or modifies the candidate.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlparse

REQUIREMENTS = {
    "NEWS-001": "Evidence-backed issues have usable source references",
    "NEWS-002": "Known retracted/superseded evidence is not reused",
    "NEWS-003": "Issue IDs are unique and declared counts match",
    "NEWS-004": "Only completed paper-research packets can be published",
    "NEWS-005": "Publication is bound to a fixed revision and run",
    "NEWS-006": "Cancellation and publication replay are fenced by the publisher",
}


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def evaluate(packet: dict, *, run_id: str, commit: str) -> dict:
    """Evaluate a serialized snapshot; equal inputs always yield equal reports."""
    findings = []

    def reject(requirement, path, reason):
        findings.append({"requirement_id": requirement, "path": path, "reason": reason})

    if not run_id or len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        reject("NEWS-005", "$", "Missing run ID or pinned Git revision")
    if not isinstance(packet, dict):
        reject("NEWS-004", "$", "Packet must be an object")
        packet = {}
    if packet.get("real_trading") is not False:
        reject("NEWS-004", "real_trading", "Explicit paper-only boundary required")
    contract = packet.get("contract")
    if not isinstance(contract, dict) or contract.get("status") not in ("ok", "degraded"):
        reject("NEWS-004", "contract.status", "Incomplete or failed execution")
    mode = packet.get("mode")
    if mode not in ("news_first_market_issue_scout", "market_issue_narrative"):
        reject("NEWS-004", "mode", "Unsupported news packet mode")
    issues = packet.get("issues")
    if not isinstance(issues, list) or not issues:
        reject("NEWS-004", "issues", "No analysis produced; preserve previous publication")
        issues = []
    summary = packet.get("summary")
    if not isinstance(summary, dict) or summary.get("issue_count") != len(issues):
        reject("NEWS-003", "summary.issue_count", "Count differs from captured issues")
    seen = set()
    for index, issue in enumerate(issues):
        prefix = f"issues[{index}]"
        if not isinstance(issue, dict):
            reject("NEWS-003", prefix, "Issue must be an object")
            continue
        identity = issue.get("issue_id")
        if not isinstance(identity, str) or not identity or identity in seen:
            reject("NEWS-003", prefix + ".issue_id", "Missing or repeated issue ID")
        else:
            seen.add(identity)
        # Price-only heuristic narratives are explicitly labelled as such. They
        # must not be silently upgraded to web-supported analysis without sources.
        heuristic = issue.get("narrative_status") == "heuristic_only" and mode != "news_first_market_issue_scout"
        sources = issue.get("sources") if mode == "news_first_market_issue_scout" else issue.get("narrative_sources")
        if not isinstance(sources, list):
            sources = []
        if not heuristic and not sources:
            reject("NEWS-001", prefix, "Missing evidence references")
        for si, source in enumerate(sources):
            path = f"{prefix}.sources[{si}]"
            if not isinstance(source, dict):
                reject("NEWS-001", path, "Invalid source object")
                continue
            url = source.get("url")
            try:
                parsed = urlparse(url) if isinstance(url, str) else None
                usable = parsed and parsed.scheme in ("http", "https") and parsed.hostname
            except ValueError:
                usable = False
            if not usable or not isinstance(source.get("title"), str) or not source["title"].strip():
                reject("NEWS-001", path, "Source needs an HTTP(S) URL and title")
            if source.get("retracted") or source.get("superseded_by"):
                reject("NEWS-002", path, "Known retraction/correction requires fresh analysis")
    return {"schema": "paper_trader.news_acceptance.v1", "decision": "block" if findings else "pass",
            "run_id": run_id, "commit": commit, "packet_sha256": digest(packet),
            "requirement_ids": sorted(REQUIREMENTS), "findings": findings}
