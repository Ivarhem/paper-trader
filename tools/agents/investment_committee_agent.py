#!/usr/bin/env python3
from __future__ import annotations
import json, re, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path


def disclosure_effective_medium(disc: dict) -> int:
    if disc.get('effective_medium') is not None:
        try:
            return int(disc.get('effective_medium') or 0)
        except Exception:
            return 0
    return int(disc.get('medium') or 0)

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.agents.lib.agent_contract import attach_contract
from app.database import latest_artifact
from app.config import get_settings
from tools.agents.lib.runtime import write_json, read_json

WEIGHTS_PATH=Path('/tmp/investment_committee_weights.json')
LATEST_PATH=Path('/tmp/investment_committee_latest.json')
HISTORY_PATH=Path('/tmp/investment_committee_history.json')

DEFAULT_WEIGHTS={
    'upside_hunter': 0.16,
    'risk_guardian': 0.20,
    'evidence_skeptic': 0.22,
    'balanced_allocator': 0.16,
    'regime_specialist': 0.12,
    'research_advocate': 0.14,
}

MIN_WEIGHT=0.10
MAX_WEIGHT=0.30


def normalize_weights(w):
    vals={k:max(0.0,float(w.get(k, DEFAULT_WEIGHTS[k]))) for k in DEFAULT_WEIGHTS}
    total=sum(vals.values()) or 1.0
    return {k:vals[k]/total for k in DEFAULT_WEIGHTS}


def bounded_weights(w):
    # Keep the committee adaptive, but prevent one evaluator from taking over before real outcome data matures.
    vals=normalize_weights(w)
    for _ in range(20):
        low=[k for k,v in vals.items() if v < MIN_WEIGHT]
        high=[k for k,v in vals.items() if v > MAX_WEIGHT]
        if not low and not high:
            break
        fixed={}
        for k in low: fixed[k]=MIN_WEIGHT
        for k in high: fixed[k]=MAX_WEIGHT
        remaining=1.0-sum(fixed.values())
        free=[k for k in vals if k not in fixed]
        if not free or remaining <= 0:
            vals={**{k:fixed.get(k, vals.get(k,0)) for k in vals}}
            break
        free_sum=sum(vals[k] for k in free) or len(free)
        vals={k:(fixed[k] if k in fixed else vals[k]/free_sum*remaining) for k in vals}
    total=sum(vals.values()) or 1.0
    return {k:round(vals[k]/total,4) for k in DEFAULT_WEIGHTS}


def pct(v):
    try: return float(v or 0)
    except Exception: return 0.0



def artifact_key_from_path(path: str | Path) -> str:
    return Path(path).name.removesuffix('.json')

def load_latest_artifact_json(path: str | Path) -> dict:
    try:
        db_payload = latest_artifact(artifact_key_from_path(path))
        if db_payload is not None:
            return db_payload
    except Exception:
        pass
    try:
        return read_json(path)
    except Exception:
        return {}

def load_weight_state():
    if WEIGHTS_PATH.exists():
        try: return json.loads(WEIGHTS_PATH.read_text(encoding='utf-8'))
        except Exception: return {}
    return {}

def load_fund_org_context():
    return load_latest_artifact_json('/tmp/fund_consensus_latest.json')

def load_fund_recommendation_context():
    return load_latest_artifact_json('/tmp/fund_recommendation_consensus_latest.json')

def load_fund_performance_context():
    return load_latest_artifact_json('/tmp/fund_performance_evaluator_latest.json')

def load_event_thesis_context():
    return load_latest_artifact_json('/tmp/event_thesis_latest.json')

def _lower_join(values):
    return ' '.join(str(v or '') for v in values).lower()

def event_thesis_context_for_row(row: dict, packet: dict | None = None) -> dict:
    packet = packet or load_event_thesis_context()
    theses = packet.get('theses') or []
    if not theses:
        return {'matched': False, 'theses': [], 'policy': 'no_event_thesis_available'}
    vb = row.get('validation_basis') or {}
    mi = row.get('market_issue_context') or {}
    mc = row.get('market_context') or {}
    fund = row.get('fund_recommendation_consensus') or {}
    text = _lower_join([
        row.get('symbol'), row.get('name'), row.get('market'), row.get('recommendation_reason'),
        row.get('action_label'), mi.get('label'), mi.get('theme_hint'), mi.get('narrative'),
        mc.get('label'), mc.get('theme_hint'), mc.get('narrative'), vb.get('market_issue_label'),
        vb.get('market_issue_theme'), vb.get('market_context_label'), fund.get('dominant_style'),
        ' '.join((fund.get('fund_styles') or {}).keys()) if isinstance(fund.get('fund_styles'), dict) else '',
    ])
    matched = []
    for thesis in theses:
        channels = set(thesis.get('affected_channels') or []) | set(thesis.get('beneficiaries') or []) | set(thesis.get('victims') or [])
        direct = any(token and token in text for token in [str(thesis.get('event_type') or '').lower(), str(thesis.get('label') or '').lower()])
        channel_hit = any(ch and ch.lower() in text for ch in channels)
        issue_hit = bool(mi and (str(mi.get('issue_id') or '') == str(thesis.get('source_issue_id') or '')))
        broad = not text.strip() and (thesis.get('confidence') or 0) >= 0.65
        if direct or channel_hit or issue_hit or broad:
            matched.append(dict(thesis, match_reason='direct_or_channel_match' if not issue_hit else 'issue_id_match'))
    matched = sorted(matched, key=lambda x: (x.get('impact_score') or 0, x.get('confidence') or 0), reverse=True)[:3]
    posture = 'neutral'
    if matched:
        risky = matched[0].get('event_type') in ('war_escalation', 'middle_east_oil_shock', 'black_sea_grain_shock')
        posture = 'event_tactical' if risky else 'macro_context'
    return {'matched': bool(matched), 'theses': matched, 'posture': posture, 'policy': 'event thesis is advisory context interpreted by committee role'}

def compact_event_thesis(event_ctx: dict) -> str:
    rows = event_ctx.get('theses') or []
    if not rows:
        return 'event thesis 없음'
    return ', '.join(f"{t.get('event_type')}({t.get('confidence')})" for t in rows[:2])

def event_beneficiary_hit(event_ctx: dict, text: str) -> bool:
    tl = (text or '').lower()
    for thesis in event_ctx.get('theses') or []:
        if any(str(x).lower() in tl for x in (thesis.get('beneficiaries') or [])):
            return True
    return False

def event_victim_hit(event_ctx: dict, text: str) -> bool:
    tl = (text or '').lower()
    for thesis in event_ctx.get('theses') or []:
        if any(str(x).lower() in tl for x in (thesis.get('victims') or [])):
            return True
    return False

def fund_overlay_for_symbol(symbol, packet):
    for row in (packet.get('symbol_consensus') or []):
        if row.get('symbol') == symbol:
            return row
    return {}

def fund_recommendation_overlay_for_symbol(symbol, packet):
    for idx, row in enumerate(packet.get('items') or [], start=1):
        if row.get('symbol') == symbol:
            out = dict(row)
            out['rank'] = idx
            return out
    return {}

def fund_performance_overlay_for_recommendation(fund_recommendation_overlay: dict, performance_packet: dict) -> dict:
    fund_ids=[d.get('fund_id') for d in (fund_recommendation_overlay or {}).get('fund_details') or [] if d.get('fund_id')]
    by_id={f.get('id'): f for f in (performance_packet or {}).get('evaluations') or [] if f.get('id')}
    rows=[]
    for fid in fund_ids:
        f=by_id.get(fid) or {}
        if not f:
            continue
        profile=f.get('strategy_effectiveness') or {}
        rows.append({
            'fund_id': fid,
            'style': f.get('style'),
            'tier': f.get('tier'),
            'tier_label': f.get('tier_label'),
            'tier_reason': f.get('tier_reason'),
            'ranking_eligible': f.get('ranking_eligible'),
            'ranking_min_age_days': f.get('ranking_min_age_days'),
            'age_days': f.get('age_days'),
            'return_pct': f.get('return_pct'),
            'mdd_pct': f.get('mdd_pct'),
            'trade_count': f.get('trade_count'),
            'fund_quality_score': f.get('fund_quality_score'),
            'alignment_pct': profile.get('alignment_pct'),
            'dominant_role': profile.get('dominant_role'),
        })
    positive=[r for r in rows if pct(r.get('return_pct')) > 0]
    avg_return=round(sum(pct(r.get('return_pct')) for r in rows)/len(rows),2) if rows else None
    avg_quality=round(sum(pct(r.get('fund_quality_score')) for r in rows)/len(rows),2) if rows else None
    best=sorted(rows, key=lambda r:(pct(r.get('fund_quality_score')), pct(r.get('return_pct'))), reverse=True)[:3]
    return {
        'participating_fund_count': len(rows),
        'positive_fund_count': len(positive),
        'avg_return_pct': avg_return,
        'avg_quality_score': avg_quality,
        'top_participating_funds': best,
        'policy': 'committee judges fund decision quality and allocation fitness, not standalone stock or strategy selection',
    }

def load_weights():
    if WEIGHTS_PATH.exists():
        try:
            data=json.loads(WEIGHTS_PATH.read_text(encoding='utf-8'))
            w=data.get('weights') or DEFAULT_WEIGHTS
        except Exception:
            w=DEFAULT_WEIGHTS.copy()
    else:
        w=DEFAULT_WEIGHTS.copy()
    return bounded_weights(w)


def save_weights(weights, performance=None, source_run_at=None):
    weights=bounded_weights(weights)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'adaptive_committee_weights','weights':weights,'performance':performance or {},'last_proxy_source_run_at':source_run_at,'note':'Weights are bounded normalized evaluator priors; updated cautiously from audit proxy/future outcome without double-counting same source.'}
    write_json(WEIGHTS_PATH, packet)


def load_committee_history(limit=100):
    if not HISTORY_PATH.exists():
        return []
    try:
        rows=json.loads(HISTORY_PATH.read_text(encoding='utf-8'))
        return rows[-limit:] if isinstance(rows,list) else []
    except Exception:
        return []


def append_committee_history(packet):
    rows=load_committee_history(300)
    rows.append({'run_at':packet.get('run_at'),'weights':packet.get('weights'),'summary':packet.get('summary'),'items':packet.get('items')})
    write_json(HISTORY_PATH, rows[-300:])


def persist_post_committee_history(items: list[dict]) -> dict:
    """Mirror committee-updated recommendation payloads into the latest DB run.

    Recommendation UI/API endpoints read recommendation_history as the source
    of truth; /tmp artifacts remain pipeline handoff/debug outputs only.
    """
    if not items:
        return {'updated': 0, 'reason': 'no_items'}
    by_symbol = {item.get('symbol'): item for item in items if item.get('symbol')}
    if not by_symbol:
        return {'updated': 0, 'reason': 'no_symbol_items'}
    conn = sqlite3.connect(get_settings().database_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recommendation_history'"
        ).fetchone()
        if not exists:
            return {'updated': 0, 'reason': 'missing_recommendation_history'}
        run_row = conn.execute("SELECT MAX(run_at) AS run_at FROM recommendation_history").fetchone()
        run_at = run_row['run_at'] if run_row and run_row['run_at'] else None
        if not run_at:
            return {'updated': 0, 'reason': 'no_history_run'}
        updated = 0
        for symbol, item in by_symbol.items():
            row = conn.execute(
                "SELECT id FROM recommendation_history WHERE run_at=? AND symbol=?",
                (run_at, symbol),
            ).fetchone()
            if not row:
                continue
            conn.execute(
                """
                UPDATE recommendation_history
                SET action=?, score=?, payload_json=?
                WHERE id=?
                """,
                (
                    item.get('action'),
                    item.get('score'),
                    json.dumps(item, ensure_ascii=False, sort_keys=True),
                    row['id'],
                ),
            )
            updated += 1
        conn.commit()
        return {'updated': updated, 'run_at': run_at}
    finally:
        conn.close()


