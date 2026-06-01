#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sqlite3,sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, list_strategy_registry
from tools.agents.lib.agent_contract import attach_contract, write_json_shared

OUT='/tmp/strategy_context_router_latest.json'

def load(path, default=None):
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception: return default if default is not None else {}

def pct(v, default=0.0):
    try: return float(v if v is not None else default)
    except Exception: return default

def logic_family(logic:str)->str:
    l=(logic or '').lower()
    if 'relative_strength' in l or 'momentum' in l or 'ma_trend' in l: return 'trend_strength'
    if 'breakout' in l or 'volume' in l: return 'breakout_volume'
    if 'rsi' in l or 'reversion' in l or 'oversold' in l: return 'mean_reversion'
    if 'range' in l or 'grid' in l: return 'range_grid'
    return 'general'

def current_regime():
    m=load('/tmp/market_context_latest.json')
    rg=load('/tmp/regime_segmentation_latest.json')
    summary=m.get('summary') or m.get('market_summary') or {}
    text=json.dumps(summary,ensure_ascii=False).lower()
    risk_off=any(x in text for x in ['risk_off','약세','selloff','bear','down'])
    risk_on=any(x in text for x in ['risk_on','강세','bull','uptrend','상승'])
    if risk_off: regime='risk_off_or_weak'
    elif risk_on: regime='risk_on_or_strong'
    else: regime='mixed_or_unknown'
    return {'regime':regime,'market_context_run_at':m.get('run_at'),'segmentation_run_at':rg.get('run_at')}

def family_bias(regime):
    if regime=='risk_on_or_strong':
        return {'trend_strength':1.10,'breakout_volume':1.08,'range_grid':0.98,'mean_reversion':0.94,'general':1.0}
    if regime=='risk_off_or_weak':
        return {'mean_reversion':1.05,'range_grid':1.03,'trend_strength':0.92,'breakout_volume':0.94,'general':0.98}
    return {'trend_strength':1.0,'breakout_volume':1.0,'range_grid':1.0,'mean_reversion':1.0,'general':1.0}


def audit_context_score(q: dict, fam: str, regime: str) -> dict:
    role=(q.get('strategy_role_profile') or {})
    axes=role.get('trust_axes') or {}
    labels=set(role.get('role_labels') or [])
    best_use=role.get('best_use')
    flags=set(q.get('quality_flags') or [])
    has_contract=bool(axes or labels or best_use or q.get('quality_flags'))
    def axis(name, default=50.0):
        try: return float(axes.get(name) if axes.get(name) is not None else default)
        except Exception: return default
    # Router should prefer context-fit and tail-safe strategies in the current
    # regime, not simply the highest average historical return.
    score=0.0; reasons=[]
    score += (axis('return_edge')-50)*0.35
    score += (axis('confidence')-50)*0.25
    score += (axis('tail_safety')-50)*0.35
    score += (axis('regime_fit')-50)*0.45
    score += (axis('execution_reliability')-50)*0.20
    score += (axis('consistency')-50)*0.25
    if not has_contract:
        score -= 8
        reasons.append('missing_audit_context_contract')
    if 'risk_adjusted_candidate' in labels:
        score += 8; reasons.append('risk_adjusted_candidate')
    if 'left_tail_risk' in labels or 'left_tail_excess_risk' in flags:
        score -= 9; reasons.append('left_tail_risk')
    if 'weak_excess' in labels or 'negative_expected_excess_value' in flags:
        score -= 7; reasons.append('weak_expected_excess')
    if 'context_sensitive_avoid' in labels:
        score -= 6; reasons.append('context_sensitive_avoid')
    if 'research_only' in labels:
        score -= 3; reasons.append('research_only')
    if best_use == 'avoid_or_small_research_weight':
        score -= 10; reasons.append('avoid_or_small_research_weight')
    elif best_use in ('candidate_generation','risk_adjusted_candidate_generation','fund_selection_support'):
        score += 5; reasons.append(str(best_use))
    elif best_use == 'defensive_or_risk_control_sleeve':
        score += 2; reasons.append('defensive_or_risk_control_sleeve')
    if regime == 'risk_on_or_strong' and fam in ('trend_strength','breakout_volume'):
        score += 4; reasons.append('regime_family_fit')
    elif regime == 'risk_off_or_weak' and fam in ('mean_reversion','range_grid'):
        score += 4; reasons.append('regime_family_fit')
    elif regime in ('risk_on_or_strong','risk_off_or_weak') and fam != 'general':
        score -= 2; reasons.append('regime_family_mismatch')
    return {'score': round(score,2), 'trust_axes': axes, 'role_labels': sorted(labels), 'best_use': best_use, 'reasons': reasons[:8]}


