#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,random,sqlite3,sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db
from tools.agents.lib.agent_contract import attach_contract
from tools.agents.lib.fx import load_usdkrw_series, fx_for_date, price_to_krw, fx_rate_for_symbol

DEFAULT_CAPITAL=10_000_000.0
ARCHETYPES=[
 {'style':'trend','cash_buffer':0.20,'max_positions':6,'risk_per_trade':0.13,'score_min':52},
 {'style':'breakout','cash_buffer':0.10,'max_positions':5,'risk_per_trade':0.16,'score_min':56},
 {'style':'balanced','cash_buffer':0.35,'max_positions':7,'risk_per_trade':0.09,'score_min':50},
 {'style':'defensive','cash_buffer':0.55,'max_positions':4,'risk_per_trade':0.07,'score_min':58},
 {'style':'supply','cash_buffer':0.25,'max_positions':6,'risk_per_trade':0.12,'score_min':50},
]

def iso(dt): return dt.replace(tzinfo=timezone.utc).isoformat()
def parse_ts(s):
    try: return datetime.fromisoformat(str(s).replace('Z','+00:00'))
    except Exception: return None

def safe_json(s):
    try: return json.loads(s or '{}')
    except Exception: return {}

def pct(v,d=0.0):
    try: return float(v if v is not None else d)
    except Exception: return d

def seed_funds(n, capital):
    funds=[]
    for i in range(n):
        base=dict(ARCHETYPES[i%len(ARCHETYPES)]); rng=random.Random(2000+i)
        for k,span in [('cash_buffer',0.08),('risk_per_trade',0.03),('score_min',4)]: base[k]=round(max(0.01,base[k]+rng.uniform(-span,span)),3)
        base.update({'average_down_enabled':True,'pyramid_enabled':True,'max_symbol_exposure_pct':0.24,'average_down_trigger_pct':3.0,'pyramid_trigger_pct':2.0,'id':f'replay_fund_{i+1:03d}','generation':1,'age_days':0,'status':'active','cash':capital,'initial_capital':capital,'positions':{},'history':[],'trade_count':0,'realized_pnl':0.0})
        funds.append(base)
    return funds

def rec_score(rec,fund):
    vb=rec.get('validation_basis') or {}; router=((vb.get('strategy_context_router') or {}).get('top_signal_decisions') or [{}])[0] or {}
    score=pct(rec.get('score'))
    fam=router.get('family'); style=fund.get('style')
    if style=='trend' and fam=='trend_strength': score+=8
    if style=='breakout' and fam=='breakout_volume': score+=8
    if style=='supply' and pct(vb.get('supply_close_score_adjustment_pct'))>0: score+=8
    if style=='defensive': score-=max(0,pct(rec.get('downside_stop_pct'))-6)*2
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

def prices_for_date(conn, date):
    rows=conn.execute("""SELECT p.symbol,p.close FROM price_bars p JOIN (SELECT symbol,max(date) date FROM price_bars WHERE timeframe='1d' AND date<=? GROUP BY symbol) m ON p.symbol=m.symbol AND p.date=m.date WHERE p.timeframe='1d'""",(date,)).fetchall()
    return {r['symbol']:float(r['close']) for r in rows}

def value(fund, prices):
    fx=prices.get('_fx') or {}
    pv=sum(float(pos.get('qty') or 0)*price_to_krw(sym, prices.get(sym,float(pos.get('entry_price') or 0)), fx) for sym,pos in fund.get('positions',{}).items())
    return float(fund.get('cash') or 0)+pv,pv

def load_recs_for_run(rows):
    recs=[]
    for r in rows:
        payload=safe_json(r['payload_json']); payload.setdefault('symbol',r['symbol']); payload.setdefault('market',r['market']); payload.setdefault('action',r['action']); payload.setdefault('score',r['score']); payload.setdefault('strategy_id',r['strategy_id']); payload.setdefault('target_1',r['target_1']); payload.setdefault('stop_reference',r['stop_reference'])
        recs.append(payload)
    return recs


def rebalance_budget(fund,pos,px,prices,direction):
    equity,pv=value(fund,prices)
    if equity <= 0 or px <= 0: return 0.0
    current=float(pos.get('qty') or 0)*price_to_krw(pos.get('symbol') or '', px, prices.get('_fx') or {})
    exposure_room=max(0.0,equity*float(fund.get('max_symbol_exposure_pct') or 0.24)-current)
    deployable=max(0.0,equity*(1-float(fund.get('cash_buffer') or 0))-pv)
    risk_slice=0.45 if direction=='pyramid_winner' else 0.40
    return min(float(fund.get('cash') or 0), exposure_room, equity*float(fund.get('risk_per_trade') or 0.1)*risk_slice, deployable)

