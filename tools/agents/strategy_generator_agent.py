#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.recommendation_auditor import LOGICS
from app.database import init_db, upsert_strategy_candidates
from tools.agents.lib.agent_contract import attach_contract


def main():
    ap=argparse.ArgumentParser(description='List/generated recommendation strategy candidates')
    ap.add_argument('--output', default='/tmp/strategy_candidates_latest.json')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--register', action='store_true')
    ap.add_argument('--high-return-quota', type=int, default=1, help='Ensure at least this many generated candidates are labeled high_return_seekers for exploration governance')
    ap.add_argument('--data-only-ratio', type=float, default=0.20, help='Minimum ratio of generated candidates that should be deterministic technical/fundamental data-only rules when --limit is used')
    args=ap.parse_args()
    items=[]
    for name,cfg in LOGICS.items():
        item={'logic':name, **cfg}
        item['exploration_profile']='high_return' if (cfg.get('target_cap') or 0) >= 0.16 or name.startswith('aggressive_') else 'balanced'
        item['data_only']=bool(cfg.get('data_only'))
        item['data_inputs']=cfg.get('data_inputs') or []
        items.append(item)
    items=sorted(items, key=lambda x:(x.get('family',''), x['logic']))
    if args.limit:
        high=[x for x in items if x.get('exploration_profile')=='high_return']
        data_only=[x for x in items if x.get('data_only')]
        rest=[x for x in items if x.get('exploration_profile')!='high_return' and not x.get('data_only')]
        take_high=high[:min(args.high_return_quota, args.limit, len(high))]
        data_quota=min(len(data_only), max(0, int(round(args.limit * args.data_only_ratio))))
        take_data=[]
        seen={x['logic'] for x in take_high}
        for x in data_only:
            if x['logic'] in seen: continue
            take_data.append(x); seen.add(x['logic'])
            if len(take_data) >= data_quota: break
        remaining=args.limit-len(take_high)-len(take_data)
        take_rest=[]
        for x in rest:
            if x['logic'] in seen: continue
            take_rest.append(x); seen.add(x['logic'])
            if len(take_rest) >= remaining: break
        items=(take_high+take_data+take_rest)[:args.limit]
    by_family={}
    by_profile={}
    for x in items:
        by_family[x.get('family','unknown')]=by_family.get(x.get('family','unknown'),0)+1
        by_profile[x.get('exploration_profile','unknown')]=by_profile.get(x.get('exploration_profile','unknown'),0)+1
        if x.get('data_only'):
            by_profile['data_only']=by_profile.get('data_only',0)+1
    registration=None
    if args.register:
        init_db(); registration=upsert_strategy_candidates(items)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'count':len(items),'by_family':by_family,'by_profile':by_profile,'high_return_quota':args.high_return_quota,'data_only_ratio_target':args.data_only_ratio,'registration':registration,'items':items}
    attach_contract(packet, 'strategy_generator', inputs={'limit': args.limit, 'register': args.register}, outputs={'count': len(items), 'by_family': by_family, 'by_profile': by_profile, 'registration': registration}, metrics={'candidate_count': len(items), 'high_return_count': by_profile.get('high_return',0), 'data_only_count': by_profile.get('data_only',0), 'registered_inserted': (registration or {}).get('inserted', 0), 'registered_updated': (registration or {}).get('updated', 0)}, next_actions=['Run validation worker to accumulate samples for new candidates.'] if args.register and registration and registration.get('inserted') else [])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
