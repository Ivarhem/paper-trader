#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract
from tools.agents.lib.runtime import read_json, write_json_and_static

DEFAULT_MIN_RANKING_AGE_DAYS = 30

def fund_age_days(f):
    try:
        return int(float(f.get('age_days') or 0))
    except Exception:
        return 0

def ranking_min_age(reg):
    for key in ('ranking_min_age_days', 'min_age_days'):
        try:
            value = int(float(reg.get(key) or 0))
            if value > 0:
                return value
        except Exception:
            pass
    return DEFAULT_MIN_RANKING_AGE_DAYS

def is_ranking_eligible(f, min_age):
    return fund_age_days(f) >= int(min_age or DEFAULT_MIN_RANKING_AGE_DAYS)

def load(p):
    try: return read_json(p)
    except Exception: return {}

def strategy_profile(f):
    allowed=set(f.get('allowed_strategy_roles') or [])
    mix={str(k):int(v or 0) for k,v in (f.get('strategy_mix') or {}).items()}
    pnl_by_role={}
    holding_count_by_role={}
    issue_mix={}
    issue_pnl_by_action={}
    for h in f.get('holdings') or []:
        role=h.get('strategy_role') or 'unknown'
        holding_count_by_role[role]=holding_count_by_role.get(role,0)+1
        pnl_by_role[role]=round(pnl_by_role.get(role,0.0)+float(h.get('unrealized_pnl') or 0),2)
        issue=(h.get('issue_context') or {})
        action=issue.get('action') or 'none'
        if issue:
            issue_mix[action]=issue_mix.get(action,0)+1
            issue_pnl_by_action[action]=round(issue_pnl_by_action.get(action,0.0)+float(h.get('unrealized_pnl') or 0),2)
        if not allowed:
            allowed.update(h.get('allowed_strategy_roles') or [])
    total=sum(mix.values())
    aligned=sum(v for k,v in mix.items() if k in allowed) if allowed else 0
    alignment_pct=round(aligned/total*100,2) if total else None
    dominant_role=max(mix.items(), key=lambda kv: kv[1])[0] if mix else None
    dominant_role_share_pct=round(max(mix.values())/total*100,2) if total else None
    positive_roles=sorted([{'strategy_role':k,'unrealized_pnl':v} for k,v in pnl_by_role.items() if v>0], key=lambda x:x['unrealized_pnl'], reverse=True)
    negative_roles=sorted([{'strategy_role':k,'unrealized_pnl':v} for k,v in pnl_by_role.items() if v<0], key=lambda x:x['unrealized_pnl'])
    return {
        'allowed_strategy_roles': sorted(allowed),
        'strategy_mix': mix,
        'strategy_role_count': len(mix),
        'alignment_pct': alignment_pct,
        'dominant_role': dominant_role,
        'dominant_role_share_pct': dominant_role_share_pct,
        'holding_count_by_role': holding_count_by_role,
        'unrealized_pnl_by_role': pnl_by_role,
        'top_positive_roles': positive_roles[:3],
        'top_negative_roles': negative_roles[:3],
        'issue_context_mix': issue_mix,
        'unrealized_pnl_by_issue_action': issue_pnl_by_action,
    }

def score(f, profile=None):
    profile = profile or strategy_profile(f)
    ret=float(f.get('return_pct') or 0); mdd=abs(float(f.get('mdd_pct') or 0)); age=float(f.get('age_days') or 0); trades=float(f.get('trade_count') or 0)
    base = ret - mdd*0.7 + min(age,120)*0.03 - max(0,trades/400-1)*2
    alignment = profile.get('alignment_pct')
    if alignment is not None:
        base += max(-5.0, min(4.0, (alignment - 75.0) / 8.0))
    role_count = int(profile.get('strategy_role_count') or 0)
    if role_count >= 2:
        base += min(2.0, (role_count - 1) * 0.6)
    dominant_share = profile.get('dominant_role_share_pct')
    if dominant_share is not None and dominant_share > 85 and role_count > 1:
        base -= 1.0
    return round(base,2)

