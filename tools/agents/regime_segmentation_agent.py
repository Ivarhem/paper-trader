#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sqlite3,sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, list_strategy_registry

def regime_for(conn, cutoff:str, benchmark='SPY')->str:
    rows=conn.execute('SELECT date, close FROM price_bars WHERE symbol=? AND date < ? AND timeframe="1d" ORDER BY date DESC LIMIT 80',(benchmark,cutoff)).fetchall()
    if len(rows)<40: return 'unknown'
    closes=[float(r['close']) for r in reversed(rows)]
    r20=(closes[-1]/closes[-21]-1)*100 if len(closes)>21 and closes[-21] else 0
    rets=[(closes[i]/closes[i-1]-1) for i in range(1,len(closes)) if closes[i-1]]
    vol=(sum((x-sum(rets)/len(rets))**2 for x in rets)/(len(rets)-1))**0.5*(252**0.5)*100 if len(rets)>2 else 0
    trend='up' if r20>=3 else ('down' if r20<=-3 else 'sideways')
    vola='high_vol' if vol>=25 else 'normal_vol'
    return f'{trend}_{vola}'

def main():
    ap=argparse.ArgumentParser(description='Segment strategy validation performance by market regime')
    ap.add_argument('--output', default='/tmp/regime_segmentation_latest.json')
    args=ap.parse_args(); init_db(); conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    active=[r['logic'] for r in list_strategy_registry() if r['status']=='active'] or [r['logic'] for r in list_strategy_registry()[:5]]
    rows=conn.execute('SELECT logic,symbol,cutoff,result,final_return_pct,excess_return_pct FROM recommendation_validation_results WHERE action="candidate_buy_zone"').fetchall()
    by={}
    for r in rows:
        if r['logic'] not in active: continue
        reg=regime_for(conn,r['cutoff'],'SPY')
        key=(r['logic'],reg); by.setdefault(key,[]).append(r)
    summaries=[]
    for (logic,reg),arr in by.items():
        n=len(arr); succ=sum(1 for x in arr if x['result']=='success')
        avg=sum((x['excess_return_pct'] or 0) for x in arr)/n if n else 0
        summaries.append({'logic':logic,'regime':reg,'samples':n,'success_rate_pct':round(succ/n*100,2) if n else 0,'avg_excess_return_pct':round(avg,2)})
    summaries=sorted(summaries,key=lambda x:(x['logic'], -x['samples']))
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'regime_segmentation','active_logics':active,'items':summaries}
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
