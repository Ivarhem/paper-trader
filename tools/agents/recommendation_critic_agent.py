#!/usr/bin/env python3
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter


def disclosure_effective_medium(disc: dict) -> int:
    if disc.get('effective_medium') is not None:
        try:
            return int(disc.get('effective_medium') or 0)
        except Exception:
            return 0
    return int(disc.get('medium') or 0)

def critique(row):
    vb=row.get('validation_basis') or {}; disc=row.get('disclosure_risk') or {}; fq=row.get('financial_quality') or {}
    issues=[]; blocking=[]; under=[]; quality=[]; severity='low'
    if (vb.get('symbol_validation_sample_count') or 0) < 10:
        msg='종목별 검증 샘플이 10건 미만입니다'; issues.append(msg); under.append(msg)
    if (vb.get('positive_symbol_edge_count') or 0) == 0:
        msg='양수 종목 edge가 확인되지 않았습니다'; issues.append(msg); under.append(msg)
    if (vb.get('avg_active_excess_return_pct') or 0) < 1:
        msg='active 전략 평균 초과수익이 낮습니다'; issues.append(msg); quality.append(msg)
    if (vb.get('avg_excess_win_rate_pct') or 0) < 52:
        msg='초과승률이 충분히 높지 않습니다'; issues.append(msg); under.append(msg)
    if disc.get('high',0)>0 or disclosure_effective_medium(disc)>=2:
        msg=f"공시 리스크가 있습니다(H:{disc.get('high',0)} effective M:{disclosure_effective_medium(disc)})"; issues.append(msg); blocking.append(msg)
    if (row.get('upside_1_pct') or 0) < abs(row.get('downside_stop_pct') or 0):
        # Reward/risk is a sizing and validation signal, not a hard safety veto.
        # The current target/stop policy can make this true for most candidates,
        # so treating it as blocking collapses the gate into a non-discriminating
        # "reject everything" rule.
        msg='1차 목표 여력이 손절 위험보다 작습니다'; issues.append(msg); quality.append(msg)
    if fq.get('warnings'):
        msg='재무 품질 경고: ' + ', '.join(fq.get('warnings', [])[:2]); issues.append(msg); blocking.append(msg)
    if (fq.get('score_adjustment') or 0) <= -20:
        blocking.append('재무 품질 하드 리스크')
    if blocking:
        severity='high'
    elif quality and len(under) >= 2:
        severity='medium'
    elif under:
        severity='under_validated'
    elif quality:
        severity='medium'
    issue_type = 'blocking' if blocking else ('under_validated' if under and not quality else ('quality' if quality else 'none'))
    tasks=[]
    if (vb.get('symbol_validation_sample_count') or 0) < 10:
        tasks.append({'task':'collect_symbol_samples','priority':'high','reason':'종목별 검증 샘플 부족'})
    if (vb.get('positive_symbol_edge_count') or 0) == 0:
        tasks.append({'task':'confirm_positive_symbol_edge','priority':'high','reason':'양수 종목 edge 미확인'})
    if (vb.get('avg_active_excess_return_pct') or 0) < 1:
        tasks.append({'task':'retest_positive_excess_or_replace_logic','priority':'medium','reason':'active 전략 평균 초과수익 부족'})
    if (vb.get('avg_excess_win_rate_pct') or 0) < 52:
        tasks.append({'task':'retest_best_logic_win_rate','priority':'medium','reason':'초과승률 부족'})
    if (row.get('upside_1_pct') or 0) < abs(row.get('downside_stop_pct') or 0):
        tasks.append({'task':'review_reward_risk_sizing','priority':'medium','reason':'1차 목표/손절 비대칭은 차단보다 진입가·포지션 크기 검증으로 처리'})
    if row.get('market_issue_context'):
        tasks.append({'task':'track_market_issue_outcome','priority':'medium','reason':'시장 이슈 성과 추적'})
    return {'symbol':row['symbol'],'severity':severity,'issue_type':issue_type,'issues':issues,'blocking_issues':blocking,'under_validated_issues':under,'quality_issues':quality,'validation_tasks':tasks,'watch_reason':row.get('watch_reason'),'summary':' / '.join(issues[:3]) if issues else '뚜렷한 반대 근거는 제한적입니다'}

def main():
    path=Path('/tmp/recommendations_latest.json'); data=json.loads(path.read_text(encoding='utf-8'))
    critics={}; opinions=[]
    for row in data.get('items',[]):
        c=critique(row); critics[row['symbol']]=c
        overlay={'critic':c}
        notes=[]
        watch_patch={}
        if c['severity']=='high':
            msg='비판 검토 필요: ' + (c['summary'] or '추가 확인 필요')
            notes.append(msg); watch_patch['critic_warning']=msg
        elif c.get('severity') == 'under_validated':
            notes.append('검증 대기: 차단 리스크보다는 종목별 샘플/edge 부족으로 추가 검증 필요')
        opinions.append({'symbol':row['symbol'],'agent':'recommendation_critic','overlay':overlay,'risk_notes_append':notes,'watch_reason_patch':watch_patch,'final_field_writer':False})
    issue_counter=Counter(); high_issue_counter=Counter()
    for c in critics.values():
        for issue in c.get('issues') or []:
            issue_counter[issue] += 1
            if c.get('severity') == 'high': high_issue_counter[issue] += 1
    top_issues=[{'issue':k,'count':v,'high_count':high_issue_counter.get(k,0)} for k,v in issue_counter.most_common(10)]
    dominant=top_issues[0] if top_issues else None
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'recommendation_critic','items':list(critics.values()),'opinions':opinions,'issue_summary':{'top_issues':top_issues,'dominant_issue':dominant,'high_count':sum(1 for c in critics.values() if c.get('severity')=='high'),'total':len(critics)},'real_trading':False,'writes_recommendations_latest':False}
    Path('/tmp/recommendation_critic_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    Path('/tmp/recommendation_opinions_critic_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
