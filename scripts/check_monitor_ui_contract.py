#!/usr/bin/env python3
"""Static regression checks for the monitor recommendation-card UI.

This intentionally avoids browser/test dependencies. It catches the recurring
class of regressions where a data/wording change reintroduces duplicate card
facts or breaks the price-plan layout contract.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MONITOR_JS = ROOT / "static" / "monitor.js"
STYLE_CSS = ROOT / "static" / "style.css"
RECOMMENDATIONS_JSON = ROOT / "static" / "recommendations_latest.json"
ORG_SUMMARY_JSON = ROOT / "static" / "research_org_suborg_summary_latest.json"


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def function_body(source: str, name: str) -> str:
    match = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source)
    require(match is not None, f"missing function {name}()")
    start = match.end()
    depth = 1
    i = start
    while i < len(source) and depth:
        char = source[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        i += 1
    require(depth == 0, f"could not parse function {name}()")
    return source[start : i - 1]


def check_monitor_source(js: str) -> None:
    render_card = function_body(js, "renderRecommendationCard")
    price_plan = function_body(js, "pricePlanBlock")
    evidence = function_body(js, "compactEvidenceStatus")

    require(render_card.count("pricePlanBlock(row)") == 1, "recommendation card must render exactly one price-plan block")
    require(render_card.count("compactEvidenceStatus(row, vb, blockers, human)") == 1, "recommendation card must render exactly one compact evidence strip")
    require("renderRiskSummary(row, blockers, human)" not in render_card, "core caution must live in evidence strip, not as a standalone price-adjacent card")
    require("function renderRiskSummary" not in js, "standalone core caution card renderer must not be reintroduced")

    require("핵심 주의" in evidence, "compact evidence strip must expose core caution beside committee/risk facts")
    require('<b>Risk Gate</b>' in evidence, "compact evidence strip must expose Risk Gate")
    require('<b>Gate</b>' not in evidence, "generic Gate label must not be reintroduced beside Risk Gate")
    require("조건 라벨" in evidence, "strategy/audit condition label should stay distinct from Risk Gate")

    context_pos = price_plan.find('class="price-plan-section context"')
    entry_pos = price_plan.find("entryPlanBlock(row)")
    exit_pos = price_plan.find('class="price-plan-section exit"')
    require(context_pos >= 0, "current price context section is missing")
    require(exit_pos >= 0, "exit/target price-plan section is missing")
    require(context_pos < entry_pos < exit_pos, "price sections must stay ordered as current price, entry plan, exit plan")

    context_block = price_plan[context_pos:entry_pos]
    exit_block = price_plan[exit_pos:]
    require("현재가" in context_block and "분석 기준가" in context_block, "current price must stay in its own context section")
    require("가격 계획" in exit_block and "무효화·손절 기준" in exit_block, "exit plan must keep target/stop content")
    require("last_price" not in exit_block and "current_price" not in exit_block, "current price fields must not move into the exit price-plan section")


def check_css_contract(css: str) -> None:
    require(".ui-system .rec-product-price-plan" in css, "missing recommendation price-plan layout rules")
    require(".ui-system .price-plan-section.context" in css, "missing current-price section styling")
    require(".ui-system .price-plan-section.exit" in css, "missing exit-plan section styling")
    require("align-items:stretch!important" in css, "price-plan sections must stretch to equal card height")
    require("grid-template-rows:auto 1fr!important" in css, "price-plan sections must share a header/content row contract")
    require("min-height:58px!important" in css, "price-plan cells must keep stable minimum height")
    final_price_grid = css.rsplit("/* 20260520 recommendation card price-grid polish", 1)[-1]
    final_price_grid = final_price_grid.split("@media(max-width:1320px)", 1)[0]
    require("align-items:start!important" not in final_price_grid, "price-plan layout must not revert to start alignment")
    require("align-self:start!important" not in final_price_grid, "price-plan sections must not opt out of equal-height stretching")
    require("align-self:stretch!important" in final_price_grid, "final price-plan override must keep sections stretched")
    require("minmax(118px,.26fr) minmax(0,1.74fr)" in final_price_grid, "desktop price-plan layout must keep current price as the left rail")
    require("grid-row:1/3!important" in final_price_grid, "current price must span the entry and price-plan rows")
    require(".ui-system .price-plan-section.entry{\n  grid-column:2!important;\n  grid-row:1!important;" in final_price_grid, "entry plan must occupy the first right-side row")
    require(".ui-system .price-plan-section.exit{\n  grid-column:2!important;\n  grid-row:2!important;" in final_price_grid, "exit price-plan section must occupy the second right-side row")
    require(".ui-system .rec-evidence-strip" in css, "missing compact evidence strip layout rules")
    require("repeat(4,minmax(0,1fr))" in css, "compact evidence strip should keep four visible facts")
    require(".ui-system .rec-evidence-strip>span:last-child" in css, "extra evidence fact should remain hidden in the primary card")
    require("@media(max-width:560px)" in css, "mobile single-column fallback is required")


def check_latest_recommendation_data() -> None:
    if not RECOMMENDATIONS_JSON.exists():
        return
    payload = json.loads(RECOMMENDATIONS_JSON.read_text(encoding="utf-8"))
    rows = payload.get("items") or []
    if len(rows) < 3:
        return

    sources = [str(r.get("recommendation_source_model") or r.get("target_price_source") or "") for r in rows]
    fund_backed = [s for s in sources if "fund" in s]
    require(len(fund_backed) >= max(1, len(rows) // 2), "latest recommendations no longer look fund-backed")

    missing_committee = [
        r.get("symbol")
        for r in rows
        if not (((r.get("investment_committee") or {}).get("synthesis") or {}).get("decision"))
    ]
    require(not missing_committee, f"latest recommendations missing investment committee decisions: {missing_committee[:5]}")

    stop_values = {
        round(float(r["downside_stop_pct"]), 2)
        for r in rows
        if isinstance(r.get("downside_stop_pct"), (int, float))
    }
    require(len(stop_values) > 1, "downside stop percentages collapsed to one repeated value")


def check_agent_guardrail_data() -> None:
    if not ORG_SUMMARY_JSON.exists():
        return
    payload = json.loads(ORG_SUMMARY_JSON.read_text(encoding="utf-8"))
    details: list[dict] = []

    def collect(value):
        if isinstance(value, dict):
            rows = value.get("managed_agent_details") or []
            if isinstance(rows, list):
                details.extend(row for row in rows if isinstance(row, dict))
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(payload)
    if not details:
        return
    guardrails = [str(row.get("guardrail") or "").strip() for row in details]
    missing = [row.get("agent_name") for row in details if not str(row.get("guardrail") or "").strip()]
    unique_guardrails = set(guardrails)
    generic_guardrails = [
        text for text in guardrails
        if text.startswith("proposal/context/validation output only")
        or text.startswith("paper recommendation evidence only")
        or text.startswith("historical/paper validation only")
        or text.startswith("data/context producer only")
        or text.startswith("market context is supporting evidence")
        or text.startswith("organization/governance output is proposal")
        or text.startswith("paper fund consensus is an overlay")
    ]
    require(not missing, f"managed agent guardrails missing: {missing[:8]}")
    require(len(unique_guardrails) >= min(40, len(details) // 2), "managed agent guardrails collapsed to repeated generic text")
    require(len(generic_guardrails) <= max(4, len(details) // 10), "too many managed agent guardrails still use generic fallback text")


def main() -> None:
    js = MONITOR_JS.read_text(encoding="utf-8")
    css = STYLE_CSS.read_text(encoding="utf-8")
    check_monitor_source(js)
    check_css_contract(css)
    check_latest_recommendation_data()
    check_agent_guardrail_data()
    print("OK: monitor UI contract")


if __name__ == "__main__":
    main()
