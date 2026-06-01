#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json, random, re, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, list_universe_members, save_latest_artifact
from app.symbols import display_name
from tools.agents.lib.agent_contract import attach_contract, write_json_shared

def artifact_key_from_path(path: str | Path) -> str:
    return Path(path).name.removesuffix('.json')


def write_latest_artifact(path: str | Path, payload: dict) -> None:
    save_latest_artifact(
        artifact_key_from_path(path),
        payload,
        artifact_path=str(path),
        status=payload.get('status') if isinstance(payload, dict) else None,
        summary=str(payload.get('summary') or '')[:1000] if isinstance(payload, dict) else None,
    )
    write_json_shared(path, payload)


BASE_LOGICS={
 'conservative_range_v1': {'target_min':0.03,'target_cap':0.08,'target_mult':0.45,'stop_min':0.03,'stop_cap':0.07,'stop_mult':0.35,'score_min':45,'family':'range_baseline'},
 'balanced_range_v1': {'target_min':0.04,'target_cap':0.12,'target_mult':0.60,'stop_min':0.04,'stop_cap':0.10,'stop_mult':0.45,'score_min':55,'family':'range_baseline'},
 'aggressive_range_v1': {'target_min':0.06,'target_cap':0.20,'target_mult':1.00,'stop_min':0.05,'stop_cap':0.12,'stop_mult':0.55,'score_min':65,'family':'range_baseline'},
 'us_momentum_breakout_v1': {'target_min':0.07,'target_cap':0.18,'target_mult':0.80,'stop_min':0.05,'stop_cap':0.10,'stop_mult':0.45,'score_min':70,'family':'us_momentum'},
 'us_relative_strength_v1': {'target_min':0.06,'target_cap':0.16,'target_mult':0.70,'stop_min':0.045,'stop_cap':0.09,'stop_mult':0.40,'score_min':65,'family':'us_momentum'},
 'us_strong_rs_pullback_t8_s4_h15': {'target_min':0.04,'target_cap':0.08,'target_mult':0.55,'stop_min':0.025,'stop_cap':0.04,'stop_mult':0.32,'score_min':58,'family':'us_relative_strength_pullback','data_only':True,'data_inputs':['price_bars.close','price_bars.volume'],'min_excess_20d_pct':0,'min_excess_60d_pct':2,'pullback_min_5d':-7,'pullback_max_5d':0,'avoid_gap_1d_pct':2.5,'require_above_ma50':True,'horizon_days':15},
 'us_high_upside_trend_v1': {'target_min':0.10,'target_cap':0.28,'target_mult':1.15,'stop_min':0.07,'stop_cap':0.14,'stop_mult':0.60,'score_min':75,'family':'us_momentum'},
 # Data-only families: deterministic technical/price-volume rules. No narrative/news/committee input.
 'technical_ma_trend_v1': {'target_min':0.04,'target_cap':0.10,'target_mult':0.55,'stop_min':0.035,'stop_cap':0.08,'stop_mult':0.40,'score_min':62,'family':'technical_data_only','data_only':True,'data_inputs':['price_bars.close','price_bars.volume']},
 'technical_volume_breakout_v1': {'target_min':0.05,'target_cap':0.13,'target_mult':0.70,'stop_min':0.04,'stop_cap':0.09,'stop_mult':0.45,'score_min':66,'family':'technical_data_only','data_only':True,'data_inputs':['price_bars.close','price_bars.volume']},
 'technical_rsi_reversion_v1': {'target_min':0.035,'target_cap':0.09,'target_mult':0.45,'stop_min':0.03,'stop_cap':0.075,'stop_mult':0.35,'score_min':58,'family':'technical_data_only','data_only':True,'data_inputs':['price_bars.close','price_bars.volume']},
}



def generate_logics() -> dict:
    logics=dict(BASE_LOGICS)
    # Keep range-grid exploration compact. The old full Cartesian grid produced
    # 100+ near-duplicate candidates, swamping novelty/audit outputs without
    # producing promotion-ready evidence. Use a small set of structurally distinct
    # archetypes instead; broader sweeps belong in bounded research runs, not the
    # recurring 15-minute paper pipeline.
    range_grid_specs=[
      (0.06,0.05,0.40,55),  # conservative near-term range
      (0.08,0.05,0.60,55),  # balanced tight stop
      (0.08,0.07,0.40,65),  # higher confidence, wider stop
      (0.10,0.05,0.85,65),  # stronger upside, tight stop
      (0.10,0.07,0.60,55),  # balanced mid range
      (0.12,0.07,0.85,65),  # high upside
      (0.12,0.09,0.60,65),  # high confidence, wider stop
      (0.16,0.09,0.85,65),  # aggressive but still bounded
    ]
    for tc,sc,tm,sm in range_grid_specs:
        name=f'range_grid_t{int(tc*100):02d}_s{int(sc*100):02d}_m{int(tm*100):02d}_q{sm}'
        logics[name]={
          'target_min':max(0.025, tc*0.45),'target_cap':tc,'target_mult':tm,
          'stop_min':max(0.025, sc*0.55),'stop_cap':sc,'stop_mult':max(0.30, tm*0.65),
          'score_min':sm,'family':'range_grid_v1'
        }
    for fast, slow, sm in [(10,40,60),(20,60,62),(20,120,64)]:
        name=f'technical_ma_trend_f{fast}_s{slow}_q{sm}'
        logics[name]={'target_min':0.04,'target_cap':0.11,'target_mult':0.55,'stop_min':0.035,'stop_cap':0.08,'stop_mult':0.40,'score_min':sm,'family':'technical_data_only','data_only':True,'data_inputs':['price_bars.close','price_bars.volume'],'fast_ma':fast,'slow_ma':slow}
    for lookback, vol_mult, sm in [(20,1.2,64),(40,1.15,66),(60,1.1,68)]:
        name=f'technical_volume_breakout_l{lookback}_v{int(vol_mult*100)}_q{sm}'
        logics[name]={'target_min':0.05,'target_cap':0.13,'target_mult':0.70,'stop_min':0.04,'stop_cap':0.09,'stop_mult':0.45,'score_min':sm,'family':'technical_data_only','data_only':True,'data_inputs':['price_bars.close','price_bars.volume'],'breakout_lookback':lookback,'volume_mult':vol_mult}
    for rsi_max, sm in [(35,58),(40,60)]:
        name=f'technical_rsi_reversion_r{rsi_max}_q{sm}'
        logics[name]={'target_min':0.035,'target_cap':0.09,'target_mult':0.45,'stop_min':0.03,'stop_cap':0.075,'stop_mult':0.35,'score_min':sm,'family':'technical_data_only','data_only':True,'data_inputs':['price_bars.close','price_bars.volume'],'rsi_max':rsi_max}
    # Discovery families: fewer, structurally different candidates than range_grid.
    # These stay paper-only and must earn validation before becoming active.
    for lookback, vol_ratio, q in [(40,0.75,62),(60,0.70,64)]:
        name=f'volatility_contraction_breakout_l{lookback}_vr{int(vol_ratio*100)}_q{q}'
        logics[name]={'target_min':0.055,'target_cap':0.16,'target_mult':0.85,'stop_min':0.035,'stop_cap':0.085,'stop_mult':0.45,'score_min':q,'family':'volatility_contraction_breakout','data_only':True,'data_inputs':['price_bars.close','price_bars.volume'],'breakout_lookback':lookback,'contraction_ratio':vol_ratio}
    for pullback, q in [(0.04,62),(0.07,64)]:
        name=f'pullback_uptrend_pb{int(pullback*100)}_q{q}'
        logics[name]={'target_min':0.045,'target_cap':0.13,'target_mult':0.65,'stop_min':0.035,'stop_cap':0.08,'stop_mult':0.42,'score_min':q,'family':'pullback_in_uptrend','data_only':True,'data_inputs':['price_bars.close','price_bars.volume'],'pullback_min':pullback}
    for r60_min, q in [(8,60),(14,64)]:
        name=f'relative_strength_persistence_r{r60_min}_q{q}'
        logics[name]={'target_min':0.05,'target_cap':0.15,'target_mult':0.75,'stop_min':0.04,'stop_cap':0.09,'stop_mult':0.45,'score_min':q,'family':'relative_strength_persistence','data_only':True,'data_inputs':['price_bars.close','price_bars.volume'],'r60_min':r60_min}
    # Risk-adjusted discovery families: optimize for promotion gates directly.
    # These prefer lower left-tail risk, positive expected excess value, and recent stability
    # over raw average return. They remain paper-only until validation earns promotion.
    for pullback, q in [(0.03,70),(0.05,72)]:
        name=f'quality_pullback_uptrend_pb{int(pullback*100)}_q{q}'
        logics[name]={'target_min':0.035,'target_cap':0.095,'target_mult':0.48,'stop_min':0.025,'stop_cap':0.055,'stop_mult':0.30,'score_min':q,'family':'quality_pullback_uptrend','data_only':True,'data_inputs':['price_bars.close','price_bars.volume'],'pullback_min':pullback}
    for lookback, q in [(40,70),(60,72)]:
        name=f'quality_breakout_l{lookback}_q{q}'
        logics[name]={'target_min':0.04,'target_cap':0.105,'target_mult':0.55,'stop_min':0.028,'stop_cap':0.06,'stop_mult':0.32,'score_min':q,'family':'quality_breakout','data_only':True,'data_inputs':['price_bars.close','price_bars.volume'],'breakout_lookback':lookback}
    for r60_min, q in [(8,68),(12,70)]:
        name=f'stable_relative_strength_r{r60_min}_q{q}'
        logics[name]={'target_min':0.04,'target_cap':0.11,'target_mult':0.55,'stop_min':0.03,'stop_cap':0.065,'stop_mult':0.34,'score_min':q,'family':'stable_relative_strength','data_only':True,'data_inputs':['price_bars.close','price_bars.volume'],'r60_min':r60_min}
    # Friend-inspired supply/close-strength rule.  The current DB has price/volume
    # but not investor-type net-buy rows, so this is a deterministic proxy for:
    # - 전일 매수/매도 총량: volume/turnover expansion versus 20d average
    # - 외국인/기관 수급: TODO external investor-flow input; currently approximated
    #   only by accumulation-style close-location + price/volume pressure
    # - 종가 근처 차트 우위: close in the upper part of the daily range and above MAs
    for q, vol_mult, close_pos in [(66,1.20,0.72),(70,1.35,0.80)]:
        name=f'supply_close_strength_v{int(vol_mult*100)}_c{int(close_pos*100)}_q{q}'
        logics[name]={'target_min':0.04,'target_cap':0.12,'target_mult':0.60,'stop_min':0.03,'stop_cap':0.075,'stop_mult':0.36,'score_min':q,'family':'supply_close_strength','data_only':True,'data_inputs':['price_bars.open','price_bars.high','price_bars.low','price_bars.close','price_bars.volume'],'volume_mult':vol_mult,'close_pos_min':close_pos}
    return logics


LOGICS=generate_logics()


