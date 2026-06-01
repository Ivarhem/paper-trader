#!/usr/bin/env python3
from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

def market(symbol): return 'KR' if symbol.endswith(('.KS','.KQ')) else 'US'
def sector_hint(symbol,name):
    txt=(symbol+' '+(name or '')).lower()
    if any(x in txt for x in ['bank','금융','생명','신한','kb','하나']): return 'financials'
    if any(x in txt for x in ['nvidia','intel','micron','semiconductor','삼성전자','하이닉스','qcom','txn']): return 'semis'
    if any(x in txt for x in ['energy','oil','s-oil','sk이노베이션','slb']): return 'energy'
    if any(x in txt for x in ['화학','chem','바이오','bio']): return 'materials_health'
    return 'general'

def main():
    path=Path('/tmp/recommendations_latest.json'); data=json.loads(path.read_text(encoding='utf-8'))
    rows=data.get('items',[]); mcnt=Counter(market(r['symbol']) for r in rows); scnt=Counter(sector_hint(r['symbol'],r.get('name')) for r in rows)
    risk_off=False; warnings=[]; opinions=[]
    for k,v in scnt.items():
        if v>=5: warnings.append(f'{k} 후보가 {v}개로 쏠림이 있습니다')
    if any((r.get('critic') or {}).get('severity')=='high' for r in rows): warnings.append('강한 반대 근거가 있는 후보가 포함되어 있습니다')
    for r in rows:
        s=sector_hint(r['symbol'],r.get('name')); notes=[]
        if scnt[s]>=5: notes.append(f'{s} 노출 과다')
        if mcnt[market(r['symbol'])]>10: notes.append('시장별 후보 수 상한 확인 필요')
        vb=r.get('validation_basis') or {}
        fund_guard=vb.get('fund_allocation_guardrail') or {}
        base_hint=3 if (r.get('confidence_grade') or {}).get('level')=='strong' else 1
        if fund_guard.get('cap_applied'):
            base_hint=min(base_hint, 1)
            notes.append('fund turnover/MDD guardrail caps allocation hint')
        pr={'sector':s,'market':market(r['symbol']),'notes':notes,'max_position_hint_pct':base_hint,'fund_allocation_guardrail':fund_guard}
        opinions.append({'symbol':r['symbol'],'agent':'portfolio_risk_manager','overlay':{'portfolio_risk':pr},'risk_notes_append':notes,'final_field_writer':False})
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'portfolio_risk_manager','market_counts':dict(mcnt),'sector_counts':dict(scnt),'warnings':warnings,'risk_off':risk_off,'opinions':opinions,'real_trading':False,'writes_recommendations_latest':False}
    Path('/tmp/portfolio_risk_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    Path('/tmp/recommendation_opinions_portfolio_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
