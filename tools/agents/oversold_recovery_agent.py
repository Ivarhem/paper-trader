#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from tools.agents.lib.agent_contract import attach_contract

def rsi(vals, window=14):
    if len(vals) < window+1: return None
    gains=[]; losses=[]
    for a,b in zip(vals[-window-1:-1], vals[-window:]):
        ch=b-a; gains.append(max(ch,0)); losses.append(abs(min(ch,0)))
    ag=sum(gains)/window; al=sum(losses)/window
    if al == 0: return 100.0
    return 100 - (100/(1+ag/al))

def pct(a,b):
    return round((a-b)/b*100,2) if b else None

def price_features(conn, sym):
    rows=conn.execute("""select date, close, volume from price_bars where symbol=? and timeframe='1d' order by date desc limit 90""",(sym,)).fetchall()
    rows=list(reversed(rows))
    if len(rows)<30: return None
    closes=[float(r['close']) for r in rows]
    c=closes[-1]
    high60=max(closes[-60:]) if len(closes)>=60 else max(closes)
    ma5=sum(closes[-5:])/5
    ma20=sum(closes[-20:])/20
    f={'last_price':c,'rsi14':round(rsi(closes,14),2) if rsi(closes,14) is not None else None,
       'return_5d_pct':pct(c,closes[-6]) if len(closes)>=6 else None,
       'return_20d_pct':pct(c,closes[-21]) if len(closes)>=21 else None,
       'drawdown_from_60d_high_pct':pct(c,high60),
       'above_ma5':c>ma5,'above_ma20':c>ma20}
    return f

def eff_medium(disc):
    return int(disc.get('effective_medium', disc.get('medium',0)) or 0)

def financial_ok(fq):
    if not fq: return False
    if (fq.get('score_adjustment') or 0) <= -15: return False
    supports=fq.get('supports') or []
    warnings=fq.get('warnings') or []
    return bool(supports) and len(warnings)<=1

def has_negative_issue(row):
    disc=row.get('disclosure_risk') or {}
    if (disc.get('high') or 0)>0: return False
    return (disc.get('medium') or 0)>0 or (disc.get('impact_low_negative') or 0)>0 or bool(row.get('market_issue_context'))

def main():
    ap=argparse.ArgumentParser(description='Find paper-only oversold recovery candidates: good fundamentals/outlook plus non-high negative issue and technical rebound setup')
    ap.add_argument('--recommendations',default='/tmp/recommendations_latest.json')
    ap.add_argument('--output',default='/tmp/oversold_recovery_latest.json')
    ap.add_argument('--limit',type=int,default=30)
    args=ap.parse_args()
    data=json.loads(Path(args.recommendations).read_text(encoding='utf-8'))
    conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    candidates=[]; reviewed=0
    for row in data.get('items',[])[:args.limit]:
        sym=row.get('symbol'); reviewed+=1
        if not sym: continue
        fq=row.get('financial_quality') or {}; disc=row.get('disclosure_risk') or {}
        pf=price_features(conn,sym)
        if not pf: continue
        oversold=((pf.get('rsi14') is not None and pf['rsi14']<=40) or (pf.get('return_20d_pct') is not None and pf['return_20d_pct']<=-8) or (pf.get('drawdown_from_60d_high_pct') is not None and pf['drawdown_from_60d_high_pct']<=-15))
        rebound=(pf.get('return_5d_pct') is not None and pf['return_5d_pct']>=0) or pf.get('above_ma5')
        issue_ok=has_negative_issue(row) and (disc.get('high') or 0)==0 and eff_medium(disc)<3
        fund_ok=financial_ok(fq) or (row.get('score') or 0)>=85
        if oversold and rebound and issue_ok and fund_ok:
            score=50
            score += min(20, max(0,(row.get('score') or 0)-65)*0.6)
            score += 10 if financial_ok(fq) else 0
            score += 8 if pf.get('above_ma5') else 0
            score += 6 if (pf.get('return_5d_pct') or -99)>=2 else 0
            score -= 8 if eff_medium(disc)>=2 else 0
            candidates.append({'symbol':sym,'name':row.get('name'),'score':round(score,2),'source_recommendation_score':row.get('score'),'bucket':row.get('recommendation_bucket'),'price_features':pf,'disclosure_risk':disc,'financial_quality':fq,'reason':'실적/재무 지지 또는 높은 추천점수 + high 공시 없음 + 과매도 후 단기 회복 확인','paper_only':True})
    candidates=sorted(candidates,key=lambda x:x['score'],reverse=True)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'oversold_recovery_screen','real_trading':False,'reviewed':reviewed,'candidate_count':len(candidates),'items':candidates[:12], 'summary':{'top_symbols':[x['symbol'] for x in candidates[:5]]}}
    attach_contract(packet,'oversold_recovery_agent',status='ok',metrics={'reviewed':reviewed,'candidate_count':len(candidates)},outputs={'top_symbols':packet['summary']['top_symbols']},warnings=[])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
