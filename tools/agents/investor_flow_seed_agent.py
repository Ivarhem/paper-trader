#!/usr/bin/env python3
from __future__ import annotations
import argparse, html, json, re, sys, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract
from app.database import init_db, save_investor_flow_seed

URL='https://finance.naver.com/sise/sise_deal_rank.naver?{query}'
MARKETS={0:('KRX_KOSPI','.KS'),1:('KRX_KOSDAQ','.KQ')}
INVESTORS={'foreign':None,'institution':'1000'}
NON_STOCK_PREFIXES=('KODEX','TIGER','ACE','RISE','SOL','PLUS','HANARO','KIWOOM','WON','TIME','FOCUS','UNICORN','TRUSTON','VITA','ITF','N2','KB ','삼성 ','신한 ','미래에셋 ','한투 ','메리츠 ','하나 ','대신 ')
NON_STOCK_KEYWORDS=('ETN','ETF','선물','레버리지','인버스','채권','커버드콜','TR','액티브','합성','국채','금리','머니마켓','SOFR')

def now(): return datetime.now(timezone.utc).isoformat()
def clean_num(s):
    s=str(s or '').replace(',','').replace('+','').strip()
    if not s or s in ('N/A','-'): return None
    try: return float(s)
    except Exception: return None

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 paper-trader research bot'})
    return urllib.request.urlopen(req,timeout=15).read().decode('euc-kr','ignore')

def is_probable_stock(name):
    n=(name or '').strip()
    if any(n.startswith(p) for p in NON_STOCK_PREFIXES): return False
    if any(k in n for k in NON_STOCK_KEYWORDS): return False
    return True

def parse_page(sosok:int, investor:str, limit:int):
    market,suffix=MARKETS[sosok]
    params={'sosok':str(sosok)}
    g=INVESTORS[investor]
    if g: params['investor_gubun']=g
    url=URL.format(query=urllib.parse.urlencode(params))
    text=fetch(url)
    rows=[]
    # Naver page has compact rows: rank, name link, close price, net-buy amount/qty fields vary by section.
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.S):
        code_m=re.search(r'/item/main\.naver\?code=(\d{6})', tr)
        if not code_m: continue
        code=code_m.group(1)
        name_m=re.search(r'class="(?:tltle|company)"[^>]*>(.*?)</a>', tr, re.S)
        if not name_m:
            name_m=re.search(r'<a[^>]*class="(?:tltle|company)"[^>]*>(.*?)</a>', tr, re.S)
        name=html.unescape(re.sub('<.*?>','',name_m.group(1))).strip() if name_m else code
        txt=' '.join(html.unescape(re.sub('<.*?>',' ',tr)).split())
        nums=[clean_num(x) for x in re.findall(r'[+\-]?[0-9][0-9,]*(?:\.\d+)?', txt)]
        nums=[x for x in nums if x is not None]
        rank_m=re.search(r"ico_n(\d{2})\.gif", tr) or re.search(r", '(\d+)', event", tr)
        rank=int(rank_m.group(1)) if rank_m else (int(nums[0]) if nums else None)
        price=nums[0] if nums else None
        # Last numeric fields on this page are net-buy-ish display fields. Keep both as raw evidence.
        net_values=nums[1:] if len(nums)>1 else []
        if rank and rank>limit: continue
        rows.append({'rank':rank,'code':code,'symbol':code+suffix,'name':name,'market':'KR','exchange':market,'investor':investor,'source':'naver_sise_deal_rank','price':price,'raw_numeric_values':net_values,'raw_text':txt[:240],'probable_stock':is_probable_stock(name),'captured_at':now(),'data_timing':'intraday_or_delayed_provisional','authority':'paper_monitoring_seed_only'})
    return rows

def main():
    ap=argparse.ArgumentParser(description='Collect Naver KR foreign/institution net-buy seed symbols for paper monitoring')
    ap.add_argument('--limit-per-market',type=int,default=50)
    ap.add_argument('--investors',default='foreign,institution')
    ap.add_argument('--stock-only',action='store_true',default=True)
    ap.add_argument('--output',default='/tmp/investor_flow_seed_latest.json')
    args=ap.parse_args()
    errors=[]; items=[]
    for inv in [x.strip() for x in args.investors.split(',') if x.strip()]:
        for sosok in MARKETS:
            try: items.extend(parse_page(sosok,inv,args.limit_per_market))
            except Exception as exc: errors.append(f'{inv} {MARKETS[sosok][0]} fetch failed: {exc}')
    if args.stock_only: items=[x for x in items if x.get('probable_stock')]
    by={}
    for x in sorted(items,key=lambda r:(r.get('rank') or 9999)):
        key=(x['symbol'],x['investor'])
        by.setdefault(key,x)
    items=list(by.values())
    by_symbol={}
    for x in items:
        b=by_symbol.setdefault(x['symbol'],{'symbol':x['symbol'],'name':x.get('name'),'market':'KR','investors':[],'best_rank':x.get('rank'),'sources':[],'captured_at':x.get('captured_at'),'authority':'paper_monitoring_seed_only'})
        b['investors'].append(x['investor']); b['sources'].append({'investor':x['investor'],'rank':x.get('rank'),'raw_numeric_values':x.get('raw_numeric_values'),'raw_text':x.get('raw_text')})
        if x.get('rank') and (b.get('best_rank') is None or x['rank']<b['best_rank']): b['best_rank']=x['rank']
    top=sorted(by_symbol.values(), key=lambda x:(x.get('best_rank') or 9999, -len(x.get('investors') or [])))
    packet={'run_at':now(),'mode':'investor_flow_seed','provider':'naver_finance_sise_deal_rank','real_trading':False,'authority':'paper_monitoring_seed_only','summary':{'item_count':len(items),'symbol_count':len(top),'foreign_count':sum(1 for x in items if x.get('investor')=='foreign'),'institution_count':sum(1 for x in items if x.get('investor')=='institution'),'error_count':len(errors)},'items':items,'top_symbols':top,'errors':errors,'warnings':['Naver table is provisional/delayed and scraped; persisted as provisional DB evidence for monitoring seed + validation priority only, not direct recommendation authority.'],'next_actions':['Feed top_symbols into daily price refresh/watchlist and supply proxy validation.']}
    init_db()
    db_save=save_investor_flow_seed(packet)
    packet['db_persistence']=db_save
    packet['summary']['db_inserted_or_updated']=db_save.get('inserted_or_updated',0)
    attach_contract(packet,'investor_flow_seed_agent',status='degraded' if errors else 'ok',outputs={'symbol_count':len(top),'db_inserted_or_updated':db_save.get('inserted_or_updated',0)},metrics=packet['summary'],warnings=packet['warnings']+errors,next_actions=packet['next_actions'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
    if errors and not items: sys.exit(1)
if __name__=='__main__': main()
