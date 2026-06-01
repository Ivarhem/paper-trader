#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def run_json(cmd: list[str]) -> tuple[dict | None, str | None]:
    try:
        res = subprocess.run(cmd, text=True, capture_output=True, check=True, timeout=600)
        return json.loads(res.stdout), None
    except Exception as exc:
        detail = str(exc)
        if isinstance(exc, subprocess.CalledProcessError):
            detail += "\nSTDERR:\n" + (exc.stderr or "")[-2000:]
            detail += "\nSTDOUT:\n" + (exc.stdout or "")[-2000:]
        return None, detail


def role(name: str, status: str, **kwargs) -> dict:
    return {"role": name, "status": status, **kwargs}


def universe_curator() -> dict:
    data, err = run_json([sys.executable, "tools/agents/universe_curator.py", "--save"])
    if err:
        return role("Universe Curator", "error", error=err)
    return role("Universe Curator", "ok", counts=data.get("counts", {}), items=data.get("items", [])[:50])


def universe_scout(limit: int, use_scout: bool) -> tuple[dict, str | None]:
    if not use_scout:
        return role("Universe Scout", "skipped", reason="disabled"), None
    data, err = run_json([sys.executable, "tools/agents/universe_scout.py", "--limit", str(limit), "--exclude-risk"])
    if err:
        return role("Universe Scout", "error", error=err), None
    selected = data.get("selected", [])
    symbols = ",".join([item["symbol"] for item in selected]) if selected else None
    return role("Universe Scout", "ok", scanned_count=data.get("scanned_count"), candidate_count=data.get("candidate_count"), selected_count=len(selected), selected=selected), symbols


def data_agent(symbols: str, start: str, skip_import: bool) -> dict:
    if skip_import:
        return role("Data Agent", "skipped", reason="skip_import requested")
    data, err = run_json([sys.executable, "tools/agents/import_stooq_daily.py", "--symbols", symbols, "--start", start])
    if err:
        return role("Data Agent", "error", error=err)
    return role("Data Agent", "ok", imports=data.get("results", []))


def disclosure_agent(begin: str, end: str) -> dict:
    data, err = run_json([sys.executable, "tools/agents/opendart_disclosure_agent.py", "--begin", begin, "--end", end, "--save"])
    if err:
        return role("Disclosure Analyst", "error", error=err)
    save = data.get("save_result") or {}
    items = data.get("list", [])
    high = [i for i in items if any(k in (i.get("report_nm") or "") for k in ["거래정지", "상장폐지", "감자", "횡령", "배임"])]
    medium = [i for i in items if any(k in (i.get("report_nm") or "") for k in ["유상증자", "전환사채", "최대주주", "기재정정"])]
    positive = [i for i in items if any(k in (i.get("report_nm") or "") for k in ["자기주식취득", "수주", "공급계약", "배당", "무상증자"])]
    return role("Disclosure Analyst", "ok", fetched=len(items), saved=save, high_count=len(high), medium_count=len(medium), positive_count=len(positive), samples=[{"corp_name": i.get("corp_name"), "report_nm": i.get("report_nm"), "rcept_dt": i.get("rcept_dt")} for i in items[:5]])


def strategy_researcher(symbols: str, cutoffs: str) -> dict:
    data, err = run_json([sys.executable, "tools/agents/walk_forward_agent.py", "--symbols", symbols, "--cutoffs", cutoffs, "--min-train-bars", "250", "--min-test-bars", "60"])
    if err:
        return role("Strategy Researcher", "error", error=err)
    results = data.get("results", [])
    candidates = [r for r in results if r.get("status") == "ok"]
    promoted = [r for r in candidates if r.get("decision") == "promote"]
    return role("Strategy Researcher", "ok", disclosure_aware=data.get("disclosure_aware"), result_count=len(results), candidate_count=len(candidates), promoted_count=len(promoted), results=results)


def skeptic_agent(results: list[dict]) -> dict:
    objections=[]
    for r in results:
        if r.get("status") != "ok":
            continue
        test = r.get("out_of_sample_test") or {}
        reasons=[]
        if test.get("trade_count", 0) < 3:
            reasons.append("too few out-of-sample trades")
        if test.get("total_return_pct", -999) <= test.get("buy_hold_return_pct", 999):
            reasons.append("does not beat buy-and-hold")
        train = r.get("selected_train") or {}
        if train and test and train.get("total_return_pct", 0) - test.get("total_return_pct", 0) > 30:
            reasons.append("large train/test performance gap")
        if reasons:
            objections.append({"symbol": r.get("symbol"), "cutoff": r.get("cutoff"), "reasons": reasons})
    return role("Skeptic Agent", "ok", objection_count=len(objections), objections=objections[:50])


