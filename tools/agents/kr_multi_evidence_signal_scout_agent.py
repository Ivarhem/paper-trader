#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sqlite3,sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, list_universe_members
from tools.agents.lib.agent_contract import attach_contract
from tools.agents import recommendation_auditor as aud

def read_json(path):
    p=Path(path)
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception: return {}

def pct(a,b): return round((float(a)/float(b)-1)*100,2) if b else None

def build_maps():
    fund=read_json('/tmp/fund_consensus_latest.json')
    fund_by={}
    for row in fund.get('symbol_consensus') or fund.get('items') or []:
        sym=str(row.get('symbol') or '').upper();
        if sym: fund_by[sym]=row
    # fallback from top_funds holdings
    if not fund_by:
        tmp={}
        for f in fund.get('top_funds') or []:
            for h in f.get('holdings') or []:
                sym=str(h.get('symbol') or '').upper();
                if not sym: continue
                b=tmp.setdefault(sym,{'symbol':sym,'votes':0,'weighted_score':0,'funds':[]})
                b['votes']+=1; b['weighted_score']+=float(h.get('score') or 0)/100; b['funds'].append(f.get('id'))
        fund_by=tmp
    inv=read_json('/tmp/investor_flow_seed_latest.json')
    inv_by={}
    for row in inv.get('top_symbols') or []:
        sym=str(row.get('symbol') or '').upper();
        if sym: inv_by[sym]=row
    market=read_json('/tmp/market_context_latest.json')
    themes=market.get('themes') or {}
    theme_hits={}
    for t in themes.values():
        for sym in t.get('affected_symbols') or []:
            theme_hits.setdefault(str(sym).upper(),[]).append(t)
    rec_ctx=read_json('/tmp/recommendation_market_context_latest.json')
    rec_by={str(x.get('symbol') or '').upper():x for x in rec_ctx.get('items') or [] if x.get('symbol')}
    supply=read_json('/tmp/supply_close_strength_scout_latest.json')
    supply_by={str(x.get('symbol') or '').upper():x for x in supply.get('items') or [] if x.get('symbol')}
    return fund_by,inv_by,theme_hits,rec_by,supply_by

def evidence_score(sym, fund_by, inv_by, theme_hits, rec_by, supply_by):
    score=0; evidence=[]; blockers=[]
    fund=fund_by.get(sym) or {}
    votes=int(fund.get('votes') or len(fund.get('funds') or []) or 0)
    fscore=float(fund.get('weighted_score') or 0)
    if votes:
        add=min(22, 6+votes*1.4+min(8,fscore/8)); score+=add; evidence.append(f'fund_consensus votes={votes} weighted={round(fscore,2)} +{round(add,1)}')
    inv=inv_by.get(sym) or {}
    investors=inv.get('investors') or []
    if investors:
        rank=float(inv.get('best_rank') or 99); add=max(3, 16-rank*1.5)+len(investors)*2; score+=add; evidence.append(f'investor_flow_seed {"/".join(investors)} rank={rank:g} +{round(add,1)}')
    themes=theme_hits.get(sym) or []
    if themes:
        add=sum(max(0,float(t.get('impact_score') or 0)-45)/5 for t in themes[:3]); add=min(18,add+len(themes)*2); score+=add; evidence.append('market_theme ' + ', '.join((t.get('label') or t.get('theme') or '') for t in themes[:3]) + f' +{round(add,1)}')
    rc=rec_by.get(sym) or {}
    if rc:
        ex5=float(rc.get('excess_5d_pct') or 0); ex20=float(rc.get('excess_20d_pct') or 0); vol=float(rc.get('volume_ratio_20d') or 0)
        if ex5>0: score+=min(8,ex5/2); evidence.append(f'relative_5d +{ex5}')
        if ex20>0: score+=min(8,ex20/3); evidence.append(f'relative_20d +{ex20}')
        if vol>=0.8: score+=min(6,vol*3); evidence.append(f'volume_ratio {vol}x')
        if ex20 < -10: blockers.append('weak_20d_relative_strength')
    sp=supply_by.get(sym) or {}
    if sp:
        add=min(14,float(sp.get('score') or 0)/8); score+=add; evidence.append(f'supply_close_proxy score={sp.get("score")} +{round(add,1)}')
    return round(score,2), evidence, blockers

