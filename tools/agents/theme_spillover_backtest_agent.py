#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sqlite3,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.database import get_settings, init_db
from tools.agents.lib.agent_contract import attach_contract
from tools.agents.market_shock_mover_scout_agent import THEME_GRAPH

def now(): return datetime.now(timezone.utc).isoformat()
def pct(a,b):
 try:
  if b in (None,0): return None
  return round((float(a)/float(b)-1)*100,2)
 except Exception: return None

def series(conn,sym):
 rows=conn.execute("select date, close from price_bars where symbol=? and timeframe='1d' order by date",(sym,)).fetchall()
 return [(r['date'],float(r['close'])) for r in rows]

def index_by_date(s): return {d:i for i,(d,_) in enumerate(s)}
def forward_ret(s, idx, h):
 if idx+h>=len(s): return None
 return pct(s[idx+h][1],s[idx][1])

def basket_ret(series_map, date, h):
 vals=[]
 for s in series_map.values():
  ix=index_by_date(s).get(date)
  if ix is None: continue
  r=forward_ret(s,ix,h)
  if r is not None: vals.append(r)
 return round(sum(vals)/len(vals),2) if vals else None

def benchmark_ret(conn, date, h, market='US'):
 symbols=['SPY','QQQ'] if market=='US' else ['069500.KS','^KS11']
 for sym in symbols:
  s=series(conn,sym)
  ix=index_by_date(s).get(date)
  if ix is None: continue
  r=forward_ret(s,ix,h)
  if r is not None: return r
 return None

def event_dates(src_map, threshold=1.5, breadth=45):
 # Use common dates from available source symbols; activation when avg 1d return and breadth cross threshold.
 rets={}
 for sym,s in src_map.items():
  for i in range(1,len(s)):
   r=pct(s[i][1],s[i-1][1])
   if r is not None: rets.setdefault(s[i][0],[]).append(r)
 out=[]
 for d,vals in rets.items():
  if len(vals)<max(2, len(src_map)//4): continue
  avg=sum(vals)/len(vals); up=sum(1 for v in vals if v>threshold)/len(vals)*100; down=sum(1 for v in vals if v<-threshold)/len(vals)*100
  if avg>=threshold and up>=breadth: out.append({'date':d,'direction':'positive_spillover','source_avg_1d_pct':round(avg,2),'source_breadth_pct':round(up,2)})
  elif avg<=-threshold and down>=breadth: out.append({'date':d,'direction':'negative_spillover','source_avg_1d_pct':round(avg,2),'source_breadth_pct':round(down,2)})
 return out[-80:]

def branch_market(symbols):
 kr=sum(1 for s in symbols if str(s).endswith(('.KS','.KQ')) or str(s).startswith('^KS'))
 return 'KR' if kr > len(symbols)/2 else 'US'

def analyze_theme(conn, theme, cfg):
 src={s:series(conn,s) for s in cfg['source']}
 src={k:v for k,v in src.items() if len(v)>40}
 events=event_dates(src)
 horizons=cfg.get('follow_through_horizons') or [3,5,10,20]
 branches=[]
 for bname,syms in cfg['downstream'].items():
  bmap={s:series(conn,s) for s in syms}
  bmap={k:v for k,v in bmap.items() if len(v)>40}
  by_h=[]
  for h in horizons:
   vals=[]; excess=[]
   market=branch_market(syms)
   for e in events:
    r=basket_ret(bmap,e['date'],h)
    if r is None: continue
    vals.append(r)
    br=benchmark_ret(conn,e['date'],h,market)
    if br is not None: excess.append(round(r-br,2))
   avg=round(sum(vals)/len(vals),2) if vals else None
   win=round(sum(1 for v in vals if v>0)/len(vals)*100,2) if vals else None
   exavg=round(sum(excess)/len(excess),2) if excess else None
   exwin=round(sum(1 for v in excess if v>0)/len(excess)*100,2) if excess else None
   by_h.append({'horizon_days':h,'sample_count':len(vals),'avg_forward_return_pct':avg,'positive_rate_pct':win,'avg_excess_forward_return_pct':exavg,'excess_positive_rate_pct':exwin,'benchmark_market':market})
  branches.append({'branch':bname,'symbol_count':len(bmap),'results':by_h})
 # score conservative: need samples and positive avg/win on any branch/horizon
 best=None
 for b in branches:
  for r in b['results']:
   if r['sample_count']<8 or r['avg_forward_return_pct'] is None: continue
   score=(r.get('avg_excess_forward_return_pct') or 0)*10 + ((r.get('excess_positive_rate_pct') or 0)-50)/2 + min(10,r['sample_count']/3)
   cand={**r,'branch':b['branch'],'score':round(score,2)}
   if best is None or cand['score']>best['score']: best=cand
 verdict='insufficient_samples'
 if best:
  if best['score']>=18 and (best.get('avg_excess_forward_return_pct') or 0)>0.75 and (best.get('excess_positive_rate_pct') or 0)>=55: verdict='promising_research_candidate'
  elif best['score']>=8 and (best.get('avg_excess_forward_return_pct') or 0)>0: verdict='watch_only'
  else: verdict='weak'
 return {'theme':theme,'label':cfg['label'],'event_count':len(events),'recent_events':events[-10:],'branches':branches,'best_branch_result':best,'verdict':verdict}

def main():
 ap=argparse.ArgumentParser(description='Historical theme spillover follow-through backtest; paper-only diagnostics')
 ap.add_argument('--theme',default='all')
 ap.add_argument('--output',default='/tmp/theme_spillover_backtest_latest.json')
 args=ap.parse_args(); init_db()
 conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
 themes=THEME_GRAPH if args.theme=='all' else {k:v for k,v in THEME_GRAPH.items() if k in set(args.theme.split(','))}
 items=[analyze_theme(conn,k,v) for k,v in themes.items()]
 conn.close()
 warnings=[]
 if any(x['verdict']=='insufficient_samples' for x in items): warnings.append('some_theme_spillovers_have_insufficient_samples')
 summary={'theme_count':len(items),'promising_count':sum(1 for x in items if x['verdict']=='promising_research_candidate'),'watch_count':sum(1 for x in items if x['verdict']=='watch_only'),'insufficient_count':sum(1 for x in items if x['verdict']=='insufficient_samples')}
 packet={'run_at':now(),'mode':'theme_spillover_historical_follow_through_backtest','real_trading':False,'authority':'diagnostic_only','summary':summary,'items':items,'next_actions':['Use promising/watch results as research hypotheses only; do not mutate recommendations or strategy status directly.']}
 attach_contract(packet,'theme_spillover_backtest',status='ok' if not warnings else 'degraded',outputs=summary,metrics=summary,warnings=warnings,next_actions=packet['next_actions'])
 Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
