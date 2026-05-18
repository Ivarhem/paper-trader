#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.database import get_settings, init_db
from tools.agents.lib.agent_contract import attach_contract

THEME_GRAPH={
 'ai_infrastructure':{
  'label':'AI infrastructure spillover',
  'source':['NVDA','AMD','AVGO','MSFT','GOOGL','META','SMCI','ANET','DELL','HPE'],
  'downstream':{
   'semiconductors':['SMH','SOXX','TSM','ASML','AMAT','LRCX','MU','QCOM','005930.KS','000660.KS','042700.KQ'],
   'datacenter_power':['ETN','GEV','VRT','PWR','EME','HUBB','010120.KS','267260.KS'],
   'cooling_infra':['VRT','TT','JCI','CARR'],
   'copper_materials':['FCX','SCCO','CPER'],
  },
  'follow_through_horizons':[3,5,10,20],
 },
 'geopolitical_defense_energy':{
  'label':'Geopolitical defense/energy shock',
  'source':['ITA','LMT','NOC','RTX','GD','XLE','XOM','CVX','OXY','USO'],
  'downstream':{
   'defense':['012450.KS','047810.KS','329180.KS','064350.KS','LMT','NOC','RTX','GD'],
   'energy':['XLE','XOM','CVX','OXY','096770.KS','010950.KS'],
   'shipping_insurance':['KEX','MATX','TRMD'],
   'inflation_rates':['TLT','IEF','GLD','UUP'],
  },
  'follow_through_horizons':[3,5,10,20],
 },
 'risk_off_growth_derating':{
  'label':'Risk-off growth derating / defensive rotation',
  'source':['QQQ','ARKK','IWM','TLT','^VIX','XLU','XLP','XLV'],
  'downstream':{
   'growth_weakness':['QQQ','ARKK','NVDA','TSLA','PLTR'],
   'defensive_strength':['XLU','XLP','XLV','KO','PG','JNJ'],
   'kr_growth_pressure':['035420.KS','035720.KS','259960.KS'],
  },
  'follow_through_horizons':[3,5,10],
 },
}

def now(): return datetime.now(timezone.utc).isoformat()
def read_json(path):
 p=Path(path)
 if not p.exists(): return {}
 try: return json.loads(p.read_text(encoding='utf-8'))
 except Exception: return {}
def market_of(sym): return 'KR' if str(sym).endswith(('.KS','.KQ')) else 'US'
def pct(a,b):
 try:
  if b in (None,0): return None
  return round((float(a)/float(b)-1)*100,2)
 except Exception: return None

def rows(conn,sym,limit=35):
 return conn.execute("select date, open, high, low, close, volume from price_bars where symbol=? and timeframe='1d' order by date desc limit ?",(sym,limit)).fetchall()[::-1]

def metric(conn,sym):
 r=rows(conn,sym,35)
 if len(r)<2: return {'symbol':sym,'available':False,'market':market_of(sym)}
 last,prev=r[-1],r[-2]
 vols=[float(x['volume'] or 0) for x in r[-21:-1]]
 avg_vol=sum(vols)/len(vols) if vols else None
 ret1=pct(last['close'],prev['close'])
 ret5=pct(last['close'],r[-6]['close']) if len(r)>=6 else None
 ret20=pct(last['close'],r[-21]['close']) if len(r)>=21 else None
 gap=pct(last['open'],prev['close'])
 vol_ratio=round(float(last['volume'] or 0)/avg_vol,2) if avg_vol else None
 intraday=pct(last['close'],last['open'])
 shock_score=0
 if ret1 is not None: shock_score+=abs(ret1)*8
 if vol_ratio is not None and vol_ratio>1: shock_score+=(vol_ratio-1)*10
 if gap is not None: shock_score+=abs(gap)*2
 return {'symbol':sym,'market':market_of(sym),'available':True,'latest_date':last['date'],'close':float(last['close']),'return_1d_pct':ret1,'return_5d_pct':ret5,'return_20d_pct':ret20,'gap_pct':gap,'intraday_return_pct':intraday,'volume_ratio_20d':vol_ratio,'shock_score':round(min(100,shock_score),2)}

