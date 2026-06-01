#!/usr/bin/env python3
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

def main():
    rec_path=Path('/tmp/recommendations_latest.json'); data=json.loads(rec_path.read_text(encoding='utf-8')); opinions=[]
    audit=json.loads(Path('/tmp/recommendation_audit_latest.json').read_text(encoding='utf-8')) if Path('/tmp/recommendation_audit_latest.json').exists() else {'summary':{}}
    best=(audit.get('summary') or {}).get('best') or {}
    quality_flags=set(best.get('quality_flags') or [])
    issue_data=json.loads(Path('/tmp/market_issue_scout_latest.json').read_text(encoding='utf-8')) if Path('/tmp/market_issue_scout_latest.json').exists() else {'issues':[]}
    news_issue_data=json.loads(Path('/tmp/market_news_issue_scout_latest.json').read_text(encoding='utf-8')) if Path('/tmp/market_news_issue_scout_latest.json').exists() else {'issues':[]}
    market_counts={}; decisions={}; downgrades=[]
    for row in data.get('items',[]):
        market=row.get('market') or ('KR' if str(row.get('symbol','')).endswith(('.KS','.KQ')) else 'US')
        market_counts[market]=market_counts.get(market,0)+1
        vb=row.get('validation_basis') or {}; grade=(row.get('confidence_grade') or {}).get('level')
        bench=vb.get('benchmark_20d_return_pct')
        decision='pass'; reasons=[]; score=55
        if bench is not None and float(bench) < -5:
            decision='caution'; score-=15; reasons.append('벤치마크 20일 약세')
        # Global audit/validation sample quality belongs to Risk Gate/validation priority.
        # Regime Gate should discriminate only market weakness, issue context, and chase risk.
        samples=vb.get('symbol_validation_sample_count') or 0
        matched=[]
        for issue in issue_data.get('issues') or []:
            if row.get('symbol') in (issue.get('affected_symbols') or []):
                matched.append(issue)
        for issue in news_issue_data.get('issues') or []:
            if row.get('symbol') in (issue.get('mentioned_symbols') or []):
                ni=dict(issue); ni['affected_symbols']=issue.get('mentioned_symbols') or []; matched.append(ni)
        market_regime='neutral'; issue_context=None; chase_risk='normal'
        if matched:
            top=sorted(matched,key=lambda x:x.get('impact_score') or 0,reverse=True)[0]
            policy=top.get('recommendation_policy') or ''
            recency_policy=top.get('recency_policy') or ('short_term_boost_allowed' if top.get('type') == 'dynamic_market_issue_cluster' else 'undated_watch_only')
            short_term_boost_allowed=(policy == 'context_boost_allowed' and recency_policy == 'short_term_boost_allowed') or top.get('type') == 'dynamic_market_issue_cluster'
            if short_term_boost_allowed:
                boost=2.0 if top.get('risk') == 'high_chase_risk' else (3.0 if (top.get('impact_score') or 0)>=70 else 1.5)
            else:
                boost=0.0
            issue_context=top.get('label')
            chase_risk=top.get('risk') or 'normal'
            vb['market_issue_score_boost']=boost
            def compact_sources(issue):
                return [{'title':s.get('title'),'headline':s.get('headline'),'url':s.get('url'),'domain':s.get('domain'),'published_at':s.get('published_at')} for s in (issue.get('sources') or issue.get('narrative_sources') or [])[:4] if (s.get('title') or s.get('headline'))]
            row['market_issue_context']={'issue_id':top.get('issue_id'),'label':top.get('label'),'impact_score':top.get('impact_score'),'risk':top.get('risk'),'narrative':top.get('narrative'),'confidence':top.get('confidence'),'member_count':top.get('member_count'),'recommendation_policy':policy,'recency_policy':recency_policy,'latest_source_date':top.get('latest_source_date'),'min_source_age_days':top.get('min_source_age_days'),'fresh_source_count':top.get('fresh_source_count'),'score_boost':boost,'sources':compact_sources(top),'matched_issues':[{'issue_id':x.get('issue_id'),'label':x.get('label'),'impact_score':x.get('impact_score'),'risk':x.get('risk'),'recency_policy':x.get('recency_policy'),'latest_source_date':x.get('latest_source_date'),'sources':compact_sources(x)} for x in matched[:4]]}
            row['validation_basis']=vb
            notes=row.get('risk_notes') or []
            if boost:
                note=f"단기 시장 이슈: {top.get('label')} · {top.get('narrative')} · 보정 +{boost}"
                if note not in notes: notes.append(note)
            else:
                note=f"장기/과거 시장 이슈 참고: {top.get('label')} · {top.get('narrative')} · 단기 보정 없음"
                if note not in notes: notes.append(note)
            if top.get('risk') in ('high_chase_risk','moderate_chase_risk') and short_term_boost_allowed:
                risk_note='시장 이슈 추격 리스크: 급등 클러스터/단기 뉴스 편입으로 장중 추격 매수보다 검증대기 관찰 우선'
                if risk_note not in notes: notes.append(risk_note)
                decision='caution'; reasons.append(f"시장 이슈 과열/추격 리스크({top.get('label')})")
            elif boost and (top.get('impact_score') or 0) >= 65:
                reasons.append(f"단기 시장 이슈 긍정({top.get('label')})")
            elif recency_policy != 'short_term_boost_allowed':
                reasons.append(f"과거 이슈 참고({top.get('label')}, 단기 보정 없음)")
            row['risk_notes']=notes
            score += boost
        else:
            # Recommendation rows may already contain a previous run's market issue
            # context. If the current scouts no longer match this symbol, clear the
            # stale short-term UI context/boost so old news cannot keep influencing
            # today's recommendation card.
            row.pop('market_issue_context', None)
            vb['market_issue_score_boost']=0.0
            row['validation_basis']=vb
            row['risk_notes']=[n for n in (row.get('risk_notes') or []) if not (str(n).startswith('국내/시장 이슈:') or str(n).startswith('단기 시장 이슈:') or str(n).startswith('장기/과거 시장 이슈 참고:') or str(n).startswith('시장 이슈 추격 리스크:'))]
        if score < 35: decision='risk_off'
        decisions[decision]=decisions.get(decision,0)+1
        if bench is not None and float(bench) >= 5:
            market_regime='risk_on'
        elif bench is not None and float(bench) < -5:
            market_regime='risk_off'
        regime_gate={'decision':decision,'status':decision,'score':max(0,min(100,score)),'reason':' · '.join(reasons) if reasons else 'regime gate 통과','market':market,'market_regime':market_regime,'issue_context':issue_context,'chase_risk':chase_risk,'benchmark_20d_return_pct':bench,'audit_quality_flags':sorted(quality_flags),'market_issue_context':row.get('market_issue_context')}
        issue_notes=[n for n in (row.get('risk_notes') or []) if str(n).startswith(('단기 시장 이슈:','장기/과거 시장 이슈 참고:','시장 이슈 추격 리스크:'))]
        overlay={'regime_gate':regime_gate,'validation_basis':vb}
        if row.get('market_issue_context') is not None:
            overlay['market_issue_context']=row.get('market_issue_context')
        opinions.append({'symbol':row.get('symbol'),'agent':'market_regime_gate','overlay':overlay,'risk_notes_append':issue_notes,'remove_risk_note_prefixes':['국내/시장 이슈:','단기 시장 이슈:','장기/과거 시장 이슈 참고:','시장 이슈 추격 리스크:'],'final_field_writer':False})
        if decision=='risk_off':
            downgrades.append({'symbol':row.get('symbol'),'reason':regime_gate['reason'],'authority':'proposal_only','final_writer':'investment_committee'})
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'market_regime_gate','downgrades':downgrades,'opinions':opinions,'summary':{'market_counts':market_counts,'decision_counts':decisions,'audit_best_quality_score':best.get('quality_score'),'audit_best_flags':sorted(quality_flags),'market_issue_count':len(issue_data.get('issues') or []),'news_issue_count':len(news_issue_data.get('issues') or []),'top_market_issues':(issue_data.get('summary') or {}).get('top_issues'),'top_news_issues':(news_issue_data.get('summary') or {}).get('top_issues')},'real_trading':False,'writes_recommendations_latest':False}
    Path('/tmp/market_regime_gate_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    Path('/tmp/recommendation_opinions_regime_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