def audit_proxy_by_symbol():
    path=Path('/tmp/recommendation_audit_latest.json')
    if not path.exists(): return {}
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    rows=data.get('items') or []
    by={}
    for r in rows:
        sym=r.get('symbol')
        if not sym: continue
        bucket=by.setdefault(sym, {'n':0,'success':0,'fail':0,'timeout':0,'avg_excess_values':[]})
        bucket['n']+=1
        if r.get('result') in bucket: bucket[r.get('result')]+=1
        if r.get('excess_return_pct') is not None: bucket['avg_excess_values'].append(float(r.get('excess_return_pct')))
    for sym,b in by.items():
        vals=b.pop('avg_excess_values')
        b['avg_excess_return_pct']=round(sum(vals)/len(vals),2) if vals else None
        b['success_rate_pct']=round(b['success']/b['n']*100,2) if b['n'] else 0
        if b['n'] < 3: b['proxy_label']='unknown'
        elif (b['avg_excess_return_pct'] or 0) > 1 and b['success_rate_pct'] >= 45: b['proxy_label']='good'
        elif (b['avg_excess_return_pct'] or 0) < -1 or b['fail'] > b['success']: b['proxy_label']='bad'
        else: b['proxy_label']='mixed'
    return by


def load_outcome_performance():
    path=Path('/tmp/committee_performance_ledger_latest.json')
    if not path.exists():
        return None
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    perf=data.get('performance') or {}
    summary=data.get('summary') or {}
    evaluated=int(summary.get('evaluated_rows') or 0)
    if evaluated < 10 or not perf:
        return {'usable':False,'reason':'insufficient_evaluated_outcomes','evaluated_rows':evaluated,'raw_performance':perf,'summary':summary}
    return {'usable':True,'evaluated_rows':evaluated,'raw_performance':perf,'summary':summary,'run_at':data.get('run_at')}


def update_weights_from_outcomes(weights):
    outcome=load_outcome_performance()
    perf={k:{'correct':0,'wrong':0,'hit_rate':None,'delta':0.0} for k in weights}
    if not outcome:
        return None
    if not outcome.get('usable'):
        return bounded_weights(weights), {'mode':outcome.get('reason'),'evaluated_rows':outcome.get('evaluated_rows',0),'evaluators':perf,'summary':outcome.get('summary')}, None
    adjusted=dict(weights)
    for agent in weights:
        raw=(outcome.get('raw_performance') or {}).get(agent) or {}
        correct=int(raw.get('correct') or 0); wrong=int(raw.get('wrong') or 0); total=correct+wrong
        perf[agent].update({'correct':correct,'wrong':wrong,'hit_rate':round(correct/total,3) if total else None})
        if total < 5:
            continue
        hit=correct/total
        if hit >= 0.60: delta=0.02
        elif hit <= 0.40: delta=-0.02
        else: delta=0.0
        perf[agent]['delta']=delta
        adjusted[agent]=max(0.01, adjusted.get(agent,0)+delta)
    return bounded_weights(adjusted), {'mode':'outcome_performance','evaluated_rows':outcome.get('evaluated_rows'),'evaluators':perf,'source_run_at':outcome.get('run_at'),'summary':outcome.get('summary')}, outcome.get('run_at')


def update_weights_from_audit_proxy(weights, rows):
    try:
        audit_packet=read_json('/tmp/recommendation_audit_latest.json')
    except Exception:
        audit_packet={}
    source_run_at=audit_packet.get('run_at')
    state=load_weight_state()
    if source_run_at and state.get('last_proxy_source_run_at') == source_run_at:
        return bounded_weights(weights), {'mode':'audit_proxy_skipped_duplicate','source_run_at':source_run_at,'evaluators':{k:{'correct':0,'wrong':0,'unknown':0,'delta':0.0} for k in weights}}, source_run_at
    audit=audit_proxy_by_symbol()
    perf={k:{'correct':0,'wrong':0,'unknown':0,'delta':0.0} for k in weights}
    if not audit or not rows:
        return bounded_weights(weights), {'mode':'not_enough_data','evaluators':perf}, source_run_at
    for item in rows:
        sym=item.get('symbol'); label=(audit.get(sym) or {}).get('proxy_label','unknown')
        committee=item.get('committee') or {}; opinions=committee.get('opinions') or []
        for op in opinions:
            agent=op.get('agent')
            if agent not in perf: continue
            opinion=op.get('opinion')
            if label=='unknown' or opinion=='watch':
                perf[agent]['unknown']+=1; continue
            correct=(opinion=='support' and label=='good') or (opinion=='oppose' and label=='bad')
            if correct: perf[agent]['correct']+=1
            else: perf[agent]['wrong']+=1
    known_total=sum(p['correct']+p['wrong'] for p in perf.values())
    if known_total < 5:
        # No meaningful feedback for the current committee rows; do not let stale proxy drift persist.
        return bounded_weights(DEFAULT_WEIGHTS), {'mode':'insufficient_known_proxy_labels','known_total':known_total,'evaluators':perf,'audit_symbol_count':len(audit),'source_run_at':source_run_at}, source_run_at
    adjusted=dict(weights)
    for agent, p in perf.items():
        total=p['correct']+p['wrong']
        if total < 3:
            continue
        hit=p['correct']/total
        # Conservative one-run adjustment: max +/- 3 percentage points before normalization.
        if hit >= 0.62: delta=0.015
        elif hit <= 0.38: delta=-0.015
        else: delta=0.0
        p['hit_rate']=round(hit,3); p['delta']=delta
        adjusted[agent]=max(0.03, adjusted.get(agent,0)+delta)
    norm=sum(adjusted.values()) or 1
    adjusted=bounded_weights(adjusted)
    return adjusted, {'mode':'audit_proxy','evaluators':perf,'audit_symbol_count':len(audit),'source_run_at':source_run_at}, source_run_at


def flags(row):
    return set(((row.get('validation_basis') or {}).get('audit_quality_flags')) or [])


def base_ctx(row):
    vb=row.get('validation_basis') or {}; disc=row.get('disclosure_risk') or {}; critic=row.get('critic') or {}; committee=row.get('investment_committee') or {}
    fund_primary = bool(vb.get('fund_consensus_review_candidate') or vb.get('fund_recommendation_consensus') or row.get('fund_recommendation_consensus'))
    event_ctx = row.get('event_thesis_context') or event_thesis_context_for_row(row)
    fund_age_ctx = row.get('fund_age_committee_context') or fund_age_context_for_row(row)
    fund_support_ctx = row.get('fund_support_committee_context') or fund_support_context_for_row(row)
    return {'vb':vb,'disc':disc,'critic':critic,'flags':flags(row),'score':pct(row.get('score')),'upside':pct(row.get('upside_1_pct')),'downside':abs(pct(row.get('downside_stop_pct'))),'market':row.get('market'),'fund_primary':fund_primary,'event':event_ctx,'fund_age':fund_age_ctx,'fund_support':fund_support_ctx}


COMMITTEE_FILL_ASSUMPTIONS={
    'upside_hunter': {'profile':'aggressive','fill_model':'optimistic_limit_target','rule':'목표가 터치 우선'},
    'balanced_allocator': {'profile':'neutral','fill_model':'close_only','rule':'현재 기준/종가 판정'},
    'risk_guardian': {'profile':'conservative','fill_model':'bracket_intraday_conservative','rule':'손절 터치 우선'},
    'evidence_skeptic': {'profile':'neutral','fill_model':'close_only','rule':'현재 기준/종가 판정'},
    'regime_specialist': {'profile':'neutral','fill_model':'close_only','rule':'현재 기준/종가 판정'},
    'research_advocate': {'profile':'aggressive','fill_model':'optimistic_limit_target','rule':'목표가 터치 우선'},
}


ROLE_BEHAVIOR={
    'upside_hunter': {
        'mandate': '매수 근거와 upside 비대칭을 먼저 찾되, hard risk는 승인권자가 아니라 Risk Gate로 이관',
        'support_at': 62,
        'watch_at': 38,
    },
    'risk_guardian': {
        'mandate': '손실 제한과 hard blocker를 판정하는 거부권 성격의 리스크 전담',
        'support_at': 68,
        'watch_at': 45,
    },
    'evidence_skeptic': {
        'mandate': '검증 품질과 과최적화 가능성을 따지는 증거 전담',
        'support_at': 68,
        'watch_at': 45,
    },
    'balanced_allocator': {
        'mandate': '보상/위험/점수 균형으로 paper allocation 적합성을 평가',
        'support_at': 66,
        'watch_at': 45,
    },
    'regime_specialist': {
        'mandate': '시장 국면이 진입 가격과 보유 기간에 주는 영향을 평가',
        'support_at': 66,
        'watch_at': 43,
    },
    'research_advocate': {
        'mandate': '신규 가설, 테마, under-validated 후보를 차단보다 연구 큐로 올리는 역할',
        'support_at': 60,
        'watch_at': 35,
    },
}

STYLE_SPONSOR_RULES=[
    (('volume_surge','momentum','breakout','growth','high_beta','aggressive'), ['upside_hunter','research_advocate'], 'aggressive_growth'),
    (('value','dividend','low_vol','quality','defensive','conservative','income'), ['risk_guardian','balanced_allocator'], 'conservative_quality'),
    (('mean_reversion','reversal','pullback','neutral','balanced'), ['balanced_allocator','regime_specialist'], 'balanced_context'),
]

DEFAULT_FUND_SPONSORS=['balanced_allocator','research_advocate']



def fund_age_bucket(age_days) -> str:
    age = int(pct(age_days))
    if age < 30:
        return 'probation'
    if age < 60:
        return 'young'
    if age < 90:
        return 'maturing'
    return 'mature'


