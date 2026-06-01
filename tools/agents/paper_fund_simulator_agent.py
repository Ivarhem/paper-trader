#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,random,sqlite3,sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db
from tools.agents.lib.agent_contract import attach_contract
from tools.agents.lib.fx import latest_usdkrw, normalize_trade_price, price_to_krw, fx_rate_for_symbol
from tools.agents.lib.runtime import write_json, read_json as artifact_read_json

STATE=Path('/tmp/paper_fund_league_state.json')
LATEST=Path('/tmp/paper_fund_simulator_latest.json')
DEFAULT_CAPITAL=10_000_000.0
BASE_ARCHETYPES=[
 {'name':'Trend Scout','style':'trend','cash_buffer':0.20,'max_positions':6,'risk_per_trade':0.13,'score_min':52,'watch_allowed':True,'target_bias':1.0,'stop_bias':1.0},
 {'name':'Breakout Sprinter','style':'breakout','cash_buffer':0.10,'max_positions':5,'risk_per_trade':0.16,'score_min':56,'watch_allowed':True,'target_bias':1.1,'stop_bias':0.95},
 {'name':'Risk Balanced','style':'balanced','cash_buffer':0.35,'max_positions':7,'risk_per_trade':0.09,'score_min':50,'watch_allowed':True,'target_bias':0.9,'stop_bias':0.9},
 {'name':'Skeptic Cash','style':'defensive','cash_buffer':0.55,'max_positions':4,'risk_per_trade':0.07,'score_min':58,'watch_allowed':False,'target_bias':0.8,'stop_bias':0.85},
 {'name':'Supply Hunter','style':'supply','cash_buffer':0.25,'max_positions':6,'risk_per_trade':0.12,'score_min':50,'watch_allowed':True,'target_bias':1.0,'stop_bias':1.0},
 {'name':'Value Accumulator','style':'value','cash_buffer':0.32,'max_positions':7,'risk_per_trade':0.085,'score_min':52,'watch_allowed':True,'target_bias':0.95,'stop_bias':0.95},
 {'name':'Quality Compounder','style':'quality','cash_buffer':0.28,'max_positions':6,'risk_per_trade':0.095,'score_min':54,'watch_allowed':True,'target_bias':1.0,'stop_bias':0.9},
 {'name':'GARP Selector','style':'garp','cash_buffer':0.24,'max_positions':6,'risk_per_trade':0.10,'score_min':54,'watch_allowed':True,'target_bias':1.05,'stop_bias':0.95},
 {'name':'Dividend Defender','style':'dividend','cash_buffer':0.45,'max_positions':6,'risk_per_trade':0.075,'score_min':54,'watch_allowed':False,'target_bias':0.75,'stop_bias':0.85},
 {'name':'Balance Sheet Guard','style':'financial_safety','cash_buffer':0.50,'max_positions':5,'risk_per_trade':0.07,'score_min':56,'watch_allowed':False,'target_bias':0.8,'stop_bias':0.85},
]

def now(): return datetime.now(timezone.utc).isoformat()
def load_json(path, default):
    return artifact_read_json(path, default)
def save_state(s): write_json(STATE, s)
def pct(v, default=0.0):
    try: return float(v if v is not None else default)
    except Exception: return default

