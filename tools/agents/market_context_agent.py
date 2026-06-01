#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.database import get_settings, init_db
from tools.agents.lib.agent_contract import attach_contract

THEMES={
 'semiconductors':{
  'label':'미국 반도체 강세','us':['SMH','SOXX','NVDA','AMD','AVGO','TSM','ASML','AMAT','LRCX','MU','QCOM','INTC'],
  'kr':['005930.KS','000660.KS','042700.KQ','039030.KQ','000990.KS','036930.KQ','064760.KQ','058470.KQ','090460.KQ','095340.KQ','108320.KS','240810.KQ','014680.KS','033160.KQ'],
  'trigger_1d':2.0,'trigger_breadth':70,'risk_note':'미국 반도체 basket 강세는 한국 반도체주 개장 반응에 긍정적일 수 있으나 갭 추격 리스크가 있습니다.'},
 'ai_infra':{
  'label':'미국 AI/인프라 강세','us':['NVDA','AVGO','SMCI','ANET','DELL','HPE','MSFT','GOOGL','META'],
  'kr':['005930.KS','000660.KS','042700.KQ','108320.KS','267260.KS','010120.KS'],
  'trigger_1d':2.0,'trigger_breadth':67,'risk_note':'AI 인프라/서버/전력 테마 강세는 국내 반도체·전력기기·장비주에 긍정 컨텍스트가 될 수 있습니다.'},
 'energy':{
  'label':'미국 에너지 강세','us':['XLE','CVX','XOM','COP','OXY','SLB','HAL'],
  'kr':['096770.KS','010950.KS','267250.KS','078930.KS'],
  'trigger_1d':1.5,'trigger_breadth':65,'risk_note':'미국 에너지 강세는 국내 정유/에너지 관련주에 긍정 컨텍스트가 될 수 있습니다.'},
 'defense_industrials':{
  'label':'미국 방산/산업재 강세','us':['ITA','LMT','NOC','RTX','GD','CAT','GE','ETN'],
  'kr':['012450.KS','047810.KS','329180.KS','064350.KS','272210.KS'],
  'trigger_1d':1.5,'trigger_breadth':65,'risk_note':'미국 방산/산업재 강세는 국내 방산·기계 관련주 컨텍스트로 참고할 수 있습니다.'},
}
US_RISK=['SPY','QQQ','IWM','DIA','^VIX','USD/KRW','KRW=X']; KR_BENCH=['^KS11','^KQ11','069500.KS','229200.KS']
def utc_now(): return datetime.now(timezone.utc).isoformat()
def market_of(sym): return 'KR' if sym.endswith(('.KS','.KQ')) or sym.startswith('^KS') or sym.startswith('^KQ') else 'US'
def rows(conn, sym, limit=90): return conn.execute("SELECT date, close, volume FROM price_bars WHERE symbol=? AND timeframe='1d' ORDER BY date DESC LIMIT ?",(sym,limit)).fetchall()[::-1]
def pct(a,b):
 try:
  if b in (None,0): return None
  return round((float(a)/float(b)-1)*100,2)
 except Exception: return None
def metric(conn,sym):
 r=rows(conn,sym,70)
 if len(r)<2: return {'symbol':sym,'market':market_of(sym),'available':False}
 last=r[-1]; prev=r[-2]
 out={'symbol':sym,'market':market_of(sym),'available':True,'latest_date':last['date'],'close':float(last['close']),'return_1d_pct':pct(last['close'],prev['close'])}
 if len(r)>=6: out['return_5d_pct']=pct(last['close'],r[-6]['close'])
 if len(r)>=21: out['return_20d_pct']=pct(last['close'],r[-21]['close'])
 return out
def avg(vals):
 vals=[float(v) for v in vals if v is not None]
 return round(sum(vals)/len(vals),2) if vals else None
def breadth(ms,threshold=0):
 av=[m for m in ms if m.get('available') and m.get('return_1d_pct') is not None]
 return round(sum(1 for m in av if m['return_1d_pct']>threshold)/len(av)*100,2) if av else None
