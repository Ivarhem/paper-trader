#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db
from tools.agents.recommendation_auditor import signal
from tools.agents.lib.agent_contract import attach_contract

def pct(a,b):
    return round((float(a)/float(b)-1)*100,2) if b else None

def load_current_scope():
    p=Path('/tmp/recommendations_latest.json')
    if not p.exists(): return [], []
    d=json.loads(p.read_text(encoding='utf-8'))
    return sorted({x.get('symbol') for x in d.get('items',[]) if x.get('symbol')}), sorted({x.get('logic') or x.get('best_logic') for x in d.get('items',[]) if (x.get('logic') or x.get('best_logic'))})

def cutoffs_from_latest():
    p=Path('/tmp/recommendation_audit_latest.json')
    if p.exists():
        try: return json.loads(p.read_text(encoding='utf-8')).get('cutoffs') or []
        except Exception: pass
    return []

def main():
    ap=argparse.ArgumentParser(description='Profile 1-2D short-horizon paper profit potential for current recommendation logics')
    ap.add_argument('--symbols', default='')
    ap.add_argument('--logics', default='')
    ap.add_argument('--horizon-days', type=int, default=2)
    ap.add_argument('--horizon-days-list', default='2,5', help='Comma-separated horizons to profile; first horizon remains by_logic compatibility summary')
    ap.add_argument('--output', default='/tmp/short_horizon_profit_profile_latest.json')
    args=ap.parse_args()
    init_db(); conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    cur_symbols, cur_logics=load_current_scope()
    symbols=[x.strip() for x in args.symbols.split(',') if x.strip()] or cur_symbols
    logics=[x.strip() for x in args.logics.split(',') if x.strip()] or cur_logics
    cutoffs=cutoffs_from_latest()
    horizons=[]
    for raw in str(args.horizon_days_list or args.horizon_days).split(','):
        raw=raw.strip()
        if raw:
            try: horizons.append(max(1,int(raw)))
            except ValueError: pass
    if not horizons:
        horizons=[args.horizon_days]
    if args.horizon_days not in horizons:
        horizons.insert(0,args.horizon_days)

    def build_for_horizon(horizon_days:int):
        items=[]
        for sym in symbols:
            rows=conn.execute("SELECT date,open,high,low,close,volume FROM price_bars WHERE symbol=? AND timeframe='1d' ORDER BY date",(sym,)).fetchall()
            if len(rows)<140: continue
            dates=[r['date'] for r in rows]
            for cutoff in cutoffs:
                idx=next((i for i,d in enumerate(dates) if d>=cutoff), None)
                if idx is None or idx<120 or idx+1>=len(rows): continue
                hist=rows[:idx+1]; fut=rows[idx+1:idx+1+horizon_days]
                if not fut: continue
                entry=float(hist[-1]['close'])
                for logic in logics:
                    sig=signal(hist, logic)
                    if not sig or sig.get('action')!='candidate_buy_zone': continue
                    max_high=max(float(r['high'] if r['high'] is not None else r['close']) for r in fut)
                    final=float(fut[-1]['close'])
                    target_ret=pct(sig.get('target'), entry)
                    max_up=pct(max_high, entry)
                    final_ret=pct(final, entry)
                    target_gap=round((target_ret or 0)-(max_up or 0),2)
                    items.append({
                        'symbol':sym,'logic':logic,'cutoff':cutoff,'entry':entry,
                        'target_ret_pct':target_ret,'max_up_pct':max_up,'final_return_pct':final_ret,
                        'target_gap_pct':target_gap,
                        'hit_target':(max_up is not None and target_ret is not None and max_up>=target_ret),
                        'hit_target_minus_1_pct_point': target_gap <= 1.0,
                        'hit_target_minus_1_5_pct_points': target_gap <= 1.5,
                        'hit_target_minus_2_pct_points': target_gap <= 2.0,
                    })
        by_logic={}
        for logic in logics:
            xs=[x for x in items if x['logic']==logic]
            if not xs: continue
            def rate(pred): return round(sum(1 for x in xs if pred(x))/len(xs)*100,2)
            def avg(key): return round(sum(float(x.get(key) or 0) for x in xs)/len(xs),2)
            hit15=rate(lambda x:(x.get('max_up_pct') or -999)>=1.5)
            minus2=rate(lambda x:x.get('hit_target_minus_2_pct_points'))
            by_logic[logic]={
                'horizon_days':horizon_days,'samples':len(xs),'symbol_count':len({x['symbol'] for x in xs}),
                'hit_1_pct':rate(lambda x:(x.get('max_up_pct') or -999)>=1.0),'hit_1_5_pct':hit15,'hit_2_pct':rate(lambda x:(x.get('max_up_pct') or -999)>=2.0),'hit_3_pct':rate(lambda x:(x.get('max_up_pct') or -999)>=3.0),
                'target_hit_pct':rate(lambda x:x.get('hit_target')),
                'target_minus_1_pct_point_hit_pct':rate(lambda x:x.get('hit_target_minus_1_pct_point')),
                'target_minus_1_5_pct_points_hit_pct':rate(lambda x:x.get('hit_target_minus_1_5_pct_points')),
                'target_minus_2_pct_points_hit_pct':minus2,
                'target_under_1_pct_hit_pct':rate(lambda x:x.get('hit_target_minus_1_pct_point')),
                'target_under_1_5_pct_hit_pct':rate(lambda x:x.get('hit_target_minus_1_5_pct_points')),
                'target_under_2_pct_hit_pct':minus2,
                'target_or_under_2pct_pct':minus2,
                'avg_max_up_pct':avg('max_up_pct'),'avg_final_return_pct':avg('final_return_pct'),'avg_target_ret_pct':avg('target_ret_pct'),
                'adjusted_target_profile':'strong_adjusted_target_touch' if minus2>=50 else ('watch_adjusted_target_touch' if minus2>=25 else 'weak_adjusted_target_touch'),
                'scalp_profile':'strong_1_2d_pop' if hit15>=60 else ('watch_1_2d_pop' if hit15>=50 else 'unproven'),
                'summary':f"{horizon_days}거래일 내 목표수익률-2%p 근접 {minus2}% / 원목표 도달 {rate(lambda x:x.get('hit_target'))}%"
            }
        return items, by_logic

    by_horizon={}
    primary_items=[]; primary_by_logic={}
    for h in horizons:
        h_items,h_by=build_for_horizon(h)
        by_horizon[str(h)]={'horizon_days':h,'item_count':len(h_items),'by_logic':h_by}
        if h == args.horizon_days or not primary_by_logic:
            primary_items, primary_by_logic = h_items, h_by
    items=primary_items
    by_logic=primary_by_logic
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'short_horizon_profit_profile','real_trading':False,'policy':'paper_research_context_only_no_orders','horizon_days':args.horizon_days,'horizons':horizons,'symbols':symbols,'logics':logics,'item_count':len(items),'by_logic':by_logic,'by_horizon':by_horizon,'warnings':[],'next_actions':[]}
    if not items: packet['warnings'].append('no_short_horizon_samples')
    attach_contract(packet,'short_horizon_profit_profile_agent',status='ok' if items else 'degraded',inputs={'symbols':len(symbols),'logics':len(logics),'horizon_days':args.horizon_days,'horizons':horizons},outputs={'item_count':len(items),'logic_count':len(by_logic)},metrics={'item_count':len(items)},warnings=packet['warnings'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
