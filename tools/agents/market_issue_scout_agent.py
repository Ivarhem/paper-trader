#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, sqlite3, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.database import get_settings, init_db
from tools.agents.lib.agent_contract import attach_contract

def utc_now(): return datetime.now(timezone.utc).isoformat()
def market_of(sym): return 'KR' if sym.endswith(('.KS','.KQ')) or sym.startswith('^KS') or sym.startswith('^KQ') else 'US'
def rows(conn,sym,limit=80):
 return conn.execute("SELECT date, close, volume FROM price_bars WHERE symbol=? AND timeframe='1d' ORDER BY date DESC LIMIT ?",(sym,limit)).fetchall()[::-1]
def pct(a,b):
 try:
  if b in (None,0): return None
  return round((float(a)/float(b)-1)*100,2)
 except Exception: return None
def avg(xs):
 xs=[float(x) for x in xs if x is not None]
 return round(sum(xs)/len(xs),2) if xs else None
def safe_json(s):
 try: return json.loads(s or '{}')
 except Exception: return {}
def norm_tokens(name):
 if not name: return []
 toks=[]
 for t in ['증권','금융','투자','반도체','전자','전기','바이오','제약','조선','중공업','방산','항공','에너지','화학','정유','은행','보험','건설','자동차','로보','AI','데이터','게임','엔터','전력','전선','기계','철강','리츠']:
  if t.lower() in str(name).lower(): toks.append(t)
 return toks
def load_universe(conn):
 rows=conn.execute("SELECT symbol,status,payload_json FROM universe_members WHERE status IN ('active','watch','candidate')").fetchall()
 out={}
 for row in rows:
  payload=safe_json(row['payload_json'])
  out[row['symbol']]={'status':row['status'],'name':payload.get('name') or row['symbol']}
 return out
def load_price_symbols(conn,limit):
 return [r['symbol'] for r in conn.execute("""
  SELECT symbol, MAX(date) AS latest_date, COUNT(*) AS bar_count
  FROM price_bars
  WHERE timeframe='1d' AND market='stock'
  GROUP BY symbol
  HAVING bar_count >= 21
  ORDER BY latest_date DESC, symbol ASC
  LIMIT ?
 """,(limit,)).fetchall()]
def metric(conn,sym,name,universe_info=None):
 r=rows(conn,sym,70)
 if len(r)<21: return None
 last=r[-1]; prev=r[-2]
 vols=[float(x['volume'] or 0) for x in r[-21:-1] if x['volume'] is not None]
 vavg=sum(vols)/len(vols) if vols else None
 universe_info=universe_info or {}
 return {'symbol':sym,'name':name or sym,'market':market_of(sym),'latest_date':last['date'],'return_1d_pct':pct(last['close'],prev['close']),'return_5d_pct':pct(last['close'],r[-6]['close']) if len(r)>=6 else None,'return_20d_pct':pct(last['close'],r[-21]['close']),'volume_spike':round(float(last['volume'] or 0)/vavg,2) if vavg else None,'tokens':norm_tokens(name or sym),'in_universe':bool(universe_info),'universe_status':universe_info.get('status')}