def fund_age_committee_context(fund_recommendation_overlay: dict | None, fund_performance_context: dict | None = None) -> dict:
    details = list((fund_recommendation_overlay or {}).get('fund_details') or [])
    perf_by_id = {r.get('fund_id'): r for r in (fund_performance_context or {}).get('top_participating_funds') or [] if r.get('fund_id')}
    rows = []
    for d in details:
        fid = d.get('fund_id')
        perf = perf_by_id.get(fid) or {}
        age = d.get('age_days') if d.get('age_days') is not None else perf.get('age_days')
        if age is None:
            continue
        age_int = int(pct(age))
        rows.append({
            'fund_id': fid,
            'style': d.get('style') or perf.get('style'),
            'age_days': age_int,
            'age_bucket': fund_age_bucket(age_int),
            'age_weight': d.get('age_weight'),
            'age_weight_reason': d.get('age_weight_reason'),
            'return_pct': perf.get('return_pct'),
            'quality': d.get('quality') if d.get('quality') is not None else perf.get('fund_quality_score'),
            'tier': perf.get('tier'),
            'tier_label': perf.get('tier_label'),
            'tier_reason': perf.get('tier_reason'),
            'ranking_eligible': perf.get('ranking_eligible'),
        })
    if not rows:
        return {
            'available': False,
            'policy': 'fund_age_lifetime_role_adjustment_v1; no fund age details available for this row',
        }
    ages = [int(r.get('age_days') or 0) for r in rows]
    buckets = {}
    for r in rows:
        b = r.get('age_bucket') or 'unknown'
        buckets[b] = buckets.get(b, 0) + 1
    high_return_young = [r for r in rows if r.get('age_bucket') in ('young', 'maturing') and pct(r.get('return_pct')) > 0 and pct(r.get('quality')) >= 10]
    mature_positive = [r for r in rows if r.get('age_bucket') == 'mature' and pct(r.get('return_pct')) > 0]
    young_count = buckets.get('probation', 0) + buckets.get('young', 0)
    posture = 'young_experimental' if young_count > len(rows) / 2 else ('mature_supported' if buckets.get('mature', 0) >= len(rows) / 2 else 'mixed_maturing')
    return {
        'available': True,
        'participating_fund_count': len(rows),
        'age_buckets': buckets,
        'min_age_days': min(ages),
        'avg_age_days': round(sum(ages) / len(ages), 1),
        'max_age_days': max(ages),
        'young_or_probation_count': young_count,
        'maturing_count': buckets.get('maturing', 0),
        'mature_count': buckets.get('mature', 0),
        'high_return_young_count': len(high_return_young),
        'mature_positive_count': len(mature_positive),
        'posture': posture,
        'details': sorted(rows, key=lambda r: (pct(r.get('quality')), pct(r.get('return_pct'))), reverse=True)[:8],
        'policy': 'fund_age_lifetime_role_adjustment_v1: aggressive roles may tactically support young high-return funds; conservative roles prefer maturing/mature proof; balanced/regime roles stay moderate unless event/tactical context supports experimentation',
    }


def fund_age_context_for_row(row: dict) -> dict:
    return fund_age_committee_context(row.get('fund_recommendation_consensus') or {}, row.get('fund_performance_context') or {})

def fund_support_committee_context(fund_recommendation_overlay: dict | None) -> dict:
    overlay = fund_recommendation_overlay or {}
    support_type = overlay.get('support_type') or 'unknown'
    supporting_count = int(overlay.get('supporting_fund_count') or len(overlay.get('participating_funds') or []) or 0)
    style_count = len(overlay.get('supporting_fund_styles') or overlay.get('fund_styles') or {})
    breadth_score = pct(overlay.get('support_breadth_score'))
    age_quality = overlay.get('support_age_quality') or {}
    isolated = supporting_count <= 1 and support_type in ('specialist_conviction', 'tactical_candidate', 'watch_only_candidate')
    young_weak = (
        isolated
        and pct((age_quality or {}).get('max_age_weight')) < 0.65
        and int((age_quality or {}).get('high_quality_fund_count') or 0) <= 0
    )
    return {
        'support_type': support_type,
        'supporting_fund_count': supporting_count,
        'supporting_fund_styles': overlay.get('supporting_fund_styles') or overlay.get('fund_styles') or {},
        'style_count': style_count,
        'support_breadth_score': breadth_score,
        'best_supporting_fund': overlay.get('best_supporting_fund'),
        'support_age_quality': age_quality,
        'isolated_candidate': isolated,
        'isolated_young_weak_candidate': young_weak,
        'policy': 'committee interprets union fund candidates by support type: aggressive/research can sponsor specialist/tactical quality; conservative/evidence prefer breadth, cross-style, and mature support',
    }

def fund_support_context_for_row(row: dict) -> dict:
    return fund_support_committee_context(row.get('fund_recommendation_consensus') or {})


def apply_fund_support_role_adjustment(agent: str, row: dict, c: dict, score: float, supports: list[str], concerns: list[str]) -> float:
    if not c.get('fund_primary'):
        return score
    support_ctx = c.get('fund_support') or {}
    support_type = support_ctx.get('support_type') or 'unknown'
    breadth = pct(support_ctx.get('support_breadth_score'))
    count = int(support_ctx.get('supporting_fund_count') or 0)
    best = support_ctx.get('best_supporting_fund') or {}
    best_quality = pct(best.get('quality'))
    quality_ok = best_quality >= 10 or breadth >= 35
    note = f"fund support {support_type}, funds {count}, breadth {breadth:.0f}, best quality {best_quality:.1f}"
    core = support_type in ('core_consensus', 'cross_style_consensus')
    specialist = support_type in ('specialist_conviction', 'tactical_candidate')
    watch_only = support_type == 'watch_only_candidate'
    event_tactical = bool((c.get('event') or {}).get('matched'))
    if agent in ('upside_hunter', 'research_advocate'):
        if core:
            score += 4
            supports.insert(0, note + ': broad fund support')
        elif specialist and quality_ok:
            score += 7 if agent == 'research_advocate' else 5
            supports.insert(0, note + ': specialist/tactical fund conviction worth committee review')
        elif watch_only:
            score += 1
            concerns.append(note + ': watch-only union candidate needs stronger trigger')
    elif agent in ('risk_guardian', 'evidence_skeptic'):
        if core:
            score += 5
            supports.insert(0, note + ': breadth reduces single-fund thesis risk')
        elif support_ctx.get('isolated_young_weak_candidate'):
            score -= 9 if agent == 'risk_guardian' else 10
            concerns.append(note + ': isolated young/weak fund support')
        elif specialist:
            score -= 4
            concerns.append(note + ': specialist-only support requires sizing and validation')
        elif watch_only:
            score -= 6
            concerns.append(note + ': holding-only/watch support is not buy conviction')
    elif agent in ('balanced_allocator', 'regime_specialist'):
        if core:
            score += 4
            supports.insert(0, note + ': suitable for broader paper allocation review')
        elif specialist and (quality_ok or event_tactical):
            score += 1
            concerns.append(note + ': keep specialist candidate watch-sized unless context confirms')
        elif specialist or watch_only:
            score -= 2
            concerns.append(note + ': not core breadth; prefer watch-only allocation posture')
    return score


def apply_fund_age_role_adjustment(agent: str, row: dict, c: dict, score: float, supports: list[str], concerns: list[str]) -> float:
    if not c.get('fund_primary'):
        return score
    age_ctx = c.get('fund_age') or {}
    if not age_ctx.get('available'):
        return score
    posture = age_ctx.get('posture')
    high_young = int(age_ctx.get('high_return_young_count') or 0)
    young = int(age_ctx.get('young_or_probation_count') or 0)
    mature = int(age_ctx.get('mature_count') or 0)
    maturing = int(age_ctx.get('maturing_count') or 0)
    avg_age = age_ctx.get('avg_age_days')
    note = f"fund age {posture}, avg {avg_age}d, young {young}, maturing {maturing}, mature {mature}"
    event_tactical = bool((c.get('event') or {}).get('matched'))
    if agent in ('upside_hunter', 'research_advocate'):
        if high_young > 0:
            score += 5 if agent == 'upside_hunter' else 6
            supports.insert(0, note + ': tactical/experimental young high-return fund support')
        elif mature > 0:
            score += 2
            supports.insert(0, note + ': mature fund backing supports thesis')
    elif agent in ('risk_guardian', 'evidence_skeptic'):
        if young > 0 and mature == 0:
            score -= 7 if agent == 'risk_guardian' else 8
            concerns.append(note + ': young fund lifetime requires watch until mature')
        elif young > 0:
            score -= 3
            concerns.append(note + ': young fund sleeve should stay size-limited')
        if mature > 0:
            score += 4
            supports.insert(0, note + ': mature positive fund evidence reduces lifetime concern')
    elif agent in ('balanced_allocator', 'regime_specialist'):
        if mature > 0 or maturing > 0:
            score += 3
            supports.insert(0, note + ': maturing/mature funds preferred for allocation')
        elif young > 0:
            if event_tactical:
                score += 1
                supports.insert(0, note + ': young fund allowed only as tactical/event sleeve')
            else:
                score -= 3
                concerns.append(note + ': prefer more lifetime before core allocation')
    return score


def fund_style_committee_context(fund_overlay: dict, fund_recommendation_overlay: dict) -> dict:
    """Map paper-fund style into a committee sponsor/meta-review split.

    Funds originate trade theses; the committee should not flatten those theses
    into one generic vote. Style-matched members first interpret why the fund
    bought, while the remaining members review market, disclosure, validation,
    and sizing risk from a meta layer.
    """
    styles={}
    for src in (fund_recommendation_overlay, fund_overlay):
        for style, count in ((src or {}).get('fund_styles') or {}).items():
            if style:
                styles[str(style)] = styles.get(str(style), 0) + int(count or 0)
    dominant_style=max(styles, key=styles.get) if styles else None
    sponsor_agents=list(DEFAULT_FUND_SPONSORS)
    sponsor_profile='generic_fund_thesis'
    style_l=dominant_style.lower() if dominant_style else ''
    for tokens, agents, profile in STYLE_SPONSOR_RULES:
        if any(t in style_l for t in tokens):
            sponsor_agents=list(agents)
            sponsor_profile=profile
            break
    meta_review_agents=[a for a in DEFAULT_WEIGHTS if a not in sponsor_agents]
    thesis_parts=[]
    if dominant_style:
        thesis_parts.append(f'{dominant_style} 성향 펀드 합의')
    if fund_recommendation_overlay:
        thesis_parts.append(f"매수합의 {fund_recommendation_overlay.get('buy_fund_count',0)}개 펀드")
    if (fund_recommendation_overlay or {}).get('buy_reasons'):
        reasons=', '.join(list((fund_recommendation_overlay.get('buy_reasons') or {}).keys())[:2])
        thesis_parts.append(f'주요 근거 {reasons}')
    return {
        'dominant_style': dominant_style,
        'style_counts': styles,
        'sponsor_profile': sponsor_profile,
        'sponsor_agents': sponsor_agents,
        'meta_review_agents': meta_review_agents,
        'thesis': '; '.join(thesis_parts) if thesis_parts else 'fund thesis unavailable',
        'policy': 'fund_style_sets_committee_sponsor; committee reviews fund decision quality, market regime, disclosures, supply, cross-fund performance, and sizing risk',
    }


def opinion(label, agent, score, supports, concerns):
    score=round(max(0,min(100,score)),2)
    behavior=ROLE_BEHAVIOR.get(agent, {})
    op='support' if score>=behavior.get('support_at',68) else ('watch' if score>=behavior.get('watch_at',45) else 'oppose')
    fill=COMMITTEE_FILL_ASSUMPTIONS.get(agent, {'profile':'neutral','fill_model':'close_only','rule':'현재 기준/종가 판정'})
    supports=[f"검증기준: {fill['rule']}"] + supports
    return {'agent':agent,'label':label,'score':score,'opinion':op,'role_mandate':behavior.get('mandate'),'fill_assumption':fill,'supports':supports[:4],'concerns':concerns[:4]}