def factor_scores_from_rec(rec):
    fq=rec.get('financial_quality') or {}
    val=rec.get('valuation_metrics') or fq.get('valuation_metrics') or {}
    value=0.0; quality=0.0; growth=0.0; yield_score=0.0; safety=0.0; tags=[]
    per=pct(val.get('per'), None); pbr=pct(val.get('pbr'), None); roe=pct(val.get('roe_pct'), None); ev=pct(val.get('ev_ebitda'), None); dy=pct(val.get('dividend_yield_pct'), None)
    debt=pct(fq.get('debt_ratio_pct'), None); op_margin=pct(fq.get('operating_margin_pct'), None); rev_growth=pct(fq.get('revenue_growth_pct'), None); op_growth=pct(fq.get('operating_income_growth_pct'), None)
    if per is not None:
        if 0 < per <= 8: value+=8; tags.append('low_per')
        elif per <= 12: value+=6
        elif per <= 18: value+=2
        elif per >= 35: value-=5; tags.append('expensive_per')
    if pbr is not None:
        if pbr <= 0.8: value+=8; tags.append('low_pbr')
        elif pbr <= 1.2: value+=6
        elif pbr <= 1.8: value+=2
        elif pbr >= 4: value-=4; tags.append('expensive_pbr')
    if ev is not None:
        if ev <= 6: value+=4
        elif ev >= 20: value-=3
    if roe is not None:
        if roe >= 18: quality+=9; tags.append('high_roe')
        elif roe >= 10: quality+=6
        elif roe < 0: quality-=8; safety-=6; tags.append('negative_roe')
    if op_margin is not None:
        if op_margin >= 12: quality+=5
        elif op_margin < 0: quality-=7; safety-=5
    if rev_growth is not None:
        if rev_growth >= 12: growth+=5; tags.append('revenue_growth')
        elif rev_growth < -15: growth-=5; safety-=3
    if op_growth is not None:
        if op_growth >= 15: growth+=4
        elif op_growth < -25: growth-=5
    if debt is not None:
        if debt <= 80: safety+=6; tags.append('low_debt')
        elif debt <= 160: safety+=2
        elif debt >= 250: safety-=8; tags.append('high_debt')
    if dy is not None:
        if dy >= 5: yield_score+=7; tags.append('high_yield')
        elif dy >= 2: yield_score+=4
    if value > 0 and quality < -2:
        value-=5; tags.append('value_trap_risk')
    return {'available':bool(fq or val),'scores':{'value':value,'quality':quality,'growth':growth,'yield':yield_score,'financial_safety':safety},'tags':tags}

def factor_adjustment_for_style(style, rec):
    factor=factor_scores_from_rec(rec)
    if not factor.get('available'): return 0.0
    s=factor.get('scores') or {}
    weights={
        'value': {'value':0.65,'quality':0.20,'financial_safety':0.15},
        'quality': {'quality':0.60,'financial_safety':0.25,'growth':0.15},
        'garp': {'growth':0.35,'quality':0.35,'value':0.30},
        'dividend': {'yield':0.55,'financial_safety':0.30,'quality':0.15},
        'financial_safety': {'financial_safety':0.55,'quality':0.30,'value':0.15},
        'defensive': {'financial_safety':0.25,'quality':0.15},
        'balanced': {'quality':0.15,'value':0.10,'financial_safety':0.10},
    }.get(style, {})
    adj=sum(float(s.get(k) or 0)*w for k,w in weights.items())
    if 'value_trap_risk' in (factor.get('tags') or []) and style in ('value','garp','dividend'): adj-=4
    return max(-10,min(12,adj))

def latest_price_map(conn):
    rows=conn.execute("""SELECT p.symbol,p.date,p.close FROM price_bars p JOIN (SELECT symbol, max(date) date FROM price_bars WHERE timeframe='1d' GROUP BY symbol) m ON p.symbol=m.symbol AND p.date=m.date WHERE p.timeframe='1d'""").fetchall()
    return {r['symbol']:{'date':r['date'],'close':float(r['close'])} for r in rows}

def seed_funds(n, capital):
    funds=[]
    for i in range(n):
        base=dict(BASE_ARCHETYPES[i%len(BASE_ARCHETYPES)])
        gen=i//len(BASE_ARCHETYPES)
        rng=random.Random(1000+i)
        for k,span in [('cash_buffer',0.08),('risk_per_trade',0.03),('score_min',4),('target_bias',0.12),('stop_bias',0.08)]:
            base[k]=round(max(0.01, base[k]+rng.uniform(-span,span)),3)
        base['average_down_enabled']=True; base['pyramid_enabled']=True; base['max_symbol_exposure_pct']=0.24; base['average_down_trigger_pct']=3.0; base['pyramid_trigger_pct']=2.0
        base['id']=f'fund_{i+1:03d}'; base['name']=f"{base['name']} G{gen+1}"; base['generation']=1; base['status']='active'; base['created_at']=now(); base['cash']=capital; base['initial_capital']=capital; base['positions']={}; base['realized_pnl']=0.0; base['trade_count']=0; base['history']=[]
        funds.append(base)
    return funds

