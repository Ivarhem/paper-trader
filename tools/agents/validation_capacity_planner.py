#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.database import init_db, validation_coverage


def load_avg_1m() -> float:
    try:
        return float(os.getloadavg()[0])
    except Exception:
        return 0.0


def plan(max_batch_size: int, default_batch_size: int) -> dict:
    init_db()
    coverage = validation_coverage()
    pending = int(coverage.get('pending_results_estimate') or 0)
    cov = float(coverage.get('coverage_pct_estimate') or 0)
    under = coverage.get('under_tested') or []
    under_30 = len([x for x in under if int(x.get('candidate_samples') or 0) < 30])
    data_only_under_80 = len([x for x in under if str(x.get('logic') or '').startswith(('technical_','volatility_contraction_breakout','pullback_uptrend','relative_strength_persistence')) and int(x.get('candidate_samples') or 0) < 80])
    load = load_avg_1m()
    batch = default_batch_size
    cadence = '3m'
    reasons = []
    if load >= 4.0:
        batch = min(batch, 300); reasons.append(f'서버 load {load:.2f}로 보수 운영')
    elif pending > 750000 and load < 2.0:
        batch = min(max_batch_size, max(batch, 1800)); reasons.append('백로그가 매우 크고 서버 여유가 있어 batch 상향')
    elif pending > 250000 and load < 2.5:
        batch = min(max_batch_size, max(batch, 1500)); reasons.append('백로그가 크고 서버 여유가 있어 batch 상향')
    elif data_only_under_80 > 0:
        batch = min(max_batch_size, max(batch, 1200)); reasons.append(f'data-only 신규 전략 검증 보강 필요 {data_only_under_80}개')
    elif under_30 > 10:
        batch = min(max_batch_size, max(batch, 1200)); reasons.append('저샘플 전략 보강 필요')
    if cov < 10 and pending > 50000 and load < 2.0:
        cadence = '3m 유지, 서버 안정 시 2m 후보'
    if not reasons:
        reasons.append('현재 batch 유지')
    return {
        'run_at': datetime.now(timezone.utc).isoformat(),
        'role': 'Validation Capacity Planner',
        'mode': 'auto_applied_capacity_recommendation',
        'real_trading': False,
        'coverage_pct': cov,
        'pending_results_estimate': pending,
        'under_30_count': under_30,
        'data_only_under_80_count': data_only_under_80,
        'load_avg_1m': round(load, 2),
        'recommended_batch_size': int(batch),
        'cadence_recommendation': cadence,
        'auto_apply': True,
        'reasons': reasons,
    }


def main():
    ap = argparse.ArgumentParser(description='Plan validation throughput based on coverage, backlog, and server load')
    ap.add_argument('--default-batch-size', type=int, default=500)
    ap.add_argument('--max-batch-size', type=int, default=2500)
    ap.add_argument('--output', default='/tmp/validation_capacity_planner_latest.json')
    args = ap.parse_args()
    payload = plan(args.max_batch_size, args.default_batch_size)
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2))
if __name__ == '__main__':
    main()
