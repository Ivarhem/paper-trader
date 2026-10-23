"""Offline, extractive evidence selection. No retrieval, LLM calls or DB writes.

Uses immutable document versions and exact text offsets. This borrows the RPL
separation principle; it does not implement its reinforcement-learning policy.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from urllib.parse import urlparse

SCHEMA = "paper_trader.news_evidence_selection.v1"
PROTECTED = {"correction", "counterargument", "retracted"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def instant(value):
    """Reject ambiguous dates: do not assume a timezone or same-day cutoff."""
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an explicit ISO datetime with timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp needs an explicit timezone")
    return result


def _claims(values):
    if not isinstance(values, list) or not values:
        raise ValueError("At least one explicit claim is required")
    claims, identities = [], set()
    for item in values:
        identity, text = item.get("claim_id"), item.get("text")
        if not isinstance(identity, str) or not identity.strip() or identity in identities:
            raise ValueError("Claims need unique nonempty claim_id values")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Claims need nonempty text")
        keywords = item.get("keywords", [])
        if not isinstance(keywords, list) or any(not isinstance(x, str) or not x.strip() for x in keywords):
            raise ValueError("keywords must be a list of nonempty strings")
        claims.append({"claim_id": identity, "text": text, "keywords": keywords})
        identities.add(identity)
    return claims


def capture(case):
    """Normalize only versions demonstrably available at the historical cutoff."""
    cutoff = instant(case["cutoff"])
    claims = _claims(case["claims"])
    documents, excluded, seen = [], [], {}
    for index, raw in enumerate(case["documents"]):
        if not isinstance(raw, dict):
            raise ValueError("Documents must be objects")
        sid = raw.get("source_id")
        if not isinstance(sid, str) or not sid.strip():
            raise ValueError("Every document needs an immutable source_id")
        identity_hash = sha(raw)
        if sid in seen:
            if seen[sid] != identity_hash:
                raise ValueError(f"Conflicting document versions share source_id {sid}")
            continue
        seen[sid] = identity_hash
        try:
            published = instant(raw.get("published_at"))
            available = instant(raw.get("available_at"))
            metadata = instant(raw.get("metadata_as_of", raw.get("available_at")))
        except (ValueError, TypeError):
            excluded.append({"source_id": sid, "reason": "unknown_or_ambiguous_time"})
            continue
        if any(t > cutoff for t in (published, available, metadata)):
            excluded.append({"source_id": sid, "reason": "after_cutoff"})
            continue
        if available < published:
            excluded.append({"source_id": sid, "reason": "availability_before_publication"})
            continue
        text, title, url = raw.get("text"), raw.get("title"), raw.get("url")
        try:
            parsed = urlparse(url) if isinstance(url, str) else None
            valid_url = parsed and parsed.scheme in ("http", "https") and parsed.hostname
        except ValueError:
            valid_url = False
        if not valid_url or not isinstance(title, str) or not title.strip() or not isinstance(text, str) or not text.strip():
            excluded.append({"source_id": sid, "reason": "missing_content_or_reference"})
            continue
        stance = raw.get("stance", "neutral")
        if stance not in {"support", "history", "neutral", *PROTECTED}:
            raise ValueError(f"Unknown evidence stance: {stance}")
        if raw.get("retracted") is True:
            stance = "retracted"
        correction = raw.get("superseded_by")
        if correction is not None and (not isinstance(correction, str) or not correction.strip()):
            raise ValueError("superseded_by must be a source ID or null")
        documents.append({"source_id": sid, "url": url, "title": title,
                          "published_at": raw["published_at"], "available_at": raw["available_at"],
                          "metadata_as_of": raw.get("metadata_as_of", raw["available_at"]),
                          "stance": stance, "superseded_by": correction,
                          "document_sha256": sha(text), "text": text})
    return claims, sorted(documents, key=lambda x: x["source_id"]), excluded


def passages(document, max_chars=600):
    """Split into verbatim spans; IDs bind source version and offsets."""
    text = document["text"]
    boundaries = [0] + [m.end() for m in re.finditer(r"(?<=[.!?。！？])\s+|\n+", text)] + [len(text)]
    metadata = {k: v for k, v in document.items() if k != "text"}
    for start, end in zip(boundaries, boundaries[1:]):
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        protected = document["stance"] in PROTECTED or bool(document["superseded_by"]) or bool(
            re.search(r"정정|철회|반론|반박|수정 공시|\b(?:correction|retract\w*|counterargument)\b", text[start:end], re.I))
        for left in range(start, end, max_chars):
            right = min(end, left + max_chars)
            excerpt = text[left:right]
            yield {**metadata, "evidence_id": "ev_" + sha([document["source_id"], document["document_sha256"], left, right])[:24],
                   "start": left, "end": right, "excerpt": excerpt, "protected": protected}


def relevance(claim, passage):
    terms = claim["keywords"] or re.findall(r"[\w가-힣]{2,}", claim["text"].casefold())
    body = (passage["title"] + " " + passage["excerpt"]).casefold()
    return sum(body.count(term.casefold()) for term in set(terms))


def select(case, *, max_context_chars=12000, max_passage_chars=600):
    if max_context_chars < 1 or max_passage_chars < 1:
        raise ValueError("Context and passage budgets must be positive")
    # Serialize first, ensuring that normalization cannot mutate caller objects.
    case = json.loads(encode(case))
    if not isinstance(case.get("documents"), list):
        raise ValueError("documents must be a list of captured versions")
    claims, documents, excluded = capture(case)
    candidates = [p for doc in documents for p in passages(doc, max_passage_chars)]
    header = {"cutoff": case["cutoff"], "claims": claims}
    def context(items):
        return {**header, "evidence": items}
    selected = {p["evidence_id"]: p for p in candidates if p["protected"]}
    # Cover each claim before spending remaining space on high overlap passages.
    for claim in claims:
        ranked = sorted(candidates, key=lambda p: (-relevance(claim, p), p["evidence_id"]))
        if ranked and relevance(claim, ranked[0]) > 0:
            selected[ranked[0]["evidence_id"]] = ranked[0]
    def ordered():
        return sorted(selected.values(), key=lambda p: (p["source_id"], p["start"]))
    ranked = sorted(candidates, key=lambda p: (-sum(relevance(c, p) for c in claims), p["evidence_id"]))
    for passage in ranked:
        if passage["evidence_id"] in selected or not any(relevance(c, passage) for c in claims):
            continue
        selected[passage["evidence_id"]] = passage
        if len(encode(context(ordered()))) > max_context_chars:
            del selected[passage["evidence_id"]]
    chosen = ordered()
    unresolved = [c["claim_id"] for c in claims if not any(relevance(c, p) for p in chosen)]
    reasons = []
    if unresolved:
        reasons.append("unresolved_claims")
    if len(encode(context(chosen))) > max_context_chars:
        reasons.append("protected_or_claim_evidence_exceeds_budget")
    if not documents:
        reasons.append("no_eligible_document_versions")
    full = {**header, "documents": documents}
    compact = context(chosen)
    return {"schema": SCHEMA, "mode": "shadow_only", "real_trading": False,
            "case_id": case.get("case_id"), "input_sha256": sha(case),
            "status": "blocked" if reasons else "ready_for_comparison",
            "reasons": reasons, "unresolved_claim_ids": unresolved,
            "excluded_documents": excluded, "eligible_source_ids": [d["source_id"] for d in documents],
            "full_context": full, "selected_context": compact,
            "claim_evidence": {c["claim_id"]: [p["evidence_id"] for p in chosen if relevance(c, p)] for c in claims},
            "metrics": {"full_context_chars": len(encode(full)), "selected_context_chars": len(encode(compact)),
                        "eligible_document_count": len(documents), "selected_passage_count": len(chosen),
                        "protected_passage_count": sum(p["protected"] for p in candidates)}}