def rec_score_for_fund(rec, fund):
    vb=rec.get('validation_basis') or {}; router=((vb.get('strategy_context_router') or {}).get('top_signal_decisions') or [{}])[0] or {}
    score=pct(rec.get('score'))
    style=fund.get('style')
    fam=router.get('family')
    if style=='trend' and fam=='trend_strength': score+=8
    if style=='breakout' and fam=='breakout_volume': score+=8
    if style=='supply' and pct(vb.get('supply_close_score_adjustment_pct'))>0: score+=8
    if style=='defensive': score-=max(0, pct(rec.get('downside_stop_pct'))-6)*2
    score += factor_adjustment_for_style(style, rec)
    audit=rec.get('audit_reliability_contract') or vb.get('audit_reliability_contract') or {}
    tags={x.get('code') for x in (rec.get('audit_reliability_tags') or vb.get('audit_reliability_tags') or audit.get('labels') or []) if isinstance(x,dict)}
    axes=audit.get('trust_axes') or {}
    regime_fit=audit.get('regime_fit') or {}
    if style=='defensive':
        score += max(0, pct(axes.get('tail_safety'))-55) * 0.18
        if 'crash_or_left_tail_sensitive' in tags or 'weak_excess_reliability' in tags: score -= 10
    if style in ('trend','breakout','volume_surge'):
        score += max(0, pct(axes.get('return_edge'))-55) * 0.12
        if 'context_dependent' in tags and pct(regime_fit.get('score')) < 45: score -= 6
    if style=='balanced':
        score += max(0, pct(axes.get('consistency'))-50) * 0.10
        if 'thin_or_uncertain_sample' in tags: score -= 4
    score += max(-5, min(5, (pct(regime_fit.get('score'))-50) * 0.08))
    if router.get('decision')=='prefer': score+=5
    if router.get('decision')=='deprioritize': score-=8
    if rec.get('recommendation_bucket')=='rejected': score-=12
    if rec.get('action')=='avoid': score-=30
    return score

def value_fund(fund, prices):
    pos_val=0.0
    fx=prices.get('_fx') or {}
    for sym,pos in fund.get('positions',{}).items():
        px=normalize_trade_price(sym, (prices.get(sym) or {}).get('close') or pos.get('entry_price') or 0)
        px_krw=price_to_krw(sym, px, fx)
        pos_val += float(pos.get('qty') or 0)*px_krw
    equity=float(fund.get('cash') or 0)+pos_val
    return equity,pos_val


def rebalance_budget(fund, pos, px, prices, direction):
    equity,pos_val=value_fund(fund,prices)
    if equity <= 0 or px <= 0:
        return 0.0
    current=float(pos.get('qty') or 0)*price_to_krw(pos.get('symbol') or '', px, prices.get('_fx') or {})
    exposure_room=max(0.0, equity*float(fund.get('max_symbol_exposure_pct') or 0.24)-current)
    deployable=max(0.0, equity*(1-float(fund.get('cash_buffer') or 0))-pos_val)
    risk_slice=0.45 if direction == 'pyramid_winner' else 0.40
    return min(float(fund.get('cash') or 0), exposure_room, equity*float(fund.get('risk_per_trade') or 0.1)*risk_slice, deployable)

def add_to_position(fund, sym, pos, px, rec, budget, run_at, reason, score):
    px=normalize_trade_price(sym, px)
    if budget <= 0 or px <= 0: return None
    fx=rec.get('_fx') or {}
    px_krw=price_to_krw(sym, px, fx)
    qty=budget/px_krw
    if qty <= 0: return None
    old_qty=float(pos.get('qty') or 0); new_qty=old_qty+qty
    pos['entry_price']=normalize_trade_price(sym, ((float(pos.get('entry_price') or px)*old_qty)+(px*qty))/new_qty)
    pos['entry_fx']=((float(pos.get('entry_fx') or fx_rate_for_symbol(sym, fx))*old_qty)+(fx_rate_for_symbol(sym, fx)*qty))/new_qty
    pos['qty']=new_qty; pos['last_add_run_at']=run_at; pos['add_count']=int(pos.get('add_count') or 0)+1
    if reason == 'average_down': pos['average_down_count']=int(pos.get('average_down_count') or 0)+1
    if reason == 'pyramid_winner': pos['pyramid_count']=int(pos.get('pyramid_count') or 0)+1
    pos['target']=rec.get('target_1') or px*(1+pct(rec.get('upside_1_pct'))/100)
    pos['stop']=rec.get('stop_reference') or px*(1+pct(rec.get('downside_stop_pct'))/100)
    fund['cash']=float(fund.get('cash') or 0)-budget
    fund['trade_count']=int(fund.get('trade_count') or 0)+1
    return {'fund_id':fund['id'],'symbol':sym,'side':'buy','price':round(px,2),'qty':round(qty,6),'budget':round(budget,2),'reason':reason,'score':round(score,2)}