def add_to_position(fund,sym,pos,px,rec,budget,run_at,date,reason,score):
    if budget <= 0 or px <= 0: return None
    fx=rec.get('_fx') or {}
    px_krw=price_to_krw(sym, px, fx)
    qty=budget/px_krw
    old_qty=float(pos.get('qty') or 0); new_qty=old_qty+qty
    if new_qty <= 0: return None
    pos['entry_price']=((float(pos.get('entry_price') or px)*old_qty)+(px*qty))/new_qty
    pos['entry_fx']=((float(pos.get('entry_fx') or fx_rate_for_symbol(sym, fx))*old_qty)+(fx_rate_for_symbol(sym, fx)*qty))/new_qty
    pos['qty']=new_qty; pos['target']=rec.get('target_1'); pos['stop']=rec.get('stop_reference'); pos['last_add_date']=date; pos['last_add_run_at']=run_at; pos['add_count']=int(pos.get('add_count') or 0)+1
    if reason=='average_down': pos['average_down_count']=int(pos.get('average_down_count') or 0)+1
    if reason=='pyramid_winner': pos['pyramid_count']=int(pos.get('pyramid_count') or 0)+1
    fund['cash']-=budget; fund['trade_count']+=1
    return {'fund_id':fund['id'],'date':date,'symbol':sym,'side':'buy','price':round(px,2),'budget':round(budget,2),'score':round(score,2),'reason':reason}

def step_fund(fund,recs,prices,run_at,date):
    if fund.get('status')!='active': return []
    trades=[]; fund['age_days']=int(fund.get('age_days') or 0)+1
    for sym,pos in list(fund.get('positions',{}).items()):
        px=prices.get(sym); rec=next((r for r in recs if r.get('symbol')==sym),None)
        if not px: continue
        reason=None
        if pos.get('target') and px>=pos['target']: reason='target_hit'
        elif pos.get('stop') and px<=pos['stop']: reason='stop_hit'
        elif not rec or rec.get('action')=='avoid' or rec.get('recommendation_bucket')=='rejected': reason='signal_exit'
        if reason:
            qty=float(pos.get('qty') or 0); fx=prices.get('_fx') or {}; exit_krw=price_to_krw(sym, px, fx); entry_krw=float(pos.get('entry_price') or px)*float(pos.get('entry_fx') or fx_rate_for_symbol(sym, fx)); pnl=(exit_krw-entry_krw)*qty
            fund['cash']+=qty*exit_krw; fund['realized_pnl']+=pnl; fund['trade_count']+=1; del fund['positions'][sym]
            trades.append({'fund_id':fund['id'],'date':date,'symbol':sym,'side':'sell','price':round(px,2),'pnl':round(pnl,2),'reason':reason})
    rec_by_symbol={r.get('symbol'):r for r in recs if r.get('symbol')}
    for sym,pos in list(fund.get('positions',{}).items()):
        px=prices.get(sym); rec=rec_by_symbol.get(sym)
        if not px or not rec or rec.get('action')=='avoid' or rec.get('recommendation_bucket')=='rejected': continue
        score=rec_score(rec,fund); entry=float(pos.get('entry_price') or px)
        if fund.get('average_down_enabled') and px <= entry*(1-float(fund.get('average_down_trigger_pct') or 3.0)/100) and score >= pct(fund.get('score_min'),50):
            rec['_fx']=prices.get('_fx') or {}; tr=add_to_position(fund,sym,pos,px,rec,rebalance_budget(fund,pos,px,prices,'average_down'),run_at,date,'average_down',score)
            if tr: trades.append(tr)
        if fund.get('pyramid_enabled') and px >= entry*(1+float(fund.get('pyramid_trigger_pct') or 2.0)/100) and score >= pct(fund.get('score_min'),50)+3:
            rec['_fx']=prices.get('_fx') or {}; tr=add_to_position(fund,sym,pos,px,rec,rebalance_budget(fund,pos,px,prices,'pyramid_winner'),run_at,date,'pyramid_winner',score)
            if tr: trades.append(tr)
    equity,pv=value(fund,prices); slots=max(0,int(fund.get('max_positions') or 5)-len(fund.get('positions') or {}))
    for rec in sorted(recs,key=lambda r:rec_score(r,fund),reverse=True):
        if slots<=0: break
        sym=rec.get('symbol'); px=prices.get(sym)
        if not sym or not px or sym in fund.get('positions',{}): continue
        if rec_score(rec,fund)<pct(fund.get('score_min'),50): continue
        if rec.get('action')=='avoid': continue
        equity,pv=value(fund,prices); budget=min(fund['cash'], equity*fund['risk_per_trade'], max(0,equity*(1-fund['cash_buffer'])-pv))
        if budget<equity*0.015: continue
        fx=prices.get('_fx') or {}; px_krw=price_to_krw(sym, px, fx); qty=budget/px_krw; fund['cash']-=budget; fund['trade_count']+=1; slots-=1
        fund['positions'][sym]={'symbol':sym,'entry_run_at':run_at,'entry_date':date,'entry_price':px,'entry_fx':fx_rate_for_symbol(sym,fx),'qty':qty,'target':rec.get('target_1'),'stop':rec.get('stop_reference'),'strategy_id':rec.get('strategy_id'),'add_count':0,'average_down_count':0,'pyramid_count':0}
        trades.append({'fund_id':fund['id'],'date':date,'symbol':sym,'side':'buy','price':round(px,2),'price_krw':round(px_krw,2),'fx_rate':round(fx_rate_for_symbol(sym,fx),4),'budget':round(budget,2),'score':round(rec_score(rec,fund),2),'reason':'fund_policy_entry'})
    equity,pv=value(fund,prices); peak=max([h.get('equity',fund['initial_capital']) for h in fund.get('history',[])]+[fund['initial_capital'],equity])
    fund['history'].append({'run_at':run_at,'date':date,'equity':round(equity,2),'return_pct':round((equity/fund['initial_capital']-1)*100,2),'mdd_pct':round((equity/peak-1)*100,2),'position_count':len(fund.get('positions') or {}),'age_days':fund['age_days']})
    return trades