def apply_event_thesis_role_adjustment(agent: str, row: dict, c: dict, score: float, supports: list[str], concerns: list[str]) -> float:
    event_ctx = c.get('event') or {}
    theses = event_ctx.get('theses') or []
    if not theses:
        return score
    row_text = _lower_join([
        row.get('symbol'), row.get('name'), row.get('recommendation_reason'), row.get('action_label'),
        row.get('market'), ((row.get('market_issue_context') or {}).get('label')),
        ((row.get('market_context') or {}).get('label')),
    ])
    top = theses[0]
    risky_event = top.get('event_type') in ('war_escalation', 'middle_east_oil_shock', 'black_sea_grain_shock')
    beneficiary = event_beneficiary_hit(event_ctx, row_text)
    victim = event_victim_hit(event_ctx, row_text)
    note = f"event thesis {compact_event_thesis(event_ctx)}"
    if agent in ('upside_hunter', 'research_advocate'):
        if beneficiary:
            score += 8
            supports.append(note + ': 수혜 채널 전술 후보')
        elif risky_event:
            score += 3
            supports.append(note + ': 사건 수혜주 탐색 가치')
        if victim:
            score -= 6
            concerns.append(note + ': 비용/피해 채널 노출')
    elif agent == 'risk_guardian':
        if risky_event:
            score -= 8
            concerns.append(note + ': 지정학/원자재 tail risk')
        if beneficiary:
            score += 2
            supports.append(note + ': 수혜 채널이나 사이징 제한 필요')
    elif agent == 'evidence_skeptic':
        score -= 3
        concerns.append(note + ': 뉴스 thesis는 사후 유사국면 검증 필요')
    elif agent == 'regime_specialist':
        if risky_event:
            score -= 5
            concerns.append(note + ': risk-off regime 가능성')
        if beneficiary:
            score += 4
            supports.append(note + ': regime 상 전술 수혜 가능')
    elif agent == 'balanced_allocator':
        if beneficiary:
            score += 3
            supports.append(note + ': 포트폴리오 내 전술 비중 후보')
        if risky_event:
            score -= 2
            concerns.append(note + ': 전체 beta 축소와 병행 필요')
    return score


def upside_hunter(row):
    c=base_ctx(row); s=50; sup=[]; con=[]
    if c['upside'] >= max(5,c['downside']*1.25): s+=22; sup.append(f'상승/손절 비대칭 우수 {c["upside"]:.1f}%/{c["downside"]:.1f}%')
    else:
        s-=12
        con.append('추격매수 보상/손절 비율 부족')
    if c['score']>=60: s+=10; sup.append('기초 점수 60 이상')
    if pct(c['vb'].get('symbol_60d_return_pct'))>8: s+=8; sup.append('60일 가격 탄력 양호')
    if pct(c['vb'].get('avg_active_excess_return_pct'))>0: s+=8; sup.append('전략 초과수익 양수')
    elif c.get('fund_primary'): sup.append('전략 평균 초과수익은 fund-backed 후보의 진단값')
    mover=(c['vb'].get('mover_context') or row.get('mover_context') or {})
    if pct(mover.get('change_pct')) >= 8: s+=6; sup.append('당일/단기 수급성 가격 탄력')
    if c['disc'].get('high',0)>0:
        s-=8; con.append('고위험 공시는 Risk Gate 확인 필요')
    if 'left_tail_excess_risk' in c['flags']:
        s-=6; con.append('하방 꼬리 위험은 가격조건으로 보정 필요')
    s=apply_fund_support_role_adjustment('upside_hunter', row, c, s, sup, con)
    s=apply_fund_age_role_adjustment('upside_hunter', row, c, s, sup, con)
    s=apply_event_thesis_role_adjustment('upside_hunter', row, c, s, sup, con)
    return opinion('공격형', 'upside_hunter', s, sup, con)


def risk_guardian(row):
    c=base_ctx(row); s=58; sup=[]; con=[]; samples=int(c['vb'].get('symbol_validation_sample_count') or 0)
    if samples>=20: s+=10; sup.append(f'검증 샘플 충분 {samples}')
    elif samples<8 and c.get('fund_primary'):
        sup.append(f'fund-backed 후보: 전략 샘플 부족 {samples}은 검증 우선순위로만 기록')
    elif samples<8: s-=14; con.append(f'검증 샘플 부족 {samples}')
    if c['downside']<=7: s+=8; sup.append('손절폭 관리 가능')
    else: s-=8; con.append('손절폭 부담')
    if c['disc'].get('high',0)>0 or c['disc'].get('medium',0)>=2: s-=22; con.append('공시 리스크')
    for f,pen,msg in [('left_tail_excess_risk',14,'하방 꼬리 위험'),('unfavorable_payoff_asymmetry',14,'손익 비대칭 불리'),('period_instability',10,'기간 안정성 부족')]:
        if f in c['flags']: s-=pen; con.append(msg)
    fq=row.get('financial_quality') or {}
    if fq.get('warnings'): s-=10; con.append('재무 품질 경고')
    s=apply_fund_support_role_adjustment('risk_guardian', row, c, s, sup, con)
    s=apply_fund_age_role_adjustment('risk_guardian', row, c, s, sup, con)
    s=apply_event_thesis_role_adjustment('risk_guardian', row, c, s, sup, con)
    return opinion('안전형', 'risk_guardian', s, sup, con)


def evidence_skeptic(row):
    c=base_ctx(row); s=55; sup=[]; con=[]
    q=pct(c['vb'].get('audit_quality_min_score'))
    if q>=70: s+=18; sup.append(f'audit 품질 점수 {q:.0f}')
    elif q>0: s-=max(8, (65-q)*0.5); con.append(f'audit 품질 점수 낮음 {q:.0f}')
    else: s-=16; con.append('audit 품질 점수 없음')
    if c.get('fund_primary'):
        sup.append('초과승률/표본 신뢰구간은 fund-backed 후보의 보조 진단')
    elif pct(c['vb'].get('avg_excess_win_rate_pct'))>=55:
        s+=10; sup.append('초과승률 55% 이상')
    else:
        s-=8; con.append('초과승률 부족')
    for f,pen in [('weak_success_confidence_interval',14),('no_positive_average_excess',18),('recent_decay',12),('period_instability',16)]:
        if f in c['flags']:
            if c.get('fund_primary') and f in ('weak_success_confidence_interval','no_positive_average_excess'):
                sup.append(f'{f}: fund-backed 후보에서는 검증 우선순위 진단')
            else:
                s-=pen; con.append(f)
    s=apply_fund_support_role_adjustment('evidence_skeptic', row, c, s, sup, con)
    s=apply_fund_age_role_adjustment('evidence_skeptic', row, c, s, sup, con)
    s=apply_event_thesis_role_adjustment('evidence_skeptic', row, c, s, sup, con)
    return opinion('검증주의형', 'evidence_skeptic', s, sup, con)


def balanced_allocator(row):
    c=base_ctx(row); s=48; sup=[]; con=[]
    s+=min(16,c['score']*0.16)
    if c.get('fund_primary'):
        s+=6; sup.append('fund consensus 기반 후보: 전략 평균 성과는 보조 진단')
    else:
        s+=min(10,max(0,pct(c['vb'].get('avg_active_excess_return_pct')))*2.5)
        s+=min(8,max(0,pct(c['vb'].get('avg_excess_win_rate_pct'))-50))
    if c['upside']>c['downside']: sup.append('목표/위험 균형 양호')
    else: s-=8; con.append('목표/위험 균형 약함')
    if (c['critic'].get('severity')=='high'): s-=12; con.append('critic high')
    blocking_flags = set(c['flags'])
    if c.get('fund_primary'):
        diagnostic_only = {'weak_success_confidence_interval', 'no_positive_average_excess'}
        if blocking_flags & diagnostic_only:
            sup.append('표본/전략 edge 플래그는 fund-backed 후보의 진단값')
        blocking_flags -= diagnostic_only
    s-=min(20,len(blocking_flags)*4)
    if blocking_flags: con.append('품질 플래그 존재')
    s=apply_fund_support_role_adjustment('balanced_allocator', row, c, s, sup, con)
    s=apply_fund_age_role_adjustment('balanced_allocator', row, c, s, sup, con)
    s=apply_event_thesis_role_adjustment('balanced_allocator', row, c, s, sup, con)
    return opinion('중립형', 'balanced_allocator', s, sup, con)


def regime_specialist(row):
    c=base_ctx(row); s=52; sup=[]; con=[]; rg=row.get('regime_gate') or {}
    reason=str(rg.get('reason') or '')
    status=str(rg.get('status') or '')
    if status in ('ok','pass','support'): s+=12; sup.append('regime 통과')
    if '약세' in reason or 'risk' in reason.lower() or status in ('watch','blocked','risk_off'): s-=14; con.append('시장 regime 부담')
    if pct(c['vb'].get('benchmark_20d_return_pct')) < -5: s-=10; con.append('벤치마크 단기 약세')
    if c['market']=='US' and pct(c['vb'].get('symbol_60d_return_pct'))>pct(c['vb'].get('benchmark_20d_return_pct')): s+=6; sup.append('시장 대비 상대강도')
    s=apply_fund_support_role_adjustment('regime_specialist', row, c, s, sup, con)
    s=apply_fund_age_role_adjustment('regime_specialist', row, c, s, sup, con)
    s=apply_event_thesis_role_adjustment('regime_specialist', row, c, s, sup, con)
    return opinion('Regime형', 'regime_specialist', s, sup, con)

def research_advocate(row):
    c=base_ctx(row); s=50; sup=[]; con=[]
    if c['score'] >= 70:
        s += 14; sup.append('추천 점수 상위권')
    mi=row.get('market_issue_context') or {}; mc=row.get('market_context') or {}
    if (mi.get('impact_score') or 0) >= 70:
        s += 10; sup.append('시장 이슈/테마 확인')
    if (mc.get('impact_score') or 0) >= 70:
        s += 8; sup.append('선행 시장 컨텍스트 확인')
    if (c['critic'].get('issue_type') == 'under_validated'):
        s += 14; sup.append('검증 부족은 차단보다 신규 연구 큐 성격')
    if (c['vb'].get('symbol_validation_sample_count') or 0) < 10:
        s += 6; sup.append('펀드 thesis 사후검증 표본 부족: 전용 검증 선호')
    if (c['vb'].get('positive_symbol_edge_count') or 0) <= 0 and c['score'] >= 65:
        s += 5; sup.append('펀드 판단 대비 사후 성과근거 미확인: 새 가설 후보')
    if c['disc'].get('high',0)>0:
        s -= 10; con.append('공시 리스크는 연구 가능하나 매수 승인은 Risk Gate 필요')
    if c['downside'] > 10:
        s -= 4; con.append('손절폭 부담은 실험 포지션 축소 필요')
    s=apply_fund_support_role_adjustment('research_advocate', row, c, s, sup, con)
    s=apply_fund_age_role_adjustment('research_advocate', row, c, s, sup, con)
    s=apply_event_thesis_role_adjustment('research_advocate', row, c, s, sup, con)
    return opinion('Research형', 'research_advocate', s, sup, con)

def is_fund_primary_row(row: dict) -> bool:
    vb = row.get('validation_basis') or {}
    return bool(vb.get('fund_consensus_review_candidate') or vb.get('fund_recommendation_consensus') or row.get('fund_recommendation_consensus'))


