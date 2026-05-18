#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract, write_json_shared


def utc_now(): return datetime.now(timezone.utc).isoformat()

def read_json(path, default=None):
    try:
        p=Path(path)
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        pass
    return default if default is not None else {}

def emit(kind, title, severity='info', symbol=None, market=None, source='internal', payload=None, tags=None):
    return {
        'kind': kind,
        'title': title,
        'severity': severity,
        'symbol': symbol,
        'market': market,
        'source': source,
        'tags': tags or [],
        'payload': payload or {},
    }

def main():
    ap=argparse.ArgumentParser(description='Build read-only internal signal feed and market event board from latest paper research artifacts')
    ap.add_argument('--output',default='/tmp/internal_signal_board_latest.json')
    args=ap.parse_args()

    previous=read_json(args.output,{})
    previous_items=previous.get('items') or []
    recs=read_json('/tmp/recommendations_latest.json',{})
    market=read_json('/tmp/market_context_latest.json',{})
    audit=read_json('/tmp/recommendation_audit_latest.json',{})
    pruner=read_json('/tmp/strategy_novelty_pruner_latest.json',{})
    balancer=read_json('/tmp/active_strategy_balancer_latest.json',{})
    candidates=read_json('/tmp/strategy_candidates_latest.json',{})
    shadow=read_json('/tmp/shadow_recommendations_latest.json',{})

    items=[]
    warnings=[]
    next_actions=[]

    for sh in shadow.get('items') or []:
        items.append(emit('shadow_signal', f"{sh.get('symbol')} shadow {sh.get('logic')} score={sh.get('shadow_score')}", 'info', symbol=sh.get('symbol'), market=sh.get('market'), source='shadow_recommendation_agent', tags=['shadow','discovery','paper_only'], payload=sh))

    for r in recs.get('items') or []:
        ctx=r.get('technical_risk_context') or {}
        action=r.get('action')
        severity='info'
        tags=['recommendation', action or 'unknown']
        if action == 'candidate_buy_zone':
            severity='attention'; tags.append('research_candidate')
        if (r.get('validation_basis') or {}).get('audit_hard_downgrade'):
            severity='warning'; tags.append('audit_downgrade')
        if ctx.get('trend_strength') == 'weak' or ctx.get('atr_bucket') == 'high' or ctx.get('volume_confirmation') is False:
            tags.append('risk_context')
        items.append(emit(
            'research_signal',
            f"{r.get('symbol')} {r.get('action_label') or action} score={r.get('score')}",
            severity=severity,
            symbol=r.get('symbol'), market=r.get('market'), source='recommendation_agent', tags=tags,
            payload={
                'action':action,'score':r.get('score'),'confidence_grade':r.get('confidence_grade'),
                'strategy_id':r.get('strategy_id'),'target_1':r.get('target_1'),'stop_reference':r.get('stop_reference'),
                'position_size_hint':r.get('position_size_hint'),'technical_risk_context':ctx,
                'risk_notes':r.get('risk_notes') or [],'real_trading':False,
            }
        ))

    ms=(market.get('summary') or {})
    if ms:
        sev='info'
        score=ms.get('cross_market_impact_score')
        if isinstance(score,(int,float)) and (score>=75 or score<=35): sev='attention'
        items.append(emit('market_event', 'Cross-market context: '+','.join(ms.get('tags') or ['neutral']), sev, source='market_context_agent', tags=ms.get('tags') or [], payload=ms))
        if ms.get('gap_chase_risk') == 'high_chase_risk':
            items.append(emit('risk_alert','High gap-chase risk in KR semiconductor context','warning',market='KR',source='market_context_agent',tags=['gap_chase_risk','semiconductor'],payload=ms))

    best=(audit.get('summary') or {}).get('best') or {}
    if best:
        q=best.get('quality_score')
        flags=best.get('quality_flags') or []
        best_logic=(audit.get('summary') or {}).get('best_logic')
        audit_payload={'quality_score':q,'quality_flags':flags,'best_logic':best_logic}
        was_same_audit_alert=any(
            i.get('source') == 'recommendation_auditor'
            and set(i.get('tags') or []) >= {'audit_quality','paper_only'}
            and (i.get('payload') or {}) == audit_payload
            for i in previous_items
        )
        if q is not None and q < 45 and not was_same_audit_alert:
            items.append(emit('risk_alert',f"Strategy trust label remains low: {best_logic or '-'}",'warning',source='recommendation_auditor',tags=['audit_quality','paper_only'],payload=audit_payload))

    dup=((pruner.get('contract') or {}).get('metrics') or {}).get('duplicate_groups')
    if dup:
        items.append(emit('system_event',f"Novelty pruner duplicate groups={dup}",'info',source='strategy_novelty_pruner',tags=['duplication_monitor'],payload={'duplicate_groups':dup,'applied_count':pruner.get('applied_count')}))

    if balancer.get('promoted'):
        items.append(emit('system_event',f"Active strategy promotions={len(balancer.get('promoted') or [])}",'attention',source='active_strategy_balancer',tags=['strategy_promotion'],payload={'promoted':balancer.get('promoted')}))

    counts=Counter(i['kind'] for i in items)
    sev_counts=Counter(i['severity'] for i in items)
    market_counts=Counter(i.get('market') or 'ALL' for i in items)
    board={
        'run_at':utc_now(),
        'mode':'internal_signal_feed_and_market_event_board',
        'real_trading':False,
        'policy':{
            'external_publish':False,
            'copy_trading':False,
            'broker_sync':False,
            'purpose':'read-only paper research observability inspired by AI-Trader signal/feed architecture',
        },
        'summary':{
            'item_count':len(items),
            'by_kind':dict(counts),
            'by_severity':dict(sev_counts),
            'by_market':dict(market_counts),
            'recommendation_run_at':recs.get('run_at'),
            'market_context_run_at':market.get('run_at'),
            'candidate_count':candidates.get('count'),
            'shadow_item_count':len(shadow.get('items') or []),
        },
        'items':items[:100],
        'next_actions':['Use this board as UI/API read-only feed; do not route to real orders or external publishing.'],
    }
    attach_contract(board,'internal_signal_board_agent',status='ok',outputs={'item_count':len(items),'by_kind':dict(counts)},metrics={'warning_items':sev_counts.get('warning',0),'attention_items':sev_counts.get('attention',0)},warnings=warnings,next_actions=board['next_actions'])
    write_json_shared(args.output, board)
    print(json.dumps(board,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
