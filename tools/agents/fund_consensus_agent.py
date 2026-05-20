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

def fund_risk_guardrails(risk):
    guardrails={}
    for row in risk.get('findings') or []:
        fid=row.get('fund_id')
        if not fid:
            continue
        entry=guardrails.setdefault(fid, {'issues': [], 'allocation_cap_multiplier': 1.0, 'blocked': False})
        issue=row.get('issue')
        entry['issues'].append({k: row.get(k) for k in ('issue','severity','mdd_pct','trades_per_day') if row.get(k) is not None})
        if row.get('severity') == 'block':
            entry['blocked'] = True
            entry['allocation_cap_multiplier'] = min(entry['allocation_cap_multiplier'], 0.0)
        elif issue == 'mdd_high':
            entry['allocation_cap_multiplier'] = min(entry['allocation_cap_multiplier'], 0.45)
        elif issue == 'turnover_high':
            try:
                tpd = float(row.get('trades_per_day') or 0)
            except Exception:
                tpd = 0.0
            cap = max(0.25, min(1.0, 4.0 / tpd)) if tpd else 0.60
            entry['allocation_cap_multiplier'] = min(entry['allocation_cap_multiplier'], cap)
    for fid, entry in guardrails.items():
        entry['allocation_cap_multiplier'] = round(float(entry.get('allocation_cap_multiplier') or 0), 2)
        entry['policy'] = 'paper_only_allocation_cap_not_standalone_signal'
    return guardrails

def main():
    perf=load('/tmp/fund_performance_evaluator_latest.json'); risk=load('/tmp/fund_risk_guardian_latest.json'); price=load('/tmp/paper_fund_price_replay_latest.json'); live=load('/tmp/paper_fund_simulator_latest.json')
    risk_guardrails=fund_risk_guardrails(risk)
    top=[]
    for f in (perf.get('evaluations') or []):
        if f.get('tier') not in ('champion','candidate'):
            continue
        guard=risk_guardrails.get(f.get('id')) or {}
        if guard.get('blocked'):
            continue
        ff=dict(f)
        if guard:
            ff['fund_risk_guardrail']=guard
        top.append(ff)
        if len(top) >= 10:
            break
    symbol_votes=collections.defaultdict(lambda:{'votes':0,'weighted_score':0.0,'funds':[]})
    # live holdings are symbol-specific; price replay currently contributes style-level guidance.
    state=load('/tmp/paper_fund_league_state.json')
    live_funds={f.get('id'):f for f in state.get('funds') or []}
    quality={f.get('id'):float(f.get('fund_quality_score') or 0) for f in top}
    quality_cap={f.get('id'):(risk_guardrails.get(f.get('id')) or {}).get('allocation_cap_multiplier', 1.0) for f in top}
    for fid,f in live_funds.items():
        if fid not in quality: continue
        w=max(1,quality[fid]/10) * float(quality_cap.get(fid, 1.0) or 1.0)
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
        w=max(1,quality.get(fid,5)/12) * float(quality_cap.get(fid, 1.0) or 1.0)
        if label == 'price_replay': w *= 0.55
        v=symbol_votes[sym]; v['votes']+=1; v['weighted_score']+=w; v['funds'].append(fid)
    consensus=[{'symbol':sym,'votes':v['votes'],'weighted_score':round(v['weighted_score'],2),'funds':list(dict.fromkeys(v['funds']))[:8]} for sym,v in symbol_votes.items()]
    consensus=sorted(consensus,key=lambda x:(x['weighted_score'],x['votes']),reverse=True)
    style_counts=collections.Counter(f.get('style') for f in top if f.get('style'))
    capped_count=sum(1 for f in top if (risk_guardrails.get(f.get('id')) or {}).get('allocation_cap_multiplier', 1.0) < 1.0)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'fund_consensus','real_trading':False,'authority':'paper_only_top_fund_consensus_overlay','summary':{'top_fund_count':len(top),'risk_capped_fund_count':capped_count,'symbol_consensus_count':len(consensus),'top_symbols':[x['symbol'] for x in consensus[:10]],'top_styles':dict(style_counts.most_common(6))},'risk_guardrail_policy':'fund risk findings cap consensus weight/allocation; they do not create standalone buy/sell signals','top_funds':top,'symbol_consensus':consensus,'warnings':(['symbol_consensus_from_recent_top_fund_buys_proxy'] if consensus else []),'next_actions':['Use symbol consensus as recommendation score overlay; use style consensus for strategy/router preference.']}
    attach_contract(packet,'fund_consensus_agent',status='ok',outputs={'top_fund_count':len(top),'symbol_consensus_count':len(consensus)},metrics=packet['summary'],warnings=[],next_actions=packet['next_actions'])
    Path('/tmp/fund_consensus_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
