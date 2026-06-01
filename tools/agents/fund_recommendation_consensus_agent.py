#!/usr/bin/env python3
from __future__ import annotations
import json, sys, collections
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract
from app.database import latest_artifact, save_latest_artifact
from app.symbols import display_name
from tools.agents.lib.runtime import write_json, read_json

def artifact_key_from_path(path: str | Path) -> str:
    return Path(path).name.removesuffix('.json')

def load(p,d=None):
    try:
        db_payload = latest_artifact(artifact_key_from_path(p))
        if db_payload is not None:
            return db_payload
    except Exception:
        pass
    try: return read_json(p)
    except Exception: return d if d is not None else {}


def fund_age_days(f):
    try:
        return int(float(f.get('age_days') or 0))
    except Exception:
        return 0

def consensus_age_weight(age_days):
    age=int(age_days or 0)
    if age < 30:
        return 0.0, 'below_ranking_age_excluded'
    if age < 60:
        return 0.35, 'young_30_59d_reduced_weight'
    if age < 90:
        return 0.65, 'maturing_60_89d_partial_weight'
    return 1.0, 'mature_full_weight'

def yday_utc():
    return (datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat()

def trade_date(tr):
    return str(tr.get('date') or tr.get('run_at') or '')[:10]

def parse_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None

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

def _fund_by_id(funds):
    return {f.get('id'): f for f in funds if f.get('id')}

def support_type_for_row(row):
    buy_count=int(row.get('buy_fund_count') or 0)
    holding_count=int(row.get('holding_fund_count') or 0)
    supporting_count=len(row.get('participating_funds') or [])
    style_count=len(row.get('fund_styles') or {})
    weighted=float(row.get('weighted_score') or 0)
    if buy_count >= 2 and style_count >= 2:
        return 'cross_style_consensus'
    if supporting_count >= 3:
        return 'core_consensus'
    if buy_count >= 1 and weighted >= 1.0:
        return 'specialist_conviction'
    if buy_count >= 1:
        return 'tactical_candidate'
    return 'watch_only_candidate'

def support_breadth_score(row):
    supporting_count=len(row.get('participating_funds') or [])
    style_count=len(row.get('fund_styles') or {})
    buy_count=int(row.get('buy_fund_count') or 0)
    holding_count=int(row.get('holding_fund_count') or 0)
    weighted=float(row.get('weighted_score') or 0)
    score=min(100.0, weighted * 12.0 + buy_count * 12.0 + holding_count * 4.0 + supporting_count * 6.0 + style_count * 5.0)
    return round(score, 2)

def best_supporting_fund(row, top_by_id):
    details=list(row.get('fund_details') or [])
    if details:
        best=max(details, key=lambda d: (float(d.get('quality') or 0), float(d.get('score') or 0)))
        return {
            'fund_id': best.get('fund_id'),
            'style': best.get('style'),
            'quality': best.get('quality'),
            'age_days': best.get('age_days'),
            'age_weight': best.get('age_weight'),
            'source': 'recent_buy',
        }
    best_id=None
    best_quality=-1.0
    for fid in row.get('participating_funds') or []:
        quality=float((top_by_id.get(fid) or {}).get('fund_quality_score') or 0)
        if quality > best_quality:
            best_id=fid
            best_quality=quality
    f=top_by_id.get(best_id) or {}
    if not f:
        return None
    return {
        'fund_id': best_id,
        'style': f.get('style'),
        'quality': round(best_quality, 2),
        'age_days': fund_age_days(f),
        'age_weight': row.get('support_age_quality', {}).get('max_age_weight'),
        'source': 'holding',
    }

FUND_OVERLAY_EXPLANATIONS = {
    'macro_regime_overlay': {
        'label': 'Macro regime / rate-FX overlay',
        'description': 'Fed/BOK rates, US yield curve/real yield, and USD/KRW pressure are reflected as fund-consensus risk context.',
        'policy': 'display_and_fund_consensus_context_only_no_order_or_sizing_authority',
    },
    'fundamental_factor_overlay': {
        'label': 'Fundamental factor/style overlay',
        'description': 'Value, quality, GARP, dividend, and financial-safety paper-fund styles reflect PER/PBR/ROE/EV-EBITDA/dividend yield/debt ratio/operating margin/growth.',
        'styles': ['value', 'quality', 'garp', 'dividend', 'financial_safety'],
        'metrics': ['PER', 'PBR', 'ROE', 'EV/EBITDA', 'dividend yield', 'debt ratio', 'operating margin', 'growth'],
        'policy': 'paper_fund_preference_metadata_only_no_standalone_signal',
    },
}

def main():
    asof=yday_utc()
    perf=load('/tmp/fund_performance_evaluator_latest.json',{})
    risk=load('/tmp/fund_risk_guardian_latest.json',{})
    risk_guardrails=fund_risk_guardrails(risk)
    price=load('/tmp/paper_fund_price_replay_latest.json',{})
    fund_replay_complete=bool(price.get('replay_complete_through_end_date'))
    fund_replay_policy=price.get('recommendation_eligibility_policy') or {}
    fund_recommendation_eligible=bool(price.get('recommendation_eligible') or fund_replay_policy.get('recommendation_eligible'))
    recommendation_asof_date=fund_replay_policy.get('recommendation_asof_date') or price.get('latest_replay_date')
    context_asof_date=fund_replay_policy.get('context_asof_date')
    context_lag_days=fund_replay_policy.get('context_lag_days')
    context_lag_policy=fund_replay_policy.get('context_lag_policy')
    live=load('/tmp/paper_fund_simulator_latest.json',{})
    recs=load('/tmp/recommendations_latest.json',{})
    rec_by_symbol={r.get('symbol'):r for r in recs.get('items') or []}
    top=[]
    for f in (perf.get('evaluations') or []):
        if not fund_recommendation_eligible:
            continue
        if f.get('tier') not in ('champion','candidate'):
            continue
        guard=risk_guardrails.get(f.get('id')) or {}
        if guard.get('blocked'):
            continue
        ff=dict(f)
        if guard:
            ff['fund_risk_guardrail']=guard
        top.append(ff)
        if len(top) >= 12:
            break
    no_mature_top_funds = not bool(top)
    top_ids={f.get('id') for f in top if f.get('id')}
    top_quality={f.get('id'):float(f.get('fund_quality_score') or max(1,float(f.get('return_pct') or 0))) for f in top if f.get('id')}
    top_age_weight={}
    top_age_weight_reason={}
    for f in top:
        if not f.get('id'):
            continue
        w, reason = consensus_age_weight(fund_age_days(f))
        top_age_weight[f.get('id')] = w
        top_age_weight_reason[f.get('id')] = reason
    top_quality_cap={f.get('id'):(risk_guardrails.get(f.get('id')) or {}).get('allocation_cap_multiplier', 1.0) for f in top if f.get('id')}
    top_by_id=_fund_by_id(top)
    rows=collections.defaultdict(lambda:{'symbol':None,'buy_fund_count':0,'holding_fund_count':0,'weighted_score':0.0,'participating_funds':[],'fund_styles':collections.Counter(),'latest_buy_date':None,'sources':collections.Counter(),'buy_reasons':collections.Counter(),'fund_details':[]})
    # Yesterday / latest-available price replay buys. If yesterday has no data, use the latest replay date <= today.
    warnings=['uses_price_replay_top_fund_buy_proxy_until_live_holdings_mature']
    if not fund_recommendation_eligible:
        warnings.append('fund_replay_or_daily_market_context_not_complete; fund consensus excluded from recommendations')
    if context_lag_policy == 'allow_latest_complete_context_for_current_day_intraday_replay':
        warnings.append(f'current_day_context_incomplete_using_latest_complete_context: recommendation_asof_date={recommendation_asof_date} context_asof_date={context_asof_date} lag_days={context_lag_days}')
    if fund_replay_policy.get('us_broad_market_status'):
        warnings.append(f"us_broad_market_fund_replay_{fund_replay_policy.get('us_broad_market_status')}")
    if no_mature_top_funds:
        warnings.append('no_mature_champion_or_candidate_funds_after_min_age_gate')
    price_trades=price.get('trades') or []
    dates=sorted({trade_date(t) for t in price_trades if trade_date(t)})
    basis_date=max([d for d in dates if d <= datetime.now(timezone.utc).date().isoformat()] or dates[-1:] or [asof])
    basis_day=parse_date(basis_date)
    expected_day=datetime.now(timezone.utc).date()-timedelta(days=1)
    price_run_at=parse_date(price.get('run_at'))
    stale_replay=bool(basis_day and basis_day < expected_day)
    if stale_replay:
        warnings.append(f'price_replay_stale: basis_date {basis_date} older than expected {expected_day.isoformat()}')
    if price_run_at and price_run_at < expected_day:
        warnings.append(f'price_replay_artifact_stale: run_at {price.get("run_at")}')
    for tr in price_trades:
        if not top_ids: continue
        if tr.get('side')!='buy' or trade_date(tr)!=basis_date: continue
        fid=tr.get('fund_id')
        if fid not in top_ids: continue
        sym=tr.get('symbol')
        if not sym: continue
        row=rows[sym]; row['symbol']=sym
        if fid not in row['participating_funds']:
            row['buy_fund_count']+=1; row['participating_funds'].append(fid)
        cap=float(top_quality_cap.get(fid, 1.0) or 1.0)
        age_weight=float(top_age_weight.get(fid, 1.0) or 0.0)
        row['weighted_score'] += max(1, top_quality.get(fid, 5)/10) * cap * age_weight
        style=next((f.get('style') for f in top if f.get('id')==fid), None)
        if style: row['fund_styles'][style]+=1
        row['latest_buy_date']=max(row['latest_buy_date'] or trade_date(tr), trade_date(tr))
        row['sources']['price_replay_yday_buy']+=1
        if tr.get('reason'): row['buy_reasons'][tr.get('reason')]+=1
        guard=risk_guardrails.get(fid) or {}
        row['fund_details'].append({'fund_id':fid,'style':style,'buy_date':trade_date(tr),'buy_price':tr.get('price'),'reason':tr.get('reason'),'score':tr.get('score'),'quality':round(top_quality.get(fid,0),2),'age_days':fund_age_days(next((f for f in top if f.get('id')==fid), {})),'age_weight':age_weight,'age_weight_reason':top_age_weight_reason.get(fid),'risk_cap_multiplier':guard.get('allocation_cap_multiplier',1.0),'risk_issues':guard.get('issues') or [],'fundamental_factor':tr.get('fundamental_factor') or {},'style_overlay_description':FUND_OVERLAY_EXPLANATIONS['fundamental_factor_overlay']['description']})
    # Live holdings as secondary current asset context.
    live_state=load('/tmp/paper_fund_league_state.json',{})
    for f in live_state.get('funds') or []:
        if not top_ids: continue
        fid=f.get('id')
        if fid not in top_ids: continue
        for sym,pos in (f.get('positions') or {}).items():
            row=rows[sym]; row['symbol']=sym
            if fid not in row['participating_funds']: row['participating_funds'].append(fid)
            row['holding_fund_count']+=1
            age_weight=float(top_age_weight.get(fid, 1.0) or 0.0)
            row['weighted_score']+=max(1,top_quality.get(fid,3)/12) * float(top_quality_cap.get(fid, 1.0) or 1.0) * age_weight
            if f.get('style'): row['fund_styles'][f.get('style')]+=1
            row['sources']['live_holding']+=1
    items=[]
    for sym,row in rows.items():
        rec=rec_by_symbol.get(sym) or {}
        fund_ages=[fund_age_days(top_by_id.get(fid) or {}) for fid in row['participating_funds']]
        age_weights=[float(top_age_weight.get(fid, 0.0) or 0.0) for fid in row['participating_funds']]
        positive_fund_count=sum(1 for fid in row['participating_funds'] if float((top_by_id.get(fid) or {}).get('return_pct') or 0) > 0)
        high_quality_fund_count=sum(1 for fid in row['participating_funds'] if float((top_by_id.get(fid) or {}).get('fund_quality_score') or 0) >= 10)
        support_age_quality={
            'min_age_days': min(fund_ages) if fund_ages else None,
            'avg_age_days': round(sum(fund_ages)/len(fund_ages), 1) if fund_ages else None,
            'max_age_days': max(fund_ages) if fund_ages else None,
            'max_age_weight': max(age_weights) if age_weights else None,
            'positive_fund_count': positive_fund_count,
            'high_quality_fund_count': high_quality_fund_count,
        }
        row['support_age_quality']=support_age_quality
        supporting_fund_styles=dict(row['fund_styles'].most_common())
        support_type=support_type_for_row(row)
        support_breadth=support_breadth_score(row)
        best_fund=best_supporting_fund(row, top_by_id)
        item={
            'symbol':sym,
            'asof_date':basis_date,
            'buy_fund_count':row['buy_fund_count'],
            'holding_fund_count':row['holding_fund_count'],
            'supporting_fund_count':len(row['participating_funds']),
            'supporting_fund_styles':supporting_fund_styles,
            'best_supporting_fund':best_fund,
            'support_type':support_type,
            'support_breadth_score':support_breadth,
            'support_age_quality':support_age_quality,
            'candidate_generation':'union_of_top_fund_recent_buys_and_holdings',
            'weighted_score':round(row['weighted_score'],2),
            'participating_funds':row['participating_funds'][:12],
            'fund_styles':supporting_fund_styles,
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
            'name':rec.get('name') or display_name(sym),
            'market':rec.get('market'),
            'risk_guardrail_policy':'fund risk findings cap fund contribution weight; they do not create standalone trade signals',
            'overlay_explanations':FUND_OVERLAY_EXPLANATIONS,
            'risk_capped_funds':[fid for fid in row['participating_funds'] if (risk_guardrails.get(fid) or {}).get('allocation_cap_multiplier', 1.0) < 1.0],
            'caveat':'paper-only fund consensus; not real trading authority',
        }
        items.append(item)
    items=sorted(items,key=lambda x:(x['weighted_score'],x['support_breadth_score'],x['buy_fund_count'],x['holding_fund_count']),reverse=True)
    capped_fund_ids=sorted({fid for x in items for fid in (x.get('risk_capped_funds') or [])})
    support_type_counts=dict(collections.Counter(x.get('support_type') for x in items).most_common())
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'fund_recommendation_consensus','real_trading':False,'authority':'paper_only_top_fund_union_candidates_committee_reviewed','candidate_generation_policy':'union_candidates_from_top_fund_recent_buys_and_holdings_not_intersection_only','asof_date':basis_date,'price_replay_run_at':price.get('run_at'),'fund_replay_complete_through_end_date':fund_replay_complete,'fund_recommendation_eligible':fund_recommendation_eligible,'fund_replay_start_date':price.get('start_date'),'fund_replay_end_date':price.get('end_date'),'fund_replay_latest_date':price.get('latest_replay_date'),'recommendation_asof_date':recommendation_asof_date,'context_asof_date':context_asof_date,'context_lag_days':context_lag_days,'context_lag_policy':context_lag_policy,'recommendation_eligibility_policy':fund_replay_policy,'stale_replay':stale_replay,'item_count':len(items),'items':items,'summary':{'top_symbols':[x['symbol'] for x in items[:10]],'item_count':len(items),'risk_capped_fund_count':len(capped_fund_ids),'top_weighted_score':items[0]['weighted_score'] if items else None,'support_type_counts':support_type_counts,'basis':'union of latest available top-fund buys and holdings, normally yesterday close','stale_replay':stale_replay,'fund_recommendation_eligible':fund_recommendation_eligible,'recommendation_asof_date':recommendation_asof_date,'context_asof_date':context_asof_date,'context_lag_days':context_lag_days,'context_lag_policy':context_lag_policy},'overlay_explanations':FUND_OVERLAY_EXPLANATIONS,'consensus_age_weight_policy':{'ranking_min_age_days':30,'young_30_59d_weight':0.35,'maturing_60_89d_weight':0.65,'mature_90d_plus_weight':1.0,'effect':'young ranking-eligible funds remain visible but contribute reduced consensus score'},'risk_guardrail_policy':'fund risk findings cap consensus weight/allocation; they do not create standalone buy/sell signals','warnings':warnings,'next_actions':['Refresh paper_fund_price_replay before relying on fund recommendation consensus.' if stale_replay else 'Use union fund candidates as the primary recommendation view; committee separates core consensus, specialist conviction, tactical, and watch-only support.']}
    attach_contract(packet,'fund_recommendation_consensus_agent',status='degraded' if (stale_replay or not fund_recommendation_eligible) else 'ok',outputs={'item_count':len(items)},metrics=packet['summary'],warnings=packet['warnings'],next_actions=packet['next_actions'])
    save_latest_artifact('fund_recommendation_consensus_latest', packet, artifact_path='/tmp/fund_recommendation_consensus_latest.json', status='degraded' if (stale_replay or not fund_recommendation_eligible) else 'ok', summary=str(packet.get('summary') or '')[:1000])
    write_json(Path('/tmp/fund_recommendation_consensus_latest.json'), packet)
    write_json(Path(ROOT/'static/fund_recommendation_consensus_latest.json'), packet)
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
