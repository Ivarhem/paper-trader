#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,math,random,sqlite3,sys,time
from datetime import date as date_cls, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import (
    daily_market_context_coverage,
    init_db,
    latest_financial_quality,
    list_daily_market_context,
    save_latest_artifact,
)
from tools.agents.lib.agent_contract import attach_contract
from tools.agents.lib.fx import load_usdkrw_series, fx_for_date, normalize_trade_price, price_to_krw, fx_rate_for_symbol
from tools.agents.lib.runtime import write_json, read_json

DEFAULT_CAPITAL=10_000_000.0
DEFAULT_REPLAY_START_DATE='2023-01-01'
REPLAY_BUSINESS_TZ='Asia/Seoul'
ARCHETYPES=[
 {'style':'trend','cash_buffer':0.20,'max_positions':6,'risk_per_trade':0.13,'score_min':58,'target_pct':8,'stop_pct':-6},
 {'style':'breakout','cash_buffer':0.12,'max_positions':5,'risk_per_trade':0.15,'score_min':60,'target_pct':10,'stop_pct':-7},
 {'style':'balanced','cash_buffer':0.35,'max_positions':7,'risk_per_trade':0.09,'score_min':55,'target_pct':6,'stop_pct':-5},
 {'style':'defensive','cash_buffer':0.55,'max_positions':4,'risk_per_trade':0.07,'score_min':62,'target_pct':5,'stop_pct':-4},
 {'style':'mean_reversion','cash_buffer':0.25,'max_positions':6,'risk_per_trade':0.11,'score_min':57,'target_pct':6,'stop_pct':-6},
 {'style':'volume_surge','cash_buffer':0.22,'max_positions':6,'risk_per_trade':0.12,'score_min':59,'target_pct':8,'stop_pct':-6},
 {'style':'value','cash_buffer':0.32,'max_positions':7,'risk_per_trade':0.085,'score_min':56,'target_pct':7,'stop_pct':-6},
 {'style':'quality','cash_buffer':0.28,'max_positions':6,'risk_per_trade':0.095,'score_min':58,'target_pct':8,'stop_pct':-5},
 {'style':'garp','cash_buffer':0.24,'max_positions':6,'risk_per_trade':0.10,'score_min':58,'target_pct':9,'stop_pct':-6},
 {'style':'dividend','cash_buffer':0.45,'max_positions':6,'risk_per_trade':0.075,'score_min':57,'target_pct':5,'stop_pct':-4},
 {'style':'financial_safety','cash_buffer':0.50,'max_positions':5,'risk_per_trade':0.07,'score_min':60,'target_pct':5,'stop_pct':-4},
]

# Paper replay friction model. Values are intentionally simple/conservative and
# configurable through CLI args in basis points / tax percent. US capital-gains tax
# is applied per profitable sell as a conservative proxy; real tax treatment may
# differ by account/country/year and this remains paper-only research.
DEFAULT_KR_COMMISSION_BPS=1.5
DEFAULT_KR_SELL_TAX_BPS=15.0
DEFAULT_US_COMMISSION_BPS=5.0
DEFAULT_US_SELL_FEE_BPS=0.30
DEFAULT_US_GAIN_TAX_PCT=22.0
MIN_CASH_UNIT=100.0