def issue_action_from_context(issue):
    return (issue or {}).get('action') or 'none'

def add_issue_strategy_bucket(buckets, key, *, fund_id=None, symbol=None, side=None, pnl=0.0, unrealized_pnl=0.0):
    entry=buckets.setdefault(key, {
        'fund_count_set': set(),
        'symbol_count_set': set(),
        'holding_count': 0,
        'trade_count': 0,
        'buy_count': 0,
        'sell_count': 0,
        'realized_pnl_sum': 0.0,
        'unrealized_pnl_sum': 0.0,
    })
    if fund_id is not None:
        entry['fund_count_set'].add(str(fund_id))
    if symbol:
        entry['symbol_count_set'].add(str(symbol))
    if side == 'holding':
        entry['holding_count'] += 1
        entry['unrealized_pnl_sum'] += float(unrealized_pnl or 0)
    elif side:
        entry['trade_count'] += 1
        if side == 'buy':
            entry['buy_count'] += 1
        if side == 'sell':
            entry['sell_count'] += 1
            entry['realized_pnl_sum'] += float(pnl or 0)

def finalize_issue_strategy_buckets(buckets):
    rows=[]
    for key, entry in buckets.items():
        issue_action, strategy_role, fund_style = key
        row={
            'issue_action': issue_action,
            'strategy_role': strategy_role,
            'fund_style': fund_style,
            'fund_count': len(entry.pop('fund_count_set')),
            'symbol_count': len(entry.pop('symbol_count_set')),
            'holding_count': entry['holding_count'],
            'trade_count': entry['trade_count'],
            'buy_count': entry['buy_count'],
            'sell_count': entry['sell_count'],
            'realized_pnl_sum': round(entry['realized_pnl_sum'], 2),
            'unrealized_pnl_sum': round(entry['unrealized_pnl_sum'], 2),
        }
        row['total_pnl_observed'] = round(row['realized_pnl_sum'] + row['unrealized_pnl_sum'], 2)
        row['avg_realized_pnl_per_sell'] = round(row['realized_pnl_sum'] / row['sell_count'], 2) if row['sell_count'] else None
        row['avg_unrealized_pnl_per_holding'] = round(row['unrealized_pnl_sum'] / row['holding_count'], 2) if row['holding_count'] else None
        if row['sell_count'] < 5 and row['holding_count'] < 5:
            row['evidence_tier'] = 'thin_sample'
        elif row['total_pnl_observed'] > 0:
            row['evidence_tier'] = 'positive_observed'
        elif row['total_pnl_observed'] < 0:
            row['evidence_tier'] = 'negative_observed'
        else:
            row['evidence_tier'] = 'mixed_or_flat'
        rows.append(row)
    return sorted(rows, key=lambda x:(x['evidence_tier'] == 'positive_observed', x['total_pnl_observed'], x['sell_count'] + x['holding_count']), reverse=True)

def issue_strategy_outcomes(funds, trade_packet, min_age=DEFAULT_MIN_RANKING_AGE_DAYS):
    buckets={}
    eligible_fund_ids={f.get('id') for f in funds if is_ranking_eligible(f, min_age)}
    for f in funds:
        if f.get('id') not in eligible_fund_ids:
            continue
        fund_id=f.get('id')
        fund_style=f.get('style') or 'unknown'
        for h in f.get('holdings') or []:
            issue=h.get('issue_context') or {}
            if not issue:
                continue
            key=(issue_action_from_context(issue), h.get('strategy_role') or 'unknown', fund_style)
            add_issue_strategy_bucket(
                buckets,
                key,
                fund_id=fund_id,
                symbol=h.get('symbol'),
                side='holding',
                unrealized_pnl=h.get('unrealized_pnl'),
            )
    for tr in (trade_packet.get('items') or trade_packet.get('trades') or []):
        issue=tr.get('issue_context') or {}
        if not issue or tr.get('fund_id') not in eligible_fund_ids:
            continue
        key=(issue_action_from_context(issue), tr.get('strategy_role') or 'unknown', tr.get('fund_style') or 'unknown')
        add_issue_strategy_bucket(
            buckets,
            key,
            fund_id=tr.get('fund_id'),
            symbol=tr.get('symbol'),
            side=tr.get('side'),
            pnl=tr.get('pnl'),
        )
    return finalize_issue_strategy_buckets(buckets)

