#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT))
from app.database import init_db, list_strategy_registry

def family(logic):
    if logic.startswith('range_grid_'): return 'range_grid'
    if 'range' in logic: return 'range_baseline'
    if 'rsi' in logic: return 'mean_reversion'
    return 'other'

def main():
    init_db(); rows=list_strategy_registry(); active=[r for r in rows if r['status']=='active']; cnt=Counter(family(r['logic']) for r in active)
    gaps=[]
    for f in ['mean_reversion','breakout_continuation','low_vol_trend','post_disclosure_reaction']:
        if cnt.get(f,0)==0: gaps.append(f)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'strategy_family_manager','active_family_counts':dict(cnt),'diversity_score':max(0,100-len(gaps)*15),'gaps':gaps,'recommendations':[f'{g} 전략군 생성/검증 필요' for g in gaps],'real_trading':False}
    Path('/tmp/strategy_family_manager_latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