def money_floor(v: float, unit: float = MIN_CASH_UNIT) -> float:
    return max(0.0, int(float(v) // unit) * unit)

def money_round(v: float, unit: float = MIN_CASH_UNIT) -> float:
    return round(float(v) / unit) * unit

def is_kr_symbol(sym: str) -> bool:
    return str(sym).endswith('.KS') or str(sym).endswith('.KQ')

def buy_fee(sym: str, notional: float, args) -> float:
    bps = args.kr_commission_bps if is_kr_symbol(sym) else args.us_commission_bps
    return max(0.0, notional * bps / 10000.0)

def sell_costs(sym: str, notional: float, gross_pnl: float, args) -> dict:
    if is_kr_symbol(sym):
        commission = notional * args.kr_commission_bps / 10000.0
        sell_tax = notional * args.kr_sell_tax_bps / 10000.0
        gain_tax = 0.0
    else:
        commission = notional * args.us_commission_bps / 10000.0
        sell_tax = notional * args.us_sell_fee_bps / 10000.0
        gain_tax = max(0.0, gross_pnl) * args.us_gain_tax_pct / 100.0
    total = max(0.0, commission + sell_tax + gain_tax)
    return {'commission':commission,'sell_tax':sell_tax,'gain_tax':gain_tax,'total':total}

def avg(xs): return sum(xs)/len(xs) if xs else None
def pct(a,b): return (a/b-1)*100 if b else 0.0

def num(v, default=None):
    try:
        if v is None: return default
        return float(v)
    except Exception:
        return default

def parse_date_arg(value: str | None, default: date_cls | None = None) -> date_cls | None:
    if not value:
        return default
    try:
        return date_cls.fromisoformat(str(value)[:10])
    except Exception:
        return default

def load_factor_snapshot(symbols):
    snapshot={}
    for sym in symbols:
        fq=None
        if str(sym).endswith(('.KS','.KQ')):
            try:
                fq=latest_financial_quality(sym)
            except Exception:
                fq=None
        val=(fq or {}).get('valuation_metrics') or {}
        debt=num((fq or {}).get('debt_ratio_pct'))
        op_margin=num((fq or {}).get('operating_margin_pct'))
        rev_growth=num((fq or {}).get('revenue_growth_pct'))
        op_growth=num((fq or {}).get('operating_income_growth_pct'))
        per=num(val.get('per')); pbr=num(val.get('pbr')); roe=num(val.get('roe_pct')); ev_ebitda=num(val.get('ev_ebitda')); div_yield=num(val.get('dividend_yield_pct'))
        value_score=0.0; quality_score=0.0; growth_score=0.0; yield_score=0.0; safety_score=0.0; tags=[]
        if per is not None:
            if 0 < per <= 8: value_score += 8; tags.append('low_per')
            elif per <= 12: value_score += 6
            elif per <= 18: value_score += 2
            elif per >= 35: value_score -= 5; tags.append('expensive_per')
        if pbr is not None:
            if pbr <= 0.8: value_score += 8; tags.append('low_pbr')
            elif pbr <= 1.2: value_score += 6
            elif pbr <= 1.8: value_score += 2
            elif pbr >= 4: value_score -= 4; tags.append('expensive_pbr')
        if ev_ebitda is not None:
            if ev_ebitda <= 6: value_score += 4
            elif ev_ebitda >= 20: value_score -= 3
        if roe is not None:
            if roe >= 18: quality_score += 9; tags.append('high_roe')
            elif roe >= 10: quality_score += 6
            elif roe < 0: quality_score -= 8; safety_score -= 6; tags.append('negative_roe')
        if op_margin is not None:
            if op_margin >= 12: quality_score += 5
            elif op_margin < 0: quality_score -= 7; safety_score -= 5; tags.append('negative_margin')
        if rev_growth is not None:
            if rev_growth >= 12: growth_score += 5; tags.append('revenue_growth')
            elif rev_growth < -15: growth_score -= 5; safety_score -= 3
        if op_growth is not None:
            if op_growth >= 15: growth_score += 4
            elif op_growth < -25: growth_score -= 5
        if debt is not None:
            if debt <= 80: safety_score += 6; tags.append('low_debt')
            elif debt <= 160: safety_score += 2
            elif debt >= 250: safety_score -= 8; tags.append('high_debt')
        if div_yield is not None:
            if div_yield >= 5: yield_score += 7; tags.append('high_yield')
            elif div_yield >= 2: yield_score += 4
            elif div_yield == 0: yield_score -= 2
        if value_score > 0 and quality_score < -2:
            value_score -= 5; tags.append('value_trap_risk')
        if growth_score > 0 and value_score >= 4 and quality_score >= 4:
            tags.append('garp_candidate')
        snapshot[sym]={'symbol':sym,'available':bool(fq or any(v is not None for v in (per,pbr,roe,ev_ebitda,div_yield))),'financial_quality':fq,'metrics':{'per':per,'pbr':pbr,'roe_pct':roe,'ev_ebitda':ev_ebitda,'dividend_yield_pct':div_yield,'debt_ratio_pct':debt,'operating_margin_pct':op_margin,'revenue_growth_pct':rev_growth,'operating_income_growth_pct':op_growth},'scores':{'value':round(max(-10,min(18,value_score)),2),'quality':round(max(-12,min(18,quality_score)),2),'growth':round(max(-10,min(12,growth_score)),2),'yield':round(max(-5,min(10,yield_score)),2),'financial_safety':round(max(-12,min(12,safety_score)),2)},'tags':tags,'policy':'cached_per_pbr_roe_ev_ebitda_dividend_debt_margin_growth_factor_snapshot'}
    return snapshot

def factor_adjustment_for_style(style, factor):
    if not factor or not factor.get('available'):
        return 0.0, []
    s=factor.get('scores') or {}; tags=factor.get('tags') or []
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
    if 'value_trap_risk' in tags and style in ('value','garp','dividend'):
        adj -= 4
    if num((factor.get('metrics') or {}).get('roe_pct')) is not None and num((factor.get('metrics') or {}).get('roe_pct')) < 0 and style in ('quality','financial_safety'):
        adj -= 4
    return round(max(-10,min(12,adj)),2), tags[:6]


def load_general_universe(conn, explicit_symbols: str = '', max_symbols: int = 0):
    if explicit_symbols:
        raw=[x.strip() for x in explicit_symbols.split(',') if x.strip()]
        filtered=[x for x in raw if is_kr_symbol(x)]
        return filtered,'cli_symbols_kr_only'
    # Canonical shared universe: fund does not own a separate seed.
    try:
        data=json.load(open('/tmp/common_universe_latest.json'))
        items=[x for x in (data.get('items') or []) if x.get('symbol') and x.get('tradable', True) and x.get('asset_type') != 'index']
        if items:
            kr=[]; us=[]
            for it in items:
                sym=it.get('symbol')
                row=conn.execute("select count(*) as n from price_bars where timeframe='1d' and symbol=?",(sym,)).fetchone()
                if not (row and row['n']>=80): continue
                (kr if str(sym).endswith(('.KS','.KQ')) else us).append(sym)
            # Phase 1 broad-market fund replay is KR-only. US broad-market analysis
            # remains a disabled placeholder until its scale/cost is handled.
            if not max_symbols or max_symbols <= 0:
                return kr,'common_universe_kr_all_us_pending'
            return kr[:max_symbols],'common_universe_kr_capped_us_pending'
    except Exception:
        pass
    # Fallback only for bootstrap/debug when common universe has not run yet.
    ordered=[]; sources=[]
    for path,label in [('/tmp/recommendations_latest.json','recommendations'),('/tmp/strategy_candidates_latest.json','strategy_candidates'),('/tmp/universe_curator_latest.json','universe_curator')]:
        try:
            data=json.load(open(path)); items=data.get('items') or data.get('candidates') or data.get('selected') or []
            for row in items:
                sym=row.get('symbol') if isinstance(row,dict) else None
                if sym and not str(sym).startswith('^') and sym not in ordered: ordered.append(sym)
            if items: sources.append(label)
        except Exception: pass
    filtered=[]
    for sym in ordered:
        if not is_kr_symbol(sym): continue
        row=conn.execute("select count(*) as n from price_bars where timeframe='1d' and symbol=?",(sym,)).fetchone()
        if row and row['n']>=80: filtered.append(sym)
        if max_symbols and len(filtered)>=max_symbols: break
    return filtered,'fallback_' + ('+'.join(dict.fromkeys(sources)) or 'empty')

def load_market_context_by_date(start_date: str, end_date: str) -> dict[str, dict]:
    rows=list_daily_market_context('KR', start_date=start_date, end_date=end_date)
    return {str(row.get('trade_date')): row for row in rows if row.get('trade_date')}

def market_context_adjustment(style: str, context: dict | None) -> tuple[float, list[str]]:
    if not context:
        return 0.0, []
    adj=0.0; notes=[]
    risk=num(context.get('risk_on_score'))
    volatility=num(context.get('volatility_score'))
    fx_pressure=num(context.get('fx_pressure_score'))
    breadth=num(context.get('market_breadth_pct'))
    if risk is not None:
        if risk >= 65 and style in ('trend','breakout','volume_surge','quality','garp'):
            adj += 1.5; notes.append('risk_on_context')
        elif risk <= 38:
            adj -= 2.0 if style in ('trend','breakout','volume_surge','garp') else 1.0
            notes.append('risk_off_context')
    if volatility is not None and volatility >= 60:
        adj -= 1.0 if style in ('defensive','dividend','financial_safety') else 1.5
        notes.append('high_volatility_context')
    if fx_pressure is not None and fx_pressure >= 58:
        adj -= 0.75
        notes.append('fx_pressure_context')
    if breadth is not None:
        if breadth >= 60 and style in ('trend','breakout','volume_surge'):
            adj += 0.75; notes.append('positive_breadth_context')
        elif breadth <= 40:
            adj -= 0.75; notes.append('weak_breadth_context')
    return round(max(-4.0,min(3.0,adj)),2), notes

STYLE_ORDER=[a['style'] for a in ARCHETYPES]
STYLE_DEFAULTS={a['style']:dict(a) for a in ARCHETYPES}

def apply_style_defaults(fund, style=None, reset_params=False):
    style=style or fund.get('style') or STYLE_ORDER[0]
    fund['style']=style
    if reset_params:
        for k,v in STYLE_DEFAULTS.get(style, {}).items():
            if k != 'style':
                fund[k]=v
    fund['pyramid_enabled']=True
    fund['average_down_enabled']=True
    fund['scale_out_enabled']=style in ('trend','balanced','volume_surge','breakout','quality','garp')
    fund['trailing_stop_enabled']=style in ('trend','breakout','volume_surge','quality','garp')
    fund['allowed_strategy_roles']=strategy_roles_for_style(style)
    # Rebalance buys are not count-limited.  They are naturally bounded by
    # cash buffer, per-trade risk, and max single-symbol exposure.
    fund['max_symbol_exposure_pct']=0.24
    fund['average_down_trigger_pct']=max(2.0, abs(float(fund.get('stop_pct') or -5))*0.45)
    fund['pyramid_trigger_pct']=max(1.0, float(fund.get('target_pct') or 6)*0.35)
    return fund

def seed_funds(n, capital):
    funds=[]
    for i in range(n):
        base=dict(ARCHETYPES[i%len(ARCHETYPES)]); rng=random.Random(3000+i)
        for k,span in [('cash_buffer',0.08),('risk_per_trade',0.035),('score_min',5),('target_pct',2),('stop_pct',1.5)]:
            base[k]=round(base[k]+rng.uniform(-span,span),3)
        base['cash_buffer']=min(max(base['cash_buffer'],0.05),0.75); base['risk_per_trade']=min(max(base['risk_per_trade'],0.03),0.22); base['stop_pct']=min(base['stop_pct'],-1.5)
        apply_style_defaults(base, base['style'])
        base.update({'id':f'price_fund_{i+1:03d}','generation':1,'age_days':0,'status':'active','cash':capital,'initial_capital':capital,'positions':{},'history':[],'trade_count':0,'realized_pnl':0.0,'total_costs':0.0})
        funds.append(base)
    return funds

def load_issue_context() -> dict:
    try:
        data=read_json('/tmp/next_trade_issue_context_latest.json')
    except Exception:
        return {'by_symbol': {}, 'summary': {}, 'by_action': {}}
    return data if isinstance(data, dict) else {'by_symbol': {}, 'summary': {}, 'by_action': {}}


def issue_context_for_symbol(symbol: str, issue_context: dict) -> dict:
    row=(issue_context.get('by_symbol') or {}).get(str(symbol).upper()) or {}
    if not isinstance(row, dict):
        return {}
    adj=max(-8.0, min(3.0, float(row.get('context_score_adjustment') or 0)))
    action=row.get('action') or 'neutral_context'
    evidence=row.get('top_evidence') or []
    labels=[str(x.get('label') or x.get('theme_hint') or x.get('source') or '') for x in evidence[:3] if isinstance(x, dict)]
    return {
        'action': action,
        'context_score_adjustment': adj,
        'evidence_count': row.get('evidence_count'),
        'top_labels': labels,
        'reasons': (row.get('reasons') or [])[:4],
        'source_run_at': issue_context.get('run_at'),
        'authority': issue_context.get('authority'),
    }


def issue_strategy_role(style: str, base_role: str, issue: dict) -> str:
    action=issue.get('action')
    labels=' '.join(issue.get('top_labels') or [])
    if action == 'block_or_avoid':
        return 'issue_risk_avoidance'
    if action == 'next_batch_priority':
        if style in ('breakout','volume_surge') or any(k in labels for k in ('강세','모멘텀','AI','반도체','로보')):
            return 'issue_momentum_breakout'
        if style == 'mean_reversion':
            return 'issue_confirmed_reversion'
        return 'issue_confirmed_context'
    if action == 'watch_or_validation_priority':
        return 'issue_watch_validation'
    return base_role


def load_prices(conn, symbols, start_date):
    ph=','.join('?' for _ in symbols)
    rows=conn.execute(f"SELECT symbol,date,open,high,low,close,volume FROM price_bars WHERE timeframe='1d' AND symbol IN ({ph}) AND date>=? ORDER BY date,symbol", [*symbols,start_date]).fetchall()
    by_date={}; by_symbol={s:[] for s in symbols}
    for r in rows:
        d=dict(r)
        d['open']=float(d.get('open') or d.get('close') or 0)
        d['close']=float(d['close']); d['high']=float(d['high']); d['low']=float(d['low']); d['volume']=float(d['volume'] or 0)
        by_date.setdefault(d['date'],{})[d['symbol']]=d; by_symbol.setdefault(d['symbol'],[]).append(d)
    return by_date,by_symbol

def market_of_symbol(sym):
    sym=str(sym or '')
    return 'KR' if sym.endswith(('.KS','.KQ')) else 'US'

def asset_type_of_symbol(sym):
    sym=str(sym or '')
    etfs={'SPY','QQQ','DIA','IWM','XLK','XLY','XLV','XBI','SMH','SOXX','KRE','XLE','XLF','XLU','TLT','HYG','LQD','EEM','EFA'}
    if sym.startswith('^'): return 'index'
    if sym in etfs or sym.endswith('11'): return 'etf_index'
    return 'stock'

def normalize_signals(signals):
    # Keep the full common universe, but compare raw scores only within similar
    # market/asset buckets so KR stocks, US stocks, ETFs and indices do not
    # compete on incompatible raw volatility/volume scales.
    buckets={}
    for sym,sig in signals.items():
        buckets.setdefault((market_of_symbol(sym), asset_type_of_symbol(sym)), []).append(sig)
    normalized={}
    for bucket,items in buckets.items():
        items=sorted(items,key=lambda x:x.get('score',0),reverse=True)
        n=len(items)
        for rank,sig in enumerate(items):
            pct=1.0 if n==1 else 1.0 - rank/(n-1)
            raw=float(sig.get('score') or 0)
            # percentile is primary; raw score is a small tie/quality adjustment.
            norm=45 + pct*35 + max(-5,min(5,(raw-60)*0.15))
            ns=dict(sig)
            ns['raw_score']=round(raw,2)
            ns['score']=round(norm,2)
            ns['score_normalization']={'bucket':f'{bucket[0]}:{bucket[1]}','rank':rank+1,'bucket_size':n,'percentile':round(pct,4)}
            normalized[ns['symbol']]=ns
    return normalized

def strategy_roles_for_style(style: str) -> list[str]:
    book = {
        "trend": ["trend_following", "pullback_in_uptrend", "winner_pyramiding"],
        "breakout": ["range_breakout", "volume_breakout", "momentum_continuation"],
        "balanced": ["trend_following", "mean_reversion", "defensive_hold"],
        "defensive": ["defensive_trend", "low_volatility_guard", "cash_preservation"],
        "mean_reversion": ["pullback_reversion", "oversold_recovery", "range_reversion"],
        "volume_surge": ["volume_breakout", "supply_close_strength", "momentum_continuation"],
        "value": ["low_valuation_accumulation", "value_quality_recovery", "margin_of_safety"],
        "quality": ["high_roe_compounder", "quality_pullback_uptrend", "profitability_momentum"],
        "garp": ["growth_at_reasonable_price", "quality_growth_breakout", "valuation_supported_momentum"],
        "dividend": ["shareholder_yield_stability", "income_defensive_hold", "dividend_quality"],
        "financial_safety": ["low_leverage_quality", "balance_sheet_defense", "cash_preservation_quality"],
    }
    return book.get(style, ["generalist_rotation"])


def choose_strategy_role(style: str, r5: float, r20: float, r60: float, vol_ratio: float, close: float, ma20: float, ma60: float, high20: float, low20: float) -> str:
    if style == "trend":
        if r20 > 6 and r5 > 1:
            return "winner_pyramiding"
        if close > ma60 and r5 < 0 <= r20:
            return "pullback_in_uptrend"
        return "trend_following"
    if style == "breakout":
        if close >= high20 * 0.995 and vol_ratio >= 1.4:
            return "volume_breakout"
        if close >= high20 * 0.995:
            return "range_breakout"
        return "momentum_continuation"
    if style == "balanced":
        if r5 < -3 and close > ma60:
            return "mean_reversion"
        if ma20 > ma60:
            return "trend_following"
        return "defensive_hold"
    if style == "defensive":
        if abs(r5) <= 3 and close > low20 * 1.04:
            return "low_volatility_guard"
        if ma20 > ma60:
            return "defensive_trend"
        return "cash_preservation"
    if style == "mean_reversion":
        if r5 < -5 and close > ma60:
            return "oversold_recovery"
        if close < ma20 * 0.97:
            return "pullback_reversion"
        return "range_reversion"
    if style == "volume_surge":
        if vol_ratio >= 1.8 and close > ma20:
            return "supply_close_strength"
        if vol_ratio >= 1.3:
            return "volume_breakout"
        return "momentum_continuation"
    if style == "value":
        if r20 > 0 and close > ma20:
            return "value_quality_recovery"
        if close < ma20 * 0.98:
            return "margin_of_safety"
        return "low_valuation_accumulation"
    if style == "quality":
        if close > ma20 and r20 > 3:
            return "profitability_momentum"
        if r5 < 0 and close > ma60:
            return "quality_pullback_uptrend"
        return "high_roe_compounder"
    if style == "garp":
        if close >= high20 * 0.99 and vol_ratio >= 1.2:
            return "quality_growth_breakout"
        if r20 > 4 and close > ma60:
            return "valuation_supported_momentum"
        return "growth_at_reasonable_price"
    if style == "dividend":
        if abs(r5) <= 3 and close > low20 * 1.03:
            return "income_defensive_hold"
        return "shareholder_yield_stability"
    if style == "financial_safety":
        if ma20 > ma60:
            return "low_leverage_quality"
        if abs(r5) <= 2:
            return "balance_sheet_defense"
        return "cash_preservation_quality"
    return "generalist_rotation"


def signal_for(symbol, idx, hist, fund, issue_context=None, factor_snapshot=None, market_context=None):
    if idx < 60: return None
    row=hist[idx]; close=row['close']; prev=hist[:idx+1]
    closes=[x['close'] for x in prev]; vols=[x['volume'] for x in prev]
    ma5=avg(closes[-5:]); ma20=avg(closes[-20:]); ma60=avg(closes[-60:])
    r5=pct(closes[-1],closes[-6]) if len(closes)>6 else 0; r20=pct(closes[-1],closes[-21]) if len(closes)>21 else 0; r60=pct(closes[-1],closes[-61]) if len(closes)>61 else 0
    vol20=avg(vols[-20:]) or 1; vol_ratio=vols[-1]/vol20 if vol20 else 1
    high20=max(x['high'] for x in prev[-20:]); low20=min(x['low'] for x in prev[-20:])
    style=fund['style']; score=50; reason=[]
    issue=issue_context_for_symbol(symbol, issue_context or {})
    strategy_role=choose_strategy_role(style,r5,r20,r60,vol_ratio,close,ma20 or close,ma60 or close,high20,low20)
    if style=='trend':
        score += (12 if ma5>ma20>ma60 else -10) + min(18,max(-10,r20))*0.8 + min(8,max(-5,r60))*0.25; reason.append('ma/trend')
    elif style=='breakout':
        score += (18 if close>=high20*0.995 else -8) + min(12,vol_ratio*3) + max(0,r5)*0.8; reason.append('20d breakout/volume')
    elif style=='balanced':
        score += (8 if ma20>ma60 else -4) + min(10,max(-8,r20))*0.5 + (4 if vol_ratio>0.8 else -2); reason.append('balanced trend')
    elif style=='defensive':
        score += (8 if ma20>ma60 else -8) - max(0,abs(r5)-6)*1.5 + (4 if close>low20*1.04 else -4); reason.append('low volatility guard')
    elif style=='mean_reversion':
        score += (14 if r5<-4 and close>ma60 else -4) + (8 if close<ma20*0.97 else 0) - max(0,-r60-10)*0.4; reason.append('pullback reversion')
    elif style=='volume_surge':
        score += min(20,(vol_ratio-1)*8) + max(0,r5)*0.7 + (6 if close>ma20 else -4); reason.append('volume surge')
    elif style=='value':
        score += (6 if close>ma60 else -2) + min(8,max(-8,r20))*0.25 - max(0,r5-7)*0.5; reason.append('valuation style base')
    elif style=='quality':
        score += (8 if ma20>ma60 else -3) + min(10,max(-5,r20))*0.45 + (3 if vol_ratio>0.7 else -2); reason.append('quality compounder base')
    elif style=='garp':
        score += (8 if close>ma60 else -4) + min(12,max(-6,r20))*0.55 + min(4,max(0,r5))*0.4; reason.append('growth at reasonable price base')
    elif style=='dividend':
        score += (5 if close>low20*1.03 else -3) - max(0,abs(r5)-5)*1.1 + (3 if ma20>ma60 else -2); reason.append('yield stability base')
    elif style=='financial_safety':
        score += (6 if ma20>ma60 else -5) - max(0,abs(r5)-4)*1.4 + (3 if close>low20*1.05 else -3); reason.append('balance sheet safety base')
    factor=(factor_snapshot or {}).get(symbol)
    factor_adj,factor_tags=factor_adjustment_for_style(style,factor)
    if factor_adj:
        score += factor_adj; reason.append('fundamental_factor')
    issue_adj=float(issue.get('context_score_adjustment') or 0)
    if issue:
        if issue.get('action') == 'block_or_avoid':
            score -= 18; reason.append('issue:block_or_avoid')
        elif style in ('breakout','volume_surge','trend','quality','garp'):
            score += issue_adj; reason.append('issue_context')
        elif style in ('balanced','mean_reversion','value','dividend','financial_safety'):
            score += min(issue_adj, 1.5); reason.append('issue_context_limited')
        elif style == 'defensive' and issue.get('action') in ('next_batch_priority','watch_or_validation_priority'):
            score -= 1.0; reason.append('issue_chase_risk_defensive_penalty')
        strategy_role=issue_strategy_role(style, strategy_role, issue)
    market_adj,market_notes=market_context_adjustment(style, market_context)
    if market_adj:
        score += market_adj; reason.append('daily_market_context')
    return {'symbol':symbol,'date':row['date'],'close':close,'score':round(score,2),'reason':reason,'strategy_role':strategy_role,'allowed_strategy_roles':fund.get('allowed_strategy_roles') or strategy_roles_for_style(style),'target':round(close*(1+fund['target_pct']/100),2),'stop':round(close*(1+fund['stop_pct']/100),2),'issue_context':issue,'issue_context_parameter':{'enabled':bool(issue),'style_adjustment_applied':round(issue_adj,2),'role_after_issue':strategy_role},'daily_market_context_parameter':{'enabled':bool(market_context),'trade_date':(market_context or {}).get('trade_date'),'coverage_level':(market_context or {}).get('coverage_level'),'context_complete':bool((market_context or {}).get('context_complete')),'style_adjustment_applied':market_adj,'notes':market_notes,'issue_keywords':((market_context or {}).get('issue_keywords') or [])[:6]},'fundamental_factor':{'enabled':bool(factor and factor.get('available')),'style_adjustment_applied':factor_adj,'tags':factor_tags,'scores':(factor or {}).get('scores'),'metrics':(factor or {}).get('metrics'),'policy':(factor or {}).get('policy')},'metrics':{'r5':round(r5,2),'r20':round(r20,2),'r60':round(r60,2),'vol_ratio':round(vol_ratio,2)}}

def value(fund, prices):
    pv=0.0
    fx=prices.get('_fx') or {}
    for sym,pos in fund['positions'].items():
        bar=prices.get(sym); px=(bar.get('close') if isinstance(bar,dict) else bar) if bar is not None else pos['entry_price']; px=normalize_trade_price(sym, float(px)); pv += pos['qty']*price_to_krw(sym, px, fx)
    return money_floor(fund['cash']+pv),money_floor(pv)

def choose_exit_price(pos, bar):
    close=float(bar.get('close') or pos['entry_price'])
    high=float(bar.get('high') or close)
    low=float(bar.get('low') or close)
    # With daily bars we do not know intraday order if both target and stop were touched.
    # Use conservative assumption: stop first. Otherwise execute at target/stop price.
    if low <= pos['stop'] and high >= pos['target']:
        return float(pos['stop']), 'stop_hit_intraday_both_touched_conservative'
    if low <= pos['stop']:
        return float(pos['stop']), 'stop_hit_intraday'
    if high >= pos['target']:
        return float(pos['target']), 'target_hit_intraday'
    return close, None


def build_fund_entry_plan(fund, sig, bar):
    close=float((bar or {}).get('close') or sig.get('close') or 0)
    high=float((bar or {}).get('high') or close)
    low=float((bar or {}).get('low') or close)
    style=fund.get('style') or ''
    metrics=sig.get('metrics') or {}
    vol_ratio=float(metrics.get('vol_ratio') or 1.0)
    r5=float(metrics.get('r5') or 0.0)
    if style in ('defensive','dividend','financial_safety'):
        pullback_pct=1.4
        if r5 > 5:
            pullback_pct += 0.8
        if vol_ratio > 1.5:
            pullback_pct += 0.4
        mode='wait_for_target_buy_touch'
    elif style in ('balanced','mean_reversion','value'):
        pullback_pct=0.8
        mode='prefer_entry_zone'
    else:
        pullback_pct=0.35
        mode='close_or_shallow_pullback'
    target_buy=round(close*(1-pullback_pct/100),2)
    acceptable_upper=round(close*(1-max(0.2,pullback_pct*0.45)/100),2)
    touched=low <= target_buy
    return {
        'policy':'paper_fund_entry_plan_intraday_low_touch_no_orders',
        'mode':mode,
        'basis_close':round(close,2),
        'day_high':round(high,2),
        'day_low':round(low,2),
        'target_buy_price':target_buy,
        'acceptable_entry_upper':acceptable_upper,
        'required_touch':'low<=target_buy_price' if style == 'defensive' else 'close_or_low_within_entry_zone',
        'touched':bool(touched),
    }


def planned_entry_price(fund, sig, bar):
    plan=build_fund_entry_plan(fund,sig,bar)
    close=float((bar or {}).get('close') or sig.get('close') or 0)
    low=float((bar or {}).get('low') or close)
    style=fund.get('style') or ''
    if style in ('defensive','dividend','financial_safety'):
        if low <= plan['target_buy_price']:
            return float(plan['target_buy_price']), plan, 'entry_plan_target_low_touch'
        return None, plan, 'entry_plan_wait_no_touch'
    if low <= plan['target_buy_price']:
        return float(plan['target_buy_price']), plan, 'entry_plan_target_low_touch'
    if close <= plan['acceptable_entry_upper']:
        return close, plan, 'entry_plan_close_inside_zone'
    if style in ('balanced','mean_reversion','value'):
        return None, plan, 'entry_plan_wait_no_touch'
    return close, plan, 'entry_plan_momentum_close'


def signal_for_entry(sig, fund, fill_px, entry_plan):
    adjusted=dict(sig)
    adjusted['entry_plan']=entry_plan
    adjusted['entry_price']=round(float(fill_px),2)
    adjusted['target']=round(float(fill_px)*(1+float(fund['target_pct'])/100),2)
    adjusted['stop']=round(float(fill_px)*(1+float(fund['stop_pct'])/100),2)
    return adjusted

def sell_qty(fund, date, sym, pos, qty, px, reason, args):
    qty=int(qty)
    if qty<=0: return None
    px=normalize_trade_price(sym, px)
    fx=args._fx if hasattr(args, '_fx') else {}
    exit_krw=price_to_krw(sym, px, fx)
    entry_krw=float(pos.get('entry_price') or px)*float(pos.get('entry_fx') or fx_rate_for_symbol(sym, fx))
    gross_pnl=money_floor((exit_krw-entry_krw)*qty); notional=money_floor(exit_krw*qty); costs=sell_costs(sym,notional,gross_pnl,args); costs={k:money_floor(v) for k,v in costs.items()}; net_pnl=money_floor(gross_pnl-costs['total'])
    fund['cash']=money_floor(fund['cash'] + notional-costs['total']); fund['realized_pnl'] += net_pnl; fund['trade_count']+=1; fund['total_costs']=fund.get('total_costs',0.0)+costs['total']
    pos['qty']=int(pos.get('qty',0))-qty
    tr={'fund_id':fund['id'],'date':date,'symbol':sym,'side':'sell','price':round(px,2),'price_krw':round(exit_krw,2),'fx_rate':round(fx_rate_for_symbol(sym, fx),4),'qty':qty,'gross_pnl':round(gross_pnl,2),'pnl':round(net_pnl,2),'costs':{k:round(v,2) for k,v in costs.items()},'reason':reason,'strategy_role':pos.get('strategy_role'),'fund_style':fund.get('style'),'fundamental_factor':pos.get('fundamental_factor'),'issue_context':pos.get('issue_context'),'issue_context_parameter':pos.get('issue_context_parameter'),'daily_market_context_parameter':pos.get('daily_market_context_parameter'),'intraday_exit':'intraday' in reason}
    if pos['qty']<=0 and sym in fund['positions']: del fund['positions'][sym]
    return tr


def can_exit_position(pos, date):
    # Entry plans can fill on an intraday low touch. With daily bars, same-day
    # target/stop ordering is unknowable, so enforce at least one overnight hold.
    entry_date=str(pos.get("entry_date") or "")
    return bool(entry_date and str(date) > entry_date)

def buy_position(fund, date, sym, px, sig, budget, args, reason='buy'):
    px=normalize_trade_price(sym, px)
    sig=signal_for_entry(sig, fund, px, sig.get('entry_plan') or {}) if sig.get('entry_plan') else dict(sig)
    fx=args._fx if hasattr(args, '_fx') else {}
    px_krw=price_to_krw(sym, px, fx)
    budget=money_floor(budget)
    qty=int(budget // px_krw)
    if qty < 1: return None
    budget=money_floor(qty * px_krw); fee=money_floor(buy_fee(sym,budget,args)); total_cash=budget+fee
    while qty>0 and total_cash>fund['cash']:
        qty-=1; budget=money_floor(qty * px_krw); fee=money_floor(buy_fee(sym,budget,args)); total_cash=budget+fee
    if qty<1: return None
    fund['cash']=money_floor(fund['cash']-total_cash); fund['trade_count']+=1; fund['total_costs']=fund.get('total_costs',0.0)+fee
    if sym in fund['positions']:
        pos=fund['positions'][sym]; old_qty=int(pos.get('qty',0)); new_qty=old_qty+qty
        pos['entry_price']=normalize_trade_price(sym, ((pos['entry_price']*old_qty)+(px*qty))/new_qty); pos['entry_fx']=round(((float(pos.get('entry_fx') or fx_rate_for_symbol(sym, fx))*old_qty)+(fx_rate_for_symbol(sym, fx)*qty))/new_qty,4); pos['qty']=new_qty; pos['target']=sig['target']; pos['score']=sig['score']; pos['reason']=sig['reason']; pos['strategy_role']=sig.get('strategy_role'); pos['allowed_strategy_roles']=sig.get('allowed_strategy_roles'); pos['fundamental_factor']=sig.get('fundamental_factor'); pos['issue_context']=sig.get('issue_context'); pos['issue_context_parameter']=sig.get('issue_context_parameter'); pos['daily_market_context_parameter']=sig.get('daily_market_context_parameter'); pos['add_count']=int(pos.get('add_count',0))+1; pos['last_add_date']=date; pos['max_price']=max(float(pos.get('max_price',px)),px)
        if reason == 'average_down': pos['average_down_count']=int(pos.get('average_down_count',0))+1
        if reason == 'pyramid_winner': pos['pyramid_count']=int(pos.get('pyramid_count',0))+1
    else:
        fund['positions'][sym]={'symbol':sym,'entry_date':date,'entry_price':px,'entry_fx':fx_rate_for_symbol(sym, fx),'qty':qty,'target':sig['target'],'stop':sig['stop'],'score':sig['score'],'reason':sig['reason'],'strategy_role':sig.get('strategy_role'),'allowed_strategy_roles':sig.get('allowed_strategy_roles'),'fundamental_factor':sig.get('fundamental_factor'),'entry_plan':sig.get('entry_plan'),'issue_context':sig.get('issue_context'),'issue_context_parameter':sig.get('issue_context_parameter'),'daily_market_context_parameter':sig.get('daily_market_context_parameter'),'buy_fee':fee,'add_count':0,'average_down_count':0,'pyramid_count':0,'scale_out_count':0,'max_price':px}
    return {'fund_id':fund['id'],'date':date,'symbol':sym,'side':'buy','price':round(px,2),'price_krw':round(px_krw,2),'fx_rate':round(fx_rate_for_symbol(sym, fx),4),'qty':qty,'budget':round(budget,2),'costs':{'commission':round(fee,2),'total':round(fee,2)},'score':sig['score'],'reason':reason+':' + ','.join(sig['reason']),'strategy_role':sig.get('strategy_role'),'fund_style':fund.get('style'),'fundamental_factor':sig.get('fundamental_factor'),'entry_plan':sig.get('entry_plan'),'issue_context':sig.get('issue_context'),'issue_context_parameter':sig.get('issue_context_parameter'),'daily_market_context_parameter':sig.get('daily_market_context_parameter')}


def position_market_value(pos, px, fx=None):
    return money_floor(int(pos.get('qty') or 0) * price_to_krw(pos.get('symbol') or '', float(px or pos.get('entry_price') or 0), fx or {}))

def rebalance_budget(fund, sym, pos, px, prices, args, direction):
    equity,pv=value(fund,prices)
    if equity <= 0 or px <= 0:
        return 0.0
    max_exposure=equity*float(fund.get('max_symbol_exposure_pct') or 0.24)
    current_exposure=position_market_value(pos, px, prices.get('_fx') or {})
    exposure_room=max(0.0, max_exposure-current_exposure)
    if exposure_room <= 0:
        return 0.0
    # Use a smaller slice for rebalance adds than brand-new positions, but do
    # not cap by add count. Repeated adds are allowed until seed/cash/exposure
    # constraints stop them.
    risk_slice=0.45 if direction == 'pyramid_winner' else 0.40
    deployable=max(0.0, equity*(1-float(fund.get('cash_buffer') or 0))-pv)
    return min(fund['cash'], exposure_room, equity*float(fund['risk_per_trade'])*risk_slice, deployable)

def step_fund(fund, date, signals, prices, args):
    if fund['status']!='active': return []
    fund['age_days']+=1; trades=[]
    for sym,pos in list(fund['positions'].items()):
        bar=prices.get(sym)
        if not bar: continue
        close=float(bar.get('close') or pos['entry_price']); high=float(bar.get('high') or close); low=float(bar.get('low') or close)
        pos['max_price']=max(float(pos.get('max_price',pos['entry_price'])), high)
        if not can_exit_position(pos,date):
            continue
        # trailing stop activates only after price moves at least halfway toward target.
        if fund.get('trailing_stop_enabled') and pos['max_price'] >= pos['entry_price']*(1+max(1.0,fund['target_pct']*0.5)/100):
            trail=pos['max_price']*(1-max(2.0,abs(fund['stop_pct'])*0.45)/100)
            pos['stop']=max(float(pos.get('stop',0)), money_floor(trail))
        if low <= pos['stop'] and high >= pos['target']:
            tr=sell_qty(fund,date,sym,pos,pos['qty'],float(pos['stop']),'stop_hit_intraday_both_touched_conservative',args)
            if tr: trades.append(tr)
            continue
        if low <= pos['stop']:
            tr=sell_qty(fund,date,sym,pos,pos['qty'],float(pos['stop']),'stop_hit_intraday',args)
            if tr: trades.append(tr)
            continue
        if high >= pos['target']:
            if fund.get('scale_out_enabled') and int(pos.get('scale_out_count',0))<1 and int(pos.get('qty',0))>1:
                qty=max(1,int(pos['qty'])//2)
                tr=sell_qty(fund,date,sym,pos,qty,float(pos['target']),'scale_out_target_hit_intraday',args)
                if tr: trades.append(tr)
                if sym in fund['positions']:
                    pos=fund['positions'][sym]; pos['scale_out_count']=int(pos.get('scale_out_count',0))+1; pos['stop']=max(float(pos.get('stop',0)), float(pos.get('entry_price',0))); pos['target']=money_floor(float(pos.get('target',close))*(1+max(2.0,fund['target_pct']*0.5)/100))
            else:
                tr=sell_qty(fund,date,sym,pos,pos['qty'],float(pos['target']),'target_hit_intraday',args)
                if tr: trades.append(tr)
            continue
        if sym not in signals or signals[sym]['score'] < fund['score_min']-10:
            tr=sell_qty(fund,date,sym,pos,pos['qty'],close,'signal_decay_close',args)
            if tr: trades.append(tr)
    equity,pv=value(fund,prices)
    for sym,pos in list(fund['positions'].items()):
        sig=signals.get(sym); bar=prices.get(sym); px=float((bar or {}).get('close') or 0)
        if not sig or not px: continue
        if fund.get('average_down_enabled'):
            trigger=float(fund.get('average_down_trigger_pct') or max(2.0,abs(fund['stop_pct'])*0.45))
            if px <= pos['entry_price']*(1-trigger/100) and sig['score'] >= max(fund['score_min'], pos.get('score',0)-2):
                budget=rebalance_budget(fund,sym,pos,px,prices,args,'average_down')
                if budget>=equity*0.01:
                    tr=buy_position(fund,date,sym,px,sig,budget,args,reason='average_down')
                    if tr: trades.append(tr)
        if fund.get('pyramid_enabled'):
            trigger=float(fund.get('pyramid_trigger_pct') or max(1.0,fund['target_pct']*0.35))
            if px >= pos['entry_price']*(1+trigger/100) and sig['score'] >= max(fund['score_min']+3, pos.get('score',0)-1):
                budget=rebalance_budget(fund,sym,pos,px,prices,args,'pyramid_winner')
                if budget>=equity*0.01:
                    tr=buy_position(fund,date,sym,px,sig,budget,args,reason='pyramid_winner')
                    if tr: trades.append(tr)
    equity,pv=value(fund,prices); slots=max(0,int(fund['max_positions'])-len(fund['positions']))
    for sig in sorted(signals.values(), key=lambda x:x['score'], reverse=True):
        if slots<=0: break
        sym=sig['symbol']; bar=prices.get(sym); close=float((bar or {}).get('close') or 0)
        if not close or sym in fund['positions'] or sig['score']<fund['score_min']: continue
        px,entry_plan,entry_reason=planned_entry_price(fund,sig,bar)
        if not px: continue
        equity,pv=value(fund,prices); budget=min(fund['cash'], equity*fund['risk_per_trade'], max(0,equity*(1-fund['cash_buffer'])-pv))
        if budget<equity*0.015: continue
        entry_sig=signal_for_entry(sig,fund,px,entry_plan)
        tr=buy_position(fund,date,sym,px,entry_sig,budget,args,reason='new_position_'+entry_reason)
        if tr:
            slots-=1; trades.append(tr)
    equity,pv=value(fund,prices); peak=max([h.get('equity',fund['initial_capital']) for h in fund['history']]+[fund['initial_capital'],equity])
    fund['history'].append({'date':date,'equity':round(equity,2),'return_pct':round((equity/fund['initial_capital']-1)*100,2),'mdd_pct':round((equity/peak-1)*100,2),'position_count':len(fund['positions']),'age_days':fund['age_days'],'total_costs':round(fund.get('total_costs',0.0),2)})
    return trades

def style_counts(funds):
    counts={style:0 for style in STYLE_ORDER}
    for f in funds:
        if f.get('status') == 'active':
            counts[f.get('style') or 'unknown']=counts.get(f.get('style') or 'unknown',0)+1
    return counts

def min_style_survivors(active_count):
    if active_count >= len(STYLE_ORDER) * 2:
        return 2
    return 1 if active_count >= len(STYLE_ORDER) else 0

def choose_underrepresented_style(counts, rng, exclude_style=None):
    ordered=sorted(STYLE_ORDER, key=lambda s:(counts.get(s,0), rng.random()))
    for style in ordered:
        if style != exclude_style:
            return style
    return ordered[0] if ordered else exclude_style

def select_retirees(ranked, retire_count, counts, min_survivors):
    selected=[]
    for f in reversed(ranked):
        if len(selected) >= retire_count:
            break
        style=f.get('style')
        if counts.get(style,0) <= min_survivors:
            continue
        selected.append(f)
        counts[style]=counts.get(style,0)-1
    if len(selected) < retire_count:
        protected={f.get('id') for f in selected}
        for f in reversed(ranked):
            if len(selected) >= retire_count:
                break
            if f.get('id') not in protected:
                selected.append(f)
    return selected

def latest_return(f):
    try:
        return float((f.get('history') or [{}])[-1].get('return_pct') or 0)
    except Exception:
        return 0.0

def latest_mdd(f):
    try:
        return abs(float((f.get('history') or [{}])[-1].get('mdd_pct') or 0))
    except Exception:
        return abs(float(f.get('mdd_pct') or 0))

def turnover_per_day(f):
    age=max(1.0,float(f.get('age_days') or 0))
    return float(f.get('trade_count') or 0)/age

def parent_quality_score(f):
    return latest_return(f) - latest_mdd(f)*0.55 - max(0.0, turnover_per_day(f)-2.5)*1.5 + min(float(f.get('age_days') or 0),120.0)*0.015

def add_parent(pool, fund, role, reason):
    if not fund or not fund.get('id'):
        return
    entry=pool.setdefault(fund['id'], {'fund':fund,'roles':[]})
    entry['roles'].append({'role':role,'reason':reason})

def build_mixed_parent_pool(ranked, active, retire_n, champion_min_age, champion_rank_cutoff, champion_challengers, rng):
    pool={}
    mature=[f for f in ranked if f.get('age_days',0)>=60]
    for f in mature[:max(2,retire_n)]:
        add_parent(pool,f,'high_performance_mature','top_return_parent_age_60_plus')
    robust=sorted([f for f in ranked if f.get('age_days',0)>=45], key=lambda f:(parent_quality_score(f), -latest_mdd(f), -turnover_per_day(f)), reverse=True)[:max(2,retire_n)]
    for f in robust:
        add_parent(pool,f,'robust_mid_pack','quality_score_balances_return_drawdown_turnover')
    represented={entry['fund'].get('style') for entry in pool.values()}
    by_style={}
    for f in ranked:
        by_style.setdefault(f.get('style') or 'unknown', []).append(f)
    for style in [s for s in STYLE_ORDER if s not in represented and by_style.get(s)][:max(1,retire_n)]:
        add_parent(pool,by_style[style][0],'diversity_underrepresented_style','preserve_style_dna_outside_top_return_cluster')
    existing_parent_ids={f.get('parent_id') for f in active if f.get('parent_id')}
    champ_min_age=max(60,int(champion_min_age or 0))
    stale_champions=[f for f in ranked[:max(1,int(champion_rank_cutoff or 0))] if f.get('age_days',0)>=champ_min_age and f.get('id') not in existing_parent_ids]
    challenger_parents=stale_champions[:max(0,int(champion_challengers or 0))]
    for f in challenger_parents:
        add_parent(pool,f,'champion_clone','top_rank_age_60_plus_no_existing_clone')
    random_candidates=[f for f in active if f.get('history') and f.get('age_days',0)>=30]
    rng.shuffle(random_candidates)
    for f in random_candidates[:max(1, retire_n//3)]:
        add_parent(pool,f,'random_immigrant','small_random_parent_component_for_exploration')
    ordered=sorted(pool.values(), key=lambda e:(parent_quality_score(e['fund']), latest_return(e['fund'])), reverse=True)
    return ordered, challenger_parents, {
        'policy':'mixed_parent_pool_v1',
        'young_fund_guard':'30-60d funds may contribute only via robust/diversity/random roles; champion clone parents require age>=60',
        'champion_clone_min_age_days':champ_min_age,
        'roles':['high_performance_mature','robust_mid_pack','diversity_underrepresented_style','random_immigrant','champion_clone'],
    }

def evolve(funds,min_age,retire_pct,capital,champion_min_age=90,champion_rank_cutoff=10,champion_challengers=2):
    active=[f for f in funds if f['status']=='active']
    eligible=[f for f in active if f['age_days']>=min_age and f.get('history')]
    if len(eligible)<5: return {'retired':[], 'debuted':[], 'eligible_count':len(eligible)}
    ranked=sorted(eligible,key=lambda f:latest_return(f),reverse=True); n=max(1,int(len(eligible)*retire_pct)); retired=[]; debuted=[]
    next_id=len(funds)+1; rng=random.Random(next_id+len(ranked))
    parent_entries, challenger_parents, parent_policy = build_mixed_parent_pool(ranked, active, n, champion_min_age, champion_rank_cutoff, champion_challengers, rng)
    if not parent_entries:
        parent_entries=[{'fund':f,'roles':[{'role':'fallback_ranked_parent','reason':'mixed_pool_empty'}]} for f in ranked[:max(2,n)]]
    retire_count=min(len(ranked), n+len(challenger_parents))
    pre_style_counts=style_counts(funds)
    working_counts=dict(pre_style_counts)
    min_survivors=min_style_survivors(len(active))
    retirees=select_retirees(ranked, retire_count, working_counts, min_survivors)
    for f in retirees:
        f['status']='retired'; f['retired_at']=datetime.now(timezone.utc).isoformat(); f['retire_reason']='daily_underperformance_after_min_age_trading_days_style_diversity_guarded'; retired.append(f['id'])
    debut_parent_entries=[parent_entries[i%len(parent_entries)] for i in range(n)] + [{'fund':p,'roles':[{'role':'champion_clone','reason':'extra_champion_challenger_slot'}]} for p in challenger_parents]
    challenger_parent_ids={p['id'] for p in challenger_parents}
    child_styles=[]; child_parent_roles=[]
    for parent_entry in debut_parent_entries:
        p0=parent_entry['fund']
        p=dict(p0); child={k:v for k,v in p.items() if k not in ('positions','history')}
        role_names=[r.get('role') for r in parent_entry.get('roles') or []]
        primary_role=role_names[0] if role_names else 'mixed_parent'
        is_challenger=p['id'] in challenger_parent_ids or primary_role == 'champion_clone'
        underrepresented=[s for s in STYLE_ORDER if working_counts.get(s,0) < min_survivors]
        mutate_style = bool(underrepresented) or (is_challenger and rng.random()<0.35) or primary_role == 'random_immigrant'
        if mutate_style:
            new_style=choose_underrepresented_style(working_counts, rng, exclude_style=p.get('style'))
            apply_style_defaults(child, new_style, reset_params=True)
            child['style_mutation_reason']='diversity_reseed' if underrepresented else ('random_immigrant_reseed' if primary_role == 'random_immigrant' else 'champion_challenger_style_probe')
        child.update({'id':f'price_fund_{next_id:03d}','generation':p['generation']+1,'age_days':0,'status':'active','cash':capital,'initial_capital':capital,'positions':{},'history':[],'trade_count':0,'realized_pnl':0.0,'total_costs':0.0,'parent_id':p['id'],'parent_selection_role':primary_role,'parent_selection_roles':parent_entry.get('roles') or [],'challenger_type':'stale_champion_mutation' if is_challenger else ('random_immigrant_seed' if primary_role == 'random_immigrant' else 'bottom_replacement')}); next_id+=1
        child_parent_roles.append({'child_id':child['id'],'parent_id':p['id'],'parent_style':p.get('style'),'parent_age_days':p.get('age_days'),'parent_return_pct':round(latest_return(p),2),'parent_mdd_pct':round(latest_mdd(p),2),'parent_turnover_per_day':round(turnover_per_day(p),3),'roles':parent_entry.get('roles') or []})
        span_scale=0.55 if is_challenger else (1.25 if primary_role == 'random_immigrant' else 1.0)
        for k,span in [('cash_buffer',0.06),('risk_per_trade',0.025),('score_min',3),('target_pct',1.2),('stop_pct',1.0)]: child[k]=round(float(child[k])+rng.uniform(-span*span_scale,span*span_scale),3)
        if is_challenger:
            child['pyramid_enabled']=not bool(child.get('pyramid_enabled')) if rng.random()<0.35 else bool(child.get('pyramid_enabled'))
            child['average_down_enabled']=not bool(child.get('average_down_enabled')) if rng.random()<0.35 else bool(child.get('average_down_enabled'))
            child['scale_out_enabled']=not bool(child.get('scale_out_enabled')) if rng.random()<0.35 else bool(child.get('scale_out_enabled'))
            child['trailing_stop_enabled']=not bool(child.get('trailing_stop_enabled')) if rng.random()<0.35 else bool(child.get('trailing_stop_enabled'))
        child['cash_buffer']=min(max(child['cash_buffer'],0.05),0.75); child['risk_per_trade']=min(max(child['risk_per_trade'],0.03),0.22); child['stop_pct']=min(child['stop_pct'],-1.5)
        apply_style_defaults(child, child.get('style'))
        funds.append(child); debuted.append(child['id']); working_counts[child.get('style')]=working_counts.get(child.get('style'),0)+1; child_styles.append(child.get('style'))
    post_style_counts=style_counts(funds)
    return {'retired':retired, 'debuted':debuted, 'eligible_count':len(eligible), 'parent_selection_policy':parent_policy, 'parent_roles':child_parent_roles, 'champion_challengers':[{'parent_id':p['id'],'parent_rank':ranked.index(p)+1,'parent_return_pct':latest_return(p),'parent_age_days':p.get('age_days')} for p in challenger_parents], 'style_diversity':{'min_survivors':min_survivors,'before':pre_style_counts,'after':post_style_counts,'debut_styles':child_styles}}

def main():
    ap=argparse.ArgumentParser(description='Fast historical paper fund replay using price-derived signals only')
    ap.add_argument('--days',type=int,default=0,help='Optional rolling window override. Default 0 uses --start-date.')
    ap.add_argument('--start-date',default=DEFAULT_REPLAY_START_DATE,help='Fund fair-start date. Default: 2023-01-01.')
    ap.add_argument('--end-date',default='',help='Replay end date. Empty means today UTC.')
    ap.add_argument('--fund-count',type=int,default=30); ap.add_argument('--symbols',default='',help='Optional override. Empty means use canonical common universe.'); ap.add_argument('--max-symbols',type=int,default=0,help='Optional cap; 0 means all common-universe symbols.'); ap.add_argument('--initial-capital',type=float,default=DEFAULT_CAPITAL); ap.add_argument('--min-age-days',type=int,default=30); ap.add_argument('--retire-pct',type=float,default=0.15); ap.add_argument('--evolve-every-days',type=int,default=5); ap.add_argument('--kr-commission-bps',type=float,default=DEFAULT_KR_COMMISSION_BPS); ap.add_argument('--kr-sell-tax-bps',type=float,default=DEFAULT_KR_SELL_TAX_BPS); ap.add_argument('--us-commission-bps',type=float,default=DEFAULT_US_COMMISSION_BPS); ap.add_argument('--us-sell-fee-bps',type=float,default=DEFAULT_US_SELL_FEE_BPS); ap.add_argument('--us-gain-tax-pct',type=float,default=DEFAULT_US_GAIN_TAX_PCT); ap.add_argument('--champion-min-age-days',type=int,default=90); ap.add_argument('--champion-rank-cutoff',type=int,default=10); ap.add_argument('--champion-challengers',type=int,default=2); ap.add_argument('--progress-every-days',type=int,default=25,help='Print replay progress every N trading days; 0 disables.'); ap.add_argument('--output',default='/tmp/paper_fund_price_replay_latest.json')
    args=ap.parse_args()
    business_today=datetime.now(ZoneInfo(REPLAY_BUSINESS_TZ)).date()
    start_date=parse_date_arg(args.start_date, date_cls.fromisoformat(DEFAULT_REPLAY_START_DATE)) or date_cls.fromisoformat(DEFAULT_REPLAY_START_DATE)
    if args.days and args.days > 0:
        start_date=business_today-timedelta(days=args.days)
    end_date=parse_date_arg(args.end_date, business_today) or business_today
    init_db(); conn=sqlite3.connect(get_settings().database_path,timeout=30); conn.row_factory=sqlite3.Row
    symbols, universe_source = load_general_universe(conn,args.symbols,args.max_symbols)
    factor_snapshot=load_factor_snapshot(symbols)
    price_start=(start_date-timedelta(days=90)).isoformat(); by_date,by_symbol=load_prices(conn,symbols,price_start); fx_series=load_usdkrw_series(conn,price_start); conn.close()
    market_context_by_date=load_market_context_by_date(start_date.isoformat(), end_date.isoformat())
    market_context_coverage=daily_market_context_coverage('KR', start_date.isoformat(), end_date.isoformat(), 'price_fx_index_issue')
    dates=sorted(d for d in by_date if start_date.isoformat() <= d <= end_date.isoformat())
    idx_by_symbol={s:{row['date']:i for i,row in enumerate(rows)} for s,rows in by_symbol.items()}
    funds=seed_funds(args.fund_count,args.initial_capital); trades=[]; evol=[]; daily=[]; issue_context=load_issue_context()
    started_at=time.time()
    for day,date in enumerate(dates,1):
        prices={s:r for s,r in by_date[date].items()}; prices['_fx']=fx_for_date(fx_series,date)
        day_market_context=market_context_by_date.get(date)
        # Signal generation is style-specific, but many funds share a style.
        # Cache per (day, style) to avoid recomputing the whole KR universe for
        # every fund seed in long backfills.
        style_signal_cache={}
        for f in funds:
            style=f.get('style') or ''
            fsigs=style_signal_cache.get(style)
            if fsigs is None:
                fsigs={}
                for sym,hist in by_symbol.items():
                    idx=idx_by_symbol.get(sym,{}).get(date)
                    if idx is None: continue
                    sig=signal_for(sym,idx,hist,f,issue_context,factor_snapshot,day_market_context)
                    if sig: fsigs[sym]=sig
                fsigs=normalize_signals(fsigs)
                style_signal_cache[style]=fsigs
            args._fx=prices.get('_fx') or {}
            trades.extend(step_fund(f,date,fsigs,prices,args))
        if day%max(1,args.evolve_every_days)==0:
            r=evolve(funds,args.min_age_days,args.retire_pct,args.initial_capital,args.champion_min_age_days,args.champion_rank_cutoff,args.champion_challengers)
            if r.get('retired') or r.get('debuted'):
                evol.append({'date':date, **r})
        active=[f for f in funds if f['status']=='active']; vals=[f['history'][-1]['return_pct'] for f in active if f['history']]
        daily.append({'date':date,'active_funds':len(active),'avg_return_pct':round(sum(vals)/len(vals),2) if vals else None,'best_return_pct':round(max(vals),2) if vals else None,'worst_return_pct':round(min(vals),2) if vals else None})
        if args.progress_every_days and (day == 1 or day == len(dates) or day % args.progress_every_days == 0):
            elapsed=max(0.1,time.time()-started_at)
            print(json.dumps({'event':'replay_progress','day':day,'trading_days':len(dates),'date':date,'active_funds':len(active),'elapsed_sec':round(elapsed,1)}, ensure_ascii=False), flush=True)
    active=[f for f in funds if f['status']=='active']; standings=[]
    for f in active:
        h=f['history'][-1] if f['history'] else {}
        latest_prices={s:r for s,r in by_date[dates[-1]].items()} if dates else {}; latest_fx=fx_for_date(fx_series,dates[-1]) if dates else fx_for_date(fx_series,None); latest_prices['_fx']=latest_fx
        holdings=[]
        for sym,pos in sorted(f.get('positions',{}).items()):
            bar=latest_prices.get(sym) or {}
            current=normalize_trade_price(sym, float(bar.get('close') or pos.get('entry_price') or 0))
            qty=int(pos.get('qty') or 0)
            entry=float(pos.get('entry_price') or 0)
            target=float(pos.get('target') or 0)
            stop=float(pos.get('stop') or 0)
            fx=latest_prices.get('_fx') or {}; current_krw=price_to_krw(sym,current,fx); entry_krw=entry*float(pos.get('entry_fx') or fx_rate_for_symbol(sym,fx)); market_value=money_floor(qty*current_krw)
            pnl=money_floor((current_krw-entry_krw)*qty)
            pnl_pct=round((current_krw/entry_krw-1)*100,2) if entry_krw else None
            holdings.append({'symbol':sym,'qty':qty,'entry_date':pos.get('entry_date'),'entry_price':round(entry,2),'current_price':round(current,2),'current_price_krw':round(current_krw,2),'entry_fx':pos.get('entry_fx'),'fx_rate':round(fx_rate_for_symbol(sym,fx),4),'target':round(target,2),'stop':round(stop,2),'market_value':market_value,'unrealized_pnl':pnl,'unrealized_pnl_pct':pnl_pct,'score':pos.get('score'),'reason':pos.get('reason'),'strategy_role':pos.get('strategy_role'),'allowed_strategy_roles':pos.get('allowed_strategy_roles'),'fundamental_factor':pos.get('fundamental_factor'),'add_count':pos.get('add_count',0),'average_down_count':pos.get('average_down_count',0),'pyramid_count':pos.get('pyramid_count',0),'last_add_date':pos.get('last_add_date'),'scale_out_count':pos.get('scale_out_count',0),'entry_plan':pos.get('entry_plan'),'issue_context':pos.get('issue_context'),'issue_context_parameter':pos.get('issue_context_parameter'),'daily_market_context_parameter':pos.get('daily_market_context_parameter')})
        standings.append({'id':f['id'],'style':f['style'],'generation':f['generation'],'age_days':f['age_days'],'ranking_min_age_days':args.min_age_days,'ranking_eligible':int(f.get('age_days') or 0)>=int(args.min_age_days),'equity':h.get('equity'),'current_asset':h.get('equity'),'return_pct':h.get('return_pct'),'mdd_pct':h.get('mdd_pct'),'position_count':h.get('position_count'),'holdings':holdings,'trade_count':f['trade_count'],'total_costs':round(f.get('total_costs',0.0),2),'parent_id':f.get('parent_id'),'challenger_type':f.get('challenger_type'),'cash_buffer':f['cash_buffer'],'risk_per_trade':f['risk_per_trade'],'score_min':f['score_min'],'target_pct':f['target_pct'],'stop_pct':f['stop_pct'],'pyramid_enabled':f.get('pyramid_enabled',False),'average_down_enabled':f.get('average_down_enabled',False),'max_symbol_exposure_pct':f.get('max_symbol_exposure_pct'),'scale_out_enabled':f.get('scale_out_enabled',False),'trailing_stop_enabled':f.get('trailing_stop_enabled',False),'allowed_strategy_roles':f.get('allowed_strategy_roles') or strategy_roles_for_style(f.get('style'))})
    standings=sorted(standings,key=lambda x:(bool(x.get('ranking_eligible')), x.get('return_pct') if x.get('return_pct') is not None else -999),reverse=True)
    trade_strategy_counts={}
    fund_strategy_counts={}
    for tr in trades:
        role=tr.get('strategy_role') or 'unknown'
        trade_strategy_counts[role]=trade_strategy_counts.get(role,0)+1
        fid=tr.get('fund_id')
        if fid:
            fund_strategy_counts.setdefault(fid,{})[role]=fund_strategy_counts.setdefault(fid,{}).get(role,0)+1
    for row in standings:
        row['strategy_mix']=fund_strategy_counts.get(row.get('id'),{})
        factor_mix={}
        for h in row.get('holdings') or []:
            ff=h.get('fundamental_factor') or {}
            for tag in ff.get('tags') or []:
                factor_mix[tag]=factor_mix.get(tag,0)+1
        row['factor_mix']=factor_mix
    warnings=[]
    latest_available_date=max([d for d in by_date if d <= end_date.isoformat()] or dates[-1:] or [''])
    latest_replay_date=dates[-1] if dates else None
    replay_complete=bool(dates) and bool(latest_available_date) and latest_replay_date >= end_date.isoformat()
    context_complete_through_end=bool(market_context_coverage.get('row_count')) and market_context_coverage.get('complete_count') == market_context_coverage.get('row_count')
    completed_context_dates=sorted(
        str(row.get('trade_date'))
        for row in market_context_by_date.values()
        if row.get('trade_date') and row.get('context_complete')
    )
    context_asof_date=max([d for d in completed_context_dates if d <= (latest_replay_date or end_date.isoformat())] or [None])
    recommendation_asof_date=latest_replay_date
    context_lag_days=(
        (datetime.fromisoformat(recommendation_asof_date).date() - datetime.fromisoformat(context_asof_date).date()).days
        if recommendation_asof_date and context_asof_date else None
    )
    context_lag_policy='same_day_complete_context_required'
    context_lag_allowed=bool(context_complete_through_end)
    if not context_complete_through_end and replay_complete and latest_replay_date == end_date.isoformat() and context_asof_date:
        context_lag_policy='allow_latest_complete_context_for_current_day_intraday_replay'
        context_lag_allowed=bool(context_lag_days is not None and 0 <= context_lag_days <= 4)
    recommendation_eligible=bool(replay_complete and context_lag_allowed)
    if args.days and len(dates)<args.days*0.5: warnings.append('price replay has fewer trading dates than expected')
    if not replay_complete:
        warnings.append(f'fund_replay_incomplete_through_business_today: latest_replay_date={dates[-1] if dates else None} required_end_date={end_date.isoformat()}')
    if not context_complete_through_end:
        warnings.append(f'daily_market_context_incomplete: complete={market_context_coverage.get("complete_count")} rows={market_context_coverage.get("row_count")} min_coverage=price_fx_index_issue')
    market_counts={}
    asset_type_counts={}
    for sym in symbols:
        market_counts[market_of_symbol(sym)] = market_counts.get(market_of_symbol(sym),0)+1
        asset_type_counts[asset_type_of_symbol(sym)] = asset_type_counts.get(asset_type_of_symbol(sym),0)+1
    trade_market_counts={}
    for tr in trades:
        sym=tr.get('symbol')
        if sym: trade_market_counts[market_of_symbol(sym)] = trade_market_counts.get(market_of_symbol(sym),0)+1
    issue_action_counts={}
    issue_strategy_counts={}
    issue_trade_pnl={}
    for tr in trades:
        issue=(tr.get('issue_context') or {})
        action=issue.get('action') or 'none'
        if issue:
            issue_action_counts[action]=issue_action_counts.get(action,0)+1
            role=tr.get('strategy_role') or 'unknown'
            issue_strategy_counts[role]=issue_strategy_counts.get(role,0)+1
            if tr.get('side')=='sell':
                bucket=issue_trade_pnl.setdefault(action, {'sell_count':0,'pnl_sum':0.0})
                bucket['sell_count']+=1; bucket['pnl_sum']+=float(tr.get('pnl') or 0)
    for bucket in issue_trade_pnl.values():
        bucket['avg_pnl']=round(bucket['pnl_sum']/bucket['sell_count'],2) if bucket['sell_count'] else None
        bucket['pnl_sum']=round(bucket['pnl_sum'],2)
    fund_market_exposure=[]
    for f in standings:
        exposure={}
        for h in f.get('holdings',[]):
            exposure[market_of_symbol(h.get('symbol'))]=exposure.get(market_of_symbol(h.get('symbol')),0)+float(h.get('market_value') or 0)
        fund_market_exposure.append({'fund_id':f.get('id'),'style':f.get('style'),'return_pct':f.get('return_pct'),'market_value_by_market':exposure})
    market_breakdown={'symbol_counts':market_counts,'asset_type_counts':asset_type_counts,'trade_counts':trade_market_counts,'top_fund_market_exposure':fund_market_exposure[:10]}
    factor_available=sum(1 for x in factor_snapshot.values() if x.get('available'))
    factor_tag_counts={}
    for x in factor_snapshot.values():
        for tag in x.get('tags') or []:
            factor_tag_counts[tag]=factor_tag_counts.get(tag,0)+1
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'paper_fund_price_direct_replay','real_trading':False,'authority':'paper_only_historical_price_replay_no_orders','requested_days':args.days,'start_date':start_date.isoformat(),'end_date':end_date.isoformat(),'business_timezone':REPLAY_BUSINESS_TZ,'replay_complete_through_end_date':replay_complete,'recommendation_eligible':recommendation_eligible,'latest_replay_date':latest_replay_date,'trading_days':len(dates),'symbol_count':len(symbols),'universe_source':universe_source,'universe_symbols':symbols,'fund_count':len(active),'market_breakdown':market_breakdown,'fundamental_factor_model':{'enabled':True,'available_symbol_count':factor_available,'coverage_pct':round(factor_available/len(symbols)*100,2) if symbols else 0,'source':'latest_financial_quality plus cached yfinance valuation metrics','description':'Value, quality, GARP, dividend, and financial-safety fund styles reflect PER/PBR/ROE/EV-EBITDA/dividend yield/debt ratio/operating margin/growth as a capped paper-fund preference overlay.','styles':['value','quality','garp','dividend','financial_safety'],'metrics':['PER','PBR','ROE','EV/EBITDA','dividend yield','debt ratio','operating margin','revenue growth','operating income growth'],'score_policy':'style-specific capped factor overlay; paper fund preference only, no recommendation or order mutation','tag_counts':factor_tag_counts},'issue_context_model':{'source':'/tmp/next_trade_issue_context_latest.json','run_at':issue_context.get('run_at'),'by_action':issue_context.get('by_action'),'score_adjustment_policy':'style-aware, capped +3, block_or_avoid penalty','issue_action_trade_counts':issue_action_counts,'issue_strategy_role_counts':issue_strategy_counts,'issue_sell_pnl':issue_trade_pnl},'daily_market_context_model':{'market':'KR','source':'daily_market_context','coverage':market_context_coverage,'score_adjustment_policy':'style-aware capped -4..+3 overlay from risk_on, volatility, fx pressure, and breadth','us_broad_market_status':'pending_placeholder_disabled'},'strategy_role_counts':trade_strategy_counts,'fund_strategy_model':'fund_style_selects_strategy_role_per_signal_plus_issue_context_parameter_plus_fundamental_factor_preference_plus_daily_market_context','score_normalization':'kr_asset_percentile_rank','retire_pct':args.retire_pct,'min_age_days':args.min_age_days,'ranking_min_age_days':args.min_age_days,'evolve_every_days':args.evolve_every_days,'parent_selection_policy':{'policy':'mixed_parent_pool_v1','roles':['high_performance_mature','robust_mid_pack','diversity_underrepresented_style','random_immigrant','champion_clone'],'young_fund_guard':'30-60d funds remain ranking-eligible but cannot dominate parent selection; champion clones require age>=60'},'champion_challenger_policy':{'min_age_days':max(60,args.champion_min_age_days),'rank_cutoff':args.champion_rank_cutoff,'max_challengers_per_evolution':args.champion_challengers},'style_diversity_policy':{'min_survivors_per_style':'2 when active fund count supports at least two per archetype, otherwise 1','retirement_guard':'bottom-ranked funds are skipped when their style is at or below the survivor floor','debut_policy':'new children reseed underrepresented styles before cloning dominant styles'},'ranking_policy':{'min_age_days':args.min_age_days,'effect':'funds younger than min_age_days are probationary for registry/evaluator/consensus ranking'},'recommendation_eligibility_policy':{'requires_replay_complete_through_end_date':True,'requires_daily_market_context_coverage':'price_fx_index_issue','context_complete_through_end_date':context_complete_through_end,'recommendation_asof_date':recommendation_asof_date,'context_asof_date':context_asof_date,'context_lag_days':context_lag_days,'context_lag_policy':context_lag_policy,'context_lag_allowed':context_lag_allowed,'recommendation_eligible':recommendation_eligible,'market':'KR','us_broad_market_status':'pending_placeholder_disabled','start_date':start_date.isoformat(),'initial_capital':args.initial_capital,'effect':'fund outputs may influence recommendations only after KR replay is complete and daily_market_context is complete through the recommendation date, or during current-day/intraday replay uses the latest complete trading-day context without fabricating current-day context'},'retirement_policy':'mark_to_market_daily_retire_every_5_trading_days_after_min_age_days_bottom_retire_pct_with_style_diversity_guard_then_new_debut_plus_mixed_parent_pool_with_style_diversity_random_immigrants_and_age60_champion_clone_challengers','currency_model':{'base_currency':'KRW','us_assets_marked_to_krw':False,'fx_symbol':'KRW=X','fx_source':(fx_series.get('latest') or {}).get('source'),'latest_usdkrw':(fx_series.get('latest') or {}).get('rate'),'latest_fx_date':(fx_series.get('latest') or {}).get('date')},'cost_model':{'kr_commission_bps':args.kr_commission_bps,'kr_sell_tax_bps':args.kr_sell_tax_bps,'us_commission_bps':args.us_commission_bps,'us_sell_fee_bps':args.us_sell_fee_bps,'us_gain_tax_pct':args.us_gain_tax_pct,'us_gain_tax_note':'US broad-market fund replay disabled/pending in this phase','exit_price_logic':'daily high/low target/stop; stop first if both touched; signal decay exits at close','quantity_logic':'integer shares only; cash/equity/notional/costs floored to 100 KRW unit','position_management':'cash/exposure-bounded repeated averaging-down and pyramiding inside each fund seed; new entries use entry-plan fill modeling; defensive/dividend/financial_safety funds wait for target_buy_price intraday low touch; exits require at least one overnight hold after entry_date; entry_date is not reset by adds; target scale-out max once; trailing stop for trend/breakout/volume/quality/garp funds','score_normalization':'raw style score converted to percentile within KR asset bucket before fund selection','entry_execution_model':{'policy':'paper_entry_plan_intraday_low_touch_no_orders','defensive_dividend_financial_safety':'wait for target_buy_price; buy only when daily low touches it','balanced_mean_reversion_value':'wait for target buy or close inside acceptable entry zone','momentum_styles':'allow close fill if pullback zone does not touch, but record entry plan'} },'summary':{'top_fund':next((f for f in standings if f.get('ranking_eligible')), standings[0] if standings else None),'bottom_fund':standings[-1] if standings else None,'ranking_eligible_count':sum(1 for f in standings if f.get('ranking_eligible')),'probation_count':sum(1 for f in standings if not f.get('ranking_eligible')),'avg_return_pct':round(sum((x.get('return_pct') or 0) for x in standings)/len(standings),2) if standings else None,'trade_count':len(trades),'evolution_events':len(evol),'retired_count':sum(len(x.get('retired') or []) for x in evol),'debut_count':sum(len(x.get('debuted') or []) for x in evol),'champion_challenger_count':sum(len(x.get('champion_challengers') or []) for x in evol),'recommendation_eligible':recommendation_eligible},'standings':standings,'daily':daily[-260:],'evolution_events':evol[-50:],'trades':trades[-500:],'warnings':warnings,'next_actions':['Promote top KR replay archetypes into strategy/router candidates after repeated complete-context runs.']}
    attach_contract(packet,'paper_fund_price_replay_agent',status='degraded' if warnings else 'ok',outputs={'fund_count':len(active),'trading_days':len(dates)},metrics=packet['summary'],warnings=warnings,next_actions=packet['next_actions'])
    save_latest_artifact(Path(args.output).name.removesuffix('.json'), packet, artifact_path=args.output, status='degraded' if warnings else 'ok', summary=str(packet.get('summary') or '')[:1000])
    write_json(Path(args.output), packet); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