def main():
    reg=load('/tmp/fund_registry_latest.json'); funds=reg.get('funds') or []
    min_age=ranking_min_age(reg)
    trade_packet=load('/tmp/fund_trade_history_latest.json')
    rows=[]
    role_quality={}
    style_role_quality={}
    issue_action_quality={}
    issue_strategy_rows=issue_strategy_outcomes(funds, trade_packet, min_age)
    for f in funds:
        r=dict(f)
        profile=strategy_profile(r)
        ranking_eligible=is_ranking_eligible(r, min_age)
        r['strategy_effectiveness']=profile
        r['fund_quality_score']=score(r, profile)
        r['ranking_min_age_days']=min_age
        r['ranking_eligible']=ranking_eligible
        if not ranking_eligible:
            r['tier']='probation'
            r['tier_reason']='below_min_ranking_age'
        else:
            r['tier']='champion' if r['fund_quality_score']>=40 else ('candidate' if r['fund_quality_score']>=10 else ('watch' if r['fund_quality_score']>=0 else 'retire_pressure'))
            if r['tier'] == 'champion' and fund_age_days(r) < 60:
                r['tier_label']='young_champion'
                r['tier_reason']='champion_score_but_age_under_60_parent_clone_probation'
            elif r['tier'] == 'champion' and fund_age_days(r) < 90:
                r['tier_label']='maturing_champion'
                r['tier_reason']='champion_score_but_consensus_age_weight_reduced_until_90d'
            else:
                r['tier_label']=r['tier']
        rows.append(r)
        if not ranking_eligible:
            continue
        ret=float(r.get('return_pct') or 0)
        for action,count in (profile.get('issue_context_mix') or {}).items():
            ientry=issue_action_quality.setdefault(action, {'fund_count':0,'holding_count':0,'return_sum':0.0,'positive_fund_count':0,'unrealized_pnl_sum':0.0})
            ientry['fund_count'] += 1
            ientry['holding_count'] += int(count or 0)
            ientry['return_sum'] += ret
            ientry['unrealized_pnl_sum'] += float((profile.get('unrealized_pnl_by_issue_action') or {}).get(action) or 0)
            if ret > 0: ientry['positive_fund_count'] += 1
        for role,count in (profile.get('strategy_mix') or {}).items():
            entry=role_quality.setdefault(role, {'fund_count':0,'trade_count':0,'return_sum':0.0,'positive_fund_count':0})
            entry['fund_count'] += 1
            entry['trade_count'] += int(count or 0)
            entry['return_sum'] += ret
            if ret > 0: entry['positive_fund_count'] += 1
            sk=(r.get('style') or 'unknown', role)
            sentry=style_role_quality.setdefault(sk, {'style':r.get('style'),'strategy_role':role,'fund_count':0,'trade_count':0,'return_sum':0.0,'positive_fund_count':0})
            sentry['fund_count'] += 1
            sentry['trade_count'] += int(count or 0)
            sentry['return_sum'] += ret
            if ret > 0: sentry['positive_fund_count'] += 1
    rows=sorted(rows,key=lambda x:(bool(x.get('ranking_eligible')), x['fund_quality_score']),reverse=True)
    for entry in role_quality.values():
        entry['avg_return_pct']=round(entry['return_sum']/entry['fund_count'],2) if entry['fund_count'] else None
        entry['positive_rate_pct']=round(entry['positive_fund_count']/entry['fund_count']*100,2) if entry['fund_count'] else None
        entry.pop('return_sum',None)
    style_role_rows=[]
    for entry in style_role_quality.values():
        entry['avg_return_pct']=round(entry['return_sum']/entry['fund_count'],2) if entry['fund_count'] else None
        entry['positive_rate_pct']=round(entry['positive_fund_count']/entry['fund_count']*100,2) if entry['fund_count'] else None
        entry.pop('return_sum',None)
        style_role_rows.append(entry)
    issue_action_rows=[]
    for entry in issue_action_quality.values():
        entry['avg_return_pct']=round(entry['return_sum']/entry['fund_count'],2) if entry['fund_count'] else None
        entry['positive_rate_pct']=round(entry['positive_fund_count']/entry['fund_count']*100,2) if entry['fund_count'] else None
        entry['unrealized_pnl_sum']=round(entry['unrealized_pnl_sum'],2)
        entry.pop('return_sum',None)
        issue_action_rows.append(entry)
    issue_action_rows=sorted(([{'issue_action':k, **v} for k,v in issue_action_quality.items()]), key=lambda x:(x.get('avg_return_pct') if x.get('avg_return_pct') is not None else -999, x.get('holding_count') or 0), reverse=True)
    role_quality_rows=sorted(([{'strategy_role':k, **v} for k,v in role_quality.items()]), key=lambda x:(x.get('avg_return_pct') if x.get('avg_return_pct') is not None else -999, x.get('trade_count') or 0), reverse=True)
    style_role_rows=sorted(style_role_rows, key=lambda x:(x.get('avg_return_pct') if x.get('avg_return_pct') is not None else -999, x.get('trade_count') or 0), reverse=True)
    warnings=[]
    if rows and not any((r.get('strategy_effectiveness') or {}).get('strategy_mix') for r in rows):
        warnings.append('strategy_mix_missing_from_fund_registry; run updated paper_fund_price_replay_agent')
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'fund_performance_evaluator','real_trading':False,'authority':'paper_only_fund_quality_gate','ranking_policy':{'min_age_days':min_age,'immature_tier':'probation','young_champion_label':'tier remains champion for existing consumers; tier_label marks young_champion under 60d and maturing_champion under 90d','effect':'below-min-age funds are visible but excluded from champion/candidate tiers, top rank, role quality, and issue strategy quality'},'summary':{'fund_count':len(rows),'ranking_min_age_days':min_age,'ranking_eligible_count':sum(1 for r in rows if r.get('ranking_eligible')),'probation_count':sum(1 for r in rows if r.get('tier')=='probation'),'champion_count':sum(1 for r in rows if r['tier']=='champion'),'candidate_count':sum(1 for r in rows if r['tier']=='candidate'),'top_fund':next((r for r in rows if r.get('ranking_eligible')), rows[0] if rows else None),'top_strategy_roles':role_quality_rows[:6],'top_style_strategy_pairs':style_role_rows[:6],'top_issue_actions':issue_action_rows[:6],'top_issue_strategy_pairs':issue_strategy_rows[:8]},'evaluations':rows,'strategy_role_quality':role_quality_rows,'style_strategy_role_quality':style_role_rows,'issue_action_quality':issue_action_rows,'issue_strategy_outcomes':issue_strategy_rows,'issue_strategy_outcome_model':{'source':'fund holdings plus fund trade history','purpose':'Measure which issue actions led funds to choose which strategy roles and how those choices performed.','promotion_policy':'paper evidence only; thin samples cannot upweight live decisions without repeated validation.'},'warnings':warnings,'next_actions':['Favor champion/candidate fund styles; keep retire_pressure funds from influencing recommendations.','Use strategy_role_quality to separate fund DNA from tactical strategy roles.','Use issue_strategy_outcomes to learn which issue types should route to trend, breakout, mean-reversion, or defensive roles after enough samples.','Investigate high-return funds whose dominant strategy role is outside their declared style roles.']}
    attach_contract(packet,'fund_performance_evaluator_agent',status='degraded' if warnings else 'ok',outputs={'fund_count':len(rows),'strategy_role_count':len(role_quality_rows),'issue_strategy_pair_count':len(issue_strategy_rows)},metrics=packet['summary'],warnings=warnings,next_actions=packet['next_actions'])
    write_json_and_static('/tmp/fund_performance_evaluator_latest.json', packet)
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
