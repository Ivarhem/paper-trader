#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.database import get_connection
from tools.agents.lib.agent_contract import attach_contract

def read_json(path):
    try: return json.load(open(path))
    except Exception: return {}

def market_of(sym):
    return 'KR' if str(sym).endswith('.KS') or str(sym).endswith('.KQ') else 'US'

def asset_type_of(sym):
    sym=str(sym or '')
    etfs={'SPY','QQQ','DIA','IWM','XLK','XLY','XLV','XBI','SMH','SOXX','KRE','XLE','XLF','XLU','XLP','XLI','XLB','TLT','HYG','LQD','EEM','EFA'}
    if sym.startswith('^'): return 'index'
    if sym in etfs: return 'etf'
    return 'stock'

def is_tradable_symbol(sym):
    return asset_type_of(sym) != 'index'

def add_symbol(out, seen, sym, source, payload=None):
    if not sym or sym in seen: return
    seen.add(sym)
    out.append({'symbol':sym,'market':market_of(sym),'asset_type':asset_type_of(sym),'tradable':is_tradable_symbol(sym),'sources':[source],'seed_payload':payload or {}})

def main():
    conn=get_connection(); rows=[]; seen=set(); source_counts={}
    sources=[('/tmp/recommendations_latest.json','recommendations','items'),('/tmp/strategy_candidates_latest.json','strategy_candidates','items'),('/tmp/universe_curator_latest.json','universe_curator','items')]
    for path,label,key in sources:
        data=read_json(path); items=data.get(key) or data.get('candidates') or data.get('selected') or []
        source_counts[label]=len(items)
        for it in items:
            if not isinstance(it,dict): continue
            add_symbol(rows,seen,it.get('symbol'),label,{k:it.get(k) for k in ['score','action','recommendation_bucket','status','reason','market'] if k in it})
    # price fallback only fills breadth; it is explicitly lower priority.
    if len(rows)<80:
        for r in conn.execute("select symbol, max(date) as latest_date, count(*) as n from price_bars where timeframe='1d' group by symbol having n>=120 order by latest_date desc, n desc limit 160").fetchall():
            add_symbol(rows,seen,r['symbol'],'price_bars_fallback',{'price_bar_count':r['n'],'latest_date':r['latest_date']})
            if len(rows)>=120: break
    filtered=[]; dropped_insufficient=[]
    for row in rows:
        r=conn.execute("select count(*) as n, max(date) as latest_date from price_bars where timeframe='1d' and symbol=?",(row['symbol'],)).fetchone()
        if not r or r['n']<80:
            dropped_insufficient.append(row['symbol']); continue
        row['price_bar_count']=r['n']; row['latest_price_date']=r['latest_date']; filtered.append(row)
    warnings=[]
    if dropped_insufficient:
        warnings.append(f"excluded_insufficient_price_bars:{len(dropped_insufficient)}")
    kr=[x for x in filtered if x['market']=='KR']; us=[x for x in filtered if x['market']=='US']
    tradable=[x for x in filtered if x.get('tradable', True)]
    asset_type_counts={}
    for x in filtered: asset_type_counts[x.get('asset_type','unknown')]=asset_type_counts.get(x.get('asset_type','unknown'),0)+1
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'common_recommendation_universe','real_trading':False,'authority':'paper_only_shared_universe','item_count':len(filtered),'tradable_count':len(tradable),'market_counts':{'KR':len(kr),'US':len(us)},'asset_type_counts':asset_type_counts,'source_counts':source_counts,'excluded_insufficient_price_bars':dropped_insufficient[:200],'excluded_insufficient_price_bar_count':len(dropped_insufficient),'items':filtered,'symbols':[x['symbol'] for x in filtered],'warnings':warnings,'next_actions':['Use this file as canonical universe input for recommendations, fund leagues, context, and UI evidence.']}
    attach_contract(packet,'common_universe_agent',status='ok',outputs={'item_count':len(filtered)},metrics={'item_count':len(filtered),'tradable_count':len(tradable),**packet['market_counts']},warnings=warnings,next_actions=packet['next_actions'])
    Path('/tmp/common_universe_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'static/common_universe_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