def fx_context(metrics):
 fx=metrics.get('USD/KRW') or metrics.get('KRW=X') or metrics.get('USDKRW=X') or {}
 if not fx.get('available'):
  return {'available':False,'label':'USD/KRW unavailable','tags':['fx_unavailable'],'kr_equity_impact':'unknown','risk_note':'USD/KRW 시계열이 없어 한국장 환율 민감도를 반영하지 못합니다.'}
 r1=fx.get('return_1d_pct'); r5=fx.get('return_5d_pct'); score=50; tags=[]
 if r1 is not None:
  if r1>=0.5: score+=12; tags.append('usdkrw_1d_up')
  if r1<=-0.5: score-=8; tags.append('usdkrw_1d_down')
 if r5 is not None:
  if r5>=1.5: score+=10; tags.append('usdkrw_5d_up')
  if r5<=-1.5: score-=8; tags.append('usdkrw_5d_down')
 impact='exporter_support_importer_pressure' if score>=62 else ('krw_strength_importer_support_exporter_pressure' if score<=42 else 'neutral')
 return {'available':True,'symbol':fx.get('symbol'),'latest_date':fx.get('latest_date'),'usdkrw':fx.get('close'),'return_1d_pct':r1,'return_5d_pct':r5,'impact_score':max(0,min(100,score)),'tags':tags,'kr_equity_impact':impact,'risk_note':'USD/KRW 상승은 수출주 원화 환산 이익 기대에는 우호적일 수 있지만 외국인 수급/시장 위험회피와 함께 해석해야 합니다.'}
def assess_theme(name,cfg,metrics):
 us=[metrics.get(s,{'symbol':s,'available':False}) for s in cfg['us']]
 avg1=avg([m.get('return_1d_pct') for m in us]); avg5=avg([m.get('return_5d_pct') for m in us]); br=breadth(us,0)
 score=50; tags=[]
 if avg1 is not None and avg1>=cfg['trigger_1d']: score+=18; tags.append('us_theme_surge')
 if br is not None and br>=cfg['trigger_breadth']: score+=10; tags.append('theme_breadth_strong')
 if avg5 is not None and avg5>=5: score+=6; tags.append('theme_5d_momentum')
 score=max(0,min(100,score)); impact='positive' if score>=62 else ('negative' if score<=35 else 'neutral')
 gap='high_chase_risk' if score>=75 else ('moderate_chase_risk' if score>=62 else 'normal')
 return {'theme':name,'label':cfg['label'],'impact_score':score,'expected_impact':impact,'gap_chase_risk':gap,'source_tags':tags,'source_symbols':cfg['us'],'affected_symbols':cfg['kr'],'us_avg_1d_pct':avg1,'us_avg_5d_pct':avg5,'us_breadth_positive_pct':br,'risk_note':cfg['risk_note'],'summary':f"{cfg['label']}: 1D {avg1}%, 5D {avg5}%, breadth {br}%, impact {score}"}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--output',default='/tmp/market_context_latest.json'); args=ap.parse_args(); init_db()
 all_syms=sorted(set(US_RISK+KR_BENCH+[s for c in THEMES.values() for s in c['us']+c['kr']]))
 conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
 metrics={s:metric(conn,s) for s in all_syms}; conn.close()
 fx=fx_context(metrics)
 themes={k:assess_theme(k,v,metrics) for k,v in THEMES.items()}
 active=[v for v in themes.values() if v['expected_impact']!='neutral']
 tags=[f"{t['theme']}:{tag}" for t in active for tag in t.get('source_tags',[])]
 best=max(themes.values(), key=lambda x:x['impact_score']) if themes else {}
 impact_map={'KR':{},'US':{}}
 for k,t in themes.items():
  impact_map['KR'][k]={kk:t[kk] for kk in ['theme','label','impact_score','expected_impact','gap_chase_risk','source_tags','affected_symbols','risk_note','summary']}
  impact_map['US'][k]={'theme':k,'label':t['label'],'impact_score':t['impact_score'],'source_symbols':t['source_symbols'],'source_tags':t['source_tags']}
 packet={'run_at':utc_now(),'mode':'theme_based_cross_market_context','real_trading':False,'summary':{'tags':tags + (fx.get('tags') or []),'active_themes':[t['theme'] for t in active],'top_theme':best.get('theme'),'top_theme_label':best.get('label'),'cross_market_impact_score':best.get('impact_score'),'gap_chase_risk':best.get('gap_chase_risk'),'fx_context':fx},'themes':themes,'impact_map':impact_map,'markets':{'symbols':list(metrics.values()),'fx_context':fx},'next_actions':['Use as small context boost/risk note; never override audit, disclosure, or committee gates.']}
 warnings=[]
 for k,cfg in THEMES.items():
  if not any(metrics.get(s,{}).get('available') for s in cfg['us']): warnings.append(f'{k}: US source unavailable')
 attach_contract(packet,'market_context_agent',status='ok' if not warnings else 'degraded',outputs={'active_themes':packet['summary']['active_themes'],'top_theme':best.get('theme'),'cross_market_impact_score':best.get('impact_score')},metrics={'theme_count':len(THEMES),'active_theme_count':len(active)},warnings=warnings,next_actions=packet['next_actions'])
 Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
