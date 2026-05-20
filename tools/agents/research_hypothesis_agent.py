#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.database import init_db, list_strategy_registry, validation_coverage
from tools.agents.lib.agent_contract import attach_contract


def now(): return datetime.now(timezone.utc).isoformat()
def read_json(path):
    p=Path(path)
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc: return {'_read_error':str(exc),'_path':path}
def f(x, default=0.0):
    try: return float(x if x is not None else default)
    except Exception: return default

def blocker_tags(row):
    reason=str(row.get('reason') or '')
    tags=[]
    if 'recent rolling window deteriorated' in reason or f(row.get('recent_avg_excess_return_pct')) < 0: tags.append('recent_deterioration')
    if f(row.get('success_rate_pct')) < 35: tags.append('low_success_rate')
    if f(row.get('avg_excess_return_pct')) < 1: tags.append('weak_avg_excess')
    if int(row.get('samples') or 0) < 500: tags.append('sample_gap')
    if 'overselective' in reason: tags.append('overselective_signal_rate')
    return tags or ['near_threshold_quality']

def score(row):
    return round(f(row.get('avg_excess_return_pct'))*4 + f(row.get('recent_avg_excess_return_pct'))*2 + f(row.get('success_rate_pct'))/10 + min(8,int(row.get('samples') or 0)/200),2)


def experiment_history():
    data=read_json('/tmp/research_experiment_ledger.json')
    latest={}
    by_family_type={}
    for e in data.get('entries') or []:
        latest[(e.get('target'), e.get('experiment_type'))]=e
        fam=logic_family(e.get('target'))
        by_family_type[(fam, e.get('experiment_type'))]=e
    return {'exact':latest,'by_family_type':by_family_type}

def result_metric_delta(entry):
    metrics_list=(entry or {}).get('result_metrics') or []
    if isinstance(metrics_list, dict):
        metrics_list=[metrics_list]
    for metrics in metrics_list:
        if not isinstance(metrics, dict):
            continue
        saved=metrics.get('saved') if isinstance(metrics.get('saved'),dict) else {}
        for key in ('inserted','updated','audited_items','sample_count'):
            try:
                if float(metrics.get(key) or 0) > 0:
                    return True
            except Exception:
                pass
        for key in ('inserted','updated'):
            try:
                if float(saved.get(key) or 0) > 0:
                    return True
            except Exception:
                pass
    return False

def has_meaningful_delta(entry):
    d=(entry or {}).get('delta') or {}
    return bool(d.get('strategies') or d.get('recommendations') or result_metric_delta(entry))

def is_stale_repeat(entry):
    # If an experiment is seen again without a fresh state delta, rotate away
    # from it.  The ledger historically used both repeat_seen_count and a
    # boolean repeated flag, so honor either field.
    return bool(entry and (int(entry.get('repeat_seen_count') or 0) > 0 or entry.get('repeated') is True or entry.get('fuzzy_repeat_basis')))

def add_if_fresh(hyps, h, history, suppressed):
    k=(h.get('target'), h.get('experiment_type'))
    exact_history=history.get('exact', history) if isinstance(history, dict) else {}
    family_history=history.get('by_family_type', {}) if isinstance(history, dict) else {}
    prior=exact_history.get(k)
    fuzzy_prior=family_history.get((logic_family(h.get('target')), h.get('experiment_type')))
    if not prior and fuzzy_prior and h.get('experiment_type') not in ('theme_spillover_follow_through','mover_symbol_validation_boost','portfolio_exit_policy_retest'):
        prior=dict(fuzzy_prior)
        prior['fuzzy_repeat_basis']='logic_family_and_experiment_type'
    # Theme spillovers/current-day mover seeds are time-varying market-context hypotheses.
    # Portfolio exit-policy retests tied to current audit left-tail/negative-EV flags are also
    # allowed through even if the previous ledger marked no-delta; otherwise the profit guard can
    # identify the right fix but never schedule the bounded retest that may improve it.
    time_varying_or_guarded = ('theme_spillover_follow_through','mover_symbol_validation_boost','portfolio_exit_policy_retest')
    if prior and h.get('experiment_type') not in time_varying_or_guarded and (not has_meaningful_delta(prior) or is_stale_repeat(prior)):
        suppressed.append({'target':h.get('target'),'experiment_type':h.get('experiment_type'),'prior_run_at':prior.get('run_at'),'repeat_seen_count':prior.get('repeat_seen_count',0),'reason':'stale_repeated_experiment' if is_stale_repeat(prior) else 'repeated_without_delta'})
        return False
    if prior:
        h['repeat_context']={'prior_run_at':prior.get('run_at'),'prior_decision':prior.get('decision'),'had_delta':has_meaningful_delta(prior)}
    hyps.append(h); return True


