#!/usr/bin/env python3
from __future__ import annotations
import json, sys, collections
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract

def load(p,d=None):
    try: return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception: return d if d is not None else {}

def yday_utc():
    return (datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat()

def trade_date(tr):
    return str(tr.get('date') or tr.get('run_at') or '')[:10]

def main():
    asof=yday_utc()
    perf=load('/tmp/fund_performance_evaluator_latest.json',{})
    risk=load('/tmp/fund_risk_guardian_latest.json',{})
    price=load('/tmp/paper_fund_price_replay_latest.json',{})
    live=load('/tmp/paper_fund_simulator_latest.json',{})
    recs=load('/tmp/recommendations_latest.json',{})
    rec_by_symbol={r.get('symbol'):r for r in recs.get('items') or []}
    top=[f for f in (perf.get('evaluations') or []) if f.get('tier') in ('champion','candidate')][:12]
    if not top:
        top=(perf.get('summary') or {}).get('top_fund') and [(perf.get('summary') or {}).get('top_fund')] or []
    top_ids={f.get('id') for f in top if f.get('id')}
    top_quality={f.get('id'):float(f.get('fund_quality_score') or max(1,float(f.get('return_pct') or 0))) for f in top if f.get('id')}
    rows=collections.defaultdict(lambda:{'symbol':None,'buy_fund_count':0,'holding_fund_count':0,'weighted_score':0.0,'participating_funds':[],'fund_styles':collections.Counter(),'latest_buy_date':None,'sources':collections.Counter(),'buy_reasons':collections.Counter(),'fund_details':[]})
    # Yesterday / latest-available price replay buys. If yesterday has no data, use the latest replay date <= today.
    price_trades=price.get('trades') or []
    dates=sorted({trade_date(t) for t in price_trades if trade_date(t)})
    basis_date=max([d for d in dates if d <= datetime.now(timezone.utc).date().isoformat()] or dates[-1:] or [asof])
    for tr in price_trades:
        if tr.get('side')!='buy' or trade_date(tr)!=basis_date: continue
        fid=tr.get('fund_id')
        if top_ids and fid not in top_ids: continue
        sym=tr.get('symbol')
        if not sym: continue
        row=rows[sym]; row['symbol']=sym
        if fid not in row['participating_funds']:
            row['buy_fund_count']+=1; row['participating_funds'].append(fid)
        row['weighted_score'] += max(1, top_quality.get(fid, 5)/10)
        style=next((f.get('style') for f in top if f.get('id')==fid), None)
        if style: row['fund_styles'][style]+=1
        row['latest_buy_date']=max(row['latest_buy_date'] or trade_date(tr), trade_date(tr))
        row['sources']['price_replay_yday_buy']+=1
        if tr.get('reason'): row['buy_reasons'][tr.get('reason')]+=1
        row['fund_details'].append({'fund_id':fid,'style':style,'buy_date':trade_date(tr),'buy_price':tr.get('price'),'reason':tr.get('reason'),'score':tr.get('score'),'quality':round(top_quality.get(fid,0),2)})
    # Live holdings as secondary current asset context.
    live_state=load('/tmp/paper_fund_league_state.json',{})
    for f in live_state.get('funds') or []:
        fid=f.get('id')
        if top_ids and fid not in top_ids: continue
        for sym,pos in (f.get('positions') or {}).items():
            row=rows[sym]; row['symbol']=sym
            if fid not in row['participating_funds']: row['participating_funds'].append(fid)
            row['holding_fund_count']+=1
            row['weighted_score']+=max(1,top_quality.get(fid,3)/12)
            if f.get('style'): row['fund_styles'][f.get('style')]+=1
            row['sources']['live_holding']+=1
    items=[]
    for sym,row in rows.items():
        rec=rec_by_symbol.get(sym) or {}
        item={
            'symbol':sym,
            'asof_date':basis_date,
            'buy_fund_count':row['buy_fund_count'],
            'holding_fund_count':row['holding_fund_count'],
            'weighted_score':round(row['weighted_score'],2),
            'participating_funds':row['participating_funds'][:12],
            'fund_styles':dict(row['fund_styles'].most_common()),
            'latest_buy_date':row['latest_buy_date'],
            'sources':dict(row['sources'].most_common()),
            'buy_reasons':dict(row['buy_reasons'].most_common(4)),
            'fund_details':sorted(row.get('fund_details') or [], key=lambda x:(x.get('quality') or 0), reverse=True)[:12],
            'recommendation_bucket':rec.get('recommendation_bucket'),
            'action_label':rec.get('recommendation_bucket_label') or rec.get('action_label') or rec.get('action'),
            'score':rec.get('score'),
            'trade_eligible':rec.get('trade_eligible'),
            'risk_notes':rec.get('risk_notes'),
            'last_price':rec.get('last_price'),
            'name':rec.get('name'),
            'market':rec.get('market'),
            'caveat':'paper-only fund consensus; not real trading authority',
        }
        items.append(item)
    items=sorted(items,key=lambda x:(x['weighted_score'],x['buy_fund_count'],x['holding_fund_count']),reverse=True)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'fund_recommendation_consensus','real_trading':False,'authority':'paper_only_yesterday_top_fund_consensus','asof_date':basis_date,'item_count':len(items),'items':items,'summary':{'top_symbols':[x['symbol'] for x in items[:10]],'item_count':len(items),'top_weighted_score':items[0]['weighted_score'] if items else None,'basis':'latest available daily fund buy consensus, normally yesterday close'},'warnings':['uses_price_replay_top_fund_buy_proxy_until_live_holdings_mature'],'next_actions':['Use this as the primary recommendation view; keep recommendation/risk gate state visible as a guardrail.']}
    attach_contract(packet,'fund_recommendation_consensus_agent',status='ok',outputs={'item_count':len(items)},metrics=packet['summary'],warnings=packet['warnings'],next_actions=packet['next_actions'])
    Path('/tmp/fund_recommendation_consensus_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    Path(ROOT/'static/fund_recommendation_consensus_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
