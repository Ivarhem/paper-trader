#!/usr/bin/env python3
from __future__ import annotations
import json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, utc_now
from tools.agents.lib.agent_contract import attach_contract
from tools.agents.lib.indicator_taxonomy import classify_indicator_logic

MIN_ACTIVE_FLOOR = 2

def success_optimizer_plan():
    p=Path('/tmp/strategy_success_optimizer_latest.json')
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8')).get('action_plan') or {}
    except Exception: return {}



def percentile(sorted_values, q: float, default=0):
    if not sorted_values:
        return default
    idx=max(0,min(len(sorted_values)-1,int((len(sorted_values)-1)*q)))
    return round(float(sorted_values[idx]),2)

def payoff_profile(logic: str, samples: int, sr: float, avg_excess: float, recent_excess: float, excess_win_rate: float, expected_excess_value: float, p10_excess: float, p25_excess: float, stable_periods: int, signal_rate: float) -> dict:
    indicator_meta = classify_indicator_logic(logic)
    is_technical = bool(indicator_meta.get('is_technical_indicator')) or 'data_only' in logic or logic.startswith(('volatility_contraction_breakout','pullback_uptrend','relative_strength_persistence'))
    flags=[]
    if is_technical: flags.append('technical_data_only')
    if sr < 38 and avg_excess > 0 and excess_win_rate >= 50:
        flags.append('low_hit_high_excess')
    if p10_excess < -10 or p25_excess < -5:
        flags.append('left_tail_risk')
    if recent_excess < -2:
        flags.append('recent_decay')
    if stable_periods < 2:
        flags.append('period_instability')
    if signal_rate < 5:
        flags.append('low_signal_rate')
    reward_risk = round((avg_excess / abs(p25_excess)), 2) if p25_excess < 0 else None
    if avg_excess > 0 and expected_excess_value > 0 and sr >= 40 and p10_excess > -8 and stable_periods >= 2:
        cls='high_conviction'
    elif avg_excess > 0 and excess_win_rate >= 50 and sr < 40 and p10_excess > -15 and stable_periods >= 1:
        cls='asymmetric_alpha'
    elif avg_excess > 0 and (p10_excess <= -10 or stable_periods < 2 or recent_excess < 0):
        cls='fragile_alpha'
    else:
        cls='overfit_or_noise'
    role = 'primary_candidate_allowed' if cls == 'high_conviction' else ('supporting_alpha_only' if cls == 'asymmetric_alpha' else 'watch_only_risk_context')
    position_size_hint = 'normal' if cls == 'high_conviction' else ('small' if cls == 'asymmetric_alpha' else 'avoid_or_tiny')
    return {'class':cls,'technical_signal_role':role,'position_size_hint':position_size_hint,'reward_risk_proxy':reward_risk,'indicator_family':indicator_meta.get('indicator_family'),'indicator_role':indicator_meta.get('indicator_role'),'indicator_components':indicator_meta.get('indicator_components'),'flags':flags,'lookahead_safety':{'entry_timing':'next_bar_after_signal_close','uses_future_data':False,'same_bar_exit_assumed':False,'close_confirmation_required':True},'validation_split':{'in_sample':'historical_cutoffs_before_recent_window','forward_recent':'latest_50_candidate_buy_zone_results','recent_avg_excess_return_pct':recent_excess}}

def lifecycle_score(row):
    summary=row.get('summary') or {}
    return float(summary.get('aggregate_quality_score') or 0) * 0.8 + float(summary.get('expected_excess_value_pct') or row.get('avg_excess_return_pct') or 0) * 8 + float(row.get('recent_avg_excess_return_pct') or 0) * 2 + min(10, int(row.get('samples') or 0) / 40)