def main():
 ap=argparse.ArgumentParser(description='Detect dynamic market issue clusters from price/volume moves')
 ap.add_argument('--output',default='/tmp/market_issue_scout_latest.json'); ap.add_argument('--limit-symbols',type=int,default=5000); args=ap.parse_args(); init_db()
 conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
 universe=load_universe(conn)
 symbols=load_price_symbols(conn,args.limit_symbols)
 names={sym:info.get('name') or sym for sym,info in universe.items()}
 rec_path=Path('/tmp/recommendations_latest.json')
 if rec_path.exists():
  try:
   for it in json.loads(rec_path.read_text()).get('items',[]):
    if it.get('symbol') and it['symbol'] not in symbols: symbols.append(it['symbol']); names[it['symbol']]=it.get('name') or it['symbol']
  except Exception: pass
 metrics=[m for s in symbols for m in [metric(conn,s,names.get(s),universe.get(s))] if m]
 conn.close()
 hot=[m for m in metrics if (m.get('return_1d_pct') or 0)>=5 or ((m.get('return_1d_pct') or 0)>=3 and (m.get('volume_spike') or 0)>=1.8) or ((m.get('return_5d_pct') or 0)>=10 and (m.get('return_1d_pct') or 0)>=1)]
 groups=defaultdict(list)
 for m in hot:
  toks=m.get('tokens') or []
  label=toks[0] if toks else ('universe_momentum' if m.get('in_universe') else 'new_discovery_momentum')
  key=(m['market'], label)
  groups[key].append(m)
 issues=[]
 for (market,label),members in groups.items():
  if len(members)<2 and label=='universe_momentum': continue
  members=sorted(members,key=lambda x:(x.get('return_1d_pct') or 0)+(x.get('volume_spike') or 0),reverse=True)[:30]
  avg1=avg([x.get('return_1d_pct') for x in members]); avg5=avg([x.get('return_5d_pct') for x in members]); vsp=avg([x.get('volume_spike') for x in members])
  breadth=round(sum(1 for x in members if (x.get('return_1d_pct') or 0)>0)/len(members)*100,2)
  score=max(0,min(100,50+(avg1 or 0)*3+min((vsp or 1),5)*5+(10 if len(members)>=4 else 0)))
  risk='high_chase_risk' if (avg1 or 0)>=8 or score>=80 else ('moderate_chase_risk' if score>=65 else 'normal')
  if label=='new_discovery_momentum': label_txt=f'{market} 신규 급등 후보'
  elif label=='universe_momentum': label_txt=f'{market} 모멘텀 클러스터'
  else: label_txt=label+' 강세'
  issues.append({'issue_id':f'{market.lower()}_{label}_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")}'.replace(' ','_'),'type':'dynamic_market_issue_cluster','market':market,'label':label_txt,'theme_hint':label,'impact_score':round(score,2),'risk':risk,'expected_impact':'positive','member_count':len(members),'affected_symbols':[x['symbol'] for x in members],'members':members[:12],'avg_1d_return_pct':avg1,'avg_5d_return_pct':avg5,'avg_volume_spike':vsp,'breadth_positive_pct':breadth,'narrative':f"가격/거래량 기반 동적 감지: {label_txt}, 평균 1D {avg1}%, 5D {avg5}%, 거래량 {vsp}배.",'confidence':round(min(0.95,0.45+len(members)*0.06+(0.12 if label not in ('new_discovery_momentum','universe_momentum') else 0)),2),'recommendation_policy':'watch_boost_only' if risk!='normal' else 'context_boost_allowed'})
 issues=sorted(issues,key=lambda x:(x['impact_score'],x['member_count']),reverse=True)[:12]
 packet={'run_at':utc_now(),'mode':'dynamic_market_issue_scout','real_trading':False,'issues':issues,'summary':{'issue_count':len(issues),'scanned_scope':'all_price_bars_symbols_not_universe_only','scanned_symbols':len(symbols),'universe_symbols':len(universe),'hot_symbols':len(hot),'hot_new_discovery_symbols':sum(1 for x in hot if not x.get('in_universe')),'top_issues':[{'label':x['label'],'market':x['market'],'impact_score':x['impact_score'],'risk':x['risk'],'member_count':x['member_count']} for x in issues[:5]]}}
 attach_contract(packet,'market_issue_scout_agent',status='ok',outputs={'issue_count':len(issues),'top_issues':packet['summary']['top_issues']},metrics={'scanned_symbols':len(symbols),'universe_symbols':len(universe),'metric_symbols':len(metrics),'hot_symbols':len(hot),'hot_new_discovery_symbols':packet['summary']['hot_new_discovery_symbols']},warnings=[],next_actions=['Connect high-confidence clusters to Market Regime Gate and validation priority; route non-universe hot movers to candidate discovery validation.'])
 Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