def eval_current_entry(conn,sym,horizon=20):
    rows=conn.execute('SELECT date, open, high, low, close, volume FROM price_bars WHERE symbol=? AND timeframe="1d" ORDER BY date ASC',(sym,)).fetchall()
    if len(rows)<80: return None
    hist=rows[:-horizon]; fut=rows[-horizon:]
    if len(hist)<60 or len(fut)<10: return None
    entry=float(hist[-1]['close'])
    # Multi-evidence KR candidates should be judged against relative return, not a fixed 7% cap in a hot benchmark tape.
    # Use a wider paper-only target and keep the conservative stop; this is evaluation only.
    target=entry*1.16; stop=entry*0.95
    bench=aud.benchmark_return(conn,hist[-1]['date'],horizon,aud.benchmark_symbol_for(sym))
    result,days,maxp,minp,exit_px,reason=aud.judge(fut,target,stop,entry=entry,fill_model='close_only')
    final=float(exit_px) if exit_px is not None else float(fut[-1]['close'])
    fret=pct(final,entry)
    return {'entry_date':hist[-1]['date'],'result':result,'final_return_pct':fret,'benchmark_return_pct':bench,'excess_return_pct':round(fret-bench,2) if bench is not None else None,'max_upside_pct':pct(maxp,entry) if maxp else None,'max_drawdown_pct':pct(minp,entry) if minp else None,'exit_reason':reason}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',default='/tmp/kr_multi_evidence_signal_scout_latest.json'); args=ap.parse_args()
    init_db(); conn=sqlite3.connect(get_settings().database_path, timeout=45); conn.row_factory=sqlite3.Row
    fund_by,inv_by,theme_hits,rec_by,supply_by=build_maps()
    syms=set([m['symbol'] for m in list_universe_members(status='active') if str(m['symbol']).endswith(('.KS','.KQ'))])
    syms |= {s for s in fund_by if s.endswith(('.KS','.KQ'))} | {s for s in inv_by if s.endswith(('.KS','.KQ'))} | {s for s in theme_hits if s.endswith(('.KS','.KQ'))} | {s for s in rec_by if s.endswith(('.KS','.KQ'))}
    rows=[]
    for sym in sorted(syms):
        score,evidence,blockers=evidence_score(sym,fund_by,inv_by,theme_hits,rec_by,supply_by)
        if score<=0: continue
        outcome=eval_current_entry(conn,sym)
        verdict='multi_evidence_watch'
        if score>=32 and len(evidence)>=3 and not blockers: verdict='multi_evidence_research_candidate'
        if outcome and outcome.get('excess_return_pct') is not None and outcome.get('excess_return_pct') < 0:
            blockers.append('outcome_preview_lags_benchmark')
            if verdict == 'multi_evidence_research_candidate': verdict='multi_evidence_watch'
        if blockers and score<45: verdict='blocked_by_relative_strength'
        rows.append({'symbol':sym,'market':'KR','score':score,'evidence_count':len(evidence),'evidence':evidence,'blockers':blockers,'verdict':verdict,'outcome_preview':outcome,'authority':'multi_evidence_research_only_no_recommendation_authority'})
    conn.close()
    rows.sort(key=lambda r:(r['verdict']=='multi_evidence_research_candidate', r['score'], r['evidence_count']), reverse=True)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'kr_multi_evidence_signal_scout','real_trading':False,'authority':'research_only_no_order_no_registry_promotion','input_summary':{'fund_symbols':len(fund_by),'investor_seed_symbols':len(inv_by),'theme_symbols':len(theme_hits),'market_context_symbols':len(rec_by),'supply_symbols':len(supply_by)},'item_count':len(rows),'items':rows,'summary':{'research_candidate_count':sum(1 for r in rows if r['verdict']=='multi_evidence_research_candidate'),'watch_count':sum(1 for r in rows if r['verdict']=='multi_evidence_watch'),'blocked_count':sum(1 for r in rows if r['verdict'].startswith('blocked')),'top_symbols':[r['symbol'] for r in rows[:10]],'top_score':rows[0]['score'] if rows else None}}
    attach_contract(packet,'kr_multi_evidence_signal_scout',status='ok',outputs=packet['summary'],metrics=packet['summary'],warnings=[],next_actions=['Backtest multi-evidence snapshots historically before recommendation authority.'] if packet['summary']['research_candidate_count'] else ['No multi-evidence candidate yet; improve evidence coverage/history.'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
