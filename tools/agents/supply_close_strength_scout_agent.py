#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, list_universe_members, get_connection, latest_investor_flow_for_symbol
from app.symbols import display_name
from tools.agents.lib.agent_contract import attach_contract


def now(): return datetime.now(timezone.utc).isoformat()
def pct(a,b): return round((a/b-1)*100,2) if b else None

def rows(conn, sym, limit=90):
    return conn.execute("""
        SELECT date, open, high, low, close, volume
        FROM price_bars
        WHERE symbol=? AND timeframe='1d'
        ORDER BY date DESC LIMIT ?
    """,(sym,limit)).fetchall()[::-1]

def investor_flow_status_for_symbol(sym: str, conn) -> tuple[str, dict]:
    try:
        flow_rows=latest_investor_flow_for_symbol(sym, conn=conn, lookback_days=5)
    except Exception:
        flow_rows=[]
    if not flow_rows:
        return 'not_available_in_local_db', {}
    investors=[]; best_rank=None; latest_date=None
    for r in flow_rows:
        inv=r['investor_type']
        if inv not in investors: investors.append(inv)
        if r['rank'] is not None and (best_rank is None or r['rank'] < best_rank): best_rank=r['rank']
        if latest_date is None or r['date'] > latest_date: latest_date=r['date']
    return 'db_persisted_provisional_seed', {'investors':investors,'best_rank':best_rank,'latest_date':latest_date,'row_count':len(flow_rows),'authority':'paper_monitoring_seed_only'}

def market_of(sym: str) -> str:
    return 'KR' if sym.endswith('.KS') or sym.endswith('.KQ') else 'US'

def read_json(path: str) -> dict:
    p=Path(path)
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception: return {}

def seed_symbols_from(path: str, key: str, limit: int=120) -> list[str]:
    data=read_json(path); rows=data.get(key) or data.get('items') or []
    out=[]
    for row in rows:
        sym=str(row.get('symbol') or '').upper().strip()
        if sym and sym not in out:
            out.append(sym)
        if len(out)>=limit: break
    return out

def score_symbol(conn, sym: str) -> dict | None:
    r=rows(conn,sym,90)
    flow_status, flow_context = investor_flow_status_for_symbol(sym, conn)
    if len(r)<61: return None
    opens=[float(x['open'] if x['open'] is not None else x['close']) for x in r]
    highs=[float(x['high'] if x['high'] is not None else x['close']) for x in r]
    lows=[float(x['low'] if x['low'] is not None else x['close']) for x in r]
    closes=[float(x['close']) for x in r]
    vols=[float(x['volume'] or 0) for x in r]
    last=r[-1]
    vol20=sum(vols[-21:-1])/20 if len(vols)>=21 else 0
    vol60=sum(vols[-60:])/60 if len(vols)>=60 else 0
    vol_ratio=vols[-1]/vol20 if vol20 else None
    vol60_ratio=vols[-1]/vol60 if vol60 else None
    day_range=highs[-1]-lows[-1]
    close_pos=(closes[-1]-lows[-1])/day_range if day_range else 0.5
    body_pct=(closes[-1]/opens[-1]-1)*100 if opens[-1] else 0
    ma5=sum(closes[-5:])/5
    ma20=sum(closes[-20:])/20
    ma50=sum(closes[-50:])/50
    prev_high20=max(highs[-21:-1])
    return_1d=pct(closes[-1],closes[-2])
    return_5d=pct(closes[-1],closes[-6])
    return_20d=pct(closes[-1],closes[-21])
    score=0; reasons=[]; cautions=[]
    volume_confirmed = bool((vol_ratio is not None and vol_ratio>=1.2) or (vol60_ratio is not None and vol60_ratio>=1.1))
    if vol_ratio is not None and vol_ratio>=1.2:
        score+=24; reasons.append(f'거래량 20일평균 대비 {vol_ratio:.2f}x')
    if vol60_ratio is not None and vol60_ratio>=1.1:
        score+=8; reasons.append(f'거래량 60일평균 대비 {vol60_ratio:.2f}x')
    if close_pos>=0.8:
        score+=26; reasons.append(f'종가 상단 마감 {close_pos:.2f}')
    elif close_pos>=0.7:
        score+=18; reasons.append(f'종가 우위 {close_pos:.2f}')
    if body_pct>=0:
        score+=8; reasons.append(f'양봉/보합 body {body_pct:.2f}%')
    if closes[-1]>ma5>ma20 and closes[-1]>=ma50*0.98:
        score+=18; reasons.append('5/20일 추세 우위')
    elif closes[-1]>ma20 and ma20>=ma50*0.98:
        score+=12; reasons.append('20일선 위 마감')
    if closes[-1]>=prev_high20*0.995:
        score+=14; reasons.append('20일 고점권')
    if return_20d is not None and return_20d>25:
        score-=12; cautions.append(f'20일 {return_20d}% 상승 과열 가능')
    if close_pos<0.55:
        score-=20; cautions.append('종가가 당일 range 중상단 미달')
    if closes[-1]<ma50:
        score-=16; cautions.append('50일선 아래')
    if not volume_confirmed:
        score-=10; cautions.append('거래량 확장 미확인: 종가강도 단독 신호로 강등')
    bucket='strong_supply_close' if score>=70 and volume_confirmed else ('watch_supply_close' if score>=58 else 'ignore')
    return {
        'symbol':sym,'name':display_name(sym),'market':market_of(sym),'latest_date':last['date'],
        'score':round(score,2),'bucket':bucket,'reasons':reasons,'cautions':cautions,
        'features':{
            'volume_vs_20d':round(vol_ratio,2) if vol_ratio is not None else None,
            'volume_vs_60d':round(vol60_ratio,2) if vol60_ratio is not None else None,
            'close_position_in_day_range':round(close_pos,3),
            'body_pct':round(body_pct,2),
            'return_1d_pct':return_1d,'return_5d_pct':return_5d,'return_20d_pct':return_20d,
            'above_ma20':closes[-1]>ma20,'above_ma50':closes[-1]>ma50,
            'near_20d_high':closes[-1]>=prev_high20*0.995,
        },
        'investor_flow_status':flow_status,
        'investor_flow_context':flow_context,
        'investor_flow_next_step':'Upgrade provisional seed to validated daily net-buy amount/quantity ingestion before treating foreign/institution buying as full decision authority.',
        'policy':'paper_research_watch_boost_only',
    }