def mover_seed_rows(path='/tmp/market_mover_seed_latest.json', limit=120):
 data=read_json(path)
 rows=data.get('top_stock_items') or data.get('items') or []
 out=[]
 for row in rows:
  sym=str(row.get('symbol') or '').upper().strip()
  if not sym: continue
  x=dict(row); x['symbol']=sym; out.append(x)
  if len(out)>=limit: break
 return out

def universe_symbols(conn, limit=420):
 syms=[r[0] for r in conn.execute("select symbol from universe_members where status in ('active','watch','candidate') order by score desc limit ?",(limit,)).fetchall()]
 graph=sorted({s for g in THEME_GRAPH.values() for s in g['source'] + [x for arr in g['downstream'].values() for x in arr]})
 seed=[r['symbol'] for r in mover_seed_rows()]
 return sorted(set(syms+graph+seed))

def classify_mover(m):
 r=m.get('return_1d_pct')
 vr=m.get('volume_ratio_20d') or 0
 gap=abs(m.get('gap_pct') or 0)
 tags=[]
 if r is None: return tags
 if r>=5: tags.append('surge')
 if r<=-5: tags.append('crash')
 if r>=2.5: tags.append('strong_up')
 if r<=-2.5: tags.append('strong_down')
 if vr>=2: tags.append('volume_confirmation')
 if gap>=2: tags.append('gap_shock')
 if abs(r)>=8 and vr>=1.5: tags.append('event_like_move')
 return tags

def avg(vals):
 vals=[float(v) for v in vals if v is not None]
 return round(sum(vals)/len(vals),2) if vals else None

def theme_assessment(theme,cfg,metrics):
 src=[metrics.get(s,{'symbol':s,'available':False}) for s in cfg['source']]
 src_av=[m for m in src if m.get('available') and m.get('return_1d_pct') is not None]
 avg1=avg([m.get('return_1d_pct') for m in src_av])
 up=sum(1 for m in src_av if (m.get('return_1d_pct') or 0)>1.5)
 down=sum(1 for m in src_av if (m.get('return_1d_pct') or 0)<-1.5)
 breadth=round(up/len(src_av)*100,2) if src_av else None
 downside=round(down/len(src_av)*100,2) if src_av else None
 branches=[]
 for name,syms in cfg['downstream'].items():
  ms=[metrics.get(s,{'symbol':s,'available':False}) for s in syms]
  av=[m for m in ms if m.get('available')]
  branches.append({'branch':name,'symbols':syms,'available_count':len(av),'avg_1d_pct':avg([m.get('return_1d_pct') for m in av]),'top_movers':sorted([m for m in av if m.get('return_1d_pct') is not None],key=lambda x:abs(x.get('return_1d_pct') or 0),reverse=True)[:5]})
 activation='inactive'; direction='mixed'; score=50; tags=[]
 if avg1 is not None and avg1>=1.5 and (breadth or 0)>=45:
  activation='active'; direction='positive_spillover'; score=70; tags=['source_basket_strength']
 if avg1 is not None and avg1<=-1.5 and (downside or 0)>=45:
  activation='active'; direction='negative_spillover'; score=70; tags=['source_basket_weakness']
 if any(any('event_like_move' in classify_mover(m) for m in b['top_movers']) for b in branches):
  score+=8; tags.append('downstream_event_like_move')
 score=max(0,min(100,score))
 return {'theme':theme,'label':cfg['label'],'activation':activation,'direction':direction,'confidence':'medium' if activation=='active' and score>=72 else ('low' if activation=='active' else 'observe'),'score':score,'source_avg_1d_pct':avg1,'source_breadth_up_pct':breadth,'source_breadth_down_pct':downside,'tags':tags,'branches':branches,'follow_through_horizons':cfg['follow_through_horizons'],'hypothesis':f"{cfg['label']} may create {direction} follow-through over {cfg['follow_through_horizons']} trading days."}

