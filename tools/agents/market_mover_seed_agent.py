#!/usr/bin/env python3
from __future__ import annotations
import argparse, html, json, re, sys, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract

NAVER_URLS={
    'gainer':'https://finance.naver.com/sise/sise_rise.naver?sosok={sosok}',
    'loser':'https://finance.naver.com/sise/sise_fall.naver?sosok={sosok}',
}
YAHOO_SCREENER_URL='https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds={scr_id}&count={count}'
YAHOO_SCREENS={'gainer':'day_gainers','loser':'day_losers','active':'most_actives'}
MARKETS={0:('KRX_KOSPI','.KS'),1:('KRX_KOSDAQ','.KQ')}
NON_STOCK_PREFIXES=('KODEX','TIGER','ACE','RISE','SOL','PLUS','HANARO','KIWOOM','WON','TIME','FOCUS','UNICORN','TRUSTON','VITA','ITF','N2','KB ','삼성 ','신한 ','미래에셋 ','한투 ','메리츠 ','하나 ','대신 ')
NON_STOCK_KEYWORDS=('ETN','ETF','선물','레버리지','인버스','채권','커버드콜','TR','액티브','합성','국채','금리','머니마켓','SOFR')

def now(): return datetime.now(timezone.utc).isoformat()
def clean_num(s):
    if s is None: return None
    s=str(s).replace(',','').replace('%','').replace('+','').strip()
    if not s or s=='N/A': return None
    try: return float(s)
    except Exception: return None

def fetch(url, encoding='euc-kr'):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 paper-trader research bot'})
    raw=urllib.request.urlopen(req,timeout=15).read()
    return raw.decode(encoding,'ignore')

def is_probable_stock(name):
    n=(name or '').strip()
    if any(n.startswith(p) for p in NON_STOCK_PREFIXES): return False
    if any(k in n for k in NON_STOCK_KEYWORDS): return False
    return True

def is_kr_upper_limit_candidate(change_pct, direction):
    if direction != 'gainer' or change_pct is None:
        return False
    # KRX daily price limit is generally +/-30%; use 29.5 to tolerate
    # rounded delayed quote displays from Naver Finance.
    return float(change_pct) >= 29.5


def parse_market(sosok, limit, direction='gainer'):
    market,suffix=MARKETS[sosok]
    text=fetch(NAVER_URLS[direction].format(sosok=sosok))
    rows=[]
    # Keep the regex deliberately local to each table row so malformed ads/menus don't leak in.
    pattern=re.compile(r'<tr>\s*<td class="no">\s*(\d+)\s*</td>\s*<td><a href="/item/main\.naver\?code=(\d{6})" class="tltle">(.*?)</a></td>\s*<td class="number">(.*?)</td>.*?<td class="number">\s*<span class="tah p11 (?:red01|nv01|blue01)">\s*([+\-]?[0-9.,]+%)\s*</span>\s*</td>.*?<td class="number">(.*?)</td>',re.S)
    for m in pattern.finditer(text):
        rank=int(m.group(1)); code=m.group(2); name=html.unescape(re.sub('<.*?>','',m.group(3))).strip()
        price=clean_num(m.group(4)); change_pct=clean_num(m.group(5)); volume=clean_num(m.group(6))
        if rank>limit: continue
        upper_limit_candidate = is_kr_upper_limit_candidate(change_pct, direction)
        rows.append({'rank':rank,'code':code,'symbol':code+suffix,'name':name,'market':'KR','exchange':market,'source':'naver_sise_rise' if direction=='gainer' else 'naver_sise_fall','direction':direction,'price':price,'change_pct':change_pct,'volume':volume,'probable_stock':is_probable_stock(name),'upper_limit_candidate':upper_limit_candidate,'seed_priority':'upper_limit' if upper_limit_candidate else direction,'captured_at':now(),'data_timing':'intraday_or_delayed_provisional'})
    return rows


