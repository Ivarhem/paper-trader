#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys, urllib.request, sqlite3
from datetime import date, timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.database import init_db, list_universe_members, get_connection, utc_now
UA='paper-trader research monitor contact: no-email@example.com'
TICKER_CACHE=Path('/tmp/sec_company_tickers.json')
HIGH_FORMS={'NT 10-K','NT 10-Q','NT 20-F','NT 40-F'}
MEDIUM_FORMS={'8-K','6-K','S-3','S-1','424B2','424B3','424B5','SC 13D','SC 13G','SCHEDULE 13D','4','144'}
POSITIVE_HINTS=['buyback','repurchase','dividend','special dividend']
RISK_HINTS=['bankruptcy','going concern','material weakness','delisting','offering','dilution','restatement']
def req_json(url:str):
    req=urllib.request.Request(url, headers={'User-Agent':UA,'Accept-Encoding':'identity'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))
def load_ticker_map():
    if TICKER_CACHE.exists() and TICKER_CACHE.stat().st_size>0:
        raw=json.loads(TICKER_CACHE.read_text())
    else:
        raw=req_json('https://www.sec.gov/files/company_tickers.json'); TICKER_CACHE.write_text(json.dumps(raw), encoding='utf-8')
    return {v['ticker'].upper(): {'cik': str(v['cik_str']).zfill(10), 'title': v.get('title') or v['ticker']} for v in raw.values()}
def active_us_symbols():
    init_db(); return [m['symbol'] for m in list_universe_members(limit=1000,status='active') if not m['symbol'].endswith(('.KS','.KQ')) and not m['symbol'].startswith('^')]
def classify(form:str, desc:str=''):
    text=f'{form} {desc}'.lower()
    if form in HIGH_FORMS or any(x in text for x in RISK_HINTS): return 'risk','high'
    if form in MEDIUM_FORMS: return 'risk','medium'
    if any(x in text for x in POSITIVE_HINTS): return 'positive','positive'
    if form in {'10-K','10-Q','20-F','40-F'}: return 'periodic_report','low'
    return 'other','low'
def fetch_recent(symbol:str, cik:str, title:str, begin:str, max_events:int=100):
    data=req_json(f'https://data.sec.gov/submissions/CIK{cik}.json'); recent=data.get('filings',{}).get('recent',{})
    events=[]; accs=recent.get('accessionNumber',[]) or []
    for i, acc in enumerate(accs):
        filing_date=(recent.get('filingDate') or [None])[i]
        if not filing_date or filing_date < begin: continue
        form=(recent.get('form') or [''])[i]; primary=(recent.get('primaryDocDescription') or [''])[i] or ''
        category,risk=classify(form, primary)
        if len(events) >= max_events: break
        events.append({'rcept_no':f'SEC-{acc}','rcept_dt':filing_date.replace('-',''),'corp_code':cik,'corp_name':title,'stock_code':symbol,'symbol':symbol,'report_nm':f'{form} {primary}'.strip(),'category':category,'risk_level':risk,'form':form,'accession_number':acc,'filing_date':filing_date,'primary_doc':(recent.get('primaryDocument') or [''])[i] if recent.get('primaryDocument') else None})
    return events
def save_events(events):
    init_db(); inserted=skipped=0; created_at=utc_now()
    with get_connection() as conn:
        for e in events:
            try:
                cur=conn.execute("INSERT INTO disclosure_events (rcept_no,rcept_dt,corp_code,corp_name,stock_code,symbol,report_nm,category,risk_level,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (e['rcept_no'],e['rcept_dt'],e.get('corp_code'),e.get('corp_name') or '',e.get('stock_code'),e.get('symbol'),e.get('report_nm') or '',e.get('category'),e.get('risk_level'),json.dumps(e,ensure_ascii=False,sort_keys=True),created_at))
                if cur.rowcount: inserted+=1
            except sqlite3.IntegrityError:
                skipped+=1
    return {'inserted':inserted,'skipped':skipped}
def main():
    ap=argparse.ArgumentParser(description='Fetch recent SEC EDGAR filings for active US universe')
    ap.add_argument('--symbols', default='active-us'); ap.add_argument('--begin', default=(date.today()-timedelta(days=14)).isoformat()); ap.add_argument('--save', action='store_true'); ap.add_argument('--output', default='/tmp/sec_edgar_disclosures_latest.json')
    args=ap.parse_args(); tm=load_ticker_map(); symbols=active_us_symbols() if args.symbols=='active-us' else [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    events=[]; calls=[]; missing=[]
    for s in symbols:
        m=tm.get(s)
        if not m: missing.append(s); continue
        try:
            rows=fetch_recent(s,m['cik'],m['title'],args.begin); events.extend(rows); calls.append({'symbol':s,'cik':m['cik'],'count':len(rows)})
        except Exception as e: calls.append({'symbol':s,'error':str(e)})
    packet={'status':'000','message':'SEC EDGAR 조회 완료','symbols':symbols,'missing_symbols':missing,'calls':calls,'list':events,'real_trading':False}
    if args.save: packet['save_result']=save_events(events)
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