def step_fund(fund, recs, prices, run_at):
    if fund.get('status')!='active': return []
    trades=[]
    # exits first
    for sym,pos in list((fund.get('positions') or {}).items()):
        px=(prices.get(sym) or {}).get('close')
        if not px: continue
        target=pos.get('target'); stop=pos.get('stop')
        exit_reason=None
        if target and px>=target: exit_reason='target_hit'
        elif stop and px<=stop: exit_reason='stop_hit'
        # if symbol no longer in recs and weak, exit slowly
        rec=next((r for r in recs if r.get('symbol')==sym), None)
        if not exit_reason and (not rec or rec.get('action')=='avoid' or rec.get('recommendation_bucket')=='rejected'):
            exit_reason='signal_exit'
        if exit_reason:
            px=normalize_trade_price(sym, px)
            qty=float(pos.get('qty') or 0); fx=prices.get('_fx') or {}; exit_krw=price_to_krw(sym, px, fx); entry_krw=float(pos.get('entry_price') or px)*float(pos.get('entry_fx') or fx_rate_for_symbol(sym, fx)); pnl=(exit_krw-entry_krw)*qty
            fund['cash']=float(fund.get('cash') or 0)+qty*exit_krw; fund['realized_pnl']=float(fund.get('realized_pnl') or 0)+pnl; fund['trade_count']=int(fund.get('trade_count') or 0)+1
            del fund['positions'][sym]
            trades.append({'fund_id':fund['id'],'symbol':sym,'side':'sell','price':round(px,2),'qty':round(qty,6),'pnl':round(pnl,2),'reason':exit_reason})
    # Rebalance existing positions before opening new slots. Adds are not count-limited;
    # cash buffer, risk slice, and max single-symbol exposure bound the seed.
    rec_by_symbol={r.get('symbol'):r for r in recs if r.get('symbol')}
    for sym,pos in list((fund.get('positions') or {}).items()):
        rec=rec_by_symbol.get(sym); px=(prices.get(sym) or {}).get('close')
        if not rec or not px or rec.get('recommendation_bucket')=='rejected' or rec.get('action')=='avoid': continue
        rs=rec_score_for_fund(rec,fund)
        entry=float(pos.get('entry_price') or px)
        if fund.get('average_down_enabled') and px <= entry*(1-float(fund.get('average_down_trigger_pct') or 3.0)/100) and rs >= pct(fund.get('score_min'),50):
            rec['_fx']=prices.get('_fx') or {}; tr=add_to_position(fund,sym,pos,px,rec,rebalance_budget(fund,pos,px,prices,'average_down'),run_at,'average_down',rs)
            if tr: trades.append(tr)
        if fund.get('pyramid_enabled') and px >= entry*(1+float(fund.get('pyramid_trigger_pct') or 2.0)/100) and rs >= pct(fund.get('score_min'),50)+3:
            rec['_fx']=prices.get('_fx') or {}; tr=add_to_position(fund,sym,pos,px,rec,rebalance_budget(fund,pos,px,prices,'pyramid_winner'),run_at,'pyramid_winner',rs)
            if tr: trades.append(tr)
    equity,pos_val=value_fund(fund,prices)
    max_pos=int(fund.get('max_positions') or 5)
    slots=max(0,max_pos-len(fund.get('positions') or {}))
    if slots:
        ranked=sorted(recs,key=lambda r: rec_score_for_fund(r,fund), reverse=True)
        for rec in ranked:
            if slots<=0: break
            sym=rec.get('symbol'); px=(prices.get(sym) or {}).get('close')
            if not sym or not px or sym in fund.get('positions',{}): continue
            rs=rec_score_for_fund(rec,fund)
            if rs < pct(fund.get('score_min'),50): continue
            if not fund.get('watch_allowed') and rec.get('recommendation_bucket')!='watch': continue
            available=max(0.0, equity*(1-float(fund.get('cash_buffer') or 0))-pos_val)
            budget=min(float(fund.get('cash') or 0), equity*float(fund.get('risk_per_trade') or 0.1), available)
            if budget < equity*0.015: continue
            px=normalize_trade_price(sym, px); fx=prices.get('_fx') or {}; px_krw=price_to_krw(sym, px, fx); qty=budget/px_krw; target=rec.get('target_1') or px*(1+pct(rec.get('upside_1_pct'))/100); stop=rec.get('stop_reference') or px*(1+pct(rec.get('downside_stop_pct'))/100)
            fund['cash']=float(fund.get('cash') or 0)-budget
            fund.setdefault('positions',{})[sym]={'symbol':sym,'market':rec.get('market'),'entry_run_at':run_at,'entry_price':px,'entry_fx':fx_rate_for_symbol(sym, fx),'qty':qty,'target':target,'stop':stop,'strategy_id':rec.get('strategy_id'),'router':((rec.get('validation_basis') or {}).get('strategy_context_router') or {}),'add_count':0,'average_down_count':0,'pyramid_count':0}
            fund['trade_count']=int(fund.get('trade_count') or 0)+1; slots-=1; pos_val+=budget
            trades.append({'fund_id':fund['id'],'symbol':sym,'side':'buy','price':round(px,2),'qty':round(qty,6),'budget':round(budget,2),'reason':'fund_policy_entry','score':round(rs,2)})
    equity,pos_val=value_fund(fund,prices)
    peak=max([h.get('equity',fund.get('initial_capital')) for h in fund.get('history',[])] + [fund.get('initial_capital') or equity, equity])
    mdd=(equity/peak-1)*100 if peak else 0
    snap={'run_at':run_at,'equity':round(equity,2),'cash':round(float(fund.get('cash') or 0),2),'position_value':round(pos_val,2),'return_pct':round((equity/float(fund.get('initial_capital') or equity)-1)*100,2),'mdd_pct':round(mdd,2),'position_count':len(fund.get('positions') or {}),'trade_count':fund.get('trade_count')}
    fund.setdefault('history',[]).append(snap); fund['history']=fund['history'][-200:]
    return trades

