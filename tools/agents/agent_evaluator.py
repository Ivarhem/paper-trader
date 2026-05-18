#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys


def score(item: dict) -> tuple[float, list[str]]:
    notes=[]
    excess=item.get('total_return_pct',0)-item.get('buy_hold_return_pct',0)
    mdd=item.get('max_drawdown_pct',0)
    trades=item.get('trade_count',0)
    pf=item.get('profit_factor') or 0
    s=0.0
    s += max(min(excess/10, 0.4), -0.4)
    s += 0.2 if mdd >= -15 else -0.2
    s += 0.2 if trades >= 10 else -0.1
    s += 0.2 if pf >= 1.2 else -0.1
    if trades < 10: notes.append('trade_count below promotion gate')
    if mdd < -15: notes.append('max_drawdown too high')
    if excess <= 0: notes.append('does not beat buy-and-hold')
    if pf and pf < 1.2: notes.append('profit_factor below gate')
    return round(max(min((s+0.5),1),0),2), notes


def main() -> int:
    ap=argparse.ArgumentParser(description='Evaluate backtest sweep candidates')
    ap.add_argument('file', nargs='?', default='-')
    args=ap.parse_args()
    data=json.load(sys.stdin if args.file=='-' else open(args.file))
    evaluated=[]
    for item in data.get('items',[]):
        sc, notes=score(item)
        evaluated.append({'score':sc,'promote': sc>=0.75 and not notes, 'symbol':item.get('symbol'), 'strategy':item.get('strategy'), 'params':item.get('params'), 'metrics':{k:item.get(k) for k in ['total_return_pct','buy_hold_return_pct','max_drawdown_pct','trade_count','win_rate_pct','profit_factor']}, 'notes':notes})
    print(json.dumps({'evaluated_count':len(evaluated),'items':evaluated}, ensure_ascii=False, indent=2))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
