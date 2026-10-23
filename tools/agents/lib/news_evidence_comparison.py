"""Independent held-out comparison; incomplete evidence never enables rollout."""
from __future__ import annotations

from .news_evidence_selection import PROTECTED, encode, select, sha


def compare(cases, *, max_context_chars=12000, token_counter=None, tokenizer_name=None):
    if not isinstance(cases, list) or not cases:
        raise ValueError("Need at least one captured case")
    if len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("Case IDs must be unique")
    if token_counter is not None and not tokenizer_name:
        raise ValueError("Token measurements must name the tokenizer")
    rows = []
    full_tokens = selected_tokens = required_count = retained_count = special_missing = 0
    for case in cases:
        result = select(case, max_context_chars=max_context_chars)
        docs = {d["source_id"]: d for d in result["full_context"]["documents"]}
        selected = result["selected_context"]["evidence"]
        gold = case.get("required_evidence")
        invalid_gold, retained, specials = [], 0, 0
        if not isinstance(gold, list) or not gold:
            gold = []
        seen_gold = set()
        for annotation in gold:
            source_id, quote = annotation.get("source_id"), annotation.get("quote")
            doc = docs.get(source_id)
            if not doc or not isinstance(quote, str) or not quote.strip() or quote not in doc["text"]:
                invalid_gold.append({"source_id": source_id, "reason": "Gold quote absent from eligible original"})
                continue
            if (source_id, quote) in seen_gold:
                invalid_gold.append({"source_id": source_id, "reason": "Duplicate gold quote"})
                continue
            seen_gold.add((source_id, quote))
            kept = any(p["source_id"] == source_id and quote in p["excerpt"] for p in selected)
            retained += int(kept)
            # Count omission relative to what the full-context arm contained.
            if annotation.get("kind") in PROTECTED or doc["stance"] in PROTECTED or doc["superseded_by"]:
                specials += int(not kept)
        note = case.get("annotation") or {}
        human_reviewed = note.get("origin") == "human" and isinstance(note.get("reviewer"), str) and bool(note["reviewer"].strip())
        valid_gold = bool(gold) and not invalid_gold
        ft = token_counter(encode(result["full_context"])) if token_counter else None
        st = token_counter(encode(result["selected_context"])) if token_counter else None
        if ft is not None:
            full_tokens += ft
            selected_tokens += st
        required_count += len(gold) if valid_gold else 0
        retained_count += retained if valid_gold else 0
        special_missing += specials
        row = {k: v for k, v in result.items() if k not in ("full_context", "selected_context")}
        row.update({"dataset_kind": case.get("dataset_kind", "unknown"),
                    "human_annotation_declared": human_reviewed, "gold_valid": valid_gold,
                    "invalid_gold": invalid_gold, "required_count": len(gold) if valid_gold else None,
                    "retained_count": retained if valid_gold else None,
                    "required_retention": retained / len(gold) if valid_gold else None,
                    "additional_correction_counterargument_omissions": specials if valid_gold else None,
                    "full_input_tokens": ft, "selected_input_tokens": st})
        rows.append(row)
    gaps = []
    if len(cases) < 20:
        gaps.append("fewer_than_20_cases")
    if any(r["dataset_kind"] != "historical" for r in rows):
        gaps.append("historical_cases_not_complete")
    if any(not r["human_annotation_declared"] or not r["gold_valid"] for r in rows):
        gaps.append("human_required_evidence_not_complete")
    if token_counter is None:
        gaps.append("tokenizer_measurement_unavailable")
    if any(r["status"] != "ready_for_comparison" for r in rows):
        gaps.append("selection_blocked_or_unresolved")
    reduction = 1 - selected_tokens / full_tokens if token_counter and full_tokens else None
    retention = retained_count / required_count if required_count else None
    criteria = {"input_token_reduction_at_least_30pct": reduction >= .30 if reduction is not None else None,
                "required_evidence_retention_at_least_95pct": retention >= .95 if retention is not None else None,
                "no_additional_correction_counterargument_omission": special_missing == 0 if not any(not r["gold_valid"] for r in rows) else None}
    eligible = not gaps and all(v is True for v in criteria.values())
    return {"schema": "paper_trader.news_evidence_comparison.v1", "mode": "offline_comparison",
            "real_trading": False, "input_sha256": sha(cases), "case_count": len(cases),
            "tokenizer": tokenizer_name if token_counter else None,
            "full_input_tokens": full_tokens if token_counter else None,
            "selected_input_tokens": selected_tokens if token_counter else None,
            "input_token_reduction": reduction, "required_evidence_retention": retention,
            "additional_correction_counterargument_omissions": special_missing if all(r["gold_valid"] for r in rows) else None,
            "full_context_chars": sum(r["metrics"]["full_context_chars"] for r in rows),
            "selected_context_chars": sum(r["metrics"]["selected_context_chars"] for r in rows),
            "criteria": criteria, "remaining_gaps": gaps, "promotion_eligible": eligible,
            "decision": "eligible_for_review" if eligible else "not_eligible",
            "note": "This report does not change configuration or publish data; human annotation provenance is declared by the dataset, not authenticated.",
            "cases": rows}