def evolve(funds,min_age,retire_pct,capital):
    active=[f for f in funds if f.get('status')=='active']; eligible=[f for f in active if int(f.get('age_days') or 0)>min_age and f.get('history')]
    if len(eligible)<5: return {'retired':[], 'debuted':[], 'eligible_count':len(eligible)}
    ranked=sorted(eligible,key=lambda f:(f.get('history') or [{}])[-1].get('return_pct',0),reverse=True); n=max(1,int(len(eligible)*retire_pct)); retired=[]; debuted=[]; parents=ranked[:max(2,n)]
    next_id=len(funds)+1; rng=random.Random(next_id+len(ranked))
    for f in ranked[-n:]:
        f['status']='retired'; f['retired_at']=datetime.now(timezone.utc).isoformat(); f['retire_reason']='daily_underperformance_after_min_20_trading_days'; retired.append(f['id'])
    for i in range(n):
        p=dict(parents[i%len(parents)]); child={k:v for k,v in p.items() if k not in ('positions','history')}
        child.update({'id':f'replay_fund_{next_id:03d}','generation':int(p.get('generation') or 1)+1,'age_days':0,'status':'active','cash':capital,'initial_capital':capital,'positions':{},'history':[],'trade_count':0,'realized_pnl':0.0,'parent_id':p['id']}); next_id+=1
        for k,span in [('cash_buffer',0.06),('risk_per_trade',0.025),('score_min',3)]: child[k]=round(max(0.01,float(child.get(k) or 0)+rng.uniform(-span,span)),3)
        funds.append(child); debuted.append(child['id'])
    return {'retired':retired, 'debuted':debuted, 'eligible_count':len(eligible)}

