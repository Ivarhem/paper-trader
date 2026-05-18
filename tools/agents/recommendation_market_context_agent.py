#!/usr/bin/env python3
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.database import get_connection
from tools.agents.lib.agent_contract import attach_contract

def read_json(p):
    try: return json.load(open(p))
    except Exception: return {}

def pct(a,b): return round((a/b-1)*100,2) if a and b else None

def latest_bars(conn,sym,n=65):
    return [dict(r) for r in conn.execute("select date,open,high,low,close,volume from price_bars where timeframe='1d' and symbol=? order by date desc limit ?",(sym,n)).fetchall()][::-1]

def ret(bars,days):
    if len(bars)<=days: return None
    return pct(float(bars[-1]['close']), float(bars[-1-days]['close']))

def vol_ratio(bars,days=20):
    if len(bars)<days+1: return None
    avg=sum(float(x.get('volume') or 0) for x in bars[-1-days:-1])/days
    return round((float(bars[-1].get('volume') or 0)/avg),2) if avg else None

def label_excess(x):
    if x is None: return '자료부족'
    if x>=2: return '시장대비 강함'
    if x<=-2: return '시장대비 약함'
    return '시장수준'

def market_for(sym): return 'KR' if str(sym).endswith('.KS') or str(sym).endswith('.KQ') else 'US'
def bench_for(sym): return '^KS11' if market_for(sym)=='KR' else ('QQQ' if sym in ('QQQ','NVDA','AAPL','MSFT','AMD','TSLA','META','GOOGL','AMZN') else 'SPY')

def disclosures(conn,sym,limit=3):
    rows=[]
    for r in conn.execute("select rcept_dt,report_nm,category,risk_level from disclosure_events where symbol=? order by rcept_dt desc limit ?",(sym,limit)).fetchall():
        rows.append(dict(r))
    return rows

def main():
    conn=get_connection()
    recs=read_json('/tmp/recommendations_latest.json').get('items') or []
    fund=read_json('/tmp/fund_recommendation_consensus_latest.json').get('items') or []
    symbols=[]
    common=read_json('/tmp/common_universe_latest.json')
    for it in (common.get('items') or []):
        sym=it.get('symbol')
        if sym and sym not in symbols: symbols.append(sym)
    for it in fund+recs:
        s=it.get('symbol')
        if s and s not in symbols: symbols.append(s)
    items=[]; warnings=[]
    for sym in symbols:
        bars=latest_bars(conn,sym)
        if not bars:
            warnings.append(f'no_price_bars:{sym}'); continue
        bench=bench_for(sym); bbars=latest_bars(conn,bench)
        r5=ret(bars,5); r20=ret(bars,20); br5=ret(bbars,5); br20=ret(bbars,20)
        ex5=round(r5-br5,2) if r5 is not None and br5 is not None else None
        ex20=round(r20-br20,2) if r20 is not None and br20 is not None else None
        vr=vol_ratio(bars)
        disc=disclosures(conn,sym)
        news_summary='최근 공시/뉴스 근거 부족'
        if disc:
            d=disc[0]; news_summary=f"{d.get('report_nm')} · {d.get('rcept_dt')} · risk {d.get('risk_level') or '-'}"
        items.append({'symbol':sym,'market':market_for(sym),'benchmark':bench,'latest_date':bars[-1]['date'],'latest_close':float(bars[-1]['close']),'return_5d_pct':r5,'return_20d_pct':r20,'benchmark_5d_pct':br5,'benchmark_20d_pct':br20,'excess_5d_pct':ex5,'excess_20d_pct':ex20,'relative_label_5d':label_excess(ex5),'relative_label_20d':label_excess(ex20),'volume_ratio_20d':vr,'volume_label':'거래량 증가' if (vr or 0)>=1.5 else ('거래량 위축' if vr is not None and vr<0.7 else '거래량 보통'),'news_summary':news_summary,'disclosures':disc,'context_line':f"지수 대비 5D {ex5 if ex5 is not None else '-'}%p · 20D {ex20 if ex20 is not None else '-'}%p · 거래량 {vr if vr is not None else '-'}x"})
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'recommendation_market_context','real_trading':False,'authority':'paper_only_context_not_recommendation_authority','item_count':len(items),'items':items,'summary':{'symbols':symbols[:20],'warnings':warnings[:10]},'warnings':warnings,'next_actions':['Use market/news context as evidence layer only; keep fund/risk gates separate.']}
    attach_contract(packet,'recommendation_market_context_agent',status='degraded' if warnings else 'ok',outputs={'item_count':len(items)},metrics=packet['summary'],warnings=warnings,next_actions=packet['next_actions'])
    Path('/tmp/recommendation_market_context_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'static/recommendation_market_context_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
