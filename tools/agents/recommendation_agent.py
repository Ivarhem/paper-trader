#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.config import get_settings
from app.database import init_db, latest_financial_quality, list_strategy_registry, list_universe_members, latest_investor_flow_for_symbol
from tools.agents.lib.agent_contract import attach_contract
from app.symbols import display_name

HORIZON_DAYS = 20
TARGET_RETURN_ADJUSTMENT_PCT_POINTS = 1.5  # fallback target-return parameter haircut, paper research only
TARGET_RETURN_PARAMETER_META_PATH = Path('/tmp/target_return_adjustment_evaluator_latest.json')

def load_target_return_parameter_policy() -> dict:
    fallback = {
        'adjustment_pct_points': -TARGET_RETURN_ADJUSTMENT_PCT_POINTS,
        'source': 'fallback_default',
        'decision': 'fallback_default',
        'basis': 'default_until_target_return_parameter_meta_has_completed_samples',
    }
    try:
        packet = json.loads(TARGET_RETURN_PARAMETER_META_PATH.read_text(encoding='utf-8'))
    except Exception:
        return fallback
    decision = packet.get('meta_decision') or {}
    selected = decision.get('selected_adjustment_pct_points')
    # Only consume the meta agent when it explicitly has enough outcome evidence.
    if decision.get('decision') not in ('keep_current', 'test_or_promote_parameter_arm') or selected is None:
        return {**fallback, 'source': 'meta_hold_fallback', 'meta_decision': decision.get('decision'), 'meta_run_at': packet.get('run_at')}
    try:
        selected = float(selected)
    except Exception:
        return fallback
    return {
        'adjustment_pct_points': selected,
        'source': 'target_return_parameter_meta_evaluator',
        'decision': decision.get('decision'),
        'basis': decision.get('reason'),
        'meta_run_at': packet.get('run_at'),
        'candidate_adjustments': decision.get('candidate_adjustments') or [],
    }

def load_strategy_context_router() -> dict:
    try:
        return json.loads(Path('/tmp/strategy_context_router_latest.json').read_text(encoding='utf-8'))
    except Exception:
        return {}

def router_row_for(logic: str, router: dict) -> dict:
    return ((router.get('by_logic') or {}).get(logic) or {}) if isinstance(router, dict) else {}

def load_fund_consensus() -> dict:
    try:
        return json.loads(Path('/tmp/fund_consensus_latest.json').read_text(encoding='utf-8'))
    except Exception:
        return {}

def fund_consensus_for_symbol(symbol: str, packet: dict) -> dict:
    for row in (packet.get('symbol_consensus') or []):
        if row.get('symbol') == symbol:
            return row
    return {}


def fund_style_context_alignment(router_family: str | None, regime: str | None, styles: dict | None) -> dict:
    """Score fund consensus only when its style agrees with the active regime/router.

    Fund votes are useful, but a mean-reversion fund cluster should not heavily
    boost breakout/trend recommendations during a risk-on regime unless the
    strategy family itself is mean-reversion/range-oriented.
    """
    styles=styles or {}
    router_family=router_family or 'general'
    regime=regime or 'unknown'
    preferred_by_regime={
        'risk_on_or_strong': {'trend_strength','breakout_volume'},
        'risk_off_or_weak': {'mean_reversion','range_grid'},
    }.get(regime, {'trend_strength','breakout_volume','mean_reversion','range_grid'})
    family_to_style={
        'trend_strength': ['trend','volume_surge'],
        'breakout_volume': ['breakout','volume_surge'],
        'mean_reversion': ['mean_reversion'],
        'range_grid': ['balanced','mean_reversion'],
        'general': ['balanced'],
    }
    keys=family_to_style.get(router_family, ['balanced'])
    raw=sum(float(styles.get(k) or 0) for k in keys)
    aligned=router_family in preferred_by_regime or router_family == 'general'
    if aligned:
        multiplier=1.0
        cap=6.0
    else:
        multiplier=0.35
        cap=2.0
    return {
        'router_family': router_family,
        'regime': regime,
        'style_keys': keys,
        'raw_style_score': round(raw,2),
        'aligned_with_regime': aligned,
        'multiplier': multiplier,
        'boost': round(min(cap, raw * multiplier), 2),
        'policy': 'fund_style_consensus_regime_aligned_boost_only',
    }


def action_label(action: str) -> str:
    return {'candidate_buy_zone':'관심 매수 후보','watch':'관망','avoid':'제외'}.get(action, action)

def logic_label(logic: str) -> str:
    if logic == 'balanced_range_v1': return '균형형 박스권 돌파 전략'
    if logic == 'conservative_range_v1': return '보수형 박스권 돌파 전략'
    if logic.startswith('range_grid_'): return '검증형 그리드 전략'
    if logic.startswith('technical_'): return '데이터 전용 기술지표 전략'
    return logic

def audit_flag_label(flag: str) -> str:
    labels = {
        'no_positive_average_excess': '초과수익 신뢰 낮음',
        'left_tail_excess_risk': '하락 꼬리위험 주의',
        'period_instability': '국면 의존',
        'negative_expected_excess_value': '기대 초과값 약함',
        'weak_success_confidence_interval': '표본 신뢰구간 부족',
        'recent_decay': '최근 성과 둔화',
    }
    return labels.get(str(flag), str(flag).replace('_', ' '))

def has_final_consonant(text: str) -> bool:
    if not text:
        return False
    ch = text[-1]
    code = ord(ch) - 0xAC00
    if 0 <= code <= 11171:
        return code % 28 != 0
    return False


def particle(text: str, pair: str = '은/는') -> str:
    a, b = pair.split('/')
    return a if has_final_consonant(text) else b


def korean_reason(text: str) -> str:
    return (text.replace('60d breakout confirmation', '60일 돌파 확인')
                .replace('20d breakout confirmation', '20일 돌파 확인')
                .replace('60d trend confirmation', '60일 추세 확인')
                .replace('120d trend confirmation', '120일 추세 확인')
                .replace('ma20>ma50>ma120 trend quality', '이동평균 정배열 확인')
                .replace('volume confirmation', '거래량 확인')
                .replace('20d momentum support', '20일 흐름 보조 신호')
                .replace('60d trend support', '60일 흐름 보조 신호')
                .replace('near 120d high / breakout zone', '120일 고점권/돌파 구간')
                .replace('price-state support only; needs validation confirmation', '가격 상태 보조 신호만 있어 추가 검증 필요'))
from tools.agents.recommendation_auditor import LOGICS, benchmark_symbol_for, benchmark_return, logic_config, pct, signal
from tools.agents.lib.corporate_actions import symbol_corporate_action_risk
from tools.agents.lib.indicator_taxonomy import classify_indicator_logic



def _bar_day(value: str):
    try:
        return datetime.fromisoformat(str(value).replace('Z','+00:00')).date()
    except Exception:
        return str(value)[:10]


def analysis_rows_for_recommendation(rows, symbol: str):
    """Use the latest fully completed daily bar as the analysis price.

    If the newest daily bar is dated today in the market's local calendar, treat it as
    potentially incomplete/intraday and anchor recommendations to the previous daily
    close. This keeps paper research on a clear T-1 close -> next-session tracking basis.
    """
    if len(rows) < 2:
        return rows, {'analysis_price_policy': 'latest_available_close', 'analysis_price_source': 'regular_session_daily_close', 'includes_pre_after_market': False}
    market_tz = ZoneInfo('Asia/Seoul') if symbol.endswith(('.KS', '.KQ')) else ZoneInfo('America/New_York')
    today = datetime.now(market_tz).date()
    latest_day = _bar_day(rows[-1]['date'])
    if latest_day == today:
        return rows[:-1], {
            'analysis_price_policy': 'previous_completed_daily_close',
            'analysis_price_source': 'regular_session_daily_close',
            'includes_pre_after_market': False,
            'excluded_latest_bar_date': rows[-1]['date'],
            'excluded_latest_bar_close': float(rows[-1]['close']),
            'market_timezone': str(market_tz),
        }
    return rows, {'analysis_price_policy': 'latest_completed_daily_close', 'analysis_price_source': 'regular_session_daily_close', 'includes_pre_after_market': False, 'market_timezone': str(market_tz)}

def rows_for(conn, symbol):
    return conn.execute("SELECT date, open, high, low, close, volume FROM price_bars WHERE symbol=? AND timeframe='1d' ORDER BY date ASC", (symbol,)).fetchall()



def technical_risk_context(rows) -> dict:
    if len(rows) < 21:
        return {}
    highs=[float(r['high'] if 'high' in r.keys() and r['high'] is not None else r['close']) for r in rows]
    lows=[float(r['low'] if 'low' in r.keys() and r['low'] is not None else r['close']) for r in rows]
    closes=[float(r['close']) for r in rows]
    vols=[float(r['volume'] or 0) for r in rows]
    trs=[]; plus_dm=[]; minus_dm=[]
    for i in range(1,len(rows)):
        tr=max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
        up=highs[i]-highs[i-1]; down=lows[i-1]-lows[i]
        plus_dm.append(up if up>down and up>0 else 0)
        minus_dm.append(down if down>up and down>0 else 0)
    atr14=sum(trs[-14:])/14 if len(trs)>=14 else None
    atr_pct=round(atr14/closes[-1]*100,2) if atr14 and closes[-1] else None
    adx14=None
    if len(trs)>=14:
        tr14=sum(trs[-14:]) or 1
        pdi=100*sum(plus_dm[-14:])/tr14
        mdi=100*sum(minus_dm[-14:])/tr14
        dx=100*abs(pdi-mdi)/(pdi+mdi) if (pdi+mdi) else 0
        adx14=round(dx,2)
    obv=0; obv_series=[]
    for i in range(1,len(rows)):
        if closes[i]>closes[i-1]: obv+=vols[i]
        elif closes[i]<closes[i-1]: obv-=vols[i]
        obv_series.append(obv)
    obv_trend=None
    if len(obv_series)>=20:
        obv_trend='rising' if obv_series[-1] > obv_series[-20] else 'falling'
    mf=[]
    for i in range(max(1,len(rows)-14),len(rows)):
        tp=(highs[i]+lows[i]+closes[i])/3
        prev_tp=(highs[i-1]+lows[i-1]+closes[i-1])/3
        mf.append((tp*vols[i], tp>prev_tp))
    pos=sum(v for v,up in mf if up); neg=sum(v for v,up in mf if not up)
    mfi=round(100 - 100/(1+(pos/(neg or 1))),2) if mf else None
    cmf=None
    if len(rows)>=20:
        num=den=0
        for i in range(len(rows)-20,len(rows)):
            hl=highs[i]-lows[i]
            mult=((closes[i]-lows[i])-(highs[i]-closes[i]))/hl if hl else 0
            num += mult*vols[i]; den += vols[i]
        cmf=round(num/(den or 1),3)
    volume_confirm = (obv_trend=='rising') or (cmf is not None and cmf>0.05) or (mfi is not None and 45<=mfi<=80)
    trend_strength = 'strong' if adx14 is not None and adx14>=25 else ('weak' if adx14 is not None and adx14<18 else 'moderate')
    atr_bucket = 'high' if atr_pct is not None and atr_pct>=5 else ('low' if atr_pct is not None and atr_pct<2 else 'normal')
    return_5d_pct=round((closes[-1]/closes[-6]-1)*100,2) if len(closes)>=6 and closes[-6] else None
    return_20d_pct=round((closes[-1]/closes[-21]-1)*100,2) if len(closes)>=21 and closes[-21] else None
    high20=max(highs[-20:]) if len(highs)>=20 else max(highs)
    near_20d_high_pct=round((closes[-1]/high20-1)*100,2) if high20 else None
    avg_vol20=sum(vols[-21:-1])/20 if len(vols)>=21 else (sum(vols[:-1])/max(1,len(vols)-1) if len(vols)>1 else 0)
    volume_surge=round(vols[-1]/avg_vol20,2) if avg_vol20 else None
    overheated = (
        ((return_5d_pct is not None and return_5d_pct >= 8) or (return_20d_pct is not None and return_20d_pct >= 20))
        and (near_20d_high_pct is not None and near_20d_high_pct >= -3)
        and (
            (mfi is not None and mfi >= 78)
            or (volume_surge is not None and volume_surge >= 1.8)
            or atr_bucket == 'high'
        )
    )
    chasing_penalty=0
    if overheated:
        chasing_penalty = 8
        if return_5d_pct is not None and return_5d_pct >= 15: chasing_penalty += 3
        if return_20d_pct is not None and return_20d_pct >= 35: chasing_penalty += 3
        if volume_surge is not None and volume_surge >= 2.5: chasing_penalty += 2
        chasing_penalty=min(chasing_penalty, 14)
    size_hint = 'small' if overheated or atr_bucket=='high' or trend_strength=='weak' else 'normal'
    return {'atr14_pct':atr_pct,'atr_bucket':atr_bucket,'adx14':adx14,'trend_strength':trend_strength,'obv_trend':obv_trend,'cmf20':cmf,'mfi14':mfi,'volume_confirmation':bool(volume_confirm),'position_size_hint_from_indicators':size_hint,'return_5d_pct':return_5d_pct,'return_20d_pct':return_20d_pct,'near_20d_high_pct':near_20d_high_pct,'volume_surge_vs_20d':volume_surge,'overheated_chase_risk':bool(overheated),'chasing_penalty':chasing_penalty}

MATERIAL_MEDIUM_DISCLOSURE_TERMS = ["유상증자", "전환사채", "신주인수권", "소송", "담보", "질권", "반대매매", "대량매도", "처분결정", "자기주식처분", "CB", "BW"]
BENIGN_DISCLOSURE_TERMS = ["최대주주등소유주식변동신고서", "임원ㆍ주요주주특정증권등소유상황보고서", "주식등의대량보유상황보고서", "연결재무제표기준영업(잠정)실적", "현금ㆍ현물배당", "기업설명회", "특수관계인과의내부거래", "특수관계인에대한출자"]


def is_material_medium_disclosure_name(name: str, risk_level: str | None) -> bool:
    compact = (name or '').replace(' ', '')
    if any(term in compact for term in MATERIAL_MEDIUM_DISCLOSURE_TERMS):
        return True
    if any(term.replace(' ', '') in compact for term in BENIGN_DISCLOSURE_TERMS):
        return False
    return risk_level == 'medium' and '기재정정' in compact


def disclosure_penalty(conn, symbol):
    disclosures=conn.execute("SELECT risk_level, report_nm, rcept_dt FROM disclosure_events WHERE symbol=? ORDER BY rcept_dt DESC LIMIT 20",(symbol,)).fetchall()
    high=sum(1 for d in disclosures if d['risk_level']=='high')
    med=sum(1 for d in disclosures if d['risk_level']=='medium' and is_material_medium_disclosure_name(d['report_nm'] or '', d['risk_level']))
    benign_med=sum(1 for d in disclosures if d['risk_level']=='medium' and not is_material_medium_disclosure_name(d['report_nm'] or '', d['risk_level']))
    pos=sum(1 for d in disclosures if d['risk_level']=='positive')
    return {'high':high,'medium':med,'benign_medium':benign_med,'positive':pos}, high*50 + med*10 + min(benign_med, 3)*2 - pos*5



def disclosure_impact_summary(conn, symbol: str) -> dict:
    try:
        rows=conn.execute("SELECT impact_direction,severity,confidence,reason,rcept_no FROM disclosure_impact_assessments WHERE symbol=? ORDER BY assessed_at DESC LIMIT 20",(symbol,)).fetchall()
    except sqlite3.OperationalError:
        return {}
    if not rows:
        return {}
    high=sum(1 for r in rows if r['impact_direction']=='negative' and r['severity']=='high')
    med=sum(1 for r in rows if r['impact_direction']=='negative' and r['severity']=='medium')
    low_neg=sum(1 for r in rows if r['impact_direction']=='negative' and r['severity']=='low')
    pos=sum(1 for r in rows if r['impact_direction']=='positive')
    neutral=sum(1 for r in rows if r['impact_direction']=='neutral')
    return {'high':high,'medium':med,'low_negative':low_neg,'positive':pos,'neutral':neutral,'assessed_count':len(rows),'latest_reason':rows[0]['reason']}