def main():
    ap=argparse.ArgumentParser(description='Select strategy/context parameter arms before recommendation generation')
    ap.add_argument('--output',default=OUT)
    args=ap.parse_args(); init_db()
    regime=current_regime(); bias=family_bias(regime['regime'])
    audit=load('/tmp/recommendation_audit_latest.json')
    by_logic=(audit.get('summary') or {}).get('by_logic') or {}
    strategies=list_strategy_registry()
    selections=[]; blocked=[]
    for s in strategies:
        logic=s.get('logic'); status=s.get('status')
        if status not in ('active','repair_active','validation_active','watch','probation'): continue
        fam=logic_family(logic); q=by_logic.get(logic) or {}
        avg=pct(s.get('avg_excess_return_pct')); win=pct((s.get('summary') or {}).get('excess_win_rate_pct'), pct(s.get('success_rate_pct'),50)); samples=int(s.get('samples') or 0)
        quality=pct(q.get('quality_score'),50)
        context=audit_context_score(q, fam, regime['regime'])
        # Keep legacy return/win data as a weak tiebreaker; context audit is the primary router signal.
        legacy_component=avg*0.8 + (win-50)*0.12 + min(5,samples/80) + (quality-50)*0.08
        score=(50 + context['score'] + legacy_component) * bias.get(fam,1.0)
        reasons=[f'family={fam}',f'regime={regime["regime"]}',f'bias={bias.get(fam,1.0)}']
        reasons.extend(context.get('reasons') or [])
        if avg>0: reasons.append(f'legacy_avg_excess {round(avg,2)}%')
        if win>=52: reasons.append(f'legacy_win/excess {round(win,1)}%')
        decision='prefer' if score>=62 else ('allow' if score>=43 else 'deprioritize')
        row={'logic':logic,'status':status,'family':fam,'router_score':round(score,2),'decision':decision,'score_multiplier':1.12 if decision=='prefer' else (0.88 if decision=='deprioritize' else 1.0),'reasons':reasons[:8],'regime':regime['regime'],'samples':samples,'avg_excess_return_pct':round(avg,2),'win_rate_pct':round(win,2),'audit_quality_score':quality,'audit_context_score':context}
        selections.append(row)
        if decision=='deprioritize': blocked.append(row)
    selections=sorted(selections,key=lambda x:x['router_score'],reverse=True)
    by_logic_out={x['logic']:x for x in selections}
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'strategy_context_router','real_trading':False,'authority':'proposal_only_strategy_selection_meta_layer','regime_context':regime,'summary':{'selection_count':len(selections),'prefer_count':sum(1 for x in selections if x['decision']=='prefer'),'deprioritize_count':sum(1 for x in selections if x['decision']=='deprioritize'),'top_logic':[x['logic'] for x in selections[:5]]},'by_logic':by_logic_out,'selections':selections[:80],'warnings':[],'next_actions':['Use router multipliers in recommendations and record context x strategy outcomes.']}
    attach_contract(packet,'strategy_context_router_agent',status='ok',outputs={'selection_count':len(selections)},metrics=packet['summary'],warnings=[],next_actions=packet['next_actions'])
    write_json_shared(args.output, packet)
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
