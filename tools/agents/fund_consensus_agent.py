#!/usr/bin/env python3
from __future__ import annotations
import json,sys,collections
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract

def load(p):
    try: return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception: return {}

def main():
    perf=load('/tmp/fund_performance_evaluator_latest.json'); risk=load('/tmp/fund_risk_guardian_latest.json'); price=load('/tmp/paper_fund_price_replay_latest.json'); live=load('/tmp/paper_fund_simulator_latest.json')
    risk_ids={x.get('fund_id') for x in risk.get('findings') or [] if x.get('issue') in ('mdd_high','turnover_high')}
    top=[f for f in (perf.get('evaluations') or []) if f.get('tier') in ('champion','candidate') and f.get('id') not in risk_ids][:10]
    symbol_votes=collections.defaultdict(lambda:{'votes':0,'weighted_score':0.0,'funds':[]})
    # live holdings are symbol-specific; price replay currently contributes style-level guidance.
    state=load('/tmp/paper_fund_league_state.json')
    live_funds={f.get('id'):f for f in state.get('funds') or []}
    quality={f.get('id'):float(f.get('fund_quality_score') or 0) for f in top}
    for fid,f in live_funds.items():
        if fid not in quality: continue
        w=max(1,quality[fid]/10)
        for sym,pos in (f.get('positions') or {}).items():
            v=symbol_votes[sym]; v['votes']+=1; v['weighted_score']+=w; v['funds'].append(fid)
    # Live holdings can be empty early in the day. Use recent buy trades from top
    # replay/price-replay funds as a paper-only symbol consensus proxy, without
    # treating it as direct trading authority.
    top_ids={f.get('id') for f in top}
    recent_buys=[]
    for src,label in [(price,'price_replay'),(live,'live_paper')]:
        for tr in (src.get('trades') or [])[-800:]:
            if tr.get('side') != 'buy' or tr.get('fund_id') not in top_ids or not tr.get('symbol'):
                continue
            recent_buys.append((label,tr))
    for label,tr in recent_buys[-400:]:
        fid=tr.get('fund_id'); sym=tr.get('symbol')
        w=max(1,quality.get(fid,5)/12)
        if label == 'price_replay': w *= 0.55
        v=symbol_votes[sym]; v['votes']+=1; v['weighted_score']+=w; v['funds'].append(fid)
    consensus=[{'symbol':sym,'votes':v['votes'],'weighted_score':round(v['weighted_score'],2),'funds':list(dict.fromkeys(v['funds']))[:8]} for sym,v in symbol_votes.items()]
    consensus=sorted(consensus,key=lambda x:(x['weighted_score'],x['votes']),reverse=True)
    style_counts=collections.Counter(f.get('style') for f in top if f.get('style'))
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'fund_consensus','real_trading':False,'authority':'paper_only_top_fund_consensus_overlay','summary':{'top_fund_count':len(top),'symbol_consensus_count':len(consensus),'top_symbols':[x['symbol'] for x in consensus[:10]],'top_styles':dict(style_counts.most_common(6))},'top_funds':top,'symbol_consensus':consensus,'warnings':(['symbol_consensus_from_recent_top_fund_buys_proxy'] if consensus else []),'next_actions':['Use symbol consensus as recommendation score overlay; use style consensus for strategy/router preference.']}
    attach_contract(packet,'fund_consensus_agent',status='ok',outputs={'top_fund_count':len(top),'symbol_consensus_count':len(consensus)},metrics=packet['summary'],warnings=[],next_actions=packet['next_actions'])
    Path('/tmp/fund_consensus_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