def logic_config(logic_name):
    cfg = LOGICS.get(logic_name)
    if cfg:
        return cfg
    # Backward-compatible support for range-grid strategies already in the
    # registry. The recurring generator intentionally narrowed its grid, but
    # older validated/repair-active grid IDs can remain in the database. Without
    # this parser the recommendation layer silently ignores them and the repair
    # overlay can collapse to zero recommendations.
    m = re.fullmatch(r'range_grid_t(\d{2})_s(\d{2})_m(\d{2})_q(\d+)', str(logic_name or ''))
    if not m:
        return None
    target_cap = int(m.group(1)) / 100.0
    stop_cap = int(m.group(2)) / 100.0
    target_mult = int(m.group(3)) / 100.0
    score_min = int(m.group(4))
    return {
        'target_min': max(0.025, target_cap * 0.45),
        'target_cap': target_cap,
        'target_mult': target_mult,
        'stop_min': max(0.025, stop_cap * 0.55),
        'stop_cap': stop_cap,
        'stop_mult': max(0.30, target_mult * 0.65),
        'score_min': score_min,
        'family': 'range_grid_v1_legacy',
        'legacy_generated': True,
    }

def pct(a,b): return round((a/b-1)*100,2) if b else None

CORPORATE_ACTION_JUMP_PCT = 80


def large_price_jump(prev_close, close, threshold=CORPORATE_ACTION_JUMP_PCT):
    if prev_close is None or close is None or float(prev_close) <= 0:
        return None
    prev = float(prev_close)
    now = float(close)
    change = pct(now, prev)
    # Upward resets: capital reduction / reverse split, e.g. 1000 -> 3000 (+200%).
    # Downward resets: stock split / par split, e.g. 50000 -> 10000 (-80%).
    # For downward moves, pct() is bounded at -100, so use ratio as well as abs(change).
    ratio = max(now / prev, prev / now) if now > 0 else float('inf')
    if change is not None and (abs(change) >= threshold or ratio >= 3):
        return {'from_close': prev, 'to_close': now, 'change_pct': change, 'ratio': round(ratio, 4), 'threshold_pct': threshold}
    return None


def corporate_action_jump(rows, entry_price=None):
    prev = entry_price
    prev_date = 'entry' if entry_price is not None else None
    for r in rows:
        close = float(r['close'])
        jump = large_price_jump(prev, close)
        if jump:
            jump.update({'from_date': prev_date, 'to_date': r.get('date') if hasattr(r, 'get') else r['date']})
            return jump
        prev = close
        prev_date = r['date']
    return None

def month_cutoffs(start='2023-01-01', end=None, step=1):
    end=end or datetime.now(timezone.utc).date().isoformat()
    y,m=map(int,start[:7].split('-')); ey,em=map(int,end[:7].split('-'))
    out=[]; i=0
    while (y,m) <= (ey,em):
        if i % step == 0: out.append(f'{y:04d}-{m:02d}-01')
        m+=1
        if m>12: y+=1; m=1
        i+=1
    return out