def main():
    ap=argparse.ArgumentParser(description='Detect friend-inspired supply + close-strength stock candidates')
    ap.add_argument('--symbols')
    ap.add_argument('--limit',type=int,default=30)
    ap.add_argument('--include-watch',action='store_true',default=True,help='Include watch universe members in default scan.')
    ap.add_argument('--seed-limit',type=int,default=120,help='Include mover/investor-flow seed symbols in default scan.')
    ap.add_argument('--output',default='/tmp/supply_close_strength_scout_latest.json')
    args=ap.parse_args(); init_db()
    conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    if args.symbols:
        symbols=[s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    else:
        symbols=[m['symbol'] for m in list_universe_members(status='active')]
        if args.include_watch:
            symbols += [m['symbol'] for m in list_universe_members(status='watch')]
        symbols += seed_symbols_from('/tmp/market_mover_seed_latest.json','top_stock_items',args.seed_limit)
        symbols += seed_symbols_from('/tmp/investor_flow_seed_latest.json','top_symbols',args.seed_limit)
        symbols=sorted(set(symbols))
        if not symbols:
            symbols=[r['symbol'] for r in conn.execute("SELECT DISTINCT symbol FROM price_bars WHERE timeframe='1d' ORDER BY symbol").fetchall()]
    items=[x for s in symbols if (x:=score_symbol(conn,s))]
    items.sort(key=lambda x:(x['score'], x['features'].get('volume_vs_20d') or 0), reverse=True)
    selected=[x for x in items if x['bucket']!='ignore'][:args.limit]
    packet={
        'run_at':now(),'mode':'supply_close_strength_scout','real_trading':False,
        'method':'volume expansion + close-location + trend proxy over active/watch/mover/investor-flow seeds; investor-type seed is monitoring priority only',
        'summary':{'scanned_symbol_count':len(symbols),'scored_symbol_count':len(items),'selected_count':len(selected),'strong_count':sum(1 for x in selected if x['bucket']=='strong_supply_close'),'watch_count':sum(1 for x in selected if x['bucket']=='watch_supply_close'),'scan_scope':'active+watch+mover_seed+investor_flow_seed'},
        'items':selected,
        'warnings':['investor_flow_db_seed_used_validation_gated'],
        'next_actions':['Backtest supply_close_strength_v120_c72_q66 and v135_c80_q70; add investor-flow ingestion before promoting to active decision authority.']
    }
    # Missing investor-flow data is an expected research limitation, not a pipeline
    # health degradation. Keep the contract ok and expose it as a warning/next action.
    attach_contract(packet,'supply_close_strength_scout_agent',status='ok',outputs={'selected_count':len(selected)},metrics=packet['summary'],warnings=packet['warnings'],next_actions=packet['next_actions'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
    conn.close()
if __name__=='__main__': main()