def status_for(samples, sr, avg_excess, recent_sr, recent_excess, excess_win_rate, old_status=None, aggregate_quality_score=0, expected_excess_value=0, p10_excess=0, concentration=100, stable_periods=0, signal_rate=100):
    if samples < 30:
        return 'candidate', 'not enough samples'
    # Profit guard: active hysteresis must not preserve strategies whose current
    # evidence shows a negative expected excess value together with a severe left
    # tail. Route them to probation/watch and let exit-policy retests repair them
    # before they can re-enter active paper research.
    if (expected_excess_value or 0) < -3 and (p10_excess or 0) < -10:
        if samples >= 250 and (avg_excess or 0) > 0 and (recent_excess or 0) >= -4:
            return 'repair_active', 'repair_active: positive aggregate edge but negative EV/severe left-tail; watch-only exit-policy retest lane, not active approval'
        return 'probation', 'profit guard: negative expected excess value plus severe left-tail; exit-policy retest required before active'
    if (expected_excess_value or 0) < 0 and (p10_excess or 0) < -12:
        if samples >= 250 and (avg_excess or 0) > 0 and (recent_excess or 0) >= -4:
            return 'repair_active', 'repair_active: positive aggregate edge but weak EV/tail; watch-only repair lane until exit-policy evidence improves'
        return 'probation', 'profit guard: negative EV and p10 tail below -12%; keep research-only until repair evidence improves'
    if signal_rate < 2:
        return 'hold', 'overselective strategy: candidate_buy_zone signal rate below 2% of audited opportunities'
    if signal_rate < 5:
        return 'watch', 'overselective strategy: candidate_buy_zone signal rate below 5% of audited opportunities'
        return 'candidate', 'not enough samples'
    if aggregate_quality_score >= 68 and (expected_excess_value or 0) > 0 and (p10_excess or 0) > -6 and concentration <= 45 and stable_periods >= 2 and recent_sr >= 35 and (recent_excess or 0) >= -2:
        return 'active', 'passes sample, success, excess-return, excess-win-rate and recent-window checks'

    # Hysteresis must run before generic deterioration/probation checks. These
    # active states are intentionally isolated paper-research tiers; demoting and
    # re-promoting them every cron creates false "new active" events without new
    # information. Keep them active unless recent/overall evidence has clearly
    # broken the exploration thesis.
    if old_status == 'active' and samples >= 300 and aggregate_quality_score >= 60 and (expected_excess_value or 0) > -0.5 and (recent_excess or 0) > 0 and concentration <= 55:
        return 'active', 'active hysteresis: reserve-quality strategy remains active to avoid lifecycle/balancer churn'
    if old_status == 'active' and samples >= 300 and aggregate_quality_score >= 52 and (expected_excess_value or 0) > -1.0 and recent_sr >= 30 and (recent_excess or 0) >= -3:
        return 'active', 'active hysteresis: probationary paper strategy remains active while gathering live recommendation evidence'
    if old_status == 'active' and samples >= 250 and aggregate_quality_score >= 48 and (avg_excess or 0) >= 0.5 and (recent_excess or 0) >= -3:
        return 'active', 'active hysteresis: high-upside paper strategy remains active in isolated exploration tier'
    if old_status == 'active' and samples >= 30 and (avg_excess or 0) >= 0.5 and excess_win_rate >= 49 and sr >= 10 and (recent_excess or 0) >= -1.0:
        return 'active', 'active hysteresis: paper strategy keeps research slot while broad excess remains positive and recent excess is only mildly weak'

    if recent_sr < 35 or (recent_excess or 0) < -5:
        return 'probation', 'recent rolling window deteriorated'
    if sr < 40 or (avg_excess or 0) < 0:
        return 'watch', 'overall validation is weak'
    return 'watch', 'mixed validation profile'