def adjusted_disclosure_medium_count(disclosures: dict) -> int:
    """Impact-aware medium count for recommendation gating.

    Repeated large-cap ownership/correction/administrative filings can create a
    large medium cluster. Once body/impact assessment exists, use high/medium as
    real blockers only when negative impact dominates. Positive/neutral assessed
    items offset medium cluster pressure; raw counts remain visible in UI.
    """
    med=int(disclosures.get('medium') or 0)
    if med <= 0 or int(disclosures.get('high') or 0) > 0:
        return med
    assessed=int(disclosures.get('impact_assessed_count') or 0)
    if assessed <= 0:
        return med
    positive=int(disclosures.get('positive') or 0)
    neutral=int(disclosures.get('impact_neutral') or 0)
    low_neg=int(disclosures.get('impact_low_negative') or 0)
    offset=positive + neutral
    # Body-assessed neutral/positive filings are strong evidence that a raw medium
    # cluster is administrative/repeating rather than a hard risk cluster.
    # low-negative still matters, but should not behave like repeated medium blockers.
    return max(0, med + max(0, low_neg-2)//2 - offset)


def current_market_return(conn, symbol):
    bench=benchmark_symbol_for(symbol)
    rows=conn.execute("SELECT date, close FROM price_bars WHERE symbol=? AND timeframe='1d' ORDER BY date ASC",(bench,)).fetchall()
    if len(rows)<21: return None
    return pct(float(rows[-1]['close']), float(rows[-21]['close']))


def org_profile() -> dict:
    path = Path('configs/org_profile.json')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {'profile': 'balanced_research', 'recommendation': {}}

def recommendation_policy() -> dict:
    profile = org_profile()
    rec = profile.get('recommendation') or {}
    return {
        'profile': profile.get('profile') or 'balanced_research',
        'candidate_threshold': float(rec.get('candidate_threshold', 65)),
        'research_candidate_threshold': float(rec.get('research_candidate_threshold', 72)),
        'research_weighted_consensus_min': float(rec.get('research_weighted_consensus_min', 2.5)),
        'research_excess_win_min': float(rec.get('research_excess_win_min', 50)),
        'allow_aggressive_research_candidate': bool(rec.get('allow_aggressive_research_candidate', False)),
    }


def strategy_weight(strategy: dict) -> float:
    reason=(strategy.get('reason') or '').lower()
    logic=(strategy.get('logic') or '').lower()
    if 'aggressive_research_active' in reason:
        return 0.35
    if 'high_upside_probation' in reason or logic.startswith('aggressive'):
        return 0.45
    if logic.startswith('us_'):
        return 0.55
    if 'probation' in reason:
        return 0.7
    return 1.0


def reliability_label_from_audit(q: dict, samples: int | None = None) -> list[dict]:
    flags=set(q.get('quality_flags') or [])
    axes=q.get('trust_axes') or {}
    role_labels=set(q.get('role_labels') or [])
    qscore=q.get('quality_score')
    try:
        qscore=float(qscore) if qscore is not None else None
    except Exception:
        qscore=None
    sample_count=int(samples or q.get('samples') or 0)
    out=[]
    if (qscore is not None and qscore >= 70 and sample_count >= 30) or 'risk_adjusted_candidate' in role_labels:
        out.append({'code':'reliable_enough','label':'신뢰 가능 구간','kind':'good','description':'현재 표본에서는 fund 선택/추천 보조 근거로 반영 가능합니다.'})
    if 'left_tail_excess_risk' in flags or 'left_tail_risk' in role_labels or float(axes.get('tail_safety') or 100) < 45:
        out.append({'code':'crash_or_left_tail_sensitive','label':'하락장/급락 취약','kind':'warn','description':'좌측 꼬리 손실이 커서 방어형 fund는 낮은 비중 또는 회피가 적절합니다.'})
    if 'negative_expected_excess_value' in flags or 'no_positive_average_excess' in flags or 'weak_excess' in role_labels:
        out.append({'code':'weak_excess_reliability','label':'초과수익 신뢰 낮음','kind':'bad','description':'벤치마크 대비 기대값이 약해 승인보다 연구/관찰 우선입니다.'})
    if 'period_instability' in flags or 'recent_decay' in flags or 'context_sensitive_avoid' in role_labels:
        out.append({'code':'context_dependent','label':'국면 의존','kind':'warn','description':'성과가 특정 기간/국면에 의존하므로 regime/context router와 함께 써야 합니다.'})
    if 'weak_success_confidence_interval' in flags or sample_count < 30 or 'research_only' in role_labels:
        out.append({'code':'thin_or_uncertain_sample','label':'표본/신뢰구간 부족','kind':'warn','description':'성공률 수치보다 샘플 축적과 신뢰구간 개선이 먼저입니다.'})
    if not out:
        out.append({'code':'neutral_watch','label':'중립 관찰','kind':'neutral','description':'치명적 플래그는 제한적이나 강한 신뢰 라벨도 아직 없습니다.'})
    return out


def recommendation_audit_contract(top_signals: list[dict], regime_context: dict | None = None) -> dict:
    labels_by_code={}
    role_labels=set()
    best_uses=[]
    fund_hints=[]
    favorable=[]
    unfavorable=[]
    trust_axes=[]
    strategies=[]
    for signal in top_signals:
        q=signal.get('audit_quality') or {}
        if not q:
            continue
        logic=signal.get('logic')
        samples=signal.get('symbol_samples')
        for label in reliability_label_from_audit(q, samples):
            labels_by_code.setdefault(label['code'], label)
        role_labels.update(q.get('role_labels') or [])
        if q.get('best_use'): best_uses.append(q.get('best_use'))
        if q.get('fund_usage_hint'): fund_hints.append(q.get('fund_usage_hint'))
        favorable.extend(q.get('favorable_contexts') or [])
        unfavorable.extend(q.get('unfavorable_contexts') or [])
        if q.get('trust_axes'): trust_axes.append(q.get('trust_axes') or {})
        strategies.append({
            'logic': logic,
            'strategy_label': logic_label(logic),
            'labels': reliability_label_from_audit(q, samples),
            'best_use': q.get('best_use'),
            'fund_usage_hint': q.get('fund_usage_hint'),
            'trust_axes': q.get('trust_axes') or {},
            'favorable_contexts': (q.get('favorable_contexts') or [])[:3],
            'unfavorable_contexts': (q.get('unfavorable_contexts') or [])[:3],
        })
    avg_axes={}
    for key in ['return_edge','confidence','tail_safety','regime_fit','execution_reliability','overheat_avoidance','consistency']:
        vals=[float(x.get(key)) for x in trust_axes if x.get(key) is not None]
        if vals:
            avg_axes[key]=round(sum(vals)/len(vals),2)
    ordered=list(labels_by_code.values())
    primary=ordered[0] if ordered else {'code':'no_audit_contract','label':'Audit 계약 없음','kind':'neutral','description':'최신 audit contract를 찾지 못했습니다.'}
    regime=(regime_context or {}).get('regime') or 'unknown'
    return {
        'policy':'audit_labels_context_fit_for_fund_strategy_selection',
        'primary_label': primary,
        'labels': ordered,
        'role_labels': sorted(role_labels),
        'best_uses': list(dict.fromkeys(best_uses)),
        'fund_usage_hints': list(dict.fromkeys(fund_hints)),
        'fund_fit_reason': (list(dict.fromkeys(fund_hints)) or [primary.get('description')])[0],
        'regime_fit': {
            'current_regime': regime,
            'score': avg_axes.get('regime_fit'),
            'favorable_contexts': favorable[:5],
            'unfavorable_contexts': unfavorable[:5],
            'interpretation': 'prefer favorable contexts, downweight unfavorable contexts, collect more samples for thin labels',
        },
        'trust_axes': avg_axes,
        'strategies': strategies[:5],
    }



def audit_quality_score_adjustment(q: dict) -> dict:
    """Translate the auditor's role/trust contract into recommendation scoring.

    The auditor now evaluates strategy quality across trust axes rather than only
    average excess return and win rate. Recommendation ranking should consume the
    same contract so weak-but-current signals surface as research/watch, while
    risk-adjusted candidates can rise without requiring obsolete pass/fail gates.
    """
    if not q:
        return {'adjustment': 0.0, 'penalty': 0.0, 'boost': 0.0, 'reasons': ['missing_audit_contract']}
    axes=q.get('trust_axes') or {}
    role_labels=set(q.get('role_labels') or [])
    best_use=q.get('best_use')
    flags=set(q.get('quality_flags') or [])
    penalty=0.0
    boost=0.0
    reasons=[]

    def axis(name, default=50.0):
        try:
            return float(axes.get(name) if axes.get(name) is not None else default)
        except Exception:
            return default

    for name, low, high, low_penalty, high_boost in [
        ('return_edge', 45, 62, 4.0, 2.5),
        ('confidence', 45, 65, 3.0, 2.0),
        ('tail_safety', 50, 68, 5.0, 2.5),
        ('regime_fit', 45, 65, 3.0, 2.0),
        ('execution_reliability', 45, 65, 3.0, 1.5),
        ('consistency', 45, 62, 3.0, 1.5),
    ]:
        value=axis(name)
        if value < low:
            p=round((low-value)/10*low_penalty, 2)
            penalty += p
            reasons.append(f'{name}_weak')
        elif value >= high:
            b=round((value-high)/10*high_boost, 2)
            boost += min(high_boost, b)
            reasons.append(f'{name}_strong')

    if 'risk_adjusted_candidate' in role_labels:
        boost += 4.0
        reasons.append('risk_adjusted_candidate')
    if 'research_only' in role_labels:
        penalty += 2.0
        reasons.append('research_only')
    if 'weak_excess' in role_labels or 'negative_expected_excess_value' in flags:
        penalty += 3.0
        reasons.append('weak_expected_excess')
    if 'left_tail_risk' in role_labels or 'left_tail_excess_risk' in flags:
        penalty += 4.0
        reasons.append('left_tail_risk')
    try:
        expected_ev = float(q.get('expected_excess_value')) if q.get('expected_excess_value') is not None else None
    except Exception:
        expected_ev = None
    try:
        p25 = float(q.get('p25_excess')) if q.get('p25_excess') is not None else None
    except Exception:
        p25 = None
    try:
        positive_periods = int(q.get('positive_periods') or 0)
        tested_periods = int(q.get('tested_periods') or 0)
    except Exception:
        positive_periods = 0
        tested_periods = 0
    if expected_ev is not None and expected_ev < 0:
        penalty += min(5.0, abs(expected_ev) * 0.7)
        reasons.append('negative_audit_ev')
    if p25 is not None and p25 < -3:
        penalty += min(4.0, abs(p25 + 3) * 0.5)
        reasons.append('audit_left_tail_depth')
    if tested_periods and positive_periods < max(2, tested_periods // 2):
        penalty += 2.0
        reasons.append('audit_period_instability_depth')
    if 'context_sensitive_avoid' in role_labels:
        penalty += 3.0
        reasons.append('context_sensitive_avoid')
    if best_use == 'avoid_or_small_research_weight':
        penalty += 5.0
        reasons.append('avoid_or_small_research_weight')
    elif best_use in ('candidate_generation', 'risk_adjusted_candidate_generation', 'fund_selection_support'):
        boost += 2.0
        reasons.append(str(best_use))
    elif best_use == 'defensive_or_risk_control_sleeve':
        boost += 1.0
        reasons.append('defensive_or_risk_control_sleeve')

    penalty=min(18.0, penalty)
    boost=min(8.0, boost)
    return {'adjustment': round(boost-penalty, 2), 'penalty': round(penalty, 2), 'boost': round(boost, 2), 'reasons': reasons[:8], 'trust_axes': axes, 'role_labels': sorted(role_labels), 'best_use': best_use}


def strategy_success_optimizer_plan() -> dict:
    path = Path('/tmp/strategy_success_optimizer_latest.json')
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8')).get('action_plan') or {}
    except Exception:
        return {}

def target_adjustment_acceptance_map() -> dict:
    path = Path('/tmp/recommendation_audit_latest.json')
    if not path.exists():
        return {}
    try:
        summary=(json.loads(path.read_text(encoding='utf-8')).get('summary') or {})
        out={}
        for market, mdata in (summary.get('by_market') or {}).items():
            for logic, ldata in (mdata.get('by_logic') or {}).items():
                tas=ldata.get('target_adjusted_summary') or {}
                if tas:
                    out[(logic, market)] = tas
        return out
    except Exception:
        return {}

def strategy_success_gate(logic: str) -> dict:
    path = Path('/tmp/strategy_success_optimizer_latest.json')
    if not path.exists():
        return {'recommendation_enabled': True, 'trade_eligible_strategy': False, 'tier': 'unknown'}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {'recommendation_enabled': True, 'trade_eligible_strategy': False, 'tier': 'unknown'}
    return ((data.get('logic_gates') or {}).get(logic) or {'recommendation_enabled': True, 'trade_eligible_strategy': False, 'tier': 'unknown'})

def symbol_validation_stats(strategy: dict, symbol: str) -> dict:
    logic = strategy.get('logic')
    summary = strategy.get('summary') or {}
    best = {'edge': 0.0, 'samples': 0, 'success_rate_pct': None, 'bucket': 'none', 'source': 'strategy_summary'}
    for bucket in ('strengths', 'weaknesses'):
        for row in summary.get(bucket, []) or []:
            if row.get('symbol') == symbol:
                edge = float(row.get('avg_excess_return_pct') or 0)
                samples = int(row.get('samples') or 0)
                success = float(row.get('success_rate_pct') or 0)
                if bucket == 'weaknesses' and edge > 0:
                    edge = -edge * 0.75
                best = {'edge': edge, 'samples': samples, 'success_rate_pct': success, 'bucket': bucket, 'source': 'strategy_summary'}
                break
    # Current-recommendation validation writes directly to recommendation_validation_results.
    # Use that live DB evidence immediately instead of waiting for the next lifecycle summary
    # refresh; this closes the org-menu loop where sample<10 stayed stale for a full cycle.
    if logic:
        try:
            with sqlite3.connect(get_settings().database_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS samples,
                           AVG(CASE WHEN result='success' THEN 1.0 ELSE 0.0 END) * 100 AS success_rate_pct,
                           AVG(excess_return_pct) AS avg_excess_return_pct
                    FROM recommendation_validation_results
                    WHERE logic=? AND symbol=? AND action='candidate_buy_zone'
                    """,
                    (logic, symbol),
                ).fetchone()
            live_samples = int(row['samples'] or 0) if row else 0
            if live_samples >= int(best.get('samples') or 0):
                edge = float(row['avg_excess_return_pct'] or 0)
                success = float(row['success_rate_pct'] or 0)
                bucket = 'live_positive' if live_samples >= 3 and edge > 0 else ('live_negative' if live_samples >= 3 and edge < 0 else 'live_thin')
                best = {'edge': edge, 'samples': live_samples, 'success_rate_pct': success, 'bucket': bucket, 'source': 'recommendation_validation_results'}
        except Exception:
            pass
    return best


def confidence_grade(score: float, avg_excess: float, avg_win: float, symbol_samples: int, positive_edges: int, disclosures: dict, financial_quality: dict | None = None) -> dict:
    fq = financial_quality or {}
    if fq.get('warnings') and fq.get('score_adjustment', 0) <= -15:
        return {'level': 'fundamental_risk', 'label': '재무 리스크', 'description': '최근 재무 품질 경고가 커서 보수적 검토 필요'}
    if disclosures.get('high', 0) > 0 or disclosures.get('medium', 0) >= 3:
        return {'level': 'risk_control', 'label': '리스크 제외', 'description': '공시 리스크가 커서 후보에서 제외'}
    if score >= 80 and avg_excess >= 2 and avg_win >= 55:
        return {'level': 'strong', 'label': '검증 강도 높음', 'description': '전략 성과와 현재 신호가 강함'}
    if score >= 65 and avg_excess > 0 and avg_win >= 50 and (symbol_samples >= 8 or positive_edges >= 1):
        return {'level': 'medium', 'label': '검증 강도 중간', 'description': '전략 근거는 있으나 종목별 검증은 더 필요'}
    return {'level': 'weak', 'label': '실험 후보', 'description': '범용 전략 합의 중심이라 종목별 근거는 약함'}



def symbol_return(rows, days: int) -> float | None:
    if len(rows) <= days:
        return None
    now = float(rows[-1]['close'])
    base = float(rows[-days-1]['close'])
    return pct(now, base) if base else None


def build_recommendation_reason(symbol: str, rows, consensus: int, best: dict, avg_active_excess: float, avg_excess_win: float, positive_symbol_edges: list[dict], market20: float | None, disclosures: dict, upside_1: float | None, downside_stop: float | None) -> str:
    name = display_name(symbol)
    r20 = symbol_return(rows, 20)
    r60 = symbol_return(rows, 60)
    lead = f"{name}{particle(name, '은/는')} "
    parts = []
    if r20 is not None and market20 is not None:
        spread = round(r20 - market20, 2)
        if spread > 5:
            parts.append(f"단기 상승률 자체가 아니라 벤치마크 대비 20일 상대강도(+{spread}%p)를 보조 확인했습니다")
        elif spread < -5:
            parts.append(f"20일 상대강도는 벤치마크보다 {abs(spread)}%p 약해 보수적으로 봅니다")
        else:
            parts.append("20일 흐름은 보조 변수로만 사용하고, 핵심은 검증된 전략 합의와 리스크 필터입니다")
    elif r20 is not None:
        parts.append("20일 가격 흐름은 보조 변수로만 사용했습니다")
    if r60 is not None and r60 > 15:
        parts.append(f"60일 추세는 {r60}%로 조건 확인에 보조 반영됐습니다")
    elif r60 is not None and r60 < -10:
        parts.append(f"60일 흐름은 {r60}%로 약해 반등 확인이 필요합니다")

    evidence = []
    evidence.append(f"active 전략 {consensus}개가 동시에 후보 구간으로 분류")
    if avg_active_excess is not None:
        evidence.append(f"상위 신호 평균 초과수익 {avg_active_excess}%는 보조 랭킹 근거로만 반영")
    if positive_symbol_edges:
        best_edge = max(positive_symbol_edges, key=lambda x: x.get('symbol_edge_pct', 0))
        evidence.append(f"이 종목에서 과거 edge가 양수인 전략 {len(positive_symbol_edges)}개(최대 {best_edge.get('symbol_edge_pct')}%)")
    else:
        evidence.append("아직 이 종목 고유 edge보다는 범용 active 전략 합의에 가까움")

    risk = []
    if disclosures.get('high') or disclosures.get('medium'):
        risk.append(f"최근 공시 리스크 H:{disclosures.get('high',0)} M:{disclosures.get('medium',0)}")
    elif disclosures.get('positive'):
        risk.append(f"고위험 공시는 없고 긍정 공시 {disclosures.get('positive',0)}건이 있습니다")
    else:
        risk.append("최근 고위험 공시 부담은 낮습니다")
    if upside_1 is not None and downside_stop is not None:
        reward_risk = abs(upside_1 / downside_stop) if downside_stop else None
        if reward_risk is not None:
            risk.append(f"1차 목표 여력 {upside_1}% / 위험 기준 {downside_stop}%로 보상-위험 비율 약 {round(reward_risk,2)}배")

    return lead + "; ".join(parts[:2]) + ". 핵심 근거: " + ", ".join(evidence) + ". 체크포인트: " + ", ".join(risk) + "."


def explain_decision(action: str, confidence: float, consensus: int, disclosures: dict, financial_quality: dict | None, market20: float | None, total_symbol_samples: int, positive_symbol_edges: list[dict], avg_excess_win: float, upside_1_pct: float | None, downside_stop_pct: float | None) -> dict:
    blockers=[]; cautions=[]; supports=[]
    effective_medium=adjusted_disclosure_medium_count(disclosures)
    fq = financial_quality or {}
    if disclosures.get('high', 0) > 0:
        blockers.append({'code':'disclosure_high','label':'고위험 공시 존재','detail':f"high {disclosures.get('high',0)}건"})
    if effective_medium >= 3:
        blockers.append({'code':'disclosure_medium_cluster','label':'중위험 공시 다수','detail':f"effective medium {effective_medium}건 / raw {disclosures.get('medium',0)}건"})
    elif disclosures.get('medium', 0) >= 3:
        cautions.append({'code':'disclosure_medium_cluster_softened','label':'반복/중립 공시 다수(완화)','detail':f"raw medium {disclosures.get('medium',0)}건 → effective {effective_medium}건"})
    if (fq.get('score_adjustment') or 0) <= -20:
        blockers.append({'code':'financial_hard_risk','label':'재무 품질 강한 경고','detail':', '.join((fq.get('warnings') or [])[:3])})
    if confidence < 60:
        cautions.append({'code':'score_below_buy_gate','label':'매수 후보 점수 미달','detail':f'{confidence} < configured gate'})
    if consensus < 3:
        cautions.append({'code':'low_strategy_consensus','label':'전략 합의 부족','detail':f'active 신호 {consensus}개'})
    if total_symbol_samples == 0:
        cautions.append({'code':'new_symbol_validation_pending','label':'종목별 검증 대기','detail':'신규/미검증 종목'})
    elif total_symbol_samples < 10:
        cautions.append({'code':'thin_symbol_validation','label':'종목별 검증 제한적','detail':f'{total_symbol_samples}건'})
    # avg_excess_win below target is tracked as aggregate strategy-quality context.
    # Do not repeat it as a per-card caution; it is too common to discriminate candidates.
    if market20 is not None and market20 < -5:
        cautions.append({'code':'market_short_term_weak','label':'벤치마크 단기 약세','detail':f'{market20}%'})
    if upside_1_pct is not None and downside_stop_pct is not None and upside_1_pct < abs(downside_stop_pct):
        cautions.append({'code':'reward_risk_unfavorable','label':'보상/위험 불리','detail':f'목표 {upside_1_pct}%, 위험 {downside_stop_pct}%'})
    if (fq.get('score_adjustment') or 0) <= -15:
        cautions.append({'code':'financial_soft_risk','label':'재무 품질 경고','detail':', '.join((fq.get('warnings') or [])[:3])})
    if confidence >= 60:
        supports.append({'code':'score_pass','label':'점수 기준 통과','detail':str(confidence)})
    if disclosures.get('high',0) == 0 and disclosures.get('medium',0) < 2:
        supports.append({'code':'disclosure_gate_pass','label':'공시 리스크 필터 통과','detail':f"H:{disclosures.get('high',0)} M:{disclosures.get('medium',0)}"})
    if positive_symbol_edges:
        supports.append({'code':'positive_symbol_edge','label':'종목별 양수 edge 존재','detail':f'{len(positive_symbol_edges)}개 전략'})
    if fq.get('supports'):
        supports.append({'code':'financial_support','label':'재무 품질 지원','detail':', '.join(fq.get('supports', [])[:3])})
    if action == 'candidate_buy_zone':
        primary = supports[0]['label'] if supports else '매수 후보 조건 통과'
    elif action == 'avoid':
        primary = blockers[0]['label'] if blockers else (cautions[0]['label'] if cautions else '제외 조건')
    else:
        primary = cautions[0]['label'] if cautions else ('추가 확인 필요' if supports else '관망 유지')
    return {'primary': primary, 'blockers': blockers, 'cautions': cautions, 'supports': supports}


def fmt_pct_value(v, suffix='%'):
    if v is None:
        return '-'
    try:
        return f"{round(float(v),2)}{suffix}"
    except Exception:
        return str(v)


def build_entry_plan(close: float, target_1: float, stop_reference: float, technical_context: dict | None, action: str) -> dict:
    """Suggest a paper-research entry price instead of only upside from current price."""
    tech = technical_context or {}
    atr_pct = tech.get('atr14_pct')
    try:
        atr_pct = float(atr_pct) if atr_pct is not None else None
    except Exception:
        atr_pct = None
    pullback_pct = 1.2
    reasons = ['현재가 추격 대신 기준 매입가를 별도 제안']
    if atr_pct is not None:
        pullback_pct = max(pullback_pct, min(5.5, atr_pct * 0.65))
        reasons.append(f"ATR {round(atr_pct,2)}% 반영")
    if tech.get('overheated_chase_risk'):
        pullback_pct += 1.8
        reasons.append('단기 과열/추격 리스크 반영')
    if tech.get('atr_bucket') == 'high':
        pullback_pct += 0.8
        reasons.append('고변동성 구간')
    if tech.get('trend_strength') == 'strong' and tech.get('volume_confirmation'):
        pullback_pct -= 0.5
        reasons.append('추세/거래량 확인으로 과도한 대기폭 완화')
    if action != 'candidate_buy_zone':
        pullback_pct += 0.8
        reasons.append('watch/rejected 후보는 더 보수적 기준가 사용')
    pullback_pct = round(max(0.8, min(7.5, pullback_pct)), 2)
    target_buy_price = round(float(close) * (1 - pullback_pct / 100), 2)
    acceptable_entry_upper_pct = round(max(0.4, pullback_pct * 0.45), 2)
    acceptable_entry_upper = round(float(close) * (1 - acceptable_entry_upper_pct / 100), 2)
    chase_above_price = round(float(close) * (1 + max(0.6, (atr_pct or 2.0) * 0.25) / 100), 2)
    target_upside_from_entry = pct(target_1, target_buy_price)
    stop_downside_from_entry = pct(stop_reference, target_buy_price)
    reward_risk = None
    if target_upside_from_entry is not None and stop_downside_from_entry is not None and stop_downside_from_entry < 0:
        reward_risk = round(float(target_upside_from_entry) / abs(float(stop_downside_from_entry)), 2)
    if action == 'candidate_buy_zone' and not tech.get('overheated_chase_risk'):
        mode = 'staged_entry_zone'
        label = '분할 관심 매입가'
    elif action == 'candidate_buy_zone':
        mode = 'pullback_required'
        label = '과열 완화 후 관심 매입가'
    else:
        mode = 'watch_entry_only'
        label = '관찰용 목표매입가'
    return {
        'policy': 'paper_research_target_buy_price_not_order_instruction',
        'mode': mode,
        'label': label,
        'analysis_price': close,
        'target_buy_price': target_buy_price,
        'acceptable_entry_upper': acceptable_entry_upper,
        'chase_above_price': chase_above_price,
        'pullback_from_analysis_price_pct': -pullback_pct,
        'acceptable_entry_pullback_pct': -acceptable_entry_upper_pct,
        'target_1_upside_from_target_buy_pct': target_upside_from_entry,
        'stop_downside_from_target_buy_pct': stop_downside_from_entry,
        'reward_risk_from_target_buy': reward_risk,
        'stop_reference': stop_reference,
        'reasons': reasons[:6],
        'note': 'paper research 기준가입니다. 실시간 주문/체결 지시가 아니며 다음 완료 일봉에서 재평가합니다.',
    }


def build_recommendation_presentation(action: str, confidence: float, grade: dict, watch_reason: dict, validation_basis: dict, risk_notes: list[str]) -> dict:
    bucket = 'approved' if action == 'candidate_buy_zone' else ('rejected' if action == 'avoid' else 'watch')
    target_count = int(validation_basis.get('target_adjustment_count') or 0)
    target_applied = int(validation_basis.get('target_adjustment_applied_count') or 0)
    target_provisional = int(validation_basis.get('target_adjustment_provisional_count') or 0)
    target_rejected = int(validation_basis.get('target_adjustment_rejected_count') or 0)
    audit_flags = validation_basis.get('audit_quality_flags') or []
    sample_count = int(validation_basis.get('symbol_validation_sample_count') or 0)
    high_conf = int(validation_basis.get('high_confidence_historical_strategy_count') or 0)
    trade_strats = int(validation_basis.get('trade_eligible_strategy_count') or 0)
    avg_excess = validation_basis.get('avg_active_excess_return_pct')
    excess_win = validation_basis.get('avg_excess_win_rate_pct')
    decision_label = '매수 후보' if bucket == 'approved' else ('제외/보류' if bucket == 'rejected' else '관찰')
    status_line = f"{decision_label} · 점수 {round(float(confidence or 0),1)} · {(grade or {}).get('label') or (grade or {}).get('grade') or '검증 대기'}"
    blockers=[]
    if trade_strats == 0:
        blockers.append('실거래 가능 전략 0개')
    if high_conf == 0:
        blockers.append('고신뢰 과거검증 전략 없음')
    if validation_basis.get('audit_hard_downgrade'):
        blockers.append('audit hard downgrade')
    if validation_basis.get('thin_no_edge_gate'):
        blockers.append('종목별 edge 표본 부족')
    if target_count and not target_applied:
        blockers.append(f"목표가 보정 미채택 {target_rejected}건" + (f"/표본대기 {target_provisional}건" if target_provisional else ''))
    positives=[]
    if avg_excess is not None:
        positives.append(f"시장 대비 전략 성과 {fmt_pct_value(avg_excess)}")
    if audit_flags:
        positives.append('전략 라벨: ' + ', '.join(audit_flag_label(x) for x in audit_flags[:2]))
    if validation_basis.get('preferred_historical_edge_count'):
        positives.append(f"선호 시장/전략 edge {validation_basis.get('preferred_historical_edge_count')}개")
    if validation_basis.get('financial_supports'):
        positives.append('재무: ' + ', '.join((validation_basis.get('financial_supports') or [])[:2]))
    fund_consensus = validation_basis.get('fund_consensus') or {}
    fund_votes = int(fund_consensus.get('votes') or fund_consensus.get('vote_count') or 0)
    fund_score = fund_consensus.get('weighted_score')
    fund_boost = validation_basis.get('fund_consensus_score_boost') or 0
    if fund_votes:
        positives.append(f"Fund 지지 {fund_votes}표 · 가중점수 {fmt_pct_value(fund_score, '')} · 보조점수 +{fmt_pct_value(fund_boost, '')}")
    supply_explanation=validation_basis.get('supply_close_explanation') if validation_basis.get('supply_close_context') else None
    supply_adj = validation_basis.get('supply_close_score_adjustment_pct') or 0
    investor_adj = validation_basis.get('investor_flow_seed_adjustment_pct') or 0
    if supply_explanation:
        positives.append(supply_explanation + (f" · 수급/거래주체 보조점수 +{fmt_pct_value(supply_adj, '')}" if supply_adj else ''))
    if investor_adj:
        inv = (validation_basis.get('investor_flow_seed_context') or {}).get('investors') or []
        positives.append(f"거래주체 seed 보조지지 {', '.join(inv) if inv else '감지'} · +{fmt_pct_value(investor_adj, '')}")
    checks=[]
    if sample_count == 0:
        checks.append('종목별 조건 라벨 보강 필요')
    if audit_flags:
        checks.append('Audit flags: ' + ', '.join(audit_flags[:4]))
    checks.extend([x.replace('관망 이유: ','') for x in (risk_notes or [])[:3]])
    if validation_basis.get('investor_flow_status') == 'not_available_in_local_db':
        checks.append('거래주체 데이터 미연동: 외국인/기관/개인 순매수는 아직 실제 근거로 쓰지 않음')
    entry_plan = validation_basis.get('entry_plan') or {}
    if entry_plan:
        positives.append(f"목표매입가 {entry_plan.get('target_buy_price')} · 허용상단 {entry_plan.get('acceptable_entry_upper')} · 추격금지 {entry_plan.get('chase_above_price')} 초과")
        if entry_plan.get('mode') != 'staged_entry_zone':
            checks.append((entry_plan.get('label') or '목표매입가') + ': 현재가 추격보다 pullback 확인 우선')
    target_summary = None
    if target_count:
        target_summary = {
            'proposed': target_count,
            'applied': target_applied,
            'provisional': target_provisional,
            'rejected': target_rejected,
            'plain': f"목표가 보정 제안 {target_count}건 중 적용 {target_applied}건, 표본대기 {target_provisional}건, 거절 {target_rejected}건",
        }
    return {
        'status_line': status_line,
        'decision_label': decision_label,
        'primary_blockers': blockers[:5],
        'positive_factors': positives[:5],
        'next_checks': checks[:6],
        'target_adjustment_summary': target_summary,
        'audit_summary': {
            'min_quality_score': validation_basis.get('audit_quality_min_score'),
            'flags': audit_flags[:8],
            'penalty_total': validation_basis.get('audit_quality_penalty_total'),
        },
    }


def build_human_decision_summary(symbol: str, action: str, confidence: float, grade: dict, watch_reason: dict, validation_basis: dict, risk_notes: list[str], investment_committee: dict | None = None) -> dict:
    name = display_name(symbol)
    syn = ((investment_committee or {}).get('synthesis') or {})
    decision = syn.get('decision')
    cautions = watch_reason.get('cautions') or []
    supports = watch_reason.get('supports') or []
    blockers = watch_reason.get('blockers') or []
    samples = validation_basis.get('symbol_validation_sample_count') or 0
    excess = validation_basis.get('avg_active_excess_return_pct')
    win = validation_basis.get('avg_excess_win_rate_pct')
    r20 = validation_basis.get('symbol_20d_return_pct')
    m20 = validation_basis.get('benchmark_20d_return_pct')
    r60 = validation_basis.get('symbol_60d_return_pct')
    pos_edges = validation_basis.get('positive_symbol_edge_count') or 0
    fin = validation_basis.get('financial_supports') or []
    tech = validation_basis.get('technical_risk_context') or {}
    supply_text = validation_basis.get('supply_close_explanation') if validation_basis.get('supply_close_context') else None
    if action == 'candidate_buy_zone' and decision != 'reject':
        headline = "근거 확인 후 paper 추적 후보"
        next_action = "목표가, 손절 기준, 검증 강도를 확인하세요."
    elif action == 'avoid' or blockers or decision == 'reject':
        headline = "아직 매수 후보로 보기엔 근거 부족"
        next_action = "리스크 해소 또는 검증 표본 보강 후 다시 확인하세요."
    else:
        headline = "관찰 유지, 추가 검증 대기"
        next_action = "가격 갱신과 추가 검증 후 우선순위를 다시 확인하세요."

    why = []
    # Prefer symbol-specific evidence over generic score/strategy boilerplate.
    if r20 is not None and m20 is not None:
        spread = round(float(r20) - float(m20), 2)
        if spread >= 5:
            why.append(f"20일 상대강도가 벤치마크보다 +{spread}%p 강합니다")
        elif spread <= -5:
            why.append(f"20일 상대강도는 벤치마크보다 {abs(spread)}%p 약합니다")
        else:
            why.append(f"20일 흐름은 벤치마크와 비슷합니다({r20}% vs {m20}%)")
    elif r20 is not None:
        why.append(f"20일 가격 흐름은 {r20}%입니다")
    if r60 is not None and abs(float(r60)) >= 8:
        why.append(f"60일 추세는 {r60}%입니다")
    if fin:
        why.append("재무 확인: " + ", ".join(fin[:2]))
    if supply_text:
        why.append(supply_text.rstrip('.'))
    if pos_edges:
        why.append(f"이 종목에서 양수 edge가 확인된 전략이 {pos_edges}개 있습니다")
    elif samples == 0:
        why.append("단, 종목별 과거 edge는 아직 확인되지 않았습니다")
    if tech:
        tech_bits=[]
        if tech.get('trend_strength') == 'strong': tech_bits.append('추세 강함')
        if tech.get('volume_confirmation'): tech_bits.append('거래량 확인')
        if tech.get('atr_bucket') == 'high': tech_bits.append('변동성 높아 소액 관찰')
        if tech_bits: why.append('기술 컨텍스트: ' + ', '.join(tech_bits[:3]))
    # Score-pass is a threshold explanation, not a standalone recommendation reason.
    non_score_supports=[x for x in supports if x.get('code') != 'score_pass']
    if not why and non_score_supports:
        why.append(f"보조 근거: {non_score_supports[0]['label']}")
    if excess is not None and len(why) < 3:
        why.append(f"전략 묶음 평균 초과수익 {excess}%는 보조 랭킹 근거로만 반영됩니다")
    if validation_basis.get('audit_quality_flags') and len(why) < 4:
        why.append('전략 신뢰 라벨: ' + ', '.join(audit_flag_label(x) for x in (validation_basis.get('audit_quality_flags') or [])[:2]))

    main_risk = None
    if blockers:
        main_risk = blockers[0]['label']
    elif cautions:
        main_risk = cautions[0]['label']
    elif risk_notes:
        main_risk = risk_notes[0].replace('관망 이유: ', '')
    else:
        main_risk = "뚜렷한 차단 사유는 제한적이지만, paper research 후보로만 봐야 합니다."

    committee_text = ""
    if syn.get('summary'):
        if decision == 'committee_support':
            committee_text = "위원회는 이 후보를 지지했습니다."
        elif decision == 'watch':
            committee_text = "위원회는 추가 확인이 필요하다고 봤습니다."
        elif decision == 'reject':
            committee_text = "위원회는 아직 지지하지 않았습니다."

    score_gate_text = '점수는 60 이상이면 후보권, 65 이상이면 기본 매수 후보권으로 봅니다.'
    grade_desc = (grade or {}).get('description') or '검증 강도는 추가 확인이 필요합니다.'
    confidence_text = f"{score_gate_text} 현재 점수 {confidence}: {grade_desc}"
    return {
        'headline': headline,
        'why_now': '. '.join(why[:5]) + ('.' if why else ''),
        'main_risk': main_risk,
        'committee_view': committee_text,
        'suggested_action': next_action,
        'confidence_explanation': confidence_text,
    }




def latest_market_context() -> dict:
    path = Path('/tmp/market_context_latest.json')
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}

def latest_supply_close_context() -> dict:
    path = Path('/tmp/supply_close_strength_scout_latest.json')
    if not path.exists(): return {}
    try: data=json.loads(path.read_text(encoding='utf-8'))
    except Exception: return {}
    by={}
    for row in data.get('items') or []:
        sym=str(row.get('symbol') or '').upper().strip()
        if sym: by[sym]=row
    return {'run_at':data.get('run_at'),'summary':data.get('summary') or {},'by_symbol':by,'warnings':data.get('warnings') or []}

def supply_close_context_for_symbol(symbol: str, context: dict | None = None) -> dict:
    ctx=context or latest_supply_close_context()
    row=(ctx.get('by_symbol') or {}).get(str(symbol).upper()) or {}
    if not row: return {}
    f=row.get('features') or {}
    return {'symbol':row.get('symbol'),'bucket':row.get('bucket'),'score':row.get('score'),'latest_date':row.get('latest_date'),'reasons':row.get('reasons') or [],'cautions':row.get('cautions') or [],'volume_vs_20d':f.get('volume_vs_20d'),'volume_vs_60d':f.get('volume_vs_60d'),'close_position_in_day_range':f.get('close_position_in_day_range'),'body_pct':f.get('body_pct'),'return_20d_pct':f.get('return_20d_pct'),'investor_flow_status':row.get('investor_flow_status'),'policy':row.get('policy') or 'paper_research_watch_boost_only','explanation_policy':'price_volume_close_strength_proxy_slightly_upweighted_validation_gated_yet'}

def supply_close_score_adjustment(ctx: dict) -> dict:
    """Small paper-research score overlay from price/volume/close-strength proxy."""
    if not ctx:
        return {'adjustment': 0.0, 'tier': 'not_available', 'reason': 'investor_flow_data_not_ingested'}
    bucket=str(ctx.get('bucket') or '').lower()
    raw_score=float(ctx.get('score') or 0)
    cautions=ctx.get('cautions') or []
    adj=0.0; tier='proxy_watch'
    if bucket == 'strong': adj += 4.8; tier='proxy_strong'
    elif bucket == 'watch': adj += 2.4
    elif raw_score >= 70: adj += 3.6; tier='proxy_strong'
    elif raw_score >= 55: adj += 1.8
    for key, hi, lo, hi_adj, lo_adj in [('volume_vs_20d',1.8,0.8,1.0,-0.8), ('close_position_in_day_range',0.75,0.45,1.0,-1.0)]:
        try:
            v=ctx.get(key)
            if v is not None and float(v) >= hi: adj += hi_adj
            elif v is not None and float(v) < lo: adj += lo_adj
        except Exception: pass
    try:
        if ctx.get('body_pct') is not None and float(ctx.get('body_pct')) >= 45: adj += 0.5
    except Exception: pass
    if cautions: adj -= min(2.0, 0.8 * len(cautions))
    adj=max(-2.0, min(6.0, adj))
    return {'adjustment': round(adj,2), 'tier': tier, 'reason': 'price_volume_close_strength_proxy_slightly_upweighted_validation_gated', 'raw_score': raw_score, 'bucket': bucket or None, 'caution_count': len(cautions)}


def supply_close_plain_text(ctx: dict) -> str:
    if not ctx:
        return '거래주체 DB seed가 없으면 가격·거래량·종가 위치 proxy만 보조근거로 사용합니다.'
    bits=[]
    if ctx.get('volume_vs_20d') is not None: bits.append(f"20일 평균 대비 거래량 {ctx.get('volume_vs_20d')}x")
    if ctx.get('volume_vs_60d') is not None: bits.append(f"60일 평균 대비 {ctx.get('volume_vs_60d')}x")
    if ctx.get('close_position_in_day_range') is not None: bits.append(f"일중 range 내 종가 위치 {ctx.get('close_position_in_day_range')}")
    if ctx.get('body_pct') is not None: bits.append(f"몸통 {ctx.get('body_pct')}%")
    reason=', '.join(bits[:4]) if bits else ', '.join((ctx.get('reasons') or [])[:3])
    caveat='외국인/기관 seed는 DB에 저장된 provisional evidence이며, 정식 순매수 시계열 검증 전까지는 수급 proxy와 함께 약한 보조근거로만 씁니다.'
    if ctx.get('cautions'): caveat += ' 주의: ' + ', '.join((ctx.get('cautions') or [])[:2])
    return f"수급/종가강도: {reason}. {caveat}"


def investor_flow_plain_text(ctx: dict) -> str:
    if not ctx:
        return '거래주체 DB seed 없음 · 가격·거래량·종가 위치 proxy만 보조근거로 사용'
    investors=', '.join(ctx.get('investors') or []) or '감지'
    rank=ctx.get('best_rank')
    date=ctx.get('latest_date') or str(ctx.get('captured_at') or '')[:10]
    if ctx.get('db_linked'):
        return f"거래주체 DB seed 연동: {investors} 관심/순매수 상위 감지" + (f" · best rank {rank}" if rank else '') + (f" · {date}" if date else '') + ' · 정식 순매수 시계열 검증 전까지 약한 보조근거'
    return f"거래주체 seed: {investors} 감지" + (f" · best rank {rank}" if rank else '') + ' · DB 미저장 seed 보조근거'

def latest_mover_context() -> dict:
    out={'seed_by_symbol':{},'shock_by_symbol':{}}
    for path,key in (('/tmp/market_mover_seed_latest.json','seed_by_symbol'),('/tmp/market_shock_mover_scout_latest.json','shock_by_symbol')):
        p=Path(path)
        if not p.exists():
            continue
        try:
            data=json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        rows=[]
        if key == 'seed_by_symbol':
            rows=data.get('top_stock_items') or data.get('items') or []
        else:
            rows=(data.get('top_surges') or []) + (data.get('top_crashes') or [])
        for row in rows:
            sym=str(row.get('symbol') or '').upper().strip()
            if sym:
                out[key][sym]=row
    return out

def mover_context_for_symbol(symbol: str, context: dict | None = None) -> dict:
    ctx=context or latest_mover_context()
    seed=(ctx.get('seed_by_symbol') or {}).get(symbol) or {}
    shock=(ctx.get('shock_by_symbol') or {}).get(symbol) or {}
    if not seed and not shock:
        return {}
    ch=shock.get('return_1d_pct', seed.get('change_pct'))
    try: abs_ch=abs(float(ch or 0))
    except Exception: abs_ch=0
    timing=shock.get('data_timing') or seed.get('data_timing') or 'provisional_intraday_seed'
    is_provisional='provisional' in str(timing) or 'intraday' in str(timing)
    boost=0.0
    if abs_ch >= 20: boost=3.0
    elif abs_ch >= 10: boost=2.0
    elif abs_ch >= 5: boost=1.0
    if is_provisional:
        boost=min(boost,2.0)
    return {'symbol':symbol,'name':seed.get('name') or shock.get('name'),'source':seed.get('source') or shock.get('source'),'direction':seed.get('direction') or ('gainer' if float(ch or 0)>0 else 'loser'),'change_pct':ch,'shock_score':shock.get('shock_score'),'data_timing':timing,'provisional':is_provisional,'recommendation_link_policy':'validation_priority_and_research_candidate_only','context_score_boost':boost}

def mover_seed_symbols(limit:int=120) -> list[str]:
    ctx=latest_mover_context()
    rows=list((ctx.get('seed_by_symbol') or {}).values())
    rows=[r for r in rows if r.get('probable_stock') is not False]
    rows=sorted(rows,key=lambda r: abs(float(r.get('change_pct') or 0)), reverse=True)
    return [str(r.get('symbol')).upper() for r in rows if r.get('symbol')][:limit]

def latest_investor_flow_seed_context() -> dict:
    path=Path('/tmp/investor_flow_seed_latest.json')
    if not path.exists(): return {}
    try: data=json.loads(path.read_text(encoding='utf-8'))
    except Exception: return {}
    by={}
    for row in data.get('top_symbols') or []:
        sym=str(row.get('symbol') or '').upper().strip()
        if sym: by[sym]=row
    return {'run_at': data.get('run_at'), 'summary': data.get('summary') or {}, 'by_symbol': by, 'warnings': data.get('warnings') or [], 'contract': data.get('contract') or {}}

def investor_flow_seed_context_for_symbol(symbol: str, context: dict | None = None, conn=None) -> dict:
    sym=str(symbol).upper().strip()
    db_rows=[]
    if conn is not None:
        try:
            db_rows=latest_investor_flow_for_symbol(sym, conn=conn, lookback_days=5)
        except Exception:
            db_rows=[]
    if db_rows:
        investors=[]; sources=[]; best_rank=None; latest_date=None; captured_at=None
        for r in db_rows:
            inv=r['investor_type']
            if inv not in investors: investors.append(inv)
            rank=r['rank']
            if rank is not None and (best_rank is None or rank < best_rank): best_rank=rank
            if latest_date is None or r['date'] > latest_date: latest_date=r['date']
            if captured_at is None or r['captured_at'] > captured_at: captured_at=r['captured_at']
            sources.append({'investor':inv,'rank':rank,'raw_numeric_values':[], 'raw_text':r['raw_text'], 'source':r['source'], 'date':r['date'], 'authority':r['authority']})
        return {'symbol':sym,'market':'KR','investors':investors,'best_rank':best_rank,'sources':sources,'captured_at':captured_at,'latest_date':latest_date,'authority':'db_persisted_provisional_seed','db_linked':True,'data_quality':'provisional_delayed_scraped'}
    ctx=context or latest_investor_flow_seed_context()
    row=(ctx.get('by_symbol') or {}).get(sym) or {}
    if row:
        row=dict(row); row.setdefault('db_linked', False)
    return row

def investor_flow_score_adjustment(ctx: dict) -> dict:
    # Provisional/delayed Naver top-list flow: small monitoring boost only.
    if not ctx:
        return {'adjustment': 0.0, 'tier': 'not_available', 'reason': 'not_in_investor_flow_seed'}
    investors=ctx.get('investors') or []
    try: rank=float(ctx.get('best_rank') or 99)
    except Exception: rank=99
    adj=0.4
    if 'foreign' in investors: adj += 0.35
    if 'institution' in investors: adj += 0.45
    if rank <= 3: adj += 0.35
    elif rank <= 5: adj += 0.2
    adj=max(0.0, min(1.4, adj))
    return {'adjustment': round(adj,2), 'tier': 'investor_flow_seed_proxy', 'reason': 'naver_foreign_institution_seed_slight_monitoring_boost_validation_gated', 'investors': investors, 'best_rank': ctx.get('best_rank')}

def investor_flow_seed_symbols(limit:int=120) -> list[str]:
    ctx=latest_investor_flow_seed_context()
    rows=list((ctx.get('by_symbol') or {}).values())
    return [str(r.get('symbol')).upper() for r in rows if r.get('symbol')][:limit]

def market_context_for_symbol(symbol: str, context: dict) -> dict:
    summary = context.get('summary') or {}
    impact_map = context.get('impact_map') or {}
    market = market_of(symbol)
    matches=[]
    for theme,row in ((impact_map.get(market) or {}).items()):
        symbols = row.get('affected_symbols') if market == 'KR' else row.get('source_symbols')
        if symbol in (symbols or []):
            matches.append({
                'theme': theme,
                'label': row.get('label') or theme,
                'impact_score': row.get('impact_score') or summary.get('cross_market_impact_score'),
                'source_tags': row.get('source_tags') or [],
                'expected_impact': row.get('expected_impact'),
                'gap_chase_risk': row.get('gap_chase_risk') or summary.get('gap_chase_risk'),
                'risk_note': row.get('risk_note'),
                'summary': row.get('summary'),
            })
    fx = summary.get('fx_context') or {}
    if market == 'KR' and fx.get('available'):
        fx_score = float(fx.get('impact_score') or 50)
        matches.append({
            'theme': 'usdkrw',
            'label': 'USD/KRW 환율 컨텍스트',
            'impact_score': fx_score,
            'source_tags': fx.get('tags') or [],
            'expected_impact': fx.get('kr_equity_impact'),
            'gap_chase_risk': summary.get('gap_chase_risk'),
            'risk_note': fx.get('risk_note'),
            'summary': f"USD/KRW {fx.get('usdkrw')} · 1D {fx.get('return_1d_pct')}% · 5D {fx.get('return_5d_pct')}%",
            'fx_context': fx,
        })
    if not matches:
        return {}
    best=dict(max(matches,key=lambda x: float(x.get('impact_score') or 0)))
    boost=0.0
    if best.get('theme') == 'usdkrw':
        boost = 1.0 if float(best.get('impact_score') or 0) >= 70 else (-1.0 if float(best.get('impact_score') or 0) <= 35 else 0.0)
    if best.get('expected_impact') == 'positive' and float(best.get('impact_score') or 0) >= 62:
        boost = 4.0 if float(best.get('impact_score') or 0) >= 75 else 2.0
    if best.get('gap_chase_risk') == 'high_chase_risk':
        boost = min(boost, 2.0)
    best['matches']=[dict(x) for x in matches]
    best['context_score_boost']=boost
    return best

def latest_audit_quality_by_logic() -> dict:
    path=ROOT / '/tmp/recommendation_audit_latest.json'
    # ROOT / absolute path would ignore ROOT on pathlib in newer Python, but keep explicit Path for clarity.
    path=Path('/tmp/recommendation_audit_latest.json')
    if not path.exists():
        return {}
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    summary=data.get('summary') or {}
    by_logic=summary.get('by_logic') or {}
    improvement_plan=summary.get('strategy_trust_improvement_plan') or summary.get('audit_score_improvement_plan') or {}
    out={}
    for logic, row in by_logic.items():
        out[logic]={'verdict':row.get('verdict'),'quality_score':row.get('quality_score'),'quality_grade':row.get('quality_grade'),'quality_flags':row.get('quality_flags') or [],'wilson_low':row.get('success_rate_wilson_low_pct'),'p10_excess':row.get('p10_excess_return_pct'),'p25_excess':row.get('p25_excess_return_pct'),'expected_excess_value':row.get('expected_excess_value_pct'),'positive_periods':row.get('positive_periods'),'tested_periods':row.get('tested_periods'),'recent_delta':row.get('recent_vs_long_term_excess_delta_pct'),'conditional_context_profile':row.get('conditional_context_profile') or {},'strategy_role_profile':row.get('strategy_role_profile') or {},'role_labels':((row.get('strategy_role_profile') or {}).get('role_labels') or []),'best_use':((row.get('strategy_role_profile') or {}).get('best_use')),'trust_axes':((row.get('strategy_role_profile') or {}).get('trust_axes') or {}),'fund_usage_hint':((row.get('strategy_role_profile') or {}).get('fund_usage_hint')),'favorable_contexts':((row.get('conditional_context_profile') or {}).get('favorable_contexts') or [])[:5],'unfavorable_contexts':((row.get('conditional_context_profile') or {}).get('unfavorable_contexts') or [])[:5],'strategy_trust_improvement_plan':improvement_plan}
    return out





def latest_short_horizon_profile() -> dict:
    path = Path('/tmp/short_horizon_profit_profile_latest.json')
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    by_logic = data.get('by_logic') or {}
    out={str(k): v for k, v in by_logic.items()}
    out['_by_horizon']=data.get('by_horizon') or {}
    return out

def short_horizon_profile_for_logic(logic: str, profiles: dict | None = None) -> dict:
    row = (profiles or latest_short_horizon_profile()).get(logic) or {}
    if not row:
        return {}
    return {
        'policy': 'paper_research_short_horizon_context_only',
        'horizon_days': row.get('horizon_days', 2),
        'samples': row.get('samples'),
        'hit_1_pct': row.get('hit_1_pct'),
        'hit_1_5_pct': row.get('hit_1_5_pct'),
        'hit_2_pct': row.get('hit_2_pct'),
        'avg_max_up_pct': row.get('avg_max_up_pct'),
        'avg_final_return_pct': row.get('avg_final_return_pct'),
        'avg_target_ret_pct': row.get('avg_target_ret_pct'),
        'target_hit_pct': row.get('target_hit_pct'),
        'target_minus_1_pct_point_hit_pct': row.get('target_minus_1_pct_point_hit_pct') or row.get('target_under_1_pct_hit_pct'),
        'target_minus_1_5_pct_points_hit_pct': row.get('target_minus_1_5_pct_points_hit_pct') or row.get('target_under_1_5_pct_hit_pct'),
        'target_minus_2_pct_points_hit_pct': row.get('target_minus_2_pct_points_hit_pct') or row.get('target_under_2_pct_hit_pct'),
        'target_under_1_pct_hit_pct': row.get('target_minus_1_pct_point_hit_pct') or row.get('target_under_1_pct_hit_pct'),
        'target_under_1_5_pct_hit_pct': row.get('target_minus_1_5_pct_points_hit_pct') or row.get('target_under_1_5_pct_hit_pct'),
        'target_under_2_pct_hit_pct': row.get('target_minus_2_pct_points_hit_pct') or row.get('target_under_2_pct_hit_pct'),
        'target_or_under_2pct_pct': row.get('target_minus_2_pct_points_hit_pct') or row.get('target_or_under_2pct_pct'),
        'adjusted_target_profile': row.get('adjusted_target_profile'),
        'scalp_profile': row.get('scalp_profile'),
        'summary': row.get('summary'),
        'by_horizon': {hk: (((hv or {}).get('by_logic') or {}).get(logic) or {}) for hk, hv in ((profiles or {}).get('_by_horizon') or {}).items()} if isinstance(profiles, dict) else {},
    }

def latest_recommendation_strategy(conn: sqlite3.Connection, symbol: str) -> dict:
    """Return the previous recommendation strategy for symbol, if history exists.

    Used as a small hysteresis/tie-breaker so the displayed/audited primary
    strategy does not churn between near-equivalent signals from run to run.
    This does not override action/score; it only keeps the prior strategy label
    when its current signal remains competitive.
    """
    try:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recommendation_history'").fetchone()
        if not exists:
            return {}
        row = conn.execute(
            "SELECT run_at, action, strategy_id, score, payload_json FROM recommendation_history WHERE symbol=? ORDER BY run_at DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if not row:
            return {}
        payload = {}
        try:
            payload = json.loads(row['payload_json'] or '{}')
        except Exception:
            payload = {}
        return {
            'run_at': row['run_at'],
            'action': row['action'],
            'strategy_id': row['strategy_id'] or payload.get('strategy_id') or payload.get('best_logic'),
            'score': row['score'],
            'recommendation_bucket': payload.get('recommendation_bucket') or payload.get('bucket'),
        }
    except Exception:
        return {}


def apply_best_signal_hysteresis(signals: list[dict], previous: dict, margin_points: float = 3.0) -> tuple[list[dict], dict | None]:
    """Keep the previous primary strategy when it is still close to the new best.

    The signal list is already score-sorted. If the previous strategy is within
    margin_points of the current best, promote it to index 0 to stabilize
    best_logic/strategy_id and recommendation history diffs.
    """
    if not signals or not previous.get('strategy_id'):
        return signals, None
    current_best = signals[0]
    prev_logic = previous.get('strategy_id')
    previous_signal = next((s for s in signals if s.get('logic') == prev_logic), None)
    if not previous_signal or previous_signal is current_best:
        return signals, None
    delta = round(float(current_best.get('score') or 0) - float(previous_signal.get('score') or 0), 2)
    if delta <= margin_points:
        stabilized = [previous_signal] + [s for s in signals if s is not previous_signal]
        note = {
            'previous_strategy_id': prev_logic,
            'raw_best_strategy_id': current_best.get('logic'),
            'score_delta_vs_raw_best': delta,
            'margin_points': margin_points,
            'previous_run_at': previous.get('run_at'),
        }
        return stabilized, note
    return signals, None



def smooth_recommendation_score(previous: dict, action: str, raw_score: float, max_step_points: float = 4.0) -> tuple[float, dict | None]:
    """Limit routine run-to-run score movement when action is unchanged.

    This reduces noisy history/UI diffs without changing candidate/watch/avoid
    decisions, because action gates are evaluated on the raw confidence first.
    Large real changes still move over multiple scheduled runs.
    """
    if not previous or previous.get('score') is None or previous.get('action') != action:
        return raw_score, None
    old_score = float(previous.get('score'))
    delta = round(float(raw_score) - old_score, 2)
    if abs(delta) <= max_step_points:
        return raw_score, None
    direction = 1 if delta > 0 else -1
    smoothed = round(old_score + direction * max_step_points, 2)
    return smoothed, {
        'previous_run_at': previous.get('run_at'),
        'previous_score': round(old_score, 2),
        'raw_score': round(float(raw_score), 2),
        'smoothed_score': smoothed,
        'raw_delta': delta,
        'max_step_points': max_step_points,
        'action': action,
    }

def recommend(conn, symbol: str, active_strategies: list[dict], short_horizon_profiles: dict | None = None) -> dict | None:
    rows=rows_for(conn,symbol)
    if len(rows)<120 or not active_strategies: return None
    analysis_rows, analysis_meta = analysis_rows_for_recommendation(rows, symbol)
    if len(analysis_rows)<120: return None
    close=float(analysis_rows[-1]['close'])
    latest_price_date=analysis_rows[-1]['date']
    technical_context=technical_risk_context(analysis_rows)

    def derived_supply_proxy_adjustment(ctx: dict) -> dict:
        # Weak all-candidate proxy from OBV/CMF/MFI/volume confirmation when scout has no symbol hit.
        if not ctx:
            return {'adjustment': 0.0, 'tier': 'not_available', 'reason': 'no_price_volume_proxy'}
        adj=0.0; flags=[]
        if ctx.get('volume_confirmation') is True:
            adj += 1.2; flags.append('volume_confirmation')
        if ctx.get('obv_trend') == 'rising':
            adj += 0.75; flags.append('obv_rising')
        if ctx.get('cmf20') is not None:
            try:
                cmf=float(ctx.get('cmf20'))
                if cmf >= 0.08: adj += 0.85; flags.append('positive_cmf')
                elif cmf <= -0.12: adj -= 1.0; flags.append('negative_cmf')
            except Exception: pass
        if ctx.get('mfi14') is not None:
            try:
                mfi=float(ctx.get('mfi14'))
                if 50 <= mfi <= 75: adj += 0.5; flags.append('healthy_mfi')
                elif mfi >= 85: adj -= 0.8; flags.append('overheated_mfi')
            except Exception: pass
        if ctx.get('volume_surge_vs_20d') is not None:
            try:
                vs=float(ctx.get('volume_surge_vs_20d'))
                if vs >= 1.5: adj += 0.5; flags.append('volume_surge')
                elif vs < 0.35: adj -= 0.4; flags.append('thin_volume')
            except Exception: pass
        adj=max(-1.5, min(3.2, adj))
        return {'adjustment': round(adj,2), 'tier': 'technical_supply_proxy', 'reason': 'obv_cmf_mfi_volume_confirmation_proxy_slightly_upweighted_validation_gated', 'flags': flags}
    signals=[]
    optimizer_plan=strategy_success_optimizer_plan()
    market_context = market_context_for_symbol(symbol, latest_market_context())
    mover_context = mover_context_for_symbol(symbol)
    supply_context = supply_close_context_for_symbol(symbol)
    investor_flow_context = investor_flow_seed_context_for_symbol(symbol, conn=conn)
    supply_score_adjustment = supply_close_score_adjustment(supply_context)
    if not supply_context:
        supply_score_adjustment = derived_supply_proxy_adjustment(technical_context)
    investor_flow_adjustment = investor_flow_score_adjustment(investor_flow_context)
    combined_supply_adjustment = {**supply_score_adjustment}
    combined_supply_adjustment['base_adjustment'] = supply_score_adjustment.get('adjustment')
    combined_supply_adjustment['investor_flow_adjustment'] = investor_flow_adjustment
    combined_supply_adjustment['adjustment'] = round(float(supply_score_adjustment.get('adjustment') or 0) + float(investor_flow_adjustment.get('adjustment') or 0), 2)
    combined_supply_adjustment['combined_reason'] = 'supply_close_or_technical_proxy_plus_investor_flow_seed_validation_gated'
    blocked_edges={(x.get('logic'), x.get('symbol')): x for x in (optimizer_plan.get('blocked_logic_symbols') or [])}
    preferred_edges={(x.get('logic'), x.get('symbol')): x for x in (optimizer_plan.get('preferred_logic_symbols') or [])}
    blocked_markets={(x.get('logic'), x.get('market')): x for x in (optimizer_plan.get('blocked_logic_markets') or [])}
    preferred_markets={(x.get('logic'), x.get('market')): x for x in (optimizer_plan.get('preferred_logic_markets') or [])}
    target_adjustments={(x.get('logic'), x.get('market')): x for x in (optimizer_plan.get('target_adjustments') or [])}
    target_acceptance=target_adjustment_acceptance_map()
    symbol_market=market_of(symbol)
    audit_quality=latest_audit_quality_by_logic()
    strategy_router=load_strategy_context_router()
    fund_consensus_packet=load_fund_consensus()
    fund_symbol_consensus=fund_consensus_for_symbol(symbol, fund_consensus_packet)
    fund_style_consensus=(fund_consensus_packet.get('summary') or {}).get('top_styles') or {}
    for strat in active_strategies:
        success_gate = strategy_success_gate(strat['logic'])
        repair_watch_only = False
        if not success_gate.get('recommendation_enabled', True):
            # Severe tail/EV strategies should not generate buy candidates, but keeping
            # their current signals visible as low-weight repair-watch evidence helps the
            # pipeline route validation/exit retests instead of going blind when all active
            # paper strategies are under profit guard.
            if success_gate.get('severe_tail_or_ev_guard'):
                repair_watch_only = True
            else:
                continue
        blocked_edge=blocked_edges.get((strat['logic'], symbol))
        if blocked_edge:
            continue
        market_block=blocked_markets.get((strat['logic'], symbol_market))
        market_prefer=preferred_markets.get((strat['logic'], symbol_market))
        if strat['logic'].startswith('us_') and symbol.endswith(('.KS', '.KQ')):
            continue
        if not logic_config(strat['logic']):
            continue
        sig=signal(analysis_rows, strat['logic'])
        if not sig:
            continue
        if sig['action']!='candidate_buy_zone':
            if repair_watch_only:
                # Repair-active overlays are intentionally watch/research-only.
                # Keep their current technical evidence visible so validation and
                # fund overlays have symbols to inspect, but never let this path
                # become a buy approval.
                sig=dict(sig)
                sig['repair_watch_signal'] = True
            else:
                continue
        target_adjustment=target_adjustments.get((strat['logic'], symbol_market))
        target_accept=target_acceptance.get((strat['logic'], symbol_market)) or {}
        target_policy=None
        if target_adjustment:
            accepted=bool(target_accept.get('accepted'))
            original_target=sig.get('target')
            scale=float(target_adjustment.get('target_scale') or 1.0)
            target_policy={'original_target':original_target,'adjusted_target':None,'target_scale':scale,'target_return_adjustment_pct_points':target_adjustment.get('target_return_adjustment_pct_points'),'target_adjustment_basis':target_adjustment.get('target_adjustment_basis'),'short_horizon_hint':target_adjustment.get('short_horizon_hint'),'source':'strategy_success_optimizer','reason':target_adjustment.get('reason'),'market':symbol_market,'samples':target_adjustment.get('samples'),'avg_excess_return_pct':target_adjustment.get('avg_excess_return_pct'),'success_rate_pct':target_adjustment.get('success_rate_pct'),'accepted':accepted,'acceptance_reason':target_accept.get('acceptance_reason') or 'missing_or_rejected_auditor_acceptance','acceptance_delta':target_accept.get('delta_vs_original')}
            if accepted and original_target and close:
                pp=target_adjustment.get('target_return_adjustment_pct_points')
                if pp is not None:
                    adjusted_target=round(float(close) * (1 + max(((float(original_target)-float(close))/float(close)*100) - float(pp), 0.0) / 100), 2)
                else:
                    adjusted_target=round(close + (float(original_target)-close)*scale, 2)
                if adjusted_target > close:
                    sig=dict(sig)
                    sig['target']=adjusted_target
                    target_policy['adjusted_target']=adjusted_target
                    target_policy['applied']=True
            elif not accepted:
                # Keep the original target. The adjustment remains visible as a rejected/provisional research proposal.
                target_policy['applied']=False
                target_policy['acceptance_status']=target_accept.get('acceptance_status') or ('rejected' if target_accept else 'audit_acceptance_missing')
                target_policy['samples_needed_for_acceptance']=target_accept.get('samples_needed_for_acceptance')
        market_profile=((strat.get('summary') or {}).get('market_profile') or {}).get(symbol_market) or {}
        avg_excess=float(market_profile.get('avg_excess_return_pct') if market_profile.get('avg_excess_return_pct') is not None else (strat.get('avg_excess_return_pct') or 0))
        excess_win=float(market_profile.get('excess_win_rate_pct') if market_profile.get('excess_win_rate_pct') is not None else ((strat.get('summary') or {}).get('excess_win_rate_pct') or 50))
        market_samples=int(market_profile.get('samples') or 0)
        market_edge_penalty=8 if market_block and market_samples >= 80 else 0
        market_edge_bonus=4 if market_prefer and market_samples >= 80 else 0
        sym_stats=symbol_validation_stats(strat, symbol)
        sym_edge=sym_stats['edge']
        # Symbol-specific validation is a risk overlay only. New symbols should not be penalized for
        # having no per-symbol samples; only clear negative evidence with enough samples should hurt.
        sample_bonus=min(3, sym_stats['samples'] * 0.08) if sym_edge > 0 else 0
        no_sample_penalty=0
        weak_symbol_penalty=8 if sym_stats['samples'] >= 5 and sym_edge < -2 and (sym_stats.get('success_rate_pct') or 100) < 35 else 0
        preferred_edge=preferred_edges.get((strat['logic'], symbol))
        preferred_bonus=5 if preferred_edge else 0
        router_decision=router_row_for(strat['logic'], strategy_router)
        router_multiplier=float(router_decision.get('score_multiplier') or 1.0)
        router_family=router_decision.get('family')
        regime_context=(strategy_router.get('regime_context') or {}) if isinstance(strategy_router, dict) else {}
        fund_style_alignment=fund_style_context_alignment(router_family, regime_context.get('regime'), fund_style_consensus)
        fund_style_boost=float(fund_style_alignment.get('boost') or 0)
        if router_decision.get('decision') == 'deprioritize' and fund_style_alignment.get('aligned_with_regime') is False:
            router_multiplier=min(router_multiplier, 0.72)
        weight=strategy_weight(strat) * (0.25 if repair_watch_only else 1.0) * router_multiplier
        q=audit_quality.get(strat['logic'], {})
        q_score=q.get('quality_score')
        q_penalty=0
        q_flags=set(q.get('quality_flags') or [])
        # For recommendation ranking, audit quality is a cautionary score adjustment, not a hard veto.
        # The auditor's newer trust-axis/role contract is the primary quality overlay; legacy flags
        # remain as back-compat guardrails until all downstream reports are contract-native.
        audit_score_adjustment=audit_quality_score_adjustment(q)
        q_penalty += float(audit_score_adjustment.get('penalty') or 0)
        if q.get('verdict') != 'pass': q_penalty += 2
        if q_score is not None and q_score < 45: q_penalty += 2
        if 'period_instability' in q_flags: q_penalty += 1.5
        if 'unfavorable_payoff_asymmetry' in q_flags: q_penalty += 2
        if 'recent_decay' in q_flags: q_penalty += 3
        if repair_watch_only: q_penalty += 14
        symbol_edge_component=max(-6, min(6, sym_edge*1.2)) if sym_stats['samples'] >= 5 else 0
        audit_contract_component=float(audit_score_adjustment.get('boost') or 0)
        logic_score=(max(0, sig['score']) + avg_excess*3 + (excess_win-45)*0.55 + symbol_edge_component + sample_bonus + market_edge_bonus + fund_style_boost + audit_contract_component - no_sample_penalty - weak_symbol_penalty - market_edge_penalty - q_penalty) * weight
        signals.append({'logic':strat['logic'],'score':round(logic_score,2),'raw_score':sig['score'],'target':sig['target'],'stop':sig['stop'],'avg_excess_return_pct':avg_excess,'excess_win_rate_pct':excess_win,'symbol_edge_pct':round(sym_edge,2),'symbol_samples':sym_stats['samples'],'symbol_success_rate_pct':sym_stats['success_rate_pct'],'symbol_edge_bucket':sym_stats['bucket'],'market_profile':market_profile,'market_edge_policy':{'blocked':bool(market_block),'preferred':bool(market_prefer),'penalty':market_edge_penalty,'bonus':market_edge_bonus},'target_policy':target_policy,'strategy_weight':weight,'strategy_tier':'repair_watch_only' if repair_watch_only else ('aggressive_research_active' if weight <= 0.35 else ('high_upside_probation' if weight < 0.5 else ('probationary' if weight < 1 else 'core'))),'strategy_success_gate':success_gate,'payoff_profile':(strat.get('summary') or {}).get('payoff_profile') or {},'indicator_meta':classify_indicator_logic(strat['logic']),'indicator_family':((strat.get('summary') or {}).get('payoff_profile') or {}).get('indicator_family') or classify_indicator_logic(strat['logic']).get('indicator_family'),'indicator_role':((strat.get('summary') or {}).get('payoff_profile') or {}).get('indicator_role') or classify_indicator_logic(strat['logic']).get('indicator_role'),'technical_signal_role':((strat.get('summary') or {}).get('payoff_profile') or {}).get('technical_signal_role'),'position_size_hint':((strat.get('summary') or {}).get('payoff_profile') or {}).get('position_size_hint'),'lookahead_safety':((strat.get('summary') or {}).get('payoff_profile') or {}).get('lookahead_safety'),'audit_quality':q,'audit_quality_penalty':round(q_penalty,2),'audit_quality_score_adjustment':audit_score_adjustment,'strategy_context_router':router_decision,'fund_style_context_alignment':fund_style_alignment,'fund_style_consensus_boost':fund_style_boost,'short_horizon_profile':short_horizon_profile_for_logic(strat['logic'], short_horizon_profiles),'reasons':sig['reasons']})
    disclosures, penalty=disclosure_penalty(conn, symbol)
    impact = disclosure_impact_summary(conn, symbol)
    if impact:
        disclosures.update({'impact_assessed_count': impact.get('assessed_count'), 'impact_neutral': impact.get('neutral'), 'impact_low_negative': impact.get('low_negative'), 'impact_latest_reason': impact.get('latest_reason')})
        disclosures['high'] = impact.get('high', disclosures['high'])
        disclosures['medium'] = impact.get('medium', disclosures['medium'])
        disclosures['positive'] = max(disclosures.get('positive', 0), impact.get('positive', 0))
        effective_medium = adjusted_disclosure_medium_count(disclosures)
        disclosures['effective_medium'] = effective_medium
        disclosures['medium_softened'] = effective_medium < disclosures.get('medium', 0)
        penalty = disclosures['high'] * 50 + effective_medium * 10 + (impact.get('low_negative', 0) * 3) - disclosures['positive'] * 5
    disclosures.setdefault('effective_medium', adjusted_disclosure_medium_count(disclosures))
    disclosures.setdefault('medium_softened', disclosures.get('effective_medium', disclosures.get('medium',0)) < disclosures.get('medium',0))
    corporate_action_risk = symbol_corporate_action_risk(conn, symbol)
    if corporate_action_risk.get('flagged'):
        ca_penalty = 80 if corporate_action_risk.get('severity') == 'high' else 35
        penalty += ca_penalty
        disclosures['high' if corporate_action_risk.get('severity') == 'high' else 'medium'] += corporate_action_risk.get('event_count', 1)
    financial_quality = latest_financial_quality(symbol) if symbol.endswith(('.KS', '.KQ')) else None
    financial_adjustment = float((financial_quality or {}).get('score_adjustment') or 0)
    if not signals:
        return None
    signals=sorted(signals,key=lambda x:x['score'],reverse=True)
    previous_recommendation=latest_recommendation_strategy(conn, symbol)
    signals, hysteresis_note=apply_best_signal_hysteresis(signals, previous_recommendation)
    consensus=len(signals)
    weighted_consensus=round(sum(s.get('strategy_weight',1.0) for s in signals),2)
    best=signals[0]
    target_values=[s['target'] for s in signals]
    original_target_1=round(min(target_values),2)
    original_target_2=round(sum(target_values)/len(target_values),2)
    original_target_3=round(max(target_values),2)
    target_parameter_policy=load_target_return_parameter_policy()
    target_return_adjustment_pct_points=abs(float(target_parameter_policy.get('adjustment_pct_points') or -TARGET_RETURN_ADJUSTMENT_PCT_POINTS))
    def adjusted_target_level(original_target: float) -> float:
        if not close or not original_target:
            return original_target
        original_upside_pct = (float(original_target) / float(close) - 1) * 100
        adjusted_upside_pct = max(original_upside_pct - target_return_adjustment_pct_points, 0.0)
        return round(float(close) * (1 + adjusted_upside_pct / 100), 2)
    target_1=adjusted_target_level(original_target_1)
    target_2=adjusted_target_level(original_target_2)
    target_3=adjusted_target_level(original_target_3)
    tight_stop=round(max(s['stop'] for s in signals),2)
    market20=current_market_return(conn,symbol)
    top_signals = signals[:5]
    total_symbol_samples=sum(s.get('symbol_samples') or 0 for s in top_signals)
    positive_symbol_edges = [s for s in top_signals if s.get('symbol_edge_pct', 0) > 0]
    audit_contract = recommendation_audit_contract(top_signals, (strategy_router.get('regime_context') or {}))
    audit_contract_axes = audit_contract.get('trust_axes') or {}
    audit_contract_supportive = (
        (audit_contract.get('primary_label') or {}).get('kind') == 'good'
        or 'risk_adjusted_candidate' in set(audit_contract.get('role_labels') or [])
        or (
            float(audit_contract_axes.get('return_edge') or 0) >= 58
            and float(audit_contract_axes.get('tail_safety') or 0) >= 58
            and float(audit_contract_axes.get('confidence') or 0) >= 58
        )
    )
    # Per-symbol samples are useful context, not a prerequisite for current recommendations.
    symbol_sample_bonus=min(3, total_symbol_samples * 0.02) if positive_symbol_edges else 0
    indicator_risk_penalty=0
    indicator_guard_flags=[]
    if technical_context:
        if technical_context.get('trend_strength') == 'weak':
            indicator_risk_penalty += 4
            indicator_guard_flags.append('weak_adx_trend_strength')
        if technical_context.get('atr_bucket') == 'high':
            indicator_risk_penalty += 5
            indicator_guard_flags.append('high_atr_volatility')
        if technical_context.get('volume_confirmation') is False:
            indicator_risk_penalty += 3
            indicator_guard_flags.append('missing_obv_cmf_mfi_volume_confirmation')
        if technical_context.get('overheated_chase_risk'):
            indicator_risk_penalty += float(technical_context.get('chasing_penalty') or 8)
            indicator_guard_flags.append('overheated_momentum_chase_risk')
    policy=recommendation_policy()
    aggressive_org = policy.get('profile') == 'aggressive_growth_research'
    market_context_boost = float((market_context or {}).get('context_score_boost') or 0) + float((mover_context or {}).get('context_score_boost') or 0)
    fund_consensus_boost = min(8.0, float((fund_symbol_consensus or {}).get('weighted_score') or 0) * 1.2)
    best_summary = best.get('summary') or best.get('strategy_success_gate') or {}
    strategy_quality_tier = best_summary.get('quality_tier') or best_summary.get('active_tier') or ('tail_risk_limited' if best_summary.get('severe_tail_or_ev_guard') else None) or ('quality_active' if best_summary.get('quality_active') else None) or ('research_active' if (best_summary.get('tier') == 'research_only' or best_summary.get('trade_eligible_strategy') is False) else 'unknown')
    quality_tier_penalty = 0.0
    if strategy_quality_tier == 'research_active':
        quality_tier_penalty = 4.0
    elif strategy_quality_tier == 'tail_risk_limited':
        quality_tier_penalty = 8.0
    confidence=round(min(100, max(0, 26 + best['score']*0.52 + weighted_consensus*3.5 + symbol_sample_bonus - penalty + financial_adjustment - indicator_risk_penalty + market_context_boost + fund_consensus_boost + float(combined_supply_adjustment.get('adjustment') or 0) - quality_tier_penalty)),2)
    action='watch'
    effective_medium = int(disclosures.get('effective_medium', disclosures.get('medium', 0)) or 0)
    if confidence>=policy['candidate_threshold'] and disclosures['high']==0 and effective_medium<2 and financial_adjustment > -15:
        action='candidate_buy_zone'
    if disclosures['high']>0 or effective_medium>=3:
        action='avoid'
    if financial_adjustment <= -20:
        action='avoid'
    elif financial_adjustment <= -15 and action == 'candidate_buy_zone':
        action='watch'

    audit_hard_downgrade_signals = [
        s for s in top_signals
        if (s.get('audit_quality') or {}).get('quality_score') is not None
        and float((s.get('audit_quality') or {}).get('quality_score') or 0) < 45
        and {'no_positive_average_excess', 'negative_expected_excess_value'}.issubset(
            set((s.get('audit_quality') or {}).get('quality_flags') or [])
        )
    ]
    if action == 'candidate_buy_zone' and audit_hard_downgrade_signals:
        action='watch'
    if action == 'candidate_buy_zone' and any((s.get('strategy_success_gate') or {}).get('severe_tail_or_ev_guard') for s in top_signals):
        action='watch'
    tail_kill_signals = [
        s for s in top_signals
        if (s.get('audit_quality') or {}).get('expected_excess_value') is not None
        and float((s.get('audit_quality') or {}).get('expected_excess_value') or 0) < -3
        and (s.get('audit_quality') or {}).get('p10_excess') is not None
        and float((s.get('audit_quality') or {}).get('p10_excess') or 0) < -10
    ]
    role_avoid_signals = [
        s for s in top_signals
        if (s.get('audit_quality') or {}).get('best_use') == 'avoid_or_small_research_weight'
        or 'left_tail_risk' in set((s.get('audit_quality') or {}).get('role_labels') or [])
    ]
    thin_no_edge = total_symbol_samples < 10 and len(positive_symbol_edges) <= 0
    profit_guard_notes=[]
    if action == 'candidate_buy_zone' and tail_kill_signals:
        action='watch'
        profit_guard_notes.append('수익률 개선 게이트: 기대초과수익/좌측꼬리 동시 악화 전략은 buy 후보에서 watch로 강등')
    if action == 'candidate_buy_zone' and role_avoid_signals:
        action='watch'
        profit_guard_notes.append('전략 역할 게이트: Audit 역할 라벨이 회피/좌측꼬리 위험이면 buy 후보에서 watch로 강등')
    if action == 'candidate_buy_zone' and thin_no_edge:
        action='watch'
        profit_guard_notes.append('수익률 개선 게이트: 종목별 조건 라벨 부족 + 양수 edge 없음 → 검증 우선')
    if action == 'candidate_buy_zone' and technical_context:
        if technical_context.get('trend_strength') == 'weak' and technical_context.get('volume_confirmation') is False:
            action='watch'
        elif technical_context.get('atr_bucket') == 'high' and technical_context.get('trend_strength') == 'weak':
            action='watch'
        elif technical_context.get('overheated_chase_risk') and confidence < policy['candidate_threshold'] + 8:
            action='watch'

    avg_active_excess = round(sum(s['avg_excess_return_pct'] for s in top_signals) / len(top_signals), 2)
    avg_excess_win = round(sum(s['excess_win_rate_pct'] for s in top_signals) / len(top_signals), 2)
    reasons=[
        f"활성 전략 {consensus}개(가중 합의 {weighted_consensus})가 현재 관심 매수 구간으로 판단",
        f"상위 전략 평균 초과수익 {avg_active_excess}%는 보조 랭킹 근거로만 반영",
        f"대표 전략: {logic_label(best['logic'])} · 전략 신뢰/국면 라벨 기준으로 해석",
    ]
    if positive_symbol_edges:
        reasons.append(f"종목별 과거 edge 양수 전략 {len(positive_symbol_edges)}개")
    for rr in best.get('reasons', [])[:3]: reasons.append(korean_reason(rr))
    if market20 is not None: reasons.append(f"벤치마크 20일 수익률 {market20}% 대비 현재 신호 확인")
    if disclosures['high'] or disclosures['medium'] or disclosures['positive']:
        reasons.append(f"공시 리스크 H:{disclosures['high']} M:{disclosures['medium']} 긍정:{disclosures['positive']}")
    if financial_quality:
        fq_bits = []
        if financial_quality.get('supports'):
            fq_bits.append('지원: ' + ', '.join(financial_quality['supports'][:2]))
        if financial_quality.get('warnings'):
            fq_bits.append('경고: ' + ', '.join(financial_quality['warnings'][:2]))
        if fq_bits:
            reasons.append('재무 품질 ' + ' / '.join(fq_bits))
    fund_votes = int((fund_symbol_consensus or {}).get('votes') or (fund_symbol_consensus or {}).get('vote_count') or 0)
    if fund_votes:
        reasons.append(f"Fund evidence 지지 {fund_votes}표 · 가중점수 {round(float((fund_symbol_consensus or {}).get('weighted_score') or 0),2)} · 판단 보조점수 {fund_consensus_boost:+.1f}")
    elif action == 'candidate_buy_zone':
        reasons.append('Fund evidence 지지는 아직 없음 · 단독 차단 사유는 아니지만 추가 확인 대상으로 표시')
    if supply_context:
        supply_adj=float(combined_supply_adjustment.get('adjustment') or 0)
        reasons.append(supply_close_plain_text(supply_context) + (f" · 수급/거래주체 판단 보조점수 {supply_adj:+.1f}" if supply_adj else ''))
    risk_notes=[f"분석 기준가: 정규장 완료 일봉 종가({latest_price_date}) · 프리/애프터마켓·실시간 가격/주문 아님"]
    if disclosures['high'] == 0 and effective_medium < 2:
        risk_notes.append('최근 고위험 공시 필터 통과')
        if disclosures.get('medium_softened'):
            risk_notes.append(f"공시 영향평가 반영: 반복/중립 medium {disclosures.get('medium',0)}건을 effective {effective_medium}건으로 완화")
    else:
        risk_notes.append('공시 리스크로 보수적 판단 필요')
    if market20 is not None and market20 < -5:
        risk_notes.append('벤치마크 단기 약세 구간')
    if strategy_quality_tier in ('research_active','tail_risk_limited'):
        risk_notes.append(f'전략 품질 티어: {strategy_quality_tier} · tail-risk 개선 전까지 research 가중치 제한')
    if any((s.get('strategy_success_gate') or {}).get('severe_tail_or_ev_guard') for s in top_signals):
        risk_notes.append('수익률 개선 모드: severe tail/negative EV 전략 신호는 repair-watch 전용이며 buy 후보로 승격 금지')
    for note in reversed(profit_guard_notes if 'profit_guard_notes' in locals() else []):
        if note not in risk_notes:
            risk_notes.insert(0, note)
    if mover_context:
        risk_notes.append(f"금일 mover/shock seed: {mover_context.get('direction')} {mover_context.get('change_pct')}% · {mover_context.get('data_timing')} · 추천/승격은 검증 우선순위에만 반영")
    if fund_votes:
        risk_notes.append(f"Fund evidence는 추천 판단 보조근거로 반영됨: votes {fund_votes}, weighted_score {round(float((fund_symbol_consensus or {}).get('weighted_score') or 0),2)}, boost {fund_consensus_boost:+.1f}")
    else:
        risk_notes.append('Fund evidence: 해당 종목 fund consensus 지지 없음/미성숙 · 전략/audit 근거 우선')
    if supply_context:
        risk_notes.append(supply_close_plain_text(supply_context) + ' · 수급/거래주체 evidence를 추천 판단 보조근거로 반영')
    else:
        adj=float(combined_supply_adjustment.get('adjustment') or 0)
        note='거래주체/수급 설명: 외국인·기관·개인 순매수 데이터 미연동, 현재는 OBV/CMF/MFI/거래량 proxy만 약하게 점수 반영'
        if adj:
            note += f'({adj:+.1f})'
        risk_notes.append(note)
    if market_context:
        if market_context.get('impact_score', 0) >= 62:
            risk_notes.append(f"시장 선행 이슈: {market_context.get('label') or market_context.get('theme')} · {market_context.get('summary') or '관련 종목에 긍정 컨텍스트'} · 점수보정 +{market_context.get('context_score_boost',0)}")
        if market_context.get('gap_chase_risk') in ('high_chase_risk', 'moderate_chase_risk'):
            risk_notes.append('갭상승 추격 리스크: 장 초반 급등 시 관망 우선')
    if technical_context:
        if technical_context.get('trend_strength') == 'weak':
            risk_notes.append('ADX 추세강도 약함: 기술지표 신호 과신 금지')
        if technical_context.get('atr_bucket') == 'high':
            risk_notes.append('ATR 변동성 높음: 포지션 사이즈 축소 우선')
        if not technical_context.get('volume_confirmation'):
            risk_notes.append('거래량 확인 미흡: OBV/CMF/MFI 보조 확인 부족')
        if technical_context.get('overheated_chase_risk'):
            risk_notes.append(f"단기 과열 추격 리스크: 5일 {technical_context.get('return_5d_pct')}%, 20일 {technical_context.get('return_20d_pct')}%, 20일 고점 근접 {technical_context.get('near_20d_high_pct')}% · 모멘텀 신호는 유지하되 추격매수 감점")
    if consensus < 3:
        risk_notes.append('전략 합의 수가 낮아 관찰 필요')
    weak_quality_signals=[s for s in top_signals if (s.get('audit_quality') or {}).get('verdict') != 'pass']
    if weak_quality_signals:
        risk_notes.append('검증 품질 주의: ' + ', '.join(sorted({logic_label(s['logic']) for s in weak_quality_signals})[:3]))
    if audit_hard_downgrade_signals:
        risk_notes.insert(0, '관망 이유: 감사 품질 저하 및 평균 초과수익 비양수 전략 포함')
    if corporate_action_risk.get('flagged'):
        risk_notes.insert(0, '기업행위/거래정지 공시 감지: ' + ', '.join(corporate_action_risk.get('keywords') or []))
        action='watch'
    if financial_quality:
        if financial_quality.get('warnings'):
            risk_notes.append('재무 경고: ' + ', '.join(financial_quality['warnings'][:3]))
        elif financial_quality.get('supports'):
            risk_notes.append('재무 품질 확인: ' + ', '.join(financial_quality['supports'][:2]))
    upside_1_pct = pct(target_1,close)
    upside_2_pct = pct(target_2,close)
    upside_3_pct = pct(target_3,close)
    downside_stop_pct = pct(tight_stop,close)
    original_upside_1_pct = pct(original_target_1,close)
    original_upside_2_pct = pct(original_target_2,close)
    original_upside_3_pct = pct(original_target_3,close)
    target_return_adjustment={
        'policy':'paper_research_target_return_parameter_arm_validation_gated',
        'adjustment_pct_points': -target_return_adjustment_pct_points,
        'basis':target_parameter_policy.get('basis') or 'target_return_parameter_arm_default',
        'source':target_parameter_policy.get('source'),
        'meta_decision':target_parameter_policy.get('decision'),
        'meta_run_at':target_parameter_policy.get('meta_run_at'),
        'candidate_adjustments':target_parameter_policy.get('candidate_adjustments') or [],
        'parameter_arms':'-2.0,-1.5,-1.0,-0.5,0.0',
        'note':'장기 목표수익률 보정치는 전략 파라미터 arm으로 취급하며, 수익률 개선 메타 에이전트가 paper outcome 기반으로 후보 보정치를 비교/제안합니다.',
    }
    short_target_policy={
        'policy':'paper_research_optimizer_controlled_target_adjustment',
        'basis':'strategy_success_optimizer_with_short_horizon_profit_profile_reference',
        'note':f"장기 목표수익률 기본 보정({-target_return_adjustment_pct_points}%p)은 적용하되, 추가 보정은 수익률 개선 게이트가 short-horizon 근거와 auditor acceptance를 참고해 조정합니다.",
    }
    short_profiles=[s.get('short_horizon_profile') or {} for s in top_signals if (s.get('short_horizon_profile') or {}).get('samples')]
    short_horizon_profile={}
    if short_profiles:
        total_sp=sum(int(x.get('samples') or 0) for x in short_profiles) or 1
        def wsp(key):
            vals=[(float(x.get(key) or 0), int(x.get('samples') or 0)) for x in short_profiles if x.get(key) is not None]
            return round(sum(v*n for v,n in vals)/sum(n for _,n in vals),2) if vals and sum(n for _,n in vals) else None
        hit15=wsp('hit_1_5_pct')
        adjusted2=wsp('target_minus_2_pct_points_hit_pct') or wsp('target_under_2_pct_hit_pct') or wsp('target_or_under_2pct_pct')
        short_horizon_profile={
            'policy':'paper_research_adjusted_target_context_only',
            'horizon_days':2,
            'sample_count':total_sp,
            'hit_1_pct':wsp('hit_1_pct'),
            'hit_1_5_pct':hit15,
            'hit_2_pct':wsp('hit_2_pct'),
            'target_hit_pct':wsp('target_hit_pct'),
            'target_minus_1_pct_point_hit_pct':wsp('target_minus_1_pct_point_hit_pct') or wsp('target_under_1_pct_hit_pct'),
            'target_minus_1_5_pct_points_hit_pct':wsp('target_minus_1_5_pct_points_hit_pct') or wsp('target_under_1_5_pct_hit_pct'),
            'target_minus_2_pct_points_hit_pct':adjusted2,
            'target_under_1_pct_hit_pct':wsp('target_minus_1_pct_point_hit_pct') or wsp('target_under_1_pct_hit_pct'),
            'target_under_1_5_pct_hit_pct':wsp('target_minus_1_5_pct_points_hit_pct') or wsp('target_under_1_5_pct_hit_pct'),
            'target_under_2_pct_hit_pct':adjusted2,
            'target_or_under_2pct_pct':adjusted2,
            'avg_max_up_pct':wsp('avg_max_up_pct'),
            'avg_final_return_pct':wsp('avg_final_return_pct'),
            'avg_target_ret_pct':wsp('avg_target_ret_pct'),
            'profile':'strong_adjusted_target_touch' if (adjusted2 is not None and adjusted2 >= 50) else ('watch_adjusted_target_touch' if (adjusted2 is not None and adjusted2 >= 25) else 'weak_adjusted_target_touch'),
            'by_horizon': {},
            'note':'장기 목표1 수익률에서 -2/-1.5/-1%p 낮춘 1~2거래일 보정 참고선; 실거래/주문 지시 아님',
        }
        horizon_keys=sorted({hk for sp in short_profiles for hk in ((sp.get('by_horizon') or {}).keys())}, key=lambda x:int(x) if str(x).isdigit() else 999)
        for hk in horizon_keys:
            h_profiles=[(sp.get('by_horizon') or {}).get(hk) or {} for sp in short_profiles if ((sp.get('by_horizon') or {}).get(hk) or {}).get('samples')]
            if not h_profiles: continue
            total_h=sum(int(x.get('samples') or 0) for x in h_profiles) or 1
            def hwsp(key):
                vals=[(float(x.get(key) or 0), int(x.get('samples') or 0)) for x in h_profiles if x.get(key) is not None]
                return round(sum(v*n for v,n in vals)/sum(n for _,n in vals),2) if vals and sum(n for _,n in vals) else None
            h_adj=hwsp('target_minus_2_pct_points_hit_pct') or hwsp('target_under_2_pct_hit_pct') or hwsp('target_or_under_2pct_pct')
            short_horizon_profile['by_horizon'][hk]={
                'horizon_days': int(hk) if str(hk).isdigit() else hk,
                'sample_count': total_h,
                'target_hit_pct': hwsp('target_hit_pct'),
                'target_minus_1_pct_point_hit_pct': hwsp('target_minus_1_pct_point_hit_pct') or hwsp('target_under_1_pct_hit_pct'),
                'target_minus_1_5_pct_points_hit_pct': hwsp('target_minus_1_5_pct_points_hit_pct') or hwsp('target_under_1_5_pct_hit_pct'),
                'target_minus_2_pct_points_hit_pct': h_adj,
                'avg_max_up_pct': hwsp('avg_max_up_pct'),
                'avg_final_return_pct': hwsp('avg_final_return_pct'),
                'profile': 'strong_adjusted_target_touch' if (h_adj is not None and h_adj >= 50) else ('watch_adjusted_target_touch' if (h_adj is not None and h_adj >= 25) else 'weak_adjusted_target_touch'),
            }
    high_confidence_count=sum(1 for s in top_signals if (s.get('strategy_success_gate') or {}).get('high_confidence_historical'))
    technical_support_signals=[s for s in top_signals if (s.get('payoff_profile') or {}).get('class') in ('asymmetric_alpha','fragile_alpha') or str(s.get('logic','')).startswith('technical_')]
    asymmetric_alpha_signals=[s for s in top_signals if (s.get('payoff_profile') or {}).get('class') == 'asymmetric_alpha']
    aggressive_research_count=sum(1 for s in top_signals if s.get('strategy_tier') == 'aggressive_research_active')
    research_quality_ok = (
        confidence >= policy['research_candidate_threshold']
        and weighted_consensus >= policy['research_weighted_consensus_min']
        and (avg_excess_win >= policy['research_excess_win_min'] or audit_contract_supportive)
        and disclosures['high'] == 0
        and effective_medium < 2
        and financial_adjustment > -15
    )
    if technical_support_signals:
        risk_notes.append('기술지표 역할: 단독 매수 근거가 아니라 비대칭 보조 알파/컨텍스트로만 사용')
    if action == 'candidate_buy_zone' and high_confidence_count == 0:
        aggressive_research_ok = aggressive_org and policy.get('allow_aggressive_research_candidate') and aggressive_research_count > 0 and confidence >= policy['candidate_threshold'] and weighted_consensus >= 1.0
        if research_quality_ok and not (technical_support_signals and len(technical_support_signals) == len(top_signals)):
            risk_notes.insert(0, '주의: 고신뢰 과거이력검증 전략은 없지만, 현 신호/전략합의/종목별 edge가 충분해 research candidate로 유지')
        elif aggressive_research_ok:
            risk_notes.insert(0, '공격형 조직모드: 고신뢰 검증 전이지만 paper-only aggressive research candidate로 노출')
        else:
            action='watch'
            risk_notes.insert(0, '관망 이유: 고신뢰 과거이력검증 전략 부재 또는 기술지표 보조 알파 단독 신호')

    raw_confidence=confidence
    confidence, score_smoothing_note=smooth_recommendation_score(previous_recommendation, action, raw_confidence)
    if score_smoothing_note:
        risk_notes.append(f"점수 안정화 적용: 원점수 {score_smoothing_note['raw_score']} → 표시점수 {score_smoothing_note['smoothed_score']}")
    watch_reason=explain_decision(action, confidence, consensus, disclosures, financial_quality, market20, total_symbol_samples, positive_symbol_edges, avg_excess_win, upside_1_pct, downside_stop_pct)
    if action == 'watch' and watch_reason.get('primary') and not any(n.startswith('관망 이유:') for n in risk_notes):
        risk_notes.insert(0, '관망 이유: ' + watch_reason['primary'])
    entry_plan = build_entry_plan(close, target_1, tight_stop, technical_context, action)
    risk_notes.append(
        f"목표매입가 제안: {entry_plan.get('target_buy_price')} 이하 중심, 허용상단 {entry_plan.get('acceptable_entry_upper')}, "
        f"{entry_plan.get('chase_above_price')} 초과 추격 금지 · {entry_plan.get('label')}"
    )

    validation_basis={
        'top_signal_count': len(top_signals),
        'weighted_strategy_consensus': weighted_consensus,
        'avg_active_excess_return_pct': avg_active_excess,
        'avg_excess_win_rate_pct': avg_excess_win,
        'audit_hard_downgrade': bool(audit_hard_downgrade_signals),
        'audit_hard_downgrade_logics': [s.get('logic') for s in audit_hard_downgrade_signals],
        'best_raw_signal_score': best.get('raw_score'),
        'short_horizon_profile': short_horizon_profile,
        'positive_symbol_edge_count': len(positive_symbol_edges),
        'symbol_validation_sample_count': total_symbol_samples,
        'benchmark_20d_return_pct': market20,
        'symbol_20d_return_pct': symbol_return(rows, 20),
        'symbol_60d_return_pct': symbol_return(rows, 60),
        'disclosure_high': disclosures['high'],
        'disclosure_medium': disclosures['medium'],
        'disclosure_effective_medium': effective_medium,
        'disclosure_medium_softened': disclosures.get('medium_softened'),
        'disclosure_positive': disclosures['positive'],
        'corporate_action_flagged': corporate_action_risk.get('flagged'),
        'corporate_action_severity': corporate_action_risk.get('severity'),
        'financial_score_adjustment': financial_adjustment,
        'financial_quality_period': (financial_quality or {}).get('latest_period'),
        'financial_warnings': (financial_quality or {}).get('warnings'),
        'financial_supports': (financial_quality or {}).get('supports'),
        'audit_quality_min_score': min([s.get('audit_quality',{}).get('quality_score') for s in top_signals if s.get('audit_quality',{}).get('quality_score') is not None], default=None),
        'audit_quality_flags': sorted({flag for s in top_signals for flag in (s.get('audit_quality',{}).get('quality_flags') or [])}),
        'audit_quality_penalty_total': round(sum(s.get('audit_quality_penalty') or 0 for s in top_signals),2),
        'target_adjustment_count': sum(1 for s in top_signals if s.get('target_policy')),
        'target_adjustment_applied_count': sum(1 for s in top_signals if (s.get('target_policy') or {}).get('applied')),
        'target_adjustment_provisional_count': sum(1 for s in top_signals if (s.get('target_policy') or {}).get('acceptance_status') == 'provisional_more_samples_needed'),
        'target_adjustment_rejected_count': sum(1 for s in top_signals if s.get('target_policy') and not (s.get('target_policy') or {}).get('applied') and (s.get('target_policy') or {}).get('acceptance_status') != 'provisional_more_samples_needed'),
        'target_adjustments': [s.get('target_policy') for s in top_signals if s.get('target_policy')],
        'strategy_context_router': {'run_at': strategy_router.get('run_at'), 'regime_context': strategy_router.get('regime_context'), 'top_signal_decisions': [s.get('strategy_context_router') for s in top_signals if s.get('strategy_context_router')]},
        'tail_kill_signal_count': len(tail_kill_signals) if 'tail_kill_signals' in locals() else 0,
        'thin_no_edge_gate': bool(thin_no_edge) if 'thin_no_edge' in locals() else False,
        'high_confidence_historical_strategy_count': high_confidence_count,
        'research_quality_override': research_quality_ok,
        'audit_contract_supportive': audit_contract_supportive,
        'audit_contract_trust_axes': audit_contract_axes,
        'trade_eligible_strategy_count': sum(1 for s in top_signals if (s.get('strategy_success_gate') or {}).get('trade_eligible_strategy')),
        'blocked_historical_edge_count': len([1 for s in signals if (s.get('historical_edge_policy') or {}).get('blocked')]),
        'preferred_historical_edge_count': sum(1 for s in top_signals if (s.get('historical_edge_policy') or {}).get('preferred')),
        'research_only_strategy_count': sum(1 for s in top_signals if (s.get('strategy_success_gate') or {}).get('tier') == 'research_only'),
        'technical_signal_role': 'supporting_alpha_only' if technical_support_signals else 'none',
        'indicator_family_counts': {fam: sum(1 for s in top_signals if s.get('indicator_family') == fam) for fam in sorted({s.get('indicator_family') for s in top_signals if s.get('indicator_family')})},
        'indicator_roles': sorted({s.get('indicator_role') for s in top_signals if s.get('indicator_role')}),
        'technical_risk_context': technical_context,
        'indicator_risk_penalty': indicator_risk_penalty,
        'indicator_guard_flags': indicator_guard_flags,'org_profile':policy.get('profile'),'aggressive_research_signal_count':aggressive_research_count if 'aggressive_research_count' in locals() else 0,
        'atr14_pct': technical_context.get('atr14_pct') if technical_context else None,
        'adx14': technical_context.get('adx14') if technical_context else None,
        'volume_confirmation': technical_context.get('volume_confirmation') if technical_context else None,
        'technical_support_signal_count': len(technical_support_signals),
        'asymmetric_alpha_signal_count': len(asymmetric_alpha_signals),
        'position_size_hint': ('small' if (technical_context or {}).get('position_size_hint_from_indicators') == 'small' else ('small' if asymmetric_alpha_signals else ('avoid_or_tiny' if technical_support_signals else 'normal'))),
        'lookahead_safety': {'entry_timing':'next_bar_after_signal_close','uses_future_data':False,'same_bar_exit_assumed':False,'close_confirmation_required':True},
        'validation_split_note': 'strategy registry separates historical aggregate and latest rolling/forward window metrics',
        'best_logic_hysteresis': hysteresis_note,
        'score_smoothing': score_smoothing_note,
        'raw_confidence_score': raw_confidence,
        'entry_plan': entry_plan,
        'target_buy_price': entry_plan.get('target_buy_price'),
        'acceptable_entry_upper': entry_plan.get('acceptable_entry_upper'),
        'chase_above_price': entry_plan.get('chase_above_price'),
        'market_context': market_context,
        'mover_context': mover_context,
        'supply_close_context': supply_context,
        'supply_close_explanation': supply_close_plain_text(supply_context) if supply_context else investor_flow_plain_text(investor_flow_context),
        'supply_close_score_adjustment': combined_supply_adjustment,
        'supply_close_score_adjustment_pct': combined_supply_adjustment.get('adjustment'),
        'supply_close_base_adjustment_pct': supply_score_adjustment.get('adjustment'),
        'investor_flow_seed_adjustment_pct': investor_flow_adjustment.get('adjustment'),
        'investor_flow_seed_context': investor_flow_context,
        'investor_flow_status': ('db_persisted_provisional_seed' if (investor_flow_context or {}).get('db_linked') else ((supply_context or {}).get('investor_flow_status') or 'not_available_in_local_db')),
        'market_context_score_boost': market_context_boost,
        'fund_consensus': fund_symbol_consensus,
        'fund_style_consensus': fund_style_consensus,
        'fund_consensus_score_boost': fund_consensus_boost,
        'fund_style_context_alignment': [s.get('fund_style_context_alignment') for s in top_signals if s.get('fund_style_context_alignment')],
        'fund_style_consensus_boost_total': round(sum(s.get('fund_style_consensus_boost') or 0 for s in top_signals),2),
        'decision_evidence_links': {
            'fund_evidence_used': bool(fund_symbol_consensus),
            'fund_votes': int((fund_symbol_consensus or {}).get('votes') or (fund_symbol_consensus or {}).get('vote_count') or 0),
            'fund_score_boost': fund_consensus_boost,
            'supply_evidence_used': bool(supply_context or investor_flow_context),
            'supply_score_boost': combined_supply_adjustment.get('adjustment'),
            'investor_flow_seed_boost': investor_flow_adjustment.get('adjustment'),
            'policy': 'decision_support_only_validation_gated_no_real_orders',
        },
        'strategy_quality_tier': strategy_quality_tier,
        'strategy_quality_tier_penalty': quality_tier_penalty,
        'market_context_impact_score': market_context.get('impact_score') if market_context else None,
        'market_context_tags': market_context.get('source_tags') if market_context else [],
        'mover_context_policy': (mover_context or {}).get('recommendation_link_policy'),
    }
    validation_basis['audit_reliability_contract'] = audit_contract
    validation_basis['audit_reliability_tags'] = audit_contract.get('labels') or []
    validation_basis['regime_fit'] = audit_contract.get('regime_fit') or {}
    validation_basis['fund_fit_reason'] = audit_contract.get('fund_fit_reason')
    grade=confidence_grade(confidence, avg_active_excess, avg_excess_win, total_symbol_samples, len(positive_symbol_edges), disclosures, financial_quality)
    explanation=build_recommendation_reason(symbol, rows, consensus, best, avg_active_excess, avg_excess_win, positive_symbol_edges, market20, disclosures, upside_1_pct, downside_stop_pct)
    human_summary=build_human_decision_summary(symbol, action, confidence, grade, watch_reason, validation_basis, risk_notes)
    presentation=build_recommendation_presentation(action, confidence, grade, watch_reason, validation_basis, risk_notes)
    best_logic = best['logic']
    # Keep explicit top-level routing/audit metadata for UI and API consumers.
    # The detailed signal list remains available, but these fields prevent
    # clients from having to infer market or primary strategy from nested data.
    recommendation_bucket = 'approved' if action == 'candidate_buy_zone' else ('rejected' if action == 'avoid' else 'watch')
    trade_eligible = action == 'candidate_buy_zone'
    return {
        'symbol':symbol,'market':market_of(symbol),'name':display_name(symbol),'action':action,'action_label':action_label(action),'recommendation_bucket':recommendation_bucket,'bucket':recommendation_bucket,'trade_eligible':trade_eligible,'score':confidence,'last_price':close,'latest_price_date':latest_price_date,'analysis_price':close,'analysis_price_date':latest_price_date,'analysis_price_meta':analysis_meta,
        'target_1':target_1,'target_2':target_2,'target_3':target_3,
        'target_buy_price':entry_plan.get('target_buy_price'),'acceptable_entry_upper':entry_plan.get('acceptable_entry_upper'),'chase_above_price':entry_plan.get('chase_above_price'),'entry_plan':entry_plan,
        'original_target_1':original_target_1,'original_target_2':original_target_2,'original_target_3':original_target_3,
        'stop_reference':tight_stop,'horizon_days':HORIZON_DAYS,'expected_period':'향후 20거래일 기준',
        'short_target_policy':short_target_policy,'target_return_adjustment':target_return_adjustment,
        'upside_1_pct':upside_1_pct,'upside_2_pct':upside_2_pct,'upside_3_pct':upside_3_pct,
        'original_upside_1_pct':original_upside_1_pct,'original_upside_2_pct':original_upside_2_pct,'original_upside_3_pct':original_upside_3_pct,
        'downside_stop_pct':downside_stop_pct,
        'strategy_consensus':consensus,'weighted_strategy_consensus':weighted_consensus,
        'logic':best_logic,'strategy_id':best_logic,'source_strategy_id':best_logic,'best_logic':best_logic,'best_logic_label':logic_label(best_logic),'best_logic_hysteresis':hysteresis_note,'market_20d_return_pct':market20,
        'disclosure_risk':disclosures,'corporate_action_risk':corporate_action_risk,'financial_quality':financial_quality,'watch_reason':watch_reason,'signals':top_signals,'reasons':reasons,'recommendation_reason':explanation,'human_summary':human_summary,'presentation':presentation,'validation_basis':validation_basis,'confidence_grade':grade,'risk_notes':risk_notes,'short_horizon_profile':short_horizon_profile,
        'audit_reliability_contract':audit_contract,'audit_reliability_tags':audit_contract.get('labels') or [],'regime_fit':audit_contract.get('regime_fit') or {},'fund_fit_reason':audit_contract.get('fund_fit_reason'),
        'technical_signal_role':validation_basis.get('technical_signal_role'),'indicator_family_counts':validation_basis.get('indicator_family_counts'),'indicator_roles':validation_basis.get('indicator_roles'),'technical_support_signal_count':validation_basis.get('technical_support_signal_count'),'asymmetric_alpha_signal_count':validation_basis.get('asymmetric_alpha_signal_count'),'position_size_hint':validation_basis.get('position_size_hint'),'lookahead_safety':validation_basis.get('lookahead_safety'),'market_context':validation_basis.get('market_context'),'mover_context':validation_basis.get('mover_context'),'technical_risk_context':validation_basis.get('technical_risk_context'),'indicator_risk_penalty':validation_basis.get('indicator_risk_penalty'),'indicator_guard_flags':validation_basis.get('indicator_guard_flags'),
        'caveat':'검증용'
    }



def market_of(symbol: str) -> str:
    return 'KR' if symbol.endswith(('.KS', '.KQ')) else 'US'


def recommendation_rank_key(item: dict) -> tuple:
    bucket = 2 if item.get('action') == 'candidate_buy_zone' or item.get('recommendation_bucket') == 'approved' or item.get('trade_eligible') else 1
    return (bucket, float(item.get('score') or 0), float(item.get('weighted_strategy_consensus') or item.get('strategy_consensus') or 0))


def latest_selected_recommendations(conn: sqlite3.Connection, runs: int = 2) -> dict:
    """Return symbols selected in the latest N persisted recommendation runs.

    Selection hysteresis uses this short history so a symbol must fall out of
    the cutoff for more than one scheduled run before being removed, as long as
    it remains a current candidate and is still near the cutoff.
    """
    try:
        ensure_recommendation_history(conn)
        run_rows=conn.execute('SELECT DISTINCT run_at FROM recommendation_history ORDER BY run_at DESC LIMIT ?', (runs,)).fetchall()
        run_ats=[r['run_at'] for r in run_rows]
        if not run_ats:
            return {}
        placeholders=','.join('?' for _ in run_ats)
        out={}
        rows=conn.execute(f"""SELECT symbol, market, action, score, strategy_id, run_at, payload_json
                              FROM recommendation_history WHERE run_at IN ({placeholders})
                              ORDER BY run_at DESC""", run_ats).fetchall()
        for r in rows:
            payload={}
            try:
                payload=json.loads(r['payload_json'] or '{}')
            except Exception:
                payload={}
            sym=r['symbol']
            item=out.setdefault(sym, {'symbol':sym,'market':r['market'],'action':r['action'],'score':r['score'],'strategy_id':r['strategy_id'],'run_at':r['run_at'],'payload':payload,'recent_selected_runs':[]})
            item['recent_selected_runs'].append(r['run_at'])
            # Keep the most recent row fields for action/score/strategy context.
            if r['run_at'] >= item.get('run_at',''):
                item.update({'market':r['market'],'action':r['action'],'score':r['score'],'strategy_id':r['strategy_id'],'run_at':r['run_at'],'payload':payload})
        for item in out.values():
            item['recent_selected_count']=len(set(item.get('recent_selected_runs') or []))
            item['history_window_runs']=run_ats
        return out
    except Exception:
        return {}


def apply_selection_hysteresis(selected: list[dict], candidates: list[dict], previous: dict, limit: int, margin_points: float = 3.5) -> list[dict]:
    """Keep prior selected symbols near the cutoff to reduce 15-minute list churn.

    A previous symbol can replace a newly-entered symbol only when it is still a
    current candidate, has the same action bucket, and is within margin_points
    of the weakest selected candidate in that market. The previous set covers
    the latest two runs, so a near-cutoff symbol needs more than one miss before
    being removed. This preserves count and does not resurrect symbols that lost
    their current signal.
    """
    if not previous or not selected:
        return selected
    by_symbol={x.get('symbol'): x for x in candidates if x.get('symbol')}
    selected_by_symbol={x.get('symbol'): x for x in selected if x.get('symbol')}
    selected_symbols=set(selected_by_symbol)
    prev_symbols=set(previous)
    changed=[]
    for prev_sym in [s for s in prev_symbols if s not in selected_symbols and s in by_symbol]:
        cand=by_symbol[prev_sym]
        same_market=[x for x in selected if market_of(x.get('symbol','')) == market_of(prev_sym)]
        if len(same_market) < limit:
            continue
        replacement_pool=[x for x in same_market if x.get('symbol') not in prev_symbols]
        if not replacement_pool:
            continue
        weakest=min(replacement_pool, key=recommendation_rank_key)
        if cand.get('action') != weakest.get('action'):
            continue
        score_gap=round(float(weakest.get('score') or 0) - float(cand.get('score') or 0), 2)
        if score_gap <= margin_points:
            selected=[cand if x is weakest else x for x in selected]
            selected_symbols.discard(weakest.get('symbol'))
            selected_symbols.add(prev_sym)
            prev_info=previous.get(prev_sym) or {}
            cand.setdefault('selection_hysteresis', {'kept_from_recent_history': True, 'replaced_symbol': weakest.get('symbol'), 'score_gap_vs_replaced': score_gap, 'margin_points': margin_points, 'recent_selected_count': prev_info.get('recent_selected_count'), 'recent_selected_runs': prev_info.get('recent_selected_runs', [])[:3]})
            changed.append(cand['selection_hysteresis'])
    return sorted(selected, key=recommendation_rank_key, reverse=True)


def top_by_market(recs: list[dict], per_market_limit: int, previous: dict | None = None) -> list[dict]:
    buckets = {'KR': [], 'US': []}
    for rec in recs:
        buckets.setdefault(market_of(rec['symbol']), []).append(rec)
    for rows in buckets.values():
        rows.sort(key=recommendation_rank_key, reverse=True)
    merged = []
    for market in ('KR', 'US'):
        market_selected=buckets.get(market, [])[:per_market_limit]
        if previous:
            market_selected=apply_selection_hysteresis(market_selected, buckets.get(market, []), previous, per_market_limit)
        merged.extend(market_selected)
    return sorted(merged, key=recommendation_rank_key, reverse=True)


def ensure_recommendation_history(conn: sqlite3.Connection) -> None:
    conn.execute('''
        CREATE TABLE IF NOT EXISTS recommendation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market TEXT,
            action TEXT NOT NULL,
            score REAL,
            strategy_id TEXT,
            target_1 REAL,
            stop_reference REAL,
            confidence_grade TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_at, symbol)
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_recommendation_history_run_at ON recommendation_history(run_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_recommendation_history_symbol_run ON recommendation_history(symbol, run_at)')


def recommendation_snapshot_row(run_at: str, item: dict) -> tuple:
    grade=item.get('confidence_grade') or {}
    grade_level=grade.get('level') if isinstance(grade, dict) else None
    return (
        run_at,
        item.get('symbol'),
        item.get('market') or market_of(item.get('symbol','')),
        item.get('action') or 'watch',
        item.get('score'),
        item.get('strategy_id') or item.get('source_strategy_id') or item.get('logic') or item.get('best_logic'),
        item.get('target_1'),
        item.get('stop_reference'),
        grade_level,
        json.dumps(item, ensure_ascii=False, sort_keys=True),
        datetime.now(timezone.utc).isoformat(),
    )


def save_recommendation_history(run_at: str, selected: list[dict]) -> dict:
    # This agent can run twice in the pipeline (before/after disclosure refresh) while
    # other diagnostics read the same SQLite DB. Use a bounded retry around the small
    # history write so transient cron overlap does not fail the whole paper pipeline.
    last_exc=None
    previous={}
    current={item.get('symbol'): item for item in selected if item.get('symbol')}
    for attempt in range(4):
        conn=sqlite3.connect(get_settings().database_path, timeout=45); conn.row_factory=sqlite3.Row
        try:
            conn.execute('PRAGMA busy_timeout=60000')
            ensure_recommendation_history(conn)
            prev_row=conn.execute('SELECT MAX(run_at) AS run_at FROM recommendation_history WHERE run_at < ?', (run_at,)).fetchone()
            prev_run=prev_row['run_at'] if prev_row and prev_row['run_at'] else None
            previous={}
            if prev_run:
                for row in conn.execute('SELECT symbol, action, score, strategy_id, payload_json FROM recommendation_history WHERE run_at=?', (prev_run,)).fetchall():
                    prev_item=dict(row)
                    try:
                        prev_payload=json.loads(row['payload_json'] or '{}')
                    except Exception:
                        prev_payload={}
                    prev_item['recommendation_bucket']=prev_payload.get('recommendation_bucket') or prev_payload.get('bucket')
                    previous[row['symbol']]=prev_item
            conn.executemany('''
                INSERT OR REPLACE INTO recommendation_history
                (run_at, symbol, market, action, score, strategy_id, target_1, stop_reference, confidence_grade, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', [recommendation_snapshot_row(run_at, item) for item in selected if item.get('symbol')])
            conn.commit(); conn.close(); break
        except sqlite3.OperationalError as exc:
            last_exc=exc
            try: conn.rollback(); conn.close()
            except Exception: pass
            if 'locked' not in str(exc).lower() or attempt >= 3:
                raise
            time.sleep(2 * (attempt + 1))
    else:
        raise last_exc

    new_symbols=sorted([s for s in current if s not in previous])
    removed_symbols=sorted([s for s in previous if s not in current])
    action_changes=[]; score_changes=[]; strategy_changes=[]; bucket_changes=[]
    for sym,item in current.items():
        old=previous.get(sym)
        if not old: continue
        old_action=old.get('action'); new_action=item.get('action')
        if old_action != new_action:
            action_changes.append({'symbol':sym,'old_action':old_action,'new_action':new_action})
        old_score=old.get('score')
        new_score=item.get('score')
        if old_score is not None and new_score is not None:
            delta=round(float(new_score)-float(old_score),2)
            if abs(delta) >= 5:
                score_changes.append({'symbol':sym,'old_score':round(float(old_score),2),'new_score':round(float(new_score),2),'delta':delta})
        old_bucket=old.get('recommendation_bucket')
        new_bucket=item.get('recommendation_bucket') or item.get('bucket')
        if old_bucket != new_bucket:
            bucket_changes.append({'symbol':sym,'old_bucket':old_bucket,'new_bucket':new_bucket})
        old_strategy=old.get('strategy_id')
        new_strategy=item.get('strategy_id') or item.get('source_strategy_id') or item.get('logic') or item.get('best_logic')
        if old_strategy and new_strategy and old_strategy != new_strategy:
            strategy_changes.append({'symbol':sym,'old_strategy_id':old_strategy,'new_strategy_id':new_strategy})
    score_changes=sorted(score_changes,key=lambda x:abs(x['delta']),reverse=True)
    return {
        'previous_run_at': prev_run,
        'new_symbols': new_symbols,
        'removed_symbols': removed_symbols,
        'action_changes': action_changes,
        'score_changes': score_changes[:10],
        'strategy_changes': strategy_changes[:10],
        'bucket_changes': bucket_changes[:20],
        'change_count': len(new_symbols)+len(removed_symbols)+len(action_changes)+len(score_changes)+len(strategy_changes)+len(bucket_changes),
    }


def export_recommendation_history_static(latest_changes: dict | None = None, runs: int = 50) -> None:
    conn=sqlite3.connect(get_settings().database_path, timeout=30); conn.row_factory=sqlite3.Row
    ensure_recommendation_history(conn)
    run_rows=conn.execute('SELECT DISTINCT run_at FROM recommendation_history ORDER BY run_at DESC LIMIT ?', (runs,)).fetchall()
    run_ats=[r['run_at'] for r in run_rows]
    items=[]
    if run_ats:
        placeholders=','.join('?' for _ in run_ats)
        rows=conn.execute(f'''SELECT run_at, symbol, market, action, score, strategy_id, target_1, stop_reference, confidence_grade, payload_json
                              FROM recommendation_history WHERE run_at IN ({placeholders})
                              ORDER BY run_at DESC, market ASC, score DESC''', run_ats).fetchall()
        latest_prices={}
        symbols=sorted({r['symbol'] for r in rows})
        if symbols:
            sym_placeholders=','.join('?' for _ in symbols)
            for pr in conn.execute(f'''SELECT symbol, date, close FROM price_bars
                                      WHERE symbol IN ({sym_placeholders}) AND timeframe='1d'
                                      ORDER BY symbol, date DESC''', symbols).fetchall():
                latest_prices.setdefault(pr['symbol'], {'latest_price_date': pr['date'], 'latest_close': pr['close']})
        for r in rows:
            try:
                payload=json.loads(r['payload_json'] or '{}')
            except json.JSONDecodeError:
                payload={}
            price=latest_prices.get(r['symbol'], {})
            items.append({'run_at':r['run_at'],'symbol':r['symbol'],'name':payload.get('name'),'market':r['market'],'action':r['action'],'score':r['score'],'strategy_id':r['strategy_id'],'latest_price_date':price.get('latest_price_date'),'latest_close':price.get('latest_close'),'target_1':r['target_1'],'stop_reference':r['stop_reference'],'confidence_grade':r['confidence_grade'],'action_label':payload.get('action_label'),'recommendation_reason':payload.get('recommendation_reason'),'risk_notes':payload.get('risk_notes') or []})
    conn.close()
    by_symbol={}
    for item in items:
        sym=item['symbol']
        stats=by_symbol.setdefault(sym,{'symbol':sym,'name':item.get('name'),'market':item.get('market'),'count':0,'latest_score':None,'latest_action':None,'latest_run_at':None,'first_run_at':item.get('run_at'),'last_strategy_id':None})
        stats['count'] += 1
        stats['first_run_at'] = item.get('run_at')
        if stats['latest_run_at'] is None or item.get('run_at') > stats['latest_run_at']:
            stats['latest_run_at']=item.get('run_at'); stats['latest_score']=item.get('score'); stats['latest_action']=item.get('action'); stats['last_strategy_id']=item.get('strategy_id')
    packet={'runs':run_ats,'items':items,'by_symbol':sorted(by_symbol.values(), key=lambda x:(x.get('latest_run_at') or '', x.get('latest_score') or 0), reverse=True),'latest_changes':latest_changes,'summary':{'run_count':len(run_ats),'item_count':len(items),'symbol_count':len(by_symbol)}}
    out=ROOT / 'static' / 'recommendation_history.json'
    out.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    ap=argparse.ArgumentParser(description='Generate benchmark-aware paper stock recommendations from active validated strategies')
    ap.add_argument('--limit',type=int,default=10, help='Legacy total limit fallback')
    ap.add_argument('--per-market-limit',type=int,default=10, help='Keep this many recommendations per market so KR/US dropdowns both have candidates')
    ap.add_argument('--output',default='/tmp/recommendations_latest.json')
    args=ap.parse_args(); init_db()
    active_symbols=sorted(set([m['symbol'] for m in list_universe_members(status='active')] + mover_seed_symbols(120) + investor_flow_seed_symbols(120)))
    registry_rows=list_strategy_registry()
    strict_active_strategies=[r for r in registry_rows if r['status']=='active']
    repair_lane_strategies=[r for r in registry_rows if r.get('status') in ('repair_active','validation_active')]
    active_strategies=strict_active_strategies or repair_lane_strategies
    repair_strategy_mode=bool(repair_lane_strategies and not strict_active_strategies)
    if not active_strategies:
        # If profit guards demote every active paper strategy, keep a tiny repair-watch
        # universe so recommendations remain validation/exit-retest aware instead of
        # disappearing completely. These gates are severe_tail_or_ev and are forced to
        # watch/research-only downstream; they are not buy approvals.
        repair_candidates=[r for r in registry_rows if r.get('status') in ('probation','watch') and strategy_success_gate(r.get('logic')).get('severe_tail_or_ev_guard')]
        repair_candidates=sorted(repair_candidates, key=lambda r: (float(r.get('recent_avg_excess_return_pct') or 0), float(r.get('avg_excess_return_pct') or 0), int(r.get('samples') or 0)), reverse=True)
        active_strategies=repair_candidates[:3]
        repair_strategy_mode=bool(active_strategies)
    short_horizon_profiles=latest_short_horizon_profile()
    conn=sqlite3.connect(get_settings().database_path, timeout=30); conn.row_factory=sqlite3.Row
    recs=[]
    for s in active_symbols:
        r=recommend(conn,s,active_strategies,short_horizon_profiles)
        if r: recs.append(r)
    previous_selected=latest_selected_recommendations(conn)
    conn.close(); recs.sort(key=recommendation_rank_key, reverse=True)
    selected=top_by_market(recs, args.per_market_limit, previous_selected) if args.per_market_limit else recs[:args.limit]
    run_at=datetime.now(timezone.utc).isoformat()
    recommendation_changes=save_recommendation_history(run_at, selected)
    export_recommendation_history_static(recommendation_changes)
    candidate_market_counts={m: sum(1 for x in recs if x.get('market') == m) for m in ('KR','US')}
    selected_market_counts={m: sum(1 for x in selected if x.get('market') == m) for m in ('KR','US')}
    status='ok' if selected else 'degraded'
    warnings=[]
    if not active_strategies: warnings.append('no active strategies available')
    elif repair_strategy_mode: warnings.append('repair_watch_strategy_mode: active strategies demoted by profit guard; using low-weight watch-only repair signals')
    if not selected: warnings.append('no recommendations selected')
    missing_meta=sum(1 for x in selected if not x.get('market') or not x.get('logic') or not x.get('strategy_id'))
    if missing_meta: warnings.append(f'{missing_meta} selected recommendations missing audit metadata')
    weak_win_count=sum(1 for item in selected if ((item.get('validation_basis') or {}).get('avg_excess_win_rate_pct') or 0) < 52)
    aggregate_quality_notes=[]
    if selected and weak_win_count / len(selected) >= 0.8:
        aggregate_quality_notes.append({'code':'weak_excess_win_rate','label':'상위 전략 초과승률이 전반적으로 낮음','detail':f'{weak_win_count}/{len(selected)} selected below 52%; candidate ranking/validation priority에만 반영'})
    packet={'run_at':run_at,'mode':'검증결과_기반_현재_종목추천','org_profile':org_profile(),'real_trading':False,'active_strategy_count':len(strict_active_strategies),'repair_active_strategy_count':len(repair_lane_strategies),'effective_strategy_count':len(active_strategies),'repair_strategy_mode':repair_strategy_mode,'market_counts':selected_market_counts,'candidate_market_counts':candidate_market_counts,'per_market_limit':args.per_market_limit,'recommendation_changes':recommendation_changes,'aggregate_quality_notes':aggregate_quality_notes,'items':selected}
    attach_contract(packet, 'recommendation_agent', status=status, inputs={'limit': args.limit, 'per_market_limit': args.per_market_limit}, outputs={'item_count': len(selected), 'market_counts': selected_market_counts, 'candidate_market_counts': candidate_market_counts, 'recommendation_changes': recommendation_changes}, metrics={'active_strategy_count': len(strict_active_strategies), 'repair_active_strategy_count': len(repair_lane_strategies), 'effective_strategy_count': len(active_strategies), 'repair_strategy_mode': repair_strategy_mode, 'candidate_count': len(recs), 'selected_count': len(selected), 'missing_metadata_count': missing_meta, 'recommendation_change_count': recommendation_changes.get('change_count', 0), 'recommendation_bucket_change_count': len(recommendation_changes.get('bucket_changes') or [])}, warnings=warnings, next_actions=['Run strategy lifecycle/balancer before recommendations.'] if not active_strategies else [])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