def main():
 ap=argparse.ArgumentParser(description='After-close shock/mover and theme-spillover scout; paper-only hypothesis source')
 ap.add_argument('--output',default='/tmp/market_shock_mover_scout_latest.json')
 args=ap.parse_args(); init_db()
 conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
 syms=universe_symbols(conn)
 metrics={s:metric(conn,s) for s in syms}
 conn.close()
 movers=[]
 for m in metrics.values():
  if not m.get('available'): continue
  tags=classify_mover(m)
  if tags:
   x=dict(m); x['tags']=tags; x['data_timing']='confirmed_daily_bar'; movers.append(x)
 seed_rows=mover_seed_rows()
 seed_symbols={r.get('symbol') for r in seed_rows}
 for row in seed_rows:
  sym=row.get('symbol')
  if sym in {m.get('symbol') for m in movers}:
   continue
  ch=row.get('change_pct')
  if ch is None or abs(float(ch))<5:
   continue
  tags=['provisional_intraday_seed','surge' if float(ch)>0 else 'crash']
  if abs(float(ch))>=8: tags.append('event_like_move')
  movers.append({'symbol':sym,'market':row.get('market') or market_of(sym),'available':False,'latest_date':row.get('captured_at'),'close':row.get('price'),'return_1d_pct':round(float(ch),2),'return_5d_pct':None,'return_20d_pct':None,'gap_pct':None,'intraday_return_pct':None,'volume_ratio_20d':None,'shock_score':min(100,round(abs(float(ch))*8,2)),'tags':tags,'name':row.get('name'),'source':row.get('source'),'data_timing':'provisional_intraday_seed'})
 movers=sorted(movers,key=lambda x:(x.get('shock_score') or 0, abs(x.get('return_1d_pct') or 0)),reverse=True)
 surges=[m for m in movers if (m.get('return_1d_pct') or 0)>0][:30]
 crashes=[m for m in movers if (m.get('return_1d_pct') or 0)<0][:30]
 themes=[theme_assessment(k,v,metrics) for k,v in THEME_GRAPH.items()]
 active=[t for t in themes if t['activation']=='active']
 hypotheses=[]
 for t in active[:5]:
  hypotheses.append({'target_type':'theme_spillover','target':t['theme'],'experiment_type':'theme_spillover_follow_through','priority':'high' if t['score']>=75 else 'medium','hypothesis':t['hypothesis'],'success_criteria':{'follow_through_horizon_days':t['follow_through_horizons'],'downstream_relative_strength':'positive for positive_spillover, defensive/avoidance for negative_spillover'},'evidence':{'direction':t['direction'],'score':t['score'],'source_avg_1d_pct':t['source_avg_1d_pct'],'tags':t['tags'],'branches':[{k:b[k] for k in ('branch','avg_1d_pct','available_count')} for b in t['branches']]}})
 packet={'run_at':now(),'mode':'after_close_market_shock_mover_scout','real_trading':False,'authority':'hypothesis_source_only','summary':{'scanned_symbol_count':len(syms),'mover_seed_count':len(mover_seed_rows()),'provisional_mover_count':sum(1 for m in movers if m.get('data_timing')=='provisional_intraday_seed'),'mover_count':len(movers),'surge_count':len(surges),'crash_count':len(crashes),'active_theme_count':len(active),'hypothesis_count':len(hypotheses)},'top_surges':surges[:15],'top_crashes':crashes[:15],'theme_spillovers':themes,'hypotheses':hypotheses,'next_actions':['Feed hypotheses to research_hypothesis/experiment_planner only; do not create direct recommendations or active strategy promotions.']}
 warnings=[]
 if not movers: warnings.append('no_movers_detected_or_price_data_stale')
 attach_contract(packet,'market_shock_mover_scout',status='ok' if not warnings else 'degraded',outputs={'hypothesis_count':len(hypotheses),'active_theme_count':len(active)},metrics=packet['summary'],warnings=warnings,next_actions=packet['next_actions'])
 Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