def main():
    init_db(); conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    plan=success_optimizer_plan()
    isolate_logics={x.get('logic'): x for x in (plan.get('demote_or_isolate_logics') or [])}
    logics=[r['logic'] for r in conn.execute('SELECT DISTINCT logic FROM recommendation_validation_results ORDER BY logic').fetchall()]
    out=[]
    for logic in logics:
        rows=conn.execute("SELECT * FROM recommendation_validation_results WHERE logic=? AND action='candidate_buy_zone' ORDER BY cutoff ASC,id ASC",(logic,)).fetchall()
        opp_row=conn.execute("SELECT COUNT(*) AS n FROM recommendation_validation_results WHERE logic=?",(logic,)).fetchone()
        opportunity_count=int(opp_row['n'] or 0) if opp_row else 0
        samples=len(rows)
        signal_rate=round(samples/opportunity_count*100,2) if opportunity_count else 0
        if not samples: continue
        success=sum(1 for r in rows if r['result']=='success')
        sr=round(success/samples*100,2)
        eval_positive=eval_neutral=eval_negative=eval_excluded=0
        for r in rows:
            ev=None
            try:
                import json as _json
                ev=(_json.loads(r['payload_json'] or '{}') or {}).get('evaluation_result')
            except Exception:
                ev=None
            if ev == 'positive': eval_positive += 1
            elif ev == 'neutral': eval_neutral += 1
            elif ev == 'negative': eval_negative += 1
            elif ev == 'excluded': eval_excluded += 1
        eval_count=eval_positive+eval_neutral+eval_negative
        evaluation_success_rate=round(eval_positive/eval_count*100,2) if eval_count else None
        excess_values=[float(r['excess_return_pct'] or 0) for r in rows]
        avg_excess=round(sum(excess_values)/samples,2)
        excess_win_rate=round(sum(1 for v in excess_values if v > 0)/samples*100,2)
        recent=rows[-50:] if len(rows)>=50 else rows
        recent_sr=round(sum(1 for r in recent if r['result']=='success')/len(recent)*100,2)
        recent_excess=round(sum(float(r['excess_return_pct'] or 0) for r in recent)/len(recent),2)
        excess_vals=sorted(float(r['excess_return_pct'] or 0) for r in rows)
        p10_excess=percentile(excess_vals,0.10)
        p25_excess=percentile(excess_vals,0.25)
        by_period={}
        for r in rows:
            by_period.setdefault(str(r['cutoff'])[:4],[]).append(r)
        stable_periods=sum(1 for arr in by_period.values() if len(arr)>=5 and sum(float(x['excess_return_pct'] or 0) for x in arr)/len(arr)>0)
        by_symbol_counts={}
        for r in rows:
            by_symbol_counts[r['symbol']]=by_symbol_counts.get(r['symbol'],0)+1
        concentration=round(max(by_symbol_counts.values(), default=0)/samples*100,2) if samples else 100
        market_profile={}
        for mk, arr in (('KR',[r for r in rows if str(r['symbol']).endswith(('.KS','.KQ'))]),('US',[r for r in rows if not str(r['symbol']).endswith(('.KS','.KQ'))])):
            if not arr:
                continue
            ex_vals=[float(r['excess_return_pct'] or 0) for r in arr]
            ev_pos=ev_neu=ev_neg=0
            for r in arr:
                try:
                    import json as _json
                    ev=(_json.loads(r['payload_json'] or '{}') or {}).get('evaluation_result')
                except Exception:
                    ev=None
                if ev == 'positive': ev_pos += 1
                elif ev == 'neutral': ev_neu += 1
                elif ev == 'negative': ev_neg += 1
            ev_n=ev_pos+ev_neu+ev_neg
            market_profile[mk]={
                'samples':len(arr),
                'success_rate_pct':round(sum(1 for r in arr if r['result']=='success')/len(arr)*100,2),
                'avg_excess_return_pct':round(sum(ex_vals)/len(ex_vals),2),
                'excess_win_rate_pct':round(sum(1 for v in ex_vals if v>0)/len(ex_vals)*100,2),
                'evaluation_success_rate_pct':round(ev_pos/ev_n*100,2) if ev_n else None,
            }
        fail_rate=round(sum(1 for r in rows if r['result']=='fail')/samples*100,2) if samples else 0
        expected_excess_value=round(avg_excess - max(0, -p10_excess)*0.35 - fail_rate*0.03,2)
        diversity_score=round(min(100, len(by_symbol_counts)*8) - max(0, concentration-25),2)
        aggregate_quality_score=round(max(0,min(100, (avg_excess*10) + (expected_excess_value*10) + diversity_score*0.2 + stable_periods*6 + min(20,samples/20) - max(0,-p10_excess)*2 - max(0,concentration-45)*0.6)),2)
        old=conn.execute('SELECT status FROM strategy_registry WHERE logic=?',(logic,)).fetchone()
        old_status=old['status'] if old else None
        status, reason=status_for(samples,sr,avg_excess,recent_sr,recent_excess,excess_win_rate,old_status,aggregate_quality_score,expected_excess_value,p10_excess,concentration,stable_periods,signal_rate)
        if logic in isolate_logics and status == 'active':
            status, reason = 'watch', 'historical success optimizer isolated active strategy: ' + (isolate_logics[logic].get('reason') or 'fails historical gate')
        if old_status == 'hold' and status != 'active':
            status, reason = 'hold', 'preserve novelty-pruner hold until distinct positive evidence qualifies for active'
        def compact(r):
            return {
                'symbol': r['symbol'], 'cutoff': r['cutoff'], 'horizon_days': r['horizon_days'],
                'result': r['result'], 'entry': r['entry'], 'target': r['target'], 'stop': r['stop'],
                'final_return_pct': r['final_return_pct'], 'excess_return_pct': r['excess_return_pct'],
            }
        successes=[compact(r) for r in rows if r['result']=='success'][-5:]
        failures=[compact(r) for r in rows if r['result']=='fail'][-5:]
        by_symbol={}
        for r in rows:
            by_symbol.setdefault(r['symbol'],[]).append(r)
        symbol_notes=[]
        for s, arr in by_symbol.items():
            if len(arr)<3: continue
            ss=sum(1 for r in arr if r['result']=='success')
            ex=round(sum(float(r['excess_return_pct'] or 0) for r in arr)/len(arr),2)
            symbol_notes.append({'symbol':s,'samples':len(arr),'success_rate_pct':round(ss/len(arr)*100,2),'avg_excess_return_pct':ex})
        symbol_notes=sorted(symbol_notes,key=lambda x:x['avg_excess_return_pct'],reverse=True)
        flags=[]
        if signal_rate < 2: flags.append('extremely_low_signal_rate')
        elif signal_rate < 5: flags.append('low_signal_rate')
        payoff=payoff_profile(logic,samples,sr,avg_excess,recent_excess,excess_win_rate,expected_excess_value,p10_excess,p25_excess,stable_periods,signal_rate)
        indicator_meta=classify_indicator_logic(logic)
        summary={'strengths':symbol_notes[:3],'weaknesses':symbol_notes[-3:],'recent_successes':successes,'recent_failures':failures,'excess_win_rate_pct':excess_win_rate,'market_profile':market_profile,'evaluation_success_rate_pct':evaluation_success_rate,'evaluation_sample_count':eval_count,'evaluation_positive':eval_positive,'evaluation_neutral':eval_neutral,'evaluation_negative':eval_negative,'evaluation_excluded':eval_excluded,'p10_excess_return_pct':p10_excess,'p25_excess_return_pct':p25_excess,'payoff_profile':payoff,'technical_signal_role':payoff.get('technical_signal_role'),'indicator_family':indicator_meta.get('indicator_family'),'indicator_role':indicator_meta.get('indicator_role'),'indicator_components':indicator_meta.get('indicator_components'),'position_size_hint':payoff.get('position_size_hint'),'lookahead_safety':payoff.get('lookahead_safety'),'validation_split':payoff.get('validation_split'),'expected_excess_value_pct':expected_excess_value,'aggregate_quality_score':aggregate_quality_score,'symbol_concentration_pct':concentration,'stable_positive_periods':stable_periods,'diversity_score':diversity_score,'opportunity_count':opportunity_count,'signal_rate_pct':signal_rate,'quality_flags':flags,'narrative':f'{logic}: {status}. {samples}/{opportunity_count} buy signals ({signal_rate}%), execution success {sr}%, evaluation success {evaluation_success_rate}%, aggregate quality {aggregate_quality_score}, expected excess {expected_excess_value}%, p10 {p10_excess}%, concentration {concentration}%, recent {recent_sr}% / {recent_excess}%.'}
        conn.execute('''INSERT INTO strategy_registry (logic,status,samples,success_rate_pct,avg_excess_return_pct,recent_success_rate_pct,recent_avg_excess_return_pct,reason,summary_json,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(logic) DO UPDATE SET status=excluded.status,samples=excluded.samples,success_rate_pct=excluded.success_rate_pct,avg_excess_return_pct=excluded.avg_excess_return_pct,recent_success_rate_pct=excluded.recent_success_rate_pct,recent_avg_excess_return_pct=excluded.recent_avg_excess_return_pct,reason=excluded.reason,summary_json=excluded.summary_json,updated_at=excluded.updated_at''',
            (logic,status,samples,sr,avg_excess,recent_sr,recent_excess,reason,json.dumps(summary,ensure_ascii=False,sort_keys=True),utc_now()))
        if old_status != status:
            conn.execute('INSERT INTO strategy_state_events (logic,old_status,new_status,reason,event_json,created_at) VALUES (?,?,?,?,?,?)',(logic,old_status,status,reason,json.dumps(summary,ensure_ascii=False,sort_keys=True),utc_now()))
        out.append({'logic':logic,'old_status':old_status,'status':status,'samples':samples,'success_rate_pct':sr,'evaluation_success_rate_pct':evaluation_success_rate,'avg_excess_return_pct':avg_excess,'excess_win_rate_pct':excess_win_rate,'recent_success_rate_pct':recent_sr,'recent_avg_excess_return_pct':recent_excess,'aggregate_quality_score':aggregate_quality_score,'expected_excess_value_pct':expected_excess_value,'p10_excess_return_pct':p10_excess,'p25_excess_return_pct':p25_excess,'payoff_profile':payoff,'symbol_concentration_pct':concentration,'stable_positive_periods':stable_periods,'reason':reason,'summary':summary})
    # Keep repair_active as a bounded research lane, not a broad status bucket.
    MAX_REPAIR_ACTIVE = 10
    repair_rows=[x for x in out if x.get('status')=='repair_active']
    repair_keep=set(x.get('logic') for x in sorted(repair_rows, key=lifecycle_score, reverse=True)[:MAX_REPAIR_ACTIVE])
    repair_capped=[]
    for x in repair_rows:
        if x.get('logic') in repair_keep:
            continue
        old_new=x.get('status')
        x['status']='probation'
        x['reason']='repair_active cap: kept in probation until it ranks into the bounded watch-only repair lane'
        if isinstance(x.get('summary'), dict):
            x['summary']['narrative']=f"{x.get('logic')}: probation. {x['reason']}"
        conn.execute('UPDATE strategy_registry SET status=?, reason=?, summary_json=?, updated_at=? WHERE logic=?',
            ('probation', x['reason'], json.dumps(x.get('summary') or {}, ensure_ascii=False, sort_keys=True), utc_now(), x['logic']))
        conn.execute('INSERT INTO strategy_state_events (logic,old_status,new_status,reason,event_json,created_at) VALUES (?,?,?,?,?,?)',
            (x['logic'], old_new, 'probation', x['reason'], json.dumps({'agent':'strategy_lifecycle','guardrail':'repair_active_cap','max_repair_active':MAX_REPAIR_ACTIVE,'score':lifecycle_score(x)}, ensure_ascii=False), utc_now()))
        repair_capped.append({'logic':x['logic'],'old_status':old_new,'new_status':'probation','reason':x['reason'],'score':round(lifecycle_score(x),2)})

    # Active-floor guardrail: keep at least a small paper-research active set when no qualified
    # replacements exist. Active still remains paper-only and down-weighted unless quality gates pass.
    # the paper active set below target when the balancer has no qualified
    # replacements. Preserve the strongest just-demoted active strategies and
    # mark why, so recommendation coverage does not collapse because audit
    # samples temporarily narrowed. This is historical/paper-only state.
    active_now=[x for x in out if x.get('status') in ('active','repair_active','validation_active')]
    floor_restored=[]
    if len(active_now) < MIN_ACTIVE_FLOOR:
        restore_needed=MIN_ACTIVE_FLOOR-len(active_now)
        restore_pool=[x for x in out if x.get('old_status')=='active' and x.get('status')!='active']
        if len(restore_pool) < restore_needed:
            recent_demoted=[r['logic'] for r in conn.execute("SELECT logic FROM strategy_state_events WHERE old_status='active' AND new_status!='active' ORDER BY id DESC LIMIT 50").fetchall()]
            by_logic={x.get('logic'):x for x in out if x.get('status')!='active'}
            for logic in recent_demoted:
                x=by_logic.get(logic)
                if x and x not in restore_pool:
                    restore_pool.append(x)
                if len(restore_pool) >= restore_needed:
                    break
        def floor_restore_eligible(x):
            # Paper-only active-floor guardrail. Do not let one weak recent hit-rate
            # window collapse coverage when the strategy still has broad positive
            # historical excess and only mildly negative recent excess. This is not
            # a quality promotion; it preserves a clearly labelled research slot.
            return (
                not str(x.get('logic') or '').startswith('range_grid_')
                and (x.get('avg_excess_return_pct') or 0) >= 0.6
                and (x.get('samples') or 0) >= 250
                and (x.get('recent_avg_excess_return_pct') or 0) >= -1.0
                and (x.get('expected_excess_value_pct') or 0) >= 0.0
                and (x.get('p10_excess_return_pct') or -99) > -8.0
                and ((x.get('excess_win_rate_pct') or 0) >= 49.0 or (x.get('success_rate_pct') or 0) >= 35.0)
            )
        restore_pool=[x for x in restore_pool if floor_restore_eligible(x)]
        restore_pool=sorted(restore_pool, key=lifecycle_score, reverse=True)[:restore_needed]
        for x in restore_pool:
            old_new=x.get('status')
            reason=(f"active floor guardrail: preserved active because active pool would fall below {MIN_ACTIVE_FLOOR} "
                    f"and no replacement should be assumed; lifecycle candidate status was {old_new}; "
                    f"samples {x.get('samples')}, success {x.get('success_rate_pct')}%, excess {x.get('avg_excess_return_pct')}%, "
                    f"excess win {x.get('excess_win_rate_pct')}%, recent {x.get('recent_success_rate_pct')}%/{x.get('recent_avg_excess_return_pct')}%")
            x['status']='active'
            x['reason']=reason
            if isinstance(x.get('summary'), dict):
                x['summary']['narrative']=f"{x.get('logic')}: active. {reason}"
            conn.execute('UPDATE strategy_registry SET status=?, reason=?, summary_json=?, updated_at=? WHERE logic=?',
                ('active', reason, json.dumps(x.get('summary') or {}, ensure_ascii=False, sort_keys=True), utc_now(), x['logic']))
            conn.execute('INSERT INTO strategy_state_events (logic,old_status,new_status,reason,event_json,created_at) VALUES (?,?,?,?,?,?)',
                (x['logic'], old_new, 'active', reason, json.dumps({'agent':'strategy_lifecycle','guardrail':'active_floor','min_active_floor':MIN_ACTIVE_FLOOR,'score':lifecycle_score(x)}, ensure_ascii=False), utc_now()))
            floor_restored.append({'logic':x['logic'],'candidate_status':old_new,'new_status':'active','reason':reason,'score':round(lifecycle_score(x),2)})
    conn.commit(); conn.close()
    changes=[x for x in out if x.get('old_status') != x.get('status')]
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'strategies':out,'active_floor_guardrail':{'min_active_floor':MIN_ACTIVE_FLOOR,'restored':floor_restored},'repair_active_guardrail':{'max_repair_active':MAX_REPAIR_ACTIVE,'capped':repair_capped}}
    next_actions=[]
    if not any(x.get('status') in ('active','repair_active','validation_active') for x in out):
        next_actions.append('Review probation/watch clusters if active/repair-active count falls to zero.')
    if floor_restored:
        next_actions.append('Review active-floor preserved strategies before relaxing demotion thresholds.')
    if repair_capped:
        next_actions.append('Review repair_active cap; lower-ranked repair candidates remain probation until exit-policy evidence improves.')
    attach_contract(packet, 'strategy_lifecycle', outputs={'strategy_count': len(out), 'status_changes': changes[:20], 'floor_restored': floor_restored, 'repair_capped': repair_capped[:20]}, metrics={'strategy_count': len(out), 'status_change_count': len(changes), 'floor_restored_count': len(floor_restored), 'repair_active_count': sum(1 for x in out if x.get('status')=='repair_active'), 'repair_capped_count': len(repair_capped)}, next_actions=next_actions)
    Path('/tmp/strategy_lifecycle_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