def logic_family(logic):
    parts=str(logic or '').split('_')
    if not parts: return 'unknown'
    if parts[0]=='range': return 'range_grid'
    if parts[0]=='technical' and len(parts)>1: return '_'.join(parts[:2])
    if parts[0] in ('quality','volatility','relative','stable','pullback') and len(parts)>1: return '_'.join(parts[:2])
    return parts[0]

def build_hypotheses():
    strategies=list_strategy_registry(); recs=read_json('/tmp/recommendations_latest.json'); audit=read_json('/tmp/recommendation_audit_latest.json'); exit_policy_optimizer=read_json('/tmp/exit_policy_optimizer_latest.json'); orch=read_json('/tmp/research_org_orchestrator_latest.json'); shock=read_json('/tmp/market_shock_mover_scout_latest.json'); mover=read_json('/tmp/market_mover_seed_latest.json')
    active=[x for x in strategies if x.get('status')=='active']; pool=[x for x in strategies if x.get('status') in ('watch','probation','candidate')]
    hyps=[]; suppressed=[]; history=experiment_history()
    for r in sorted(pool,key=score,reverse=True)[:6]:
        tags=blocker_tags(r)
        if 'recent_deterioration' in tags:
            htype='exit_policy_retest'; owner='exit_policy_optimizer'; expected='reduce recent drawdown/tail risk without losing positive long-term excess'; criteria={'recent_avg_excess_return_pct':'>= 0','p10_excess_return_pct':'> -8'}
        elif 'low_success_rate' in tags:
            htype='entry_filter_retest'; owner='recommendation_audit'; expected='raise win-rate confidence by filtering weak entries'; criteria={'success_rate_wilson_low_pct':'>= 35','excess_win_rate_pct':'>= 50'}
        elif 'weak_avg_excess' in tags:
            htype='threshold_variant_retest'; owner='simulation_validation_worker'; expected='improve average excess while preserving sample size'; criteria={'avg_excess_return_pct':'>= 1','samples':'>= 500'}
        else:
            htype='near_threshold_validation'; owner='simulation_validation_worker'; expected='decide promote vs retire with more evidence'; criteria={'aggregate_quality_score':'>= 50'}
        add_if_fresh(hyps, {'id':f"hyp_{len(hyps)+1:03d}",'priority':'high' if len(hyps)<2 else 'medium','target_type':'strategy','target':r.get('logic'),'hypothesis':f"{r.get('logic')} may be unblocked by {htype} because blockers={','.join(tags)}.",'experiment_type':htype,'owner_agent':owner,'expected_improvement':expected,'success_criteria':criteria,'evidence':{'status':r.get('status'),'score':score(r),'samples':r.get('samples'),'success_rate_pct':r.get('success_rate_pct'),'avg_excess_return_pct':r.get('avg_excess_return_pct'),'recent_avg_excess_return_pct':r.get('recent_avg_excess_return_pct'),'blockers':tags,'reason':r.get('reason')}}, history, suppressed)
    for rec in [x for x in (recs.get('items') or []) if x.get('recommendation_bucket')=='paper_buy_candidate'][:4]:
        critic=rec.get('critic') or {}
        add_if_fresh(hyps, {'id':f"hyp_{len(hyps)+1:03d}",'priority':'high','target_type':'symbol','target':rec.get('symbol'),'hypothesis':f"{rec.get('symbol')} can move from paper_buy_candidate/research-only toward stronger committee support if symbol-specific validation clears under-validation.",'experiment_type':'symbol_validation_boost','owner_agent':'current_recommendation_validation','expected_improvement':'reduce under_validated critic issue and clarify paper-only eligibility','success_criteria':{'symbol_validation_sample_count':'>= 10','positive_symbol_edge_count':'>= 1'},'evidence':{'score':rec.get('score'),'bucket':rec.get('recommendation_bucket'),'critic_summary':critic.get('summary')}}, history, suppressed)

    best=(audit.get('summary') or {}).get('best') or {}
    if best.get('quality_flags'):
        add_if_fresh(hyps, {'id':f"hyp_{len(hyps)+1:03d}",'priority':'high' if {'left_tail_excess_risk','negative_expected_excess_value'}.intersection(set(best.get('quality_flags') or [])) else 'medium','target_type':'portfolio','target':best.get('logic') or (audit.get('summary') or {}).get('best_logic'),'hypothesis':'Current best logic has left-tail/EV flags; bounded exit-policy variants should be validated before stronger recommendations.','experiment_type':'portfolio_exit_policy_retest','owner_agent':'exit_policy_optimizer','expected_improvement':'improve expected excess value and reduce left-tail flags','success_criteria':{'expected_excess_value_pct':'> -1','p10_excess_return_pct':'> -8','quality_flags':'no negative_expected_excess_value'},'evidence':{'quality_score':best.get('quality_score'),'quality_flags':best.get('quality_flags'),'avg_excess_return_pct':best.get('avg_excess_return_pct'),'p10_excess_return_pct':best.get('p10_excess_return_pct'),'expected_excess_value_pct':best.get('expected_excess_value_pct'),'exit_suggestions':(exit_policy_optimizer.get('suggestions') or [])[:4]}}, history, suppressed)

    for mv in sorted([x for x in (mover.get('top_stock_items') or []) if abs(float(x.get('change_pct') or 0)) >= 10], key=lambda x: abs(float(x.get('change_pct') or 0)), reverse=True)[:6]:
        add_if_fresh(hyps, {'id':f"hyp_{len(hyps)+1:03d}",'priority':'high','target_type':'symbol','target':mv.get('symbol'),'hypothesis':f"{mv.get('symbol')} current mover seed should enter paper recommendation/strategy-promotion path only after symbol validation and strategy fit are verified.",'experiment_type':'mover_symbol_validation_boost','owner_agent':'current_recommendation_validation','expected_improvement':'convert market-wide shock discovery into validated paper recommendation candidates without direct trading authority','success_criteria':{'symbol_validation_sample_count':'>= 10','positive_symbol_edge_count':'>= 1','committee_bucket':'paper_buy_candidate_or_research_watch'},'evidence':{'name':mv.get('name'),'market':mv.get('market'),'direction':mv.get('direction'),'change_pct':mv.get('change_pct'),'source':mv.get('source'),'policy':'validation_priority_only'}}, history, suppressed)
    for sh in (shock.get('hypotheses') or [])[:4]:
        target=sh.get('target')
        if not target: continue
        add_if_fresh(hyps, {'id':f"hyp_{len(hyps)+1:03d}",'priority':sh.get('priority') or 'medium','target_type':'theme_spillover','target':target,'hypothesis':sh.get('hypothesis') or f"{target} spillover may have follow-through after after-close movers.",'experiment_type':'theme_spillover_follow_through','owner_agent':'market_shock_mover_scout','expected_improvement':'discover second-order/third-order sector flow candidates without direct recommendation authority','success_criteria':sh.get('success_criteria') or {'follow_through':'positive relative downstream response'},'evidence':sh.get('evidence') or {}}, history, suppressed)

    cov=validation_coverage()
    if len(hyps) < 4:
        family_counts={}
        pending=[]
        for existing in hyps:
            fam=logic_family(existing.get('target'))
            family_counts[fam]=family_counts.get(fam,0)+1
        for u in (cov.get('under_tested') or [])[:50]:
            logic=u.get('logic')
            if not logic: continue
            fam=logic_family(logic)
            h={'id':f"hyp_{len(hyps)+1:03d}",'priority':'medium','target_type':'strategy','target':logic,'hypothesis':f"{logic} is under-tested; targeted validation can decide whether it deserves watch/probation focus.",'experiment_type':'coverage_gap_validation','owner_agent':'simulation_validation_worker','expected_improvement':'reduce blind spots in strategy universe and discover overlooked active candidates','success_criteria':{'candidate_samples':'>= 500','avg_excess_return_pct':'>= 1'},'evidence':{**u,'family':fam}}
            if family_counts.get(fam,0) >= 2:
                pending.append(h)
                continue
            if add_if_fresh(hyps,h,history,suppressed):
                family_counts[fam]=family_counts.get(fam,0)+1
            if len(hyps) >= 6: break
        # If diversity limit leaves too few ideas, backfill from pending same-family
        # candidates rather than starving the autonomous loop.
        for h in pending:
            if len(hyps) >= 6: break
            add_if_fresh(hyps,h,history,suppressed)
    return hyps[:10], {'active_count':len(active),'candidate_pool_count':len(pool),'coverage':cov,'orchestrator_gap':(orch.get('active_pool_gap') or {}).get('gap'),'suppressed_repeat_count':len(suppressed),'suppressed_repeats':suppressed[:12]}

def main():
    ap=argparse.ArgumentParser(description='Generate bounded research hypotheses for paper_trader autonomous planning')
    ap.add_argument('--output',default='/tmp/research_hypotheses_latest.json'); args=ap.parse_args(); init_db()
    hyps, context=build_hypotheses()
    packet={'run_at':now(),'mode':'research_hypothesis_generation','real_trading':False,'authority':'propose_experiments_only','hypotheses':hyps,'summary':{'hypothesis_count':len(hyps),'high_priority_count':sum(1 for h in hyps if h.get('priority')=='high'),**context}}
    attach_contract(packet,'research_hypothesis_agent',status='ok',outputs={'hypothesis_count':len(hyps)},metrics=packet['summary'],warnings=[],next_actions=['Pass hypotheses to experiment planner; do not mutate strategy/recommendation state directly.'])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
