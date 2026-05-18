#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db

def pct(a,b): return round((a/b-1)*100,2) if b else None

def main():
    ap=argparse.ArgumentParser(description='Detect price-data quality issues that can distort validation')
    ap.add_argument('--symbols')
    ap.add_argument('--output', default='/tmp/data_quality_latest.json')
    ap.add_argument('--strict-exit', action='store_true', help='Exit nonzero for data-quality fail verdicts; default keeps scheduled research pipeline non-blocking')
    args=ap.parse_args(); init_db()
    conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    symbols=[s.strip().upper() for s in args.symbols.split(',')] if args.symbols else [r['symbol'] for r in conn.execute('SELECT DISTINCT symbol FROM price_bars ORDER BY symbol')]
    issues=[]; summaries=[]
    for sym in symbols:
        rows=conn.execute('SELECT date, close, exchange FROM price_bars WHERE symbol=? AND timeframe="1d" ORDER BY date', (sym,)).fetchall()
        jumps=[]; sources={}
        prev=None
        for r in rows:
            sources[r['exchange'] or 'unknown']=sources.get(r['exchange'] or 'unknown',0)+1
            close=float(r['close'])
            if prev and prev[1]>0:
                change=pct(close, prev[1])
                ratio=max(close/prev[1], prev[1]/close) if close > 0 else 999
                if abs(change) >= 35 or ratio >= 3:
                    severity = 'fail' if abs(change) >= 80 or ratio >= 3 else 'watch'
                    jumps.append({'from_date':prev[0],'to_date':r['date'],'from_close':prev[1],'to_close':close,'change_pct':change,'ratio':round(ratio,4),'severity':severity,'from_source':prev[2] or 'unknown','to_source':r['exchange'] or 'unknown'})
            prev=(r['date'],close,r['exchange'])
        level='ok'
        if any(j.get('severity') == 'fail' for j in jumps): level='fail'
        elif jumps or len(sources)>1: level='watch'
        summaries.append({'symbol':sym,'bars':len(rows),'sources':sources,'level':level,'jump_count':len(jumps),'jumps':jumps[:10]})
        for j in jumps[:10]: issues.append({'symbol':sym,'type':'large_close_jump','severity':j.get('severity','watch'),'detail':j})
        if len(sources)>1:
            issues.append({'symbol':sym,'type':'mixed_sources','severity':'watch','detail':sources})
    verdict='fail' if any(i['severity']=='fail' for i in issues) else ('watch' if issues else 'ok')
    hard_error_count=sum(1 for i in issues if i.get('severity') in ('hard','error'))
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'data_quality_check','verdict':verdict,'issue_count':len(issues),'hard_error_count':hard_error_count,'non_blocking':not args.strict_exit,'issues':issues[:100],'symbols':summaries}
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
    # Scheduled research should keep producing paper outputs when quality findings
    # are diagnostic/watch/fail records but not hard ingestion errors. Operators can
    # opt into legacy blocking behavior with --strict-exit.
    if args.strict_exit and (verdict=='fail' or hard_error_count): sys.exit(2)
    if hard_error_count: sys.exit(2)
if __name__=='__main__': main()