def market_committee_context(row, research_decision, risk_gate_decision, opinions):
    vb = row.get('validation_basis') or {}
    mover = vb.get('mover_context') or row.get('mover_context') or {}
    tech = vb.get('technical_risk_context') or row.get('technical_risk_context') or {}
    benchmark5 = pct(vb.get('benchmark_5d_return_pct'))
    benchmark20 = pct(vb.get('benchmark_20d_return_pct'))
    symbol20 = pct(vb.get('symbol_20d_return_pct'))
    symbol60 = pct(vb.get('symbol_60d_return_pct'))
    volume = pct(vb.get('volume_ratio_20d') or row.get('volume_ratio_20d'))
    mover_change = pct(mover.get('change_pct'))
    flags = set((vb.get('audit_quality_flags') or []))
    stress = []
    if benchmark5 <= -3:
        stress.append(f'지수 5D {benchmark5:.1f}%')
    if tech.get('overheated_chase_risk') or symbol20 >= 20 or symbol60 >= 35 or mover_change >= 15:
        stress.append('단기 급등/추격 위험')
    if 0 < volume < 0.7:
        stress.append(f'거래량 {volume:.2f}x')
    if 'unfavorable_payoff_asymmetry' in flags:
        stress.append('보상/손절 비대칭 약함')
    if (not is_fund_primary_row(row)) and ('no_positive_average_excess' in flags or 'weak_success_confidence_interval' in flags):
        stress.append('펀드 thesis 검증 근거 약함')
    if not stress:
        stress.append('시장/검증 조건 중립')
    upside = next((o for o in opinions if o.get('agent') == 'upside_hunter'), {})
    if upside.get('opinion') == 'support':
        aggressive_note = '공격형은 상승 탄력 자체는 인정해 연구 지지 쪽에 섰습니다.'
    elif risk_gate_decision != 'pass':
        aggressive_note = '공격형도 상승 탄력은 인정하지만 risk warning을 감안해 가격조건과 사이징을 보수적으로 봅니다.'
    else:
        aggressive_note = '공격형은 조건부 관찰이며, 최종 승인 여부는 위원회 종합점수와 hard risk 여부로 판단합니다.'
    approval_note = (
        'Fund 합의가 투자 판단의 원천이고, 위원회는 펀드 성과/성향과 시장·공시·수급·사이징·hard risk warning을 메타 검토합니다. '
        f'Research={research_decision}, Risk Gate={risk_gate_decision}.'
    )
    return {
        'market_stress_reasons': stress[:4],
        'aggressive_note': aggressive_note,
        'approval_note': approval_note,
    }

EVALUATORS=[upside_hunter,risk_guardian,evidence_skeptic,balanced_allocator,regime_specialist,research_advocate]


def relevant(op, row):
    # v1: all evaluators vote, but notes keep future conditional activation easy.
    return True


def synthesize(row, opinions, weights):
    active=[o for o in opinions if o.get('active',True)]
    research_agents={'upside_hunter','balanced_allocator','regime_specialist','research_advocate'}
    risk_agents={'risk_guardian','evidence_skeptic'}
    def layer_score(agent_set):
        rows=[o for o in active if o['agent'] in agent_set]
        total=sum(weights.get(o['agent'],0) for o in rows) or 1
        return round(sum(o['score']*weights.get(o['agent'],0)/total for o in rows),2), rows
    research_score, research_rows = layer_score(research_agents)
    risk_score, risk_rows = layer_score(risk_agents)
    score=round((research_score*0.55)+(risk_score*0.45),2)
    support=sum(1 for o in active if o['opinion']=='support')
    oppose=sum(1 for o in active if o['opinion']=='oppose')
    research_support=sum(1 for o in research_rows if o['opinion']=='support')
    research_oppose=sum(1 for o in research_rows if o['opinion']=='oppose')
    risk_oppose=sum(1 for o in risk_rows if o['opinion']=='oppose')
    risk_support=sum(1 for o in risk_rows if o['opinion']=='support')
    critic=row.get('critic') or {}; disc=row.get('disclosure_risk') or {}; fq=row.get('financial_quality') or {}; vb=row.get('validation_basis') or {}
    blocking_critic = critic.get('issue_type') == 'blocking' or critic.get('severity') == 'high'
    hard_risk = blocking_critic or (disc.get('high',0)>0) or row.get('corporate_action_risk',{}).get('flagged') or (fq.get('score_adjustment') or 0) <= -20
    fund_packet=load_fund_org_context()
    fund_overlay=fund_overlay_for_symbol(row.get('symbol'), fund_packet)
    fund_recommendation_packet=load_fund_recommendation_context()
    fund_recommendation_overlay=fund_recommendation_overlay_for_symbol(row.get('symbol'), fund_recommendation_packet)
    fund_primary_candidate=bool(fund_recommendation_overlay) or is_fund_primary_row(row)
    sample_under_validated = ((vb.get('symbol_validation_sample_count') or 0) < 10 or (vb.get('positive_symbol_edge_count') or 0) <= 0)
    under_validated = (not fund_primary_candidate) and (critic.get('issue_type') == 'under_validated' or sample_under_validated)
    fund_diagnostic_under_validated = bool(fund_primary_candidate and (critic.get('issue_type') == 'under_validated' or sample_under_validated))
    fund_performance_context=fund_performance_overlay_for_recommendation(fund_recommendation_overlay, load_fund_performance_context())
    fund_allocation_guardrail = {
        'policy': (fund_recommendation_overlay or {}).get('risk_guardrail_policy') or 'fund risk findings cap consensus weight only',
        'risk_capped_funds': (fund_recommendation_overlay or {}).get('risk_capped_funds') or [],
        'cap_applied': bool((fund_recommendation_overlay or {}).get('risk_capped_funds')),
    }
    fund_style_context=fund_style_committee_context(fund_overlay, fund_recommendation_overlay)
    fund_age_context=fund_age_committee_context(fund_recommendation_overlay, fund_performance_context)
    fund_support_context=fund_support_committee_context(fund_recommendation_overlay)
    row['fund_age_committee_context']=fund_age_context
    row['fund_support_committee_context']=fund_support_context
    row['fund_performance_context']=fund_performance_context
    row['fund_recommendation_consensus']=fund_recommendation_overlay
    event_thesis_context=row.get('event_thesis_context') or event_thesis_context_for_row(row)
    sponsor_agents=set(fund_style_context.get('sponsor_agents') or [])
    for op in active:
        op['fund_style_role']='sponsor' if op.get('agent') in sponsor_agents else 'meta_review'
        op['fund_style_context']={
            'dominant_style': fund_style_context.get('dominant_style'),
            'sponsor_profile': fund_style_context.get('sponsor_profile'),
        }
    sponsor_rows=[o for o in active if o.get('agent') in sponsor_agents]
    meta_rows=[o for o in active if o.get('agent') not in sponsor_agents]
    sponsor_support=sum(1 for o in sponsor_rows if o.get('opinion') == 'support')
    sponsor_watch=sum(1 for o in sponsor_rows if o.get('opinion') == 'watch')
    meta_oppose=sum(1 for o in meta_rows if o.get('opinion') == 'oppose')
    hard_meta_oppose=sum(1 for o in risk_rows if o.get('opinion') == 'oppose')
    if fund_primary_candidate:
        # Fund-first committee model: funds originate the asset decision.
        # The committee supports or trims fund allocation by observed fund quality,
        # current market/disclosure/supply context, and cross-fund agreement.
        # It does not re-run the stock/strategy selection as a separate mandate.
        sponsor_bonus = sponsor_support * 5.0 + sponsor_watch * 2.0
        quality_bonus = max(0.0, min(10.0, float(fund_performance_context.get('avg_quality_score') or 0) * 0.08))
        positive_bonus = min(8.0, int(fund_performance_context.get('positive_fund_count') or 0) * 2.0)
        consensus_bonus = float(fund_recommendation_overlay.get('weighted_score') or 0) * 0.14 + int(fund_recommendation_overlay.get('buy_fund_count') or 0) * 1.0
        support_type = fund_support_context.get('support_type')
        if support_type in ('core_consensus', 'cross_style_consensus'):
            consensus_bonus += 5.0
        elif support_type in ('specialist_conviction', 'tactical_candidate'):
            consensus_bonus += 2.5
            if fund_support_context.get('isolated_young_weak_candidate'):
                consensus_bonus -= 3.0
        elif support_type == 'watch_only_candidate':
            consensus_bonus -= 2.0
        research_score = max(research_score, min(86.0, 50.0 + consensus_bonus + quality_bonus + positive_bonus + sponsor_bonus))
        score = round((research_score*0.55)+(risk_score*0.45),2)
    # Layer 1: is this worth paper research attention?
    # Hard risk is committee evidence, not a pre-review veto. Risk-focused
    # members can still pull the final decision down through scores/opposition.
    if fund_primary_candidate and (sponsor_support > 0 or sponsor_watch > 0):
        research_decision='support'
    elif research_score >= 58 or (row.get('score') or 0) >= 70 or research_support >= 1:
        research_decision='support'
    elif research_score >= 45:
        research_decision='watch'
    else:
        research_decision='ignore'
    # Layer 2: advisory risk channel only.
    # Validation depth belongs in the committee members' scores and concerns,
    # not in a pre-review gate that can stop the committee from issuing a view.
    # Hard risk is surfaced to the committee as a warning; it does not decide
    # approval/rejection outside the synthesis matrix.
    if hard_risk:
        risk_gate_decision='warning'
    else:
        risk_gate_decision='pass'
    # Matrix decision
    committee_support_threshold = 56 if fund_primary_candidate else 62
    risk_floor = 40 if fund_primary_candidate else 48
    if fund_primary_candidate:
        support_type = fund_support_context.get('support_type')
        if support_type in ('core_consensus', 'cross_style_consensus'):
            committee_support_threshold -= 2
            risk_floor -= 2
        elif support_type in ('specialist_conviction', 'tactical_candidate'):
            committee_support_threshold += 2
        elif support_type == 'watch_only_candidate':
            committee_support_threshold += 5
            risk_floor += 4
        if fund_support_context.get('isolated_young_weak_candidate'):
            committee_support_threshold += 4
            risk_floor += 5
    if research_decision == 'support' and score >= committee_support_threshold and risk_score >= risk_floor and risk_oppose <= 1:
        decision='committee_support'
    elif research_decision == 'support':
        decision='research_support'
    elif research_decision in ('watch','support'):
        decision='watch'
    else:
        decision='reject'
    hard_oppose={o['agent'] for o in risk_rows if o['opinion']=='oppose'}
    leaders=sorted(active,key=lambda o:o['score'],reverse=True)
    if (fund_overlay or fund_recommendation_overlay) and decision in ('watch','reject'):
        decision='research_support'
    for op in active:
        op['fund_age_role_context']={
            'posture': fund_age_context.get('posture'),
            'avg_age_days': fund_age_context.get('avg_age_days'),
            'age_buckets': fund_age_context.get('age_buckets'),
            'policy': fund_age_context.get('policy'),
        }
        op['fund_support_role_context']={
            'support_type': fund_support_context.get('support_type'),
            'supporting_fund_count': fund_support_context.get('supporting_fund_count'),
            'support_breadth_score': fund_support_context.get('support_breadth_score'),
            'isolated_candidate': fund_support_context.get('isolated_candidate'),
            'policy': fund_support_context.get('policy'),
        }
    context = market_committee_context(row, research_decision, risk_gate_decision, active)
    voice_leaders=sorted(active,key=lambda o:weights.get(o['agent'],0),reverse=True)[:2]
    voice_note=', '.join(f"{o['label']} {weights.get(o['agent'],0):.0%}" for o in voice_leaders)
    summary=(f"RiskGate {risk_gate_decision}({risk_score}) / Research {research_decision}({research_score}) → {decision}; "
             f"상위펀드 합의 {fund_overlay.get('votes',0) if fund_overlay else 0}, 고수익펀드 매수합의 {fund_recommendation_overlay.get('buy_fund_count',0) if fund_recommendation_overlay else 0}, "
             f"fund thesis {fund_style_context.get('dominant_style') or 'unknown'} sponsor {sponsor_support}/{len(sponsor_rows)} support, meta oppose {meta_oppose}/{len(meta_rows)}, "
             f"fund support {fund_support_context.get('support_type') or 'unknown'} breadth {fund_support_context.get('support_breadth_score')}, "
             f"fund sizing cap {len(fund_allocation_guardrail.get('risk_capped_funds') or [])}, "
             f"fund age {fund_age_context.get('posture') or 'unknown'} avg {fund_age_context.get('avg_age_days')}, "
             f"찬성 {support}, 반대 {oppose}; 성과발언권 {voice_note}; 최고 {leaders[0]['label']}={leaders[0]['opinion']}, 최저 {leaders[-1]['label']}={leaders[-1]['opinion']}")
    event_note = ''
    if event_thesis_context.get('matched'):
        event_note = f" Event thesis: {compact_event_thesis(event_thesis_context)}. 공격형은 수혜 채널을 전술 후보로 보고, 안전형/검증형은 risk-off와 뉴스 thesis 검증 필요성을 반영합니다."
    readable_summary = f"{context['approval_note']} Fund thesis: {fund_style_context.get('thesis')}. Support type: {fund_support_context.get('support_type') or 'unknown'}, breadth {fund_support_context.get('support_breadth_score')}.{event_note} 성향 담당 위원은 해당 펀드군의 판단을 지지/축소 관점에서 해석하고, 메타 검토 위원은 시장/공시/수급/다른 펀드 성과/사이징을 재확인합니다. 성과 기반 발언권은 {voice_note} 순입니다. 공격형 해석: {context['aggressive_note']} 현재 보류 근거: {', '.join(context['market_stress_reasons'])}."
    return {'score':score,'decision':decision,'summary':summary,'support_count':support,'oppose_count':oppose,'hard_oppose_agents':sorted(hard_oppose),'weights':{o['agent']:weights.get(o['agent'],0) for o in active},'fill_assumptions':{o['agent']:o.get('fill_assumption') for o in active},
            'readable_summary': readable_summary, 'market_context': context,
            'fund_consensus':fund_overlay,'fund_recommendation_consensus':fund_recommendation_overlay,'fund_performance_context':fund_performance_context,'fund_allocation_guardrail':fund_allocation_guardrail,'fund_style_committee_context':fund_style_context,'fund_age_committee_context':fund_age_context,'fund_support_committee_context':fund_support_context,'fund_age_committee_policy':fund_age_context.get('policy'),'event_thesis_context':event_thesis_context,'fund_primary_candidate':fund_primary_candidate,'research_committee':{'decision':research_decision,'score':research_score,'support_count':research_support,'oppose_count':research_oppose,'sponsor_support_count':sponsor_support,'sponsor_watch_count':sponsor_watch,'meta_oppose_count':meta_oppose,'hard_meta_oppose_count':hard_meta_oppose,'agents':[o['agent'] for o in research_rows]},
            'risk_gate':{'decision':risk_gate_decision,'score':risk_score,'support_count':risk_support,'oppose_count':risk_oppose,'hard_risk':bool(hard_risk),'under_validated':bool(under_validated),'fund_diagnostic_under_validated':bool(fund_diagnostic_under_validated),'fund_cap_applied':bool(fund_allocation_guardrail.get('cap_applied')),'fund_cap_policy':'sizing_guardrail_not_veto','agents':[o['agent'] for o in risk_rows]}}


