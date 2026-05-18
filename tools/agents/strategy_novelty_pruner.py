#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from tools.agents.lib.indicator_taxonomy import classify_indicator_logic
from app.database import init_db, list_strategy_registry, utc_now
from tools.agents.lib.agent_contract import attach_contract


def family_key(logic:str)->str:
    if logic.startswith('technical_'):
        meta=classify_indicator_logic(logic)
        parts=logic.split('_')
        return 'technical_' + str(meta.get('indicator_family')) + '_' + (parts[2] if len(parts)>2 else 'generic')
    if logic.endswith('_v1'): return logic
    parts=logic.split('_')
    # group by target/stop/momentum, ignore q score threshold for obvious duplicates
    return '_'.join(parts[:5]) if len(parts)>=6 else logic


def rank(row):
    return ({'active':5,'watch':4,'probation':3,'candidate':2,'pending_validation':2,'retired':0}.get(row.get('status'),0), row.get('samples') or 0, row.get('avg_excess_return_pct') or -999, row.get('success_rate_pct') or 0)


def safe_to_hold(row, best=None):
    # Never auto-demote active strategies. Hold redundant non-active variants when
    # they lack independent evidence OR when a better sibling already represents
    # the same target/stop/momentum family. This makes pruning actionable instead
    # of repeatedly reporting duplicate groups with applied_count=0.
    if row.get('status') in ('active','hold','retired'): return False
    if int(row.get('samples') or 0) < 30: return True
    if (row.get('avg_excess_return_pct') or 0) <= 0: return True
    if (row.get('success_rate_pct') or 0) < 35: return True
    if best is not None and rank(best) >= rank(row): return True
    if str(row.get('logic','')).startswith('technical_') and row.get('status') != 'active': return True
    return False


def main():
    ap=argparse.ArgumentParser(description='Find/apply duplicate noisy strategy candidates to reduce overfitting')
    ap.add_argument('--output', default='/tmp/strategy_novelty_pruner_latest.json')
    ap.add_argument('--apply', action='store_true', help='Safely move redundant non-active weak variants to hold')
    ap.add_argument('--max-apply', type=int, default=25)
    args=ap.parse_args(); init_db(); rows=list_strategy_registry()
    # Focus duplicate reporting on strategies still competing for selection. Held
    # and retired variants have already been suppressed, so counting them as
    # duplicate groups created noisy, non-actionable alerts every cron run.
    eligible_statuses={'active','watch','probation','candidate','pending_validation'}
    rows=[r for r in rows if r.get('status') in eligible_statuses]
    groups={}
    for r in rows: groups.setdefault(family_key(r['logic']),[]).append(r)
    recs=[]; apply_rows=[]
    for key,arr in groups.items():
        if len(arr)<2: continue
        best=sorted(arr,key=rank, reverse=True)[0]
        redundant=[x for x in arr if x['logic']!=best['logic']]
        safe=[x for x in redundant if safe_to_hold(x, best)]
        if redundant:
            rec={'group':key,'keep':best['logic'],'redundant_count':len(redundant),'safe_apply_count':len(safe),'redundant':[x['logic'] for x in redundant[:20]],'safe_to_hold':[x['logic'] for x in safe[:20]],'reason':'same target/stop/momentum family; score-threshold variants need distinct evidence before promotion'}
            recs.append(rec); apply_rows.extend(safe)
    applied=[]
    if args.apply and apply_rows:
        conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
        seen=set()
        for r in apply_rows:
            logic=r['logic']
            if logic in seen or len(applied)>=args.max_apply: continue
            seen.add(logic)
            old=r.get('status')
            reason='novelty pruner hold: redundant weak/non-active variant in same strategy family'
            conn.execute('UPDATE strategy_registry SET status=?, reason=?, updated_at=? WHERE logic=? AND status NOT IN (?,?)',('hold',reason,utc_now(),logic,'active','hold'))
            conn.execute('INSERT INTO strategy_state_events (logic,old_status,new_status,reason,event_json,created_at) VALUES (?,?,?,?,?,?)',(logic,old,'hold',reason,json.dumps({'agent':'strategy_novelty_pruner','family':family_key(logic)},ensure_ascii=False),utc_now()))
            applied.append({'logic':logic,'old_status':old,'new_status':'hold','reason':reason})
        conn.commit(); conn.close()
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'strategy_novelty_pruning','apply':args.apply,'recommendation_count':len(recs),'applied_count':len(applied),'applied':applied,'recommendations':recs[:50]}
    attach_contract(packet,'strategy_novelty_pruner',inputs={'apply':args.apply,'max_apply':args.max_apply},outputs={'recommendation_count':len(recs),'applied_count':len(applied)},metrics={'duplicate_groups':len(recs),'applied_count':len(applied)},next_actions=['Review held variants before retirement.'] if applied else [])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