def risk_manager(results: list[dict]) -> dict:
    vetoes=[]; watches=[]
    for r in results:
        if r.get("status") != "ok":
            continue
        df = r.get("disclosure_features") or {}
        test = r.get("out_of_sample_test") or {}
        reasons=[]
        if df.get("high", 0) > 0:
            reasons.append("high-risk disclosure veto")
        if df.get("medium", 0) >= 2:
            reasons.append("multiple medium-risk disclosures")
        if test.get("max_drawdown_pct", 0) < -15:
            reasons.append("max drawdown breach")
        if reasons:
            vetoes.append({"symbol": r.get("symbol"), "cutoff": r.get("cutoff"), "reasons": reasons})
        elif df.get("positive", 0) > 0:
            watches.append({"symbol": r.get("symbol"), "cutoff": r.get("cutoff"), "reason": "positive disclosure support"})
    return role("Risk Manager", "ok", veto_count=len(vetoes), watch_count=len(watches), vetoes=vetoes[:50], watches=watches[:50])


def portfolio_manager(promoted: list[dict]) -> dict:
    symbols = sorted({r.get("symbol") for r in promoted if r.get("symbol")})
    return role("Portfolio Manager", "ok", promoted_symbol_count=len(symbols), symbols=symbols, note="correlation/position sizing skeleton; no real orders")


def recommendation_agent() -> dict:
    data, err = run_json([sys.executable, "tools/agents/recommendation_agent.py", "--limit", "10"])
    if err:
        return role("Recommendation Agent", "error", error=err)
    items=data.get("items", [])
    return role("Recommendation Agent", "ok", recommendation_count=len(items), items=items)


def investment_committee(strategy: dict, skeptic: dict, risk: dict, portfolio: dict) -> dict:
    results = strategy.get("results", [])
    promoted=[]; watchlist=[]; rejected=[]
    veto_keys={(v.get("symbol"), v.get("cutoff")) for v in risk.get("vetoes", [])}
    objection_keys={(o.get("symbol"), o.get("cutoff")) for o in skeptic.get("objections", [])}
    for r in results:
        if r.get("status") != "ok":
            continue
        key=(r.get("symbol"), r.get("cutoff"))
        if key in veto_keys:
            rejected.append({"symbol": key[0], "cutoff": key[1], "reason": "risk veto"})
        elif r.get("decision") == "promote" and key not in objection_keys:
            promoted.append(r)
        elif r.get("decision") == "promote":
            watchlist.append({"symbol": key[0], "cutoff": key[1], "reason": "promoted by strategy but skeptic objection"})
    return role("Investment Committee", "ok", promoted_count=len(promoted), watchlist_count=len(watchlist), rejected_count=len(rejected), promoted=promoted, watchlist=watchlist, rejected=rejected[:50])


def main():
    ap=argparse.ArgumentParser(description="Run stock research as an investment-organization style pipeline")
    ap.add_argument('--symbols', default='AAPL,MSFT,NVDA,SPY,QQQ,005930.KS,000660.KS,035420.KS')
    ap.add_argument('--cutoffs', default='2025-01-01,2026-01-01')
    ap.add_argument('--start', default='2019-01-01')
    ap.add_argument('--skip-import', action='store_true')
    ap.add_argument('--use-scout', action='store_true', help='Use Universe Scout output as research universe')
    ap.add_argument('--scout-limit', type=int, default=12)
    ap.add_argument('--disclosure-days', type=int, default=7)
    ap.add_argument('--output', default='/tmp/stock_research_org_latest.json')
    args=ap.parse_args()
    end=date.today()
    begin=end - timedelta(days=args.disclosure_days)
    curator = universe_curator()
    scout, scout_symbols = universe_scout(args.scout_limit, args.use_scout)
    effective_symbols = scout_symbols or args.symbols
    data=data_agent(effective_symbols, args.start, args.skip_import)
    disclosure=disclosure_agent(begin.isoformat(), end.isoformat())
    strategy=strategy_researcher(effective_symbols, args.cutoffs)
    results=strategy.get('results', []) if strategy.get('status') == 'ok' else []
    skeptic=skeptic_agent(results)
    risk=risk_manager(results)
    raw_promoted=[r for r in results if r.get('decision') == 'promote']
    portfolio=portfolio_manager(raw_promoted)
    recommendations=recommendation_agent()
    committee=investment_committee(strategy, skeptic, risk, portfolio)
    packet={
        'run_at': datetime.now(timezone.utc).isoformat(),
        'mode': 'investment_research_organization',
        'real_trading': False,
        'symbols': [s.strip().upper() for s in effective_symbols.split(',') if s.strip()],
        'cutoffs': [c.strip() for c in args.cutoffs.split(',') if c.strip()],
        'roles': [curator, scout, data, disclosure, strategy, skeptic, risk, portfolio, recommendations, committee],
        'summary': {
            'promoted_count': committee.get('promoted_count', 0),
            'watchlist_count': committee.get('watchlist_count', 0),
            'risk_veto_count': risk.get('veto_count', 0),
            'skeptic_objection_count': skeptic.get('objection_count', 0),
            'recommendation_count': recommendations.get('recommendation_count', 0),
        },
    }
    Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'run_at': packet['run_at'], 'mode': packet['mode'], 'summary': packet['summary'], 'output': args.output}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
