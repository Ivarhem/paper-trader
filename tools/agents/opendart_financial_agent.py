#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, sys, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import load_env_file
from app.database import init_db, list_universe_members, save_financial_snapshot
from tools.agents.opendart_disclosure_agent import load_stock_to_corp_code, stock_code_from_symbol
from tools.agents.lib.agent_contract import attach_contract

load_env_file(ROOT/'.env')

ACCOUNT_ALIASES={
    'revenue':['매출액','영업수익'],
    'operating_income':['영업이익'],
    'net_income':['당기순이익','분기순이익','반기순이익'],
    'assets':['자산총계'],
    'liabilities':['부채총계'],
    'equity':['자본총계'],
    'operating_cashflow':['영업활동으로인한현금흐름','영업활동 현금흐름'],
}

def parse_amount(v):
    if v in (None,'','-'):
        return None
    try: return float(str(v).replace(',','').strip())
    except ValueError: return None

def active_kr_symbols(limit):
    init_db()
    return [m['symbol'] for m in list_universe_members(limit=1000,status='active') if m['symbol'].endswith(('.KS','.KQ'))][:limit]

def fetch_single(api_key, corp_code, year, reprt_code):
    params={'crtfc_key':api_key,'corp_code':corp_code,'bsns_year':str(year),'reprt_code':reprt_code,'fs_div':'CFS'}
    url='https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json?'+urllib.parse.urlencode(params)
    with urllib.request.urlopen(url,timeout=30) as res:
        return json.loads(res.read().decode('utf-8'))

def summarize(symbol, corp_code, year, reprt_code, data):
    snap={'symbol':symbol,'corp_code':corp_code,'bsns_year':str(year),'reprt_code':reprt_code,'raw_status':data.get('status'),'raw_message':data.get('message')}
    rows=data.get('list') or []
    for key,names in ACCOUNT_ALIASES.items():
        val=None
        for r in rows:
            name=(r.get('account_nm') or '').replace(' ','')
            if any(alias.replace(' ','') == name for alias in names):
                val=parse_amount(r.get('thstrm_amount'))
                break
        snap[key]=val
    snap['row_count']=len(rows)
    return snap

def main():
    ap=argparse.ArgumentParser(description='Fetch OpenDART financial statement snapshots for KR active universe')
    ap.add_argument('--symbols',default='active-kr')
    ap.add_argument('--years',default='2025,2024')
    ap.add_argument('--reprt-codes',default='11013,11012,11014,11011',help='Q1,H1,Q3,annual')
    ap.add_argument('--limit',type=int,default=20)
    ap.add_argument('--save',action='store_true')
    ap.add_argument('--output',default='/tmp/opendart_financials_latest.json')
    args=ap.parse_args()
    key=os.getenv('OPENDART_API_KEY')
    if not key:
        packet={'status':'missing_api_key','env':'OPENDART_API_KEY','items':[],'real_trading':False}
        Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2)); return
    symbols=active_kr_symbols(args.limit) if args.symbols=='active-kr' else [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    mapping=load_stock_to_corp_code(key); items=[]; calls=[]; saved=0; missing=[]
    for symbol in symbols[:args.limit]:
        code=stock_code_from_symbol(symbol); corp=mapping.get(code or '')
        if not corp: missing.append(symbol); continue
        latest=None
        years=[x.strip() for x in args.years.split(',') if x.strip()]
        reprt_codes=[x.strip() for x in args.reprt_codes.split(',') if x.strip()]
        for year in years:
            for rc in reprt_codes:
                data=fetch_single(key,corp,year,rc); calls.append({'symbol':symbol,'year':year,'reprt_code':rc,'status':data.get('status'),'count':len(data.get('list') or [])})
                if data.get('status')=='000' and data.get('list'):
                    latest=(year,rc,data)
                    snap=summarize(symbol,corp,year,rc,data); items.append(snap)
                    if args.save: save_financial_snapshot(snap); saved+=1
                    break
            if latest: break
        if latest:
            year,rc,_data=latest
            try: prev_year=str(int(year)-1)
            except ValueError: prev_year=None
            if prev_year and prev_year not in [i.get('bsns_year') for i in items if i.get('symbol')==symbol and i.get('reprt_code')==rc]:
                data=fetch_single(key,corp,prev_year,rc); calls.append({'symbol':symbol,'year':prev_year,'reprt_code':rc,'status':data.get('status'),'count':len(data.get('list') or []),'purpose':'yoy_baseline'})
                if data.get('status')=='000' and data.get('list'):
                    snap=summarize(symbol,corp,prev_year,rc,data); items.append(snap)
                    if args.save: save_financial_snapshot(snap); saved+=1
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'opendart_financial_snapshots','real_trading':False,'status':'ok','symbols':symbols,'missing_symbols':missing,'items':items,'calls':calls,'saved':saved}
    attach_contract(packet,'opendart_financial_agent',status='ok',inputs={'symbols':args.symbols,'limit':args.limit},outputs={'item_count':len(items),'saved':saved},metrics={'symbols':len(symbols),'saved':saved},warnings=[] if items else ['no financial snapshots fetched'],next_actions=[] if items else ['Check OPENDART coverage/API limits.'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