def distance_pct(price, ref):
    try:
        price = float(price)
        ref = float(ref)
        if not price or not ref:
            return None
        return round((price / ref - 1) * 100, 2)
    except Exception:
        return None


def price_posture(row: dict, synthesis: dict, gate: dict) -> dict:
    """Convert committee context into UI price-highlight intent.

    The committee should not pick a single magic price. It chooses a posture
    from market/sector/symbol/risk context; the UI then highlights the price
    fields that match the next paper-only action.
    """
    vb = row.get('validation_basis') or {}
    tech = vb.get('technical_risk_context') or row.get('technical_risk_context') or {}
    mover = vb.get('mover_context') or row.get('mover_context') or {}
    risk_gate = synthesis.get('risk_gate') or {}
    research = synthesis.get('research_committee') or {}
    current = row.get('last_price') or row.get('analysis_price')
    target_1 = row.get('target_1')
    stop = row.get('stop_reference')
    benchmark20 = pct(vb.get('benchmark_20d_return_pct'))
    symbol20 = pct(vb.get('symbol_20d_return_pct'))
    symbol60 = pct(vb.get('symbol_60d_return_pct'))
    mover_change = pct(mover.get('change_pct'))
    target_distance = distance_pct(target_1, current)
    stop_distance = distance_pct(current, stop)

    targets = ['entry_band', 'entry_lower', 'entry_upper']
    posture = 'balanced'
    reason = '시장/종목 맥락이 중립권이라 평균 진입 밴드를 우선 강조'

    hard_block = gate.get('recommendation_bucket') == 'rejected'
    hard_warning = bool(risk_gate.get('hard_risk'))
    volatile = tech.get('atr_bucket') == 'high' or abs(mover_change) >= 12
    overheated = bool(tech.get('overheated_chase_risk')) or benchmark20 >= 8 or symbol20 >= 12 or symbol60 >= 25 or mover_change >= 18
    oversold_research = (
        not hard_block
        and research.get('decision') == 'support'
        and (benchmark20 <= -5 or symbol20 <= -8 or symbol60 <= -15 or mover_change <= -12)
    )

    if target_distance is not None and 0 <= target_distance <= 2.5 and not hard_block:
        posture = 'take_profit'
        targets = ['target_1']
        reason = f"1차 실현가까지 {target_distance}% 남아 실현 계획을 우선 강조"
    elif hard_warning and not hard_block:
        posture = 'balanced'
        targets = ['entry_lower', 'stop_reference', 'position_size']
        reason = 'hard risk warning이 있어 위원회 판단 안에서 낮은 진입가·손절·사이징을 함께 강조'
    elif stop_distance is not None and 0 <= stop_distance <= 3.0:
        posture = 'avoid'
        targets = ['stop_reference']
        reason = f"무효화/손절 기준까지 {stop_distance}% 거리라 이탈 계획을 우선 강조"
    elif hard_block:
        posture = 'avoid'
        targets = ['stop_reference', 'chase_above']
        reason = '위원회/Risk Gate가 차단 또는 보류해 매입가보다 리스크 기준을 강조'
    elif overheated:
        posture = 'defensive'
        targets = ['entry_band', 'entry_lower', 'stop_reference']
        reason = '시장 또는 종목 단기 과열 신호가 있어 보수 진입/무효화 기준을 강조'
    elif oversold_research:
        posture = 'aggressive'
        targets = ['entry_band', 'entry_upper', 'target_2']
        reason = '단기 약세/과매도 구간에서 Research Committee 지지가 있어 공격 진입 밴드를 허용'
    elif volatile:
        posture = 'defensive'
        targets = ['entry_band', 'entry_lower', 'stop_reference']
        reason = '변동성이 커서 평균 진입보다 보수 밴드와 이탈 기준을 강조'

    return {
        'committee_posture': posture,
        'posture_reason': reason,
        'highlight_targets': targets,
        'inputs': {
            'benchmark_20d_return_pct': vb.get('benchmark_20d_return_pct'),
            'symbol_20d_return_pct': vb.get('symbol_20d_return_pct'),
            'symbol_60d_return_pct': vb.get('symbol_60d_return_pct'),
            'atr_bucket': tech.get('atr_bucket'),
            'overheated_chase_risk': tech.get('overheated_chase_risk'),
            'mover_change_pct': mover.get('change_pct'),
            'risk_gate_decision': risk_gate.get('decision'),
            'research_decision': research.get('decision'),
            'target_1_distance_pct': target_distance,
            'stop_distance_pct': stop_distance,
        },
        'policy': 'committee_context_selects_posture_ui_highlights_not_price_vote',
    }



def summarize_gate_reason(gate: dict) -> str:
    blockers=gate.get('blockers') or []
    cautions=gate.get('cautions') or []
    labels={
        'base_action_not_buy_candidate':'기본 추천 단계가 매수 후보가 아님',
        'critic_high':'critic 고위험',
        'disclosure_risk':'공시 리스크',
        'corporate_action_risk':'기업행위 리스크',
        'financial_hard_risk':'재무 하드 리스크',
        'thin_symbol_validation':'종목별 검증 표본 부족',
        'no_positive_symbol_edge':'펀드 thesis 사후 성과근거 부족',
        'left_tail_ev_guard':'좌측꼬리/기대값 수익률 개선 게이트',
        'thin_no_edge_profit_guard':'펀드 thesis 성과근거 부족 수익률 개선 게이트',
        'weak_excess_win_rate':'초과승률 약함',
        'score_below_buy_gate':'점수 기준 미달',
        'risk_gate_needs_more_validation':'위원회 검증 주의',
        'committee_watch':'위원회 관찰 판정',
        'committee_reject':'위원회 보류 판정',
        'mover_seed_requires_validation':'금일 mover seed: 검증 우선순위 상향',
    }
    parts=[]
    for b in blockers:
        if str(b).startswith('committee_'):
            parts.append('위원회 추가 확인 필요')
        else:
            parts.append(labels.get(b, b))
    for c in cautions:
        parts.append(labels.get(c, c))
    # De-duplicate while preserving order and keep UI text short.
    compact=[]
    for x in parts:
        if x and x not in compact:
            compact.append(x)
    return ' / '.join(compact[:3])