def evolve(state, retire_pct, max_funds, min_age_runs=20):
    funds=state.get('funds') or []
    active=[f for f in funds if f.get('status')=='active']
    eligible=[f for f in active if len(f.get('history') or [])>min_age_runs]
    if len(eligible)<5: return {'retired':[], 'debuted':[], 'eligible_count':len(eligible)}
    ranked=sorted(eligible,key=lambda f:(f.get('history') or [{}])[-1].get('return_pct',0), reverse=True)
    retire_n=max(1,int(len(eligible)*retire_pct))
    retired=[]; debuted=[]
    for f in ranked[-retire_n:]:
        f['status']='retired'; f['retired_at']=now(); f['retire_reason']='daily_underperformance_after_min_20_runs'; retired.append(f['id'])
    parents=ranked[:max(2,retire_n)]
    next_idx=len(funds)+1
    rng=random.Random(len(funds)+int(datetime.now().timestamp())//3600)
    for i in range(retire_n):
        p=dict(parents[i%len(parents)])
        child={k:v for k,v in p.items() if k not in ('positions','history')}
        child['id']=f'fund_{next_idx:03d}'; next_idx+=1; child['name']=p['name'].split(' child')[0]+f' child{child.get("generation",1)+1}'; child['generation']=int(p.get('generation') or 1)+1; child['status']='active'; child['created_at']=now(); child['cash']=child['initial_capital']; child['positions']={}; child['history']=[]; child['realized_pnl']=0; child['trade_count']=0; child['parent_id']=p['id']
        for k,span in [('cash_buffer',0.06),('risk_per_trade',0.025),('score_min',3),('target_bias',0.08),('stop_bias',0.06)]: child[k]=round(max(0.01,float(child.get(k) or 0)+rng.uniform(-span,span)),3)
        funds.append(child); debuted.append(child['id'])
    return {'retired':retired, 'debuted':debuted, 'eligible_count':len(eligible)}

def main():
    ap=argparse.ArgumentParser(description='Run lightweight paper fund league agents over latest recommendations')
    ap.add_argument('--fund-count',type=int,default=30); ap.add_argument('--initial-capital',type=float,default=DEFAULT_CAPITAL); ap.add_argument('--evolve-every-runs',type=int,default=5); ap.add_argument('--retire-pct',type=float,default=0.15); ap.add_argument('--min-age-runs',type=int,default=20); ap.add_argument('--output',default=str(LATEST))
    args=ap.parse_args(); init_db(); conn=sqlite3.connect(get_settings().database_path,timeout=30); conn.row_factory=sqlite3.Row; prices=latest_price_map(conn); prices['_fx']=latest_usdkrw(conn); conn.close()
    rec_packet=load_json('/tmp/recommendations_latest.json',{}); recs=rec_packet.get('items') or []; run_at=now()
    state=load_json(STATE,{})
    if not state.get('funds'):
        state={'created_at':run_at,'runs':0,'funds':seed_funds(args.fund_count,args.initial_capital),'events':[]}
    else:
        active_existing=[f for f in state.get('funds',[]) if f.get('status')=='active']
        if len(active_existing) < args.fund_count:
            new_funds=seed_funds(args.fund_count-len(active_existing),args.initial_capital)
            next_idx=len(state.get('funds') or [])+1
            for nf in new_funds:
                nf['id']=f'fund_{next_idx:03d}'; nf['name']=nf['name']+' expansion'; nf['generation']=1; nf['created_at']=run_at; nf['debut_reason']='fund_count_expansion'; next_idx+=1
                state.setdefault('funds',[]).append(nf)
            state.setdefault('events',[]).append({'run_at':run_at,'event':'fund_count_expansion_debut','debuted':[f['id'] for f in new_funds],'target_fund_count':args.fund_count})
    trades=[]
    for f in state['funds']:
        trades.extend(step_fund(f,recs,prices,run_at))
    state['runs']=int(state.get('runs') or 0)+1
    evolution={'retired':[], 'debuted':[], 'eligible_count':0}
    if state['runs'] % max(1,args.evolve_every_runs)==0:
        evolution=evolve(state,args.retire_pct,args.fund_count,args.min_age_runs)
        if evolution.get('retired') or evolution.get('debuted'):
            state.setdefault('events',[]).append({'run_at':run_at,'event':'daily_underperformance_retire_and_debut', **evolution})
    save_state(state)
    active=[f for f in state['funds'] if f.get('status')=='active']
    standings=[]
    for f in active:
        snap=(f.get('history') or [{}])[-1]
        standings.append({'id':f['id'],'name':f['name'],'style':f.get('style'),'generation':f.get('generation'),'equity':snap.get('equity'),'return_pct':snap.get('return_pct'),'mdd_pct':snap.get('mdd_pct'),'position_count':snap.get('position_count'),'trade_count':snap.get('trade_count'),'parent_id':f.get('parent_id')})
    standings=sorted(standings,key=lambda x:x.get('return_pct') or -999,reverse=True)
    warnings=[]
    if not recs: warnings.append('no recommendations available for fund simulation')
    packet={'run_at':run_at,'mode':'paper_fund_league','real_trading':False,'authority':'paper_only_simulated_fund_league','initial_capital':args.initial_capital,'run_count':state['runs'],'fund_count':len(active),'retired_this_run':evolution.get('retired',[]),'debuted_this_run':evolution.get('debuted',[]),'min_age_runs':args.min_age_runs,'evolve_every_runs':args.evolve_every_runs,'retirement_policy':'mark_to_market_each_run_retire_every_5_runs_after_min_20_runs_bottom_retire_pct_then_new_debut','summary':{'top_fund':standings[0] if standings else None,'bottom_fund':standings[-1] if standings else None,'avg_return_pct':round(sum(x.get('return_pct') or 0 for x in standings)/len(standings),2) if standings else None,'trade_count':len(trades),'active_positions':sum(x.get('position_count') or 0 for x in standings)},'standings':standings,'trades':trades[:200],'warnings':warnings,'next_actions':['Mark funds to market each run, but retire/debut only every 5 runs after the 20-run observation period; keep paper-only.']}
    attach_contract(packet,'paper_fund_simulator_agent',status='degraded' if warnings else 'ok',outputs={'fund_count':len(active),'trade_count':len(trades)},metrics=packet['summary'],warnings=warnings,next_actions=packet['next_actions'])
    write_json(Path(args.output), packet); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