def main():
    ap=argparse.ArgumentParser(description='Historical daily replay for paper fund league using only recommendation_history payload at each past run')
    ap.add_argument('--days',type=int,default=365); ap.add_argument('--fund-count',type=int,default=30); ap.add_argument('--initial-capital',type=float,default=DEFAULT_CAPITAL); ap.add_argument('--retire-pct',type=float,default=0.15); ap.add_argument('--min-age-days',type=int,default=20); ap.add_argument('--evolve-every-days',type=int,default=5); ap.add_argument('--output',default='/tmp/paper_fund_historical_replay_latest.json')
    args=ap.parse_args(); init_db(); conn=sqlite3.connect(get_settings().database_path,timeout=30); conn.row_factory=sqlite3.Row
    cutoff=iso(datetime.now(timezone.utc)-timedelta(days=args.days))
    run_rows=conn.execute('''SELECT max(run_at) AS run_at FROM recommendation_history WHERE run_at>=? GROUP BY substr(run_at,1,10) ORDER BY run_at''',(cutoff,)).fetchall()
    fx_series=load_usdkrw_series(conn, cutoff)
    funds=seed_funds(args.fund_count,args.initial_capital); trades=[]; evolution=[]; day=0; price_cache={}
    for rr in run_rows:
        run_at=rr['run_at']; dt=parse_ts(run_at); date=(dt.date().isoformat() if dt else run_at[:10]); prices=price_cache.get(date)
        if prices is None:
            prices=prices_for_date(conn,date); prices['_fx']=fx_for_date(fx_series, date); price_cache[date]=prices
        rows=conn.execute('SELECT * FROM recommendation_history WHERE run_at=?',(run_at,)).fetchall(); recs=load_recs_for_run(rows)
        for f in funds: trades.extend(step_fund(f,recs,prices,run_at,date))
        day+=1
        if day%max(1,args.evolve_every_days)==0:
            ev=evolve(funds,args.min_age_days,args.retire_pct,args.initial_capital)
            if ev.get('retired') or ev.get('debuted'):
                evolution.append({'run_at':run_at,'date':date, **ev})
    conn.close(); active=[f for f in funds if f.get('status')=='active']
    standings=[]
    for f in active:
        h=(f.get('history') or [{}])[-1]
        standings.append({'id':f['id'],'style':f.get('style'),'generation':f.get('generation'),'age_days':f.get('age_days'),'return_pct':h.get('return_pct'),'mdd_pct':h.get('mdd_pct'),'position_count':h.get('position_count'),'trade_count':f.get('trade_count'),'parent_id':f.get('parent_id')})
    standings=sorted(standings,key=lambda x:x.get('return_pct') if x.get('return_pct') is not None else -999,reverse=True)
    warnings=[]
    if not run_rows: warnings.append('no recommendation_history rows in replay window')
    if run_rows and (parse_ts(run_rows[0]['run_at']) or datetime.now(timezone.utc)) > datetime.now(timezone.utc)-timedelta(days=args.days-7): warnings.append('available recommendation_history is shorter than requested replay window')
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'paper_fund_historical_daily_replay','real_trading':False,'authority':'paper_only_historical_replay_no_orders','requested_days':args.days,'actual_run_count':len(run_rows),'fund_count':len(active),'retire_pct':args.retire_pct,'min_age_days':args.min_age_days,'evolve_every_days':args.evolve_every_days,'retirement_policy':'mark_to_market_daily_retire_every_5_trading_days_after_min_20_days_bottom_retire_pct_then_new_debut','position_management':'cash/exposure-bounded repeated averaging-down and pyramiding inside each fund seed; entry_date is not reset by adds','currency_model':{'base_currency':'KRW','us_assets_marked_to_krw':True,'fx_symbol':'USD/KRW','fx_source':(fx_series.get('latest') or {}).get('source'),'latest_usdkrw':(fx_series.get('latest') or {}).get('rate'),'latest_fx_date':(fx_series.get('latest') or {}).get('date')},'summary':{'top_fund':standings[0] if standings else None,'bottom_fund':standings[-1] if standings else None,'avg_return_pct':round(sum((x.get('return_pct') or 0) for x in standings)/len(standings),2) if standings else None,'trade_count':len(trades),'evolution_events':len(evolution),'retired_count':sum(len(x.get('retired') or []) for x in evolution),'debut_count':sum(len(x.get('debuted') or []) for x in evolution)},'standings':standings,'evolution_events':evolution[-20:],'trades':trades[-300:],'warnings':warnings,'next_actions':['Backfill or regenerate historical recommendation snapshots for a true 1-year replay.' if warnings else 'Use replay standings to seed live paper fund league.']}
    attach_contract(packet,'paper_fund_historical_replay_agent',status='degraded' if warnings else 'ok',outputs={'fund_count':len(active),'actual_run_count':len(run_rows)},metrics=packet['summary'],warnings=warnings,next_actions=packet['next_actions'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