def available_cutoffs(conn, symbols, start, end=None, min_history=160, horizon=60) -> list[str]:
    end=end or datetime.now(timezone.utc).date().isoformat()
    dates=set()
    for symbol in symbols:
        rows=conn.execute('SELECT date FROM price_bars WHERE symbol=? AND timeframe="1d" ORDER BY date ASC',(symbol,)).fetchall()
        ds=[r['date'] for r in rows]
        for idx,d in enumerate(ds):
            if d < start or d > end: continue
            if idx < min_history: continue
            if len(ds)-idx < max(10,horizon//3): continue
            dates.add(d)
    return sorted(dates)

def mixed_cutoffs(conn, symbols, start='2023-01-01', end=None, monthly_step=2, random_count=24, seed=42, recent_cap_per_quarter=4, horizon=60) -> tuple[list[str], dict]:
    monthly=month_cutoffs(start,end,monthly_step)
    pool=[d for d in available_cutoffs(conn,symbols,start,end,horizon=horizon) if d not in set(monthly)]
    rng=random.Random(seed)
    sampled=sorted(rng.sample(pool, min(random_count, len(pool)))) if pool else []
    merged=sorted(set(monthly+sampled))
    capped=[]; quarter_counts={}
    for d in sorted(merged, reverse=True):
        q=f'{d[:4]}-Q{((int(d[5:7])-1)//3)+1}'
        if quarter_counts.get(q,0) >= recent_cap_per_quarter:
            continue
        quarter_counts[q]=quarter_counts.get(q,0)+1
        capped.append(d)
    capped=sorted(capped)
    meta={'mode':'mixed_monthly_random','seed':seed,'monthly_count':len(monthly),'random_pool_count':len(pool),'random_requested':random_count,'random_selected':len(sampled),'recent_cap_per_quarter':recent_cap_per_quarter,'quarter_counts':quarter_counts,'cutoff_count':len(capped)}
    return capped, meta

def rsi14(closes):
    if len(closes) < 15: return None
    gains=[]; losses=[]
    for a,b in zip(closes[-15:-1], closes[-14:]):
        diff=b-a
        gains.append(max(0,diff)); losses.append(max(0,-diff))
    avg_gain=sum(gains)/14; avg_loss=sum(losses)/14
    if avg_loss == 0: return 100.0
    rs=avg_gain/avg_loss
    return round(100 - (100/(1+rs)),2)

def signal(history, logic_name):
    cfg=logic_config(logic_name)
    if not cfg: return None
    if len(history)<120: return None
    # If the recent lookback contains a split/capital-reduction-sized discontinuity,
    # do not let the mechanical price reset masquerade as momentum or breakout skill.
    if corporate_action_jump(history[-130:]):
        return None
    closes=[float(r['close']) for r in history]
    vols=[]
    for r in history:
        try:
            vols.append(float(r['volume'] or 0))
        except (IndexError, KeyError):
            vols.append(0.0)
    close=closes[-1]
    highs=[float(r['high'] if 'high' in r.keys() and r['high'] is not None else r['close']) for r in history]
    lows=[float(r['low'] if 'low' in r.keys() and r['low'] is not None else r['close']) for r in history]
    opens=[float(r['open'] if 'open' in r.keys() and r['open'] is not None else r['close']) for r in history]
    high20=max(closes[-20:]); low20=min(closes[-20:]); high60=max(closes[-60:]); high120=max(closes[-120:])
    low60=min(closes[-60:]); ma20=sum(closes[-20:])/20; ma50=sum(closes[-50:])/50; ma120=sum(closes[-120:])/120
    r1=pct(close,closes[-2]) if len(closes)>2 else None; r5=pct(close,closes[-6]) if len(closes)>6 else None
    r20=pct(close,closes[-21]) if len(closes)>21 else None; r60=pct(close,closes[-61]) if len(closes)>61 else None
    r120=pct(close,closes[-121]) if len(closes)>121 else None
    range20=(high20-low20)/close if close else 0
    target=round(close*(1+min(max(range20*cfg['target_mult'],cfg['target_min']),cfg['target_cap'])),2)
    stop=round(close*(1-min(max(range20*cfg['stop_mult'],cfg['stop_min']),cfg['stop_cap'])),2)
    score=0; reasons=[]; auxiliary=[]
    fam=cfg.get('family')
    if fam == 'technical_data_only':
        fast=int(cfg.get('fast_ma') or 20); slow=int(cfg.get('slow_ma') or 60)
        if len(closes) >= slow:
            ma_fast=sum(closes[-fast:])/fast
            ma_slow=sum(closes[-slow:])/slow
            if ma_fast > ma_slow and close > ma_fast:
                score += 30; reasons.append(f'data-only MA trend {fast}>{slow}')
            if r20 and r20 > 0:
                score += min(16, max(0, r20)); reasons.append(f'data-only 20d return {r20}%')
        lookback=int(cfg.get('breakout_lookback') or 20)
        if len(closes) >= lookback+1:
            prev_high=max(closes[-lookback-1:-1])
            if close > prev_high*0.995:
                score += 28; reasons.append(f'data-only {lookback}d breakout')
        vol20=sum(vols[-20:])/20 if len(vols)>=20 else 0
        vol60=sum(vols[-60:])/60 if len(vols)>=60 else 0
        v_mult=float(cfg.get('volume_mult') or 1.1)
        if vol60 and vol20/vol60 >= v_mult:
            score += 18; reasons.append(f'data-only volume expansion {round(vol20/vol60,2)}x')
        rsi=rsi14(closes)
        rsi_max=float(cfg.get('rsi_max') or 38)
        if rsi is not None and rsi <= rsi_max and close > low20*1.02:
            score += 30; reasons.append(f'data-only RSI reversion setup rsi={rsi}')
        if not reasons:
            reasons.append('data-only technical rule not confirmed')
    elif fam == 'volatility_contraction_breakout':
        lookback=int(cfg.get('breakout_lookback') or 40)
        prev_high=max(closes[-lookback-1:-1]) if len(closes)>=lookback+1 else high60
        range60=(high60-low60)/close if close else 0
        range20=(high20-low20)/close if close else 0
        contraction_ratio=float(cfg.get('contraction_ratio') or 0.75)
        vol20=sum(vols[-20:])/20 if len(vols)>=20 else 0
        vol60=sum(vols[-60:])/60 if len(vols)>=60 else 0
        if range60 and range20/range60 <= contraction_ratio:
            score += 26; reasons.append(f'volatility contraction range20/range60={round(range20/range60,2)}')
        if close > prev_high*0.995:
            score += 28; reasons.append(f'{lookback}d breakout after contraction')
        if vol60 and vol20/vol60 >= 1.08:
            score += 12; reasons.append(f'volume confirmation {round(vol20/vol60,2)}x')
        if close < ma50:
            score -= 18; auxiliary.append('below 50d average')
    elif fam == 'pullback_in_uptrend':
        pullback_min=float(cfg.get('pullback_min') or 0.04)
        trend_stack=ma20 > ma50 > ma120
        pullback_from_high=(high20-close)/high20 if high20 else 0
        rsi=rsi14(closes)
        if trend_stack and close > ma50:
            score += 30; reasons.append('uptrend stack ma20>ma50>ma120')
        if pullback_from_high >= pullback_min and close >= ma50*0.98:
            score += 24; reasons.append(f'controlled pullback {round(pullback_from_high*100,2)}%')
        if rsi is not None and 38 <= rsi <= 58:
            score += 12; reasons.append(f'neutral pullback RSI={rsi}')
        if r60 and r60 > 8:
            score += 10; auxiliary.append(f'60d relative strength {r60}%')
        if close < ma120:
            score -= 25; reasons.append('below 120d average')
    elif fam == 'relative_strength_persistence':
        vol20=sum(vols[-20:])/20 if len(vols)>=20 else 0
        vol60=sum(vols[-60:])/60 if len(vols)>=60 else 0
        r60_min=float(cfg.get('r60_min') or 8)
        if r60 and r60 >= r60_min:
            score += 28; reasons.append(f'60d strength persistence {r60}%')
        if r20 and r20 > 2:
            score += 14; reasons.append(f'20d positive continuation {r20}%')
        if ma20 > ma50 and close > ma20:
            score += 18; reasons.append('price above rising intermediate trend')
        if r120 and r120 > 0:
            score += 8; auxiliary.append(f'120d positive base {r120}%')
        if vol60 and vol20/vol60 >= 0.9:
            score += 6; auxiliary.append('volume not fading')
        if close < ma50:
            score -= 22; reasons.append('relative strength failed below 50d')
    elif fam == 'quality_pullback_uptrend':
        pullback_min=float(cfg.get('pullback_min') or 0.03)
        trend_stack=ma20 > ma50 > ma120
        pullback_from_high=(high20-close)/high20 if high20 else 0
        rsi=rsi14(closes)
        range60=(high60-low60)/close if close else 0
        range20=(high20-low20)/close if close else 0
        vol20=sum(vols[-20:])/20 if len(vols)>=20 else 0
        vol60=sum(vols[-60:])/60 if len(vols)>=60 else 0
        if trend_stack and close > ma50 and ma50 > ma120*1.02:
            score += 32; reasons.append('quality uptrend stack with 50d cushion')
        if pullback_from_high >= pullback_min and pullback_from_high <= 0.12 and close >= ma50*0.99:
            score += 24; reasons.append(f'bounded pullback {round(pullback_from_high*100,2)}%')
        if rsi is not None and 42 <= rsi <= 58:
            score += 12; reasons.append(f'stable pullback RSI={rsi}')
        if r60 and r60 > 6 and (not r20 or r20 > -3):
            score += 10; auxiliary.append(f'stable 60d strength {r60}%')
        if range60 and range20/range60 <= 0.82:
            score += 8; auxiliary.append('compressed recent range')
        if vol60 and 0.75 <= vol20/vol60 <= 1.35:
            score += 6; auxiliary.append('volume stable, not exhausted')
        if close < ma50 or range20 > 0.18:
            score -= 28; reasons.append('tail-risk filter failed')
    elif fam == 'quality_breakout':
        lookback=int(cfg.get('breakout_lookback') or 40)
        prev_high=max(closes[-lookback-1:-1]) if len(closes)>=lookback+1 else high60
        range60=(high60-low60)/close if close else 0
        range20=(high20-low20)/close if close else 0
        vol20=sum(vols[-20:])/20 if len(vols)>=20 else 0
        vol60=sum(vols[-60:])/60 if len(vols)>=60 else 0
        trend_stack=ma20 > ma50 > ma120
        if close > prev_high*0.998 and trend_stack:
            score += 34; reasons.append(f'quality {lookback}d breakout with trend stack')
        if range60 and range20/range60 <= 0.75:
            score += 18; reasons.append('pre-breakout volatility contraction')
        if vol60 and 1.0 <= vol20/vol60 <= 1.6:
            score += 12; reasons.append(f'controlled volume confirmation {round(vol20/vol60,2)}x')
        if r60 and 5 <= r60 <= 35:
            score += 10; auxiliary.append(f'non-overextended 60d return {r60}%')
        if close < ma50 or range20 > 0.20 or (r20 and r20 > 22):
            score -= 30; reasons.append('breakout tail-risk/overextension filter failed')
    elif fam == 'stable_relative_strength':
        r60_min=float(cfg.get('r60_min') or 8)
        range20=(high20-low20)/close if close else 0
        range60=(high60-low60)/close if close else 0
        vol20=sum(vols[-20:])/20 if len(vols)>=20 else 0
        vol60=sum(vols[-60:])/60 if len(vols)>=60 else 0
        if r60 and r60 >= r60_min and r60 <= 35:
            score += 28; reasons.append(f'stable 60d relative strength {r60}%')
        if r20 and -2 <= r20 <= 14:
            score += 14; reasons.append(f'non-exhausted 20d continuation {r20}%')
        if ma20 > ma50 > ma120 and close > ma20:
            score += 22; reasons.append('persistent trend stack')
        if range60 and range20/range60 <= 0.85:
            score += 8; auxiliary.append('lower recent volatility')
        if vol60 and vol20/vol60 >= 0.85:
            score += 6; auxiliary.append('volume participation intact')
        if close < ma50 or range20 > 0.18:
            score -= 28; reasons.append('stable strength risk filter failed')
    elif fam == 'supply_close_strength':
        vol20_prev=sum(vols[-21:-1])/20 if len(vols)>=21 else 0
        vol60=sum(vols[-60:])/60 if len(vols)>=60 else 0
        vol_ratio=vols[-1]/vol20_prev if vol20_prev else 0
        intraday_range=highs[-1]-lows[-1]
        close_pos=(closes[-1]-lows[-1])/intraday_range if intraday_range else 0.5
        body_pos=(closes[-1]-opens[-1])/opens[-1]*100 if opens[-1] else 0
        prev_high20=max(highs[-21:-1]) if len(highs)>=21 else high20
        trend_ok=close > ma20 and ma20 >= ma50*0.98
        accumulation_proxy=close_pos >= float(cfg.get('close_pos_min') or 0.75) and body_pos >= 0
        volume_confirmed = vol_ratio >= float(cfg.get('volume_mult') or 1.2) or (vol60 and vols[-1]/vol60 >= 1.1)
        if vol_ratio >= float(cfg.get('volume_mult') or 1.2):
            score += 24; reasons.append(f'전일 거래량 확대 {round(vol_ratio,2)}x')
        if accumulation_proxy:
            score += 24; reasons.append(f'종가 상단 마감 close-position {round(close_pos,2)}')
        if trend_ok:
            score += 16; reasons.append('종가가 20일선 위/중기 추세 우위')
        if close >= prev_high20*0.995:
            score += 14; reasons.append('20일 고점권 종가')
        if vol60 and vols[-1]/vol60 >= 1.1:
            score += 8; auxiliary.append(f'60일 대비 수급 압력 {round(vols[-1]/vol60,2)}x')
        if r20 and r20 > 25:
            score -= 18; reasons.append('단기 과열 추격 위험')
        if not volume_confirmed:
            score -= 14; reasons.append('거래량 확장 미확인: 수급 proxy 신뢰 낮음')
        if close < ma50 or close_pos < 0.55:
            score -= 22; reasons.append('마감강도/추세 필터 실패')
        auxiliary.append('외국인/기관 순매수 데이터는 아직 미연동: price-volume proxy only')
    elif fam == 'us_relative_strength_pullback':
        # US-only relative-strength pullback candidate. Historical retest showed
        # positive average excess and improved tail, but EV remains negative;
        # keep as paper/validation-active only until wider validation improves EV.
        vol20=sum(vols[-20:])/20 if len(vols)>=20 else 0
        vol60=sum(vols[-60:])/60 if len(vols)>=60 else 0
        above_ma50=close > ma50
        above_ma20=close > ma20
        pullback_ok = (r5 is not None and float(cfg.get('pullback_min_5d', -7)) <= r5 <= float(cfg.get('pullback_max_5d', 0)))
        rs20_ok = (r20 is not None and r20 >= float(cfg.get('min_excess_20d_pct', 0)))
        rs60_ok = (r60 is not None and r60 >= float(cfg.get('min_excess_60d_pct', 2)))
        gap_ok = (r1 is None or r1 <= float(cfg.get('avoid_gap_1d_pct', 2.5)))
        if rs60_ok:
            score += 24; reasons.append(f'60d relative strength {r60}%')
        if rs20_ok:
            score += 16; reasons.append(f'20d relative strength {r20}%')
        if pullback_ok:
            score += 22; reasons.append(f'controlled 5d pullback {r5}%')
        if above_ma50:
            score += 14; reasons.append('above 50d average')
        if above_ma20:
            score += 6; auxiliary.append('above 20d average')
        if vol60 and vol20 and vol20/vol60 >= 0.85:
            score += 6; auxiliary.append('volume participation not collapsing')
        if not pullback_ok:
            score -= 24; reasons.append('pullback window not satisfied')
        if not rs60_ok:
            score -= 20; reasons.append('60d relative strength too weak')
        if not rs20_ok:
            score -= 10; auxiliary.append('20d relative strength below threshold')
        if not gap_ok:
            score -= 20; reasons.append('1d gap/extension chase risk')
        if close < ma50:
            score -= 25; reasons.append('below 50d average')
    elif fam == 'us_momentum':
        vol20=sum(vols[-20:])/20 if len(vols)>=20 else 0
        vol60=sum(vols[-60:])/60 if len(vols)>=60 else 0
        breakout60 = close > high60*0.995
        breakout20 = close > high20*0.995
        trend_stack = ma20 > ma50 > ma120
        above_ma = close > ma20 and close > ma50
        volume_confirm = bool(vol60 and vol20/vol60>1.15)
        intermediate_strength = bool(r60 and r60>12)
        long_trend = bool(r120 and r120>20)
        short_trend = bool(r20 and r20>6)
        if breakout60: score+=30; reasons.append('60d breakout confirmation')
        elif breakout20: score+=18; reasons.append('20d breakout confirmation')
        if intermediate_strength: score+=20; reasons.append(f'60d trend confirmation {r60}%')
        if long_trend: score+=14; reasons.append(f'120d trend confirmation {r120}%')
        if trend_stack: score+=18; reasons.append('ma20>ma50>ma120 trend quality')
        if above_ma: score+=8; auxiliary.append('above key moving averages')
        if volume_confirm: score+=10; reasons.append('volume confirmation')
        if short_trend:
            score+=6
            auxiliary.append(f'20d momentum support {r20}%')
        if logic_name == 'us_high_upside_trend_v1' and r60 and r60 > 25 and (breakout60 or volume_confirm):
            score+=10; reasons.append('high-upside acceleration with confirmation')
        confirmation_count=sum([breakout60 or breakout20, intermediate_strength or long_trend, trend_stack, volume_confirm])
        if confirmation_count < 3:
            score-=25; auxiliary.append('insufficient non-momentum confirmation')
        if close < ma50:
            score-=25; reasons.append('below 50d average')
    else:
        # Range strategies may use momentum to locate a current price state, but
        # do not present short-term price rise as the investment thesis by itself.
        if r20 and r20>5: score+=18; auxiliary.append(f'20d momentum support {r20}%')
        if r60 and r60>10: score+=18; reasons.append(f'60d trend support {r60}%')
        if high120 and close/high120>0.95: score+=22; reasons.append('near 120d high / breakout zone')
    if auxiliary and reasons:
        reasons.extend(auxiliary[:2])
    elif auxiliary:
        reasons.append('price-state support only; needs validation confirmation')
    action='candidate_buy_zone' if score>=cfg['score_min'] else 'watch'
    return {'entry':close,'target':target,'stop':stop,'score':score,'action':action,'reasons':reasons,'logic':logic_name}

def judge(future,target,stop,entry=None,fill_model='optimistic_limit_target'):
    maxp=minp=None
    prev=entry
    for idx,row in enumerate(future,start=1):
        # Corporate-action detection still uses close-to-close discontinuities.
        close=float(row['close'])
        if large_price_jump(prev, close):
            return 'corporate_action_excluded', idx, maxp, minp, None, 'corporate_action_filter'
        high=float(row['high'] if row['high'] is not None else row['close'])
        low=float(row['low'] if row['low'] is not None else row['close'])
        maxp=high if maxp is None else max(maxp,high)
        minp=low if minp is None else min(minp,low)
        if fill_model == 'close_only':
            if close<=stop: return 'fail',idx,maxp,minp,close,'close_stop'
            if close>=target: return 'success',idx,maxp,minp,target,'close_target'
        elif fill_model == 'bracket_intraday_conservative':
            # With daily bars, same-day target/stop order is unknowable; conservative
            # bracket assumes the stop is hit first when both prices trade intraday.
            if low<=stop: return 'fail',idx,maxp,minp,stop,'intraday_stop_first'
            if high>=target: return 'success',idx,maxp,minp,target,'intraday_target'
        else:
            # Optimistic limit-sell model for the intended use case: buy after the
            # recommendation, place a target limit sell, and count any intraday high
            # touch as filled. Stop remains close-based unless a stricter bracket
            # model is explicitly requested.
            if high>=target: return 'success',idx,maxp,minp,target,'intraday_target_limit'
            if close<=stop: return 'fail',idx,maxp,minp,close,'close_stop'
        prev=close
    return 'timeout',len(future),maxp,minp,None,'horizon_close'

def benchmark_symbol_for(symbol):
    if symbol.endswith('.KQ'):
        return '^KQ11'
    if symbol.endswith('.KS'):
        return '^KS11'
    # Compare US stocks/ETFs against the broad US market so bull-market beta
    # does not masquerade as strategy skill. SPY itself remains self-benchmarked.
    return 'SPY'

def benchmark_return(conn, cutoff, horizon, benchmark='SPY'):
    rows=conn.execute('SELECT date, close FROM price_bars WHERE symbol=? AND timeframe="1d" ORDER BY date ASC',(benchmark,)).fetchall()
    fut=[r for r in rows if r['date']>=cutoff][:horizon]
    if len(fut)<2: return None
    return pct(float(fut[-1]['close']), float(fut[0]['close']))

def audit_symbol(conn,symbol,cutoffs,horizon,logic_names,fill_model='optimistic_limit_target',target_adjustments=None):
    target_adjustments=target_adjustments or {}
    rows=conn.execute('SELECT date, open, high, low, close, volume FROM price_bars WHERE symbol=? AND timeframe="1d" ORDER BY date ASC',(symbol,)).fetchall()
    out=[]
    for cutoff in cutoffs:
      hist=[r for r in rows if r['date']<cutoff]; fut=[r for r in rows if r['date']>=cutoff][:horizon]
      for logic in logic_names:
        sig=signal(hist,logic)
        if not sig or len(fut)<max(10,horizon//3):
          out.append({'symbol':symbol,'name':display_name(symbol),'cutoff':cutoff,'logic':logic,'status':'insufficient_data','history_bars':len(hist),'future_bars':len(fut)}); continue
        bench=benchmark_return(conn,cutoff,horizon, benchmark_symbol_for(symbol))
        committee_fill_profiles={}
        for profile_name, model in [('aggressive','optimistic_limit_target'),('neutral','close_only'),('conservative','bracket_intraday_conservative')]:
            pr, pdays, pmax, pmin, pexit, preason=judge(fut,sig['target'],sig['stop'],entry=sig['entry'],fill_model=model)
            pfinal=(float(pexit) if pexit is not None else (float(fut[-1]['close']) if fut and pr!='corporate_action_excluded' else sig['entry']))
            pfret=pct(pfinal,sig['entry'])
            profile_item={'fill_model':model,'result':pr,'days_to_event':pdays,'final_return_pct':pfret,'excess_return_pct':round(pfret-bench,2) if pfret is not None and bench is not None else None,'exit_price':round(pfinal,4) if pfinal is not None else None,'exit_reason':preason,'max_upside_pct':pct(pmax,sig['entry']) if pmax else None,'max_drawdown_pct':pct(pmin,sig['entry']) if pmin else None}
            attach_evaluation(profile_item)
            committee_fill_profiles[profile_name]=profile_item
        primary=committee_fill_profiles.get('neutral' if fill_model == 'close_only' else ('aggressive' if fill_model == 'optimistic_limit_target' else 'conservative')) or committee_fill_profiles['neutral']
        adjustment=target_adjustments.get((logic, market_of(symbol)))
        target_adjusted_profile=None
        adj_target=adjusted_target_for(sig['entry'], sig['target'], adjustment)
        if adj_target:
            ar, adays, amax, amin, aexit, areason=judge(fut,adj_target,sig['stop'],entry=sig['entry'],fill_model=fill_model)
            afinal=(float(aexit) if aexit is not None else (float(fut[-1]['close']) if fut and ar!='corporate_action_excluded' else sig['entry']))
            afret=pct(afinal,sig['entry'])
            target_adjusted_profile={'target':adj_target,'original_target':sig['target'],'target_scale':adjustment.get('target_scale'),'target_return_adjustment_pct_points':adjustment.get('target_return_adjustment_pct_points'),'target_adjustment_basis':adjustment.get('target_adjustment_basis'),'short_horizon_hint':adjustment.get('short_horizon_hint'),'source':'strategy_success_optimizer','reason':adjustment.get('reason'),'result':ar,'days_to_event':adays,'final_return_pct':afret,'excess_return_pct':round(afret-bench,2) if afret is not None and bench is not None else None,'exit_price':round(afinal,4) if afinal is not None else None,'exit_reason':areason,'max_upside_pct':pct(amax,sig['entry']) if amax else None,'max_drawdown_pct':pct(amin,sig['entry']) if amin else None,'fill_model':fill_model}
            attach_evaluation(target_adjusted_profile)
        result=primary['result']; days=primary['days_to_event']; final=primary['exit_price']; fret=primary['final_return_pct']; maxp=primary.get('max_upside_pct'); minp=primary.get('max_drawdown_pct')
        status = 'excluded_corporate_action' if result == 'corporate_action_excluded' else 'audited'
        row={'symbol':symbol,'market':market_of(symbol),'name':display_name(symbol),'cutoff':cutoff,'status':status,'logic':logic,'entry':sig['entry'],'target':sig['target'],'stop':sig['stop'],'score':sig['score'],'action':sig['action'],'result':result,'days_to_event':days,'horizon_days':horizon,'final_return_pct':fret,'benchmark_return_pct':bench,'excess_return_pct':primary.get('excess_return_pct'),'max_upside_pct':maxp,'max_drawdown_pct':minp,'fill_model':fill_model,'committee_fill_profiles':committee_fill_profiles,'target_adjusted_profile':target_adjusted_profile,'exit_price':round(final,4) if final is not None else None,'exit_reason':primary.get('exit_reason'),'reasons':sig['reasons']}
        attach_evaluation(row)
        out.append(row)
    return out


def wilson_lower_bound(successes: int, n: int, z: float = 1.96) -> float | None:
    if n <= 0:
        return None
    phat = successes / n
    denom = 1 + z*z/n
    centre = phat + z*z/(2*n)
    margin = z * ((phat*(1-phat) + z*z/(4*n))/n) ** 0.5
    return round((centre - margin) / denom * 100, 2)


def percentile(values, q):
    vals=sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals)==1:
        return round(vals[0],2)
    pos=(len(vals)-1)*q
    lo=int(pos); hi=min(lo+1,len(vals)-1)
    frac=pos-lo
    return round(vals[lo]*(1-frac)+vals[hi]*frac,2)


def evaluation_result_for(item):
    if item.get('result') == 'corporate_action_excluded':
        return 'excluded', False, 'corporate_action_excluded'
    excess=item.get('excess_return_pct')
    final=item.get('final_return_pct')
    drawdown=item.get('max_drawdown_pct')
    result=item.get('result')
    if excess is None:
        if final is not None and final > 1 and (drawdown is None or drawdown > -8):
            return 'positive', True, 'positive_absolute_return_without_benchmark'
        if final is not None and final < -3:
            return 'negative', False, 'negative_absolute_return_without_benchmark'
        return 'neutral', False, 'missing_benchmark'
    # Target success remains positive unless it underperformed badly versus benchmark.
    if result == 'success' and excess >= -2:
        return 'positive', True, 'target_success'
    # A recommendation can be useful even without target fill when it beats the benchmark
    # and avoids material drawdown. This is the strategic/evaluation success layer.
    if excess >= 1.0 and (drawdown is None or drawdown >= -8):
        return 'positive', True, 'positive_excess_controlled_drawdown'
    if excess >= 0 and final is not None and final >= 0 and (drawdown is None or drawdown >= -6):
        return 'positive', True, 'nonnegative_excess_and_return'
    if excess <= -3 or (final is not None and final <= -4) or (drawdown is not None and drawdown <= -10):
        return 'negative', False, 'negative_excess_or_drawdown'
    return 'neutral', False, 'mixed_or_small_edge'


def attach_evaluation(item):
    ev, ok, reason=evaluation_result_for(item)
    item['evaluation_result']=ev
    item['evaluation_success']=ok
    item['evaluation_reason']=reason
    return item


def market_of(symbol):
    return 'KR' if str(symbol).upper().endswith(('.KS','.KQ')) else 'US'


def load_target_adjustments(path='/tmp/strategy_success_optimizer_latest.json'):
    try:
        data=json.load(open(path))
        plan=data.get('action_plan') or {}
        return {(x.get('logic'), x.get('market')): x for x in (plan.get('target_adjustments') or []) if x.get('logic') and x.get('market')}
    except Exception:
        return {}


def adjusted_target_for(entry, target, adjustment):
    if not adjustment or entry is None or target is None:
        return None
    try:
        pp=adjustment.get('target_return_adjustment_pct_points')
        if pp is not None:
            original_return=(float(target)-float(entry))/float(entry)*100
            adjusted_return=max(original_return-float(pp), 0.0)
            out=round(float(entry)*(1+adjusted_return/100), 2)
        else:
            scale=float(adjustment.get('target_scale') or 1.0)
            out=round(float(entry) + (float(target)-float(entry))*scale, 2)
        return out if out > float(entry) else None
    except Exception:
        return None


def validation_quality(arr, period_stats=None):
    n=len(arr)
    evaluated=[attach_evaluation(dict(x)) for x in arr]
    evaluation_successes=sum(1 for x in evaluated if x.get('evaluation_success'))
    evaluation_positive_rate=round(evaluation_successes/n*100,2) if n else 0
    evaluation_lb=wilson_lower_bound(evaluation_successes,n)
    successes=sum(1 for x in arr if x.get('result')=='success')
    fails=[x for x in arr if x.get('result')=='fail']
    timeouts=[x for x in arr if x.get('result')=='timeout']
    excess=[x.get('excess_return_pct') for x in arr if x.get('excess_return_pct') is not None]
    drawdowns=[x.get('max_drawdown_pct') for x in arr if x.get('max_drawdown_pct') is not None]
    upside=[x.get('max_upside_pct') for x in arr if x.get('max_upside_pct') is not None]
    years=period_stats or {}
    positive_periods=sum(1 for v in years.values() if v.get('samples',0)>=5 and (v.get('avg_excess_return_pct') or 0)>0)
    tested_periods=sum(1 for v in years.values() if v.get('samples',0)>=5)
    lb=wilson_lower_bound(successes,n)
    avg_excess=round(sum(excess)/len(excess),2) if excess else None
    p25_excess=percentile(excess,0.25)
    p10_excess=percentile(excess,0.10)
    avg_fail_drawdown=round(sum((x.get('max_drawdown_pct') or 0) for x in fails)/len(fails),2) if fails else None
    avg_success_upside=round(sum((x.get('max_upside_pct') or 0) for x in arr if x.get('result')=='success')/max(1,successes),2) if successes else None
    timeout_rate=round(len(timeouts)/n*100,2) if n else 0
    fail_rate=round(len(fails)/n*100,2) if n else 0
    flags=[]
    if n < 60: flags.append('low_sample_size')
    if evaluation_lb is not None and evaluation_lb < 38: flags.append('weak_evaluation_success_confidence_interval')
    elif lb is not None and lb < 25: flags.append('weak_execution_success_confidence_interval')
    if avg_excess is None or avg_excess <= 0: flags.append('no_positive_average_excess')
    if p25_excess is not None and p25_excess < -3: flags.append('left_tail_excess_risk')
    if tested_periods and positive_periods < max(2, tested_periods//2): flags.append('period_instability')
    if timeout_rate > 35: flags.append('high_timeout_rate')
    if avg_fail_drawdown is not None and avg_success_upside is not None and abs(avg_fail_drawdown) > max(4, avg_success_upside*0.85): flags.append('unfavorable_payoff_asymmetry')
    expected_excess_value=round((avg_excess or 0) - max(0, -(p10_excess or 0))*0.35 - fail_rate*0.03 - timeout_rate*0.015, 2) if n else None
    score=100
    penalties={'low_sample_size':18,'weak_evaluation_success_confidence_interval':18,'weak_execution_success_confidence_interval':10,'no_positive_average_excess':18,'left_tail_excess_risk':14,'period_instability':16,'high_timeout_rate':6,'unfavorable_payoff_asymmetry':12}
    penalty_breakdown=[]
    flag_reasons={
        'low_sample_size': f'sample count {n} < 60',
        'weak_evaluation_success_confidence_interval': f'evaluation Wilson lower {evaluation_lb} < 38',
        'weak_execution_success_confidence_interval': f'execution Wilson lower {lb} < 25',
        'no_positive_average_excess': f'avg_excess_return_pct {avg_excess} <= 0',
        'left_tail_excess_risk': f'p25_excess_return_pct {p25_excess} < -3',
        'period_instability': f'positive_periods {positive_periods} below required {max(2, tested_periods//2) if tested_periods else 0} of tested_periods {tested_periods}',
        'high_timeout_rate': f'timeout_rate_pct {timeout_rate} > 35',
        'unfavorable_payoff_asymmetry': f'avg_fail_drawdown_pct {avg_fail_drawdown} vs avg_success_upside_pct {avg_success_upside}',
    }
    for f in flags:
        penalty=penalties.get(f,8)
        score-=penalty
        penalty_breakdown.append({'flag':f,'penalty':penalty,'reason':flag_reasons.get(f,'quality flag active')})
    score=max(0,min(100,score))
    grade='high' if score>=80 and not flags else ('medium' if score>=60 else 'low')
    ev_components={'avg_excess_return_pct':avg_excess,'p10_tail_penalty':round(max(0, -(p10_excess or 0))*0.35,2),'fail_rate_penalty':round(fail_rate*0.03,2),'timeout_rate_penalty':round(timeout_rate*0.015,2)}
    return {'quality_score':score,'quality_grade':grade,'quality_flags':flags,'quality_penalty_breakdown':penalty_breakdown,'quality_score_formula':'100 - sum(flag penalties), clamped 0..100','expected_excess_value_components':ev_components,'success_rate_wilson_low_pct':lb,'evaluation_success_rate_pct':evaluation_positive_rate,'evaluation_success_wilson_low_pct':evaluation_lb,'p25_excess_return_pct':p25_excess,'p10_excess_return_pct':p10_excess,'expected_excess_value_pct':expected_excess_value,'timeout_rate_pct':timeout_rate,'fail_rate_pct':fail_rate,'avg_fail_drawdown_pct':avg_fail_drawdown,'avg_success_upside_pct':avg_success_upside,'positive_periods':positive_periods,'tested_periods':tested_periods}


def strategy_trust_improvement_plan(summary: dict) -> dict:
    """Expose levers that improve Strategy Trust Audit labeling quality.

    Audit is not trying to raise a user-facing score. Numeric quality remains
    an internal confidence signal; this plan prioritizes evidence that makes
    strategy trust labels, role profiles, and fund-routing hints more accurate.
    """
    best = (summary or {}).get('best') or {}
    flags = set(best.get('quality_flags') or [])
    penalties = best.get('quality_penalty_breakdown') or []
    by_flag = {p.get('flag'): p for p in penalties if p.get('flag')}
    score = best.get('quality_score')
    positive_periods = int(best.get('positive_periods') or 0)
    tested_periods = int(best.get('tested_periods') or 0)
    required_positive_periods = max(2, tested_periods // 2) if tested_periods else 0
    actions = []

    def add(flag: str, lever: str, target: str, experiment: str, priority: int, points: int | None = None):
        penalty = by_flag.get(flag, {})
        actions.append({
            'flag': flag,
            'priority': priority,
            'label_quality_points': points if points is not None else penalty.get('penalty', 0),
            'current_reason': penalty.get('reason'),
            'lever': lever,
            'target': target,
            'next_experiment': experiment,
        })

    if 'left_tail_excess_risk' in flags:
        add('left_tail_excess_risk', 'exit_policy_and_entry_quarantine', 'p25_excess_return_pct >= -3 and p10_excess_return_pct > -8', 'Retest stop/timeout rules and quarantine symbol-period cohorts that dominate p10/p25 losses.', 1)
    if 'negative_expected_excess_value' in flags:
        add('negative_expected_excess_value', 'expected_value_gate', 'expected_excess_value_pct > 0', 'Block buy-zone authority for logic/context pairs with negative EV until adjusted exits or context routing turns EV positive.', 2, 10)
    if 'no_positive_average_excess' in flags:
        add('no_positive_average_excess', 'positive_excess_candidate_generation', 'avg_excess_return_pct > 0', 'Generate candidates from positive cohorts first, then validate across active-universe symbols before routing to recommendations.', 3)
    if 'period_instability' in flags:
        add('period_instability', 'regime_split_routing', f'positive_periods >= {required_positive_periods}', 'Split audit by market/regime/score bucket and grant authority only to contexts with positive period evidence.', 4)
    if 'weak_evaluation_success_confidence_interval' in flags:
        add('weak_evaluation_success_confidence_interval', 'sample_expansion', 'evaluation Wilson lower >= 38', 'Expand symbol-edge validation samples for the dominant critic bottleneck without promoting concentrated single-symbol wins.', 5)
    if 'unfavorable_payoff_asymmetry' in flags:
        add('unfavorable_payoff_asymmetry', 'payoff_shape_repair', 'average fail drawdown materially smaller than average success upside', 'Prefer asymmetric entry plans: lower target buy, tighter invalidation, and no-chase filters for overheated signals.', 6)

    actions.sort(key=lambda x: (x.get('priority') or 99, -(x.get('label_quality_points') or 0)))
    projected_quality_if_top_three_resolved = None
    if isinstance(score, (int, float)):
        projected_quality_if_top_three_resolved = max(0, min(100, round(score + sum(float(a.get('label_quality_points') or 0) for a in actions[:3]), 2)))
    return {
        'current_quality_score': score,
        'current_expected_excess_value_pct': best.get('expected_excess_value_pct'),
        'current_avg_excess_return_pct': best.get('avg_excess_return_pct'),
        'current_p25_excess_return_pct': best.get('p25_excess_return_pct'),
        'current_p10_excess_return_pct': best.get('p10_excess_return_pct'),
        'positive_periods': positive_periods,
        'tested_periods': tested_periods,
        'required_positive_periods': required_positive_periods,
        'top_actions': actions[:6],
        'first_three_label_quality_points': sum(float(a.get('label_quality_points') or 0) for a in actions[:3]),
        'projected_quality_if_top_three_resolved': projected_quality_if_top_three_resolved,
        'policy': 'improve trust labels and fund routing evidence; do not optimize for a user-facing Audit score',
    }

def metric_block(arr):
    if not arr:
        return {'samples':0,'verdict':'insufficient_samples'}
    evaluated=[attach_evaluation(dict(x)) for x in arr]
    es=sum(1 for x in evaluated if x.get('evaluation_success'))
    epos=sum(1 for x in evaluated if x.get('evaluation_result')=='positive')
    eneg=sum(1 for x in evaluated if x.get('evaluation_result')=='negative')
    eneutral=sum(1 for x in evaluated if x.get('evaluation_result')=='neutral')
    s=sum(1 for x in arr if x['result']=='success'); f=sum(1 for x in arr if x['result']=='fail'); t=sum(1 for x in arr if x['result']=='timeout')
    avg=round(sum(x.get('final_return_pct') or 0 for x in arr)/len(arr),2)
    excess_vals=[x.get('excess_return_pct') for x in arr if x.get('excess_return_pct') is not None]
    avg_excess=round(sum(excess_vals)/len(excess_vals),2) if excess_vals else None
    excess_win=round(sum(1 for x in excess_vals if x>0)/len(excess_vals)*100,2) if excess_vals else None
    sr=round(s/len(arr)*100,2)
    evaluation_success_rate=round(es/len(arr)*100,2)
    return {'samples':len(arr),'success':s,'fail':f,'timeout':t,'success_rate_pct':sr,'evaluation_success':es,'evaluation_positive':epos,'evaluation_neutral':eneutral,'evaluation_negative':eneg,'evaluation_success_rate_pct':evaluation_success_rate,'avg_final_return_pct':avg,'avg_excess_return_pct':avg_excess,'excess_win_rate_pct':excess_win}



def score_bucket(score):
    try:
        v=float(score or 0)
    except Exception:
        return 'score_unknown'
    if v >= 80: return 'score_80_plus'
    if v >= 70: return 'score_70_79'
    if v >= 60: return 'score_60_69'
    return 'score_below_60'


def drawdown_bucket(drawdown):
    if drawdown is None:
        return 'drawdown_unknown'
    try:
        v=float(drawdown)
    except Exception:
        return 'drawdown_unknown'
    if v <= -10: return 'drawdown_tail_10_plus'
    if v <= -6: return 'drawdown_6_10'
    if v <= -3: return 'drawdown_3_6'
    return 'drawdown_controlled_lt3'


def excess_bucket(excess):
    if excess is None:
        return 'excess_unknown'
    try:
        v=float(excess)
    except Exception:
        return 'excess_unknown'
    if v >= 3: return 'excess_strong_positive'
    if v > 0: return 'excess_mild_positive'
    if v <= -3: return 'excess_strong_negative'
    return 'excess_mild_negative'


def context_labels_for_item(x):
    # Fund/router decisions can only use context known at decision time.
    # Outcome-derived labels such as final excess/drawdown are diagnostics, not
    # pre-trade usage conditions, so keep them out of the fund_usage profile.
    return [
        f"market:{x.get('market') or market_of(x.get('symbol'))}",
        f"period:{str(x.get('cutoff') or '')[:4] or 'unknown'}",
        f"score:{score_bucket(x.get('score'))}",
    ]


def conditional_context_profile(arr, min_samples=20):
    groups={}
    for x in arr:
        for label in context_labels_for_item(x):
            groups.setdefault(label,[]).append(x)
    rows=[]
    for label, xs in groups.items():
        base=metric_block(xs)
        quality=validation_quality(xs,{}) if xs else {}
        eval_rate=base.get('evaluation_success_rate_pct')
        avg_excess=base.get('avg_excess_return_pct')
        p10=quality.get('p10_excess_return_pct')
        if len(xs) < min_samples:
            verdict='thin_sample'
            use='observe_only'
        elif (avg_excess is not None and avg_excess > 1 and (eval_rate or 0) >= 50 and (p10 is None or p10 > -8)):
            verdict='favorable_context'
            use='prefer_or_allow'
        elif (avg_excess is not None and avg_excess <= -1.5) or (p10 is not None and p10 <= -10) or (eval_rate is not None and eval_rate < 38):
            verdict='unfavorable_context'
            use='avoid_or_downweight'
        else:
            verdict='mixed_context'
            use='small_size_or_neutral'
        rows.append({
            'context': label,
            'samples': len(xs),
            'evaluation_success_rate_pct': eval_rate,
            'avg_excess_return_pct': avg_excess,
            'excess_win_rate_pct': base.get('excess_win_rate_pct'),
            'p10_excess_return_pct': p10,
            'p25_excess_return_pct': quality.get('p25_excess_return_pct'),
            'expected_excess_value_pct': quality.get('expected_excess_value_pct'),
            'quality_flags': quality.get('quality_flags') or [],
            'verdict': verdict,
            'fund_usage_hint': use,
        })
    rows=sorted(rows, key=lambda r:(r['samples']>=min_samples, r.get('expected_excess_value_pct') if r.get('expected_excess_value_pct') is not None else -999, r.get('avg_excess_return_pct') if r.get('avg_excess_return_pct') is not None else -999, r['samples']), reverse=True)
    favorable=[r for r in rows if r['verdict']=='favorable_context']
    unfavorable=sorted([r for r in rows if r['verdict']=='unfavorable_context'], key=lambda r:(r.get('expected_excess_value_pct') if r.get('expected_excess_value_pct') is not None else 999, r.get('avg_excess_return_pct') if r.get('avg_excess_return_pct') is not None else 999, -r['samples']))
    return {
        'purpose': 'conditional_strategy_fit_not_universal_magic_strategy',
        'minimum_samples': min_samples,
        'contexts': rows,
        'favorable_contexts': favorable[:8],
        'unfavorable_contexts': unfavorable[:8],
        'fund_usage_policy': 'funds should prefer favorable contexts, downweight/avoid unfavorable contexts, and treat thin samples as research-only',
    }



def clamp_score(value):
    try:
        return int(round(max(0, min(100, float(value)))))
    except Exception:
        return 0


def strategy_role_profile(logic, arr, base=None, quality=None, context_profile=None, committee_fill_summary=None, min_samples=60):
    base = base or metric_block(arr)
    quality = quality or (validation_quality(arr, {}) if arr else {'quality_score': 0, 'quality_flags': ['no_samples']})
    context_profile = context_profile or conditional_context_profile(arr)
    committee_fill_summary = committee_fill_summary or summarize_committee_fill_profiles(arr)

    samples = int(base.get('samples') or len(arr) or 0)
    avg_excess = base.get('avg_excess_return_pct')
    excess_win = base.get('excess_win_rate_pct')
    eval_rate = base.get('evaluation_success_rate_pct')
    expected_ev = quality.get('expected_excess_value_pct')
    p10 = quality.get('p10_excess_return_pct')
    p25 = quality.get('p25_excess_return_pct')
    qscore = quality.get('quality_score') or 0
    eval_lb = quality.get('evaluation_success_wilson_low_pct') or 0
    positive_periods = quality.get('positive_periods') or 0
    tested_periods = quality.get('tested_periods') or 0
    fail_rate = quality.get('fail_rate_pct') or 0
    timeout_rate = quality.get('timeout_rate_pct') or 0
    flags = set(quality.get('quality_flags') or [])

    aggressive = committee_fill_summary.get('aggressive') or {}
    conservative = committee_fill_summary.get('conservative') or {}
    neutral = committee_fill_summary.get('neutral') or {}
    ag_ex = aggressive.get('avg_excess_return_pct')
    co_ex = conservative.get('avg_excess_return_pct')
    ne_ex = neutral.get('avg_excess_return_pct')
    fill_spread = None
    if ag_ex is not None and co_ex is not None:
        fill_spread = round((ag_ex or 0) - (co_ex or 0), 2)

    favorable_contexts = context_profile.get('favorable_contexts') or []
    unfavorable_contexts = context_profile.get('unfavorable_contexts') or []
    preferred_contexts = [x.get('context') for x in favorable_contexts if x.get('context')][:8]
    avoid_contexts = [x.get('context') for x in unfavorable_contexts if x.get('context')][:8]

    return_edge = clamp_score(50 + (avg_excess or 0) * 10 + (expected_ev or 0) * 8 + ((excess_win or 50) - 50) * 0.5)
    confidence = clamp_score(min(35, samples / 3) + eval_lb * 0.9 + min(20, positive_periods * 8) - (18 if samples < min_samples else 0))
    tail_safety = clamp_score(70 + (p10 or -10) * 3 + (p25 or -5) * 2 - fail_rate * 0.4 - (12 if 'left_tail_excess_risk' in flags else 0))
    context_fit = clamp_score(45 + len(favorable_contexts) * 12 - len(unfavorable_contexts) * 8 + max(-20, min(20, (positive_periods - max(0, tested_periods - positive_periods)) * 5)))
    execution_reliability = clamp_score(75 - max(0, fill_spread or 0) * 5 - timeout_rate * 0.4 + ((ne_ex or 0) * 3))
    overheat_avoidance = clamp_score(55 + (p10 or -6) * 2 + (p25 or -3) * 2 - (10 if fill_spread and fill_spread > 5 else 0))
    consistency = clamp_score(qscore * 0.55 + min(30, samples / 4) + min(15, positive_periods * 5) - (18 if 'period_instability' in flags else 0) - (16 if 'symbol_concentration_risk' in flags else 0))

    trust_axes = {
        'return_edge': return_edge,
        'confidence': confidence,
        'tail_safety': tail_safety,
        'regime_fit': context_fit,
        'execution_reliability': execution_reliability,
        'overheat_avoidance': overheat_avoidance,
        'consistency': consistency,
    }

    labels = []
    if samples < min_samples or 'low_sample_size' in flags:
        labels.append('research_only')
    if return_edge >= 65 and confidence >= 55:
        labels.append('return_edge_candidate')
    elif return_edge < 45:
        labels.append('weak_excess')
    if tail_safety >= 68 and confidence >= 50:
        labels.append('low_return_safe' if return_edge < 60 else 'risk_adjusted_candidate')
    elif tail_safety < 45:
        labels.append('left_tail_risk')
    if overheat_avoidance >= 68 and tail_safety >= 55:
        labels.append('hot_market_avoidance')
    if context_fit >= 62 and preferred_contexts:
        labels.append('conditional_context_fit')
    if context_fit < 42 or avoid_contexts:
        labels.append('context_sensitive_avoid')
    if execution_reliability < 50:
        labels.append('execution_model_sensitive')
    if consistency < 48:
        labels.append('unstable_or_concentrated')
    if not labels:
        labels.append('neutral_watch')

    if 'research_only' in labels:
        best_use = 'validation_priority_only'
        fund_usage_hint = 'do_not_promote; collect more samples and context evidence'
    elif 'left_tail_risk' in labels or 'unstable_or_concentrated' in labels:
        best_use = 'avoid_or_small_research_weight'
        fund_usage_hint = 'downweight unless fund explicitly accepts tail/context risk'
    elif 'low_return_safe' in labels or 'hot_market_avoidance' in labels:
        best_use = 'defensive_or_risk_control_sleeve'
        fund_usage_hint = 'allow for defensive funds or weak/overheated regimes with smaller return expectations'
    elif 'return_edge_candidate' in labels and 'conditional_context_fit' in labels:
        best_use = 'context_gated_alpha_sleeve'
        fund_usage_hint = 'prefer only in listed favorable contexts; avoid outside them'
    elif 'return_edge_candidate' in labels:
        best_use = 'alpha_candidate'
        fund_usage_hint = 'allow for growth/aggressive funds subject to risk gates'
    else:
        best_use = 'watch_or_small_size'
        fund_usage_hint = 'neutral weight only; wait for clearer edge or safety role'

    evidence = {
        'samples': samples,
        'avg_excess_return_pct': avg_excess,
        'expected_excess_value_pct': expected_ev,
        'excess_win_rate_pct': excess_win,
        'evaluation_success_rate_pct': eval_rate,
        'evaluation_success_wilson_low_pct': eval_lb,
        'p10_excess_return_pct': p10,
        'p25_excess_return_pct': p25,
        'fail_rate_pct': fail_rate,
        'timeout_rate_pct': timeout_rate,
        'fill_model_avg_excess': {
            'aggressive': ag_ex,
            'neutral': ne_ex,
            'conservative': co_ex,
            'aggressive_minus_conservative_pct': fill_spread,
        },
        'quality_flags': sorted(flags),
    }

    return {
        'logic': logic,
        'purpose': 'semantic_strategy_role_and_trust_profile_for_fund_selection',
        'role_labels': labels,
        'best_use': best_use,
        'trust_axes': trust_axes,
        'preferred_contexts': preferred_contexts,
        'avoid_contexts': avoid_contexts,
        'fund_usage_hint': fund_usage_hint,
        'evidence': evidence,
    }



def horizon_bucket(days, result=None):
    if result == 'timeout' or days is None:
        return 'timeout'
    try:
        d=int(days)
    except Exception:
        return 'unknown'
    if d <= 5: return 'd01_05'
    if d <= 10: return 'd06_10'
    if d <= 20: return 'd11_20'
    if d <= 40: return 'd21_40'
    return 'd41_plus'


def horizon_metric_block(arr):
    buckets={}
    for x in arr:
        b=horizon_bucket(x.get('days_to_event'), x.get('result'))
        buckets.setdefault(b,[]).append(x)
    order=['d01_05','d06_10','d11_20','d21_40','d41_plus','timeout','unknown']
    return {b: metric_block(buckets[b]) for b in order if b in buckets}


def committee_profile_items(arr, profile):
    out=[]
    for x in arr:
        prof=(x.get('committee_fill_profiles') or {}).get(profile) or {}
        if not prof:
            continue
        out.append({**x,
            'result': prof.get('result'),
            'days_to_event': prof.get('days_to_event'),
            'final_return_pct': prof.get('final_return_pct'),
            'excess_return_pct': prof.get('excess_return_pct'),
            'max_upside_pct': prof.get('max_upside_pct'),
            'max_drawdown_pct': prof.get('max_drawdown_pct'),
            'exit_price': prof.get('exit_price'),
            'exit_reason': prof.get('exit_reason'),
            'fill_model': prof.get('fill_model'),
        })
    return out


def summarize_committee_fill_profiles(arr):
    profiles={}
    for profile, label in [('aggressive','목표가 터치 우선'),('neutral','현재 기준/종가 판정'),('conservative','손절 터치 우선')]:
        parr=committee_profile_items(arr, profile)
        base=metric_block(parr)
        quality=validation_quality(parr, {}) if parr else {'quality_score':0,'quality_grade':'low','quality_flags':['no_samples']}
        profiles[profile]={
            **base,
            'label': label,
            'fill_model': (parr[0].get('fill_model') if parr else None),
            'horizon_buckets': horizon_metric_block(parr),
            'quality': quality,
        }
    if profiles.get('aggressive',{}).get('avg_excess_return_pct') is not None and profiles.get('conservative',{}).get('avg_excess_return_pct') is not None:
        profiles['spread']={
            'aggressive_minus_conservative_avg_excess_pct': round((profiles['aggressive'].get('avg_excess_return_pct') or 0) - (profiles['conservative'].get('avg_excess_return_pct') or 0), 2),
            'aggressive_minus_neutral_avg_excess_pct': round((profiles['aggressive'].get('avg_excess_return_pct') or 0) - (profiles['neutral'].get('avg_excess_return_pct') or 0), 2),
            'neutral_minus_conservative_avg_excess_pct': round((profiles['neutral'].get('avg_excess_return_pct') or 0) - (profiles['conservative'].get('avg_excess_return_pct') or 0), 2),
        }
    return profiles


def target_adjusted_items(arr):
    out=[]
    for x in arr:
        prof=x.get('target_adjusted_profile') or {}
        if not prof:
            continue
        out.append({**x,
            'target': prof.get('target'),
            'result': prof.get('result'),
            'days_to_event': prof.get('days_to_event'),
            'final_return_pct': prof.get('final_return_pct'),
            'excess_return_pct': prof.get('excess_return_pct'),
            'max_upside_pct': prof.get('max_upside_pct'),
            'max_drawdown_pct': prof.get('max_drawdown_pct'),
            'exit_price': prof.get('exit_price'),
            'exit_reason': prof.get('exit_reason'),
            'fill_model': prof.get('fill_model'),
        })
    return out


def summarize_target_adjusted(arr):
    adj=target_adjusted_items(arr)
    if not adj:
        return {'samples':0,'verdict':'no_target_adjustments','accepted':False,'acceptance_reason':'no_adjusted_samples'}
    base=metric_block(adj)
    quality=validation_quality(adj,{})
    original=metric_block(arr)
    original_quality=validation_quality(arr,{}) if arr else {}
    delta={
        'success_rate_delta_pct': round((base.get('success_rate_pct') or 0)-(original.get('success_rate_pct') or 0),2),
        'evaluation_success_rate_delta_pct': round((base.get('evaluation_success_rate_pct') or 0)-(original.get('evaluation_success_rate_pct') or 0),2),
        'avg_excess_return_delta_pct': round((base.get('avg_excess_return_pct') or 0)-(original.get('avg_excess_return_pct') or 0),2) if base.get('avg_excess_return_pct') is not None and original.get('avg_excess_return_pct') is not None else None,
        'expected_excess_value_delta_pct': round((quality.get('expected_excess_value_pct') or 0)-(original_quality.get('expected_excess_value_pct') or 0),2) if quality.get('expected_excess_value_pct') is not None and original_quality.get('expected_excess_value_pct') is not None else None,
    }
    hard_reasons=[]
    sample_reasons=[]
    min_samples=30
    if len(adj) < min_samples: sample_reasons.append('low_adjusted_sample_size')
    if delta.get('avg_excess_return_delta_pct') is None or delta.get('avg_excess_return_delta_pct') < 0: hard_reasons.append('adjusted_avg_excess_not_improved')
    if delta.get('expected_excess_value_delta_pct') is None or delta.get('expected_excess_value_delta_pct') < 0: hard_reasons.append('adjusted_ev_not_improved')
    if delta.get('evaluation_success_rate_delta_pct') < 0: hard_reasons.append('adjusted_evaluation_success_worse')
    if base.get('avg_excess_return_pct') is None or base.get('avg_excess_return_pct') <= 0: hard_reasons.append('adjusted_avg_excess_not_positive')
    accepted=(not hard_reasons and not sample_reasons)
    if accepted:
        status='accepted'; reason='accepted_improves_excess_ev_and_evaluation'
    elif not hard_reasons and sample_reasons:
        status='provisional_more_samples_needed'; reason=';'.join(sample_reasons)
    else:
        status='rejected'; reason=';'.join(sample_reasons+hard_reasons)
    return {**base,'quality':quality,'original_quality':original_quality,'delta_vs_original':delta,'accepted':accepted,'acceptance_status':status,'minimum_acceptance_samples':min_samples,'samples_needed_for_acceptance':max(0,min_samples-len(adj)),'verdict':status,'acceptance_reason':reason}


def audited_market_reality(items):
    out={}
    audited=[x for x in items if x.get('status')=='audited']
    for market in ['KR','US']:
        market_items=[x for x in audited if (x.get('market') or market_of(x.get('symbol'))) == market]
        block=metric_block(market_items)
        excess_vals=sorted(float(x.get('excess_return_pct')) for x in market_items if x.get('excess_return_pct') is not None)
        benchmark_vals=[float(x.get('benchmark_return_pct')) for x in market_items if x.get('benchmark_return_pct') is not None]
        block.update({
            'audited_count': len(market_items),
            'candidate_buy_zone_count': sum(1 for x in market_items if x.get('action')=='candidate_buy_zone'),
            'avg_benchmark_return_pct': round(sum(benchmark_vals)/len(benchmark_vals),2) if benchmark_vals else None,
            'p10_excess_return_pct': round(excess_vals[max(0,int(len(excess_vals)*0.1)-1)],2) if excess_vals else None,
            'interpretation': 'full_audit_all_watch_and_candidate_rows_not_selected_recommendation_subset',
        })
        out[market]=block
    return out

def summarize_by_market(logic_summaries, items):
    out={}
    audited=[x for x in items if x.get('status')=='audited' and x.get('action')=='candidate_buy_zone']
    for market in ['KR','US']:
        market_items=[x for x in audited if (x.get('market') or market_of(x.get('symbol'))) == market]
        by_logic={}
        for logic in LOGICS:
            arr=[x for x in market_items if x.get('logic')==logic]
            if not arr:
                continue
            by_period={}
            for x in arr:
                by_period.setdefault(str(x.get('cutoff',''))[:4],[]).append(x)
            period_stats={k:{'samples':len(v),'avg_excess_return_pct':round(sum((z.get('excess_return_pct') or 0) for z in v)/len(v),2)} for k,v in by_period.items()}
            base=metric_block(arr); quality=validation_quality(arr,period_stats)
            by_logic[logic]={**base, **quality, 'target_adjusted_summary': summarize_target_adjusted(arr)}
        best=None
        if by_logic:
            best=sorted(by_logic.items(), key=lambda kv:(kv[1].get('expected_excess_value_pct') or -999, kv[1].get('avg_excess_return_pct') or -999, kv[1].get('evaluation_success_rate_pct') or -999), reverse=True)[0]
        out[market]={'samples':len(market_items),'best_logic':best[0] if best else None,'best':best[1] if best else None,'by_logic':by_logic}
    return out


def summarize(items):
    by={}
    opportunities={}
    for logic in LOGICS:
        by[logic]=[]
        opportunities[logic]=0
    for x in items:
        if x.get('status')=='audited':
            opportunities[x['logic']]=opportunities.get(x['logic'],0)+1
        if x.get('status')=='audited' and x.get('action')=='candidate_buy_zone':
            by.setdefault(x['logic'],[]).append(x)
    summaries={}
    for logic,arr in by.items():
        opportunity_count=opportunities.get(logic,0)
        signal_rate=round(len(arr)/opportunity_count*100,2) if opportunity_count else 0
        signal_flags=[]
        if opportunity_count >= 60 and len(arr) == 0:
            signal_flags.append('no_candidate_buy_signals')
        elif opportunity_count >= 80 and signal_rate < 2:
            signal_flags.append('extremely_low_signal_rate')
        elif opportunity_count >= 80 and signal_rate < 5:
            signal_flags.append('low_signal_rate')
        if not arr:
            summaries[logic]={'samples':0,'opportunity_count':opportunity_count,'signal_rate_pct':signal_rate,'quality_flags':signal_flags,'verdict':'insufficient_samples'}; continue
        base=metric_block(arr); s=base['success']; f=base['fail']; t=base['timeout']; avg=base['avg_final_return_pct']; avg_excess=base['avg_excess_return_pct']; excess_win=base['excess_win_rate_pct']; sr=base['success_rate_pct']
        by_symbol={}; by_period={}
        for x in arr:
            by_symbol.setdefault(x['symbol'],[]).append(x)
            by_period.setdefault(x['cutoff'][:4],[]).append(x)
        symbol_stats={k:{'samples':len(v),'success_rate_pct':round(sum(1 for x in v if x['result']=='success')/len(v)*100,2),'evaluation_success_rate_pct':round(sum(1 for x in v if x.get('evaluation_success'))/len(v)*100,2),'avg_excess_return_pct':round(sum((x.get('excess_return_pct') or 0) for x in v)/len(v),2)} for k,v in by_symbol.items()}
        period_stats={k:{'samples':len(v),'success_rate_pct':round(sum(1 for x in v if x['result']=='success')/len(v)*100,2),'evaluation_success_rate_pct':round(sum(1 for x in v if x.get('evaluation_success'))/len(v)*100,2),'avg_excess_return_pct':round(sum((x.get('excess_return_pct') or 0) for x in v)/len(v),2)} for k,v in by_period.items()}
        concentration=round(max((len(v) for v in by_symbol.values()), default=0)/len(arr)*100,2)
        symbol_count=len(by_symbol)
        diversity_score=round(min(100, symbol_count*8) - max(0, concentration-25), 2)
        stable_periods=sum(1 for v in period_stats.values() if v['samples']>=5 and v['avg_excess_return_pct']>0)
        quality=validation_quality(arr, period_stats)
        quality['quality_flags'].extend(signal_flags)
        recent_cutoffs=sorted({x.get('cutoff') for x in arr if x.get('cutoff')})[-4:]
        recent_arr=[x for x in arr if x.get('cutoff') in recent_cutoffs]
        recent=metric_block(recent_arr)
        recent_quality=validation_quality(recent_arr, {}) if recent_arr else {'quality_score':0,'quality_grade':'low','quality_flags':['no_recent_samples']}
        recent_delta=round((recent.get('avg_excess_return_pct') or 0) - (base.get('avg_excess_return_pct') or 0), 2) if recent.get('avg_excess_return_pct') is not None and base.get('avg_excess_return_pct') is not None else None
        expected_value=quality.get('expected_excess_value_pct')
        aggregate_quality_score=round((quality['quality_score'] or 0)*0.35 + max(-20,min(40,(expected_value or 0)*10))*0.25 + max(0,min(100, diversity_score))*0.15 + max(0,min(100,(quality.get('evaluation_success_wilson_low_pct') or 0)*1.7))*0.20 + max(0,min(100,(quality.get('success_rate_wilson_low_pct') or 0)*1.7))*0.05, 2)
        if concentration > 45:
            quality['quality_flags'].append('symbol_concentration_risk')
        if expected_value is not None and expected_value <= 0:
            quality['quality_flags'].append('negative_expected_excess_value')
        overselective_flags={'no_candidate_buy_signals','extremely_low_signal_rate','low_signal_rate'}
        pass_quality=aggregate_quality_score>=68 and not {'negative_expected_excess_value','period_instability','symbol_concentration_risk'}.union(overselective_flags).intersection(set(quality['quality_flags']))
        eval_sr=quality.get('evaluation_success_rate_pct') or 0
        verdict='pass' if len(arr)>=60 and signal_rate>=5 and eval_sr>=45 and (avg_excess is not None and avg_excess>0) and concentration<=45 and stable_periods>=2 and pass_quality else ('weak' if len(arr)>=10 else 'insufficient_samples')
        if recent_delta is not None and recent_delta < -4 and verdict == 'pass':
            verdict='weak'
            quality['quality_flags'].append('recent_decay')
        long_term={**base,'opportunity_count':opportunity_count,'signal_rate_pct':signal_rate,'max_symbol_concentration_pct':concentration,'symbol_count':symbol_count,'diversity_score':diversity_score,'stable_positive_periods':stable_periods,'aggregate_quality_score':aggregate_quality_score, **quality}
        regime_by_period=period_stats
        committee_fill_summary=summarize_committee_fill_profiles(arr)
        horizon_profile=horizon_metric_block(arr)
        target_adjusted_summary=summarize_target_adjusted(arr)
        context_profile=conditional_context_profile(arr)
        role_profile=strategy_role_profile(logic, arr, base=base, quality=quality, context_profile=context_profile, committee_fill_summary=committee_fill_summary)
        summaries[logic]={**base,'opportunity_count':opportunity_count,'signal_rate_pct':signal_rate,'max_symbol_concentration_pct':concentration,'symbol_count':symbol_count,'diversity_score':diversity_score,'stable_positive_periods':stable_periods,'aggregate_quality_score':aggregate_quality_score,'verdict':verdict, **quality, 'recent_vs_long_term_excess_delta_pct':recent_delta,'recent_quality':recent_quality,'long_term':long_term,'recent':recent,'regime_by_period':regime_by_period,'recent_cutoffs':recent_cutoffs,'horizon_profile':horizon_profile,'committee_fill_summary':committee_fill_summary,'target_adjusted_summary':target_adjusted_summary,'conditional_context_profile':context_profile,'strategy_role_profile':role_profile,'by_symbol':symbol_stats,'by_period':period_stats}
    best=sorted(summaries.items(), key=lambda kv:(kv[1].get('verdict')=='pass', kv[1].get('aggregate_quality_score') or -999, kv[1].get('expected_excess_value_pct') or -999, kv[1].get('avg_excess_return_pct') or -999), reverse=True)[0]
    committee_overall=summarize_committee_fill_profiles([x for x in items if x.get('status')=='audited' and x.get('action')=='candidate_buy_zone'])
    by_market=summarize_by_market(summaries, items)
    target_adjusted_overall=summarize_target_adjusted([x for x in items if x.get('status')=='audited' and x.get('action')=='candidate_buy_zone'])
    overall_items=[x for x in items if x.get('status')=='audited' and x.get('action')=='candidate_buy_zone']
    conditional_context_overall=conditional_context_profile(overall_items)
    overall_role_profile=strategy_role_profile('overall', overall_items, context_profile=conditional_context_overall, committee_fill_summary=committee_overall)
    summary={'best_logic':best[0], 'best':best[1], 'conditional_context_profile':conditional_context_overall, 'strategy_role_profile':overall_role_profile, 'market_reality':audited_market_reality(items), 'best_by_market':{m:v.get('best_logic') for m,v in by_market.items()}, 'by_market':by_market, 'committee_fill_summary':committee_overall, 'target_adjusted_summary':target_adjusted_overall, 'by_logic':summaries}
    summary['strategy_trust_improvement_plan']=strategy_trust_improvement_plan(summary)
    return summary

def main():
    ap=argparse.ArgumentParser(description='Audit target-price recommendation logic by historical cutoff outcomes')
    ap.add_argument('--symbols')
    ap.add_argument('--cutoffs')
    ap.add_argument('--monthly-from', default='2023-01-01')
    ap.add_argument('--monthly-step', type=int, default=1)
    ap.add_argument('--cutoff-mode', choices=['monthly','mixed'], default='mixed')
    ap.add_argument('--random-cutoffs', type=int, default=24)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--recent-cap-per-quarter', type=int, default=4)
    ap.add_argument('--horizon-days', type=int, default=60)
    ap.add_argument('--logics', default=','.join(LOGICS.keys()))
    ap.add_argument('--fill-model', choices=['close_only','optimistic_limit_target','bracket_intraday_conservative'], default='close_only')
    ap.add_argument('--output', default='/tmp/recommendation_audit_latest.json')
    args=ap.parse_args(); init_db(); conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    symbols=[s.strip().upper() for s in args.symbols.split(',')] if args.symbols else [m['symbol'] for m in list_universe_members(status='active')]
    cutoff_meta={'mode':'explicit'}
    if args.cutoffs:
        cutoffs=[c.strip() for c in args.cutoffs.split(',') if c.strip()]
    elif args.cutoff_mode=='mixed':
        cutoffs, cutoff_meta=mixed_cutoffs(conn,symbols,args.monthly_from,monthly_step=args.monthly_step,random_count=args.random_cutoffs,seed=args.seed,recent_cap_per_quarter=args.recent_cap_per_quarter,horizon=args.horizon_days)
    else:
        cutoffs=month_cutoffs(args.monthly_from, step=args.monthly_step); cutoff_meta={'mode':'monthly','monthly_step':args.monthly_step,'cutoff_count':len(cutoffs)}
    logics=[l.strip() for l in args.logics.split(',') if l.strip() in LOGICS]
    target_adjustments=load_target_adjustments()
    items=[]
    for s in symbols: items.extend(audit_symbol(conn,s,cutoffs,args.horizon_days,logics,fill_model=args.fill_model,target_adjustments=target_adjustments))
    conn.close(); packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'recommendation_logic_audit_multi','real_trading':False,'fill_model':args.fill_model,'execution_assumption':'primary summary uses neutral close-only; committee_fill_profiles store aggressive target-touch-first and conservative stop-touch-first variants','evaluation_policy':'Execution result remains target/stop/timeout based; evaluation_result adds positive/neutral/negative judgement using excess return, absolute return, and drawdown. Strategy quality emphasizes evaluation_success while preserving execution success as a secondary metric.','horizon_policy':'Event timing is bucketed separately from aggregate win/loss so short-target fills, late fills, and timeouts are visible by strategy and committee profile.','market_policy':'KR and US audit summaries are split so a strategy can be evaluated/routed by market instead of averaged across markets.','target_adjustment_policy':'When strategy_success_optimizer proposes target_adjustments, auditor computes a separate target_adjusted_profile and summary. This does not replace original target/result; it compares adjusted-target success, evaluation success, excess return, and EV against the original. A target adjustment is accepted only when adjusted avg excess, expected excess value, and evaluation success do not degrade and sample size is sufficient.','cutoffs':cutoffs,'cutoff_meta':cutoff_meta,'horizon_days':args.horizon_days,'logics':logics,'summary':summarize(items),'items':items}
    candidate_audited=[x for x in items if x.get('status')=='audited' and x.get('action')=='candidate_buy_zone']
    all_audited=[x for x in items if x.get('status')=='audited']
    preview_source=candidate_audited if candidate_audited else all_audited
    preview_filter='candidate_buy_zone' if candidate_audited else 'all_audited_no_candidate_buy_zone'
    preview_source.sort(key=lambda x:(str(x.get('cutoff') or ''), {'success':3,'timeout':2,'fail':1}.get(x.get('result'),0), float(x.get('excess_return_pct') or 0)), reverse=True)
    seen=set(); preview=[]
    for x in preview_source:
        key=(x.get('symbol'), x.get('cutoff'), x.get('result'), x.get('action'))
        if key in seen: continue
        seen.add(key); preview.append(x)
        if len(preview)>=200: break
    full_path=Path(args.output).with_name(Path(args.output).stem.replace('_latest','_full_latest') + Path(args.output).suffix)
    write_json_shared(full_path, packet)
    action_counts={}
    for x in all_audited:
        action_counts[x.get('action') or 'unknown']=action_counts.get(x.get('action') or 'unknown',0)+1
    packet['items_total_raw']=len(items); packet['items_total_audited']=len(all_audited); packet['items_total_candidate_buy_zone']=len(candidate_audited); packet['items_total_filtered']=len(preview_source); packet['items_preview_filter']=preview_filter; packet['action_counts']=action_counts; packet['items_limit']=200; packet['items_deduped']=True; packet['latest_cutoff']=preview[0].get('cutoff') if preview else None; packet['full_output']=str(full_path); packet['items']=preview
    best=packet.get('summary',{}).get('best') or {}
    quality_flags=best.get('quality_flags') or []
    no_candidate_buy_zone=bool(all_audited and not candidate_audited)
    if best.get('verdict') == 'pass':
        status='ok'
    elif no_candidate_buy_zone:
        status='ok'
    else:
        status='degraded'
    warnings=[f'best_logic_quality:{flag}' for flag in quality_flags]
    if no_candidate_buy_zone:
        warnings.append('no_candidate_buy_zone_signals; audit ran on watch-only opportunities')
    improvement_plan=(packet.get('summary') or {}).get('strategy_trust_improvement_plan') or {}
    next_actions=[x.get('next_experiment') for x in (improvement_plan.get('top_actions') or [])[:3]]
    if no_candidate_buy_zone:
        next_actions.append('Recommendation gates produced watch-only signals; treat as safety-gate state, not audit agent failure.')
    if status!='ok' and not next_actions:
        next_actions.append('Treat weak/degraded audit as caution in recommendation scoring.')
    attach_contract(packet, 'recommendation_auditor', status=status, inputs={'symbol_count': len(symbols), 'logic_count': len(logics), 'horizon_days': args.horizon_days, 'cutoff_count': len(cutoffs), 'fill_model': args.fill_model}, outputs={'items_total_raw': len(items), 'items_total_audited': len(all_audited), 'items_total_candidate_buy_zone': len(candidate_audited), 'items_total_filtered': len(preview_source), 'preview_filter': preview_filter, 'preview_count': len(preview), 'best_logic': packet.get('summary',{}).get('best_logic'), 'strategy_trust_improvement_plan': improvement_plan}, metrics={'best_quality_score': best.get('quality_score'), 'best_quality_grade': best.get('quality_grade'), 'best_samples': best.get('samples'), 'best_avg_excess_return_pct': best.get('avg_excess_return_pct'), 'best_wilson_low_pct': best.get('success_rate_wilson_low_pct'), 'first_three_label_quality_points': improvement_plan.get('first_three_label_quality_points'), 'projected_quality_if_top_three_resolved': improvement_plan.get('projected_quality_if_top_three_resolved')}, warnings=warnings, next_actions=next_actions)
    write_latest_artifact(args.output, packet); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