def parse_yahoo_us(limit, direction='gainer'):
    scr_id=YAHOO_SCREENS[direction]
    text=fetch(YAHOO_SCREENER_URL.format(scr_id=urllib.parse.quote(scr_id),count=limit), encoding='utf-8')
    data=json.loads(text)
    quotes=((data.get('finance') or {}).get('result') or [{}])[0].get('quotes') or []
    rows=[]
    for rank,q in enumerate(quotes[:limit],1):
        if q.get('quoteType') not in (None,'EQUITY'):
            continue
        sym=str(q.get('symbol') or '').upper().strip()
        if not sym or any(ch in sym for ch in ['=','^','/']):
            continue
        change=q.get('regularMarketChangePercent')
        price=q.get('regularMarketPrice')
        volume=q.get('regularMarketVolume')
        rows.append({'rank':rank,'code':sym,'symbol':sym,'name':q.get('shortName') or q.get('longName') or sym,'market':'US','exchange':q.get('fullExchangeName') or q.get('exchange'),'source':'yahoo_day_gainers' if direction=='gainer' else ('yahoo_day_losers' if direction=='loser' else 'yahoo_most_actives'),'direction':direction,'price':float(price) if price is not None else None,'change_pct':round(float(change),4) if change is not None else None,'volume':float(volume) if volume is not None else None,'probable_stock':True,'captured_at':now(),'data_timing':'intraday_or_delayed_provisional'})
    return rows

def main():
    ap=argparse.ArgumentParser(description='Collect KR/US top mover seed symbols for paper-only shock research')
    ap.add_argument('--limit-per-market',type=int,default=80)
    ap.add_argument('--us-limit',type=int,default=80)
    ap.add_argument('--markets',default='KR,US',help='Comma-separated markets to seed: KR,US')
    ap.add_argument('--stock-only',action='store_true',default=False)
    ap.add_argument('--output',default='/tmp/market_mover_seed_latest.json')
    args=ap.parse_args()
    errors=[]; items=[]
    markets={x.strip().upper() for x in args.markets.split(',') if x.strip()}
    if 'KR' in markets:
        for sosok in MARKETS:
            for direction in ('gainer','loser'):
                try: items.extend(parse_market(sosok,args.limit_per_market,direction))
                except Exception as exc: errors.append(f'{MARKETS[sosok][0]} {direction} fetch failed: {exc}')
    if 'US' in markets:
        for direction in ('gainer','loser','active'):
            try: items.extend(parse_yahoo_us(args.us_limit,direction))
            except Exception as exc: errors.append(f'US yahoo {direction} fetch failed: {exc}')
    if args.stock_only: items=[x for x in items if x.get('probable_stock')]
    # Deduplicate by symbol, keep best rank per exchange page.
    by={}
    for x in sorted(items,key=lambda r:(r.get('rank') or 9999)):
        by.setdefault(x['symbol'],x)
    items=list(by.values())
    top_stock=[x for x in items if x.get('probable_stock')]
    upper_limit=[x for x in top_stock if x.get('upper_limit_candidate')]
    prioritized_stock=upper_limit + [x for x in top_stock if not x.get('upper_limit_candidate')]
    packet={'run_at':now(),'mode':'market_mover_seed','provider':'naver_finance_sise_rise_fall+yahoo_finance_screener','real_trading':False,'authority':'data_seed_only','summary':{'seed_count':len(items),'probable_stock_count':sum(1 for x in items if x.get('probable_stock')),'kr_seed_count':sum(1 for x in items if x.get('market')=='KR'),'us_seed_count':sum(1 for x in items if x.get('market')=='US'),'gainer_count':sum(1 for x in items if x.get('direction')=='gainer'),'loser_count':sum(1 for x in items if x.get('direction')=='loser'),'active_count':sum(1 for x in items if x.get('direction')=='active'),'upper_limit_candidate_count':len(upper_limit),'top_stock_count':len(top_stock),'error_count':len(errors)},'items':items,'top_stock_items':prioritized_stock,'top_upper_limit_items':upper_limit,'errors':errors,'next_actions':['Use upper-limit candidates as provisional price-refresh and shock-scout inputs only; no recommendation or active strategy authority.']}
    attach_contract(packet,'market_mover_seed',status='degraded' if errors else 'ok',outputs={'seed_count':len(items),'probable_stock_count':packet['summary']['probable_stock_count'],'upper_limit_candidate_count':len(upper_limit)},metrics=packet['summary'],warnings=errors,next_actions=packet['next_actions'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    Path('/tmp/market_mover_upper_limit_symbols.txt').write_text(','.join(x.get('symbol','') for x in upper_limit if x.get('symbol')), encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
    if errors and not items: sys.exit(1)
if __name__=='__main__': main()