def buy_gate_diagnostics(row: dict, gate: dict, synthesis: dict) -> dict:
    vb = row.get('validation_basis') or {}
    watch = row.get('watch_reason') or {}
    disc = row.get('disclosure_risk') or {}
    fund_priority = vb.get('fund_recommendation_priority') or {}
    reasons = []
    if not (fund_priority.get('primary_review_candidate') or vb.get('fund_consensus_review_candidate')):
        reasons.append('not_fund_primary_candidate')
    if gate.get('trade_eligible'):
        reasons.append('committee_support_approved')
    fund_primary_candidate = bool(fund_priority.get('primary_review_candidate') or vb.get('fund_consensus_review_candidate'))
    if (row.get('score') or 0) < 65 and not fund_primary_candidate:
        reasons.append('score_below_buy_gate')
    if (disc.get('high') or 0) > 0:
        reasons.append('high_risk_disclosure_advisory')
    if disclosure_effective_medium(disc) >= 2:
        reasons.append('medium_disclosure_advisory')
    if gate.get('risk_gate_decision') in ('warning', 'needs_more_validation'):
        reasons.append('committee_risk_gate_' + str(gate.get('risk_gate_decision')))
    for code in gate.get('blockers') or []:
        reasons.append(str(code))
    for code in gate.get('cautions') or []:
        if code in ('score_below_buy_gate', 'no_positive_symbol_edge', 'thin_no_edge_profit_guard', 'left_tail_ev_guard'):
            reasons.append(str(code))
    for block in watch.get('blockers') or []:
        code = block.get('code')
        if code:
            reasons.append(str(code))
    compact = []
    for reason in reasons:
        if reason and reason not in compact:
            compact.append(reason)
    return {
        'eligible': bool(gate.get('trade_eligible')),
        'action': row.get('action'),
        'score': row.get('score'),
        'candidate_threshold': 65,
        'fund_primary_review_candidate': fund_primary_candidate,
        'fund_recommendation_buy_fund_count': (vb.get('decision_evidence_links') or {}).get('fund_recommendation_buy_fund_count'),
        'committee_decision': synthesis.get('decision'),
        'committee_risk_gate_decision': gate.get('risk_gate_decision'),
        'primary_reason': compact[0] if compact else None,
        'reasons': compact[:8],
        'policy': 'advisory_diagnostics_only_committee_controls_bucket',
    }


def aggregate_buy_gate_diagnostics(items: list[dict]) -> dict:
    rows = [item.get('buy_gate_diagnostics') or {} for item in items]
    counts = {}
    for row in rows:
        reason = row.get('primary_reason') or ('buy_gate_passed' if row.get('eligible') else 'unknown')
        counts[reason] = counts.get(reason, 0) + 1
    fund_rows = [row for row in rows if row.get('fund_primary_review_candidate')]
    return {
        'policy': 'fund_recommendations_feed_primary_review; diagnostics are advisory and committee synthesis controls bucket',
        'candidate_threshold': 65,
        'selected_count': len(items),
        'trade_eligible_count': sum(1 for row in rows if row.get('eligible')),
        'fund_primary_review_count': len(fund_rows),
        'primary_reason_counts': counts,
        'top_non_approved_fund_rows': [row for row in fund_rows if not row.get('eligible')][:10],
    }


def qualify_watch_only_momentum_text(text: str, gate: dict) -> str:
    """Make strong relative-strength text read as watch-only when buy approval failed."""
    if not text:
        return text
    out = re.sub(
        r"20일 상대강도가 벤치마크보다 \+(.*?) 강합니다\.",
        r"20일 상대강도는 벤치마크보다 +\1 강하지만, 단독 추천 근거가 아니라 관찰용 보조 신호입니다.",
        text,
        count=1,
    )
    if gate.get('recommendation_bucket') in ('research_watch', 'watch', 'rejected') and not out.startswith('승인 보류:'):
        reason = summarize_gate_reason(gate) or gate.get('reason') or '추가 검증 필요'
        out = f"승인 보류: {reason}. " + out
    return out


def committee_rationale(committee: dict) -> dict:
    opinions = committee.get('opinions') or []
    syn = committee.get('synthesis') or {}
    supporters = [o for o in opinions if o.get('opinion') == 'support']
    watchers = [o for o in opinions if o.get('opinion') == 'watch']
    opposers = [o for o in opinions if o.get('opinion') == 'oppose']
    def bits(rows, key, limit=4):
        out=[]
        for o in sorted(rows, key=lambda x: float(x.get('score') or 0), reverse=True):
            vals=o.get(key) or []
            if not vals:
                continue
            label=o.get('label') or o.get('agent')
            out.append(f"{label}: {', '.join(vals[:2])}")
            if len(out) >= limit: break
        return out
    support_bits = bits(supporters, 'supports', 3)
    if not support_bits and watchers:
        support_bits = bits(watchers, 'supports', 2)
    oppose_bits = bits(opposers, 'concerns', 3)
    short=[]
    if support_bits:
        short.append('지지/관찰 근거: ' + ' | '.join(support_bits))
    if oppose_bits:
        short.append('반대 근거: ' + ' | '.join(oppose_bits))
    if syn.get('summary'):
        short.append('종합: ' + syn['summary'])
    if syn.get('readable_summary'):
        short.insert(0, syn['readable_summary'])
    return {
        'supporters': [{'agent':o.get('agent'),'label':o.get('label'),'score':o.get('score'),'supports':o.get('supports') or []} for o in supporters],
        'watchers': [{'agent':o.get('agent'),'label':o.get('label'),'score':o.get('score'),'supports':o.get('supports') or [],'concerns':o.get('concerns') or []} for o in watchers],
        'opposers': [{'agent':o.get('agent'),'label':o.get('label'),'score':o.get('score'),'concerns':o.get('concerns') or []} for o in opposers],
        'support_summary': ' / '.join(support_bits) if support_bits else '',
        'oppose_summary': ' / '.join(oppose_bits) if oppose_bits else '',
        'plain_summary': ' '.join(short[:3]),
    }

def trade_gate(row, synthesis):
    """Expose trade-readiness diagnostics without making a second decision.

    The investment committee is the final paper-recommendation decision maker.
    This helper used to re-gate committee output with score/caution counts,
    which created a second bottleneck after the committee had already reviewed
    the fund-backed candidate. Keep the diagnostics for UI and follow-up work,
    but derive eligibility/bucket directly from committee synthesis.
    """
    vb=row.get('validation_basis') or {}
    critic=row.get('critic') or {}
    disc=row.get('disclosure_risk') or {}
    fq=row.get('financial_quality') or {}
    blockers=[]
    cautions=[]
    risk_gate = synthesis.get('risk_gate') or {}
    committee_decision = synthesis.get('decision') or 'unknown'
    if committee_decision not in ('committee_support','research_support'):
        gate_code = f"committee_{committee_decision}"
        cautions.append(gate_code)
    elif synthesis.get('decision') == 'research_support':
        cautions.append('risk_gate_needs_more_validation')
    if critic.get('severity') == 'high' and critic.get('issue_type') == 'blocking':
        cautions.append('critic_high_advisory')
    elif critic.get('severity') == 'high':
        cautions.append('critic_high_not_hard_blocking')
    fund_primary_gate = is_fund_primary_row(row)
    if (critic.get('issue_type') == 'under_validated' or critic.get('severity') == 'under_validated') and not fund_primary_gate:
        cautions.append('under_validated_not_blocking')
    if (disc.get('high') or 0) > 0 or disclosure_effective_medium(disc) >= 3:
        cautions.append('disclosure_risk_advisory')
    if row.get('corporate_action_risk', {}).get('flagged'):
        cautions.append('corporate_action_risk_advisory')
    if (fq.get('score_adjustment') or 0) <= -20:
        cautions.append('financial_hard_risk_advisory')
    # Thin symbol validation is tracked through validation_priority/under_validated,
    # not repeated as a per-card trade-gate caution. For fund-primary rows it is
    # supporting diagnostic context only; funds choose candidates and the
    # committee reviews hard risk, sizing, market, disclosure, and price context.
    if (vb.get('positive_symbol_edge_count') or 0) <= 0 and not fund_primary_gate:
        cautions.append('no_positive_symbol_edge')
    if (vb.get('tail_kill_signal_count') or 0) > 0:
        cautions.append('left_tail_ev_guard')
    audit_authority_blocked = bool(vb.get('audit_buy_zone_authority_blocked'))
    if audit_authority_blocked:
        if fund_primary_gate:
            cautions.append('audit_buy_zone_authority_block_advisory')
        else:
            blockers.append('audit_buy_zone_authority_blocked')
    if vb.get('thin_no_edge_gate') and not fund_primary_gate:
        cautions.append('thin_no_edge_profit_guard')
    # Weak aggregate excess win rate is handled as portfolio/strategy-quality context,
    # not repeated as a per-candidate trade-gate caution.
    if (row.get('score') or 0) < 65 and not fund_primary_gate:
        cautions.append('score_below_buy_gate')
    # base_not_buy is an aggregate recommendation-surface state. Repeating it on
    # every card hides the concrete critic/risk reasons, so org_evaluator tracks
    # it as an aggregate bottleneck instead of an item-level caution.
    research_watch = ((row.get('score') or 0) >= 65
                      and ('under_validated_not_blocking' in cautions or (vb.get('symbol_validation_sample_count') or 0) < 10 or (vb.get('positive_symbol_edge_count') or 0) <= 0))
    priority_points=0
    if synthesis.get('research_committee',{}).get('score',0) >= 65: priority_points += 2
    if (row.get('score') or 0) >= 75: priority_points += 2
    if row.get('market_issue_context') or row.get('market_context'): priority_points += 1
    mover_context=(row.get('validation_basis') or {}).get('mover_context') or row.get('mover_context')
    if mover_context:
        priority_points += 2 if abs(float(mover_context.get('change_pct') or 0)) >= 10 else 1
        if not fund_primary_gate:
            cautions.append('mover_seed_requires_validation')
    samples=vb.get('symbol_validation_sample_count') or 0
    if samples >= 5: priority_points += 1
    if (row.get('downside_stop_pct') is not None) and abs(row.get('downside_stop_pct') or 0) <= 8: priority_points += 1
    validation_priority='high' if priority_points >= 5 else ('medium' if priority_points >= 3 else 'low')
    trade_eligible = synthesis.get('decision') == 'committee_support'
    if blockers:
        trade_eligible = False
    if trade_eligible:
        bucket = 'approved'
        label = '승인 추천'
    elif synthesis.get('decision') == 'research_support':
        bucket = 'research_watch'
        label = '위원회 연구 지지'
    elif synthesis.get('decision') == 'reject':
        bucket = 'rejected'
        label = '위원회 보류'
    else:
        bucket = 'watch'
        label = '위원회 관찰'
    return {
        'trade_eligible': trade_eligible,
        'recommendation_bucket': bucket,
        'bucket_label': label,
        'blockers': blockers,
        'cautions': cautions,
        'reason': ' / '.join(blockers or cautions or ['committee decision accepted']),
        'mode': 'advisory_only_committee_controls_bucket',
        'advisory_only': True,
        'research_decision': (synthesis.get('research_committee') or {}).get('decision'),
        'risk_gate_decision': (synthesis.get('risk_gate') or {}).get('decision'),
        'validation_priority': validation_priority,
        'validation_priority_points': priority_points,
    }



def load_overlay_opinions() -> dict:
    out={}
    for path in ['/tmp/recommendation_opinions_critic_latest.json','/tmp/recommendation_opinions_portfolio_latest.json','/tmp/recommendation_opinions_regime_latest.json']:
        p=Path(path)
        if not p.exists():
            continue
        try:
            packet=json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        for op in packet.get('opinions') or []:
            sym=op.get('symbol')
            if not sym:
                continue
            out.setdefault(sym,[]).append(op)
    return out


