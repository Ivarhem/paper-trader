#!/usr/bin/env python3
from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

def reason(x):
    if x.get('result')=='success':
        if (x.get('excess_return_pct') or 0)>0: return '전략/종목 신호가 벤치마크보다 우수하게 작동'
        return '절대 목표는 달성했지만 벤치마크 대비 우위는 약함'
    if x.get('result')=='fail':
        if (x.get('max_upside_pct') or 0) > abs(x.get('max_drawdown_pct') or 0): return '목표 전 일부 상승 후 손절, stop/목표폭 재조정 필요'
        if (x.get('excess_return_pct') or 0)<0: return '종목 고유 약세 또는 시장 대비 열위'
        return '절대 손절 실패이나 벤치마크 대비 방어력은 일부 확인'
    return '기간 내 목표/손절 미확정, horizon 또는 목표폭 검토 필요'

def main():
    path=Path('/tmp/recommendation_audit_latest.json'); data=json.loads(path.read_text(encoding='utf-8'))
    full_path=Path(data.get('full_output') or '/tmp/recommendation_audit_full_latest.json')
    source_data=data
    preview_items=source_data.get('items') or []
    expected_items=source_data.get('items_total_filtered') or source_data.get('items_total_audited') or len(preview_items)
    if full_path.exists() and (not preview_items or expected_items > len(preview_items)):
        source_data=json.loads(full_path.read_text(encoding='utf-8'))
    rows=[x for x in source_data.get('items',[]) if x.get('status')=='audited' and x.get('action')=='candidate_buy_zone']
    if not rows:
        rows=[x for x in source_data.get('items',[]) if x.get('status')=='audited']
    for x in rows: x['attribution']=reason(x)
    cnt=Counter(x['attribution'] for x in rows)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'outcome_attribution','summary':dict(cnt),'sample_count':len(rows),'real_trading':False}
    Path('/tmp/outcome_attribution_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    annotated={(x.get('symbol'), x.get('logic'), x.get('cutoff'), x.get('result'), x.get('action')) for x in rows}
    if data.get('items'):
        data['items']=[
            ({**x,'attribution':reason(x)} if (x.get('symbol'), x.get('logic'), x.get('cutoff'), x.get('result'), x.get('action')) in annotated else x)
            for x in data.get('items',[])
        ]
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