def apply_overlay_opinions(row: dict, opinions: list[dict]) -> dict:
    notes=row.get('risk_notes') or []
    for op in opinions or []:
        prefixes=op.get('remove_risk_note_prefixes') or []
        if prefixes:
            notes=[n for n in notes if not any(str(n).startswith(pref) for pref in prefixes)]
        for k,v in (op.get('overlay') or {}).items():
            if v is not None:
                if k == 'validation_basis' and isinstance(row.get(k), dict) and isinstance(v, dict):
                    merged=dict(v)
                    merged.update(row.get(k) or {})  # current recommendation fields win over stale overlay snapshots
                    row[k]=merged
                else:
                    row[k]=v
        if op.get('watch_reason_patch'):
            wr=row.get('watch_reason') or {}
            wr.update(op.get('watch_reason_patch') or {})
            row['watch_reason']=wr
        for note in op.get('risk_notes_append') or []:
            if note and note not in notes:
                notes.append(note)
    row['risk_notes']=notes
    if opinions:
        row['recommendation_opinions']=[{'agent':op.get('agent'),'final_field_writer':op.get('final_field_writer',False)} for op in opinions]
    return row

def main():
    path=Path('/tmp/recommendations_latest.json')
    data=json.loads(path.read_text(encoding='utf-8'))
    overlay_opinions=load_overlay_opinions()
    weights=load_weights(); rows=[]; downgrades=[]
    event_thesis_packet=load_event_thesis_context()
    fund_recommendation_packet_for_roles=load_fund_recommendation_context()
    fund_performance_packet_for_roles=load_fund_performance_context()
    committee_bucket_changes=[]
    updated_items=[]
    for row in data.get('items',[]):
        row['event_thesis_context']=event_thesis_context_for_row(row, event_thesis_packet)
        row_fund_recommendation=fund_recommendation_overlay_for_symbol(row.get('symbol'), fund_recommendation_packet_for_roles)
        row_fund_performance=fund_performance_overlay_for_recommendation(row_fund_recommendation, fund_performance_packet_for_roles)
        row['fund_recommendation_consensus']=row_fund_recommendation
        row['fund_performance_context']=row_fund_performance
        row['fund_age_committee_context']=fund_age_committee_context(row_fund_recommendation, row_fund_performance)
        row['fund_support_committee_context']=fund_support_committee_context(row_fund_recommendation)
        source_bucket=row.get('recommendation_bucket') or row.get('bucket')
        row['pre_committee_recommendation_bucket']=source_bucket
        row=apply_overlay_opinions(row, overlay_opinions.get(row.get('symbol')) or [])
        opinions=[]
        for fn in EVALUATORS:
            op=fn(row); op['active']=relevant(op,row); opinions.append(op)
        syn=synthesize(row,opinions,weights)
        row['investment_committee']={'version':'adaptive_v1','opinions':opinions,'synthesis':syn}
        rationale=committee_rationale(row['investment_committee'])
        row['investment_committee']['rationale']=rationale
        row['committee_rationale']=rationale
        gate=trade_gate(row, syn)
        row['trade_eligible']=gate['trade_eligible']
        row['recommendation_bucket']=gate['recommendation_bucket']
        row['bucket']=gate['recommendation_bucket']
        row['recommendation_bucket_label']=gate['bucket_label']
        if gate['recommendation_bucket'] == 'approved':
            row['action'] = 'candidate_buy_zone'
            row['action_label'] = '관심 매수 후보'
        elif gate['recommendation_bucket'] in ('research_watch', 'watch'):
            row['action'] = 'watch'
            row['action_label'] = '관망'
        elif gate['recommendation_bucket'] == 'rejected':
            row['action'] = 'avoid'
            row['action_label'] = '제외'
        if source_bucket != gate['recommendation_bucket']:
            transition={'symbol':row.get('symbol'),'old_bucket':source_bucket,'new_bucket':gate['recommendation_bucket'],'reason':gate.get('reason'),'research_decision':gate.get('research_decision'),'risk_gate_decision':gate.get('risk_gate_decision')}
            committee_bucket_changes.append(transition)
            row['committee_bucket_transition']=transition
        row['trade_gate']=gate
        row['buy_gate_diagnostics']=buy_gate_diagnostics(row, gate, syn)
        row['validation_priority']=gate.get('validation_priority')
        posture=price_posture(row, syn, gate)
        syn['committee_posture']=posture['committee_posture']
        syn['posture_reason']=posture['posture_reason']
        syn['highlight_targets']=posture['highlight_targets']
        syn['posture_inputs']=posture['inputs']
        syn['highlight_policy']=posture['policy']
        row['committee_posture']=posture['committee_posture']
        row['posture_reason']=posture['posture_reason']
        row['highlight_targets']=posture['highlight_targets']
        row['price_highlight_policy']=posture['policy']
        human=row.get('human_summary') if isinstance(row.get('human_summary'), dict) else {}
        name=row.get('name') or row.get('symbol')
        if gate['recommendation_bucket'] == 'approved':
            human['headline']='위원회 통과, paper 추적 후보'
            human['suggested_action']='목표가·손절 기준·포지션 크기 제한을 paper 기준으로 추적하세요.'
        elif gate['recommendation_bucket'] == 'paper_buy_candidate':
            human['headline']='Paper 매수후보로 승격, 단 Risk Gate 검증중'
            human['suggested_action']='실거래 없이 paper 기준으로 목표가·손절·성과를 집중 추적하세요.'
        elif gate['recommendation_bucket'] == 'research_watch':
            human['headline']='위원회 검토 완료, 연구 관찰'
            human['suggested_action']='paper-only research watch로 두고 위원회 감점 요인이 완화되는지 확인하세요.'
        elif gate['recommendation_bucket'] == 'rejected':
            human['headline']='아직 매수 후보로 보기엔 근거 부족'
            human['suggested_action']='근거가 보강될 때까지 paper 관찰만 유지하세요.'
        else:
            human['headline']='관찰 유지, 추가 검증 대기'
            human['suggested_action']='추가 검증 결과가 쌓이는지 확인하며 관찰하세요.'
        human['committee_view']=rationale.get('plain_summary') or syn.get('summary') or human.get('committee_view') or ''
        human['price_posture']=posture['posture_reason']
        if rationale.get('support_summary'):
            why = human.get('why_now') or ''
            support_sentence = '위원회 지지/관찰 근거: ' + rationale['support_summary'] + '.'
            if support_sentence not in why:
                human['why_now'] = (why + ' ' + support_sentence).strip()
        if not gate['trade_eligible'] and human.get('why_now'):
            human['why_now'] = qualify_watch_only_momentum_text(human['why_now'], gate)
        human['main_risk']=summarize_gate_reason(gate) or human.get('main_risk')
        row['human_summary']=human
        # Add a compact committee explanation to the long recommendation reason without extra model/API calls.
        if rationale.get('plain_summary'):
            base_reason = row.get('recommendation_reason') or ''
            addendum = ' 위원회 해석: ' + rationale['plain_summary']
            if addendum.strip() not in base_reason:
                row['recommendation_reason'] = (base_reason.rstrip() + addendum).strip()
        notes=[n for n in (row.get('risk_notes') or []) if not str(n).startswith(('투자성향 위원회:', '펀드 메타위원회:'))]
        gate_note='위원회 감점/주의: '+(summarize_gate_reason(gate) or gate['reason'])
        if not gate['trade_eligible'] and gate_note not in notes:
            notes.insert(0, gate_note)
        committee_note='펀드 메타위원회: '+syn['summary']
        if committee_note not in notes:
            notes.append(committee_note)
        row['risk_notes']=notes
        if syn['decision']!='committee_support' and row.get('action')=='candidate_buy_zone':
            row['action']='watch'; row['action_label']='위원회 관망'
            wr=row.get('watch_reason') or {}; wr['primary']='펀드 메타위원회 관망: '+syn['summary']; wr['committee_downgraded']=True; row['watch_reason']=wr
            row['risk_notes'].insert(0,'관망 이유: '+wr['primary'])
            downgrades.append({'symbol':row.get('symbol'),'decision':syn['decision'],'score':syn['score']})
        rows.append({'symbol':row.get('symbol'),'name':row.get('name'),'action':row.get('action'),'trade_eligible':row.get('trade_eligible'),'recommendation_bucket':row.get('recommendation_bucket'),'trade_gate':row.get('trade_gate'),'buy_gate_diagnostics':row.get('buy_gate_diagnostics'),'committee':row['investment_committee']})
        updated_items.append(row)
    buy_gate_summary=aggregate_buy_gate_diagnostics(updated_items)
    summary={'item_count':len(rows),'downgrade_count':len(downgrades),'bucket_change_count':len(committee_bucket_changes),'support_count':sum(1 for r in rows if r['committee']['synthesis']['decision']=='committee_support'),'research_support_count':sum(1 for r in rows if r['committee']['synthesis']['decision']=='research_support'),'watch_count':sum(1 for r in rows if r['committee']['synthesis']['decision']=='watch'),'reject_count':sum(1 for r in rows if r['committee']['synthesis']['decision']=='reject'),'trade_eligible_count':sum(1 for r in rows if r.get('trade_eligible')),'approved_count':sum(1 for r in rows if r.get('recommendation_bucket')=='approved'),'bucket_counts':{k:sum(1 for r in rows if r.get('recommendation_bucket')==k) for k in ('approved','paper_buy_candidate','research_watch','watch','rejected')},'buy_gate_diagnostics':buy_gate_summary}
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'fund_first_meta_investment_committee','event_thesis_policy':'event_thesis_v1_advisory_role_specific','event_thesis_summary':event_thesis_packet.get('summary') or {},'bucket_changes':committee_bucket_changes,'committee_fill_policy':COMMITTEE_FILL_ASSUMPTIONS,'opinion_sources':sorted({op.get('agent') for ops in overlay_opinions.values() for op in ops if op.get('agent')}),'real_trading':False,'buy_gate_diagnostics':buy_gate_summary,'weights':weights,'items':rows,'downgrades':downgrades,'summary':summary}
    attach_contract(packet,'investment_committee',status='ok',outputs={'item_count':len(rows),'downgrade_count':len(downgrades)},metrics=summary,warnings=[f"committee_downgraded:{d['symbol']}" for d in downgrades],next_actions=[])
    outcome_update = update_weights_from_outcomes(weights)
    if outcome_update and outcome_update[1].get('mode') == 'outcome_performance':
        learned_weights, performance, source_run_at = outcome_update
        performance['fallback_audit_proxy_used'] = False
    else:
        learned_weights, performance, source_run_at = update_weights_from_audit_proxy(weights, rows)
        if outcome_update:
            performance['outcome_status'] = outcome_update[1]
        performance['fallback_audit_proxy_used'] = True
    packet['learned_next_weights']=learned_weights
    packet['weight_performance']=performance
    write_json(LATEST_PATH, packet)
    append_committee_history(packet)
    save_weights(learned_weights, performance, source_run_at)
    data['items']=updated_items
    data['buy_gate_diagnostics']=buy_gate_summary
    data.setdefault('recommendation_changes', {})['post_committee_bucket_changes']=committee_bucket_changes[:20]
    data.setdefault('recommendation_changes', {})['post_committee_bucket_change_count']=len(committee_bucket_changes)
    data.setdefault('recommendation_changes', {})['change_count']=(data.get('recommendation_changes',{}).get('change_count') or 0)+len(committee_bucket_changes)
    history_update = persist_post_committee_history(updated_items)
    packet['history_update'] = history_update
    data.setdefault('recommendation_changes', {})['post_committee_history_update'] = history_update
    static_history_update = {'updated': False, 'reason': 'disabled_db_source_of_truth'}
    packet['static_history_update'] = static_history_update
    data.setdefault('recommendation_changes', {})['post_committee_static_history_update'] = static_history_update
    write_json(path, data)
    print(json.dumps(packet,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
